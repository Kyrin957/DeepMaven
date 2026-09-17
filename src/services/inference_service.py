"""推理服务：基于 Ultralytics 的图片 / 视频 / 相机缺陷检测。

坐标与统计结果统一为普通 Python 结构，便于界面展示与报告导出。
"""

from __future__ import annotations

import time
from typing import Callable

from src.utils.logger import get_logger

logger = get_logger("inference")

ProgressFn = Callable[[int, str], None]
CancelFn = Callable[[], bool]


def _noop_progress(_percent: int, _text: str = "") -> None:
    pass


def _noop_cancel() -> bool:
    return False


class InferenceService:
    """检测推理（静态方法，模型按需加载）。"""

    # -----------------------------------------------------------
    # 可用性 / 模型
    # -----------------------------------------------------------
    @staticmethod
    def is_available() -> bool:
        try:
            import cv2  # noqa: F401
            import ultralytics  # noqa: F401
        except ImportError:
            return False
        return True

    @staticmethod
    def load_model(weights: str):
        """加载 YOLO 模型。"""
        from ultralytics import YOLO

        return YOLO(weights)

    # -----------------------------------------------------------
    # 结果解析
    # -----------------------------------------------------------
    @staticmethod
    def extract_records(result, frame: int | None = None) -> list[dict]:
        """把推理结果中的检测框转为记录列表。"""
        boxes = getattr(result, "boxes", None)
        if boxes is None or len(boxes) == 0:
            return []

        names = getattr(result, "names", {}) or {}
        xyxy = boxes.xyxy.cpu().numpy()
        confidences = boxes.conf.cpu().numpy()
        classes = boxes.cls.cpu().numpy().astype(int)

        records: list[dict] = []
        for index in range(len(classes)):
            x1, y1, x2, y2 = (float(value) for value in xyxy[index])
            cls_id = int(classes[index])
            record = {
                "class_id": cls_id,
                "class_name": names.get(cls_id, str(cls_id)),
                "confidence": round(float(confidences[index]), 4),
                "x1": round(x1, 1),
                "y1": round(y1, 1),
                "x2": round(x2, 1),
                "y2": round(y2, 1),
                "width": round(abs(x2 - x1), 1),
                "height": round(abs(y2 - y1), 1),
            }
            if frame is not None:
                record["frame"] = frame
            records.append(record)
        return records

    @staticmethod
    def plot(result):
        """绘制检测结果（BGR 数组），失败返回 None。"""
        try:
            return result.plot()
        except Exception as exc:  # noqa: BLE001 - 绘制失败不应中断流程
            logger.warning("绘制检测结果失败: %s", exc)
            return None

    # -----------------------------------------------------------
    # 图片 / 视频
    # -----------------------------------------------------------
    @staticmethod
    def detect_image(
        weights: str, source, conf: float = 0.25, iou: float = 0.45,
        device: str = "auto",
    ) -> dict:
        """单图检测。"""
        model = InferenceService.load_model(weights)
        start = time.perf_counter()
        results = model.predict(
            source=str(source), conf=conf, iou=iou, device=device, verbose=False
        )
        elapsed = time.perf_counter() - start
        if not results:
            return {"annotated": None, "records": [], "elapsed": elapsed}
        result = results[0]
        return {
            "annotated": InferenceService.plot(result),
            "records": InferenceService.extract_records(result),
            "elapsed": elapsed,
        }

    @staticmethod
    def detect_video(
        weights: str, source, conf: float = 0.25, iou: float = 0.45,
        device: str = "auto",
        progress: ProgressFn | None = None,
        is_cancelled: CancelFn | None = None,
    ) -> dict:
        """视频逐帧检测，返回最后一帧的标注图与全部检测记录。"""
        import cv2

        progress = progress or _noop_progress
        is_cancelled = is_cancelled or _noop_cancel
        model = InferenceService.load_model(weights)

        capture = cv2.VideoCapture(str(source))
        if not capture.isOpened():
            raise RuntimeError(f"无法打开视频：{source}")

        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        records: list[dict] = []
        annotated = None
        index = 0
        start = time.perf_counter()
        try:
            while True:
                if is_cancelled():
                    break
                ok, frame = capture.read()
                if not ok:
                    break
                index += 1
                results = model.predict(
                    source=frame, conf=conf, iou=iou, device=device, verbose=False
                )
                if results:
                    result = results[0]
                    annotated = InferenceService.plot(result)
                    records.extend(InferenceService.extract_records(result, index))
                if total and index % 5 == 0:
                    progress(int(100 * index / total), f"帧 {index}/{total}")
        finally:
            capture.release()

        progress(100, f"共 {index} 帧")
        return {
            "annotated": annotated,
            "records": records,
            "frames": index,
            "elapsed": time.perf_counter() - start,
        }
