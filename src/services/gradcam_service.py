"""Grad-CAM：分类模型的热力图解释（torch 钩子实现，不改动 Ultralytics）。

做法：在骨干**最后一个卷积层**挂前向 / 反向钩子，取该层激活与目标类别的梯度，
用梯度的通道均值加权激活 → ReLU → 归一化，得到「模型把注意力放在哪里」的热力图。

仅支持**分类模型**（YOLO 分类权重）：检测 / 分割没有单一的分类头，
`supported()` 会给出明确判断，界面据此禁用入口而不是静默失败。
"""

from __future__ import annotations

from pathlib import Path

from src.services.ood_service import OodService
from src.utils.image_ops import overlay_heatmap
from src.utils.logger import get_logger

logger = get_logger("gradcam")


def _preprocess(path, size: int):
    """读图 → 缩放 → 归一化张量（与 OOD 服务同一口径）。"""
    import numpy as np
    import torch
    from PIL import Image

    with Image.open(path) as handle:
        image = handle.convert("RGB").resize((size, size))
    array = np.asarray(image, dtype=np.float32) / 255.0
    return torch.from_numpy(array).permute(2, 0, 1).unsqueeze(0)


class GradCamService:
    """分类模型的 Grad-CAM 热力图。"""

    def __init__(self, weights: str = "", imgsz: int = 224, device: str = "auto"):
        self.weights = str(weights or "")
        self.imgsz = max(32, int(imgsz or 224))
        self.device = str(device or "auto")

    # -----------------------------------------------------------
    @staticmethod
    def is_available() -> bool:
        try:
            import torch  # noqa: F401
            import ultralytics  # noqa: F401
        except ImportError:
            return False
        return True

    @staticmethod
    def supported(weights: str) -> bool:
        """是否可用的分类模型（复用 OOD 的校验口径）。"""
        try:
            return bool(OodService.supported(weights))
        except Exception:  # noqa: BLE001
            return False

    # -----------------------------------------------------------
    def _load(self):
        from ultralytics import YOLO

        if not self.weights:
            raise ValueError("请先选择模型权重")
        module = YOLO(self.weights).model
        if not hasattr(module, "classifier"):
            raise ValueError("Grad-CAM 目前只支持分类模型")
        return module

    @staticmethod
    def _last_conv(module):
        """骨干里最后一个卷积层（Grad-CAM 的目标层）。"""
        import torch

        found = None
        for item in module.modules():
            if isinstance(item, torch.nn.Conv2d):
                found = item
        if found is None:
            raise ValueError("模型里没有卷积层，无法生成 Grad-CAM")
        return found

    def heatmap(self, path, target=None):
        """生成 Grad-CAM 热力图（2D 数组，0~1）。"""
        import numpy as np
        import torch

        module = self._load()
        device = "cpu" if self.device == "auto" else self.device
        module = module.to(device)
        module.eval()
        layer = self._last_conv(module)

        captured: dict = {}
        forward = layer.register_forward_hook(
            lambda _m, _inp, out: captured.__setitem__("activation", out)
        )
        backward = layer.register_full_backward_hook(
            lambda _m, _gin, gout: captured.__setitem__("gradient", gout[0])
        )
        try:
            tensor = _preprocess(path, self.imgsz).to(device)
            logits = module(tensor)
            if target is None:
                target = int(logits.argmax(dim=1).item())
            module.zero_grad()
            logits[0, int(target)].backward(retain_graph=False)
        finally:
            forward.remove()
            backward.remove()

        activation = captured.get("activation")
        gradient = captured.get("gradient")
        if activation is None or gradient is None:
            raise ValueError("未取到卷积层激活或梯度")
        weights = gradient.mean(dim=(2, 3), keepdim=True)
        combined = torch.relu((weights * activation).sum(dim=1)).squeeze()
        array = combined.detach().cpu().numpy().astype(np.float32)
        low, high = float(array.min()), float(array.max())
        if high - low <= 1e-9:
            return np.zeros_like(array)
        return (array - low) / (high - low)

    def overlay(self, path, target=None, target_path=None, alpha: float = 0.55) -> str:
        """生成 Grad-CAM 叠加图，返回图片路径（失败返回空串）。"""
        heat = self.heatmap(path, target)
        if target_path is None:
            target_path = Path(path).with_name(f"{Path(path).stem}_cam.jpg")
        written = overlay_heatmap(path, heat, target_path, alpha=float(alpha))
        return str(written) if written else ""
