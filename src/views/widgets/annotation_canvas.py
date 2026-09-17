"""标注画布：基于 QGraphicsView 的矩形 / 多边形标注。

坐标约定：
    * 场景坐标 == 图片像素坐标；
    * 对外信号一律使用 **归一化坐标**（0~1），与 YOLO 格式一致。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QGraphicsPolygonItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
)

from src.models.annotation import BOX, POLYGON, Annotation

MODE_BROWSE = "browse"
MODE_BOX = "box"
MODE_POLYGON = "polygon"

_MIN_SIZE = 3.0        # 小于该像素尺寸的框视为误触
_FALLBACK_COLOR = "#66CCFF"


class AnnotationCanvas(QGraphicsView):
    """标注画布。"""

    annotationAdded = Signal(int, str, list)   # cls_id, kind, 归一化点列
    deleteRequested = Signal()
    selectionChanged = Signal(int)             # 选中索引，-1 表示无

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self._scene.setBackgroundBrush(QBrush(QColor("#1E1E1E")))
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setMouseTracking(True)

        self._img_w = 0
        self._img_h = 0
        self._mode = MODE_BOX
        self._classes: list = []
        self._pending_class = 0
        self._items: list[Annotation] = []
        self._shapes: list = []
        self._selected = -1

        # 交互中间态
        self._panning = False
        self._pan_start = None
        self._drawing = False
        self._start = None
        self._rubber = None
        self._poly_points: list[QPointF] = []
        self._poly_preview = None

    # -----------------------------------------------------------
    # 配置
    # -----------------------------------------------------------
    def set_classes(self, classes: list) -> None:
        self._classes = list(classes)

    def set_pending_class(self, cls_id: int) -> None:
        self._pending_class = cls_id

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        self._cancel_pending()
        if mode == MODE_BROWSE:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)

    @property
    def mode(self) -> str:
        return self._mode

    # -----------------------------------------------------------
    # 内容
    # -----------------------------------------------------------
    def set_image(self, path) -> None:
        """载入图片并重置视图。"""
        self._cancel_pending()
        self._scene.clear()
        self._items = []
        self._shapes = []
        self._selected = -1
        self._poly_preview = None

        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self._img_w = self._img_h = 0
            return
        self._img_w = pixmap.width()
        self._img_h = pixmap.height()
        item = self._scene.addPixmap(pixmap)
        self._scene.setSceneRect(QRectF(pixmap.rect()))
        self.fitInView(item, Qt.AspectRatioMode.KeepAspectRatio)

    def set_annotations(self, items: list) -> None:
        """重建标注图形。"""
        for shape in self._shapes:
            self._scene.removeItem(shape)
        self._shapes = []
        self._items = list(items or [])
        self._selected = -1

        for item in self._items:
            shape = self._make_shape(item)
            self._scene.addItem(shape)
            self._shapes.append(shape)
        self._apply_selection()

    def select(self, index: int) -> None:
        if 0 <= index < len(self._shapes):
            self._selected = index
        else:
            self._selected = -1
        self._apply_selection()
        self.selectionChanged.emit(self._selected)

    # -----------------------------------------------------------
    # 图形构建
    # -----------------------------------------------------------
    def _make_shape(self, item: Annotation):
        color = QColor(self._color_of(item.cls_id))
        pen = QPen(color, 2)
        if item.is_box:
            x1, y1, x2, y2 = item.bounds()
            rect = QRectF(
                x1 * self._img_w, y1 * self._img_h,
                (x2 - x1) * self._img_w, (y2 - y1) * self._img_h,
            )
            shape = QGraphicsRectItem(rect)
        else:
            polygon = QPolygonF([
                QPointF(x * self._img_w, y * self._img_h) for x, y in item.points
            ])
            shape = QGraphicsPolygonItem(polygon)
        shape.setPen(pen)
        fill = QColor(color)
        fill.setAlpha(60)
        shape.setBrush(QBrush(fill))
        shape.setToolTip(self._class_name(item.cls_id))
        return shape

    def _apply_selection(self) -> None:
        for index, shape in enumerate(self._shapes):
            item = self._items[index]
            color = QColor(self._color_of(item.cls_id))
            if index == self._selected:
                pen = QPen(QColor("#FFFFFF"), 3)
                pen.setStyle(Qt.PenStyle.DashLine)
            else:
                pen = QPen(color, 2)
            shape.setPen(pen)

    def _color_of(self, cls_id: int) -> str:
        for cls in self._classes:
            if cls.cls_id == cls_id:
                return cls.color
        return _FALLBACK_COLOR

    def _class_name(self, cls_id: int) -> str:
        for cls in self._classes:
            if cls.cls_id == cls_id:
                return cls.name
        return str(cls_id)

    # -----------------------------------------------------------
    # 坐标
    # -----------------------------------------------------------
    def _to_normalized(self, points: list) -> list:
        width = self._img_w or 1
        height = self._img_h or 1
        return [
            (
                min(max(p.x() / width, 0.0), 1.0),
                min(max(p.y() / height, 0.0), 1.0),
            )
            for p in points
        ]

    @staticmethod
    def _to_polygon_points(points: list) -> list:
        return [QPointF(x, y) for x, y in points]

    # -----------------------------------------------------------
    # 鼠标 / 键盘
    # -----------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        position = event.position().toPoint()
        scene_pos = self.mapToScene(position)

        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_start = position
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return

        if event.button() == Qt.MouseButton.RightButton:
            if self._mode == MODE_POLYGON:
                self._finish_polygon()
            self._cancel_pending()
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        if self._mode == MODE_BOX:
            self._drawing = True
            self._start = scene_pos
            self._rubber = QGraphicsRectItem(QRectF(scene_pos, scene_pos))
            self._rubber.setPen(QPen(QColor(self._color_of(self._pending_class)), 2))
            self._scene.addItem(self._rubber)
        elif self._mode == MODE_POLYGON:
            self._poly_points.append(scene_pos)
            if self._poly_preview is None:
                self._poly_preview = QGraphicsPolygonItem(QPolygonF(self._poly_points))
                self._poly_preview.setPen(
                    QPen(QColor(self._color_of(self._pending_class)), 2)
                )
                self._scene.addItem(self._poly_preview)
            else:
                self._poly_preview.setPolygon(QPolygonF(self._poly_points))
        else:
            self._select_at(scene_pos)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        position = event.position().toPoint()
        if self._panning and self._pan_start is not None:
            delta = position - self._pan_start
            self._pan_start = position
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x()
            )
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - delta.y()
            )
            return

        scene_pos = self.mapToScene(position)
        if self._drawing and self._rubber is not None and self._start is not None:
            self._rubber.setRect(QRectF(self._start, scene_pos).normalized())
        elif self._mode == MODE_POLYGON and self._poly_points and self._poly_preview:
            self._poly_preview.setPolygon(
                QPolygonF(self._poly_points + [scene_pos])
            )

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = False
            self.setCursor(
                Qt.CursorShape.CrossCursor
                if self._mode != MODE_BROWSE else Qt.CursorShape.ArrowCursor
            )
            return

        if (
            self._drawing
            and event.button() == Qt.MouseButton.LeftButton
            and self._rubber is not None
        ):
            rect = self._rubber.rect()
            self._scene.removeItem(self._rubber)
            self._rubber = None
            self._drawing = False
            if rect.width() >= _MIN_SIZE and rect.height() >= _MIN_SIZE:
                points = self._to_normalized([rect.topLeft(), rect.bottomRight()])
                self.annotationAdded.emit(self._pending_class, BOX, points)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if self._mode == MODE_POLYGON:
            self._finish_polygon()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self._selected >= 0:
                self.deleteRequested.emit()
                return
        if event.key() == Qt.Key.Key_Escape:
            self._cancel_pending()
            return
        super().keyPressEvent(event)

    # -----------------------------------------------------------
    # 内部
    # -----------------------------------------------------------
    def _select_at(self, scene_pos: QPointF) -> None:
        for index in range(len(self._shapes) - 1, -1, -1):
            if self._shapes[index].contains(scene_pos):
                self._selected = index
                self._apply_selection()
                self.selectionChanged.emit(index)
                return
        self._selected = -1
        self._apply_selection()
        self.selectionChanged.emit(-1)

    def _finish_polygon(self) -> None:
        points = list(self._poly_points)
        self._poly_points.clear()
        if self._poly_preview is not None:
            self._scene.removeItem(self._poly_preview)
            self._poly_preview = None
        if len(points) >= 3:
            self.annotationAdded.emit(
                self._pending_class, POLYGON, self._to_normalized(points)
            )

    def _cancel_pending(self) -> None:
        self._drawing = False
        if self._rubber is not None:
            self._scene.removeItem(self._rubber)
            self._rubber = None
        self._poly_points.clear()
        if self._poly_preview is not None:
            self._scene.removeItem(self._poly_preview)
            self._poly_preview = None
