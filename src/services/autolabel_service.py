"""半自动预标注服务：YOLO 候选框 + SAM 精细化。

两步流程：
    1. **YOLO 预标注**：用预训练 / 已训练权重批量生成候选框；
    2. **SAM 细化**：以候选框或点击点为提示，用 SAM 分割出掩码并转为多边形。

两者均通过 Ultralytics 实现（已安装），SAM 权重由 Ultralytics 按需下载，
无需额外安装分割依赖。前置依赖采用方法内懒加载。
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from src.models.annotation import BOX, Annotation
from src.utils.logger import get_logger

logger = get_logger("autolabel")

DEFAULT_DETECT_WEIGHTS = "yolo11n.pt"
DEFAULT_SAM_WEIGHTS = "mobile_sam.pt"

ProgressFn = Callable[[int, str], None]
CancelFn = Callable[[], bool]


def _noop_progress(_percent: int, _text: str = "") -> None:
    pass


def _noop_cancel() -> bool:
    return False


class AutoLabelService:
    """预标注与精细化（静态方法，模型按需加载）。"""

    # -----------------------------------------------------------
    # 依赖可用性
    # -----------------------------------------------------------
    @staticmethod
    def is_available() -> bool:
        """Ultralytics 是否可用。"""
        try:
            import ultralytics  # noqa: F401
        except ImportError:
            return False
        return True

    # -----------------------------------------------------------
    # YOLO 预标注
    # -----------------------------------------------------------
    @staticmethod
    def detect(
        weights: str,
        images: list,
        conf: float = 0.25,
        iou: float = 0.45,
        device: str = "auto",
        progress: ProgressFn | None = None,
        is_cancelled: CancelFn | None = None,
    ) -> dict[str, list[Annotation]]:
        """对图片批量推理，返回 {图片文件名: [矩形标注]}。"""
        progress = progress or _noop_progress
        is_cancelled = is_cancelled or _noop_cancel

        from ultralytics import YOLO

        paths = [Path(p) for p in images]
        if not paths:
            return {}

        progress(0, f"加载模型 {weights}")
        model = YOLO(weights or DEFAULT_DETECT_WEIGHTS)

        progress(5, "开始推理")
        results = model.predict(
            source=[str(p) for p in paths],
            conf=conf,
            iou=iou,
            device=device,
            verbose=False,
        )

        total = len(results) or 1
        output: dict[str, list[Annotation]] = {}
        for index, result in enumerate(results):
            if is_cancelled():
                break
            output[Path(result.path).name] = AutoLabelService.boxes_to_annotations(result)
            progress(5 + int(90 * (index + 1) / total), f"已处理 {index + 1}/{total}")
        progress(100, "预标注完成")
        return output

    @staticmethod
    def boxes_to_annotations(result) -> list[Annotation]:
        """把 Ultralytics 推理结果中的检测框转为归一化标注。"""
        boxes = getattr(result, "boxes", None)
        if boxes is None or len(boxes) == 0:
            return []
        height, width = result.orig_shape
        width = width or 1
        height = height or 1

        xyxy = boxes.xyxy.cpu().numpy()
        cls_ids = boxes.cls.cpu().numpy().astype(int)
        items: list[Annotation] = []
        for index in range(len(cls_ids)):
            x1, y1, x2, y2 = (float(v) for v in xyxy[index])
            items.append(Annotation(
                cls_id=int(cls_ids[index]),
                kind=BOX,
                points=[
                    (x1 / width, y1 / height),
                    (x2 / width, y2 / height),
                ],
            ))
        return items

    # -----------------------------------------------------------
    # SAM 细化
    # -----------------------------------------------------------
    @staticmethod
    def segment(
        image_path: str,
        boxes: list | None = None,
        points: list | None = None,
        labels: list | None = None,
        weights: str = DEFAULT_SAM_WEIGHTS,
    ) -> list[list[tuple[float, float]]]:
        """用 SAM 对提示（框或点）分割，返回归一化多边形列表。"""
        from ultralytics import SAM

        model = SAM(weights or DEFAULT_SAM_WEIGHTS)
        if boxes:
            results = model(image_path, bboxes=boxes)
        elif points:
            results = model(image_path, points=points, labels=labels)
        else:
            return []

        if not results:
            return []
        result = results[0]
        masks = getattr(result, "masks", None)
        if masks is None or len(masks) == 0:
            return []

        height, width = result.orig_shape
        polygons: list[list[tuple[float, float]]] = []
        for mask in masks.data.cpu().numpy():
            polygons.extend(
                AutoLabelService.mask_to_polygons(mask, width or 1, height or 1)
            )
        return polygons

    @staticmethod
    def mask_to_polygons(
        mask, width: int, height: int, epsilon_ratio: float = 0.002,
        min_area: float = 16.0,
    ) -> list[list[tuple[float, float]]]:
        """把二值掩码转为归一化多边形（外轮廓 + 多边形逼近）。"""
        import cv2
        import numpy as np

        binary = (np.asarray(mask) > 0).astype("uint8") * 255
        contours, _ = cv2.findContours(
            binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        polygons: list[list[tuple[float, float]]] = []
        for contour in contours:
            if cv2.contourArea(contour) < min_area:
                continue
            epsilon = epsilon_ratio * cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, epsilon, True)
            if len(approx) < 3:
                continue
            polygons.append([
                (
                    min(max(float(p[0][0]) / width, 0.0), 1.0),
                    min(max(float(p[0][1]) / height, 0.0), 1.0),
                )
                for p in approx
            ])
        return polygons
