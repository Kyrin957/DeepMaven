"""模型训练 ViewModel：参数配置与训练状态机。"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from src.models.training import TrainingConfig
from src.services.yolo_service import YOLOService
from src.utils.logger import get_logger

logger = get_logger("train_vm")


class TrainViewModel(QObject):
    """模型训练页的业务逻辑。

    骨架阶段：维护训练配置与状态信号；实际的训练线程（QThread）
    在后续迭代接入 YOLOService 时实现。
    """

    configChanged = Signal(object)       # TrainingConfig
    statusChanged = Signal(str)          # idle/running/paused/finished/error
    progressChanged = Signal(float)      # 0~1
    metricsChanged = Signal(dict)        # {loss, mAP50, precision, recall, epoch}
    logAppended = Signal(str)            # 训练日志行
    message = Signal(str, str)           # level, text

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._config = TrainingConfig()
        self._yolo = YOLOService()

    @property
    def config(self) -> TrainingConfig:
        return self._config

    # -----------------------------------------------------------
    # 参数更新
    # -----------------------------------------------------------
    def update_config(self, **kwargs) -> None:
        for key, value in kwargs.items():
            if hasattr(self._config, key):
                setattr(self._config, key, value)
        self.configChanged.emit(self._config)

    def set_task_type(self, task_type: str) -> None:
        self.update_config(task_type=task_type)

    def set_model_key(self, key: str) -> None:
        self.update_config(model_key=key)

    # -----------------------------------------------------------
    # 训练执行（骨架）
    # -----------------------------------------------------------
    def start(self) -> None:
        if self._config.status == "running":
            self.message.emit("warning", "训练已在运行中")
            return
        if not self._config.data_yaml:
            self.message.emit("warning", "请先在此页或数据管理页设置数据集 (data.yaml)")
            return
        logger.info("发起训练: %s", self._config.to_dict())
        self._config.status = "running"
        self.statusChanged.emit("running")
        self.logAppended.emit("训练任务已排队（骨架阶段，实际训练待接入）")

    def stop(self) -> None:
        self._config.status = "idle"
        self.statusChanged.emit("idle")
        self.logAppended.emit("训练已停止")

    def reset(self) -> None:
        """重置训练状态。"""
        self._config.status = "idle"
        self._config.progress = 0.0
        self._config.current_epoch = 0
        self.statusChanged.emit("idle")
        self.progressChanged.emit(0.0)
        self.metricsChanged.emit({})
        self.logAppended.emit("训练状态已重置")