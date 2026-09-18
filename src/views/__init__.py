"""View 层 —— Fluent Design 风格界面组件。"""

from .annotate_tab import AnnotateTab
from .evaluate_tab import EvaluateTab
from .export_tab import ExportTab
from .gallery_tab import GalleryTab
from .model_tab import ModelTab
from .project_tab import ProjectTab
from .review_tab import ReviewTab
from .split_tab import SplitTab
from .train_tab import TrainTab

__all__ = [
    "AnnotateTab",
    "EvaluateTab",
    "ExportTab",
    "GalleryTab",
    "ModelTab",
    "ProjectTab",
    "ReviewTab",
    "SplitTab",
    "TrainTab",
]
