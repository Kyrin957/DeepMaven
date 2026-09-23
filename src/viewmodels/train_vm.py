"""模型训练 ViewModel：参数配置、子进程训练、实时曲线与训练记录。

训练在独立子进程中执行（见 `services/train_service.py`），进度与指标通过
JSON 事件流回传，可随时强制终止。本 ViewModel 负责：

* 把子进程事件整理成「指标行 + 曲线数据 + 日志」三类输出；
* 维护实时曲线（训练/验证损失、各评价指标）；
* 记录历次训练（供结果页回看，含 results.csv 解析）。
"""

from __future__ import annotations

import csv
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from src.models.training import TrainingConfig
from src.services.backends import resolve as resolve_backend
from src.services.dataset_service import DatasetService
from src.services.project_service import ProjectService
from src.services.train_service import TrainService
from src.utils.constants import DATA_DIR
from src.utils.image_ops import build_param_preview
from src.utils.logger import get_logger
from src.utils.tasks import task_metrics, task_of
from src.viewmodels.project_vm import ProjectViewModel

logger = get_logger("train_vm")

# 训练参数预览图的存放目录（临时可视化，不进项目）
_PREVIEW_DIR = DATA_DIR / "preview"


def _pick(metrics: dict, keys: tuple) -> float:
    """从指标字典中按候选键名取第一个可用值。"""
    for key in keys:
        if key in metrics:
            try:
                return float(metrics[key])
            except (TypeError, ValueError):
                continue
    return 0.0


def _sum_loss(metrics: dict, prefix: str) -> float | None:
    """把 `prefix/*_loss` 各项求和（训练/验证总损失）。找不到返回 None。"""
    total = 0.0
    found = False
    for key, value in metrics.items():
        text = str(key)
        if text.startswith(f"{prefix}/") and text.endswith("loss"):
            try:
                total += float(value)
            except (TypeError, ValueError):
                continue
            found = True
    return total if found else None


# 训练记录保留条数
_HISTORY_LIMIT = 20


