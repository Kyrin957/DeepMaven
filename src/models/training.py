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
        }


@dataclass
class EvaluationConfig:
    """模型评估（推理）参数。"""

    weights_path: str = ""             # 待加载的模型权重
    source_type: str = "image"         # image / video / camera
    confidence: float = 0.25           # 置信度阈值
    iou: float = 0.45                  # IOU 阈值
    device: str = "auto"


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