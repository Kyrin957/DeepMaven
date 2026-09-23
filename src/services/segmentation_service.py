"""语义分割服务：掩码预测与检查点评估（U-Net 后端使用）。

评估页与「导出后回归」都走这里，避免同一套推理逻辑写两遍。
权重可以是训练检查点（`.pt`）或导出件（`.torchscript`，输入尺寸从同名
`<name>.meta.json` 读取）。
"""

from __future__ import annotations

import json
from pathlib import Path

from src.utils.logger import get_logger

logger = get_logger("segmentation")


class SegmentationService:
    """U-Net 推理与评估（静态方法，依赖懒加载）。"""

    # -----------------------------------------------------------
    # 模型
    # -----------------------------------------------------------
    @staticmethod
    def is_available() -> bool:
        try:
            import torch  # noqa: F401
        except ImportError:
            return False
        return True

    @staticmethod
    def load_model(weights) -> tuple:
        """读取权重，返回 (model, meta)。"""
        import torch

        path = Path(str(weights))
        if path.suffix.lower() == ".torchscript":
            model = torch.jit.load(str(path))
            model.eval()
            meta_path = path.with_suffix(".meta.json")
            meta: dict = {}
            if meta_path.is_file():
                try:
                    meta = dict(json.loads(meta_path.read_text(encoding="utf-8")))
                except (OSError, json.JSONDecodeError) as exc:
                    logger.warning("读取导出元信息失败 %s: %s", meta_path, exc)
            return model, meta
        from src.services.unet_model import load_checkpoint

        model, meta, _variant = load_checkpoint(path)
        return model, dict(meta)

    # -----------------------------------------------------------
    # 数据
    # -----------------------------------------------------------
    @staticmethod
    def pairs_for(root, split: str) -> list[tuple[Path, Path]]:
        """取某子集的（原图, 掩码）对；缺掩码的图片跳过。"""
        folder = Path(root) / "images" / split
        if not folder.is_dir():
            return []
        pairs: list[tuple[Path, Path]] = []
        for image in sorted(item for item in folder.iterdir() if item.is_file()):
            mask = Path(root) / "masks" / split / f"{image.stem}.png"
            if mask.is_file():
                pairs.append((image, mask))
        return pairs

    # -----------------------------------------------------------
    # 推理
    # -----------------------------------------------------------
    @staticmethod
    def predict_mask(model, image_path, imgsz: int = 256):
        """单图预测，返回**与原图同尺寸**的类别掩码（numpy uint8）。"""
        import numpy as np
        import torch
        from PIL import Image

        size = max(32, int(imgsz or 256))
        with Image.open(image_path) as handle:
            base = handle.convert("RGB")
            width, height = base.size
            resized = base.resize((size, size), Image.Resampling.BILINEAR)
        array = np.asarray(resized, dtype="float32") / 255.0
        tensor = torch.from_numpy(array).permute(2, 0, 1).unsqueeze(0)
        with torch.no_grad():
            predicted = model(tensor).argmax(dim=1)[0].cpu().numpy().astype("uint8")
        if (width, height) != (size, size):
            predicted = np.asarray(Image.fromarray(predicted).resize(
                (width, height), Image.Resampling.NEAREST
            ))
        return predicted

    # -----------------------------------------------------------
    # 评估
    # -----------------------------------------------------------
    @staticmethod
    def evaluate_checkpoint(
        weights, pairs: list, class_names: list | None = None,
        plot_dir=None, palette=None, progress=None,
    ) -> dict:
        """逐图预测并与真值掩码比对。

        Args:
            pairs: [(原图路径, 掩码路径), ...]
            plot_dir: 叠加对比图的输出目录（None 则不生成）

        Returns:
            {"metrics", "rows", "class_names", "num_classes"}
        """
        import numpy as np
        from PIL import Image

        from src.services.segmentation_metrics import evaluate as mask_scores
        from src.utils.image_ops import overlay_mask

        model, meta = SegmentationService.load_model(weights)
        imgsz = int(meta.get("imgsz") or 256)
        names = [str(name) for name in (class_names or [])]
        num_classes = max(int(meta.get("num_classes") or 0), len(names), 2)
        while len(names) < num_classes:
            names.append(f"类别{len(names)}")

        targets: list = []
        predictions: list = []
        rows: list[dict] = []
        total = max(1, len(pairs))
        for index, (image_path, mask_path) in enumerate(pairs):
            predicted = SegmentationService.predict_mask(model, image_path, imgsz)
            with Image.open(mask_path) as handle:
                truth = np.asarray(handle.convert("L").resize(
                    (predicted.shape[1], predicted.shape[0]), Image.Resampling.NEAREST
                )).astype("uint8")
            targets.append(truth)
            predictions.append(predicted)

            pixels = int(truth.size) or 1
            accuracy = float((truth == predicted).sum()) / pixels
            overlay = ""
            if plot_dir is not None:
                overlay = overlay_mask(
                    image_path, predicted,
                    Path(plot_dir) / f"seg_{Path(image_path).stem}.png",
                    palette=palette,
                )
            rows.append({
                "path": str(image_path),
                "name": Path(image_path).name,
                "label": SegmentationService._dominant(truth, names),
                "pred_label": SegmentationService._dominant(predicted, names),
                "correct": accuracy >= 0.99,
                "confidence": round(accuracy, 6),
                "ms": 0.0,
                "detections": [],
                "probs": {},
                "annotated": overlay,
            })
            if callable(progress):
                progress(int(100 * (index + 1) / total), f"已评估 {index + 1}/{total}")

        scores = mask_scores(targets, predictions, num_classes, names)
        logger.info(
            "语义分割评估完成：%s 张 · mIoU %.4f · Dice %.4f",
            len(pairs), scores["miou"], scores["dice"],
        )
        return {
            "metrics": scores,
            "rows": rows,
            "class_names": names,
            "num_classes": num_classes,
        }

    @staticmethod
    def _dominant(mask, names: list) -> str:
        """掩码里的主导类别名（结果网格的标签展示用）。"""
        import numpy as np

        values, counts = np.unique(mask, return_counts=True)
        if values.size == 0:
            return "—"
        index = int(values[int(np.argmax(counts))])
        return names[index] if 0 <= index < len(names) else str(index)
