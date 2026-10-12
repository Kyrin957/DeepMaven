"""后端适配层：接口、共享结构与训练控制文件。

一个「后端」= 一套模型训练 / 推理实现（当前 Ultralytics YOLO、Anomalib，
后续 OCR / U-Net）。训练仍由 `TrainService` 起独立子进程，命令行参数、
启动前校验、产物识别统一由后端适配器提供，因此新增后端只需在
`src/services/backends/` 下加一个适配器并注册（见《开发文档.md》§8.2.2）。

子进程事件协议（父进程按此解析，任何后端都必须遵守）：
    {"type": "phase", "text": "..."}                            阶段提示
    {"type": "iteration", ...}                                  批次级进度
    {"type": "epoch", "epoch": 1, "total": 100, "metrics": {}}  每轮指标
    {"type": "metrics", "metrics": {}}                          汇总指标
    {"type": "status", "status": "paused" | "running"}          暂停 / 继续
    {"type": "done", "save_dir": ..., "best": ..., "last": ...}  完成（含产物）
    {"type": "error", "text": "..."}                            失败
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from src.models.prediction import Prediction
from src.utils.constants import PROJECT_ROOT

# 事件类型（供各后端产出事件时对照）
EVENT_TYPES = (
    "phase", "iteration", "epoch", "metrics", "status", "done", "error",
)


@dataclass(frozen=True)
class Problem:
    """启动前的校验问题。

    message: 讲清原因（含缺失的路径），供日志与训练服务使用；
    hint:    界面引导语（该做什么），供训练页提示使用。
    """

    message: str
    hint: str = ""


@dataclass(frozen=True)
class EnvReport:
    """后端环境探测结果（依赖是否可用、解释器路径）。"""

    key: str
    label: str
    available: bool
    interpreter: str = ""
    missing: tuple[str, ...] = ()
    detail: str = ""


@dataclass(frozen=True)
class Artifact:
    """训练产物条目：种类（model / run）+ 文件路径。"""

    kind: str
    path: Path


@dataclass(frozen=True)
class ExportResult:
    """一次导出的产物：单文件（YOLO 各格式）或模型包目录（异常检测）。"""

    path: Path
    format: str = ""
    files: tuple[Path, ...] = ()
    note: str = ""                      # 降级说明（如「未安装 onnx」）

    @property
    def is_package(self) -> bool:
        """产物是否为目录（模型包）。"""
        return self.path.is_dir()

    def size_mb(self) -> float:
        """产物总大小（目录按内容累加）。"""
        targets = list(self.files) or [self.path]
        total = 0
        for item in targets:
            try:
                if item.is_dir():
                    total += sum(
                        path.stat().st_size for path in item.rglob("*") if path.is_file()
                    )
                elif item.is_file():
                    total += item.stat().st_size
            except OSError:
                continue
        return total / (1024 * 1024)


def pause_file(config) -> Path:
    """暂停标记文件：父进程与训练子进程约定的控制文件。

    路径通过 `--pause-file` 传给支持暂停的后端子进程，因此无需额外 IPC。
    """
    root = Path(config.project_dir or (PROJECT_ROOT / "runs"))
    return root / "train.pause"


class BackendAdapter:
    """后端适配器接口。"""

    key: str = ""
    label: str = ""
    tasks: tuple[str, ...] = ()

    # -----------------------------------------------------------
    # 环境
    # -----------------------------------------------------------
    def is_available(self) -> bool:
        """依赖是否可用（不导入重依赖，按模块探测）。"""
        raise NotImplementedError

    def check_env(self) -> EnvReport:
        from sys import executable

        available = self.is_available()
        return EnvReport(
            key=self.key,
            label=self.label,
            available=available,
            interpreter=executable,
            missing=() if available else (self.key,),
        )

    # -----------------------------------------------------------
    # 训练
    # -----------------------------------------------------------
    def validate(self, config) -> Problem | None:
        """启动前校验：通过返回 None，否则返回问题（含界面引导语）。"""
        return None

    def build_args(self, config) -> list[str]:
        """构造子进程命令行参数（模块入口 + 参数）。"""
        raise NotImplementedError

    # -----------------------------------------------------------
    # 训练参数的适用范围（界面据此显隐，见 §4.72 / §27）
    # -----------------------------------------------------------
    #: 该后端**真正消费**的训练参数键；``None`` = 全部适用（YOLO 系）。
    #: 子类给白名单后，界面把不在名单里的字段整行隐藏——不显示"设了不生效"的项。
    train_param_keys: frozenset[str] | None = None

    def supports_train_param(self, key: str) -> bool:
        """该后端是否消费这个训练参数（界面显隐与后端 `build_args` 同源）。"""
        keys = self.train_param_keys
        return keys is None or str(key) in keys

    #: 任务专属参数的适用范围（键 → 只在哪些任务里被库消费）；未列出的键全任务适用
    task_only_params: dict[str, tuple[str, ...]] = {}

    def supports_task_param(self, key: str, task: str) -> bool:
        """该参数在**这个任务**里是否被库真正消费（任务专属表，§4.84）。

        界面据它与 `supports_train_param` **一起**决定字段显隐，`build_args`
        同样只传通过的项——「设了不生效就不显示、不下发」同一条口径。
        """
        allowed = self.task_only_params.get(str(key))
        return allowed is None or str(task) in allowed

    def augment_keys(self, task: str) -> tuple[str, ...]:
        """该任务**真正消费**的数据增强项（界面按此建卡片，空 = 不显示增强卡）。

        与 `train_param_keys` 同一口径：设了不生效的项一律不显示（§23）。
        YOLO 系按任务分两套（检测族走 `v8_transforms`、分类走
        `classify_augmentations`）；U-Net / Anomalib / OCR 的训练不吃增强参数，
        保持空（见 §4.83）。
        """
        return ()

    def candidates(self, task: str) -> list[dict]:
        """该任务的模型候选（[{"key": 权重名或变体 key, "label": 显示名}]）。"""
        return []

    def pause_supported(self) -> bool:
        """子进程是否实现轮边界暂停（未实现时界面按钮无效）。"""
        return False

    # -----------------------------------------------------------
    # 推理与导出
    # -----------------------------------------------------------
    def predict(
        self, weights, images: list, task: str = "",
        conf: float = 0.25, device: str = "auto",
    ) -> list["Prediction"]:
        """批量推理，返回统一契约（供导出后回归与后续 OCR 使用）。

        不支持的后端 / 导出格式直接抛异常，由调用方转为「跳过」说明。
        """
        raise NotImplementedError

    def export_options(self) -> list[dict]:
        """可用导出格式（[{"key", "label"}]），供导出页过滤下拉。"""
        return []

    def export(self, config, progress=None) -> ExportResult:
        """执行导出（单文件或模型包）。"""
        raise NotImplementedError

    # -----------------------------------------------------------
    # 产物
    # -----------------------------------------------------------
    def artifacts(self, summary: dict) -> list["Artifact"]:
        """从 done 事件汇总出的产物清单（默认认 best / last + results.csv）。"""
        items: list[Artifact] = []
        seen: set[Path] = set()
        for key in ("best", "last"):
            path = Path(str(summary.get(key) or ""))
            # best 与 last 指向同一文件时（如异常检测只有一个 .ckpt）只登记一次
            if not path.is_file() or path in seen:
                continue
            seen.add(path)
            items.append(Artifact("model", path))
        save_dir = Path(str(summary.get("save_dir") or ""))
        if save_dir:
            results = save_dir / "results.csv"
            if results.is_file():
                items.append(Artifact("run", results))
        return items
