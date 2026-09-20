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
    split_id: int = 0                  # 使用的数据拆分 id（项目内多套拆分）
    split_name: str = ""               # 拆分名称（展示 / 记录用）
    epochs: int = 100
    batch: int = 16
    lr: float = 0.01                   # 初始学习率
    imgsz: int = 640
    optimizer: str = "auto"
    weight_decay: float = 0.0005       # 权重衰减（Halcon DLT 的「权重先验」）
    momentum: float = 0.937            # SGD 动量
    warmup_epochs: float = 3.0         # 学习率预热轮数
    patience: int = 50                 # 早停耐心值，0 = 不早停
    cos_lr: bool = False               # 余弦学习率调度
    deterministic: bool = True         # 使用确定性算法
    close_mosaic: int = 10             # 末 N 轮关闭马赛克
    val: bool = True                   # 每轮验证
    cache: bool = False                # 缓存图片（速度换内存）
    single_cls: bool = False           # 全部视为单一类别
    rect: bool = False                 # 矩形训练（检测任务加速）
    dropout: float = 0.0               # 分类任务的 dropout
    device: str = "auto"               # auto / cpu / cuda:0 ...
    workers: int = 4
    seed: int = 0
    resume: bool = False               # 断点续训
    project_dir: str = ""              # 训练结果保存目录

    # 在线数据增强（Ultralytics 训练期增强）
    augment: bool = True               # 启用图像增强
    hflip: float = 0.5                 # 水平翻转概率
    vflip: float = 0.0                 # 垂直翻转概率
    degrees: float = 0.0               # 旋转角度范围
    scale: float = 0.5                 # 缩放幅度
    translate: float = 0.1             # 平移幅度
    hsv_h: float = 0.015               # 色调变化
    hsv_s: float = 0.7                 # 饱和度变化
    hsv_v: float = 0.4                 # 亮度变化
    mosaic: float = 1.0                # 马赛克概率
    mixup: float = 0.0                 # 混合概率
    # 类别权重（参考值：按训练集频次平衡，见训练页「类别权重」卡片）
    class_weights: dict[str, float] = field(default_factory=dict)

    # 异常检测（Anomalib）参数
    anomaly_root: str = ""             # 数据集根目录（含 normal/、abnormal/）
    anomaly_normal_dir: str = "normal"
    anomaly_abnormal_dir: str = "abnormal"
    anomaly_pretrained: bool = True    # 是否加载骨干预训练权重（离线可关闭）

    # 训练中实时状态（非用户配置，由 ViewModel/Service 更新）
    status: str = "idle"               # idle / running / paused / finished / error
    progress: float = 0.0              # 0 ~ 1
    current_epoch: int = 0
    iteration: int = 0                 # 已完成的迭代次数
    iterations: int = 0                # 总迭代次数
    lr_now: float = 0.0                # 当前学习率
    elapsed: float = 0.0               # 已用时（秒）
    eta: float = 0.0                   # 预计剩余（秒）
    best_value: float = 0.0            # 最佳验证指标
    best_epoch: int = 0                # 最佳指标出现的轮次
    save_dir: str = ""                 # 本次训练产物目录
    loss: float = 0.0
    val_loss: float = 0.0
    mAP50: float = 0.0
    precision: float = 0.0
    recall: float = 0.0
    # 历次训练记录（每完成一次追加一条，供训练页回看）
    history: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "task_type": self.task_type,
            "model_key": self.model_key,
            "weights_path": self.weights_path,
            "data_yaml": self.data_yaml,
            "split_id": self.split_id,
            "split_name": self.split_name,
            "epochs": self.epochs,
            "batch": self.batch,
            "lr": self.lr,
            "imgsz": self.imgsz,
            "optimizer": self.optimizer,
            "weight_decay": self.weight_decay,
            "momentum": self.momentum,
            "warmup_epochs": self.warmup_epochs,
            "patience": self.patience,
            "cos_lr": self.cos_lr,
            "deterministic": self.deterministic,
            "close_mosaic": self.close_mosaic,
            "val": self.val,
            "cache": self.cache,
            "single_cls": self.single_cls,
            "rect": self.rect,
            "dropout": self.dropout,
            "device": self.device,
            "workers": self.workers,
            "seed": self.seed,
            "resume": self.resume,
            "project_dir": self.project_dir,
            "augment": self.augment,
            "hflip": self.hflip,
            "vflip": self.vflip,
            "degrees": self.degrees,
            "scale": self.scale,
            "translate": self.translate,
            "hsv_h": self.hsv_h,
            "hsv_s": self.hsv_s,
            "hsv_v": self.hsv_v,
            "mosaic": self.mosaic,
            "mixup": self.mixup,
            "class_weights": dict(self.class_weights),
            "anomaly_root": self.anomaly_root,
            "anomaly_normal_dir": self.anomaly_normal_dir,
            "anomaly_abnormal_dir": self.anomaly_abnormal_dir,
            "anomaly_pretrained": self.anomaly_pretrained,
            "status": self.status,
            "progress": self.progress,
            "current_epoch": self.current_epoch,
            "iteration": self.iteration,
            "iterations": self.iterations,
            "lr_now": self.lr_now,
            "elapsed": self.elapsed,
            "eta": self.eta,
            "best_value": self.best_value,
            "best_epoch": self.best_epoch,
            "save_dir": self.save_dir,
            "loss": self.loss,
            "val_loss": self.val_loss,
            "mAP50": self.mAP50,
            "precision": self.precision,
            "recall": self.recall,
            "history": self.history,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TrainingConfig":
        return cls(
            task_type=data.get("task_type", "detect"),
            model_key=data.get("model_key", "yolo11n"),
            weights_path=data.get("weights_path", ""),
            data_yaml=data.get("data_yaml", ""),
            split_id=data.get("split_id", 0),
            split_name=data.get("split_name", ""),
            epochs=data.get("epochs", 100),
            batch=data.get("batch", 16),
            lr=data.get("lr", 0.01),
            imgsz=data.get("imgsz", 640),
            optimizer=data.get("optimizer", "auto"),
            weight_decay=data.get("weight_decay", data.get("decay", 0.0005)),
            momentum=data.get("momentum", 0.937),
            warmup_epochs=data.get("warmup_epochs", 3.0),
            patience=data.get("patience", 50),
            cos_lr=data.get("cos_lr", False),
            deterministic=data.get("deterministic", True),
            close_mosaic=data.get("close_mosaic", 10),
            val=data.get("val", True),
            cache=data.get("cache", False),
            single_cls=data.get("single_cls", False),
            rect=data.get("rect", False),
            dropout=data.get("dropout", 0.0),
            device=data.get("device", "auto"),
            workers=data.get("workers", 4),
            seed=data.get("seed", 0),
            resume=data.get("resume", False),
            project_dir=data.get("project_dir", ""),
            augment=data.get("augment", True),
            hflip=data.get("hflip", 0.5),
            vflip=data.get("vflip", 0.0),
            degrees=data.get("degrees", 0.0),
            scale=data.get("scale", 0.5),
            translate=data.get("translate", 0.1),
            hsv_h=data.get("hsv_h", 0.015),
            hsv_s=data.get("hsv_s", 0.7),
            hsv_v=data.get("hsv_v", 0.4),
            mosaic=data.get("mosaic", 1.0),
            mixup=data.get("mixup", 0.0),
            class_weights=dict(data.get("class_weights", {}) or {}),
            anomaly_root=data.get("anomaly_root", ""),
            anomaly_normal_dir=data.get("anomaly_normal_dir", "normal"),
            anomaly_abnormal_dir=data.get("anomaly_abnormal_dir", "abnormal"),
            anomaly_pretrained=data.get("anomaly_pretrained", True),
            status=data.get("status", "idle"),
            progress=data.get("progress", 0.0),
            current_epoch=data.get("current_epoch", 0),
            iteration=data.get("iteration", 0),
            iterations=data.get("iterations", 0),
            lr_now=data.get("lr_now", 0.0),
            elapsed=data.get("elapsed", 0.0),
            eta=data.get("eta", 0.0),
            best_value=data.get("best_value", 0.0),
            best_epoch=data.get("best_epoch", 0),
            save_dir=data.get("save_dir", ""),
            loss=data.get("loss", 0.0),
            val_loss=data.get("val_loss", 0.0),
            mAP50=data.get("mAP50", 0.0),
            precision=data.get("precision", 0.0),
            recall=data.get("recall", 0.0),
            history=list(data.get("history", []) or []),
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
    # 数据集评估（带真实标签，统计指标与混淆矩阵）
    eval_split_id: int = 0             # 评估使用的数据拆分
    eval_split_name: str = ""
    eval_subset: str = "val"           # train / val / test
    eval_folder: str = ""              # 自定义评估目录（子目录名 = 真实类别）
    max_images: int = 200              # 单次评估的图片上限
    # 多子集选择 + 随机抽样（对齐 DLT 的评估图像集多选与「限制数量」）
    eval_subsets: list[str] = field(default_factory=list)   # train / val / test 可多选
    eval_random: bool = False          # 上限内随机抽样（固定种子可复现）
    eval_seed: int = 0
    eval_sort: str = "order"           # 结果排序：order / conf_desc / conf_asc / iou_asc
    # 异常检测后处理（对齐 DLT 的分类阈值与分数容忍度）
    anomaly_threshold: float = 0.0     # 0 表示用「中位阈值」
    anomaly_tolerance: float = 0.0     # 分数容忍度 0~0.5（抬高判定门槛）
    anomaly_min_size: int = 0          # 最小缺陷尺寸（像素），0 = 不过滤

    def to_dict(self) -> dict:
        return {
            "weights_path": self.weights_path,
            "source_type": self.source_type,
            "source_path": self.source_path,
            "confidence": self.confidence,
            "iou": self.iou,
            "device": self.device,
            "anomaly_model": self.anomaly_model,
            "eval_split_id": self.eval_split_id,
            "eval_split_name": self.eval_split_name,
            "eval_subset": self.eval_subset,
            "eval_folder": self.eval_folder,
            "max_images": self.max_images,
            "eval_subsets": list(self.eval_subsets),
            "eval_random": self.eval_random,
            "eval_seed": self.eval_seed,
            "eval_sort": self.eval_sort,
            "anomaly_threshold": self.anomaly_threshold,
            "anomaly_tolerance": self.anomaly_tolerance,
            "anomaly_min_size": self.anomaly_min_size,
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
            eval_split_id=data.get("eval_split_id", 0),
            eval_split_name=data.get("eval_split_name", ""),
            eval_subset=data.get("eval_subset", "val"),
            eval_folder=data.get("eval_folder", ""),
            max_images=data.get("max_images", 200),
            eval_subsets=list(data.get("eval_subsets", []) or []),
            eval_random=data.get("eval_random", False),
            eval_seed=data.get("eval_seed", 0),
            eval_sort=data.get("eval_sort", "order"),
            anomaly_threshold=data.get("anomaly_threshold", 0.0),
            anomaly_tolerance=data.get("anomaly_tolerance", 0.0),
            anomaly_min_size=data.get("anomaly_min_size", 0),
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
    # 优化方向（对应界面上的两个勾选项，见 ExportConfig.apply_options）
    for_inference: bool = True         # 针对推断优化：固定尺寸 + 图精简
    for_api: bool = False              # 针对 API 优化：动态尺寸，便于服务端变尺寸输入
    half: bool = False                 # 半精度（FP16）导出：体积更小，需 GPU
    # 历次导出记录（时间 / 格式 / 路径 / 大小）
    history: list[dict] = field(default_factory=list)

    def apply_options(self) -> None:
        """把「优化方向」两个勾选换算成导出器实际使用的参数。"""
        self.dynamic = bool(self.for_api)         # API 优化 → 动态尺寸
        self.simplify = bool(self.for_inference)   # 推断优化 → 图精简

    def to_dict(self) -> dict:
        return {
            "weights_path": self.weights_path,
            "format": self.format,
            "output_dir": self.output_dir,
            "imgsz": self.imgsz,
            "opset": self.opset,
            "dynamic": self.dynamic,
            "simplify": self.simplify,
            "for_inference": self.for_inference,
            "for_api": self.for_api,
            "half": self.half,
            "history": self.history,
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
            for_inference=data.get("for_inference", True),
            for_api=data.get("for_api", False),
            half=data.get("half", False),
            history=list(data.get("history", []) or []),
        )