"""缩略图网格：用于标注页的图片导航。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import QListView, QListWidget, QListWidgetItem

_PATH_ROLE = Qt.ItemDataRole.UserRole
_ANNOTATED_COLOR = "#005FB8"
_PLAIN_COLOR = "#8A8A8A"


class ThumbnailGrid(QListWidget):
    """以图标网格展示图片，标记当前项与已标注状态。"""

    imageActivated = Signal(int)         # 选中项索引

    THUMB_SIZE = 96

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setIconSize(QSize(self.THUMB_SIZE, self.THUMB_SIZE))
        self.setGridSize(QSize(self.THUMB_SIZE + 16, self.THUMB_SIZE + 34))
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self.setUniformItemSizes(True)
        self.setWordWrap(True)
        self.setSpacing(4)
        self.currentRowChanged.connect(self._on_row_changed)

    # -----------------------------------------------------------
    # 数据
    # -----------------------------------------------------------
    def set_images(self, paths: list) -> None:
        """重建缩略图列表。"""
        self.blockSignals(True)
        self.clear()
        for raw in paths:
            path = Path(raw)
            item = QListWidgetItem(self._icon(path), path.name)
            item.setData(_PATH_ROLE, str(path))
            item.setToolTip(str(path))
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            self.addItem(item)
        self.blockSignals(False)

    def set_annotated(self, flags: list) -> None:
        """按「是否已标注」着色并更新提示。"""
        for index in range(self.count()):
            item = self.item(index)
            done = bool(flags[index]) if index < len(flags) else False
            item.setForeground(QColor(_ANNOTATED_COLOR if done else _PLAIN_COLOR))
            path = item.data(_PATH_ROLE) or ""
            item.setToolTip(f"{path}\n{'已标注' if done else '未标注'}")

    def set_current_index(self, index: int) -> None:
        """外部同步当前项（不触发二次跳转）。"""
        if index < 0 or index >= self.count():
            return
        self.blockSignals(True)
        self.setCurrentRow(index)
        self.blockSignals(False)

    # -----------------------------------------------------------
    # 内部
    # -----------------------------------------------------------
    @staticmethod
    def _icon(path: Path) -> QIcon:
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            return QIcon()
        return QIcon(pixmap.scaled(
            ThumbnailGrid.THUMB_SIZE, ThumbnailGrid.THUMB_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))

    def _on_row_changed(self, row: int) -> None:
        if row >= 0:
            self.imageActivated.emit(row)
