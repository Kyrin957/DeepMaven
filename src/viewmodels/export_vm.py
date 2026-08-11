"""模型导出 ViewModel：格式选择与导出执行。"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from src.models.training import ExportConfig
from src.services.export_service import ExportService
from src.utils.logger import get_logger

logger = get_logger("export_vm")


class ExportViewModel(QObject):
    """模型导出页的业务逻辑。"""

    configChanged = Signal(object)       # ExportConfig
    exportStarted = Signal()
    exportFinished = Signal(str)         # 导出文件路径
    progressChanged = Signal(float)      # 0~1
    message = Signal(str, str)           # level, text

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._config = ExportConfig(output_dir="runs/export")
        self._service = None

    @property
    def config(self) -> ExportConfig:
        return self._config

    # -----------------------------------------------------------
    # 命令
    # -----------------------------------------------------------
    def set_format(self, fmt: str) -> None:
        self._config.format = fmt
        self.configChanged.emit(self._config)

    def set_weights(self, path: str) -> None:
        self._config.weights_path = path
        self.configChanged.emit(self._config)

    def set_output_dir(self, path: str) -> None:
        self._config.output_dir = path
        self.configChanged.emit(self._config)

    def export(self) -> None:
        """执行导出。骨架阶段校验参数并记录意图。"""
        if not self._config.weights_path:
            self.message.emit("warning", "请先选择待导出的模型权重")
            return
        logger.info("导出配置: %s", self._config.to_dict() if hasattr(self._config, "to_dict") else self._config)
        self.exportStarted.emit()
        self.progressChanged.emit(0.5)
        self.exportFinished.emit(
            f"runs/export/{self._config.weights_path.rsplit('/', 1)[-1].rsplit('.', 1)[0]}.{self._config.format}"
        )
        self.progressChanged.emit(1.0)
        self.message.emit("success", "导出完成（骨架阶段模拟）")