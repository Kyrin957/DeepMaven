"""Service 层 —— 外部服务封装。

Service 层不依赖任何 UI 组件，仅依赖 Model 层与第三方库。
重的深度学习依赖（torch / ultralytics）采用方法内懒加载，避免拖慢应用启动。
"""

from .yolo_service import YOLOService
from .dataset_service import DatasetService
from .project_service import ProjectService
from .export_service import ExportService

__all__ = [
    "YOLOService",
    "DatasetService",
    "ProjectService",
    "ExportService",
]