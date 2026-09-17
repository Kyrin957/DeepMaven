"""模型管理 ViewModel：预训练模型选择、权重导入与模型信息探测。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from src.services.yolo_service import YOLOService
from src.utils.constants import YOLO_MODEL_VARIANTS
from src.utils.logger import get_logger
from src.utils.workers import FunctionWorker

logger = get_logger("model_vm")


class ModelViewModel(QObject):
    """模型管理页的业务逻辑。"""

    modelSelected = Signal(dict)         # 模型变体信息
    weightsImported = Signal(str)        # 权重路径
    modelInfo = Signal(dict)             # 模型基础信息
    taskStarted = Signal(str)            # 后台任务开始（任务名）
    taskProgress = Signal(int, str)      # 百分比, 描述
    taskFinished = Signal(str)           # 完成描述
    taskFailed = Signal(str)             # 失败描述
    message = Signal(str, str)           # level, text

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._variants = YOLO_MODEL_VARIANTS
        self._selected: dict = self._variants[0]
        self._weights: str = ""
        self._worker: FunctionWorker | None = None

    # -----------------------------------------------------------
    # 查询
    # -----------------------------------------------------------
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
        """导入自定义预训练权重（.pt）并探测模型信息。"""
        if not path:
            self.message.emit("warning", "请选择权重文件")
            return
        if not Path(path).is_file():
            self.message.emit("error", f"权重文件不存在：{path}")
            return
        self._weights = path
        self.weightsImported.emit(path)
        self._probe(path, f"探测权重 {Path(path).name}")

    def prepare_variant(self) -> None:
        """加载当前变体的官方预训练权重（缺失时由 Ultralytics 自动下载）。"""
        weights = f"{self._selected['key']}.pt"
        self._weights = weights
        self.weightsImported.emit(weights)
        self._probe(weights, f"加载 {weights}")

    def clear_weights(self) -> None:
        self._weights = ""
        self.modelInfo.emit({})
        self.message.emit("info", "已清除自定义权重，将使用官方预训练权重")

    # -----------------------------------------------------------
    # 后台探测
    # -----------------------------------------------------------
    def is_busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _probe(self, weights: str, name: str) -> None:
        if self.is_busy():
            self.message.emit("warning", "已有任务在运行")
            return

        def job(progress, _is_cancelled):
            progress(10, "加载模型…")
            info = YOLOService(weights).info()
            progress(100, "完成")
            return info

        worker = FunctionWorker(job, self)
        worker.progress.connect(self.taskProgress.emit)
        worker.finishedOk.connect(lambda info: self._on_probe_done(name, info))
        worker.failed.connect(lambda error: self.taskFailed.emit(f"{name}失败：{error}"))
        self._worker = worker
        self.taskStarted.emit(name)
        worker.start()

    def _on_probe_done(self, name: str, info: dict) -> None:
        logger.info("模型信息: %s", info)
        self.modelInfo.emit(info)
        self.taskFinished.emit(f"{name}完成")
        self.message.emit("success", f"{name}完成")
