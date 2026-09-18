"""全局常量定义。"""

from pathlib import Path

# ---------------------------------------------------------------
# 应用信息
# ---------------------------------------------------------------
APP_NAME = "DeepMaven"
APP_VERSION = "0.1.0"
ORG_NAME = "DeepMaven"

# 项目根目录（src/ 的上级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
# 数据目录（缓存、日志等本地产物）
DATA_DIR = PROJECT_ROOT / "data"
# 运行产物（训练结果、导出模型等）
RUNS_DIR = PROJECT_ROOT / "runs"

# ---------------------------------------------------------------
# 单文件项目 (.mprj)
# ---------------------------------------------------------------
# 自定义文件头魔数（确保通用解压/识别工具无法直接打开）
PROJECT_MAGIC = b"DMJPRJ"
# 容器格式版本号
PROJECT_VERSION = 1
# 支持的扩展名
PROJECT_EXTS = {".mprj"}
# 文件对话框过滤器
PROJECT_FILE_FILTER = "DeepMaven 项目 (*.mprj)"
# 项目内封面缩略图的归档名（用于最近项目卡片）
COVER_ENTRY_NAME = "cover.jpg"

# ---------------------------------------------------------------
# 界面
# ---------------------------------------------------------------
# 主题色（品牌蓝，Fluent Design 风格）
BRAND_COLOR = "#005FB8"
# 缩放比例（QFluentWidgets 布局缩放）
WINDOW_MIN_WIDTH = 1080
WINDOW_MIN_HEIGHT = 680

# ---------------------------------------------------------------
# 模型
# ---------------------------------------------------------------
# 支持的 YOLO 模型变体（名称、说明、默认 imgsz）
# 参考开发文档：YOLOv11 / YOLOv26 的 n/s/m/l/x 变体
YOLO_MODEL_VARIANTS = [
    {"key": "yolo11n", "label": "YOLO11n", "desc": "轻量，速度最快", "imgsz": 640},
    {"key": "yolo11s", "label": "YOLO11s", "desc": "均衡，通用首选", "imgsz": 640},
    {"key": "yolo11m", "label": "YOLO11m", "desc": "更高精度", "imgsz": 640},
    {"key": "yolo11l", "label": "YOLO11l", "desc": "高精度", "imgsz": 640},
    {"key": "yolo11x", "label": "YOLO11x", "desc": "最高精度", "imgsz": 640},
    {"key": "yolo26n", "label": "YOLO26n", "desc": "新一代轻量变体", "imgsz": 640},
    {"key": "yolo26s", "label": "YOLO26s", "desc": "新一代均衡变体", "imgsz": 640},
    {"key": "yolo26m", "label": "YOLO26m", "desc": "新一代高精度", "imgsz": 640},
    {"key": "yolo26l", "label": "YOLO26l", "desc": "新一代高精度", "imgsz": 640},
    {"key": "yolo26x", "label": "YOLO26x", "desc": "新一代最高精度", "imgsz": 640},
]

# 任务类型
TASK_TYPES = [
    {"key": "detect", "label": "目标检测 (Detect)"},
    {"key": "segment", "label": "实例分割 (Segment)"},
    {"key": "classify", "label": "图像分类 (Classify)"},
    {"key": "anomaly", "label": "异常检测 (Anomaly)"},
]

# 任务类型 → YOLO 权重名后缀（拼出 yolo11n / yolo11n-seg / yolo11n-cls / yolo11n-obb）
TASK_MODEL_SUFFIX = {
    "detect": "",
    "segment": "-seg",
    "classify": "-cls",
    "obb": "-obb",
}

