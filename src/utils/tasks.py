"""任务能力注册表（TaskSpec）。

一个「任务」= 一种项目类型（检测 / 旋转框 / 分割 / 分类 / 异常检测 /
语义分割 / OCR）。本表集中声明每个任务的能力，原先散落在
`constants`（项目类型、模型后缀）、`train_vm`（指标）、`evaluate_vm`
（任务列表）、`train_tab`（模型候选）、`dataset_vm`（数据布局）里的知识
统一收敛到这里，新增任务只需改本文件（见《开发文档.md》§8.2.1）。

各字段的第 1 期使用范围：
    annotation / model_suffix / supported / note  → 新建项目对话框、标注页
    metrics                                       → 训练页曲线与主指标
    backends / dataset_layout / eval_views        → 后端路由、数据布局（逐步接入）
    pages / export_formats / quick_fit            → 页面门控与导出（第 2 期接入）
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------
# 导航页 key（与 MainWindow 注册顺序一致）
# ---------------------------------------------------------------
PAGE_PROJECT = "project"
PAGE_GALLERY = "gallery"
PAGE_ANNOTATE = "annotate"
PAGE_REVIEW = "review"
PAGE_SPLIT = "split"
PAGE_TRAIN = "train"
PAGE_EVALUATE = "evaluate"
PAGE_EXPORT = "export"

PAGE_ORDER: tuple[str, ...] = (
    PAGE_PROJECT,
    PAGE_GALLERY,
    PAGE_ANNOTATE,
    PAGE_REVIEW,
    PAGE_SPLIT,
    PAGE_TRAIN,
    PAGE_EVALUATE,
    PAGE_EXPORT,
)
ALL_PAGES = PAGE_ORDER

# ---------------------------------------------------------------
# 标注方式（供标注页限定工具集）
# ---------------------------------------------------------------
ANNOTATION_NONE = "none"
ANNOTATION_BOX = "box"
ANNOTATION_OBB = "obb"
ANNOTATION_POLYGON = "polygon"
ANNOTATION_TEXT = "text"          # OCR：文本框 + 转写（第 3 期）

# ---------------------------------------------------------------
# 数据集落盘结构（DatasetService 的 layout）
# 第 1 期只有 detect / classify 两种实现，其余为第 3/4 期的目标值
# ---------------------------------------------------------------
LAYOUT_DETECT = "detect"
LAYOUT_CLASSIFY = "classify"
LAYOUT_MASK = "mask"                    # 语义分割：像素掩码 PNG（第 4 期）
LAYOUT_OCR = "ocr_det_rec"              # OCR：检测框 + 转写（第 3 期）
LAYOUT_ANOMALY = "anomaly_folder"       # 异常：normal / abnormal（第 4 期）

# ---------------------------------------------------------------
# 评估结果视图（评估页右栏结果卡片标识）
# ---------------------------------------------------------------
VIEW_DETECT = "detect"
VIEW_CLASSIFY = "classify"
VIEW_ANOMALY = "anomaly"
VIEW_SEGMENT = "segment"                # 语义分割：掩码对比与逐类 IoU
VIEW_OCR = "ocr"                        # 第 3 期

# ---------------------------------------------------------------
# 训练指标（曲线名, 候选键名）——自 train_vm 迁入
# ---------------------------------------------------------------
DETECT_METRICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("mAP50", ("metrics/mAP50(B)", "metrics/mAP50")),
    ("mAP50-95", ("metrics/mAP50-95(B)", "metrics/mAP50-95")),
    ("Precision", ("metrics/precision(B)", "metrics/precision")),
    ("Recall", ("metrics/recall(B)", "metrics/recall")),
)
CLASSIFY_METRICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Top1 准确率", ("metrics/accuracy_top1",)),
    ("Top5 准确率", ("metrics/accuracy_top5",)),
)
ANOMALY_METRICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("AUROC", ("image_AUROC", "image_AUROC_macro", "pixel_AUROC")),
)
# 语义分割：逐像素指标（worker 产出的键名）
SEMANTIC_METRICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("mIoU", ("metrics/mIoU", "miou")),
    ("Dice", ("metrics/Dice", "dice")),
)
# OCR：识别质量用字符错误率与精确匹配率（训练日志里的键名由 worker 产出）
OCR_METRICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("字符错误率", ("cer",)),
    ("精确匹配率", ("exact_match",)),
)

# 默认导出格式（与 constants.EXPORT_FORMATS 的 key 一致）
DEFAULT_EXPORT_FORMATS: tuple[str, ...] = ("pt", "onnx", "torchscript")


@dataclass(frozen=True)
class TaskSpec:
    """一个任务（项目类型）的能力声明。"""

    key: str
    label: str
    short: str = ""
    detail: str = ""
    note: str = ""
    annotation: str = ANNOTATION_NONE
    model_suffix: str = ""
    supported: bool = True
    backends: tuple[str, ...] = ("yolo",)
    dataset_layout: str = LAYOUT_DETECT
    metrics: tuple[tuple[str, tuple[str, ...]], ...] = DETECT_METRICS
    eval_views: tuple[str, ...] = (VIEW_DETECT,)
    export_formats: tuple[str, ...] = DEFAULT_EXPORT_FORMATS
    pages: tuple[str, ...] = ALL_PAGES
    quick_fit: bool = False

    def to_dict(self) -> dict:
        """旧 `constants.PROJECT_TYPES` 结构（新建项目对话框等按此读取）。"""
        return {
            "key": self.key,
            "label": self.label,
            "short": self.short,
            "detail": self.detail,
            "annotation": self.annotation,
            "model_suffix": self.model_suffix,
            "supported": self.supported,
            "note": self.note,
        }


# ---------------------------------------------------------------
# 任务清单（顺序即新建项目对话框的卡片顺序）
# ---------------------------------------------------------------
# 已实现的两个后端 key（见 services/backends）
BACKEND_YOLO = "yolo"
BACKEND_ANOMALIB = "anomalib"
BACKEND_UNET = "unet"
BACKEND_OCR = "ocr"

_TASKS: tuple[TaskSpec, ...] = (
    TaskSpec(
        key="classify",
        label="分类",
        short="整图归类",
        detail="整张图像归入一个类别",
        note="无需框选，类别由目录决定",
        annotation=ANNOTATION_NONE,
        model_suffix="-cls",
        backends=(BACKEND_YOLO,),
        dataset_layout=LAYOUT_CLASSIFY,
        metrics=CLASSIFY_METRICS,
        eval_views=(VIEW_CLASSIFY,),
    ),
    TaskSpec(
        key="anomaly",
        label="异常检测",
        short="仅用正常样本",
        detail="只用正常样本训练",
        note="建议 2GB 以上显存",
        annotation=ANNOTATION_NONE,
        model_suffix="",
        backends=(BACKEND_ANOMALIB,),
        # 第 4 期改为 LAYOUT_ANOMALY：拆分页目前仍按检测结构落盘
        dataset_layout=LAYOUT_DETECT,
        metrics=ANOMALY_METRICS,
        eval_views=(VIEW_ANOMALY,),
        export_formats=(),                 # 第 2 期：backbone + 记忆库「模型包」
        quick_fit=True,
    ),
    TaskSpec(
        key="detect",
        label="对象检测",
        short="轴对齐矩形",
        detail="水平矩形框标出缺陷",
        note="标注成本低",
        annotation=ANNOTATION_BOX,
        model_suffix="",
        backends=(BACKEND_YOLO,),
        dataset_layout=LAYOUT_DETECT,
        metrics=DETECT_METRICS,
        eval_views=(VIEW_DETECT,),
    ),
    TaskSpec(
        key="obb",
        label="对象检测·旋转框",
        short="带角度矩形",
        detail="带旋转角度的矩形框",
        note="拖出矩形后用「角度」微调",
        annotation=ANNOTATION_OBB,
        model_suffix="-obb",
        backends=(BACKEND_YOLO,),
        dataset_layout=LAYOUT_DETECT,
        metrics=DETECT_METRICS,
        eval_views=(VIEW_DETECT,),
    ),
    TaskSpec(
        key="segment",
        label="实例分割",
        short="多边形轮廓",
        detail="多边形勾出缺陷轮廓",
        note="标注成本较高",
        annotation=ANNOTATION_POLYGON,
        model_suffix="-seg",
        backends=(BACKEND_YOLO,),
        dataset_layout=LAYOUT_DETECT,
        metrics=DETECT_METRICS,
        eval_views=(VIEW_DETECT,),
    ),
    TaskSpec(
        key="semantic",
        label="语义分割",
        short="逐像素分类",
        detail="逐像素分类",
        note="轻量 U-Net，CPU 可训练",
        annotation=ANNOTATION_POLYGON,
        model_suffix="",
        supported=True,
        backends=(BACKEND_UNET,),
        dataset_layout=LAYOUT_MASK,
        metrics=SEMANTIC_METRICS,
        eval_views=(VIEW_SEGMENT,),
    ),
    TaskSpec(
        key="ocr",
        label="Deep OCR",
        short="字符识别",
        detail="识别图中字符",
        note="需要 PaddleOCR 环境",
        annotation=ANNOTATION_TEXT,
        model_suffix="",
        supported=True,
        backends=(BACKEND_OCR,),
        dataset_layout=LAYOUT_OCR,
        metrics=OCR_METRICS,
        eval_views=(VIEW_OCR,),
    ),
)

TASK_SPECS: dict[str, TaskSpec] = {spec.key: spec for spec in _TASKS}

# 默认任务（未知类型回退）
DEFAULT_TASK = "detect"


# ---------------------------------------------------------------
# 查询
# ---------------------------------------------------------------
def task_spec(key: str) -> TaskSpec:
    """按 key 取任务定义（找不到时回退为对象检测）。"""
    return TASK_SPECS.get(str(key), TASK_SPECS[DEFAULT_TASK])


def project_types() -> list[dict]:
    """全部任务（旧 `PROJECT_TYPES` 结构，顺序固定）。"""
    return [spec.to_dict() for spec in _TASKS]


def task_keys() -> list[str]:
    return [spec.key for spec in _TASKS]


def task_of(key: str) -> str:
    """项目类型 → 训练任务：未就绪的类型（如语义分割 / OCR）回退为对象检测。"""
    spec = TASK_SPECS.get(str(key))
    if spec is None or not spec.supported:
        return DEFAULT_TASK
    return spec.key


def annotation_mode(key: str) -> str:
    return task_spec(key).annotation


def dataset_layout(key: str) -> str:
    return task_spec(key).dataset_layout


def task_metrics(key: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """任务的训练指标（无专属指标时回退检测指标）。"""
    spec = TASK_SPECS.get(str(key))
    if spec is None or not spec.metrics:
        return DETECT_METRICS
    return spec.metrics


def backend_candidates(key: str) -> tuple[str, ...]:
    """任务可用的后端 key（按优先级）。"""
    return task_spec(key).backends


def eval_views(key: str) -> tuple[str, ...]:
    """任务的评估结果视图（结果卡片标识，界面据此决定显示哪些卡片）。"""
    return task_spec(key).eval_views


def default_backend_for(key: str) -> str:
    """任务的默认后端（旧项目没有 backend 字段时据此补齐）。"""
    candidates = backend_candidates(key)
    return candidates[0] if candidates else BACKEND_YOLO


def is_quick_fit(key: str) -> bool:
    """是否为「快速拟合」任务（异常检测的记忆库构建，非长时间训练）。"""
    return task_spec(key).quick_fit
