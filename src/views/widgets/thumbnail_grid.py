"""缩略图网格：图库 / 图像标注 / 标注检查三页共用的图片排布视图。

参照 Halcon DLT 的图库视图：
    * 图片以卡片网格密集排布，卡片下方显示文件名
    * 已标注的图片右上角带角标，颜色取自该图所属类别颜色
    * 右下角以 T / V / E 标示所属数据集（训练 / 验证 / 测试）
    * 选中项高亮描边
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, Signal
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

# 条目数据角色
_PATH_ROLE = Qt.ItemDataRole.UserRole
_ANNOTATED_ROLE = Qt.ItemDataRole.UserRole + 1
_MASTER_ROLE = Qt.ItemDataRole.UserRole + 2
_COLOR_ROLE = Qt.ItemDataRole.UserRole + 3      # 类别颜色（已标注角标用）
_SUBSET_ROLE = Qt.ItemDataRole.UserRole + 4     # 所属数据集：T / V / E
_MARKERS_ROLE = Qt.ItemDataRole.UserRole + 5    # 图像标记颜色列表

# 子集标记 → SPLIT_COLORS 的键 / 显示名
_SUBSET_KEY = {"T": "train", "V": "val", "E": "test"}
_SUBSET_NAME = {"T": "train（训练）", "V": "val（验证）", "E": "test（测试）"}
_MAX_MARKERS = 4            # 左下角最多显示几个标记色点

# 缩略图尺寸档位（界面上的 小 / 中 / 大）
THUMB_SMALL = 88
THUMB_MEDIUM = 124
THUMB_LARGE = 176
MASTER_SIZE = 176          # 缓存主图的边长（内存与清晰度的折中）

# 配色
_COLOR_BG = "#2B2B2B"
_COLOR_BG_HOVER = "#343434"
_COLOR_BG_SELECTED = "#0B3D6B"
_COLOR_BORDER = "#333333"
_COLOR_BORDER_SELECTED = "#0F6CBD"
_COLOR_MARK = "#E3008C"        # 已标注角标（DLT 粉色）
_COLOR_TEXT = "#E8E8E8"
_COLOR_TEXT_DIM = "#909090"

_SUBSET_SIZE = 14.0            # 右下角 T / V / E 标记的边长
_COLOR_TAG_EMPTY = "#8A8A8A"   # 标记没有颜色时的色点颜色

# 主图缓存：同一进程内复用已解码并缩放的缩略图，避免每次刷新都重新读盘解码
_MASTER_CACHE: dict = {}
_MASTER_CACHE_LIMIT = 512


class ThumbnailDelegate(QStyledItemDelegate):
    """绘制缩略图卡片。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.thumb = THUMB_MEDIUM

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 - Qt 命名
        return QSize(self.thumb + 18, self.thumb + 42)

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: N802
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(option.rect).adjusted(2.0, 2.0, -2.0, -2.0)

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

        # 缩略图
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(icon, QIcon) and not icon.isNull():
            icon_rect = QRectF(
                rect.left() + 5, rect.top() + 5,
                rect.width() - 10, rect.height() - 28,
            )
            icon.paint(painter, icon_rect.toRect(), Qt.AlignmentFlag.AlignCenter)

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
        if annotated:
            size = 11.0
            path = QPainterPath()
            path.moveTo(rect.right() - 1, rect.top() + 1)
            path.lineTo(rect.right() - 1 - size, rect.top() + 1)
            path.lineTo(rect.right() - 1, rect.top() + 1 + size)
            path.closeSubpath()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(str(index.data(_COLOR_ROLE) or "") or _COLOR_MARK))
            painter.drawPath(path)

        # 所属数据集标记：右下角 T / V / E
        subset = str(index.data(_SUBSET_ROLE) or "")
        if subset:
            box = QRectF(
                rect.right() - 7.0 - _SUBSET_SIZE,
                rect.bottom() - 30.0 - _SUBSET_SIZE,
                _SUBSET_SIZE,
                _SUBSET_SIZE,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(
                QColor(SPLIT_COLORS.get(_SUBSET_KEY.get(subset, ""), "#4A5459"))
            )
            painter.drawRoundedRect(box, 3.0, 3.0)
            badge_font = QFont(option.font)
            badge_font.setPointSizeF(max(6.5, badge_font.pointSizeF() - 2.0))
            badge_font.setBold(True)
            painter.setFont(badge_font)
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, subset)

        # 图像标记：左下角色点（最多 4 个）
        markers = index.data(_MARKERS_ROLE) or []
        if isinstance(markers, (list, tuple)) and markers:
            size, gap = 6.0, 2.0
            left = rect.left() + 7.0
            top = rect.bottom() - 30.0 - size
            painter.setPen(Qt.PenStyle.NoPen)
            for position, color in enumerate(list(markers)[:_MAX_MARKERS]):
                painter.setBrush(QColor(str(color) or _COLOR_TAG_EMPTY))
                painter.drawEllipse(
                    QRectF(left + position * (size + gap), top, size, size)
                )

        painter.restore()


class ThumbnailGrid(QListWidget):
    """图片缩略图网格。"""

    imageActivated = Signal(int)        # 当前项变化（点击 / 键盘）
    imageDoubleClicked = Signal(int)    # 双击
    imageContextMenu = Signal(int, object)   # 右键：(行号, 全局坐标)

    def __init__(self, parent=None):
        super().__init__(parent)
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
    ) -> None:
        """重建网格。

        Args:
            paths: 图片路径序列。
            annotated: 与 paths 等长的「是否已标注」序列，可为 None。
            colors: 与 paths 等长的类别颜色序列（右上角角标着色）。
            subsets: 与 paths 等长的数据集标记序列（T / V / E，右下角显示）。
            markers: 与 paths 等长的标记颜色列表（左下角色点）。
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

    @staticmethod
    def _tooltip(item) -> str:
        """悬停提示：路径 + 标注状态 + 所属数据集。"""
        lines = [str(item.data(_PATH_ROLE) or "")]
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
        self.setIconSize(QSize(size, size))
        self.setGridSize(QSize(size + 18, size + 42))
        for index in range(self.count()):
            master = self.item(index).data(_MASTER_ROLE)
            if isinstance(master, QPixmap) and not master.isNull():
                self.item(index).setIcon(self._icon(master))
        self.doItemsLayout()

    def thumb_size(self) -> int:
        return self._delegate.thumb

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
