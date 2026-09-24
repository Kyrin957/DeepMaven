"""数据集处理服务：导入扫描、内容去重、统计、划分、YOLO 结构生成。"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
from pathlib import Path

from src.models.dataset import Dataset
from src.utils.constants import (
    ANOMALY_ABNORMAL_DIR,
    ANOMALY_NORMAL_DIR,
    IMAGE_EXTS,
    LABEL_EXTS,
    SPLIT_SUBDIRS,
    UNLABELED_LABEL,
)
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
        """扫描目录下的所有标签文件（.txt）。

        跳过 `notes/` 子目录：图片备注同样是 `.txt`，不应被当作 YOLO 标签。
        """
        directory = Path(directory)
        return [
            p for p in sorted(directory.rglob("*"))
            if p.is_file() and p.suffix.lower() in LABEL_EXTS
            and "notes" not in p.relative_to(directory).parts
        ]

    # -----------------------------------------------------------
    # 多来源目录（图库可由多个文件夹累加而成）
    # -----------------------------------------------------------
    @staticmethod
    def scan_images_multi(dirs: list) -> list[Path]:
        """按给定顺序扫描多个目录并合并图片清单（按路径去重）。"""
        result: list[Path] = []
        seen: set[str] = set()
        for raw in dirs or []:
            directory = Path(raw)
            if not directory.is_dir():
                continue
            for path in DatasetService.scan_images(directory):
                key = str(path)
                if key in seen:
                    continue
                seen.add(key)
                result.append(path)
        return result

    @staticmethod
    def scan_labels_multi(dirs: list) -> list[Path]:
        """按给定顺序扫描多个目录并合并标签清单（按路径去重）。"""
        result: list[Path] = []
        seen: set[str] = set()
        for raw in dirs or []:
            directory = Path(raw)
            if not directory.is_dir():
                continue
            for path in DatasetService.scan_labels(directory):
                key = str(path)
                if key in seen:
                    continue
                seen.add(key)
                result.append(path)
        return result

    @staticmethod
    def build_label_index_multi(dirs: list) -> dict[str, Path]:
        """多来源目录的「图片主干 → 标签」索引（先出现者优先）。"""
        index: dict[str, Path] = {}
        for raw in dirs or []:
            for stem, path in DatasetService.build_label_index(raw).items():
                index.setdefault(stem, path)
        return index

    @staticmethod
    def label_index_for(images: list, dirs: list = ()) -> dict[str, Path]:
        """图片清单 → 标签索引。

        来源目录内的图片按 YOLO 约定扫描配对；目录外的图片（多选文件导入）
        按「同目录同名 .txt」配对，保证零散文件也能带上标签。
        """
        index = DatasetService.build_label_index_multi(list(dirs or []))
        for raw in images or []:
            image = Path(raw)
            if image.stem in index:
                continue
            sibling = image.parent / f"{image.stem}.txt"
            if sibling.is_file():
                index[image.stem] = sibling
        return index

    @staticmethod
    def child_class_dirs_multi(dirs: list) -> dict[str, list[Path]]:
        """多来源目录的分类类别（子目录名）合并统计。"""
        result: dict[str, list[Path]] = {}
        for raw in dirs or []:
            for name, items in DatasetService.child_class_dirs(raw).items():
                result.setdefault(name, []).extend(items)
        return result

    @staticmethod
    def sort_images(paths: list, mode: str = "name") -> list[Path]:
        """按文件名 / 修改时间升序排序图片（同值时按文件名兜底）。"""
        items = [Path(p) for p in paths]

        if mode == "time":
            def key(path: Path):
                try:
                    return (path.stat().st_mtime, path.name.lower())
                except OSError:
                    return (0.0, path.name.lower())
        else:
            def key(path: Path):
                return path.name.lower()

        return sorted(items, key=key)

    @staticmethod
    def accumulate_classes(labels: list) -> tuple[list[str], dict[str, int]]:
        """统计标签中出现的类别（读取 YOLO 行的首字段）。"""
        return DatasetService._accumulate_classes([Path(p) for p in labels])

    @staticmethod
    def summarize_library(
        roots: list,
        images: list | None = None,
        label_index: dict | None = None,
        name: str = "",
        duplicate_count: int = 0,
    ) -> Dataset:
        """按「来源目录 + 图片清单 + 标签索引」重建数据集统计。

        与 `summarize` 的差别：图片清单与标签索引可以由调用方直接给定，
        因此支持多个来源目录（分多次导入）以及目录外的零散图片。
        """
        dirs = [Path(r) for r in (roots or [])]
        if images is None:
            images = DatasetService.scan_images_multi(dirs)
        images = [Path(p) for p in images]
        if label_index is None:
            label_index = DatasetService.build_label_index_multi(dirs)

        used_labels = [
            label_index[image.stem] for image in images if image.stem in label_index
        ]
        class_names, class_counts = DatasetService.accumulate_classes(used_labels)

        source = str(dirs[0]) if dirs else ""
        dataset = Dataset(
            name=name or (dirs[0].name if dirs else "未命名数据集"),
            source_path=source,
            source_paths=[str(d) for d in dirs],
            image_count=len(images),
            label_count=len(used_labels),
            duplicate_count=duplicate_count,
        )
        dataset.class_names = class_names
        dataset.class_counts = class_counts
        if not class_names:
            # 分类数据集没有标签文件，类别来自子目录名
            folder_classes = DatasetService.child_class_dirs_multi(dirs)
            if folder_classes:
                dataset.class_names = list(folder_classes)
                dataset.class_counts = {
                    class_name: len(items)
                    for class_name, items in folder_classes.items()
                }
        return dataset

    # -----------------------------------------------------------
    # 内容去重（SHA256）
    # -----------------------------------------------------------
    @staticmethod
    def remove_file(path: str | Path) -> None:
        """删除文件；只读文件先去掉只读属性再删。

        从压缩包（ZIP）解出来的图片在 Windows 上带「只读」属性，
        直接 `unlink()` 会抛 `PermissionError: [WinError 5]`，
        这里补一次「清属性 → 再删」，与资源管理器的行为保持一致。

        Raises:
            OSError: 文件不存在或确实无法删除时向上抛出，由调用方处理。
        """
        target = Path(path)
        try:
            target.unlink()
            return
        except PermissionError:
            pass
        mode = target.stat().st_mode
        os.chmod(target, mode | stat.S_IWRITE)
        target.unlink()

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
                        # 只认 YOLO 标签行（首字段为整数类别 id），
                        # 其它 .txt（备注、说明等）一律忽略，避免污染类别列表
                        try:
                            cls_id = str(int(float(stripped.split()[0])))
                        except ValueError:
                            continue
                        class_counts[cls_id] = class_counts.get(cls_id, 0) + 1
                        if cls_id not in class_names:
                            class_names.append(cls_id)
            except OSError as exc:
                logger.warning("读取标签失败 %s: %s", label, exc)
        return class_names, class_counts

    @staticmethod
    def child_class_dirs(directory: str | Path) -> dict[str, list[Path]]:
        """分类数据集：把「含图片的直接子目录」识别为类别。

        Returns:
            {类别名: [该类别下的图片路径, ...]}，按类别名排序。
        """
        directory = Path(directory)
        if not directory.is_dir():
            return {}
        result: dict[str, list[Path]] = {}
        for child in sorted(directory.iterdir()):
            if not child.is_dir():
                continue
            images = [
                p for p in sorted(child.iterdir())
                if p.is_file() and p.suffix.lower() in IMAGE_EXTS
            ]
            if images:
                result[child.name] = images
        return result

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
        if not dataset.class_names:
            # 分类数据集没有标签文件，类别来自子目录名
            folder_classes = DatasetService.child_class_dirs(directory)
            if folder_classes:
                dataset.class_names = list(folder_classes)
                dataset.class_counts = {
                    class_name: len(items)
                    for class_name, items in folder_classes.items()
                }
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
        if not dataset.class_names:
            by_parent: dict[str, int] = {}
            for image in images:
                by_parent[image.parent.name] = by_parent.get(image.parent.name, 0) + 1
            if len(by_parent) > 1:
                dataset.class_names = sorted(by_parent)
                dataset.class_counts = dict(sorted(by_parent.items()))
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
    def _group_key(image: Path, label_index: dict, layout: str) -> tuple:
        """分层抽样的分组键。

        分类任务看图片所在的子目录名；检测/分割看标签里出现的类别组合。
        """
        if layout == "classify":
            return (image.parent.name,)
        return tuple(sorted(DatasetService._label_classes(label_index.get(image.stem))))

    @staticmethod
    def split_members(
        images: list,
        label_index: dict,
        split: tuple[float, float, float] = (0.7, 0.2, 0.1),
        seed: int = 0,
        stratified: bool = True,
        layout: str = "detect",
        kinds: dict | None = None,
        counts: dict | None = None,
    ) -> dict[str, list[Path]]:
        """把图片分配到 train / val / test。

        stratified=True 时按分组键分层抽样，避免小类别整体落进同一子集；
        样本数少于 3 的组整体归入训练集，避免验证集出现单样本噪声。

        Args:
            kinds: 异常检测的「图片 → 类别」表（``str(图片)`` → normal /
                abnormal）；给出时改按类别分配（见 `_allocate_by_kind`），
                训练集只放良好图。
            counts: 按类别指定的各子集数量（`Split.anomaly_counts`）。
        """
        import random

        rng = random.Random(seed)
        buckets: dict[str, list[Path]] = {sub: [] for sub in SPLIT_SUBDIRS}
        items = [Path(p) for p in images]
        if not items:
            return buckets

        if kinds:
            return DatasetService._allocate_by_kind(
                items, kinds, counts or {}, split, seed
            )

        if not stratified:
            rng.shuffle(items)
            total = len(items)
            n_train = int(total * split[0])
            n_val = int(total * split[1])
            buckets["train"] = items[:n_train]
            buckets["val"] = items[n_train:n_train + n_val]
            buckets["test"] = items[n_train + n_val:]
            return buckets

        groups: dict[tuple, list[Path]] = {}
        for image in items:
            key = DatasetService._group_key(image, label_index, layout)
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
    def _proportional_sizes(total: int, weights: dict) -> dict[str, int]:
        """按权重把 ``total`` 拆成整数份（最大余数法，结果确定且合计等于 total）。"""
        cleaned = {key: max(0, int(value)) for key, value in weights.items()}
        span = sum(cleaned.values())
        if total <= 0 or span <= 0:
            return {key: 0 for key in cleaned}
        exact = {key: total * value / span for key, value in cleaned.items()}
        sizes = {key: int(value) for key, value in exact.items()}
        # 余数按「小数部分从大到小、同值按键名」补，保证与图片顺序无关
        order = sorted(exact, key=lambda key: (-(exact[key] - sizes[key]), key))
        for index in range(total - sum(sizes.values())):
            sizes[order[index % len(order)]] += 1
        return sizes

    @staticmethod
    def _allocate_by_kind(
        images: list[Path],
        kinds: dict,
        counts: dict,
        split: tuple[float, float, float],
        seed: int,
    ) -> dict[str, list[Path]]:
        """异常检测的分配：良好的训练图 + 异常的验证 / 测试图。

        约定（与异常检测「只用正常样本训练」一致）：
            * 训练集**只放良好图**，异常图一律进验证 / 测试；
            * 未标注（拿不到类别）的图片不参与分配；
            * ``counts`` 按权重使用：与实际图片数不一致时按比例归一化，
              图片增删后既不丢图，也不会把异常图挤进训练集。
        """
        import random

        rng = random.Random(seed)
        buckets: dict[str, list[Path]] = {sub: [] for sub in SPLIT_SUBDIRS}
        pools: dict[str, list[Path]] = {"normal": [], "abnormal": []}
        for image in images:
            kind = str(kinds.get(str(image), ""))
            if kind in pools:
                pools[kind].append(image)

        for kind, members in pools.items():
            if not members:
                continue
            rng.shuffle(members)
            weights = {
                sub: max(0, int((counts.get(kind) or {}).get(sub, 0)))
                for sub in SPLIT_SUBDIRS
            }
            if not any(weights.values()):
                # 未配置：按当前比例分配（异常图的比例去掉训练集后归一化）
                weights = {
                    sub: max(0, round(float(split[index]) * 1000))
                    for index, sub in enumerate(SPLIT_SUBDIRS)
                }
            if kind == "abnormal":
                weights["train"] = 0
            sizes = DatasetService._proportional_sizes(len(members), weights)
            cursor = 0
            for sub in SPLIT_SUBDIRS:
                buckets[sub].extend(members[cursor:cursor + sizes[sub]])
                cursor += sizes[sub]
        return buckets

    @staticmethod
    def split_dataset(
        source_dir: str | Path,
        target_dir: str | Path,
        split: tuple[float, float, float] = (0.7, 0.2, 0.1),
        seed: int = 0,
        stratified: bool = True,
        layout: str = "detect",
        images: list | None = None,
        label_index: dict | None = None,
        kinds: dict | None = None,
        counts: dict | None = None,
    ) -> dict:
        """把图片（及同名标签）复制为可训练的 YOLO 数据集结构。

        layout="detect"/"segment"（默认）：
            dataset/images/{train,val,test}/ + dataset/labels/{train,val,test}/
        layout="classify"：
            dataset/{train,val,test}/<类别名>/  （Ultralytics 分类按目录扫描）
        layout="ocr_det_rec"：
            在检测结构之上追加 OCR 产物（见 `_write_ocr_products`）
        layout="mask"：
            dataset/images/{train,val,test}/ + dataset/masks/{train,val,test}/
            （掩码为单通道 PNG，像素值 = cls_id + 1，0 为背景）

        Args:
            stratified: 是否分层抽样（分类任务恒按类别分层）。
            layout: 数据集结构，detect / segment / classify。
            images: 待划分的图片清单；为 None 时扫描 `source_dir`。
                图库可由多个文件夹组成，此时由调用方把合并后的清单传进来。
            label_index: 「图片主干 → 标签」索引；为 None 时按 `source_dir` 配对。

        Returns:
            统计字典：total / train / val / test / labels / layout。
        """
        source = Path(source_dir)
        target = Path(target_dir)
        if images is None:
            images = DatasetService.scan_images(source)
        else:
            images = [Path(p) for p in images]
        stats = {
            "total": len(images), "train": 0, "val": 0, "test": 0,
            "labels": 0, "masks": 0, "linked": 0, "copied": 0, "layout": layout,
        }
        if not images:
            logger.warning("无图片可划分: %s", source)
            return stats

        if layout == "anomaly_folder":
            # 异常检测：产物即 Anomalib Folder 结构——
            #   normal/       训练良好图（只用正常样本拟合）
            #   normal_test/  验证 + 测试的良好图（评估用）
            #   abnormal/     验证 + 测试的异常图（评估用）
            buckets = DatasetService.split_members(
                images, {}, split=split, seed=seed,
                stratified=stratified, layout=layout,
                kinds=kinds, counts=counts,
            )
            eval_members = buckets["val"] + buckets["test"]
            layout_dirs = {
                ANOMALY_NORMAL_DIR: list(buckets["train"]),
                f"{ANOMALY_NORMAL_DIR}_test": [
                    img for img in eval_members
                    if str((kinds or {}).get(str(img), "")) != "abnormal"
                ],
                ANOMALY_ABNORMAL_DIR: [
                    img for img in eval_members
                    if str((kinds or {}).get(str(img), "")) == "abnormal"
                ],
            }
            for name, members in layout_dirs.items():
                folder = target / name
                folder.mkdir(parents=True, exist_ok=True)
                for img in members:
                    DatasetService._count_place(stats, img, folder / img.name)
            # 统计仍按 train / val / test 记（与拆分页的分配一致），
            # 便于拆分列表、占比图与导出页沿用同一套口径
            for sub in SPLIT_SUBDIRS:
                stats[sub] = len(buckets[sub])
            stats["files"] = sum(len(items) for items in layout_dirs.values())
            logger.info(
                "异常检测数据集划分完成 normal=%s normal_test=%s abnormal=%s",
                len(layout_dirs[ANOMALY_NORMAL_DIR]),
                len(layout_dirs[f"{ANOMALY_NORMAL_DIR}_test"]),
                len(layout_dirs[ANOMALY_ABNORMAL_DIR]),
            )
            return stats

        if layout == "classify":
            buckets = DatasetService.split_members(
                images, {}, split=split, seed=seed,
                stratified=True, layout="classify",
            )
            for sub in SPLIT_SUBDIRS:
                for img in buckets[sub]:
                    dest = target / sub / img.parent.name
                    dest.mkdir(parents=True, exist_ok=True)
                    DatasetService._count_place(stats, img, dest / img.name)
                    stats[sub] += 1
            logger.info(
                "分类数据集划分完成 train=%s val=%s test=%s",
                stats["train"], stats["val"], stats["test"],
            )
            return stats

        if layout == "mask":
            from src.services.annotation_service import AnnotationService

            if label_index is None:
                label_index = DatasetService.build_label_index(source)
            buckets = DatasetService.split_members(
                images, label_index, split=split, seed=seed,
                stratified=stratified, layout=layout,
            )
            for sub in SPLIT_SUBDIRS:
                img_dir = target / "images" / sub
                mask_dir = target / "masks" / sub
                img_dir.mkdir(parents=True, exist_ok=True)
                mask_dir.mkdir(parents=True, exist_ok=True)
                for img in buckets[sub]:
                    DatasetService._count_place(stats, img, img_dir / img.name)
                    stats[sub] += 1
                    size = DatasetService.image_size(img)
                    if size is None:
                        logger.warning("读取图片尺寸失败，掩码跳过：%s", img)
                        continue
                    label = label_index.get(img.stem)
                    annotation = (
                        AnnotationService.load_yolo(label, size[0], size[1])
                        if label is not None else None
                    )
                    mask = DatasetService.mask_from_annotation(
                        annotation, size[0], size[1]
                    )
                    mask.save(mask_dir / f"{img.stem}.png")
                    stats["masks"] += 1
            logger.info(
                "掩码数据集划分完成 train=%s val=%s test=%s（掩码 %s 张）",
                stats["train"], stats["val"], stats["test"], stats["masks"],
            )
            return stats

        if label_index is None:
            label_index = DatasetService.build_label_index(source)
        buckets = DatasetService.split_members(
            images, label_index, split=split, seed=seed,
            stratified=stratified, layout=layout,
            kinds=kinds, counts=counts,
        )

        for sub in SPLIT_SUBDIRS:
            img_dir = target / "images" / sub
            lab_dir = target / "labels" / sub
            img_dir.mkdir(parents=True, exist_ok=True)
            lab_dir.mkdir(parents=True, exist_ok=True)
            for img in buckets[sub]:
                DatasetService._count_place(stats, img, img_dir / img.name)
                stats[sub] += 1
                label = label_index.get(img.stem)
                if label is not None:
                    DatasetService._place(label, lab_dir / label.name)
                    stats["labels"] += 1
        if layout == "ocr_det_rec":
            DatasetService._write_ocr_products(target, buckets, label_index, stats)
        logger.info(
            "数据集划分完成（分层=%s）train=%s val=%s test=%s",
            stratified, stats["train"], stats["val"], stats["test"],
        )
        return stats

    @staticmethod
    def image_size(path: str | Path) -> tuple[int, int] | None:
        """读取图片尺寸；失败返回 None。"""
        from PIL import Image

        try:
            with Image.open(path) as handle:
                return int(handle.width), int(handle.height)
        except Exception as exc:  # noqa: BLE001 - 图片损坏或格式异常
            logger.warning("读取图片尺寸失败 %s: %s", path, exc)
            return None

    @staticmethod
    def mask_from_annotation(annotation, width: int, height: int):
        """把标注栅格化为类别掩码（PIL 单通道：0 = 背景，cls_id + 1 = 类别）。

        矩形按框填充，多边形 / 掩码按顶点填充；文本框不属于分割目标，忽略。
        """
        from PIL import Image, ImageDraw

        mask = Image.new("L", (max(1, int(width)), max(1, int(height))), 0)
        if annotation is None:
            return mask
        draw = ImageDraw.Draw(mask)
        for item in annotation.items:
            if item.is_text:
                continue
            value = min(255, max(1, int(item.cls_id) + 1))
            if item.is_rect:
                x1, y1, x2, y2 = item.bounds()
                draw.rectangle(
                    [x1 * width, y1 * height, x2 * width, y2 * height], fill=value,
                )
                continue
            points = [(px * width, py * height) for px, py in item.points]
            if len(points) >= 3:
                draw.polygon(points, fill=value)
            elif len(points) == 2:
                draw.rectangle(
                    [points[0][0], points[0][1], points[1][0], points[1][1]],
                    fill=value,
                )
        return mask

    @staticmethod
    def _write_ocr_products(
        target: Path, buckets: dict, label_index: dict, stats: dict
    ) -> None:
        """写 OCR 训练产物（检测标签带转写 + 识别裁切图）。

            ocr/det/<split>.txt      每行 `图片路径<TAB>[{"transcription","points","difficult"}]`
                                     （PaddleOCR 检测数据格式，坐标为像素）
            ocr/rec/<split>/         按文本框裁切的小图
            ocr/rec_gt_<split>.txt   每行 `裁切图路径<TAB>文本`

        没有转写的框只进检测标签、不进识别集（数量记入 `ocr_skipped`）。
        """
        import json

        from PIL import Image

        from src.services.annotation_service import AnnotationService

        det_dir = target / "ocr" / "det"
        rec_dir = target / "ocr" / "rec"
        det_rows = rec_rows = skipped = 0

        for sub in SPLIT_SUBDIRS:
            det_lines: list[str] = []
            rec_lines: list[str] = []
            for image in buckets.get(sub, []):
                label = label_index.get(image.stem)
                if label is None:
                    continue
                try:
                    with Image.open(image) as handle:
                        width, height = handle.size
                        source = handle.convert("RGB")
                except Exception as exc:  # noqa: BLE001 - 单张读失败不影响整体
                    logger.warning("读取 OCR 图片失败 %s: %s", image, exc)
                    continue

                annotation = AnnotationService.load_yolo(label, width, height)
                entries: list[dict] = []
                for index, item in enumerate(annotation.items):
                    if not item.is_rect:
                        continue
                    x1, y1, x2, y2 = item.bounds()
                    entries.append({
                        "transcription": str(item.text or ""),
                        "points": [
                            [round(x1 * width, 2), round(y1 * height, 2)],
                            [round(x2 * width, 2), round(y1 * height, 2)],
                            [round(x2 * width, 2), round(y2 * height, 2)],
                            [round(x1 * width, 2), round(y2 * height, 2)],
                        ],
                        "difficult": False,
                    })
                    if not item.text:
                        skipped += 1
                        continue
                    # 归一化坐标乘回像素时会有浮点误差（如 11.9999），
                    # 截断会让裁切框少 1 像素，因此按四舍五入取整并夹到图内
                    crop = source.crop((
                        max(0, round(x1 * width)), max(0, round(y1 * height)),
                        min(width, round(x2 * width)), min(height, round(y2 * height)),
                    ))
                    if crop.width < 2 or crop.height < 2:
                        skipped += 1
                        continue
                    name = f"{image.stem}_{index}.jpg"
                    folder = rec_dir / sub
                    folder.mkdir(parents=True, exist_ok=True)
                    crop.save(folder / name, quality=95)
                    rec_lines.append(f"ocr/rec/{sub}/{name}\t{item.text}")
                    rec_rows += 1

                if entries:
                    det_rows += 1
                    det_lines.append(
                        f"images/{sub}/{image.name}\t"
                        + json.dumps(entries, ensure_ascii=False)
                    )

            if det_lines:
                det_dir.mkdir(parents=True, exist_ok=True)
                (det_dir / f"{sub}.txt").write_text(
                    "\n".join(det_lines) + "\n", encoding="utf-8"
                )
            if rec_lines:
                (target / "ocr" / f"rec_gt_{sub}.txt").write_text(
                    "\n".join(rec_lines) + "\n", encoding="utf-8"
                )

        stats.update({"ocr_det": det_rows, "ocr_rec": rec_rows, "ocr_skipped": skipped})
        logger.info(
            "OCR 数据已生成：检测 %s 张 / 识别 %s 张（缺转写 %s 个框）",
            det_rows, rec_rows, skipped,
        )

    @staticmethod
    def _place(source: Path, destination: Path) -> str:
        """把文件放到目标位置：硬链接 → 软链接 → 复制（逐级回退）。

        为什么不能只给一份「文件名清单」：
            * **分类任务**：Ultralytics 的 `ClassificationDataset` 直接包装
              `torchvision.datasets.ImageFolder`，只认 `root/<类别>/<图片>`
              目录结构，传入 `.txt` 会被拒绝；
            * **检测 / 分割**：虽然 `data.yaml` 接受 `.txt` 图片清单，但标签路径
              由 `img2label_paths()` 从图片路径推导（`/images/` → `/labels/`），
              并不读标签清单，因此仍需按 `images/ + labels/` 组织。

        这里用链接代替复制：目录结构完整，但不额外占用磁盘
        （跨盘且无法建链接时才回退为复制）。

        Returns:
            "linked" / "symlinked" / "copied"。
        """
        if destination.exists():
            try:
                # 划分产物可能是上一次留下的只读硬链接，同样需要先清属性
                DatasetService.remove_file(destination)
            except OSError:
                pass
        try:
            os.link(source, destination)
            return "linked"
        except OSError:
            pass
        try:
            os.symlink(source, destination)
            return "symlinked"
        except OSError:
            shutil.copy2(source, destination)
            return "copied"

    @staticmethod
    def _count_place(stats: dict, source: Path, destination: Path) -> None:
        """放置文件并累计「链接 / 复制」计数。"""
        mode = DatasetService._place(source, destination)
        if mode == "copied":
            stats["copied"] = stats.get("copied", 0) + 1
        else:
            stats["linked"] = stats.get("linked", 0) + 1

    @staticmethod
    def make_cover(image_path, size: int = 256, quality: int = 85) -> bytes | None:
        """生成项目封面缩略图（JPEG 字节），失败返回 None。

        服务层不依赖 Qt，因此用 PIL 实现。
        """
        try:
            import io

            from PIL import Image

            with Image.open(image_path) as image:
                image = image.convert("RGB")
                image.thumbnail((size, size))
                buffer = io.BytesIO()
                image.save(buffer, "JPEG", quality=quality)
            return buffer.getvalue()
        except Exception as exc:  # noqa: BLE001 - 图片格式 / IO 异常类型较多
            logger.warning("生成项目封面失败: %s", exc)
            return None

    @staticmethod
    def label_has_content(path) -> bool:
        """标签文件是否存在且非空。"""
        try:
            target = Path(path)
            return target.is_file() and target.stat().st_size > 0
        except OSError:
            return False

    @staticmethod
    def image_class_name(image, label_index: dict, layout: str) -> str:
        """图片所属类别名（分类看目录名，检测/分割看标签首个类别 id）。"""
        return DatasetService._class_label_of(Path(image), label_index, layout)

    @staticmethod
    def _class_label_of(image: Path, label_index: dict, layout: str) -> str:
        """图片所属类别名：分类看目录名，检测/分割看标签中的首个类别 id。"""
        if layout == "classify":
            return image.parent.name
        label = label_index.get(image.stem)
        if label is None:
            return UNLABELED_LABEL
        classes = DatasetService._label_classes(label)
        if not classes:
            return UNLABELED_LABEL
        return str(sorted(classes)[0])

    @staticmethod
    def preview_split(
        source_dir: str | Path,
        split: tuple[float, float, float] = (0.7, 0.2, 0.1),
        seed: int = 0,
        stratified: bool = True,
        layout: str = "detect",
        images: list | None = None,
        label_index: dict | None = None,
        kinds: dict | None = None,
        counts: dict | None = None,
    ) -> dict:
        """**不落盘**地计算划分结果，供界面预览（拆分页的饼图与类别分布）。

        Args:
            images: 待划分的图片清单；为 None 时扫描 `source_dir`。
            label_index: 「图片主干 → 标签」索引；为 None 时按 `source_dir` 配对。

        Returns:
            {
              "total": 图片总数,
              "layout": 结构类型,
              "subsets": {train/val/test: {"count": n, "classes": {类别: n}}},
              "per_class": {类别: {"total": n, "train": n, "val": n, "test": n}},
            }
        """
        source = Path(source_dir) if source_dir else None
        if images is None:
            images = DatasetService.scan_images(source) if source else []
        result: dict = {
            "total": len(images), "layout": layout,
            "subsets": {}, "per_class": {},
        }
        if not images:
            return result

        if label_index is None:
            label_index = (
                {} if layout == "classify" or source is None
                else DatasetService.build_label_index(source)
            )
        buckets = DatasetService.split_members(
            images, label_index, split=split, seed=seed,
            stratified=stratified, layout=layout,
            kinds=kinds, counts=counts,
        )

        per_class: dict[str, dict] = {}
        for sub in SPLIT_SUBDIRS:
            counts: dict[str, int] = {}
            for image in buckets[sub]:
                name = DatasetService._class_label_of(image, label_index, layout)
                counts[name] = counts.get(name, 0) + 1
                entry = per_class.setdefault(
                    name, {"total": 0, "train": 0, "val": 0, "test": 0}
                )
                entry["total"] += 1
                entry[sub] += 1
            result["subsets"][sub] = {
                "count": len(buckets[sub]), "classes": counts,
            }
        result["per_class"] = per_class
        return result

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
