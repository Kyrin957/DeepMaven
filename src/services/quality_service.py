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
    # 标签校验（按任务差异）
    # -----------------------------------------------------------
    # 各类任务的 YOLO 行规格（每行「类别 + 坐标值」允许的坐标值个数）：
    #   detect / ocr —— 4 值（cx cy w h；OCR 主标签同为文本框）
    #   obb —— 4 值（未旋转）或 8 值（旋转后为四角点，见画布 rotate_selected）
    #   segment / semantic —— 4 值（退化矩形）或 ≥6 且偶数（多边形点列）
    #   classify / anomaly —— 无 YOLO 标签体系（类别由目录 / 用户标记决定）
    _FIXED_COORDS: dict[str, tuple[int, ...]] = {
        "detect": (4,),
        "ocr": (4,),
        "obb": (4, 8),
    }
    # 多边形类任务：坐标值个数为 4 或 ≥6 的偶数
    _POLYGON_TASKS: tuple[str, ...] = ("segment", "semantic")
    # 无 YOLO 标签体系的任务（整图任务）：跳过标签校验，避免全量误报
    _NO_LABEL_TASKS: tuple[str, ...] = ("classify", "anomaly")

    @classmethod
    def coord_count_ok(cls, task: str, count: int) -> bool:
        """某一行的坐标值个数是否符合任务的 YOLO 行规格。"""
        allowed = cls._FIXED_COORDS.get(task)
        if allowed is not None:
            return count in allowed
        if task in cls._POLYGON_TASKS:
            return count == 4 or (count >= 6 and count % 2 == 0)
        # 未知任务按检测口径（4 值）兜底
        return count == 4

    @classmethod
    def check_labels(
        cls, images: list, label_index: dict, task: str = "detect"
    ) -> dict:
        """按任务校验 YOLO 标签：空文件、行规格、坐标越界、缺失 / 孤立标签。

        Args:
            images: 图片路径列表。
            label_index: {图片文件名主干: 标签路径}。
            task: 项目任务（detect / obb / segment / semantic / ocr /
                classify / anomaly）。分类与异常检测没有每图 .txt 标签
                （类别由目录 / 用户标记决定），跳过校验并置 `skipped`。

        Returns:
            {"empty": [...], "out_of_range": [...], "missing_label": [...],
             "orphan_label": [...], "skipped": bool}
        """
        if task in cls._NO_LABEL_TASKS:
            return {
                "empty": [], "out_of_range": [], "missing_label": [],
                "orphan_label": [], "skipped": True,
            }

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
                if len(parts) < 2:
                    out_of_range.append(f"{label}: 缺少类别字段 -> {line}")
                    continue
                try:
                    float(parts[0])
                except ValueError:
                    out_of_range.append(f"{label}: 类别 ID 非法 -> {line}")
                    continue
                try:
                    coords = [float(v) for v in parts[1:]]
                except ValueError:
                    out_of_range.append(f"{label}: 数值非法 -> {line}")
                    continue
                if not cls.coord_count_ok(task, len(coords)):
                    # 按任务规格校验整行——detect 重复校验、obb 四角点、
                    # 多边形点列等；不再只看前两个点
                    out_of_range.append(f"{label}: 行规格不符 -> {line}")
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
            "skipped": False,
        }
