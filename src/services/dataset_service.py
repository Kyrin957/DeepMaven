"""数据集处理服务：导入扫描、内容去重、统计、划分、YOLO 结构生成。"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

from src.models.dataset import Dataset
from src.utils.constants import IMAGE_EXTS, LABEL_EXTS, SPLIT_SUBDIRS
from src.utils.logger import get_logger

logger = get_logger("dataset")


def _common_parent(dirs: set[str]) -> str:
    """返回若干目录的公共父目录；无法确定时返回空串。"""
    if not dirs:
        return ""
    if len(dirs) == 1:
        return next(iter(dirs))
    try:
        return os.path.commonpath(sorted(dirs))
    except ValueError:  # 跨盘符等情况
        return ""


class DatasetService:
    """数据集相关文件操作。"""

    # -----------------------------------------------------------
    # 扫描统计
    # -----------------------------------------------------------
    @staticmethod
    def scan_images(directory: str | Path) -> list[Path]:
        """递归扫描目录下的所有图片文件。"""
        directory = Path(directory)
        return [
            p for p in sorted(directory.rglob("*"))
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS
        ]

    @staticmethod
    def scan_labels(directory: str | Path) -> list[Path]:
        """扫描目录下的所有标签文件（.txt）。"""
        directory = Path(directory)
        return [
            p for p in sorted(directory.rglob("*"))
            if p.is_file() and p.suffix.lower() in LABEL_EXTS
        ]

    # -----------------------------------------------------------
    # 内容去重（SHA256）
    # -----------------------------------------------------------
    @staticmethod
    def file_sha256(path: str | Path, chunk_size: int = 1 << 20) -> str:
        """按内容计算 SHA256（分块读取，避免大图占内存）。"""
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(chunk_size), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def dedupe_by_content(paths: list) -> tuple[list[Path], dict[str, list[Path]]]:
        """按内容哈希去重。

        Returns:
            (unique, duplicates)：unique 为首次出现的路径（保持原顺序）；
            duplicates 为 {sha256: [同内容的全部路径...]}（仅含重复 >= 2 的组）。
        """
        unique: list[Path] = []
        seen: dict[str, Path] = {}
        duplicates: dict[str, list[Path]] = {}
        for raw in paths:
            path = Path(raw)
            try:
                digest = DatasetService.file_sha256(path)
            except OSError as exc:
                logger.warning("读取失败 %s: %s", path, exc)
                continue
            first = seen.get(digest)
            if first is None:
                seen[digest] = path
                unique.append(path)
            else:
                duplicates.setdefault(digest, [first]).append(path)
        return unique, duplicates

    # -----------------------------------------------------------
    # 类别统计
    # -----------------------------------------------------------
    @staticmethod
    def _accumulate_classes(labels: list[Path]) -> tuple[list[str], dict[str, int]]:
        """读取标签首字段（YOLO class id），累计类别列表与样本数。"""
        class_names: list[str] = []
        class_counts: dict[str, int] = {}
        for label in labels:
            try:
                with open(label, "r", encoding="utf-8") as f:
                    for line in f:
                        stripped = line.strip()
                        if not stripped:
                            continue
                        cls_id = stripped.split()[0]
                        class_counts[cls_id] = class_counts.get(cls_id, 0) + 1
                        if cls_id not in class_names:
                            class_names.append(cls_id)
            except OSError as exc:
                logger.warning("读取标签失败 %s: %s", label, exc)
        return class_names, class_counts

    # -----------------------------------------------------------
    # 导入统计
    # -----------------------------------------------------------
    @staticmethod
    def summarize(directory: str | Path, name: str = "", dedupe: bool = True) -> Dataset:
        """分析目录，生成数据集统计信息（可选按内容去重）。"""
        directory = Path(directory)
        images = DatasetService.scan_images(directory)
        labels = DatasetService.scan_labels(directory)
        duplicate_count = 0
        if dedupe and images:
            images, duplicates = DatasetService.dedupe_by_content(images)
            duplicate_count = sum(len(group) - 1 for group in duplicates.values())

        dataset = Dataset(
            name=name or directory.name,
            source_path=str(directory),
            image_count=len(images),
            label_count=len(labels),
            duplicate_count=duplicate_count,
        )
        dataset.class_names, dataset.class_counts = DatasetService._accumulate_classes(labels)
        return dataset

    @staticmethod
    def summarize_files(paths: list, name: str = "", dedupe: bool = True) -> Dataset:
        """按给定文件列表（多选导入）生成统计信息。

        标签配对规则：与图片同目录、同文件名的 `.txt`。
        """
        images = [Path(p) for p in paths if Path(p).suffix.lower() in IMAGE_EXTS]
        duplicate_count = 0
        if dedupe and images:
            images, duplicates = DatasetService.dedupe_by_content(images)
            duplicate_count = sum(len(group) - 1 for group in duplicates.values())

        label_index: dict[str, Path] = {}
        for image in images:
            sibling = image.parent / f"{image.stem}.txt"
            if sibling.is_file():
                label_index.setdefault(image.stem, sibling)
        labels = list(label_index.values())

        source = _common_parent({str(p.parent) for p in images})
        dataset = Dataset(
            name=name or (Path(source).name if source else "未命名数据集"),
            source_path=source,
            image_count=len(images),
            label_count=len(labels),
            duplicate_count=duplicate_count,
        )
        dataset.class_names, dataset.class_counts = DatasetService._accumulate_classes(labels)
        return dataset

    # -----------------------------------------------------------
    # 标签配对 / 类别名
    # -----------------------------------------------------------
    @staticmethod
    def build_label_index(directory: str | Path) -> dict[str, Path]:
        """建立「图片文件名主干 → 标签文件」索引（YOLO 同名配对）。"""
        index: dict[str, Path] = {}
        for label in DatasetService.scan_labels(directory):
            index.setdefault(label.stem, label)
        return index

    @staticmethod
    def load_class_names(directory: str | Path) -> list[str]:
        """从来源目录的 data.yaml / data.yml / classes.txt 读取缺陷类别名。

        读取失败或不存在时返回空列表，由调用方决定回退策略。
        """
        directory = Path(directory)
        for filename in ("data.yaml", "data.yml"):
            yaml_file = directory / filename
            if not yaml_file.is_file():
                continue
            try:
                import yaml

                data = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
            except Exception as exc:  # noqa: BLE001 - YAML 解析异常类型较多
                logger.warning("读取 %s 失败: %s", yaml_file, exc)
                continue
            names = data.get("names")
            if isinstance(names, dict):
                try:
                    keys = sorted(names, key=lambda k: int(k))
                except (TypeError, ValueError):
                    keys = sorted(names, key=lambda k: str(k))
                return [str(names[k]) for k in keys]
            if isinstance(names, list):
                return [str(x) for x in names]

        classes_file = directory / "classes.txt"
        if classes_file.is_file():
            try:
                lines = classes_file.read_text(encoding="utf-8").splitlines()
            except OSError as exc:
                logger.warning("读取 %s 失败: %s", classes_file, exc)
                return []
            return [line.strip() for line in lines if line.strip()]
        return []

    # -----------------------------------------------------------
    # 数据集划分
    # -----------------------------------------------------------
    @staticmethod
    def _label_classes(label_path) -> set:
        """读取标签文件中出现的类别 id 集合（用于分层）。"""
        if label_path is None:
            return set()
        try:
            text = Path(label_path).read_text(encoding="utf-8")
        except OSError:
            return set()
        result: set[int] = set()
        for line in text.splitlines():
            parts = line.split()
            if not parts:
                continue
            try:
                result.add(int(float(parts[0])))
            except ValueError:
                continue
        return result

    @staticmethod
    def split_members(
        images: list,
        label_index: dict,
        split: tuple[float, float, float] = (0.7, 0.2, 0.1),
        seed: int = 0,
        stratified: bool = True,
    ) -> dict[str, list[Path]]:
        """把图片分配到 train / val / test。

        stratified=True 时按「类别组合」分层抽样，避免小类别整体落进同一子集；
        样本数少于 3 的类别组合整体归入训练集，避免验证集出现单样本噪声。
        """
        import random

        rng = random.Random(seed)
        buckets: dict[str, list[Path]] = {sub: [] for sub in SPLIT_SUBDIRS}
        items = [Path(p) for p in images]
        if not items:
            return buckets

        if not stratified:
            rng.shuffle(items)
            total = len(items)
            n_train = int(total * split[0])
            n_val = int(total * split[1])
            buckets["train"] = items[:n_train]
            buckets["val"] = items[n_train:n_train + n_val]
            buckets["test"] = items[n_train + n_val:]
            return buckets

        groups: dict[frozenset, list[Path]] = {}
        for image in items:
            key = frozenset(DatasetService._label_classes(label_index.get(image.stem)))
            groups.setdefault(key, []).append(image)

        for key in sorted(groups, key=lambda k: sorted(k)):
            members = sorted(groups[key])
            rng.shuffle(members)
            count = len(members)
            if count >= 3:
                n_train = max(1, int(count * split[0]))
                n_val = max(1, int(count * split[1]))
                while n_train + n_val >= count:
                    if n_val > 1:
                        n_val -= 1
                    elif n_train > 1:
                        n_train -= 1
                    else:
                        break
            else:
                n_train, n_val = count, 0
            buckets["train"].extend(members[:n_train])
            buckets["val"].extend(members[n_train:n_train + n_val])
            buckets["test"].extend(members[n_train + n_val:])
        return buckets

    @staticmethod
    def split_dataset(
        source_dir: str | Path,
        target_dir: str | Path,
        split: tuple[float, float, float] = (0.7, 0.2, 0.1),
        seed: int = 0,
        stratified: bool = True,
    ) -> dict:
        """把图片（及同名标签）复制到 images + labels 的 train/val/test 结构。

        参照开发文档 6.2 节 YOLO 数据集结构：
            dataset/images/{train,val,test}/
            dataset/labels/{train,val,test}/

        Args:
            stratified: 是否按类别分层抽样（默认开启）。

        Returns:
            统计字典：total 图片总数；train/val/test 各子集图片数；labels 已配对标签数。
        """
        source = Path(source_dir)
        target = Path(target_dir)
        images = DatasetService.scan_images(source)
        stats = {"total": len(images), "train": 0, "val": 0, "test": 0, "labels": 0}
        if not images:
            logger.warning("无图片可划分: %s", source)
            return stats

        label_index = DatasetService.build_label_index(source)
        buckets = DatasetService.split_members(
            images, label_index, split=split, seed=seed, stratified=stratified
        )

        for sub in SPLIT_SUBDIRS:
            img_dir = target / "images" / sub
            lab_dir = target / "labels" / sub
            img_dir.mkdir(parents=True, exist_ok=True)
            lab_dir.mkdir(parents=True, exist_ok=True)
            for img in buckets[sub]:
                shutil.copy2(img, img_dir / img.name)
                stats[sub] += 1
                label = label_index.get(img.stem)
                if label is not None:
                    shutil.copy2(label, lab_dir / label.name)
                    stats["labels"] += 1
        logger.info(
            "数据集划分完成（分层=%s）train=%s val=%s test=%s",
            stratified, stats["train"], stats["val"], stats["test"],
        )
        return stats

    # -----------------------------------------------------------
    # data.yaml 生成
    # -----------------------------------------------------------
    @staticmethod
    def write_data_yaml(
        target_dir: str | Path,
        classes: list[str],
        train: str,
        val: str,
        test: str | None = None,
    ) -> Path:
        """生成 YOLO 训练所需的 data.yaml。"""
        import yaml

        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)
        data = {
            "path": str(target),
            "train": train,
            "val": val,
        }
        if test:
            data["test"] = test
        data["names"] = {i: name for i, name in enumerate(classes)}
        yaml_path = target / "data.yaml"
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        logger.info("生成 data.yaml: %s", yaml_path)
        return yaml_path
