"""U-Net 语义分割后端适配器。

数据来自拆分产物目录（`data.yaml` 所在目录）：
    <root>/images/{train,val}/<图> + <root>/masks/{train,val}/<同名>.png
训练子进程入口为 `src.services.unet_worker`，只依赖 PyTorch（无额外运行时），
因此 CPU 也能训练中小数据集；支持轮边界暂停（与 YOLO 后端同协议）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from src.models.prediction import Prediction
from src.models.training import TrainingConfig
from src.services.backends.base import (
    BackendAdapter,
    EnvReport,
    ExportResult,
    Problem,
    pause_file,
)
from src.services.unet_model import DEFAULT_VARIANT, VARIANTS, variant_labels
from src.utils.constants import PROJECT_ROOT
from src.utils.device import normalize_device


class UnetBackend(BackendAdapter):
    """U-Net（语义分割）。"""

    key = "unet"
    label = "U-Net"
    tasks = ("semantic",)

    # -----------------------------------------------------------
    # 环境
    # -----------------------------------------------------------
    def is_available(self) -> bool:
        try:
            return importlib.util.find_spec("torch") is not None
        except (ImportError, ValueError):
            return False

    def check_env(self) -> EnvReport:
        available = self.is_available()
        return EnvReport(
            key=self.key,
            label=self.label,
            available=available,
            missing=() if available else ("torch",),
            detail="" if available else "未安装 PyTorch",
        )

    # -----------------------------------------------------------
    # 训练
    # -----------------------------------------------------------
    @staticmethod
    def data_root(config: TrainingConfig) -> Path:
        """数据根目录 = 拆分产物目录（data.yaml 所在目录）。"""
        data_yaml = str(getattr(config, "data_yaml", "") or "")
        return Path(data_yaml).parent if data_yaml else Path("")

    def validate(self, config: TrainingConfig) -> Problem | None:
        if not str(getattr(config, "data_yaml", "") or ""):
            return Problem(
                message="未选择数据集：请先在数据拆分页生成掩码数据集",
                hint="请先在数据拆分页完成划分",
            )
        root = self.data_root(config)
        images = root / "images" / "train"
        masks = root / "masks" / "train"
        if not images.is_dir() or not masks.is_dir():
            return Problem(
                message=f"掩码数据集不完整（缺少 images/ 或 masks/）：{root}",
                hint="掩码数据集不完整，请重新划分",
            )
        if not any(masks.glob("*.png")):
            return Problem(
                message=f"掩码目录里没有 PNG 掩码：{masks}",
                hint="掩码目录为空，请重新划分",
            )
        return None

    def build_args(self, config: TrainingConfig) -> list[str]:
        variant = str(config.model_key or "")
        if variant not in VARIANTS:
            variant = DEFAULT_VARIANT
        args = [
            "-m", "src.services.unet_worker",
            "--root", str(self.data_root(config)),
            "--variant", variant,
            "--epochs", str(config.epochs),
            "--batch", str(config.batch),
            "--imgsz", str(config.imgsz),
            "--lr", str(config.lr),
            "--device", normalize_device(config.device),
            "--output", config.project_dir or str(PROJECT_ROOT / "runs" / "unet"),
            "--seed", str(config.seed),
            "--pause-file", str(pause_file(config)),
        ]
        return args

    def candidates(self, task: str) -> list[dict]:
        return variant_labels()

    def pause_supported(self) -> bool:
        return True

    # -----------------------------------------------------------
    # 推理与导出
    # -----------------------------------------------------------
    def predict(
        self, weights, images: list, task: str = "semantic",
        conf: float = 0.0, device: str = "auto",
    ) -> list[Prediction]:
        """逐图预测掩码（写 PNG），供导出后回归对比。"""
        from PIL import Image

        from src.models.prediction import Prediction
        from src.services.segmentation_service import SegmentationService
        from src.utils.constants import DATA_DIR

        model, meta = SegmentationService.load_model(weights)
        imgsz = int(meta.get("imgsz") or 256)
        folder = DATA_DIR / "seg_pred"
        folder.mkdir(parents=True, exist_ok=True)

        predictions: list[Prediction] = []
        for image in images:
            mask = SegmentationService.predict_mask(model, image, imgsz)
            path = folder / f"{Path(str(image)).stem}.png"
            Image.fromarray(mask).save(path)
            predictions.append(Prediction(
                image=str(image), task=task or "semantic",
                backend=self.key, mask=str(path),
            ))
        return predictions

    def export_options(self) -> list[dict]:
        return [{"key": "torchscript", "label": "TorchScript (.torchscript)"}]

    def export(self, config, progress=None) -> ExportResult:
        """导出 TorchScript + 输入尺寸元信息（回归与部署都需要后者）。"""
        import json

        import torch

        from src.services.segmentation_service import SegmentationService

        model, meta = SegmentationService.load_model(config.weights_path)
        imgsz = int(getattr(config, "imgsz", 0) or meta.get("imgsz") or 256)
        target_dir = Path(config.output_dir or (PROJECT_ROOT / "runs" / "export"))
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{Path(str(config.weights_path)).stem}.torchscript"
        if callable(progress):
            progress(40, "导出 TorchScript")
        traced = torch.jit.trace(model, torch.zeros(1, 3, imgsz, imgsz), strict=False)
        torch.jit.save(traced, str(target))
        meta_path = target.with_suffix(".meta.json")
        meta_path.write_text(
            json.dumps({**meta, "imgsz": imgsz}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if callable(progress):
            progress(100, "已导出")
        return ExportResult(
            path=target, format="torchscript", files=(target, meta_path),
        )
