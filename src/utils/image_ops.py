"""图像小工具：显示增强（亮度 / 对比度）等**只影响显示、不改原图**的处理。

图库 / 检查页的亮度对比度与 Halcon DLT 一致，仅用于「看不清时调一下」：
调整结果不写回文件，也不会进入训练数据。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QImage, QPixmap

from src.utils.logger import get_logger

logger = get_logger("image_ops")

# 显示增强的量化步长（缓存键用，避免浮点误差产生一堆等价缓存）
_STEP = 0.01


def normalize_display(value: float) -> float:
    """把显示参数夹到 [-1, 1] 并按步长取整（便于缓存命中）。"""
    number = max(-1.0, min(1.0, float(value or 0.0)))
    return round(number / _STEP) * _STEP


def adjust_qimage(
    image: QImage, brightness: float = 0.0, contrast: float = 0.0
) -> QImage:
    """按亮度 / 对比度调整图像（用 256 级查找表，只改 RGB 通道）。

    Args:
        brightness: -1.0 ~ 1.0，0 为原图（正数提亮）。
        contrast: -1.0 ~ 1.0，0 为原图（正数增强对比）。

    返回新的 `QImage`；无法处理时原样返回（调用方按「未调整」处理）。
    """
    brightness = normalize_display(brightness)
    contrast = normalize_display(contrast)
    if not brightness and not contrast:
        return image
    source = image.convertToFormat(QImage.Format.Format_RGB32)
    width, height = source.width(), source.height()
    if width <= 0 or height <= 0:
        return image
    try:
        import numpy as np
    except ImportError:  # pragma: no cover - numpy 随 torch 一起安装
        logger.warning("未安装 numpy，显示增强不可用")
        return image

    buffer = source.bits()
    array = np.frombuffer(buffer, dtype=np.uint8)
    if not array.flags.writeable:
        return image
    levels = np.arange(256, dtype=np.float32)
    factor = 1.0 + float(contrast)
    lut = np.clip(
        (levels - 128.0) * factor + 128.0 + float(brightness) * 255.0, 0.0, 255.0
    ).astype(np.uint8)
    rows = max(1, source.bytesPerLine() // 4)
    view = array.reshape(height, rows, 4)
    view[..., :3] = lut[view[..., :3]]
    return source


def adjust_pixmap(
    source: QPixmap, brightness: float = 0.0, contrast: float = 0.0
) -> QPixmap:
    """按亮度 / 对比度调整位图；参数为 0 时直接返回原图。"""
    brightness = normalize_display(brightness)
    contrast = normalize_display(contrast)
    if source.isNull() or (not brightness and not contrast):
        return source
    return QPixmap.fromImage(adjust_qimage(source.toImage(), brightness, contrast))


# -----------------------------------------------------------
# 训练参数预览（letterbox + 在线增强示意）
# -----------------------------------------------------------
LETTERBOX_PAD = 114       # Ultralytics 的补边灰（与训练口径一致）
_CAPTION_BG = (28, 28, 28)


def _letterbox(image, size: int, pad: int = LETTERBOX_PAD):
    """等比缩放到 size×size 方形画布并补边（与训练时的 letterbox 一致）。"""
    from PIL import Image

    ratio = min(size / max(1, image.width), size / max(1, image.height))
    inner = image.resize((
        max(1, int(round(image.width * ratio))),
        max(1, int(round(image.height * ratio))),
    ), Image.Resampling.BILINEAR)
    board = Image.new("RGB", (size, size), (pad, pad, pad))
    board.paste(inner, ((size - inner.width) // 2, (size - inner.height) // 2))
    return board


def augment_preview_image(image, config, rng):
    """按训练增强参数处理一张图（**示意**：翻转 / 旋转 / 缩放 / HSV）。

    Returns:
        (处理后的 PIL 图像, 生效的增强说明列表)。mosaic / mixup 需要多张图
        拼接，预览里不体现；因此说明文字会明确标注「示意」。
    """
    from PIL import Image

    import numpy as np

    applied: list[str] = []
    if float(getattr(config, "hflip", 0.0) or 0.0) > 0 and rng.random() < float(config.hflip):
        image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        applied.append("水平翻转")
    if float(getattr(config, "vflip", 0.0) or 0.0) > 0 and rng.random() < float(config.vflip):
        image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        applied.append("垂直翻转")
    degrees = float(getattr(config, "degrees", 0.0) or 0.0)
    if degrees > 0:
        angle = rng.uniform(-degrees, degrees)
        if abs(angle) > 0.05:
            image = image.rotate(
                angle, resample=Image.Resampling.BILINEAR, expand=False,
                fillcolor=(LETTERBOX_PAD, LETTERBOX_PAD, LETTERBOX_PAD),
            )
            applied.append(f"旋转 {angle:+.0f}°")
    scale = float(getattr(config, "scale", 0.0) or 0.0)
    if scale > 0:
        factor = 1.0 + rng.uniform(-scale, scale)
        width, height = image.size
        resized = image.resize((
            max(8, int(width * factor)), max(8, int(height * factor))
        ), Image.Resampling.BILINEAR)
        board = Image.new("RGB", (width, height),
                          (LETTERBOX_PAD, LETTERBOX_PAD, LETTERBOX_PAD))
        board.paste(resized, ((width - resized.width) // 2,
                              (height - resized.height) // 2))
        image = board
        applied.append(f"缩放 ×{factor:.2f}")

    gains = []
    for key, label, span in (
        ("hsv_h", "色调", 0.5), ("hsv_s", "饱和", 1.0), ("hsv_v", "亮度", 1.0),
    ):
        value = float(getattr(config, key, 0.0) or 0.0)
        if value <= 0:
            continue
        gain = 1.0 + rng.uniform(-value, value) * span
        gains.append(gain)
    if any(abs(gain - 1.0) > 1e-3 for gain in gains):
        array = np.asarray(image).astype(np.float32)
        for index, gain in enumerate(gains):
            array[..., index] = np.clip(array[..., index] * gain, 0, 255)
        image = Image.fromarray(array.astype(np.uint8))
        applied.append("色彩抖动")
    return image, applied


def build_param_preview(
    config, paths: list, target, size: int = 256, columns: int = 4,
    seed: int = 0,
) -> str:
    """生成训练参数预览拼图（每张图 letterbox 到 `size` 并叠加增强）。

    Args:
        config: 训练配置（读取 imgsz / 增强参数）。
        paths: 用于预览的图片路径（取前 `columns × 2` 张）。
        target: 输出图片路径。
        size: 单张缩略图边长（= 预览用的 imgsz 缩放结果）。
    Returns:
        实际写出的路径；失败返回空串。
    """
    import random

    from PIL import Image, ImageDraw

    rng = random.Random(int(seed))
    imgsz = int(getattr(config, "imgsz", 0) or 640)
    tiles = []
    for raw in list(paths)[: max(1, int(columns) * 2)]:
        try:
            with Image.open(raw) as handle:
                source = handle.convert("RGB")
        except Exception as exc:  # noqa: BLE001 - 单张失败跳过
            logger.debug("预览读图失败 %s: %s", raw, exc)
            continue
        board = _letterbox(source, size)
        note = f"原图 {source.width}×{source.height}"
        if bool(getattr(config, "augment", True)):
            board, applied = augment_preview_image(board, config, rng)
            if applied:
                note = "＋".join(applied)
        tiles.append((board, note))
    if not tiles:
        return ""

    rows = (len(tiles) + columns - 1) // columns
    gap = 8
    caption = 18
    canvas = Image.new(
        "RGB",
        (columns * size + (columns + 1) * gap,
         rows * (size + caption) + (rows + 1) * gap + caption),
        _CAPTION_BG,
    )
    draw = ImageDraw.Draw(canvas)
    font_caption = _preview_font(max(11, size // 22))
    for index, (tile, note) in enumerate(tiles):
        column, row = index % columns, index // columns
        left = gap + column * (size + gap)
        top = gap + caption + row * (size + caption + gap)
        canvas.paste(tile, (left, top))
        draw.text((left + 4, top + size + 2), note, fill=(220, 220, 220),
                  font=font_caption)
    draw.text(
        (gap, 4),
        f"训练参数预览 · imgsz {imgsz} → 预览边长 {size} · "
        f"增强 {'开启' if bool(getattr(config, 'augment', True)) else '关闭'}"
        "（mosaic / mixup 未体现）",
        fill=(255, 255, 255), font=font_caption,
    )
    target = Path(target)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(target, quality=88)
    except (OSError, ValueError) as exc:
        logger.warning("保存预览图失败 %s: %s", target, exc)
        return ""
    return str(target)


def _preview_font(size: int):
    """预览图用字体（复用评估可视化里的中文字体查找）。"""
    from src.services.evaluation_service import _load_font

    return _load_font(size)


# -----------------------------------------------------------
# 异常热力图（Anomalib 的 anomaly_map 叠加到原图）
# -----------------------------------------------------------
def save_rgb(array, target) -> str:
    """把 RGB 数组存成图片（热力图 / 叠加图公用）。"""
    from PIL import Image

    target = Path(target)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(array.astype("uint8")).save(target, quality=88)
    except (OSError, ValueError) as exc:
        logger.warning("保存图像失败 %s: %s", target, exc)
        return ""
    return str(target)


def colorize_heat(heat, width: int, height: int):
    """把异常分数图（任意量级）缩放并映射成 JET 彩色图（RGB 数组）。"""
    try:
        import cv2
        import numpy as np
    except ImportError:
        logger.warning("需要 OpenCV 才能生成热力图")
        return None
    array = np.nan_to_num(np.asarray(heat, dtype=np.float32), nan=0.0)
    array = np.squeeze(array)
    if array.ndim != 2 or array.size == 0:
        return None
    low, high = float(array.min()), float(array.max())
    span = max(1e-9, high - low)
    normalized = ((array - low) / span * 255).astype("uint8")
    scaled = cv2.resize(normalized, (int(width), int(height)),
                        interpolation=cv2.INTER_LINEAR)
    return cv2.cvtColor(cv2.applyColorMap(scaled, cv2.COLORMAP_JET),
                        cv2.COLOR_BGR2RGB)


def overlay_heatmap(image_path, heat, target, alpha: float = 0.55) -> str:
    """把异常热力图叠加到原图上（评估页「热图」视图）。

    Args:
        heat: 异常分数图（2D 数组，任意量级，内部会归一化）。
        alpha: 热力图的不透明度（0~1，越大越突出异常区域）。
    """
    try:
        from PIL import Image as PILImage
    except ImportError:
        logger.warning("未安装 Pillow，无法生成热力图")
        return ""
    try:
        with PILImage.open(image_path) as handle:
            base = handle.convert("RGB")
    except (OSError, ValueError) as exc:
        logger.warning("读取图像失败 %s: %s", image_path, exc)
        return ""
    colored = colorize_heat(heat, base.width, base.height)
    if colored is None:
        return ""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return ""
    blended = cv2.addWeighted(
        np.asarray(base), 1.0 - float(alpha), colored, float(alpha), 0
    )
    return save_rgb(blended, target)
