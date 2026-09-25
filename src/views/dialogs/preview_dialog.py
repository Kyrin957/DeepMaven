"""通用图片预览弹窗：训练参数预览、评估可视化等复用。

只做「把一张图按窗口缩放显示 + 一段说明」，避免各处重复实现。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, MessageBoxBase, SubtitleLabel

from src.views.ui import tokens as T


class ImagePreviewDialog(MessageBoxBase):
    """图片预览弹窗。

    用法：
        ImagePreviewDialog(parent, r".\\a.jpg", "训练参数预览", "imgsz 640").exec()
    """

    def __init__(
        self,
        parent=None,
        image: str = "",
        title: str = "预览",
        hint: str = "",
        width: int = 760,
        height: int = 460,
    ):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel(title, self)
        self.viewLayout.addWidget(self.titleLabel)

        holder = QWidget(self)
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(T.SPACE_SM)

        self.view = QLabel(holder)
        self.view.setMinimumSize(width, height)
        self.view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.view.setStyleSheet(
            f"background:{T.CANVAS_BG}; border-radius:{T.RADIUS_SM}px;"
        )
        layout.addWidget(self.view)

        self.hint = CaptionLabel(hint or "", holder)
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.viewLayout.addWidget(holder)

        self.widget.setMinimumWidth(width + 40)
        self.cancelButton.setVisible(False)
        self.yesButton.setText("关闭")

        self._pixmap = QPixmap(str(image)) if image else QPixmap()
        self._width, self._height = width, height
        self._refresh()

    def set_image(self, image: str, hint: str = "") -> None:
        """切换显示的图片。"""
        self._pixmap = QPixmap(str(image)) if image else QPixmap()
        if hint:
            self.hint.setText(hint)
        self._refresh()

    def _refresh(self) -> None:
        if self._pixmap.isNull():
            self.view.setText("无法加载预览图")
            return
        self.view.setText("")
        self.view.setPixmap(self._pixmap.scaled(
            self._width, self._height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))
