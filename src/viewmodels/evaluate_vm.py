"""模型评估 ViewModel：图片/相机输入、实时检测。"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from src.models.training import EvaluationConfig
from src.utils.logger import get_logger

logger = get_logger("evaluate_vm")


class EvaluateViewModel(QObject):
    """模型评估页的业务逻辑。"""

    configChanged = Signal(object)       # EvaluationConfig
    detectionDone = Signal(dict)         # {count, elapsed, results}
    message = Signal(str, str)           # level, text

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._config = EvaluationConfig()

    @property
    def config(self) -> EvaluationConfig:
        return self._config

    # -----------------------------------------------------------
    # 命令
    # -----------------------------------------------------------
    def set_source(self, source_type: str) -> None:
        self._config.source_type = source_type
        self.configChanged.emit(self._config)

    def set_weights(self, path: str) -> None:
        self._config.weights_path = path
        self.configChanged.emit(self._config)

    def set_thresholds(self, confidence: float, iou: float) -> None:
        self._config.confidence = confidence
        self._config.iou = iou
        self.configChanged.emit(self._config)

    def run_detection(self, source: str | None = None) -> None:
        """执行一次检测（骨架阶段仅模拟结果）。"""
        if not self._config.weights_path:
            self.message.emit("warning", "请先在模型管理页导入模型权重")
            return
        self.configChanged.emit(self._config)
        self.detectionDone.emit({
            "count": 0,
            "elapsed": 0.0,
            "results": [],
        })
        self.message.emit("info", "检测结果（骨架阶段模拟数据）")

    def export_report(self) -> None:
        self.message.emit("info", "检测报告导出将在后续迭代中实现")