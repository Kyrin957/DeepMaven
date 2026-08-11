"""模型管理 ViewModel：预训练模型选择与权重导入。"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from src.utils.constants import YOLO_MODEL_VARIANTS
from src.utils.logger import get_logger

logger = get_logger("model_vm")


class ModelViewModel(QObject):
    """模型管理页的业务逻辑。"""

    modelSelected = Signal(dict)         # 模型变体信息
    weightsImported = Signal(str)        # 权重路径
    modelInfo = Signal(dict)             # 模型基础信息
    message = Signal(str, str)           # level, text

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._variants = YOLO_MODEL_VARIANTS
        self._selected: dict = self._variants[0]
        self._weights: str = ""

    @property
    def variants(self) -> list[dict]:
        return self._variants

    @property
    def selected(self) -> dict:
        return self._selected

    @property
    def weights(self) -> str:
        return self._weights

    # -----------------------------------------------------------
    # 命令
    # -----------------------------------------------------------
    def select_variant(self, key: str) -> None:
        """按 key 选择模型变体。"""
        for variant in self._variants:
            if variant["key"] == key:
                self._selected = variant
                self.modelSelected.emit(variant)
                self.message.emit("info", f"已选择模型：{variant['label']}")
                return
        self.message.emit("warning", f"未知模型变体：{key}")

    def import_weights(self, path: str) -> None:
        """导入自定义预训练权重 (.pt)。"""
        self._weights = path
        logger.info("导入权重: %s", path)
        self.weightsImported.emit(path)
        self.modelInfo.emit({"weights": path, "task": "detect"})
        self.message.emit("success", f"权重已导入：{path}")

    def clear_weights(self) -> None:
        self._weights = ""
        self.modelInfo.emit({})
        self.message.emit("info", "已清除自定义权重，将使用官方预训练权重")