"""Service 层 —— 外部服务封装。

Service 层不依赖任何 UI 组件，仅依赖 Model 层与第三方库。
重的深度学习依赖（torch / ultralytics / anomalib）采用方法内懒加载，
避免拖慢应用启动。
"""

from .yolo_service import YOLOService
from .dataset_service import DatasetService
from .project_service import ProjectService
from .export_service import ExportService
from .category_service import CategoryService
from .quality_service import QualityService
from .annotation_service import AnnotationService
from .autolabel_service import AutoLabelService
from .augment_service import AugmentConfig, AugmentService
from .train_service import TrainService
from .inference_service import InferenceService
from .report_service import ReportService
from .anomalib_service import AnomalibService

__all__ = [
    "YOLOService",
    "DatasetService",
    "ProjectService",
    "ExportService",
    "CategoryService",
    "QualityService",
    "AnnotationService",
    "AutoLabelService",
    "AugmentService",
    "AugmentConfig",
    "TrainService",
    "InferenceService",
    "ReportService",
    "AnomalibService",
]
