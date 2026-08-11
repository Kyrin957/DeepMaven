"""数据集处理服务：导入扫描、统计、划分、YOLO 结构生成。"""

from __future__ import annotations

from pathlib import Path

from src.models.dataset import Dataset
from src.utils.constants import IMAGE_EXTS, LABEL_EXTS, SPLIT_SUBDIRS
from src.utils.logger import get_logger

logger = get_logger("dataset")


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

    @staticmethod
    def summarize(directory: str | Path, name: str = "") -> Dataset:
        """分析目录，生成数据集统计信息。"""
        directory = Path(directory)
        images = DatasetService.scan_images(directory)
        labels = DatasetService.scan_labels(directory)
        dataset = Dataset(
            name=name or directory.name,
            source_path=str(directory),
            image_count=len(images),
            label_count=len(labels),
        )
        # 类别统计：读取标签首字段（YOLO 格式 class id）
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
        dataset.class_names = class_names
        dataset.class_counts = class_counts
        return dataset

    # -----------------------------------------------------------
    # 数据集划分
    # -----------------------------------------------------------
    @staticmethod
    def split_dataset(
        source_dir: str | Path,
        target_dir: str | Path,
        split: tuple[float, float, float] = (0.7, 0.2, 0.1),
        seed: int = 0,
    ) -> None:
        """按比例将图片（及同名标签）划分到 images + labels 的 train/val/test 结构。

        参照开发文档 6.2 节 YOLO 数据集结构：
            dataset/images/{train,val,test}/
            dataset/labels/{train,val,test}/

        实现说明：本题骨架阶段仅记录意图；实际文件复制/移动逻辑在
        后续迭代中结合标注数据补齐。
        """
        import random

        images = DatasetService.scan_images(source_dir)
        if not images:
            logger.warning("无图片可划分: %s", source_dir)
            return

        rng = random.Random(seed)
        rng.shuffle(images)
        n = len(images)
        n_train = int(n * split[0])
        n_val = int(n * split[1])
        buckets = images[:n_train], images[n_train:n_train + n_val], images[n_train + n_val:]

        target = Path(target_dir)
        for sub, bucket in zip(SPLIT_SUBDIRS, buckets):
            (target / "images" / sub).mkdir(parents=True, exist_ok=True)
            (target / "labels" / sub).mkdir(parents=True, exist_ok=True)
            for img in bucket:
                pass  # 具体文件复制逻辑待实现
        logger.info(
            "数据集划分完成 train=%s val=%s test=%s",
            n_train, n_val, n - n_train - n_val,
        )

    # -----------------------------------------------------------
    # data.yaml 生成
    # -----------------------------------------------------------
    @staticmethod
    def write_data_yaml(target_dir: str | Path, classes: list[str], train: str, val: str) -> Path:
        """生成 YOLO 训练所需的 data.yaml。"""
        import yaml

        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)
        data = {
            "path": str(target),
            "train": train,
            "val": val,
            "names": {i: name for i, name in enumerate(classes)},
        }
        yaml_path = target / "data.yaml"
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        logger.info("生成 data.yaml: %s", yaml_path)
        return yaml_path