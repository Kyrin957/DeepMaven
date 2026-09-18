"""模型导出 ViewModel：格式选择与后台导出执行。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from src.models.training import ExportConfig
from src.services.export_service import ExportService
from src.utils.logger import get_logger
from src.utils.workers import FunctionWorker
from src.viewmodels.project_vm import ProjectViewModel

logger = get_logger("export_vm")


class ExportViewModel(QObject):
    """模型导出页的业务逻辑。"""

    configChanged = Signal(object)       # ExportConfig
    exportStarted = Signal()
    exportFinished = Signal(str)         # 导出文件路径
    taskStarted = Signal(str)
    taskProgress = Signal(int, str)
    taskFinished = Signal(str)
    taskFailed = Signal(str)
    message = Signal(str, str)           # level, text

    def __init__(
        self,
        project_vm: ProjectViewModel | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._project_vm = project_vm
        self._config = ExportConfig(output_dir="runs/export")
        self._worker: FunctionWorker | None = None

    def load_from_project(self, project) -> None:
        """打开 / 新建项目后把导出参数绑定到项目。"""
        self._config = (
            project.export if project is not None
            else ExportConfig(output_dir="runs/export")
        )
        self.configChanged.emit(self._config)

    def _emit(self) -> None:
        """配置变更：标记项目待保存并广播。"""
        project = self._project_vm.project if self._project_vm else None
        if project is not None and self._config is project.export:
            project.touch()
        self.configChanged.emit(self._config)

    @property
    def config(self) -> ExportConfig:
        return self._config

    def is_busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    # -----------------------------------------------------------
    # 配置
    # -----------------------------------------------------------
    def set_format(self, fmt: str) -> None:
        self._config.format = fmt
        self._emit()

    def set_weights(self, path: str) -> None:
        self._config.weights_path = path
        self._emit()

    def set_output_dir(self, path: str) -> None:
        self._config.output_dir = path
        self._emit()

    def set_imgsz(self, value: int) -> None:
        self._config.imgsz = int(value)
        self._emit()

    def set_opset(self, value: int) -> None:
        self._config.opset = int(value)
        self._emit()

    def set_dynamic(self, enabled: bool) -> None:
        self._config.dynamic = bool(enabled)
        self._emit()

    def set_simplify(self, enabled: bool) -> None:
        self._config.simplify = bool(enabled)
        self._emit()

    # -----------------------------------------------------------
    # 执行
    # -----------------------------------------------------------
    def export(self) -> None:
        """在后台线程执行模型导出。"""
        if self.is_busy():
            self.message.emit("warning", "已有任务在运行")
            return
        if not self._config.weights_path:
            self.message.emit("warning", "请先选择待导出的模型权重")
            return
        if not Path(self._config.weights_path).is_file():
            self.message.emit("error", f"权重文件不存在：{self._config.weights_path}")
            return

        config = self._config
        self.exportStarted.emit()

        def job(progress, _is_cancelled):
            progress(10, f"导出为 {config.format}")
            return ExportService().export(config)

        def done(path):
            self.exportFinished.emit(str(path))
            self.message.emit("success", f"导出完成：{path}")

        self._start_worker(job, "模型导出", on_done=done)

    # -----------------------------------------------------------
    # 内部
    # -----------------------------------------------------------
    def _start_worker(self, job, name: str, on_done=None) -> None:
        worker = FunctionWorker(job, self)
        worker.progress.connect(self.taskProgress.emit)
        worker.finishedOk.connect(
            lambda payload: self._on_task_done(name, payload, on_done)
        )
        worker.failed.connect(lambda error: self._on_task_failed(name, error))
        self._worker = worker
        self.taskStarted.emit(name)
        worker.start()

    def _on_task_done(self, name: str, payload, on_done) -> None:
        if on_done is not None:
            try:
                on_done(payload)
            except Exception as exc:  # noqa: BLE001 - 结果应用异常需回传界面
                logger.warning("%s结果应用失败: %s", name, exc)
                self.taskFailed.emit(f"{name}失败：{exc}")
                return
            self.taskFinished.emit(f"{name}完成")
        else:
            self.taskFinished.emit(str(payload) if payload else f"{name}完成")

    def _on_task_failed(self, name: str, error: str) -> None:
        logger.warning("%s失败: %s", name, error)
        self.taskFailed.emit(f"{name}失败：{error}")
