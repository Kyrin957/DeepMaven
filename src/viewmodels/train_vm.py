"""模型训练 ViewModel：参数配置、子进程训练、实时曲线与训练记录。

训练在独立子进程中执行（见 `services/train_service.py`），进度与指标通过
JSON 事件流回传，可随时强制终止。本 ViewModel 负责：

* 把子进程事件整理成「指标行 + 曲线数据 + 日志」三类输出；
* 维护实时曲线（训练/验证损失、各评价指标）；
* 记录历次训练（供结果页回看，含 results.csv 解析）。
"""

from __future__ import annotations

import csv
import os
import shutil
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from src.models.training import TrainingConfig
from src.services.backends import for_task as backend_for_task
from src.services.backends import resolve as resolve_backend
from src.services.dataset_service import DatasetService
from src.services.project_service import ProjectService
from src.services.train_service import TrainService
from src.utils.constants import (
    DATA_DIR,
    PLOT_COLORS,
    TASK_MODEL_SUFFIX,
    base_variant_key,
)
from src.utils.image_ops import build_param_preview
from src.utils.logger import get_logger
from src.utils.tasks import supports_class_counts, task_metrics, task_of
from src.viewmodels.project_vm import ProjectViewModel

logger = get_logger("train_vm")

# 训练参数预览图的存放目录（临时可视化，不进项目）
_PREVIEW_DIR = DATA_DIR / "preview"


def normalize_model_key(key, task: str) -> str:
    """把模型 key 与任务的权重后缀对齐（得到**精确的权重名**）。

    YOLO 后端的权重名带任务后缀（分类 ``yolo11n-cls`` / 分割 ``-seg`` /
    旋转框 ``-obb``，检测无后缀）；其余后端（anomalib / unet / ocr）的 key
    原样保留。旧项目可能把 key 存成基础名（如分类项目里的 ``yolo11n``），
    卡片规格行与下拉都按 key 显示——必须规范成精确权重名，避免与检测变体
    混淆（历史故障：分类项目显示 ``YOLO11n``）。

    注意后端**必须按任务查**（`for_task`）：`resolve()` 收的是训练配置，
    传任务字符串会退回默认的 YOLO 后端（历史实现即如此），异常检测 / 语义
    分割 / OCR 的 key 判断随之失真。
    """
    text = str(key or "")
    if not text:
        return ""
    if backend_for_task(task).key != "yolo":
        return text
    base = base_variant_key(text)
    suffix = TASK_MODEL_SUFFIX.get(str(task or ""), "")
    return f"{base}{suffix}"


def default_model_key(task: str) -> str:
    """该任务在「模型与权重」下拉里的**默认选项**（下拉首项 = 默认选择）。

    候选由任务对应的后端提供（`BackendAdapter.candidates`）：检测族是 YOLO
    变体，异常检测是 anomalib 算法（``Padim``），语义分割是 U-Net 变体，
    OCR 是 ``det``。界面下拉与新建模型的默认 key 必须**同源**——否则异常
    检测项目会拿到 YOLO 的 key（§4.80：新项目自动建的模型卡片显示 YOLO11n）。
    """
    candidates = backend_for_task(task).candidates(task)
    return str(candidates[0]["key"]) if candidates else ""


