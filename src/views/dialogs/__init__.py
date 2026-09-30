"""对话框组件。"""

from .backup_dialog import BackupVersionDialog, ask_backup_version
from .class_edit_dialog import ClassEditDialog, ColorPicker
from .delete_project_dialog import DeleteProjectDialog
from .import_images_dialog import ImportImagesDialog
from .new_project_dialog import NewProjectDialog
from .ood_dialog import OodDialog
from .preview_dialog import ImagePreviewDialog
from .split_edit_dialog import (
    CANCEL,
    CREATE_APPLY,
    MODE_CREATE,
    MODE_EDIT,
    SAVE_APPLY,
    SplitEditDialog,
)
from .tag_edit_dialog import TagEditDialog

__all__ = [
    "BackupVersionDialog",
    "ask_backup_version",
    "ClassEditDialog",
    "ColorPicker",
    "DeleteProjectDialog",
    "ImagePreviewDialog",
    "ImportImagesDialog",
    "OodDialog",
    "NewProjectDialog",
    "SplitEditDialog",
    "TagEditDialog",
    "CANCEL",
    "CREATE_APPLY",
    "MODE_CREATE",
    "MODE_EDIT",
    "SAVE_APPLY",
]