class TrainViewModel(QObject):
    """模型训练页的业务逻辑。"""

    configChanged = Signal(object)       # TrainingConfig
    statusChanged = Signal(str)          # idle/running/finished/error
    progressChanged = Signal(float)      # 0~1
    metricsChanged = Signal(dict)        # 指标行（epoch/iteration/lr/loss/主指标…）
    curvesChanged = Signal(dict)         # 曲线数据 {"loss": [(名, 点)], "metric": [...]}
    logAppended = Signal(str)            # 训练日志行
    artifactsReady = Signal(dict)        # 训练产物 {save_dir, best, last, results}
    historyChanged = Signal(list)        # 训练记录列表
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
        # 实时曲线：名称 → [(轮次, 值)]
        self._loss_curves: dict[str, list[tuple[float, float]]] = {}
        self._metric_curves: dict[str, list[tuple[float, float]]] = {}
        self._main_label = ""
        self._last_row: dict = {}        # 上一轮指标（供「上一个 Epoch / 新值」对比）
        self._viewing_history = False    # 当前曲线是否来自历史记录
        self._run_cache: dict = {}       # results.csv 解析缓存（横向对比用）

    # -----------------------------------------------------------
    # 项目绑定
    # -----------------------------------------------------------
    def load_from_project(self, project) -> None:
        """打开 / 新建项目后把配置绑定到项目：后续修改直接进入项目并随保存落盘。

        项目类型会决定训练任务（分类 / 检测 / 旋转框 / 分割 / 异常检测）。
        """
        self._config = project.training if project is not None else TrainingConfig()
        if project is not None:
            self._config.task_type = task_of(project.model_type)
            # 训练产物落在项目文件夹里（旧项目未设置保存目录时补齐默认值）
            path = str(project.params.get("path") or "")
            if path and not str(self._config.project_dir or "").strip():
                self._config.project_dir = str(
                    ProjectService.project_dir(path) / "runs"
                )
        self.configChanged.emit(self._config)
        self.statusChanged.emit(self._config.status)
        self.progressChanged.emit(self._config.progress)
        self.historyChanged.emit(self.history())
        # 重启后仍能看到上次训练的最后状态
        if self._config.current_epoch:
            self.metricsChanged.emit(self._summary_row())

    # -----------------------------------------------------------
    # 查询
    # -----------------------------------------------------------
    @property
    def config(self) -> TrainingConfig:
        return self._config

    @property
    def project(self):
        """当前项目（未打开项目时为 None）。"""
        return self._project_vm.project if self._project_vm else None

    def is_running(self) -> bool:
        return self._service.is_running()

    def main_label(self) -> str:
        """当前任务的主评价指标名。"""
        return self._main_label or _metric_specs(self._config.task_type)[0][0]

    def history(self) -> list[dict]:
        """历次训练记录（最近在前）。"""
        return list(reversed(self._config.history or []))

    def curves(self) -> dict:
        """曲线数据：{"loss": [(名, 点)], "metric": [(名, 点)], "x": "Epoch"}。"""
        return self._curves_payload()

    def viewing_history(self) -> bool:
        return self._viewing_history

    # -----------------------------------------------------------
    # 参数更新
    # -----------------------------------------------------------
    def update_config(self, **kwargs) -> None:
        for key, value in kwargs.items():
            if hasattr(self._config, key):
                setattr(self._config, key, value)
        self._mark_dirty()
        self.configChanged.emit(self._config)

    def _mark_dirty(self) -> None:
        """配置已绑定到项目时，把项目标记为有未保存变更。"""
        project = self._project_vm.project if self._project_vm else None
        if project is not None and self._config is project.training:
            project.touch()

    # -----------------------------------------------------------
    # 训练设置（setup）：命名保存多套训练参数，一键切换对比
    # -----------------------------------------------------------
    def setups(self) -> list[dict]:
        """项目里的训练设置列表（首次调用时自动建立一套）。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return []
        raw = project.params.get("training_setups")
        if not isinstance(raw, list) or not raw:
            raw = self._create_default_setup(project)
        return [dict(item) for item in raw]

    def active_setup_id(self) -> int:
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return 0
        self.setups()      # 保证至少有一套
        return int(project.params.get("active_setup_id", 0) or 0)

    def active_setup(self) -> dict:
        target = self.active_setup_id()
        return next(
            (item for item in self.setups()
             if int(item.get("setup_id", -1)) == target),
            {},
        )

    def create_setup(self, name: str = "", copy_current: bool = True) -> int:
        """新建训练设置（默认以当前配置为起点）。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            self.message.emit("warning", "请先创建或打开项目")
            return 0
        raw = list(project.params.get("training_setups") or [])
        self._store_active()
        setup_id = max(
            [int(item.get("setup_id", 0) or 0) for item in raw] or [0]
        ) + 1
        raw.append({
            "setup_id": setup_id,
            "name": str(name).strip() or f"设置 {setup_id}",
            "note": "",
            "split_id": int(self._config.split_id or 0),
            "config": self._snapshot() if copy_current else {},
        })
        project.params["training_setups"] = raw
        project.params["active_setup_id"] = setup_id
        self._commit_setups(f"已新建训练设置「{raw[-1]['name']}」")
        return setup_id

    def duplicate_setup(self, setup_id: int) -> int:
        """复制某一套训练设置（含参数）。"""
        source = next(
            (item for item in self.setups()
             if int(item.get("setup_id", -1)) == int(setup_id)),
            None,
        )
        if source is None:
            return 0
        return self.create_setup(f"{source.get('name') or '设置'} 副本")

    def rename_setup(self, setup_id: int, name: str = "", note: str = "") -> bool:
        """重命名训练设置 / 修改备注。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return False
        raw = list(project.params.get("training_setups") or [])
        for item in raw:
            if int(item.get("setup_id", -1)) != int(setup_id):
                continue
            if str(name).strip():
                item["name"] = str(name).strip()
            item["note"] = str(note)
            project.params["training_setups"] = raw
            self._commit_setups("训练设置已更新")
            return True
        return False

    def delete_setup(self, setup_id: int) -> bool:
        """删除某一套训练设置（至少保留一套）。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return False
        raw = list(project.params.get("training_setups") or [])
        if len(raw) <= 1:
            self.message.emit("warning", "至少保留一套训练设置")
            return False
        remain = [
            item for item in raw if int(item.get("setup_id", -1)) != int(setup_id)
        ]
        if len(remain) == len(raw):
            return False
        project.params["training_setups"] = remain
        if int(project.params.get("active_setup_id", 0) or 0) == int(setup_id):
            project.params["active_setup_id"] = int(remain[0]["setup_id"])
            self.update_config(**self._payload(remain[0]))
        self._commit_setups("训练设置已删除")
        return True

    def apply_setup(self, setup_id: int) -> bool:
        """切换训练设置：先保存当前配置，再载入目标设置。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return False
        target = next(
            (item for item in self.setups()
             if int(item.get("setup_id", -1)) == int(setup_id)),
            None,
        )
        if target is None or int(setup_id) == self.active_setup_id():
            return False
        self._store_active()
        project.params["active_setup_id"] = int(setup_id)
        self.update_config(**self._payload(target))
        self._commit_setups(f"已切换到训练设置「{target.get('name')}」")
        return True

    # -----------------------------------------------------------
    # 训练设置内部工具
    # -----------------------------------------------------------
    _RUNTIME_KEYS = frozenset({
        "status", "progress", "current_epoch", "iteration", "iterations",
        "lr_now", "elapsed", "eta", "best_value", "best_epoch", "save_dir",
        "loss", "val_loss", "mAP50", "precision", "recall", "history",
    })

    def _snapshot(self) -> dict:
        """当前训练配置中**属于设置**的部分（去掉运行状态与历史）。"""
        return {
            key: value for key, value in self._config.to_dict().items()
            if key not in self._RUNTIME_KEYS
        }

    def _payload(self, setup: dict) -> dict:
        """设置里的参数 → `update_config` 可用的字段。"""
        known = set(self._config.to_dict())
        return {
            key: value for key, value in (setup.get("config") or {}).items()
            if key in known and key not in self._RUNTIME_KEYS
        }

    def _create_default_setup(self, project) -> list:
        """首次使用时建立一套「默认设置」并落盘。"""
        raw = [{
            "setup_id": 1,
            "name": "默认设置",
            "note": "",
            "split_id": int(self._config.split_id or 0),
            "config": self._snapshot(),
        }]
        project.params["training_setups"] = raw
        project.params["active_setup_id"] = 1
        return raw

    def _store_active(self) -> None:
        """把当前配置写回「当前设置」。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return
        raw = list(project.params.get("training_setups") or [])
        active = int(project.params.get("active_setup_id", 0) or 0)
        for item in raw:
            if int(item.get("setup_id", -1)) == active:
                item["config"] = self._snapshot()
                item["split_id"] = int(self._config.split_id or 0)
                return

    def _commit_setups(self, text: str = "") -> None:
        """训练设置变更后落盘并通知界面。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return
        project.touch()
        try:
            self._project_vm.service.save(project)
        except (OSError, ValueError) as exc:
            logger.warning("保存训练设置失败: %s", exc)
            return
        self._project_vm.notify_changed()
        self.configChanged.emit(self._config)
        if text:
            self.message.emit("success", text)

    # -----------------------------------------------------------
    # 训练参数预览（letterbox + 在线增强示意）
    # -----------------------------------------------------------
    def preview_images(self, count: int = 8) -> list:
        """预览用的训练集图片（分类取类别目录，检测 / 分割取 images/train）。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return []
        config = self._config
        if str(config.task_type) == "anomaly":
            root = str(config.anomaly_root or "")
            return DatasetService.scan_images_multi([root])[:count] if root else []
        split = project.split_by_id(int(config.split_id)) or project.active_split()
        if split is None or not split.ready:
            return []
        root = Path(split.output_dir)
        if str(config.task_type) == "classify":
            return DatasetService.scan_images(root / "train")[:count]
        return DatasetService.scan_images(root / "images" / "train")[:count]

    def preview_params(self, size: int = 256, columns: int = 4) -> str:
        """生成训练参数预览拼图，返回图片路径（失败返回空串）。"""
        images = self.preview_images(count=max(1, columns * 2))
        if not images:
            self.message.emit("warning", "训练集里没有图片，无法预览参数")
            return ""
        target = _PREVIEW_DIR / (
            f"train_params_{int(time.time() * 1000)}.jpg"
        )
        written = build_param_preview(
            self._config, images, target, size=size, columns=columns,
            seed=int(self._config.seed or 0),
        )
        if not written:
            self.message.emit("error", "生成参数预览失败")
            return ""
        logger.info("训练参数预览：%s", written)
        return written

    # -----------------------------------------------------------
    # 类别权重（参考：按训练集频次平衡）
    # -----------------------------------------------------------
    def train_class_counts(self) -> dict[str, int]:
        """训练集各类别的样本数。

        分类任务按类别目录里的图片数统计；检测 / 分割按标签文件里的**框数**
        统计（更贴近损失里的类别占比）；异常检测按 normal / abnormal 两个目录。
        """
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return {}
        config = self._config
        if str(config.task_type) == "anomaly":
            root = str(config.anomaly_root or "")
            if not root:
                return {}
            counts = {}
            for name in ("normal", "abnormal"):
                folder = Path(root) / name
                if folder.is_dir():
                    counts[name] = len(DatasetService.scan_images(folder))
            return counts
        split = project.split_by_id(int(config.split_id)) or project.active_split()
        if split is None or not split.ready:
            return {}
        root = Path(split.output_dir)
        if str(config.task_type) == "classify":
            counts: dict[str, int] = {}
            train_dir = root / "train"
            if not train_dir.is_dir():
                return {}
            for folder in sorted(item for item in train_dir.iterdir() if item.is_dir()):
                counts[folder.name] = len(DatasetService.scan_images(folder))
            return counts

        names = [str(name) for name in (getattr(project, "class_names", []) or [])]
        counts = {name: 0 for name in names}
        label_dir = root / "labels" / "train"
        if not label_dir.is_dir():
            return {}
        for label in sorted(label_dir.glob("*.txt")):
            try:
                text = label.read_text(encoding="utf-8")
            except OSError:
                continue
            for line in text.splitlines():
                parts = line.split()
                if not parts:
                    continue
                try:
                    class_id = int(float(parts[0]))
                except ValueError:
                    continue
                name = names[class_id] if 0 <= class_id < len(names) else str(class_id)
                counts[name] = counts.get(name, 0) + 1
        return {name: value for name, value in counts.items() if name}

    def suggest_class_weights(self) -> dict[str, float]:
        """按逆频次给出建议权重，再做均值归一化（平均权重 = 1）。

        逆频次保证「样本少的类别权重高」；再做一次均值归一化，保证整体损失
        量级不变（平均权重 1.0），便于横向比较不同数据集。频次为 0 的类别
        给 1.0（不干预）。
        """
        counts = self.train_class_counts()
        total = sum(counts.values())
        if not counts or total <= 0:
            return {}
        average = total / len(counts)
        raw = {
            name: (average / count if count else 1.0)
            for name, count in counts.items()
        }
        mean = sum(raw.values()) / len(raw)
        if mean <= 0:
            return {}
        return {name: round(value / mean, 4) for name, value in raw.items()}

    def class_weights(self) -> dict[str, float]:
        """当前类别权重（未设置时给出建议值，仅用于展示）。"""
        stored = dict(getattr(self._config, "class_weights", {}) or {})
        return stored or self.suggest_class_weights()

    def class_weight_rows(self) -> list[dict]:
        """「类别 → 数量 / 权重」列表（供界面展示）。"""
        counts = self.train_class_counts()
        stored = dict(getattr(self._config, "class_weights", {}) or {})
        weights = stored or self.suggest_class_weights()
        rows = []
        for name, count in counts.items():
            rows.append({
                "name": name, "count": int(count),
                "weight": float(weights.get(name, 1.0) or 1.0),
                "applied": name in stored,
            })
        return rows

    def balance_class_weights(self) -> dict[str, float]:
        """一键平衡（BALANCE）：按训练集频次生成逆频次权重。"""
        weights = self.suggest_class_weights()
        if not weights:
            self.message.emit("warning", "没有可用的训练集统计，无法平衡类别权重")
            return {}
        self.update_config(class_weights=weights)
        self.message.emit("success", f"已按训练集频次平衡 {len(weights)} 个类别")
        return weights

    def reset_class_weights(self) -> None:
        """清除类别权重（回到不干预）。"""
        if not dict(getattr(self._config, "class_weights", {}) or {}):
            return
        self.update_config(class_weights={})
        self.message.emit("success", "已清除类别权重")

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
        # 启动条件由任务对应的后端决定（检测 / 分类看 data.yaml，异常看数据目录）
        problem = resolve_backend(self._config).validate(self._config)
        if problem is not None:
            self.message.emit("warning", problem.hint or problem.message)
            return

        # 训练参数随项目持久化，避免程序重启后丢失
        if self._project_vm is not None and self._project_vm.has_project():
            self._project_vm.save_project()

        self._reset_runtime()
        self.logAppended.emit("=== 启动训练 ===")
        self._set_status("running")
        if not self._service.start(self._config):
            self._set_status("error")

    # -----------------------------------------------------------
    # 暂停 / 继续
    # -----------------------------------------------------------
    def is_paused(self) -> bool:
        """训练是否处于「已请求暂停」状态。"""
        return self._service.is_paused()

    def pause(self) -> None:
        """暂停训练（在当前轮结束后生效，可随时继续）。"""
        if not self.is_running():
            self.message.emit("warning", "当前没有正在进行的训练")
            return
        if self._service.pause():
            self._set_status("paused")
            self.logAppended.emit(
                "=== 已请求暂停：本轮结束后挂起，点「继续」即可恢复 ==="
            )

    def resume(self) -> None:
        """继续训练（移除暂停标记，下一轮边界恢复）。"""
        if not self.is_running():
            self.message.emit("warning", "当前没有正在进行的训练")
            return
        self._service.resume()
        self._set_status("running")
        self.logAppended.emit("=== 已继续训练 ===")

    def continue_training(self, extra_epochs: int = 50) -> None:
        """在最近一次训练的基础上追加轮数继续（同一次运行的断点续训）。

        依赖 Ultralytics 的 `resume=True`：需要上次运行的 `weights/last.pt`，
        因此这里把权重指向该文件，并把总轮数改为「已完成 + 追加」。
        """
        if self.is_running():
            self.message.emit("warning", "训练进行中，请先暂停或终止")
            return
        records = self._config.history or []
        if not records:
            self.message.emit("warning", "还没有可继续的训练记录")
            return
        record = records[-1]
        results = Path(str(record.get("results") or ""))
        last = results.parent / "weights" / "last.pt"
        if not last.is_file():
            self.message.emit("warning", "找不到上次训练的 last.pt，无法继续训练")
            return
        done = int(record.get("total") or record.get("epochs") or 0)
        extra = max(1, int(extra_epochs))
        self.update_config(
            epochs=max(1, done + extra),
            resume=True,
            weights_path=str(last),
        )
        self.logAppended.emit(
            f"=== 继续训练：在已完成 {done} 轮的基础上追加 {extra} 轮 ==="
        )
        self.start()

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
        self._reset_runtime()
        self.logAppended.emit("训练状态已重置")

    def _reset_runtime(self) -> None:
        """清空实时状态与曲线（不含训练记录）。"""
        config = self._config
        config.progress = 0.0
        config.current_epoch = 0
        config.iteration = 0
        config.iterations = 0
        config.lr_now = 0.0
        config.elapsed = 0.0
        config.eta = 0.0
        config.best_value = 0.0
        config.best_epoch = 0
        config.save_dir = ""
        config.loss = 0.0
        config.val_loss = 0.0
        config.mAP50 = 0.0
        config.precision = 0.0
        config.recall = 0.0
        self._loss_curves = {}
        self._metric_curves = {}
        self._last_row = {}
        self._main_label = ""
        self._viewing_history = False
        self.statusChanged.emit(config.status if config.status == "idle" else "idle")
        self.progressChanged.emit(0.0)
        self.metricsChanged.emit({})
        self.curvesChanged.emit(self._curves_payload())

    # -----------------------------------------------------------
    # 子进程事件
    # -----------------------------------------------------------
    def _on_event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "phase":
            self.logAppended.emit(f"[阶段] {event.get('text', '')}")
        elif kind == "iteration":
            self._apply_iteration(event)
        elif kind == "epoch":
            self._apply_epoch(event)
        elif kind == "metrics":
            self._apply_metrics(event.get("metrics") or {})
        elif kind == "status":
            self._apply_status(event)
        elif kind == "error":
            self.logAppended.emit(f"[错误] {event.get('text', '')}")

    def _apply_iteration(self, event: dict) -> None:
        """批次级进度：只更新进度与当前学习率，避免频繁重绘曲线。"""
        config = self._config
        config.current_epoch = int(event.get("epoch", config.current_epoch) or 0)
        config.iteration = int(event.get("iteration", config.iteration) or 0)
        config.iterations = int(event.get("iterations", config.iterations) or 0)
        config.lr_now = float(event.get("lr", config.lr_now) or 0.0)
        config.elapsed = float(event.get("elapsed", config.elapsed) or 0.0)
        total = max(1, int(event.get("total", config.epochs) or 1))
        # 以「轮次 + 轮内进度」估算整体进度
        per_epoch = config.iterations / total if config.iterations else 0
        fraction = 0.0
        if per_epoch:
            done = config.iteration - (config.current_epoch - 1) * per_epoch
            fraction = min(1.0, max(0.0, done / per_epoch))
        progress = min(1.0, ((config.current_epoch - 1) + fraction) / total)
        self._emit_progress(progress, event)

    def _apply_epoch(self, event: dict) -> None:
        """每轮结束：更新指标行、曲线与最佳值。"""
        config = self._config
        epoch = int(event.get("epoch", 0))
        total = int(event.get("total", 0)) or 1
        metrics = event.get("metrics") or {}
        task = config.task_type
        specs = _metric_specs(task)

        train_loss = _sum_loss(metrics, "train")
        val_loss = _sum_loss(metrics, "val")
        if train_loss is None and ("train/loss" in metrics or "val/loss" in metrics):
            # 异常检测等任务没有分项损失：此时不画 0 值点，避免曲线误导
            train_loss = _pick(metrics, ("train/loss", "val/loss"))

        config.current_epoch = epoch
        config.iterations = int(event.get("iterations", config.iterations) or 0)
        config.iteration = int(event.get("iteration", config.iteration) or 0)
        config.lr_now = float(event.get("lr", config.lr_now) or 0.0)
        config.elapsed = float(event.get("elapsed", config.elapsed) or 0.0)
        config.eta = float(event.get("eta", config.eta) or 0.0)
        config.loss = float(train_loss or 0.0)
        config.val_loss = float(val_loss or 0.0)
        config.mAP50 = _pick(metrics, ("metrics/mAP50(B)", "metrics/mAP50"))
        config.precision = _pick(metrics, ("metrics/precision(B)", "metrics/precision"))
        config.recall = _pick(metrics, ("metrics/recall(B)", "metrics/recall"))

        # 曲线：损失（训练 / 验证）
        if train_loss is not None:
            self._loss_curves.setdefault("训练损失", []).append((epoch, train_loss))
        if val_loss is not None:
            self._loss_curves.setdefault("验证损失", []).append((epoch, val_loss))

        # 曲线：评价指标
        main_label, main_value = "", 0.0
        for label, keys in specs:
            value = _pick(metrics, keys)
            if not any(key in metrics for key in keys):
                continue
            self._metric_curves.setdefault(label, []).append((epoch, value))
            if not main_label:
                main_label, main_value = label, value
        if not main_label:
            main_label = specs[0][0]
        self._main_label = main_label

        # 最佳指标（越大越好；损失类指标不参与）
        if main_value > config.best_value:
            config.best_value = main_value
            config.best_epoch = epoch

        self._emit_progress(min(epoch / total, 1.0), event)
        self.metricsChanged.emit(self._metric_row(epoch, total, metrics, specs))
        self.curvesChanged.emit(self._curves_payload())
        self.logAppended.emit(
            f"Epoch {epoch}/{total}  loss={config.loss:.4f}  "
            f"{main_label}={main_value:.4f}"
        )

    def _apply_metrics(self, metrics: dict) -> None:
        """评估阶段汇总指标（异常检测的 AUROC 等）。"""
        auroc = _pick(metrics, ("image_AUROC", "image_AUROC_macro", "pixel_AUROC"))
        if not auroc:
            return
        config = self._config
        label = "AUROC"
        epoch = config.current_epoch or 1
        self._metric_curves.setdefault(label, []).append((epoch, auroc))
        config.best_value = max(config.best_value, auroc)
        config.best_epoch = config.best_epoch or epoch
        row = self._metric_row(epoch, config.epochs, metrics, _metric_specs("anomaly"))
        row.update({"main_label": label, "main_value": round(auroc, 4)})
        self.metricsChanged.emit(row)
        self.curvesChanged.emit(self._curves_payload())
        self.logAppended.emit(f"AUROC = {auroc:.4f}")

    # -----------------------------------------------------------
    # 指标与曲线的组装
    # -----------------------------------------------------------
    def _metric_row(
        self, epoch: int, total: int, metrics: dict, specs: list
    ) -> dict:
        """组装指标行（含上一轮对比值）。"""
        config = self._config
        row: dict = {
            "epoch": epoch,
            "total": total,
            "iteration": config.iteration,
            "iterations": config.iterations,
            "lr": config.lr_now,
            "elapsed": config.elapsed,
            "eta": config.eta,
            "progress": config.progress,
            "loss": round(config.loss, 4),
            "val_loss": round(config.val_loss, 4),
            "best_value": round(config.best_value, 4),
            "best_epoch": config.best_epoch,
            "previous": dict(self._last_row),
        }
        main_label = ""
        for label, keys in specs:
            value = _pick(metrics, keys)
            if not any(key in metrics for key in keys):
                continue
            if not main_label:
                main_label = label
                row["main_label"] = label
                row["main_value"] = round(value, 4)
            else:
                row.setdefault("subs", []).append((label, round(value, 4)))
        if not row.get("main_label"):
            row["main_label"] = specs[0][0]
            row["main_value"] = round(config.best_value, 4)
        self._last_row = row
        return row

    def _summary_row(self) -> dict:
        """重启后按配置里的最后状态组装指标行。"""
        config = self._config
        return {
            "epoch": config.current_epoch,
            "total": config.epochs,
            "iteration": config.iteration,
            "iterations": config.iterations,
            "lr": config.lr_now,
            "elapsed": config.elapsed,
            "eta": config.eta,
            "progress": config.progress,
            "loss": round(config.loss, 4),
            "val_loss": round(config.val_loss, 4),
            "best_value": round(config.best_value, 4),
            "best_epoch": config.best_epoch,
            "main_label": self.main_label(),
            "main_value": round(config.best_value, 4),
            "subs": [],
            "previous": {},
        }

    def _emit_progress(self, value: float, event: dict | None = None) -> None:
        self._config.progress = value
        self.progressChanged.emit(value)
        if event is not None and event.get("type") == "iteration":
            # 批次级事件不发指标行，只补一个轻量更新
            self.metricsChanged.emit({
                "epoch": self._config.current_epoch,
                "total": int(event.get("total", self._config.epochs) or 0),
                "iteration": self._config.iteration,
                "iterations": self._config.iterations,
                "lr": self._config.lr_now,
                "elapsed": self._config.elapsed,
                "eta": self._config.eta,
                "progress": value,
                "loss": round(self._config.loss, 4),
                "val_loss": round(self._config.val_loss, 4),
                "best_value": round(self._config.best_value, 4),
                "best_epoch": self._config.best_epoch,
                "main_label": self.main_label(),
                "main_value": round(self._config.best_value, 4),
                "subs": [],
                "previous": {},
                "partial": True,
            })

    def _curves_payload(self) -> dict:
        return {
            "loss": [(name, list(points)) for name, points in self._loss_curves.items()],
            "metric": [
                (name, list(points)) for name, points in self._metric_curves.items()
            ],
            "x": "Epoch",
        }

    # -----------------------------------------------------------
    # 训练记录
    # -----------------------------------------------------------
    def add_history(self, record: dict) -> None:
        """追加一条训练记录（按时间倒序取出）。"""
        self._config.history.append(record)
        self._run_cache.clear()
        if len(self._config.history) > _HISTORY_LIMIT:
            del self._config.history[:-_HISTORY_LIMIT]
        self.historyChanged.emit(self.history())

    def load_run(self, index: int) -> bool:
        """回看历史训练：读取该次的 results.csv，重建曲线。

        Args:
            index: 记录序号（0 = 最近一次，与 `history()` 同序）。
        """
        records = self.history()
        if not (0 <= index < len(records)):
            return False
        record = records[index]
        path = Path(str(record.get("results") or ""))
        if not path.is_file():
            self.message.emit("warning", "该次训练的结果文件已不在磁盘上")
            return False
        columns = parse_results_csv(path)
        if not columns:
            self.message.emit("warning", "结果文件解析失败")
            return False

        task = str(record.get("task") or self._config.task_type)
        specs = _metric_specs(task)
        self._loss_curves = {}
        self._metric_curves = {}
        for name, points in columns.items():
            if name.endswith("loss") and name.startswith("train/"):
                self._loss_curves.setdefault("训练损失", [])
                _merge_points(self._loss_curves["训练损失"], points)
            elif name.endswith("loss") and name.startswith("val/"):
                self._loss_curves.setdefault("验证损失", [])
                _merge_points(self._loss_curves["验证损失"], points)
        for label, keys in specs:
            for key in keys:
                if key in columns:
                    self._metric_curves[label] = list(columns[key])
                    break

        self._viewing_history = True
        self._main_label = specs[0][0]
        self.logAppended.emit(f"[回看] {record.get('name', '')} {path}")
        self.curvesChanged.emit(self._curves_payload())
        self.metricsChanged.emit({
            "epoch": int(record.get("epochs", 0) or 0),
            "total": int(record.get("total", 0) or 0),
            "iteration": 0,
            "iterations": 0,
            "lr": 0.0,
            "elapsed": 0.0,
            "eta": 0.0,
            "progress": 0.0,
            "loss": round(float(record.get("loss", 0.0) or 0.0), 4),
            "val_loss": 0.0,
            "best_value": round(float(record.get("best_value", 0.0) or 0.0), 4),
            "best_epoch": int(record.get("best_epoch", 0) or 0),
            "main_label": str(record.get("best_label") or self._main_label),
            "main_value": round(float(record.get("best_value", 0.0) or 0.0), 4),
            "subs": [],
            "previous": {},
            "history": True,
        })
        return True

    def run_curves(self, index: int) -> dict:
        """读取某次训练记录的曲线（results.csv），供多模型横向对比。

        Returns:
            {"loss": (名称, 点) | None, "metric": (名称, 点) | None, "record": dict}
        """
        records = self.history()
        if not (0 <= index < len(records)):
            return {}
        record = records[index]
        path = Path(str(record.get("results") or ""))
        if not path.is_file():
            return {"record": record}
        cached = self._run_cache.get(str(path))
        if cached is None:
            cached = parse_results_csv(path)
            self._run_cache[str(path)] = cached
        columns = cached
        if not columns:
            return {"record": record}

        loss_points: list[tuple[float, float]] = []
        loss_name = "训练损失"
        for key, points in columns.items():
            if str(key).startswith("train/") and str(key).endswith("loss"):
                _merge_points(loss_points, points)
        if not loss_points:
            loss_name = "验证损失"
            for key, points in columns.items():
                if str(key).startswith("val/") and str(key).endswith("loss"):
                    _merge_points(loss_points, points)

        task = str(record.get("task") or self._config.task_type)
        metric_name, metric_points = "", []
        for label, keys in _metric_specs(task):
            for key in keys:
                if key in columns:
                    metric_name, metric_points = label, list(columns[key])
                    break
            if metric_points:
                break
        return {
            "loss": (loss_name, loss_points) if loss_points else None,
            "metric": (metric_name, metric_points) if metric_points else None,
            "record": record,
        }

    def clear_history(self) -> None:
        """清空训练记录（不影响磁盘上的产物）。"""
        self._config.history = []
        self.historyChanged.emit([])

    def delete_history(self, index: int) -> None:
        """删除一条训练记录。"""
        records = self.history()
        if not (0 <= index < len(records)):
            return
        target = records[index]
        self._config.history = [
            item for item in self._config.history if item is not target
            and item.get("results") != target.get("results")
        ]
        self.historyChanged.emit(self.history())

    # -----------------------------------------------------------
    # 结束与产物
    # -----------------------------------------------------------
    def _on_finished(self, code: int, summary: dict) -> None:
        if code != 0:
            self._set_status("error")
            self.message.emit("error", f"训练进程退出（code={code}）")
            return

        config = self._config
        self._set_status("finished")
        config.progress = 1.0
        config.save_dir = str(summary.get("save_dir") or config.save_dir)
        config.eta = 0.0
        self.progressChanged.emit(1.0)
        if config.best_value and not config.best_epoch:
            config.best_epoch = config.current_epoch

        best = summary.get("best") or ""
        self.logAppended.emit(f"=== 训练完成 === {best or config.save_dir}")
        self.message.emit("success", f"训练完成：{best or config.save_dir}")
        # 该拆分已用于训练：比例锁定（与 Halcon 一致，避免划分被改动后无法复现）
        project = self.project
        split = project.split_by_id(config.split_id) if project is not None else None
        if split is not None:
            split.locked = True

        setup = self.active_setup()
        self.add_history({
            "name": _now_text(),
            "task": config.task_type,
            "model": config.model_key,
            # 训练设置归属：多套参数横向比较时据此区分
            "setup_id": int(setup.get("setup_id") or 0),
            "setup_name": str(setup.get("name") or ""),
            "split_id": int(config.split_id),
            "split_name": str(
                config.split_name or (split.name if split is not None else "")
            ),
            "epochs": config.current_epoch or config.epochs,
            "total": config.epochs,
            "loss": round(config.loss, 4),
            "best_label": self.main_label(),
            "best_value": round(config.best_value, 4),
            "best_epoch": config.best_epoch,
            "save_dir": config.save_dir,
            "results": str(summary.get("results") or ""),
            "best_weights": best,
        })
        self.artifactsReady.emit(summary)
        self._register_artifacts(summary)

    def _on_failed(self, text: str) -> None:
        logger.warning("训练启动失败: %s", text)
        self.message.emit("error", text)
        if self._config.status == "running":
            self._set_status("error")

    def _register_artifacts(self, summary: dict) -> None:
        """把后端产物（best / last 权重 + results.csv）归档进当前项目。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return

        run_folder = _run_folder_name(
            summary, self._config, self._config.history
        )
        if self._config.history:
            # 记录归档目录，便于回溯该次训练的产物位置
            self._config.history[-1]["archive_folder"] = f"runs/{run_folder}"
        items: list[tuple[str, str, bytes]] = []
        for artifact in resolve_backend(self._config).artifacts(summary):
            try:
                payload = artifact.path.read_bytes()
            except OSError as exc:
                logger.warning("读取训练产物失败 %s: %s", artifact.path, exc)
                continue
            items.append((
                artifact.kind,
                f"runs/{run_folder}/{artifact.path.name}",
                payload,
            ))
        if not items:
            return

        # 训练产物同时作为「模型评估 / 模型导出」的默认权重，避免用户重复选择
        best = str(summary.get("best") or "")
        if best:
            if not project.evaluation.weights_path:
                project.evaluation.weights_path = best
            if not project.export.weights_path:
                project.export.weights_path = best
            project.touch()

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

    def _apply_status(self, event: dict) -> None:
        """子进程上报的暂停 / 继续状态。"""
        status = str(event.get("status") or "")
        if status in ("paused", "running"):
            self._set_status(status)
        text = str(event.get("text") or "")
        if text:
            self.logAppended.emit(f"=== {text} ===")


def _metric_specs(task: str) -> list:
    """任务 → 评价指标列表 [(曲线名, 候选键名)]（声明在任务注册表）。"""
    return list(task_metrics(task))


def _merge_points(target: list, points: list) -> None:
    """把「列级」损失点按轮次累加进目标序列（训练/验证总损失）。"""
    for epoch, value in points:
        for index, (existing_epoch, existing_value) in enumerate(target):
            if existing_epoch == epoch:
                target[index] = (existing_epoch, existing_value + value)
                break
        else:
            target.append((epoch, value))


def _now_text() -> str:
    from datetime import datetime

    return datetime.now().strftime("%m-%d %H:%M")


def _safe_name(text: str) -> str:
    """替换路径片段里的非法字符（虚拟路径也保持可读）。"""
    cleaned = "".join(
        "_" if char in '\\/:*?"<>|' else char for char in str(text or "")
    ).strip()
    return cleaned or "run"


def _run_folder_name(summary: dict, config, history: list | None = None) -> str:
    """归档目录名 = 后端保存目录名（或模型名）+ 时间戳。

    异常检测与语义分割的输出目录由配置固定，多次训练会互相覆盖；
    加上时间戳后每次训练各有独立归档（虚拟路径 `runs/<目录>/...`）。
    少数情况下（同一秒内完成两次）时间戳会重复，此时后缀递增序号。
    """
    from datetime import datetime

    base = Path(str(summary.get("save_dir") or "")).name
    if not base:
        base = str(getattr(config, "model_key", "") or "run")
    name = f"{_safe_name(base)}-{datetime.now().strftime('%m%d-%H%M%S')}"
    # 记录里存的是 `runs/<目录>`，这里统一取末段目录名再比较
    used = {
        Path(str(item.get("archive_folder") or "")).name
        for item in (history or [])
    }
    if name not in used:
        return name
    index = 2
    while f"{name}-{index}" in used:
        index += 1
    return f"{name}-{index}"


def parse_results_csv(path) -> dict[str, list[tuple[float, float]]]:
    """解析 Ultralytics `results.csv` → {列名: [(epoch, 值), ...]}。"""
    columns: dict[str, list[tuple[float, float]]] = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace", newline="") as handle:
            reader = csv.reader(handle)
            header = [str(name).strip() for name in next(reader, [])]
            for row in reader:
                if not row:
                    continue
                epoch = _to_float(row[0])
                if epoch is None:
                    continue
                for index, name in enumerate(header[1:], start=1):
                    if index >= len(row) or not name:
                        continue
                    value = _to_float(row[index])
                    if value is None:
                        continue
                    columns.setdefault(name, []).append((epoch, value))
    except (OSError, csv.Error):
        return {}
    return columns


def _to_float(text) -> float | None:
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return None