def model_key_for_task(key, task: str) -> str:
    """把模型 key 收敛到该任务的候选列表：不在列表里 → 该任务的默认项。

    用于打开项目（活配置 + 各模型快照）与新建模型：**任务的候选表是唯一
    判据**——写别的后端 / 别的任务的 key，训练时拼出的权重名根本不存在
    （`yolo11n.pt` vs anomalib 算法名）。
    """
    candidates = backend_for_task(task).candidates(task)
    keys = {str(item.get("key")) for item in candidates}
    text = normalize_model_key(key, task)
    if text and (not keys or text in keys):
        return text
    return str(candidates[0]["key"]) if candidates else text


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
    modelsChanged = Signal(list)         # 训练模型列表（卡片刷新）
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

        **活动模型的参数以活配置为准**：`project.training` 就是活动模型上一次
        保存时的完整状态（切模型 / 开始训练前先 `_store_active`），而模型快照
        只在「切模型 / 开始训练」时回写——改完参数直接保存的项目里，快照落后
        于活配置。因此**不得**再用快照覆盖活配置，否则「导入权重 / 改超参后
        保存，重开被抹回默认值」（历史故障，见《开发文档.md》§4.69）。
        这里反向对齐：把活配置写回活动模型快照，两者立即一致。
        """
        self._config = project.training if project is not None else TrainingConfig()
        if project is not None:
            # 任务类型由项目类型决定（模型快照里的旧值不得覆盖它——否则分类
            # 项目里下拉会变回检测模型列表，历史故障）
            task = task_of(project.model_type)
            self._config.task_type = task
            # 模型 key 与任务对齐：后缀对齐（分类 → yolo11n-cls；检测无后缀）
            # **且必须落在该任务的候选表里**——新项目的活配置是
            # `TrainingConfig()` 的默认值（yolo11n），异常检测 / 语义分割 / OCR
            # 直接用它会让卡片与下拉显示别的任务的模型（§4.80）。
            # 快照（含非活动模型）与活配置都要对齐——活配置是活动模型的来源。
            self._normalize_project_model_keys(project, task)
            self._config.model_key = model_key_for_task(
                self._config.model_key, task
            )
            # 训练模型：迁移旧数据（训练设置 / 单配置），产物目录指向活动模型的
            # model/<模型名>/（互不干扰）；活配置随即回写其快照
            active = self.active_model()
            if active:
                self._store_active()
                self._apply_model_dir(active)
        self.configChanged.emit(self._config)
        self.statusChanged.emit(self._config.status)
        self.progressChanged.emit(self._config.progress)
        self.historyChanged.emit(self.history())
        self.modelsChanged.emit(self.models())
        # 重启后仍能看到上次训练的最后状态
        if self._config.current_epoch:
            self.metricsChanged.emit(self._summary_row())

    # -----------------------------------------------------------
    # 查询
    # -----------------------------------------------------------
    def _normalize_project_model_keys(self, project, task: str) -> None:
        """把项目内所有训练模型的 model_key 与项目任务对齐（打开时迁移）。

        口径见 `model_key_for_task`：后缀对齐 + 不在任务候选表里就换成该任务
        的默认项（早期版本在异常检测项目里也建 YOLO 模型的遗留数据，见 §4.80）。
        仅在确有变化时回写并 `touch`（标记项目待保存 + 触发自动备份）。
        """
        raw = list(project.params.get("training_models") or [])
        changed = False
        for item in raw:
            config = dict(item.get("config") or {})
            current = str(config.get("model_key") or "")
            fixed = model_key_for_task(current, task)
            if fixed and fixed != current:
                config["model_key"] = fixed
                item["config"] = config
                changed = True
        if not changed:
            return
        project.params["training_models"] = raw
        project.touch()

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
    # 训练模型：一个项目可建多个模型（参数 + 拆分 + 产物 + 指标）
    # -----------------------------------------------------------
    def models(self) -> list[dict]:
        """项目里的训练模型列表。

        存储于 ``project.params["training_models"]``（键不存在时按旧数据
        迁移：训练设置 / 单配置 → 模型；显式空列表表示用户删光了模型，
        不回填）。
        """
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return []
        raw = project.params.get("training_models")
        if isinstance(raw, list):
            return [dict(item) for item in raw]
        return self._migrate_models(project)

    def has_model(self) -> bool:
        return bool(self.models())

    def active_model_id(self) -> int:
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return 0
        models = self.models()
        stored = int(project.params.get("active_model_id", 0) or 0)
        if any(int(item.get("model_id", -1)) == stored for item in models):
            return stored
        return int(models[0].get("model_id", 0)) if models else 0

    def active_model(self) -> dict:
        target = self.active_model_id()
        return next(
            (dict(item) for item in self.models()
             if int(item.get("model_id", -1)) == target),
            {},
        )

    def create_model(self, name: str = "", copy_current: bool = True) -> int:
        """新建训练模型（默认以当前参数为起点）——左侧模型管理的入口。

        默认模型（权重来源）必须是**该任务下拉的默认项**：新项目的活配置是
        `TrainingConfig()` 的默认值（``yolo11n``），异常检测 / 语义分割 / OCR
        照抄会给出一张 YOLO 模型卡（§4.80）。
        """
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            self.message.emit("warning", "请先创建或打开项目")
            return 0
        self._store_active()
        raw = list(project.params.get("training_models") or [])
        model_id = max(
            [int(item.get("model_id", 0) or 0) for item in raw] or [0]
        ) + 1
        config = self._snapshot() if copy_current else {}
        task = str(self._config.task_type or "detect")
        config["model_key"] = model_key_for_task(
            str(config.get("model_key") or ""), task
        )
        raw.append(self._make_model(
            model_id,
            str(name).strip() or f"模型 {model_id}",
            config=config,
            split_id=int(self._config.split_id or 0),
        ))
        project.params["training_models"] = raw
        project.params["active_model_id"] = model_id
        self._commit_models(f"已新建训练模型「{raw[-1]['name']}」")
        return model_id

    def duplicate_model(self, model_id: int) -> int:
        """复制一个训练模型（含参数；不复制产物与指标）。"""
        project = self._project_vm.project if self._project_vm else None
        source = next(
            (item for item in self.models()
             if int(item.get("model_id", -1)) == int(model_id)),
            None,
        )
        if project is None or source is None:
            return 0
        self._store_active()
        raw = list(project.params.get("training_models") or [])
        new_id = max(
            [int(item.get("model_id", 0) or 0) for item in raw] or [0]
        ) + 1
        copy = self._make_model(
            new_id,
            f"{source.get('name') or '模型'} 副本",
            config=dict(source.get("config") or {}),
            split_id=int(source.get("split_id", 0) or 0),
        )
        raw.append(copy)
        project.params["training_models"] = raw
        project.params["active_model_id"] = new_id
        self._commit_models(f"已复制训练模型「{copy['name']}」")
        return new_id

    def rename_model(self, model_id: int, name: str) -> bool:
        """重命名训练模型：磁盘产物目录随名字移动，路径引用同步。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return False
        new_name = str(name).strip()
        if not new_name:
            return False
        raw = list(project.params.get("training_models") or [])
        model = next(
            (item for item in raw
             if int(item.get("model_id", -1)) == int(model_id)),
            None,
        )
        if model is None or new_name == model.get("name"):
            return False
        if any(
            item is not model and str(item.get("name")) == new_name
            for item in raw
        ):
            self.message.emit("warning", f"已存在同名模型「{new_name}」")
            return False
        old_name = str(model.get("name") or "")
        # 磁盘目录随名字：<项目>/model/<旧名>/ → <项目>/model/<新名>/
        path_text = str(project.params.get("path") or "").strip()
        if path_text:
            old_dir = ProjectService.model_dir(path_text) / old_name
            new_dir = ProjectService.model_dir(path_text) / new_name
            if old_dir.is_dir() and not new_dir.exists():
                try:
                    shutil.move(str(old_dir), str(new_dir))
                except OSError as exc:
                    logger.warning("训练模型目录改名失败 %s: %s", old_dir, exc)
                else:
                    model["config"] = self._retarget(
                        model.get("config") or {},
                        str(old_dir), str(new_dir),
                    )
                    model["run_dir"] = self._retarget_text(
                        str(model.get("run_dir") or ""),
                        str(old_dir), str(new_dir),
                    )
                    if int(model.get("model_id", -1)) == self.active_model_id():
                        # 活动模型：活配置与历史同步改写
                        for name_, value in list(
                            self._config.to_dict().items()
                        ):
                            if not isinstance(value, str) or not value:
                                continue
                            updated = self._retarget_text(
                                value, str(old_dir), str(new_dir)
                            )
                            if updated != value:
                                setattr(self._config, name_, updated)
                        for record in self._config.history or []:
                            if not isinstance(record, dict):
                                continue
                            for key, value in list(record.items()):
                                if isinstance(value, str):
                                    record[key] = self._retarget_text(
                                        value, str(old_dir), str(new_dir)
                                    )
        model["name"] = new_name
        project.params["training_models"] = raw
        self._commit_models("训练模型已重命名")
        return True

    def select_model(self, model_id: int) -> bool:
        """切换训练模型：先保存当前参数，再载入目标模型的参数。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return False
        target = next(
            (item for item in self.models()
             if int(item.get("model_id", -1)) == int(model_id)),
            None,
        )
        if target is None or int(model_id) == self.active_model_id():
            return False
        self._store_active()
        project.params["active_model_id"] = int(model_id)
        payload = self._model_payload(target)
        self.update_config(**payload)
        self._apply_model_dir(target)
        self._commit_models(f"已切换到训练模型「{target.get('name')}」")
        return True

    def remove_model(self, model_id: int) -> bool:
        """删除训练模型：连同其磁盘产物目录（来源数据与历史记录不动）。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return False
        raw = list(project.params.get("training_models") or [])
        model = next(
            (item for item in raw
             if int(item.get("model_id", -1)) == int(model_id)),
            None,
        )
        if model is None or self.is_running() and int(model_id) == self.active_model_id():
            if model is None:
                return False
            self.message.emit("warning", "该模型正在训练中，请先停止训练")
            return False
        removed_dir = self._remove_model_dir(project, model)
        remain = [
            item for item in raw
            if int(item.get("model_id", -1)) != int(model_id)
        ]
        project.params["training_models"] = remain
        if int(project.params.get("active_model_id", 0) or 0) == int(model_id):
            fallback = remain[0] if remain else None
            project.params["active_model_id"] = (
                int(fallback.get("model_id", 0)) if fallback else 0
            )
            if fallback is not None:
                self.update_config(**self._model_payload(fallback))
                self._apply_model_dir(fallback)
        detail = "（已清理产物目录）" if removed_dir else ""
        self._commit_models(f"已删除训练模型「{model.get('name')}」{detail}")
        return True

    def open_model_dir(self, model_id: int) -> str:
        """返回「在文件资源管理器中打开训练」的目标目录（无则空串）。"""
        project = self._project_vm.project if self._project_vm else None
        model = next(
            (item for item in self.models()
             if int(item.get("model_id", -1)) == int(model_id)),
            None,
        )
        if project is None or model is None:
            return ""
        run_dir = str(model.get("run_dir") or "")
        if run_dir and Path(run_dir).is_dir():
            return run_dir
        path_text = str(project.params.get("path") or "").strip()
        if not path_text:
            return ""
        folder = ProjectService.model_dir(path_text) / str(model.get("name") or "")
        return str(folder) if folder.is_dir() else str(folder.parent)

    # -----------------------------------------------------------
    # 训练模型内部工具
    # -----------------------------------------------------------
    def _make_model(
        self, model_id: int, name: str, config: dict | None = None,
        split_id: int = 0,
    ) -> dict:
        from datetime import datetime

        return {
            "model_id": int(model_id),
            "name": str(name),
            "note": "",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "split_id": int(split_id),
            "config": dict(config or {}),
            "status": "",
            "metric_label": "",
            "metric_value": 0.0,
            "metric_epoch": 0,
            "run_dir": "",
        }

    def _model_payload(self, model: dict) -> dict:
        """模型里的参数 → `update_config` 可用的字段。"""
        known = set(self._config.to_dict())
        return {
            key: value for key, value in (model.get("config") or {}).items()
            if key in known and key not in self._RUNTIME_KEYS
        }

    def _migrate_models(self, project) -> list[dict]:
        """旧数据迁移：训练设置 / 训练记录 → 训练模型列表。

        每套训练设置转为一个模型，指标从历史记录里按 setup_id 匹配最近一条。
        **新建项目不得凭空出现模型**（与拆分一致：点「新建」之前列表为空，
        RULE §18）：只有确有过训练（留有历史记录）的老项目才把最近一次训练
        转成「模型 1」，其余显式落一个空列表并返回空。参数本身都还在活配置
        里，用户点「新建模型」时原样带走，不会丢。
        """
        setups = project.params.get("training_setups")
        # 以**项目自己的配置**为准：项目切换的瞬间 `self._config` 可能还指向
        # 上一个项目（信号先于 load_from_project 到达时），用它会看错训练痕迹
        source = project.training
        records = list(getattr(source, "history", []) or [])
        models: list[dict] = []
        active = 0
        if isinstance(setups, list) and setups:
            for item in setups:
                setup_id = int(item.get("setup_id", 0) or 0)
                record = next(
                    (
                        row for row in reversed(records)
                        if int(row.get("setup_id", -1) or -1) == setup_id
                    ),
                    None,
                )
                model = self._make_model(
                    setup_id or (len(models) + 1),
                    str(item.get("name") or f"模型 {len(models) + 1}"),
                    config=item.get("config") or {},
                    split_id=int(item.get("split_id", 0) or 0),
                )
                self._apply_record(model, record)
                models.append(model)
            active = int(project.params.get("active_setup_id", 0) or 0)
        if not models and records:
            # 老项目：把最近一次训练转成「模型 1」，指标 / 产物目录一并带上
            model = self._make_model(
                1, "模型 1", config=self._snapshot_of(source),
                split_id=int(getattr(source, "split_id", 0) or 0),
            )
            self._apply_record(model, records[-1])
            models = [model]
            active = 1
        if not models:
            # 从未训练过（含新建项目）：**不补建模型**，也不标记未保存——
            # 空态由左栏提示「暂无训练模型：点击「新建模型」创建」表达（§23）
            project.params["training_models"] = []
            project.params["active_model_id"] = 0
            project.params.pop("training_setups", None)
            project.params.pop("active_setup_id", None)
            return []
        project.params["training_models"] = models
        project.params["active_model_id"] = active or int(models[0]["model_id"])
        project.params.pop("training_setups", None)
        project.params.pop("active_setup_id", None)
        project.touch()
        self.message.emit(
            "info", "训练设置已升级为训练模型（保存项目以写入改动）"
        )
        return [dict(item) for item in models]

    @staticmethod
    def _apply_record(model: dict, record: dict | None) -> None:
        """把一条训练历史记录的指标写入模型卡片字段。"""
        if not record:
            return
        model["status"] = "finished"
        model["metric_label"] = str(record.get("best_label") or "")
        model["metric_value"] = float(record.get("best_value") or 0.0)
        model["metric_epoch"] = int(record.get("best_epoch") or 0)
        model["run_dir"] = str(record.get("save_dir") or "")

    @staticmethod
    def _retarget_text(value: str, old: str, new: str) -> str:
        """把指向旧目录的路径改写为新目录前缀（带分隔符边界检查）。"""
        if not value or not old:
            return value
        if value == old:
            return new
        if value.startswith(old + os.sep) or value.startswith(old + "/"):
            return new + value[len(old):]
        return value

    @classmethod
    def _retarget(cls, value, old: str, new: str):
        """递归改写 dict / list / str 里的旧目录前缀。"""
        if isinstance(value, str):
            return cls._retarget_text(value, old, new)
        if isinstance(value, list):
            return [cls._retarget(item, old, new) for item in value]
        if isinstance(value, dict):
            return {
                key: cls._retarget(item, old, new)
                for key, item in value.items()
            }
        return value

    def _apply_model_dir(self, model: dict) -> None:
        """把活动模型的产物目录设为其自己的 `model/<模型名>/`。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None or not model:
            return
        path_text = str(project.params.get("path") or "").strip()
        if not path_text:
            return
        self._config.project_dir = str(
            ProjectService.model_dir(path_text) / str(model.get("name") or "")
        )

    def _remove_model_dir(self, project, model: dict) -> bool:
        """删除模型的磁盘产物目录（来源数据与项目文件夹其余内容不动）。"""
        path_text = str(project.params.get("path") or "").strip()
        if not path_text:
            return False
        folder = ProjectService.model_dir(path_text) / str(model.get("name") or "")
        if not folder.is_dir():
            return False
        try:
            shutil.rmtree(folder)
        except OSError as exc:
            logger.warning("删除训练模型目录失败 %s: %s", folder, exc)
            return False
        return True

    def _store_active(self) -> None:
        """把当前配置写回「活动模型」的参数快照。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return
        raw = list(project.params.get("training_models") or [])
        active = self.active_model_id()
        for item in raw:
            if int(item.get("model_id", -1)) == active:
                item["config"] = self._snapshot()
                item["split_id"] = int(self._config.split_id or 0)
                return

    def _commit_models(self, text: str = "") -> None:
        """训练模型变更后登记未保存改动并通知界面（随项目统一保存）。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return
        project.touch()
        self._project_vm.notify_changed()
        self.configChanged.emit(self._config)
        self.modelsChanged.emit(self.models())
        if text:
            self.message.emit("success", text)

    def _set_model_status(self, status: str, **extra) -> None:
        """更新活动模型的训练状态 / 指标（卡片显示）并通知界面。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return
        raw = list(project.params.get("training_models") or [])
        active = self.active_model_id()
        for item in raw:
            if int(item.get("model_id", -1)) != active:
                continue
            item["status"] = str(status)
            item.update(extra)
            project.params["training_models"] = raw
            project.touch()
            self.modelsChanged.emit(self.models())
            return

    # -----------------------------------------------------------
    # 快照工具（默认模型的参数来源）
    # -----------------------------------------------------------
    _RUNTIME_KEYS = frozenset({
        "status", "progress", "current_epoch", "iteration", "iterations",
        "lr_now", "elapsed", "eta", "best_value", "best_epoch", "save_dir",
        "loss", "val_loss", "mAP50", "precision", "recall", "history",
    })

    def _snapshot(self) -> dict:
        """当前训练配置中**属于设置**的部分（去掉运行状态与历史）。"""
        return self._snapshot_of(self._config)

    @classmethod
    def _snapshot_of(cls, config) -> dict:
        """把一份训练配置转成模型快照（去掉运行状态 / 历史 / `task_type`）。

        排除 `task_type`：任务类型由项目类型决定（打开项目时绑定），
        快照里存了它会在加载时覆盖项目任务（曾导致分类项目的模型下拉
        变回检测列表）。
        """
        payload = {
            key: value for key, value in config.to_dict().items()
            if key not in cls._RUNTIME_KEYS
        }
        payload.pop("task_type", None)
        return payload

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
    # 类别分布（只读：按当前拆分的训练集统计）
    # -----------------------------------------------------------
    def train_class_counts(self) -> dict[str, int]:
        """训练集各类别的样本数（只统计有类别频次口径的任务）。

        分类任务按类别目录里的图片数统计；检测 / 分割 / 旋转框按标签文件里的
        **框数**统计（更贴近损失里的类别占比）。语义分割按像素掩码标注、异常
        检测只有正常样本，都没有「类别频次」这个口径（`tasks.class_counts`）
        → 返回空，界面整卡隐藏。
        """
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return {}
        config = self._config
        if not supports_class_counts(str(config.task_type)):
            return {}
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

    def class_colors(self, names: list) -> list[str]:
        """类别名 → 颜色（优先用项目里定义的类别颜色，与标注 / 图库同色）。

        与 `EvaluateViewModel.class_colors` 同一口径：项目里定义过颜色就用它，
        否则按图表配色循环——保证同一类别在各页面的颜色一致。
        """
        project = self._project_vm.project if self._project_vm else None
        palette: dict[str, str] = {}
        if project is not None:
            palette = {str(item.name): str(item.color) for item in project.classes}
        return [
            palette.get(str(name)) or PLOT_COLORS[index % len(PLOT_COLORS)]
            for index, name in enumerate(names)
        ]

    def class_distribution(self) -> list[dict]:
        """类别分布（**只读**）：名称 / 数量 / 占比，供界面展示。

        原先这里还额外算了「逆频次权重」并可写进配置，但没有任何后端消费那份
        字典（Ultralytics 的类别失衡是内部的 `cls_pw` 幂次，见 §4.72）——
        误导性设置已删除，只留数据本身的分布事实。
        """
        counts = self.train_class_counts()
        total = sum(counts.values())
        if not counts or total <= 0:
            return []
        return [
            {
                "name": name,
                "count": int(count),
                "share": float(count) / total * 100.0,
            }
            for name, count in counts.items()
        ]

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
        if not self.has_model():
            self.message.emit("warning", "请先在左侧新建训练模型")
            return
        # 参数与产物目录绑定到当前模型（产物落 model/<模型名>/）
        self._store_active()
        self._apply_model_dir(self.active_model())
        # 启动条件由任务对应的后端决定（检测 / 分类看 data.yaml，异常看数据目录）
        problem = resolve_backend(self._config).validate(self._config)
        if problem is not None:
            self.message.emit("warning", problem.hint or problem.message)
            return

        # 训练参数随项目持久化，避免程序重启后丢失
        if self._project_vm is not None and self._project_vm.has_project():
            self._project_vm.save_project()

        self._reset_runtime()
        self._set_model_status("running")
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
        self._mark_dirty()      # 训练记录随项目保存
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
        self._mark_dirty()
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
        self._mark_dirty()
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

        model = self.active_model()
        self.add_history({
            "name": _now_text(),
            "task": config.task_type,
            "model": config.model_key,
            # 训练模型归属：多模型横向对比时据此区分
            "model_id": int(model.get("model_id") or 0),
            "model_name": str(model.get("name") or ""),
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
        # 模型卡片同步指标与产物目录（左侧模型管理据显示状态与能力）
        self._set_model_status(
            "finished",
            metric_label=self.main_label(),
            metric_value=round(config.best_value, 4),
            metric_epoch=int(config.best_epoch or 0),
            run_dir=str(config.save_dir or ""),
        )
        self.artifactsReady.emit(summary)
        self._register_artifacts(summary)

    def _on_failed(self, text: str) -> None:
        logger.warning("训练启动失败: %s", text)
        self.message.emit("error", text)
        if self._config.status == "running":
            self._set_status("error")
        self._set_model_status("error")

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
            # （归档内虚拟路径；旧记录为 runs/ 前缀，读取端按记录值兼容）
            self._config.history[-1]["archive_folder"] = f"model/{run_folder}"
        items: list[tuple[str, str, bytes]] = []
        for artifact in resolve_backend(self._config).artifacts(summary):
            try:
                payload = artifact.path.read_bytes()
            except OSError as exc:
                logger.warning("读取训练产物失败 %s: %s", artifact.path, exc)
                continue
            items.append((
                artifact.kind,
                f"model/{run_folder}/{artifact.path.name}",
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
    加上时间戳后每次训练各有独立归档（虚拟路径 `model/<目录>/...`，
    旧记录为 `runs/…` 前缀）。
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
