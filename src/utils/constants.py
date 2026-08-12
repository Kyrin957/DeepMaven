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
]

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

# 支持的图片扩展名
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
# 支持的标签扩展名
LABEL_EXTS = {".txt"}