"""模型导出服务：封装 YOLO 模型导出为 PT / ONNX / TorchScript。"""

from __future__ import annotations

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
        """执行导出并返回导出文件路径。"""
        yolo = self._get_yolo(config.weights_path)
        fmt_args = {
            "imgsz": config.imgsz,
            "opset": config.opset,
            "dynamic": config.dynamic,
        }
        if config.format == "onnx":
            fmt_args["simplify"] = config.simplify

        result = yolo.export(format=config.format, **fmt_args)
        path = result if isinstance(result, (str, Path)) else None
        if path is None:
            # 某些版本返回模型对象，从导出目录推断文件名
            path = Path(config.output_dir) / f"{Path(config.weights_path).stem}.{config.format}"
        out = Path(path)
        logger.info("模型已导出: %s", out)
        return out