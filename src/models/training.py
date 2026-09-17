"""训练配置数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TrainingConfig:
    """一次训练的全部可配置参数。

    对应开发文档「模型训练」模块：任务类型、模型变体、epochs、
    batch size、学习率、图像尺寸、优化器等。
    """

    task_type: str = "detect"          # detect / segment / classify
    model_key: str = "yolo11n"         # 模型变体 key（见 constants.YOLO_MODEL_VARIANTS）
    weights_path: str = ""             # 自定义预训练权重（.pt），为空则用官方权重
    data_yaml: str = ""                # 数据集 data.yaml 路径
    epochs: int = 100
    batch: int = 16
    lr: float = 0.01
    imgsz: int = 640
    optimizer: str = "auto"
    device: str = "auto"               # auto / cpu / cuda:0 ...
    workers: int = 4
    seed: int = 0
    resume: bool = False               # 断点续训
    project_dir: str = ""              # 训练结果保存目录

    # 异常检测（Anomalib）参数
    anomaly_root: str = ""             # 数据集根目录（含 normal/、abnormal/）
    anomaly_normal_dir: str = "normal"
    anomaly_abnormal_dir: str = "abnormal"
    anomaly_pretrained: bool = True    # 是否加载骨干预训练权重（离线可关闭）

    # 训练中实时状态（非用户配置，由 ViewModel/Service 更新）
    status: str = "idle"               # idle / running / paused / finished / error
    progress: float = 0.0              # 0 ~ 1
    current_epoch: int = 0
    loss: float = 0.0
    mAP50: float = 0.0
    precision: float = 0.0
    recall: float = 0.0

    def to_dict(self) -> dict:
        return {
            "task_type": self.task_type,
            "model_key": self.model_key,
            "weights_path": self.weights_path,
            "data_yaml": self.data_yaml,
            "epochs": self.epochs,
            "batch": self.batch,
            "lr": self.lr,
            "imgsz": self.imgsz,
            "optimizer": self.optimizer,
            "device": self.device,
            "workers": self.workers,
            "seed": self.seed,
            "resume": self.resume,
            "project_dir": self.project_dir,
            "anomaly_root": self.anomaly_root,
            "anomaly_normal_dir": self.anomaly_normal_dir,
            "anomaly_abnormal_dir": self.anomaly_abnormal_dir,
            "anomaly_pretrained": self.anomaly_pretrained,
            "status": self.status,
            "progress": self.progress,
            "current_epoch": self.current_epoch,
            "loss": self.loss,
            "mAP50": self.mAP50,
            "precision": self.precision,
            "recall": self.recall,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TrainingConfig":
        return cls(
            task_type=data.get("task_type", "detect"),
            model_key=data.get("model_key", "yolo11n"),
            weights_path=data.get("weights_path", ""),
            data_yaml=data.get("data_yaml", ""),
            epochs=data.get("epochs", 100),
            batch=data.get("batch", 16),
            lr=data.get("lr", 0.01),
            imgsz=data.get("imgsz", 640),
            optimizer=data.get("optimizer", "auto"),
            device=data.get("device", "auto"),
            workers=data.get("workers", 4),
            seed=data.get("seed", 0),
            resume=data.get("resume", False),
            project_dir=data.get("project_dir", ""),
            anomaly_root=data.get("anomaly_root", ""),
            anomaly_normal_dir=data.get("anomaly_normal_dir", "normal"),
            anomaly_abnormal_dir=data.get("anomaly_abnormal_dir", "abnormal"),
            anomaly_pretrained=data.get("anomaly_pretrained", True),
            status=data.get("status", "idle"),
            progress=data.get("progress", 0.0),
            current_epoch=data.get("current_epoch", 0),
            loss=data.get("loss", 0.0),
            mAP50=data.get("mAP50", 0.0),
            precision=data.get("precision", 0.0),
            recall=data.get("recall", 0.0),
        )


@dataclass
class EvaluationConfig:
    """模型评估（推理）参数。"""

    weights_path: str = ""             # 待加载的模型权重
    source_type: str = "image"         # image / video / camera
    source_path: str = ""              # 图片 / 视频文件路径；相机为设备序号
    confidence: float = 0.25           # 置信度阈值
    iou: float = 0.45                  # IOU 阈值
    device: str = "auto"
    anomaly_model: str = "Padim"       # 异常检测模型（权重为 .ckpt 时使用）

    def to_dict(self) -> dict:
        return {
            "weights_path": self.weights_path,
            "source_type": self.source_type,
            "source_path": self.source_path,
            "confidence": self.confidence,
            "iou": self.iou,
            "device": self.device,
            "anomaly_model": self.anomaly_model,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EvaluationConfig":
        return cls(
            weights_path=data.get("weights_path", ""),
            source_type=data.get("source_type", "image"),
            source_path=data.get("source_path", ""),
            confidence=data.get("confidence", 0.25),
            iou=data.get("iou", 0.45),
            device=data.get("device", "auto"),
            anomaly_model=data.get("anomaly_model", "Padim"),
        )


@dataclass
class ExportConfig:
    """模型导出参数。"""

    weights_path: str = ""             # 源模型
    format: str = "onnx"               # pt / onnx / torchscript
    output_dir: str = ""               # 导出目录
    imgsz: int = 640
    opset: int = 12
    dynamic: bool = False              # 动态尺寸
    simplify: bool = True              # ONNX 图精简

    def to_dict(self) -> dict:
        return {
            "weights_path": self.weights_path,
            "format": self.format,
            "output_dir": self.output_dir,
            "imgsz": self.imgsz,
            "opset": self.opset,
            "dynamic": self.dynamic,
            "simplify": self.simplify,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ExportConfig":
        return cls(
            weights_path=data.get("weights_path", ""),
            format=data.get("format", "onnx"),
            output_dir=data.get("output_dir", ""),
            imgsz=data.get("imgsz", 640),
            opset=data.get("opset", 12),
            dynamic=data.get("dynamic", False),
            simplify=data.get("simplify", True),
        )