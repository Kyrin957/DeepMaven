"""Ultralytics YOLO 后端适配器。

覆盖检测 / 旋转框 / 实例分割 / 分类四类任务，训练子进程入口为
`src.services.train_worker`。命令行参数原先写在 `TrainService._yolo_args`，
迁到此处后由 `TrainService` 按任务查表调用（行为不变）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from src.models.prediction import Box, Prediction
from src.models.training import TrainingConfig
from src.services.backends.base import (
    BackendAdapter,
    EnvReport,
    ExportResult,
    Problem,
    pause_file,
)
from src.utils.constants import (
    EXPORT_FORMATS,
    PROJECT_ROOT,
    TASK_MODEL_SUFFIX,
    YOLO_MODEL_VARIANTS,
    uses_dfl,
)
from src.utils.device import normalize_device
from src.utils.pretrained import resolve_pretrained
from src.utils.tasks import is_detection_family


class YoloBackend(BackendAdapter):
    """Ultralytics YOLO（检测 / 旋转框 / 分割 / 分类）。"""

    key = "yolo"
    label = "Ultralytics YOLO"
    tasks = ("detect", "obb", "segment", "classify")

    # -----------------------------------------------------------
    # 环境
    # -----------------------------------------------------------
    def is_available(self) -> bool:
        try:
            return importlib.util.find_spec("ultralytics") is not None
        except (ImportError, ValueError):
            return False

    def check_env(self) -> EnvReport:
        available = self.is_available()
        return EnvReport(
            key=self.key,
            label=self.label,
            available=available,
            missing=() if available else ("ultralytics",),
            detail="" if available else "未安装 Ultralytics",
        )

    # -----------------------------------------------------------
    # 训练
    # -----------------------------------------------------------
    def validate(self, config: TrainingConfig) -> Problem | None:
        if not config.data_yaml or not Path(config.data_yaml).exists():
            return Problem(
                message=f"数据集配置不存在：{config.data_yaml or '（未设置）'}",
                hint="请先在数据拆分页完成划分，生成数据集配置",
            )
        return None

    def build_args(self, config: TrainingConfig) -> list[str]:
        """YOLO 训练参数（覆盖训练页「设置」里的全部参数）。"""
        args = [
            "-m", "src.services.train_worker",
            "--data", config.data_yaml,
            "--weights", resolve_pretrained(
                config.weights_path or f"{config.model_key}.pt"
            ),
            "--epochs", str(config.epochs),
            "--batch", str(config.batch),
            "--imgsz", str(config.imgsz),
            "--channels", str(config.channels),
            "--lr", str(config.lr),
            "--optimizer", config.optimizer,
            "--weight-decay", str(config.weight_decay),
            "--momentum", str(config.momentum),
            "--warmup-epochs", str(config.warmup_epochs),
            "--patience", str(config.patience),
            "--cos-lr" if config.cos_lr else "--no-cos-lr",
            "--deterministic" if config.deterministic else "--no-deterministic",
            "--close-mosaic", str(config.close_mosaic),
            "--val" if config.val else "--no-val",
            "--cache" if config.cache else "--no-cache",
            "--single-cls" if config.single_cls else "--no-single-cls",
            "--rect" if config.rect else "--no-rect",
            "--dropout", str(config.dropout),
            "--device", normalize_device(config.device),
            "--workers", str(config.workers),
            "--seed", str(config.seed),
            "--project", config.project_dir or str(PROJECT_ROOT / "runs"),
            "--name", config.model_key,
            "--augment" if config.augment else "--no-augment",
            # 增强逐项生效值：未启用的项按 0 下发（Ultralytics 里 0 = 关闭），
            # 数值本身保留在配置里，重新启用即可恢复
            "--hflip", str(config.augment_value("hflip")),
            "--vflip", str(config.augment_value("vflip")),
            "--degrees", str(config.augment_value("degrees")),
            "--scale", str(config.augment_value("scale")),
            "--translate", str(config.augment_value("translate")),
            "--hsv-h", str(config.augment_value("hsv_h")),
            "--hsv-s", str(config.augment_value("hsv_s")),
            "--hsv-v", str(config.augment_value("hsv_v")),
            "--mosaic", str(config.augment_value("mosaic")),
            "--mixup", str(config.augment_value("mixup")),
            "--pause-file", str(pause_file(config)),
        ]
        # 检测族专属参数：分类任务（无权重 CrossEntropy、无回归头）、语义分割
        # （自研 U-Net）、异常检测（无监督）都不适用 —— **不传**，界面同样不
        # 显示（避免"设了不生效"）
        if is_detection_family(config.task_type):
            # 类别失衡权重强度（Ultralytics `cls_pw`：0 = 关闭，1 = 完全逆频次）
            args += ["--cls-pw", str(config.cls_pw)]
            # DFL 增益：仅 YOLO11 系列（reg_max=16）；YOLO26 是 reg_max=1 的
            # L1 回归，该参数被 Ultralytics 忽略，因此不传
            if uses_dfl(config.model_key):
                args += ["--dfl", str(config.dfl)]
        if config.resume:
            args.append("--resume")
        return args

    def candidates(self, task: str) -> list[dict]:
        # 显示名带任务后缀（分类 yolo11n-cls → YOLO11n-cls）：与权重名一致，
        # 避免和检测同名变体混淆（历史：下拉只显示 YOLO11n，看不出任务）
        suffix = TASK_MODEL_SUFFIX.get(task, "")
        return [
            {
                "key": f"{variant['key']}{suffix}",
                "label": f"{variant['label']}{suffix}",
            }
            for variant in YOLO_MODEL_VARIANTS
        ]

    def pause_supported(self) -> bool:
        return True

    # -----------------------------------------------------------
    # 推理与导出
    # -----------------------------------------------------------
    def predict(
        self, weights, images: list, task: str = "",
        conf: float = 0.25, device: str = "auto",
    ) -> list[Prediction]:
        """批量推理（检测族返回框，分类返回 top1），供导出后回归对比。"""
        from src.services.inference_service import InferenceService

        paths = [str(item) for item in images]
        if not paths:
            return []
        model = InferenceService.load_model(str(weights))
        results = model.predict(
            source=paths, conf=conf, device=normalize_device(device), verbose=False
        )
        predictions: list[Prediction] = []
        for index, result in enumerate(results or []):
            image = paths[index] if index < len(paths) else str(getattr(result, "path", ""))
            prediction = Prediction(image=image, task=task, backend=self.key)
            for record in InferenceService.extract_records(result):
                if "x1" in record:
                    prediction.boxes.append(Box(
                        x1=float(record.get("x1") or 0.0),
                        y1=float(record.get("y1") or 0.0),
                        x2=float(record.get("x2") or 0.0),
                        y2=float(record.get("y2") or 0.0),
                        cls_id=int(record.get("class_id") or 0),
                        conf=float(record.get("confidence") or 0.0),
                    ))
                else:
                    prediction.cls_id = int(record.get("class_id") or -1)
                    prediction.conf = float(record.get("confidence") or 0.0)
            predictions.append(prediction)
        return predictions

    def export_options(self) -> list[dict]:
        return [{"key": fmt["key"], "label": fmt["label"]} for fmt in EXPORT_FORMATS]

    def export(self, config, progress=None) -> ExportResult:
        from src.services.export_service import ExportService

        path = Path(ExportService().export(config))
        return ExportResult(path=path, format=str(config.format), files=(path,))
