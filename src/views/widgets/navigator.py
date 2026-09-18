"""图像导航器：整图缩略总览 + 当前视口框，可拖动平移（参照 DLT 的导航器）。"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

_COLOR_BG = "#1B1B1B"
_COLOR_BORDER = "#3A3A3A"
_COLOR_VIEW = "#0F6CBD"


class Navigator(QWidget):
    """显示整图的缩小版，并用矩形框标出当前画布可见区域。

    拖动矩形（或点击）可让画布平移到对应位置。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(120)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._canvas = None
        self._pixmap: QPixmap | None = None
        self._frame = QRectF()          # 归一化视口矩形
        self._dragging = False

    # -----------------------------------------------------------
    # 关联
    # -----------------------------------------------------------
    def attach(self, canvas) -> None:
        """绑定标注画布，跟随其视口变化。"""
        self._canvas = canvas
        canvas.viewChanged.connect(self._on_view_changed)

    def set_image(self, path) -> None:
        """载入整图缩略。"""
        pixmap = QPixmap(str(path))
        self._pixmap = None if pixmap.isNull() else pixmap
        self._frame = QRectF()
        self.update()

    # -----------------------------------------------------------
    # 绘制
    # -----------------------------------------------------------
    def _image_rect(self) -> QRectF:
        """图片在控件内的显示矩形（保持比例居中）。"""
        if self._pixmap is None or self._pixmap.isNull():
            return QRectF()
        area = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        scaled = self._pixmap.size().scaled(
            area.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio
        )
        return QRectF(
            area.left() + (area.width() - scaled.width()) / 2,
            area.top() + (area.height() - scaled.height()) / 2,
            scaled.width(), scaled.height(),
        )

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(_COLOR_BG))
        painter.setPen(QPen(QColor(_COLOR_BORDER), 1))
        painter.drawRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5))

        target = self._image_rect()
        if self._pixmap is None or target.isEmpty():
            painter.setPen(QColor("#7A7A7A"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "无图像")
            return

        painter.drawPixmap(target, self._pixmap, QRectF(self._pixmap.rect()))

        if not self._frame.isEmpty():
            frame = QRectF(
                target.left() + self._frame.left() * target.width(),
                target.top() + self._frame.top() * target.height(),
                max(3.0, self._frame.width() * target.width()),
                max(3.0, self._frame.height() * target.height()),
            )
            painter.setPen(QPen(QColor(_COLOR_VIEW), 2))
            painter.setBrush(QColor(15, 108, 189, 60))
            painter.drawRect(frame)

    # -----------------------------------------------------------
    # 交互
    # -----------------------------------------------------------
    def _frame_from_pos(self, pos) -> QRectF:
        target = self._image_rect()
        if target.isEmpty() or self._frame.isEmpty():
            return QRectF()
        x = (pos.x() - target.left()) / target.width()
        y = (pos.y() - target.top()) / target.height()
        return QRectF(
            x - self._frame.width() / 2, y - self._frame.height() / 2,
            self._frame.width(), self._frame.height(),
        )

    def _move_to(self, pos) -> None:
        if self._canvas is None:
            return
        frame = self._frame_from_pos(pos)
        if frame.isEmpty():
            return
        self._canvas.center_on_normalized(
            frame.left() + frame.width() / 2, frame.top() + frame.height() / 2
        )

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self._move_to(event.position())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if self._dragging:
            self._move_to(event.position())

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)

    # -----------------------------------------------------------
    # 同步
    # -----------------------------------------------------------
    def _on_view_changed(self) -> None:
        if self._canvas is None:
            return
        self._frame = self._canvas.visible_scene_rect_normalized()
        self.update()
