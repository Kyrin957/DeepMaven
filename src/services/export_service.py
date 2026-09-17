"""模型导出服务：封装 YOLO 模型导出为 PT / ONNX / TorchScript。"""

from __future__ import annotations

import shutil
from pathlib import Path

from src.models.training import ExportConfig
from src.utils.logger import get_logger

logger = get_logger("export")


class ExportService:
    """将训练完成的模型导出为部署格式。"""

    def __init__(self):
        self._yolo = None

    def _get_yolo(self, weights: str):
        """懒加载 YOLOService 并切换到指定权重。"""
        if self._yolo is None:
            from src.services.yolo_service import YOLOService

            self._yolo = YOLOService(weights)
        else:
            self._yolo.set_weights(weights)
        return self._yolo

    def export(self, config: ExportConfig) -> Path:
        """执行导出并返回导出文件路径。

        Ultralytics 会把导出文件写在源权重旁边，若指定了 output_dir，
        则把产物移动过去。
        """
        yolo = self._get_yolo(config.weights_path)
        fmt_args = {
            "imgsz": config.imgsz,
            "opset": config.opset,
            "dynamic": config.dynamic,
        }
        if config.format == "onnx":
            fmt_args["simplify"] = config.simplify

        result = yolo.export(format=config.format, **fmt_args)
        produced = Path(str(result)) if result else None
        if produced is None or not produced.is_file():
            produced = Path(config.weights_path).with_suffix(f".{config.format}")

        out = self._place(produced, config.output_dir)
        logger.info("模型已导出: %s", out)
        return out

    @staticmethod
    def _place(produced: Path, output_dir: str) -> Path:
        """把导出产物移动到指定目录（未指定则原地返回）。"""
        if not output_dir or not produced.is_file():
            return produced
        target_dir = Path(output_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / produced.name
        if produced.resolve() != target.resolve():
            shutil.move(str(produced), str(target))
        return target