# ---------------------------------------------------------------
# 项目类型（深度学习任务）——参照 Halcon DLT 的「深度学习方法」
# 每个类型决定：标注方式、模型后缀、数据图表与训练任务
#     annotation: none / box / obb / polygon
#     supported : 当前后端（Ultralytics）是否可直接训练
# ---------------------------------------------------------------
PROJECT_TYPES = [
    {
        "key": "classify",
        "label": "分类",
        "short": "整图归类",
        "detail": "判断整张图像属于哪一类（如合格 / 划痕 / 异物）。",
        "annotation": "none",
        "model_suffix": "-cls",
        "supported": True,
        "note": "分类不需要框选标注，类别由图像所在目录决定。",
    },
    {
        "key": "anomaly",
        "label": "异常检测",
        "short": "仅用正常样本",
        "detail": "只用正常样本学习「正常」的样子，输出异常分数与热力图。",
        "annotation": "none",
        "model_suffix": "",
        "supported": True,
        "note": "可发现未知缺陷；建议配备独立显卡（≥2GB 显存）。",
    },
    {
        "key": "detect",
        "label": "对象检测",
        "short": "轴对齐矩形",
        "detail": "用水平矩形框标出每个缺陷的位置与类别。",
        "annotation": "box",
        "model_suffix": "",
        "supported": True,
        "note": "最常用的缺陷检测方式，标注成本低。",
    },
    {
        "key": "obb",
        "label": "对象检测·旋转框",
        "short": "带角度矩形",
        "detail": "用带旋转角度的矩形框标注，适合细长或倾斜的缺陷。",
        "annotation": "obb",
        "model_suffix": "-obb",
        "supported": True,
        "note": "标注时先拖出矩形，再用「角度」微调旋转。",
    },
    {
        "key": "segment",
        "label": "实例分割",
        "short": "多边形轮廓",
        "detail": "用多边形勾出缺陷的精确轮廓，可统计面积与形状。",
        "annotation": "polygon",
        "model_suffix": "-seg",
        "supported": True,
        "note": "标注成本较高，适合需要面积/形状分析的场景。",
    },
    {
        "key": "semantic",
        "label": "语义分割",
        "short": "逐像素分类",
        "detail": "把每个像素归入某个类别，输出整幅分割掩码。",
        "annotation": "polygon",
        "model_suffix": "",
        "supported": False,
        "note": "当前后端（Ultralytics）未直接提供该任务，规划中。",
    },
    {
        "key": "ocr",
        "label": "Deep OCR",
        "short": "字符识别",
        "detail": "识别图像中的字符，如喷码、批号、标签文字。",
        "annotation": "none",
        "model_suffix": "",
        "supported": False,
        "note": "需要独立的 OCR 引擎，规划中。",
    },
]

# 项目类型 → 标注模式（供标注页限定可用工具）
PROJECT_ANNOTATION = {
    item["key"]: item["annotation"] for item in PROJECT_TYPES
}


def project_type(key: str) -> dict:
    """按 key 取项目类型定义（找不到时回退为对象检测）。"""
    for item in PROJECT_TYPES:
        if item["key"] == key:
            return item
    return PROJECT_TYPES[2]

# 异常检测（Anomalib）数据目录约定
ANOMALY_NORMAL_DIR = "normal"
ANOMALY_ABNORMAL_DIR = "abnormal"

# 优化器
OPTIMIZERS = ["auto", "SGD", "Adam", "AdamW", "RMSProp"]

# 划分比例（训练/验证/测试）
DEFAULT_SPLIT = (0.7, 0.2, 0.1)

# ---------------------------------------------------------------
# 导出
# ---------------------------------------------------------------
# 支持的导出格式
EXPORT_FORMATS = [
    {"key": "pt", "label": "PyTorch (.pt)", "desc": "原生训练格式，可继续训练/推理"},
    {"key": "onnx", "label": "ONNX (.onnx)", "desc": "跨平台部署，OpenCV/DNN 支持"},
    {"key": "torchscript", "label": "TorchScript (.torchscript)", "desc": "C++ 部署友好"},
]

# ---------------------------------------------------------------
# 数据集 YOLO 结构子目录
# ---------------------------------------------------------------
DATASET_DIRS = ["images", "labels"]
SPLIT_SUBDIRS = ["train", "val", "test"]

# 划分产物（Ultralytics 数据集目录）的默认目录名
DEFAULT_SPLIT_NAME = "dataset"
# 数据增强产物目录名
DEFAULT_AUGMENT_NAME = "augment"
# 未标注图片在类别统计中的显示名
UNLABELED_LABEL = "未标注"

# 数据集划分结果的显示配色（与 DLT 的三色区分保持一致）
SPLIT_COLORS = {
    "train": "#0F6CBD",
    "val": "#0F7B0F",
    "test": "#C42B1C",
}
SPLIT_LABELS = {"train": "训练", "val": "验证", "test": "测试"}

# 支持的图片扩展名
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
# 支持的标签扩展名
LABEL_EXTS = {".txt"}