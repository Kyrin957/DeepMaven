"""缩略图网格：图库 / 图像标注 / 标注检查三页共用的图片排布视图。

参照 Halcon DLT 的图库视图：
    * 图片以卡片网格密集排布，卡片下方显示文件名
    * 已标注的图片右上角带角标，颜色取自该图所属类别颜色
    * 右下角以 T / V / E 标示所属数据集（训练 / 验证 / 测试）
    * 选中项高亮描边
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QListView,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
)

from src.utils.constants import SPLIT_COLORS
from src.utils.image_ops import adjust_pixmap, normalize_display

# 条目数据角色
_PATH_ROLE = Qt.ItemDataRole.UserRole
_ANNOTATED_ROLE = Qt.ItemDataRole.UserRole + 1
_MASTER_ROLE = Qt.ItemDataRole.UserRole + 2
_COLOR_ROLE = Qt.ItemDataRole.UserRole + 3      # 类别颜色（已标注角标用）
_SUBSET_ROLE = Qt.ItemDataRole.UserRole + 4     # 所属数据集：T / V / E
_MARKERS_ROLE = Qt.ItemDataRole.UserRole + 5    # 图像标记颜色列表
_EVAL_ROLE = Qt.ItemDataRole.UserRole + 6       # 评估标记 {conf, correct, label}
_CLASSNAME_ROLE = Qt.ItemDataRole.UserRole + 7  # 类别名（可叠加在缩略图上）

# 子集标记 → SPLIT_COLORS 的键 / 显示名
_SUBSET_KEY = {"T": "train", "V": "val", "E": "test"}
_SUBSET_NAME = {"T": "train（训练）", "V": "val（验证）", "E": "test（测试）"}
_MAX_MARKERS = 4            # 左下角最多显示几个标记色点

# 图片上的三个角标：下面都是**基准尺寸**（176px 档位下的大小），
# 绘制时按「缩略图档位 / 基准边长」等比缩放，避免小图角标过大、大图角标过小。
_REFERENCE_THUMB = 176.0    # 基准缩略图边长（THUMB_LARGE，此档位下角标大小刚好）
_MIN_SCALE = 0.25           # 缩放下限，防止极端档位下角标被压成 0

_MARK_SIZE = 22.0           # 右上角「已标注」三角
_SUBSET_SIZE = 28.0         # 右下角 T / V / E 方块
_SUBSET_FONT = 12.0         # T / V / E 字母字号
_MARKER_SIZE = 12.0         # 左下角标记色点
_MARKER_GAP = 4.0

_BADGE_INSET = 7.0          # 角标距缩略图框边缘的间距（基准）
_THUMB_INSET = 5.0          # 缩略图框距卡片边缘的间距
_THUMB_TEXT_RESERVE = 28.0  # 卡片底部为文件名一行预留的高度

# 缩略图尺寸档位（界面上的 小 / 中 / 大 / 特大）
THUMB_SMALL = 88
THUMB_MEDIUM = 124
THUMB_LARGE = 176
THUMB_XLARGE = 352         # 特大档：最大档再大一倍
THUMB_STEPS = (THUMB_SMALL, THUMB_MEDIUM, THUMB_LARGE, THUMB_XLARGE)
MASTER_SIZE = THUMB_XLARGE  # 缓存主图的边长（按最大档取，放大时不糊）

# 配色
_COLOR_BG = "#2B2B2B"
_COLOR_BG_HOVER = "#343434"
_COLOR_BG_SELECTED = "#0B3D6B"
_COLOR_BORDER = "#333333"
_COLOR_BORDER_SELECTED = "#0F6CBD"
_COLOR_MARK = "#E3008C"        # 已标注角标（DLT 粉色）
_COLOR_TEXT = "#E8E8E8"
_COLOR_TEXT_DIM = "#909090"

_COLOR_TAG_EMPTY = "#8A8A8A"   # 标记没有颜色时的色点颜色

# 评估页：预测正确 / 错误的标记色
_COLOR_EVAL_OK = "#0F7B0F"
_COLOR_EVAL_BAD = "#C42B1C"

# 主图缓存：同一进程内复用已解码并缩放的缩略图，避免每次刷新都重新读盘解码
# （主图按最大档尺寸缓存，条目较大，因此上限比早期版本收敛）
_MASTER_CACHE: dict = {}
_MASTER_CACHE_LIMIT = 192

# 显示增强（亮度 / 对比度）结果的缓存上限：按「图片 + 档位 + 参数」缓存
_ADJUST_CACHE_LIMIT = 600


class ThumbnailDelegate(QStyledItemDelegate):
    """绘制缩略图卡片。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.thumb = THUMB_MEDIUM
        # 显示增强：只影响显示，不写回文件（图库 / 检查页用）
        self.brightness = 0.0
        self.contrast = 0.0
        self.show_class_names = False
        self.name_width = 0.6
        self._adjust_cache: dict = {}

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 - Qt 命名
        return QSize(self.thumb + 18, self.thumb + 42)

    def _badge_scale(self) -> float:
        """角标缩放系数（以 `_REFERENCE_THUMB` 为基准）。"""
        return max(_MIN_SCALE, self.thumb / _REFERENCE_THUMB)

    # -----------------------------------------------------------
    # 显示增强（亮度 / 对比度 / 类别名叠加）
    # -----------------------------------------------------------
    def set_display(
        self,
        brightness: float = 0.0,
        contrast: float = 0.0,
        show_class_names: bool = False,
        name_width: float = 0.6,
    ) -> None:
        self.brightness = normalize_display(brightness)
        self.contrast = normalize_display(contrast)
        self.show_class_names = bool(show_class_names)
        self.name_width = max(0.25, min(1.0, float(name_width or 0.6)))
        self._adjust_cache.clear()

    def display_settings(self) -> dict:
        return {
            "brightness": self.brightness,
            "contrast": self.contrast,
            "show_class_names": self.show_class_names,
            "name_width": self.name_width,
        }

    def clear_display_cache(self) -> None:
        self._adjust_cache.clear()

    def needs_display_adjust(self) -> bool:
        return bool(self.brightness or self.contrast)

    def _display_pixmap(self, index) -> "QPixmap | None":
        """按显示参数调整后的缩略图；未启用增强时返回 None（走默认图标绘制）。

        结果按「图片 + 档位 + 参数」缓存，滚动时不会反复做整图运算。
        """
        if not self.needs_display_adjust():
            return None
        master = index.data(_MASTER_ROLE)
        if not isinstance(master, QPixmap) or master.isNull():
            return None
        size = int(self.thumb)
        key = (
            str(index.data(_PATH_ROLE) or ""), size,
            round(float(self.brightness), 3), round(float(self.contrast), 3),
        )
        cached = self._adjust_cache.get(key)
        if cached is not None:
            return cached
        scaled = master.scaled(
            size, size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        adjusted = adjust_pixmap(scaled, self.brightness, self.contrast)
        if len(self._adjust_cache) >= _ADJUST_CACHE_LIMIT:
            self._adjust_cache.clear()
        self._adjust_cache[key] = adjusted
        return adjusted

    @staticmethod
    def _thumb_rect(rect: QRectF) -> QRectF:
        """缩略图框（图片显示区域）。下边两个角标以它为锚点。"""
        return QRectF(
            rect.left() + _THUMB_INSET,
            rect.top() + _THUMB_INSET,
            rect.width() - _THUMB_INSET * 2,
            rect.height() - _THUMB_TEXT_RESERVE,
        )

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: N802
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(option.rect).adjusted(2.0, 2.0, -2.0, -2.0)

        # 角标随缩略图档位等比缩放（176px 为基准）
        scale = self._badge_scale()
        mark_size = _MARK_SIZE * scale
        subset_size = _SUBSET_SIZE * scale
        marker_size = _MARKER_SIZE * scale
        marker_gap = _MARKER_GAP * scale
        badge_inset = max(3.0, _BADGE_INSET * scale)

        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        annotated = bool(index.data(_ANNOTATED_ROLE))

        if selected:
            background, border = QColor(_COLOR_BG_SELECTED), QColor(_COLOR_BORDER_SELECTED)
        elif hovered:
            background, border = QColor(_COLOR_BG_HOVER), QColor("#414141")
        else:
            background, border = QColor(_COLOR_BG), QColor(_COLOR_BORDER)

        painter.setPen(QPen(border, 1))
        painter.setBrush(background)
        painter.drawRoundedRect(rect, 4.0, 4.0)

        # 缩略图（显示增强只作用于显示，原图不受影响）
        thumb_rect = self._thumb_rect(rect)
        shot = self._display_pixmap(index)
        if shot is not None:
            painter.drawPixmap(
                QPointF(
                    thumb_rect.center().x() - shot.width() / 2.0,
                    thumb_rect.center().y() - shot.height() / 2.0,
                ),
                shot,
            )
        else:
            icon = index.data(Qt.ItemDataRole.DecorationRole)
            if isinstance(icon, QIcon) and not icon.isNull():
                icon.paint(painter, thumb_rect.toRect(), Qt.AlignmentFlag.AlignCenter)

        # 文件名（过长时中间省略）
        font = QFont(option.font)
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1.0))
        painter.setFont(font)
        painter.setPen(QColor(_COLOR_TEXT if selected else _COLOR_TEXT_DIM))
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        text = painter.fontMetrics().elidedText(
            text, Qt.TextElideMode.ElideMiddle, max(10, int(rect.width()) - 10)
        )
        text_rect = QRectF(rect.left() + 4, rect.bottom() - 21, rect.width() - 8, 17)
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            text,
        )

        # 已标注角标：右上角实心三角，颜色取自该图所属类别颜色
        # （挂在卡片右上角，不压到图片显示区域）
        if annotated:
            path = QPainterPath()
            path.moveTo(rect.right() - 1, rect.top() + 1)
            path.lineTo(rect.right() - 1 - mark_size, rect.top() + 1)
            path.lineTo(rect.right() - 1, rect.top() + 1 + mark_size)
            path.closeSubpath()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(str(index.data(_COLOR_ROLE) or "") or _COLOR_MARK))
            painter.drawPath(path)

        # 所属数据集标记：贴着缩略图框右下角
        subset = str(index.data(_SUBSET_ROLE) or "")
        if subset:
            box = QRectF(
                thumb_rect.right() - badge_inset - subset_size,
                thumb_rect.bottom() - badge_inset - subset_size,
                subset_size,
                subset_size,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(
                QColor(SPLIT_COLORS.get(_SUBSET_KEY.get(subset, ""), "#4A5459"))
            )
            painter.drawRoundedRect(box, max(2.0, 3.0 * scale), max(2.0, 3.0 * scale))
            badge_font = QFont(option.font)
            badge_font.setPointSizeF(max(6.0, _SUBSET_FONT * scale))
            badge_font.setBold(True)
            painter.setFont(badge_font)
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, subset)

        # 图像标记：贴着缩略图框左下角（最多 4 个）
        markers = index.data(_MARKERS_ROLE) or []
        if isinstance(markers, (list, tuple)) and markers:
            left = thumb_rect.left() + badge_inset
            top = thumb_rect.bottom() - badge_inset - marker_size
            painter.setPen(Qt.PenStyle.NoPen)
            for position, color in enumerate(list(markers)[:_MAX_MARKERS]):
                painter.setBrush(QColor(str(color) or _COLOR_TAG_EMPTY))
                painter.drawEllipse(
                    QRectF(left + position * (marker_size + marker_gap),
                           top, marker_size, marker_size)
                )

        self._paint_class_name(painter, option, index, thumb_rect, scale)
        self._paint_eval(painter, option, index, thumb_rect, scale)
        painter.restore()

    def _paint_class_name(self, painter, option, index, thumb_rect: QRectF, scale: float) -> None:
        """缩略图左上角叠加类别名（宽度按比例可调，参照 DLT 的类别名框）。"""
        if not self.show_class_names:
            return
        info = index.data(_EVAL_ROLE)
        if isinstance(info, dict) and info:
            return      # 评估页左上角已是对错徽标，避免重叠
        name = str(index.data(_CLASSNAME_ROLE) or "")
        if not name:
            return
        font = QFont(option.font)
        font.setPointSizeF(max(6.5, 9.0 * scale))
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        width = max(34.0, thumb_rect.width() * self.name_width)
        height = float(metrics.height() + 4)
        box = QRectF(
            thumb_rect.left() + 2.0, thumb_rect.top() + 2.0, width, height
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 170))
        painter.drawRoundedRect(box, 3.0, 3.0)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(
            box.adjusted(5.0, 0, -5.0, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            metrics.elidedText(
                name, Qt.TextElideMode.ElideRight, max(10, int(width) - 10)
            ),
        )

    def _paint_eval(self, painter, option, index, thumb_rect: QRectF, scale: float) -> None:
        """评估标记：对错边框 + 左上角对错徽标 + 底部置信度条。"""
        info = index.data(_EVAL_ROLE)
        if not isinstance(info, dict) or not info:
            return
        correct = bool(info.get("correct"))
        color = QColor(_COLOR_EVAL_OK if correct else _COLOR_EVAL_BAD)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(color, max(1.4, 2.0 * scale)))
        painter.drawRoundedRect(thumb_rect.adjusted(0.5, 0.5, -0.5, -0.5), 4.0, 4.0)

        # 左上角对错徽标（用线段绘制，避免依赖字体里的对勾字形）
        badge = max(11.0, 17.0 * scale)
        box = QRectF(thumb_rect.left() + 2.0, thumb_rect.top() + 2.0, badge, badge)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(box)
        painter.setPen(QPen(QColor("#FFFFFF"), max(1.2, 1.8 * scale)))
        if correct:
            path = QPainterPath()
            path.moveTo(box.left() + badge * 0.26, box.top() + badge * 0.54)
            path.lineTo(box.left() + badge * 0.45, box.top() + badge * 0.72)
            path.lineTo(box.left() + badge * 0.76, box.top() + badge * 0.30)
            painter.drawPath(path)
        else:
            inset = badge * 0.30
            painter.drawLine(
                QPointF(box.left() + inset, box.top() + inset),
                QPointF(box.right() - inset, box.bottom() - inset),
            )
            painter.drawLine(
                QPointF(box.right() - inset, box.top() + inset),
                QPointF(box.left() + inset, box.bottom() - inset),
            )

        # 底部置信度条（缩略图内，半透明底）
        strip_h = max(12.0, 16.0 * scale)
        strip = QRectF(
            thumb_rect.left(), thumb_rect.bottom() - strip_h,
            thumb_rect.width(), strip_h,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 160))
        painter.drawRect(strip)

        text_font = QFont(option.font)
        text_font.setPointSizeF(max(6.5, 9.0 * scale))
        text_font.setBold(True)
        painter.setFont(text_font)
        painter.setPen(QColor("#FFFFFF"))
        text = f"{float(info.get('conf') or 0):.3f}"
        painter.drawText(
            strip.adjusted(4.0, 0, -4.0, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            text,
        )
        label = str(info.get("label") or "")
        if label:
            painter.setPen(color.lighter(160))
            painter.drawText(
                strip.adjusted(4.0, 0, -4.0, 0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                painter.fontMetrics().elidedText(
                    label, Qt.TextElideMode.ElideRight,
                    max(10, int(thumb_rect.width()) - 46),
                ),
            )


class ThumbnailGrid(QListWidget):
    """图片缩略图网格。"""

    imageActivated = Signal(int)        # 当前项变化（点击 / 键盘）
    imageDoubleClicked = Signal(int)    # 双击
    imageContextMenu = Signal(int, object)   # 右键：(行号, 全局坐标)
    thumbSizeChanged = Signal(int)      # Ctrl + 滚轮调整尺寸后广播（供页面同步滑杆）

    def __init__(self, parent=None):
        super().__init__(parent)
        # Ctrl + 滚轮缩放默认关闭，由需要它的页面打开（避免影响其它页面的固定尺寸）
        self._wheel_zoom = False
        # 本网格允许的缩放档位（页面可用 set_thumb_steps 收窄，与滑杆共用）
        self._steps: tuple[int, ...] = THUMB_STEPS
        self._delegate = ThumbnailDelegate(self)
        self.setItemDelegate(self._delegate)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setSelectionMode(QListView.SelectionMode.ExtendedSelection)
        self.setUniformItemSizes(True)
        self.setSpacing(3)
        self.setMouseTracking(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.set_thumb_size(THUMB_MEDIUM)

        self.currentRowChanged.connect(self._on_row_changed)
        self.itemDoubleClicked.connect(self._on_double_clicked)

    # -----------------------------------------------------------
    # 数据
    # -----------------------------------------------------------
    def set_images(
        self,
        paths: list,
        annotated: list | None = None,
        colors: list | None = None,
        subsets: list | None = None,
        markers: list | None = None,
        evals: list | None = None,
        classnames: list | None = None,
    ) -> None:
        """重建网格。

        Args:
            paths: 图片路径序列。
            annotated: 与 paths 等长的「是否已标注」序列，可为 None。
            colors: 与 paths 等长的类别颜色序列（右上角角标着色）。
            subsets: 与 paths 等长的数据集标记序列（T / V / E，右下角显示）。
            markers: 与 paths 等长的标记颜色列表（左下角色点）。
            evals: 与 paths 等长的评估标记（{conf, correct, label}），模型评估页使用。
            classnames: 与 paths 等长的类别名（开启「显示类别名」后叠加在缩略图上）。
        """

        def pick(values, index, default=""):
            return values[index] if values and index < len(values) else default

        self.blockSignals(True)
        self.clear()
        for index, raw in enumerate(paths):
            path = Path(raw)
            master = self._load_master(path)
            item = QListWidgetItem(self._icon(master), path.name)
            item.setData(_PATH_ROLE, str(path))
            item.setData(_MASTER_ROLE, master)
            item.setData(_ANNOTATED_ROLE, bool(pick(annotated, index, False)))
            item.setData(_COLOR_ROLE, str(pick(colors, index, "")))
            item.setData(_SUBSET_ROLE, str(pick(subsets, index, "")))
            item.setData(_MARKERS_ROLE, list(pick(markers, index, [])))
            item.setData(_EVAL_ROLE, pick(evals, index, {}))
            item.setData(_CLASSNAME_ROLE, str(pick(classnames, index, "")))
            item.setToolTip(self._tooltip(item))
            self.addItem(item)
        self.blockSignals(False)
        self.doItemsLayout()

    def set_annotated(
        self,
        flags: list,
        colors: list | None = None,
        markers: list | None = None,
        subsets: list | None = None,
    ) -> None:
        """就地更新角标（图片集合不变时用，避免重建全部缩略图）。"""
        for index in range(self.count()):
            item = self.item(index)
            item.setData(
                _ANNOTATED_ROLE, bool(flags[index]) if index < len(flags) else False
            )
            if colors is not None and index < len(colors):
                item.setData(_COLOR_ROLE, str(colors[index]))
            if markers is not None and index < len(markers):
                item.setData(_MARKERS_ROLE, list(markers[index]))
            if subsets is not None and index < len(subsets):
                item.setData(_SUBSET_ROLE, str(subsets[index]))
            item.setToolTip(self._tooltip(item))
        self.viewport().update()

    def set_class_names(self, names: list) -> None:
        """就地更新缩略图上叠加的类别名（图片集合不变时用）。"""
        for index in range(self.count()):
            item = self.item(index)
            item.setData(
                _CLASSNAME_ROLE, str(names[index]) if index < len(names) else ""
            )
        self.viewport().update()

    # -----------------------------------------------------------
    # 显示增强（亮度 / 对比度 / 类别名叠加，仅影响显示）
    # -----------------------------------------------------------
    def set_display(
        self,
        brightness: float = 0.0,
        contrast: float = 0.0,
        show_class_names: bool = False,
        name_width: float = 0.6,
    ) -> None:
        self._delegate.set_display(
            brightness, contrast, show_class_names, name_width
        )
        self.viewport().update()

    def display_settings(self) -> dict:
        return self._delegate.display_settings()

    @staticmethod
    def _tooltip(item) -> str:
        """悬停提示：路径 + 标注状态 + 所属数据集（评估页为预测信息）。"""
        lines = [str(item.data(_PATH_ROLE) or "")]
        info = item.data(_EVAL_ROLE)
        if isinstance(info, dict) and info:
            label = str(info.get("label") or "—")
            lines.append(f"预测：{label} {float(info.get('conf') or 0):.3f}")
            lines.append("正确" if info.get("correct") else "错误")
            return "\n".join(lines)
        lines.append("已标注" if item.data(_ANNOTATED_ROLE) else "未标注")
        subset = str(item.data(_SUBSET_ROLE) or "")
        if subset:
            lines.append(f"数据集：{_SUBSET_NAME.get(subset, subset)}")
        return "\n".join(lines)

    def path_at(self, index: int) -> str:
        item = self.item(index)
        return str(item.data(_PATH_ROLE) or "") if item is not None else ""

    def paths(self) -> list[str]:
        return [self.path_at(i) for i in range(self.count())]

    def selected_indexes(self) -> list[int]:
        """当前选中的行号（没有选中时退化为当前项）。"""
        rows = sorted(self.row(item) for item in self.selectedItems())
        if rows:
            return rows
        current = self.current_index()
        return [current] if current >= 0 else []

    def selected_paths(self) -> list[str]:
        """当前选中的图片路径。"""
        return [self.path_at(index) for index in self.selected_indexes()]

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """右键：先让光标下的项成为选中项，再广播菜单请求。"""
        item = self.itemAt(event.pos())
        if item is None:
            return
        if not item.isSelected():
            self.clearSelection()
            item.setSelected(True)
            self.setCurrentItem(item)
        self.imageContextMenu.emit(self.row(item), event.globalPos())
        event.accept()

    def set_current_index(self, index: int) -> None:
        """外部同步当前项（不触发 imageActivated）。"""
        if index < 0 or index >= self.count():
            return
        self.blockSignals(True)
        self.setCurrentRow(index)
        self.blockSignals(False)

    def current_index(self) -> int:
        return self.currentRow()

    # -----------------------------------------------------------
    # 显示
    # -----------------------------------------------------------
    def set_thumb_size(self, size: int) -> None:
        """调整缩略图尺寸档位，并重新缩放已有图标。"""
        size = int(size)
        if size == self._delegate.thumb:
            return
        self._delegate.thumb = size
        self._delegate.clear_display_cache()      # 档位变了，旧的显示增强缓存作废
        self.setIconSize(QSize(size, size))
        self.setGridSize(QSize(size + 18, size + 42))
        for index in range(self.count()):
            master = self.item(index).data(_MASTER_ROLE)
            if isinstance(master, QPixmap) and not master.isNull():
                self.item(index).setIcon(self._icon(master))
        self.doItemsLayout()

    def thumb_size(self) -> int:
        return self._delegate.thumb

    def set_thumb_steps(self, steps) -> None:
        """限定本网格允许的缩放档位（升序去重；非法时回退为全部档位）。

        Ctrl + 滚轮与页面上的尺寸滑杆**共用这一套档位**，页面据此设置滑杆范围，
        避免两侧档位数量不一致导致滑杆指到错误位置。
        """
        values = sorted({int(s) for s in (steps or []) if int(s) > 0})
        self._steps = tuple(values) or THUMB_STEPS
        if self.thumb_size() not in self._steps:
            self.set_thumb_size(self._steps[self.thumb_index()])

    def thumb_steps(self) -> tuple[int, ...]:
        return self._steps

    def thumb_index(self) -> int:
        """当前尺寸在档位表中的下标（取最近档位）。"""
        return min(
            range(len(self._steps)),
            key=lambda i: abs(self._steps[i] - self.thumb_size()),
        )

    # -----------------------------------------------------------
    # Ctrl + 滚轮缩放
    # -----------------------------------------------------------
    def set_wheel_zoom(self, enabled: bool) -> None:
        """是否允许 Ctrl + 滚轮调整缩略图尺寸。"""
        self._wheel_zoom = bool(enabled)

    def zoom(self, delta: int) -> bool:
        """按本网格的档位缩放缩略图（delta 为 +1 / -1）。返回档位是否变化。"""
        sizes = self._steps
        current = self.thumb_size()
        index = self.thumb_index()
        target = max(0, min(len(sizes) - 1, index + int(delta)))
        if sizes[target] == current:
            return False
        self.set_thumb_size(sizes[target])
        self.thumbSizeChanged.emit(sizes[target])
        return True

    def _handle_wheel(self, event) -> bool:
        """Ctrl + 滚轮缩放；返回是否已处理（未处理则交给默认滚动）。"""
        if event.type() != QEvent.Type.Wheel:
            return False
        if not self._wheel_zoom:
            return False
        if not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            return False
        self.zoom(1 if event.angleDelta().y() > 0 else -1)
        event.accept()
        return True

    def viewportEvent(self, event) -> bool:  # noqa: N802 - Qt 命名
        """真实鼠标滚轮由视口接收，必须在这里拦截才生效。"""
        if self._handle_wheel(event):
            return True
        return super().viewportEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """Ctrl + 滚轮缩放缩略图；否则交给父类滚动列表。"""
        if not self._handle_wheel(event):
            super().wheelEvent(event)

    # -----------------------------------------------------------
    # 内部
    # -----------------------------------------------------------
    def _on_row_changed(self, row: int) -> None:
        if row >= 0:
            self.imageActivated.emit(row)

    def _on_double_clicked(self, item) -> None:
        self.imageDoubleClicked.emit(self.row(item))

    @staticmethod
    def _load_master(path: Path) -> QPixmap:
        """读取并按最高档位预缩放；命中缓存时直接复用（带容量上限）。"""
        key = str(path)
        cached = _MASTER_CACHE.get(key)
        if cached is not None:
            return cached

        pixmap = QPixmap(key)
        if pixmap.isNull():
            return QPixmap()
        master = pixmap.scaled(
            MASTER_SIZE, MASTER_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        if len(_MASTER_CACHE) >= _MASTER_CACHE_LIMIT:
            _MASTER_CACHE.clear()
        _MASTER_CACHE[key] = master
        return master

    @staticmethod
    def clear_cache() -> None:
        """清空缩略图缓存（数据集整体更换后调用，及时释放内存）。"""
        _MASTER_CACHE.clear()

    def _icon(self, master) -> QIcon:
        if not isinstance(master, QPixmap) or master.isNull():
            return QIcon()
        size = self._delegate.thumb
        return QIcon(
            master.scaled(
                size, size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
