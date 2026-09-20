"""模型导出服务：封装 YOLO 模型导出为 PT / ONNX / TorchScript。"""

from __future__ import annotations

import shutil
from pathlib import Path

from src.models.training import ExportConfig
from src.utils.logger import get_logger

logger = get_logger("export")

# 各导出格式实际支持的参数：Ultralytics 会拒绝格式不支持的额外参数
# （例如 torchscript 不接受 opset / dynamic），因此按格式白名单传参。
_FORMAT_ARGS = {
    "onnx": ("imgsz", "opset", "dynamic", "simplify", "half"),
    "torchscript": ("imgsz", "half"),
    "pt": ("imgsz",),
}


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
        allowed = _FORMAT_ARGS.get(str(config.format), ("imgsz",))
        values = {
            "imgsz": config.imgsz,
            "opset": config.opset,
            "dynamic": config.dynamic,
            "simplify": config.simplify,
            "half": bool(getattr(config, "half", False)),
        }
        fmt_args = {key: values[key] for key in allowed}
        if not fmt_args.get("half"):
            fmt_args.pop("half", None)      # 半精度只在勾选时传入

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