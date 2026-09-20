"""标注画布：基于 QGraphicsView 的矩形 / 多边形 / 掩码标注。

坐标约定：
    * 场景坐标 == 图片像素坐标；
    * 对外信号一律使用 **归一化坐标**（0~1），与 YOLO 格式一致。

交互要点（参照 Halcon DLT）：
    * 浏览模式：点选 / `Ctrl` 多选 / `Shift` 框选 / `Ctrl+A` 全选，拖动即移动；
    * 方向键微调（`Alt` 或 `Shift` 更细 / 更大步长）；
    * 多边形模式：单击加点、右键或双击闭合；开启「孔洞」后画的是孔洞；
    * 掩码模式：画笔涂抹、橡皮擦除，`生成轮廓` 把掩码转成多边形实例。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsPolygonItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
)

from src.models.annotation import BOX, POLYGON, Annotation
from src.utils.geometry import area, merge_holes, rotate, translate

MODE_BROWSE = "browse"
MODE_BOX = "box"
MODE_POLYGON = "polygon"
MODE_MASK = "mask"

_MIN_SIZE = 3.0        # 小于该像素尺寸的框视为误触
_FALLBACK_COLOR = "#66CCFF"
_NUDGE_PIXELS = {"normal": 1.0, "fine": 0.2, "coarse": 10.0}
# 掩码上过小的连通域直接丢弃（图片面积的比例；归一化面积本身即占比）
_MIN_MASK_AREA = 0.00001


class AnnotationCanvas(QGraphicsView):
    """标注画布。"""

    annotationAdded = Signal(int, str, list)   # cls_id, kind, 归一化点列
    annotationChanged = Signal()               # 已有标注被修改（如旋转 / 移动）
    deleteRequested = Signal()                 # 删除选中的标注（单/多选）
    selectionChanged = Signal(int)             # 主选中索引，-1 表示无
    selectionListChanged = Signal(list)        # 选中索引列表（多选）
    classRequested = Signal(int)               # 数字键请求切换类别（cls_id）
    imageStepRequested = Signal(int)           # PageUp / PageDown 翻图（±1 / ±999）
    editStarted = Signal()                     # 开始一次几何编辑（供撤销快照）
    editFinished = Signal(str)                 # 编辑结束（参数为动作名）
    viewChanged = Signal()                     # 视口/缩放变化（供导航器同步）
    maskChanged = Signal()                     # 掩码内容变化

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
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._img_w = 0
        self._img_h = 0
        self._pixmap_item = None
        self._auto_fit = True        # 当前缩放是否由「自动适配」产生
        self._source_image: QImage | None = None
        self._brightness = 0
        self._contrast = 0
        self._mode = MODE_BOX
        self._classes: list = []
        self._pending_class = 0
        self._items: list[Annotation] = []
        self._shapes: list = []
        self._selected = -1
        self._selection: list[int] = []

        # 交互中间态
        self._panning = False
        self._pan_start = None
        self._drawing = False
        self._start = None
        self._rubber = None
        self._poly_points: list[QPointF] = []
        self._poly_preview = None

        # 拖动移动
        self._moving = False
        self._move_start = None
        self._move_origin: dict[int, list] = {}

        # 框选
        self._band_selecting = False
        self._band_item = None

        # 掩码
        self._mask_image: QImage | None = None
        self._mask_item: QGraphicsPixmapItem | None = None
        self._mask_painting = False
        self._mask_last: QPointF | None = None
        self._mask_erase = False
        self._brush_size = 24.0
        self._hole_mode = False

        # 显示选项（来自偏好设置）
        self._cursor_scene: QPointF | None = None
        self._crosshair = True
        self._crosshair_opacity = 100
        self._region_opacity = 50
        self._wheel_inverted = False
        self._show_pixel = True

    # -----------------------------------------------------------
    # 显示选项
    # -----------------------------------------------------------
    def set_crosshair(self, enabled: bool, opacity: int | None = None) -> None:
        self._crosshair = bool(enabled)
        if opacity is not None:
            self._crosshair_opacity = max(15, min(100, int(opacity)))
        self.viewport().update()

    def set_region_opacity(self, percent: int) -> None:
        """标注区域填充不透明度（0~100）。"""
        self._region_opacity = max(0, min(100, int(percent)))
        for index in range(len(self._shapes)):
            self._apply_fill(index)
        self.viewport().update()

    def set_wheel_inverted(self, inverted: bool) -> None:
        self._wheel_inverted = bool(inverted)

    def set_show_pixel(self, enabled: bool) -> None:
        self._show_pixel = bool(enabled)
        self.viewport().update()

    # -----------------------------------------------------------
    # 配置
    # -----------------------------------------------------------
    def set_classes(self, classes: list) -> None:
        self._classes = list(classes)

    def set_pending_class(self, cls_id: int) -> None:
        self._pending_class = cls_id

    def set_mode(self, mode: str) -> None:
        # 退出掩码模式时保留掩码内容（便于切回来继续涂），只在载入新图时清空
        self._mode = mode
        self._cancel_pending()
        self._moving = False
        self._band_selecting = False
        if mode == MODE_BROWSE:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        elif mode == MODE_MASK:
            self._ensure_mask()
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)

    @property
    def mode(self) -> str:
        return self._mode

    # -----------------------------------------------------------
    # 掩码（画笔 / 橡皮 / 生成轮廓）
    # -----------------------------------------------------------
    def set_brush_size(self, pixels: float) -> None:
        self._brush_size = max(2.0, float(pixels))

    def brush_size(self) -> float:
        return self._brush_size

    def set_mask_erase(self, erase: bool) -> None:
        """True 时画笔变为橡皮（擦除掩码）。"""
        self._mask_erase = bool(erase)

    def set_hole_mode(self, hole: bool) -> None:
        """True 时多边形工具画的是「孔洞」（从选中实例中挖掉）。"""
        self._hole_mode = bool(hole)

    def hole_mode(self) -> bool:
        return self._hole_mode

    def mask_active(self) -> bool:
        return self._mask_image is not None

    def clear_mask(self) -> None:
        if self._mask_image is None:
            return
        self._mask_image.fill(Qt.GlobalColor.transparent)
        self._refresh_mask_item()
        self.maskChanged.emit()

    def generate_from_mask(self) -> int:
        """把掩码转成多边形实例（含孔洞桥接），返回生成的实例数。"""
        polygons = self.mask_polygons()
        if not polygons:
            return 0
        self.clear_mask()
        for points in polygons:
            self.annotationAdded.emit(self._pending_class, POLYGON, points)
        return len(polygons)

    def mask_polygons(self) -> list[list]:
        """掩码 → 归一化多边形（孔洞已并入外轮廓）。"""
        if self._mask_image is None or not self._img_w or not self._img_h:
            return []
        try:
            import cv2
            import numpy as np
        except ImportError:
            return []
        image = self._mask_image.convertToFormat(QImage.Format.Format_RGBA8888)
        buffer = image.constBits()
        array = np.frombuffer(
            buffer, dtype=np.uint8, count=image.sizeInBytes()
        ).reshape((self._img_h, image.bytesPerLine() // 4, 4))
        alpha = np.ascontiguousarray(array[:, : self._img_w, 3])
        _, binary = cv2.threshold(alpha, 8, 255, cv2.THRESH_BINARY)
        contours, hierarchy = cv2.findContours(
            binary, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE
        )
        if not contours or hierarchy is None:
            return []
        hierarchy = hierarchy[0]
        min_area = _MIN_MASK_AREA      # area() 返回归一化面积（即占整图比例）
        result: list[list] = []
        for index, contour in enumerate(contours):
            # 只取外轮廓（父级为 -1），孔洞随后并入
            if hierarchy[index][3] != -1:
                continue
            outer = self._contour_points(contour)
            if area(outer) < min_area:
                continue
            holes = []
            child = hierarchy[index][2]
            while child != -1:
                inner = self._contour_points(contours[child])
                if area(inner) >= min_area:
                    holes.append(inner)
                child = hierarchy[child][0]
            result.append(merge_holes(outer, holes))
        return result

    def _contour_points(self, contour) -> list:
        """cv2 轮廓 → 归一化点列（去掉重复的闭合点）。"""
        points = [
            (float(point[0][0]), float(point[0][1])) for point in contour
        ]
        if len(points) > 1 and points[0] == points[-1]:
            points.pop()
        return [
            (min(max(x / self._img_w, 0.0), 1.0),
             min(max(y / self._img_h, 0.0), 1.0))
            for x, y in points
        ]

    def rasterize_selected(self, grow: float = 0.0) -> bool:
        """把选中的多边形「画」进掩码，便于继续用画笔精修。"""
        targets = self._selection or ([self._selected] if self._selected >= 0 else [])
        polygons = [
            self._items[index].points
            for index in targets
            if 0 <= index < len(self._items) and len(self._items[index].points) >= 3
        ]
        if not polygons:
            return False
        self._ensure_mask()
        painter = QPainter(self._mask_image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        color = QColor(self._color_of(self._pending_class))
        color.setAlpha(255)
        painter.setBrush(QBrush(color))
        painter.setPen(QPen(color, max(0.0, grow)))
        for points in polygons:
            polygon = QPolygonF([
                QPointF(x * self._img_w, y * self._img_h) for x, y in points
            ])
            painter.drawPolygon(polygon)
        painter.end()
        self._refresh_mask_item()
        self.maskChanged.emit()
        return True

    def apply_hole(self, hole_points: list) -> bool:
        """把孔洞并入主选中实例（写成单个多边形）。"""
        index = self._selected
        if not (0 <= index < len(self._items)) or len(hole_points) < 3:
            return False
        item = self._items[index]
        if item.is_box:
            x1, y1, x2, y2 = item.bounds()
            item.points = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
            item.kind = POLYGON
        self.editStarted.emit()
        item.points = merge_holes(list(item.points), [list(hole_points)])
        self._rebuild_shape(index)
        self.annotationChanged.emit()
        self.editFinished.emit("添加孔洞")
        return True

    def _ensure_mask(self) -> None:
        """按图片尺寸创建掩码图层（已存在则复用）。"""
        if self._mask_image is not None or not self._img_w or not self._img_h:
            return
        self._mask_image = QImage(
            self._img_w, self._img_h, QImage.Format.Format_RGBA8888
        )
        self._mask_image.fill(Qt.GlobalColor.transparent)
        self._mask_item = QGraphicsPixmapItem()
        self._mask_item.setZValue(5)
        self._mask_item.setOpacity(0.55)
        self._scene.addItem(self._mask_item)
        self._refresh_mask_item()

    def _refresh_mask_item(self) -> None:
        if self._mask_item is not None and self._mask_image is not None:
            self._mask_item.setPixmap(QPixmap.fromImage(self._mask_image))

    def _paint_mask(self, scene_pos: QPointF) -> None:
        if self._mask_image is None:
            self._ensure_mask()
        if self._mask_image is None:
            return
        painter = QPainter(self._mask_image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._mask_erase:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        color = QColor(self._color_of(self._pending_class))
        color.setAlpha(255)
        painter.setPen(QPen(color, self._brush_size,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                            Qt.PenJoinStyle.RoundJoin))
        if self._mask_last is None:
            painter.drawPoint(scene_pos)
            painter.setBrush(QBrush(color))
            painter.drawEllipse(scene_pos, self._brush_size / 2, self._brush_size / 2)
        else:
            painter.drawLine(self._mask_last, scene_pos)
        painter.end()
        self._mask_last = scene_pos
        self._refresh_mask_item()
        self.maskChanged.emit()

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
        self._selection = []
        self._poly_preview = None
        self._pixmap_item = None
        self._mask_image = None
        self._mask_item = None
        self._mask_last = None
        self._moving = False
        self._band_selecting = False

        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self._img_w = self._img_h = 0
            self._source_image = None
            return
        self._source_image = pixmap.toImage()
        self._img_w = pixmap.width()
        self._img_h = pixmap.height()
        self._pixmap_item = self._scene.addPixmap(pixmap)
        self._scene.setSceneRect(QRectF(pixmap.rect()))
        self._auto_fit = True
        self._apply_adjust()
        self._fit()

    # -----------------------------------------------------------
    # 亮度 / 对比度
    # -----------------------------------------------------------
    def set_brightness(self, value: int) -> None:
        """亮度 -100 ~ 100（调整显示，不影响保存的像素）。"""
        self._brightness = max(-100, min(100, int(value)))
        self._apply_adjust()

    def set_contrast(self, value: int) -> None:
        """对比度 -100 ~ 100。"""
        self._contrast = max(-100, min(100, int(value)))
        self._apply_adjust()

    def reset_adjust(self) -> None:
        self._brightness = 0
        self._contrast = 0
        self._apply_adjust()

    @property
    def brightness(self) -> int:
        return self._brightness

    @property
    def contrast(self) -> int:
        return self._contrast

    def _apply_adjust(self) -> None:
        """把亮度/对比度作用到显示用位图（保持当前缩放与平移不变）。"""
        if self._pixmap_item is None or self._source_image is None:
            return
        image = self._source_image
        if self._brightness or self._contrast:
            image = self._adjust_image(image)
        self._pixmap_item.setPixmap(QPixmap.fromImage(image))

    def _adjust_image(self, image: QImage) -> QImage:
        try:
            import cv2
            import numpy as np
        except ImportError:
            return image
        try:
            width, height = image.width(), image.height()
            converted = image.convertToFormat(QImage.Format.Format_RGB888)
            bits = converted.constBits()
            array = np.frombuffer(bits, dtype=np.uint8, count=converted.sizeInBytes())
            array = array.reshape((height, converted.bytesPerLine()))[:, : width * 3]
            array = array.reshape((height, width, 3))
            alpha = 1.0 + self._contrast / 100.0
            beta = float(self._brightness) * 2.0
            adjusted = cv2.convertScaleAbs(array, alpha=alpha, beta=beta)
            adjusted = np.ascontiguousarray(adjusted)
            result = QImage(
                adjusted.data, width, height, width * 3,
                QImage.Format.Format_RGB888,
            )
            return result.copy()
        except (cv2.error, ValueError):
            return image

    def set_annotations(self, items: list) -> None:
        """重建标注图形。"""
        for shape in self._shapes:
            self._scene.removeItem(shape)
        self._shapes = []
        self._items = list(items or [])
        self._selected = -1
        self._selection = []

        for item in self._items:
            shape = self._make_shape(item)
            self._scene.addItem(shape)
            self._shapes.append(shape)
        self._apply_selection()
        self.selectionListChanged.emit([])

    def select(self, index: int) -> None:
        if 0 <= index < len(self._shapes):
            self._selected = index
            self._selection = [index]
        else:
            self._selected = -1
            self._selection = []
        self._apply_selection()
        self.selectionChanged.emit(self._selected)
        self.selectionListChanged.emit(list(self._selection))

    def select_many(self, indexes: list, primary: int | None = None) -> None:
        """选中多个标注（`primary` 为主选中项，供属性面板使用）。"""
        valid = sorted({int(i) for i in indexes if 0 <= int(i) < len(self._items)})
        self._selection = valid
        if primary is not None and primary in valid:
            self._selected = int(primary)
        else:
            self._selected = valid[-1] if valid else -1
        self._apply_selection()
        self.selectionChanged.emit(self._selected)
        self.selectionListChanged.emit(list(self._selection))

    def select_all(self) -> None:
        self.select_many(list(range(len(self._items))))

    def selection(self) -> list[int]:
        """当前选中的全部标注索引。"""
        return list(self._selection)

    def set_selected_points(self, index: int, points: list) -> bool:
        """直接设置某个标注的点列（数值编辑用）。"""
        if not (0 <= index < len(self._items)):
            return False
        self.editStarted.emit()
        self._items[index].points = [
            (min(max(float(x), 0.0), 1.0), min(max(float(y), 0.0), 1.0))
            for x, y in points
        ]
        self._rebuild_shape(index)
        self.annotationChanged.emit()
        self.editFinished.emit("修改标注")
        return True

    def set_selected_class(self, indexes: list, cls_id: int) -> int:
        """批量改类别，返回改动的标注数。"""
        changed = 0
        self.editStarted.emit()
        for index in indexes:
            if 0 <= index < len(self._items):
                self._items[index].cls_id = int(cls_id)
                self._rebuild_shape(index)
                changed += 1
        if changed:
            self._apply_selection()
            self.annotationChanged.emit()
            self.editFinished.emit("修改类别")
        return changed

    def nudge_selected(self, dx_pixels: float, dy_pixels: float) -> bool:
        """按像素微调选中的标注（多选时一起移动）。"""
        targets = self._selection or ([self._selected] if self._selected >= 0 else [])
        targets = [i for i in targets if 0 <= i < len(self._items)]
        if not targets:
            return False
        width = self._img_w or 1
        height = self._img_h or 1
        self.editStarted.emit()
        for index in targets:
            self._items[index].points = translate(
                self._items[index].points, dx_pixels / width, dy_pixels / height
            )
            self._rebuild_shape(index)
        self._apply_selection()
        self.annotationChanged.emit()
        self.editFinished.emit("移动标注")
        return True

    def rotate_selected_multi(self, degrees: float) -> int:
        """绕各自中心旋转选中的全部标注。"""
        targets = self._selection or ([self._selected] if self._selected >= 0 else [])
        targets = [i for i in targets if 0 <= i < len(self._items)]
        count = 0
        self.editStarted.emit()
        for index in targets:
            item = self._items[index]
            if item.is_box:
                x1, y1, x2, y2 = item.bounds()
                item.points = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
                item.kind = POLYGON
            item.points = rotate(list(item.points), degrees)
            self._rebuild_shape(index)
            count += 1
        if count:
            self._apply_selection()
            self.annotationChanged.emit()
            self.editFinished.emit("旋转标注")
        return count

    def _rebuild_shape(self, index: int) -> None:
        """按当前数据重建第 index 个图形。"""
        if not (0 <= index < len(self._shapes)):
            return
        self._scene.removeItem(self._shapes[index])
        shape = self._make_shape(self._items[index])
        self._scene.addItem(shape)
        self._shapes[index] = shape

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
        shape.setBrush(QBrush(self._fill_color(color)))
        shape.setToolTip(self._class_name(item.cls_id))
        return shape

    def _fill_color(self, color: str | QColor) -> QColor:
        """按「标注区域不透明度」得到填充色。"""
        fill = QColor(color)
        fill.setAlpha(int(255 * self._region_opacity / 100 * 0.5))
        return fill

    def _apply_fill(self, index: int) -> None:
        if not (0 <= index < len(self._shapes)):
            return
        color = self._color_of(self._items[index].cls_id)
        self._shapes[index].setBrush(QBrush(self._fill_color(color)))

    def _apply_selection(self) -> None:
        marked = set(self._selection)
        if not marked and self._selected >= 0:
            marked = {self._selected}
        for index, shape in enumerate(self._shapes):
            item = self._items[index]
            color = QColor(self._color_of(item.cls_id))
            if index in marked:
                width = 3 if index == self._selected else 2
                pen = QPen(QColor("#FFFFFF"), width)
                if index != self._selected:
                    pen.setStyle(Qt.PenStyle.DotLine)
                else:
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
    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        if self._auto_fit:
            QTimer.singleShot(0, self._fit)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        if self._auto_fit:
            QTimer.singleShot(0, self._fit)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        position = event.position().toPoint()
        scene_pos = self.mapToScene(position)

        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._auto_fit = False
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

        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)

        if self._mode == MODE_MASK:
            self._mask_painting = True
            self._mask_last = None
            self._paint_mask(scene_pos)
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
                pen = QPen(QColor(self._color_of(self._pending_class)), 2)
                if self._hole_mode:
                    pen.setStyle(Qt.PenStyle.DashLine)
                self._poly_preview.setPen(pen)
                self._scene.addItem(self._poly_preview)
            else:
                self._poly_preview.setPolygon(QPolygonF(self._poly_points))
        else:
            hit = self._shape_index_at(scene_pos)
            if shift:
                # Shift + 拖动 = 框选
                self._band_selecting = True
                self._start = scene_pos
                self._band_item = QGraphicsRectItem(QRectF(scene_pos, scene_pos))
                self._band_item.setPen(
                    QPen(QColor(_FALLBACK_COLOR), 1, Qt.PenStyle.DashLine)
                )
                self._scene.addItem(self._band_item)
            elif hit >= 0:
                # 点在已选中的标注上 → 拖动移动；否则先选中它
                if ctrl:
                    selection = list(self._selection)
                    if hit in selection:
                        selection.remove(hit)
                    else:
                        selection.append(hit)
                    self.select_many(selection, primary=hit if hit in selection else None)
                elif hit not in self._selection:
                    self.select_many([hit], primary=hit)
                self._start_move(scene_pos)
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
        # 十字准线跟随光标
        self._cursor_scene = scene_pos
        self.viewport().update()
        if self._mask_painting:
            self._paint_mask(scene_pos)
            return
        if self._moving and self._move_start is not None:
            self._move_to(scene_pos)
            return
        if self._band_selecting and self._band_item is not None and self._start is not None:
            self._band_item.setRect(QRectF(self._start, scene_pos).normalized())
            return
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

        if event.button() == Qt.MouseButton.LeftButton:
            if self._mask_painting:
                self._mask_painting = False
                self._mask_last = None
                return
            if self._moving:
                self._finish_move()
                return
            if self._band_selecting:
                self._finish_band_select()
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
        self._auto_fit = False
        zoom_in = event.angleDelta().y() > 0
        if self._wheel_inverted:
            zoom_in = not zoom_in
        factor = 1.15 if zoom_in else 1 / 1.15
        self.scale(factor, factor)
        self.viewChanged.emit()

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._cursor_scene = None
        self.viewport().update()
        super().leaveEvent(event)

    # -----------------------------------------------------------
    # 十字准线 / 像素值（前景绘制）
    # -----------------------------------------------------------
    def drawForeground(self, painter, rect) -> None:  # noqa: N802 - Qt 命名
        super().drawForeground(painter, rect)
        if not self._img_w or self._cursor_scene is None:
            return
        if self._crosshair:
            alpha = int(255 * self._crosshair_opacity / 100)
            pen = QPen(QColor(255, 255, 255, alpha), 1, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            painter.setPen(pen)
            x, y = self._cursor_scene.x(), self._cursor_scene.y()
            painter.drawLine(QPointF(x, 0.0), QPointF(x, float(self._img_h)))
            painter.drawLine(QPointF(0.0, y), QPointF(float(self._img_w), y))
        if self._show_pixel:
            text = self._pixel_text()
            if not text:
                return
            painter.setPen(QPen(QColor("#FFFFFF"), 1))
            font = painter.font()
            font.setPointSizeF(max(7.5, font.pointSizeF() - 1.0))
            painter.setFont(font)
            metrics = painter.fontMetrics()
            box = QRectF(
                self._cursor_scene.x() + 8, self._cursor_scene.y() + 8,
                metrics.horizontalAdvance(text) + 10, metrics.height() + 4,
            )
            painter.fillRect(box, QColor(0, 0, 0, 170))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    def _pixel_text(self) -> str:
        """光标处的像素值（灰度 / RGB）。"""
        if self._source_image is None or self._cursor_scene is None:
            return ""
        x = int(self._cursor_scene.x())
        y = int(self._cursor_scene.y())
        if not (0 <= x < self._source_image.width()
                and 0 <= y < self._source_image.height()):
            return ""
        pixel = self._source_image.pixelColor(x, y)
        if pixel.red() == pixel.green() == pixel.blue():
            return f"({x}, {y})  {pixel.red()}"
        return f"({x}, {y})  R{pixel.red()} G{pixel.green()} B{pixel.blue()}"

    def scrollContentsBy(self, dx: int, dy: int) -> None:  # noqa: N802 - Qt 命名
        super().scrollContentsBy(dx, dy)
        self.viewChanged.emit()

    # -----------------------------------------------------------
    # 视口（供导航器使用）
    # -----------------------------------------------------------
    def visible_scene_rect_normalized(self) -> QRectF:
        """当前可见区域在图片坐标系下的归一化矩形。"""
        if not self._img_w or not self._img_h:
            return QRectF()
        rect = self.mapToScene(self.viewport().rect()).boundingRect()
        return QRectF(
            rect.left() / self._img_w,
            rect.top() / self._img_h,
            rect.width() / self._img_w,
            rect.height() / self._img_h,
        )

    def center_on_normalized(self, x: float, y: float) -> None:
        """把画布中心移动到指定归一化坐标。"""
        if not self._img_w or not self._img_h:
            return
        self.centerOn(x * self._img_w, y * self._img_h)

    def fit_to_view(self) -> None:
        """恢复为「适应窗口」缩放。"""
        self._auto_fit = True
        self._fit()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        alt = bool(mods & Qt.KeyboardModifier.AltModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)

        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self._selected >= 0 or self._selection:
                self.deleteRequested.emit()
            return
        if key == Qt.Key.Key_Escape:
            self._cancel_pending()
            if self._selection:
                self.select_many([])
            return
        if key == Qt.Key.Key_A and ctrl:
            self.select_all()
            return
        if key == Qt.Key.Key_0 and ctrl:
            self.fit_to_view()
            return
        if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal) and ctrl:
            self._zoom_by(1.15)
            return
        if key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore) and ctrl:
            self._zoom_by(1 / 1.15)
            return
        if key in (Qt.Key.Key_PageDown, Qt.Key.Key_PageUp):
            step = 1 if key == Qt.Key.Key_PageDown else -1
            if ctrl:
                step = 9999 * (1 if step > 0 else -1)      # Ctrl + 翻页 = 首 / 末张
            self.imageStepRequested.emit(step)
            return
        # 数字键 1-9 选类别（与 DLT 一致）
        if Qt.Key.Key_1 <= key <= Qt.Key.Key_9 and not ctrl and not alt:
            self.classRequested.emit(key - Qt.Key.Key_1)
            return
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            step = _NUDGE_PIXELS["fine" if alt else ("coarse" if shift else "normal")]
            dx = -step if key == Qt.Key.Key_Left else (step if key == Qt.Key.Key_Right else 0.0)
            dy = -step if key == Qt.Key.Key_Up else (step if key == Qt.Key.Key_Down else 0.0)
            if self.nudge_selected(dx, dy):
                return
        super().keyPressEvent(event)

    def _zoom_by(self, factor: float) -> None:
        self._auto_fit = False
        self.scale(factor, factor)
        self.viewChanged.emit()

    # -----------------------------------------------------------
    # 内部
    # -----------------------------------------------------------
    def _fit(self) -> None:
        """按当前视口尺寸适配图片。

        标注页可能尚未显示（或刚创建、未完成布局）就载入了图片，此时视口尺寸
        不正确，算出的缩放会明显偏小；因此适配会在 showEvent / resizeEvent
        中重做，直到用户手动缩放或平移（`_auto_fit` 置 False）为止。
        """
        if self._pixmap_item is None:
            return
        if self.viewport().width() <= 1 or self.viewport().height() <= 1:
            return
        self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
        self.viewChanged.emit()

    def rotate_selected(self, delta_deg: float) -> bool:
        """把选中的标注绕自身中心旋转指定角度（多选时一起旋转）。

        矩形会先转换为四点多边形 —— 这正好是 YOLO 旋转框（OBB）的标注格式，
        因此旋转框任务无需额外的数据格式支持。
        """
        return self.rotate_selected_multi(float(delta_deg)) > 0

    def _select_at(self, scene_pos: QPointF) -> None:
        index = self._shape_index_at(scene_pos)
        if index >= 0:
            self.select_many([index], primary=index)
            return
        self.select_many([])

    def _finish_polygon(self) -> None:
        points = list(self._poly_points)
        self._poly_points.clear()
        if self._poly_preview is not None:
            self._scene.removeItem(self._poly_preview)
            self._poly_preview = None
        if len(points) < 3:
            return
        normalized = self._to_normalized(points)
        if self._hole_mode:
            # 孔洞：并入当前选中的实例
            if not self.apply_hole(normalized):
                self.annotationAdded.emit(self._pending_class, POLYGON, normalized)
            return
        self.annotationAdded.emit(self._pending_class, POLYGON, normalized)

    def _shape_index_at(self, scene_pos: QPointF) -> int:
        """点中的最上层标注索引，未命中返回 -1。"""
        for index in range(len(self._shapes) - 1, -1, -1):
            if self._shapes[index].contains(scene_pos):
                return index
        return -1

    # -----------------------------------------------------------
    # 拖动移动 / 框选
    # -----------------------------------------------------------
    def _start_move(self, scene_pos: QPointF) -> None:
        self._moving = True
        self._move_start = scene_pos
        self._move_origin = {
            index: list(self._items[index].points)
            for index in (self._selection or [self._selected])
            if 0 <= index < len(self._items)
        }
        self.editStarted.emit()

    def _move_to(self, scene_pos: QPointF) -> None:
        if self._move_start is None:
            return
        dx = scene_pos.x() - self._move_start.x()
        dy = scene_pos.y() - self._move_start.y()
        width = self._img_w or 1
        height = self._img_h or 1
        for index, origin in self._move_origin.items():
            self._items[index].points = translate(
                origin, dx / width, dy / height
            )
            self._rebuild_shape(index)
        self._apply_selection()
        self.annotationChanged.emit()

    def _finish_move(self) -> None:
        self._moving = False
        self._move_start = None
        self._move_origin = {}
        self.editFinished.emit("移动标注")

    def _finish_band_select(self) -> None:
        self._band_selecting = False
        rect = self._band_item.rect() if self._band_item is not None else QRectF()
        if self._band_item is not None:
            self._scene.removeItem(self._band_item)
            self._band_item = None
        if rect.isEmpty():
            return
        # 完全落在框内的才选中（与 DLT 一致）
        indexes = [
            index for index, item in enumerate(self._items)
            if rect.contains(
                QRectF(
                    item.bounds()[0] * self._img_w, item.bounds()[1] * self._img_h,
                    (item.bounds()[2] - item.bounds()[0]) * self._img_w,
                    (item.bounds()[3] - item.bounds()[1]) * self._img_h,
                )
            )
        ]
        if indexes:
            self.select_many(indexes)

    def _cancel_pending(self) -> None:
        self._drawing = False
        self._mask_painting = False
        self._mask_last = None
        self._moving = False
        self._move_start = None
        self._move_origin = {}
        if self._rubber is not None:
            self._scene.removeItem(self._rubber)
            self._rubber = None
        if self._band_item is not None:
            self._scene.removeItem(self._band_item)
            self._band_item = None
        self._band_selecting = False
        self._poly_points.clear()
        if self._poly_preview is not None:
            self._scene.removeItem(self._poly_preview)
            self._poly_preview = None
