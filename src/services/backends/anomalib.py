"""Anomalib 后端适配器（无监督异常检测）。

训练子进程入口为 `src.services.anomaly_worker`：只需正常样本，构建特征
记忆库（Padim / Patchcore 等），因此标记为「快速拟合」而非长时间训练。
命令行参数原先写在 `TrainService._anomaly_args`，迁到此处后由
`TrainService` 按任务查表调用（行为不变）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from src.models.training import TrainingConfig
from src.services.anomalib_service import AnomalibService
from src.services.backends.base import (
    BackendAdapter,
    EnvReport,
    ExportResult,
    Problem,
)
from src.utils.constants import PROJECT_ROOT
from src.utils.device import normalize_device


class AnomalibBackend(BackendAdapter):
    """Anomalib（异常检测）。"""

    key = "anomalib"
    label = "Anomalib"
    tasks = ("anomaly",)

    # -----------------------------------------------------------
    # 环境
    # -----------------------------------------------------------
    def is_available(self) -> bool:
        return AnomalibService.is_available()

    def check_env(self) -> EnvReport:
        available = self.is_available()
        missing: tuple[str, ...] = ()
        detail = ""
        if not available:
            missing = ("anomalib", "lightning")
            detail = "未安装 Anomalib"
        elif importlib.util.find_spec("lightning") is None:
            missing = ("lightning",)
            detail = "缺少 Lightning 运行时"
        return EnvReport(
            key=self.key,
            label=self.label,
            available=available,
            missing=missing,
            detail=detail,
        )

    # -----------------------------------------------------------
    # 训练
    # -----------------------------------------------------------
    def validate(self, config: TrainingConfig) -> Problem | None:
        if not config.anomaly_root or not Path(config.anomaly_root).is_dir():
            return Problem(
                message=f"异常检测数据目录不存在：{config.anomaly_root or '（未设置）'}",
                hint="请选择含 normal/ 与 abnormal/ 的目录",
            )
        return None

    def build_args(self, config: TrainingConfig) -> list[str]:
        """异常检测（Anomalib）参数。"""
        args = [
            "-m", "src.services.anomaly_worker",
            "--root", config.anomaly_root,
            "--model", config.model_key or "Padim",
            "--epochs", str(config.epochs),
            "--batch", str(config.batch),
            "--normal-dir", config.anomaly_normal_dir,
            "--abnormal-dir", config.anomaly_abnormal_dir,
            "--output", config.project_dir or str(PROJECT_ROOT / "runs" / "anomaly"),
            "--seed", str(config.seed),
            "--device", normalize_device(config.device),
        ]
        if not config.anomaly_pretrained:
            args.append("--no-pretrained")
        return args

    def candidates(self, task: str) -> list[dict]:
        return [
            {"key": name, "label": name}
            for name in AnomalibService.available_models()
        ]

    def pause_supported(self) -> bool:
        # Anomalib 训练无轮回调，暂无暂停协议
        return False

    # 说明：`predict()` 暂未实现——模型包的推理需要部署端运行时（骨干 + 记忆库
    # 最近邻搜索），当前回归只做包结构校验，见《开发文档.md》§8.7 第 2 期。

    # -----------------------------------------------------------
    # 导出
    # -----------------------------------------------------------
    def export_options(self) -> list[dict]:
        return [{"key": "package", "label": "模型包（记忆库 + 配置）"}]

    def export(self, config, progress=None) -> ExportResult:
        result = AnomalibService.export_package(
            str(config.weights_path),
            str(getattr(config, "anomaly_model", "") or "Padim"),
            str(config.output_dir or (PROJECT_ROOT / "runs" / "export")),
            imgsz=int(getattr(config, "imgsz", 0) or 256),
            progress=progress,
        )
        files = tuple(Path(item) for item in (result.get("files") or []))
        return ExportResult(
            path=Path(str(result.get("dir") or "")),
            format="package",
            files=files,
            note=str(result.get("note") or ""),
        )
