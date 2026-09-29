"""对话框组件。"""

from .class_edit_dialog import ClassEditDialog, ColorPicker
from .delete_project_dialog import DeleteProjectDialog
from .import_images_dialog import ImportImagesDialog
from .new_project_dialog import NewProjectDialog
from .ood_dialog import OodDialog
from .preview_dialog import ImagePreviewDialog
from .tag_edit_dialog import TagEditDialog

__all__ = [
    "ClassEditDialog",
    "ColorPicker",
    "DeleteProjectDialog",
    "ImagePreviewDialog",
    "ImportImagesDialog",
    "OodDialog",
    "NewProjectDialog",
    "TagEditDialog",
]
