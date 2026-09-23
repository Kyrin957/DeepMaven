"""U-Net 模型定义与检查点读写（语义分割后端使用）。

刻意保持轻量：编码器 / 解码器各三层 + 瓶颈层，纯 `torch.nn` 实现，
不引入额外依赖，CPU 也能训练小数据集（见《开发文档.md》§8.13）。

检查点里带 `meta`（变体、类别名、输入尺寸），推理与导出据此重建网络。
"""

from __future__ import annotations

from pathlib import Path

# 变体：名称 → 各层基础通道数（由小到大）
VARIANTS: dict[str, tuple[int, int, int]] = {
    "unet-s": (16, 32, 64),
    "unet-m": (32, 64, 128),
}
DEFAULT_VARIANT = "unet-s"


def variant_labels() -> list[dict]:
    """模型下拉用的候选（变体 key + 显示名）。"""
    return [
        {"key": "unet-s", "label": "U-Net 轻量"},
        {"key": "unet-m", "label": "U-Net 标准"},
    ]


def build_model(variant: str = DEFAULT_VARIANT, num_classes: int = 2):
    """构建 U-Net（num_classes 含背景）。"""
    import torch
    import torch.nn as nn

    channels = VARIANTS.get(str(variant), VARIANTS[DEFAULT_VARIANT])
    c1, c2, c3 = channels
    c4 = c3 * 2

    class _DoubleConv(nn.Module):
        def __init__(self, in_channels: int, out_channels: int):
            super().__init__()
            self.block = nn.Sequential(
                nn.Conv2d(in_channels, out_channels, 3, padding=1, bias=False),
                nn.BatchNorm2d(out_channels),
                nn.ReLU(inplace=True),
                nn.Conv2d(out_channels, out_channels, 3, padding=1, bias=False),
                nn.BatchNorm2d(out_channels),
                nn.ReLU(inplace=True),
            )

        def forward(self, x):
            return self.block(x)

    class _UNet(nn.Module):
        def __init__(self):
            super().__init__()
            self.down1 = _DoubleConv(3, c1)
            self.down2 = _DoubleConv(c1, c2)
            self.down3 = _DoubleConv(c2, c3)
            self.pool = nn.MaxPool2d(2)
            self.bottleneck = _DoubleConv(c3, c4)
            self.up3 = nn.ConvTranspose2d(c4, c3, 2, stride=2)
            self.dec3 = _DoubleConv(c3 * 2, c3)
            self.up2 = nn.ConvTranspose2d(c3, c2, 2, stride=2)
            self.dec2 = _DoubleConv(c2 * 2, c2)
            self.up1 = nn.ConvTranspose2d(c2, c1, 2, stride=2)
            self.dec1 = _DoubleConv(c1 * 2, c1)
            self.head = nn.Conv2d(c1, num_classes, 1)

        def forward(self, x):
            d1 = self.down1(x)
            d2 = self.down2(self.pool(d1))
            d3 = self.down3(self.pool(d2))
            b = self.bottleneck(self.pool(d3))
            u3 = self.dec3(torch.cat([self.up3(b), d3], dim=1))
            u2 = self.dec2(torch.cat([self.up2(u3), d2], dim=1))
            u1 = self.dec1(torch.cat([self.up1(u2), d1], dim=1))
            return self.head(u1)

    return _UNet()


def save_checkpoint(
    path: str | Path, model, meta: dict, variant: str = DEFAULT_VARIANT
) -> Path:
    """保存检查点（权重 + 结构元信息，便于推理与导出时重建）。"""
    import torch

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "state_dict": model.state_dict(),
            "meta": {**meta, "variant": variant, "arch": "unet"},
        },
        target,
    )
    return target


def load_checkpoint(path: str | Path) -> tuple:
    """读取检查点，返回 (model, meta, variant)。"""
    import torch

    payload = torch.load(str(path), map_location="cpu", weights_only=False)
    meta = dict(payload.get("meta") or {})
    variant = str(meta.get("variant") or DEFAULT_VARIANT)
    num_classes = int(meta.get("num_classes") or 2)
    model = build_model(variant, num_classes)
    model.load_state_dict(payload.get("state_dict") or {})
    model.eval()
    return model, meta, variant
