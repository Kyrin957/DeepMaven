"""全局常量定义。"""

from pathlib import Path

from src.utils.tasks import project_types, task_spec

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
# 容器格式版本号（v2：项目新增 backend 字段；v1 项目仍可打开，见 project_format）
PROJECT_VERSION = 2
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
# 导航栏展开宽度按最长标题自适应：条目所需宽度之外另留的余量
# （面板左右各 5px 内边距 + 条目右侧呼吸空间）
NAV_WIDTH_MARGIN = 22
# 导航栏展开宽度下限（标题较短时不至于过窄）
NAV_MIN_WIDTH = 132

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

# 任务类型 → YOLO 权重名后缀（拼出 yolo11n / yolo11n-seg / yolo11n-cls / yolo11n-obb）
# 任务清单与后缀声明在任务注册表（src/utils/tasks.py），此处派生供旧调用点使用
TASK_MODEL_SUFFIX = {
    spec["key"]: spec["model_suffix"]
    for spec in project_types() if spec["model_suffix"]
}

# ---------------------------------------------------------------
# 项目类型（深度学习任务）——定义在任务注册表 src/utils/tasks.py
# 每个类型的能力（标注方式 / 模型后缀 / 支持状态 / 数据布局 / 指标 /
# 可用后端 / 所需导航页）由 tasks.TaskSpec 声明，此处派生旧结构供界面读取
# ---------------------------------------------------------------
PROJECT_TYPES = project_types()

# 项目类型 → 标注模式（供标注页限定可用工具）
PROJECT_ANNOTATION = {
    item["key"]: item["annotation"] for item in PROJECT_TYPES
}


def project_type(key: str) -> dict:
    """按 key 取项目类型定义（找不到时回退为对象检测）。"""
    return task_spec(key).to_dict()

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
    {"key": "pt", "label": "PyTorch (.pt)", "desc": "可继续训练"},
    {"key": "onnx", "label": "ONNX (.onnx)", "desc": "跨平台部署"},
    {"key": "torchscript", "label": "TorchScript (.torchscript)", "desc": "C++ 部署"},
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

# 图表配色（评估页的混淆矩阵 / 概率条等按类别顺序取色）
PLOT_COLORS = ["#0F6CBD", "#0F7B0F", "#C42B1C", "#B16CEA", "#E8A33D", "#4A5459"]

# 支持的图片扩展名
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
# 支持的标签扩展名
LABEL_EXTS = {".txt"}