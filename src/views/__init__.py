"""View 层 —— Fluent Design 风格界面组件。"""

from .project_tab import ProjectTab
from .data_tab import DataTab
from .annotate_tab import AnnotateTab
from .model_tab import ModelTab
from .train_tab import TrainTab
from .evaluate_tab import EvaluateTab
from .export_tab import ExportTab

__all__ = [
    "ProjectTab",
    "DataTab",
    "AnnotateTab",
    "ModelTab",
    "TrainTab",
    "EvaluateTab",
    "ExportTab",
]
