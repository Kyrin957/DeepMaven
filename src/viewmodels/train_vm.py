"""模型训练 ViewModel：参数配置、子进程训练与实时监控。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from src.models.training import TrainingConfig
from src.services.train_service import TrainService
from src.utils.logger import get_logger
from src.viewmodels.project_vm import ProjectViewModel

logger = get_logger("train_vm")


def _pick(metrics: dict, keys: tuple) -> float:
    """从指标字典中按候选键名取第一个可用值。"""
    for key in keys:
        if key in metrics:
            try:
                return float(metrics[key])
            except (TypeError, ValueError):
                continue
    return 0.0


class TrainViewModel(QObject):
    """模型训练页的业务逻辑。

    训练在独立子进程中执行（见 `services/train_service.py`），
    进度与指标通过 JSON 事件流回传，可随时强制终止。
    """

    configChanged = Signal(object)       # TrainingConfig
    statusChanged = Signal(str)          # idle/running/finished/error
    progressChanged = Signal(float)      # 0~1
    metricsChanged = Signal(dict)        # {epoch, total, loss, mAP50, precision, recall}
    logAppended = Signal(str)            # 训练日志行
    artifactsReady = Signal(dict)        # 训练产物 {save_dir, best, last}
    message = Signal(str, str)           # level, text

    def __init__(
        self,
        project_vm: ProjectViewModel | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._config = TrainingConfig()
        self._project_vm = project_vm
        self._service = TrainService(self)
        self._service.logLine.connect(self.logAppended.emit)
        self._service.event.connect(self._on_event)
        self._service.finished.connect(self._on_finished)
        self._service.failed.connect(self._on_failed)

    # -----------------------------------------------------------
    # 查询
    # -----------------------------------------------------------
    @property
    def config(self) -> TrainingConfig:
        return self._config

    def is_running(self) -> bool:
        return self._service.is_running()

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

    def set_data_yaml(self, path: str) -> None:
        self.update_config(data_yaml=path)

    # -----------------------------------------------------------
    # 训练执行
    # -----------------------------------------------------------
    def start(self) -> None:
        if self.is_running():
            self.message.emit("warning", "训练已在运行中")
            return
        if self._config.task_type == "anomaly":
            if not self._config.anomaly_root:
                self.message.emit(
                    "warning", "请先选择异常检测数据目录（需含 normal/ 与 abnormal/）"
                )
                return
        elif not self._config.data_yaml:
            self.message.emit("warning", "请先在数据管理页完成划分，生成数据集配置")
            return

        self._config.progress = 0.0
        self._config.current_epoch = 0
        self.progressChanged.emit(0.0)
        self.metricsChanged.emit({})
        self.logAppended.emit("=== 启动训练 ===")
        self._set_status("running")
        if not self._service.start(self._config):
            self._set_status("error")

    def stop(self) -> None:
        if not self.is_running():
            self.message.emit("warning", "当前没有正在进行的训练")
            return
        self.logAppended.emit("=== 正在终止训练 ===")
        self._service.stop()

    def reset(self) -> None:
        if self.is_running():
            self.message.emit("warning", "请先终止训练")
            return
        self._config.status = "idle"
        self._config.progress = 0.0
        self._config.current_epoch = 0
        self._config.loss = 0.0
        self._config.mAP50 = 0.0
        self._config.precision = 0.0
        self._config.recall = 0.0
        self.statusChanged.emit("idle")
        self.progressChanged.emit(0.0)
        self.metricsChanged.emit({})
        self.logAppended.emit("训练状态已重置")

    # -----------------------------------------------------------
    # 子进程事件
    # -----------------------------------------------------------
    def _on_event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "phase":
            self.logAppended.emit(f"[阶段] {event.get('text', '')}")
        elif kind == "epoch":
            self._apply_epoch(event)
        elif kind == "metrics":
            self._apply_metrics(event.get("metrics") or {})
        elif kind == "error":
            self.logAppended.emit(f"[错误] {event.get('text', '')}")

    def _apply_metrics(self, metrics: dict) -> None:
        """处理评估阶段汇总指标（异常检测的 AUROC 等）。"""
        auroc = _pick(metrics, ("image_AUROC", "image_AUROC_macro", "pixel_AUROC"))
        if auroc:
            self.metricsChanged.emit({"auroc": round(auroc, 4)})
            self.logAppended.emit(f"AUROC = {auroc:.4f}")

    def _apply_epoch(self, event: dict) -> None:
        epoch = int(event.get("epoch", 0))
        total = int(event.get("total", 0)) or 1
        metrics = event.get("metrics") or {}

        loss = _pick(metrics, ("train/box_loss", "train/loss", "val/box_loss"))
        mAP50 = _pick(metrics, ("metrics/mAP50(B)", "metrics/mAP50"))
        precision = _pick(metrics, ("metrics/precision(B)", "metrics/precision"))
        recall = _pick(metrics, ("metrics/recall(B)", "metrics/recall"))
        auroc = _pick(metrics, ("image_AUROC", "image_AUROC_macro", "pixel_AUROC"))

        self._config.current_epoch = epoch
        self._config.progress = min(epoch / total, 1.0)
        self._config.loss = loss
        self._config.mAP50 = mAP50
        self._config.precision = precision
        self._config.recall = recall

        self.progressChanged.emit(self._config.progress)
        payload = {
            "epoch": epoch,
            "total": total,
            "loss": round(loss, 4),
            "mAP50": round(mAP50, 4),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
        }
        if auroc:
            payload["auroc"] = round(auroc, 4)
        self.metricsChanged.emit(payload)
        self.logAppended.emit(
            f"Epoch {epoch}/{total}  loss={loss:.4f}  mAP50={mAP50:.4f}"
        )

    def _on_finished(self, code: int, summary: dict) -> None:
        if code == 0:
            self._set_status("finished")
            self._config.progress = 1.0
            self.progressChanged.emit(1.0)
            best = summary.get("best") or ""
            self.logAppended.emit(f"=== 训练完成 === {best or summary.get('save_dir', '')}")
            self.message.emit("success", f"训练完成：{best or summary.get('save_dir', '')}")
            self.artifactsReady.emit(summary)
            self._register_artifacts(summary)
        else:
            self._set_status("error")
            self.message.emit("error", f"训练进程退出（code={code}）")

    def _on_failed(self, text: str) -> None:
        logger.warning("训练启动失败: %s", text)
        self.message.emit("error", text)
        if self._config.status == "running":
            self._set_status("error")

    # -----------------------------------------------------------
    # 产物登记
    # -----------------------------------------------------------
    def _register_artifacts(self, summary: dict) -> None:
        """把 best.pt / last.pt / results.csv 归档进当前项目。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return

        items: list[tuple[str, str, bytes]] = []
        for key in ("best", "last"):
            path = Path(summary.get(key) or "")
            if path.is_file():
                items.append(("model", f"runs/{path.name}", path.read_bytes()))
        save_dir = Path(summary.get("save_dir") or "")
        if save_dir:
            results = save_dir / "results.csv"
            if results.is_file():
                items.append(("run", "runs/results.csv", results.read_bytes()))
        if not items:
            return

        try:
            self._project_vm.service.add_files(project, items, save=True)
            self._project_vm.notify_changed()
            self.message.emit("success", f"训练产物已归档：{len(items)} 个文件")
        except (OSError, ValueError) as exc:
            logger.warning("登记训练产物失败: %s", exc)
            self.message.emit("warning", f"训练产物归档失败：{exc}")

    # -----------------------------------------------------------
    # 内部
    # -----------------------------------------------------------
    def _set_status(self, status: str) -> None:
        self._config.status = status
        self.statusChanged.emit(status)
