"""数据质检服务：重复图片、模糊/曝光异常、YOLO 标签校验。

仅使用已安装依赖（Pillow / NumPy / OpenCV），不引入额外第三方库。
图像处理依赖采用方法内懒加载，避免拖慢应用启动。
"""

from __future__ import annotations

from pathlib import Path

from src.utils.logger import get_logger

logger = get_logger("quality")


class QualityService:
    """数据集质量检查（静态方法，不持有状态）。"""

    # -----------------------------------------------------------
    # 重复图片（感知哈希 dHash）
    # -----------------------------------------------------------
    @staticmethod
    def perceptual_hash(path: str | Path, size: int = 8) -> int | None:
        """计算图片的 dHash（difference hash），失败返回 None。"""
        from PIL import Image

        try:
            with Image.open(path) as img:
                gray = img.convert("L").resize((size + 1, size))
                pixels = list(gray.getdata())
        except Exception as exc:  # noqa: BLE001 - 图片损坏或格式异常
            logger.warning("读取图片失败 %s: %s", path, exc)
            return None

        bits = 0
        for row in range(size):
            base = row * (size + 1)
            for col in range(size):
                bits = (bits << 1) | int(pixels[base + col] > pixels[base + col + 1])
        return bits

    @staticmethod
    def find_duplicates(paths: list, max_distance: int = 0) -> list[list[str]]:
        """按感知哈希查找重复 / 近重复图片，返回分组（每组 >= 2 张）。

        Args:
            paths: 图片路径列表。
            max_distance: 汉明距离阈值，0 表示完全一致。
        """
        hashed: list[tuple[int, str]] = []
        for path in paths:
            value = QualityService.perceptual_hash(path)
            if value is not None:
                hashed.append((value, str(path)))

        groups: list[list[str]] = []
        used: set[int] = set()
        for i, (hi, pi) in enumerate(hashed):
            if i in used:
                continue
            group = [pi]
            for j in range(i + 1, len(hashed)):
                if j in used:
                    continue
                if bin(hi ^ hashed[j][0]).count("1") <= max_distance:
                    used.add(j)
                    group.append(hashed[j][1])
            if len(group) > 1:
                used.add(i)
                groups.append(group)
        return groups

    # -----------------------------------------------------------
    # 模糊 / 曝光
    # -----------------------------------------------------------
    @staticmethod
    def sharpness(path: str | Path) -> float | None:
        """用 Laplacian 方差评估清晰度，数值越小越模糊。"""
        import cv2

        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            return None
        return float(cv2.Laplacian(image, cv2.CV_64F).var())

    @staticmethod
    def brightness(path: str | Path) -> float | None:
        """返回平均亮度（0~255）。"""
        import cv2

        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            return None
        return float(image.mean())

    @staticmethod
    def find_blurry(paths: list, threshold: float = 100.0) -> list[tuple[str, float]]:
        """找出清晰度低于阈值的图片。"""
        result: list[tuple[str, float]] = []
        for path in paths:
            value = QualityService.sharpness(path)
            if value is not None and value < threshold:
                result.append((str(path), value))
        return result

    @staticmethod
    def find_exposure(
        paths: list, low: float = 30.0, high: float = 225.0
    ) -> list[tuple[str, float]]:
        """找出过暗或过亮的图片。"""
        result: list[tuple[str, float]] = []
        for path in paths:
            value = QualityService.brightness(path)
            if value is not None and (value < low or value > high):
                result.append((str(path), value))
        return result

    # -----------------------------------------------------------
    # 标签校验
    # -----------------------------------------------------------
    @staticmethod
    def check_labels(images: list, label_index: dict) -> dict:
        """校验 YOLO 标签：空文件、坐标越界、缺失标签、孤立标签。

        Args:
            images: 图片路径列表。
            label_index: {图片文件名主干: 标签路径}。

        Returns:
            {"empty": [...], "out_of_range": [...],
             "missing_label": [...], "orphan_label": [...]}
        """
        empty: list[str] = []
        out_of_range: list[str] = []
        missing_label: list[str] = []

        stems: set[str] = set()
        for image in images:
            image = Path(image)
            stems.add(image.stem)
            label = label_index.get(image.stem)
            if label is None:
                missing_label.append(str(image))
                continue
            try:
                lines = [
                    line.strip()
                    for line in Path(label).read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            except OSError as exc:
                logger.warning("读取标签失败 %s: %s", label, exc)
                out_of_range.append(f"{label}: 无法读取")
                continue
            if not lines:
                empty.append(str(label))
                continue
            for line in lines:
                parts = line.split()
                if len(parts) < 5:
                    out_of_range.append(f"{label}: 字段不足 -> {line}")
                    continue
                try:
                    coords = [float(v) for v in parts[1:5]]
                except ValueError:
                    out_of_range.append(f"{label}: 数值非法 -> {line}")
                    continue
                if any(v < 0.0 or v > 1.0 for v in coords):
                    out_of_range.append(f"{label}: 坐标越界 -> {line}")

        orphan_label = [
            str(path) for stem, path in label_index.items() if stem not in stems
        ]
        return {
            "empty": empty,
            "out_of_range": out_of_range,
            "missing_label": missing_label,
            "orphan_label": orphan_label,
        }
