"""YOLO 训练/推理服务封装。

参照开发文档 6.1 节，将 Ultralytics YOLO 训练/推理/导出能力封装为
可注入 ViewModel 的服务对象。torch/ultralytics 采用懒加载，降低启动开销。
"""

from __future__ import annotations

from pathlib import Path

from src.utils.logger import get_logger

logger = get_logger("yolo")


class YOLOService:
    """基于 Ultralytics YOLO 的训练与推理封装。"""

    def __init__(self, weights: str | Path = "yolo11n.pt"):
        self._weights = str(weights)
        self._model = None  # 懒加载的 YOLO 实例

    # -----------------------------------------------------------
    # 属性
    # -----------------------------------------------------------
    @property
    def model(self):
        """当前加载的 YOLO 模型（懒加载）。"""
        if self._model is None:
            from ultralytics import YOLO

            logger.info("加载模型: %s", self._weights)
            self._model = YOLO(self._weights)
        return self._model

    @property
    def weights(self) -> str:
        return self._weights

    def set_weights(self, weights: str) -> None:
        self._model = None
        self._weights = weights

    # -----------------------------------------------------------
    # 训练
    # -----------------------------------------------------------
    def train(
        self,
        data_yaml: str,
        epochs: int = 100,
        batch: int = 16,
        imgsz: int = 640,
        lr: float = 0.01,
        optimizer: str = "auto",
        device: str = "auto",
        project: str = "runs",
        name: str = "train",
        **kwargs,
    ):
        """启动模型训练。

        Args:
            data_yaml: 数据集配置文件 (data.yaml) 路径。
            epochs/batch/imgsz/lr/optimizer/device: 训练超参数。
            project/name: 训练结果保存位置。
            **kwargs: 透传给 ultralytics 的其它参数。
        """
        logger.info(
            "开始训练 data=%s epochs=%s batch=%s imgsz=%s lr=%s",
            data_yaml, epochs, batch, imgsz, lr,
        )
        return self.model.train(
            data=data_yaml,
            epochs=epochs,
            batch=batch,
            imgsz=imgsz,
            lr0=lr,
            optimizer=optimizer,
            device=device,
            project=project,
            name=name,
            **kwargs,
        )

    # -----------------------------------------------------------
    # 推理
    # -----------------------------------------------------------
    def predict(
        self,
        source,
        conf: float = 0.25,
        iou: float = 0.45,
        device: str = "auto",
        **kwargs,
    ):
        """对图片/视频/相机源执行检测。"""
        return self.model.predict(
            source=source, conf=conf, iou=iou, device=device, **kwargs
        )

    # -----------------------------------------------------------
    # 导出
    # -----------------------------------------------------------
    def export(self, format: str = "onnx", **kwargs):
        """导出模型为指定格式。"""
        logger.info("导出模型为 %s", format)
        return self.model.export(format=format, **kwargs)

    # -----------------------------------------------------------
    # 模型信息
    # -----------------------------------------------------------
    def info(self) -> dict:
        """返回模型的基础信息（任务类型、类别、参数量、层数）。"""
        result = {
            "weights": self._weights,
            "task": "unknown",
            "names": {},
            "params": 0,
            "layers": 0,
        }
        try:
            model = self.model
            result["task"] = getattr(model, "task", "unknown")
            result["names"] = dict(getattr(model, "names", {}) or {})
            net = getattr(model, "model", None)
            if net is not None and hasattr(net, "parameters"):
                result["params"] = int(sum(p.numel() for p in net.parameters()))
                result["layers"] = int(sum(1 for _ in net.modules()))
        except Exception as exc:  # noqa: BLE001 - 权重缺失/损坏等
            logger.warning("获取模型信息失败: %s", exc)
        return result