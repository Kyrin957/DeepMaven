"""数据集 ViewModel：导入预览、统计、划分。"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from src.models.dataset import Dataset
from src.services.dataset_service import DatasetService
from src.utils.logger import get_logger

logger = get_logger("dataset_vm")


class DatasetViewModel(QObject):
    """数据管理页的业务逻辑。"""

    datasetChanged = Signal(object)      # Dataset
    datasetImported = Signal(object)     # Dataset
    splitChanged = Signal(tuple)         # (train, val, test) 比例
    message = Signal(str, str)           # level, text

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._service = DatasetService()
        self._dataset: Dataset | None = None

    @property
    def dataset(self) -> Dataset | None:
        return self._dataset

    # -----------------------------------------------------------
    # 命令
    # -----------------------------------------------------------
    def import_images(self, directory: str) -> Dataset:
        """导入图片目录并生成统计。"""
        dataset = self._service.summarize(directory)
        self._dataset = dataset
        logger.info(
            "导入数据集 %s: %s 张图片, %s 个标签",
            dataset.name, dataset.image_count, dataset.label_count,
        )
        self.datasetImported.emit(dataset)
        self.datasetChanged.emit(dataset)
        self.message.emit(
            "success",
            f"导入完成：{dataset.image_count} 张图片，{dataset.class_count} 个类别",
        )
        return dataset

    def set_split(self, train: float, val: float, test: float) -> None:
        """更新划分比例（应满足 train+val+test ≈ 1）。"""
        if self._dataset is not None:
            self._dataset.split_train = train
            self._dataset.split_val = val
            self._dataset.split_test = test
        self.splitChanged.emit((train, val, test))
        self.message.emit("info", f"划分比例已更新：{train:.0%}/{val:.0%}/{test:.0%}")

    def apply_split(self) -> None:
        """执行数据集划分（骨架阶段记录意图）。"""
        if self._dataset is None or not self._dataset.source_path:
            self.message.emit("warning", "请先导入数据集")
            return
        self.message.emit("info", "数据集划分功能将在后续迭代中实现")

    def clear(self) -> None:
        self._dataset = None
        self.datasetChanged.emit(None)