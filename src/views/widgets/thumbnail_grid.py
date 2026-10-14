"""缩略图网格：图库 / 图像标注 / 标注检查三页共用的图片排布视图。

参照 Halcon DLT 的图库视图：
    * 图片以卡片网格密集排布，卡片下方显示文件名
    * 已标注的图片右上角带角标，颜色取自该图所属类别颜色
    * 右下角以 T / V / E 标示所属数据集（训练 / 验证 / 测试）；
      悬停标签有提示，点击任一标签 → **全部**标签展开为「训练 / 验证 / 测试」
      文字横条（再点收起）
    * 选中项高亮描边

**万张级性能约定（§4.89，改动前先读本节）**：`QListWidget` 只承载**轻量条目
数据**（路径 + 角标数据），像素解码与缩放**在绘制时按需进行**、结果落在有界
LRU 缓存里：

* **禁止**把解码后的 pixmap / QIcon 存进每个 item（历史实现每项常驻约 0.4 MB
  —— 352px 主图 363 KB + 124px 图标 45 KB，1 万张约 **3.9 GB**，且每次进页
  全量重解码 20+ 秒）；
* tooltip **按需计算**（悬停时算一条，见 `viewportEvent`）——历史实现每次刷新
  为 1 万个 item 重建 tooltip；
* 解码走 `QImageReader.setScaledSize`（JPEG 可按比例快速降采样），**只缩不放**；
* 可见区（含上下各一行余量）由 `_schedule_warm()` **在线程池里预热**：
  解码产出 `QImage`（可跨线程），回到 GUI 线程转 `QPixmap` 入缓存——
  滚动 / 筛选都不阻塞输入，过期代次的结果直接丢弃。
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QObject,
    QPointF,
    QRectF,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QImageReader,
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
    QToolTip,
)

from src.utils.constants import SPLIT_COLORS
from src.utils.image_ops import adjust_pixmap, normalize_display
from src.views.ui import BusyPopup
from src.views.ui import tokens as T

# 条目数据角色（**只存轻量数据**：路径 / 角标 / 类别名；不存 pixmap，见文件头）
_PATH_ROLE = Qt.ItemDataRole.UserRole
_ANNOTATED_ROLE = Qt.ItemDataRole.UserRole + 1
_COLOR_ROLE = Qt.ItemDataRole.UserRole + 3      # 类别颜色（已标注角标用）
_SUBSET_ROLE = Qt.ItemDataRole.UserRole + 4     # 所属数据集：T / V / E
_MARKERS_ROLE = Qt.ItemDataRole.UserRole + 5    # 图像标记颜色列表
_EVAL_ROLE = Qt.ItemDataRole.UserRole + 6       # 评估标记 {conf, correct, label}
_CLASS_ROLE = Qt.ItemDataRole.UserRole + 7      # 类别名（右上角标签展开时显示）

# 子集标记 → SPLIT_COLORS 的键 / 悬停提示名 / 展开标签文字
_SUBSET_KEY = {"T": "train", "V": "val", "E": "test"}
_SUBSET_NAME = {"T": "train（训练）", "V": "val（验证）", "E": "test（测试）"}
_SUBSET_LABEL = {"T": "训练", "V": "验证", "E": "测试"}
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

# 角标展开态（文字胶囊）：贴图片框右上 / 右下角内侧、右对齐，与收起态角标同位；
# 胶囊**宽度按文字长度自适应**（避免固定长宽导致短文字拖长 / 长文字不够）
_TAG_H = 18.0               # 胶囊高度（基准，档位缩放下限 12）
_TAG_MAX_RATIO = 0.86       # 胶囊最大宽度（占缩略图框比例；再长则文字省略）
_TAG_PAD_H = 8.0            # 胶囊内文字的水平留白（基准）
_TAG_MIN_PAD = 6.0          # 水平留白下限（小档位下仍保留呼吸感）
_TAG_SLACK = 2.0            # 宽度容差：字体 hinting 取整会让 int 度量比 float 大 ~1px

# 缩略图尺寸档位（界面上的 小 / 中 / 大 / 特大）
THUMB_SMALL = 88
THUMB_MEDIUM = 124
THUMB_LARGE = 176
THUMB_XLARGE = 352         # 特大档：最大档再大一倍
THUMB_STEPS = (THUMB_SMALL, THUMB_MEDIUM, THUMB_LARGE, THUMB_XLARGE)

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

# 缩略图缓存：按「路径 + 档位」缓存**已缩到档位**的 pixmap，绘制时按需填充。
# 键含档位 → 换档位不会串用旧尺寸；容量按**字节**上限 LRU 淘汰（外加条数上限
# 兜底），因此 352px 大档也不会把内存吃满（历史实现：192 条上限 + 超限整表清空，
# 1 万张下等于没有缓存）。
_THUMB_CACHE_MAX_BYTES = 192 * 1024 * 1024
_THUMB_CACHE_MAX_ITEMS = 4000
_THUMB_CACHE: "OrderedDict[tuple[str, int], QPixmap]" = OrderedDict()
_THUMB_CACHE_BYTES = 0

# 显示增强（亮度 / 对比度）结果的缓存上限：按「图片 + 档位 + 参数」缓存
_ADJUST_CACHE_LIMIT = 600


def _pixmap_bytes(pixmap: QPixmap) -> int:
    """位图占用的近似字节数（宽 × 高 × 4）。"""
    return int(pixmap.width()) * int(pixmap.height()) * 4


def _cache_get(key):
    """取缓存（命中即刷新 LRU 位置）。"""
    pixmap = _THUMB_CACHE.get(key)
    if pixmap is None:
        return None
    _THUMB_CACHE.move_to_end(key)
    return pixmap


def _cache_put(key, pixmap: QPixmap) -> None:
    """写缓存并按字节 / 条数上限淘汰最久未用的条目。"""
    global _THUMB_CACHE_BYTES
    if pixmap is None or pixmap.isNull():
        return
    old = _THUMB_CACHE.pop(key, None)
    if old is not None:
        _THUMB_CACHE_BYTES -= _pixmap_bytes(old)
    _THUMB_CACHE[key] = pixmap
    _THUMB_CACHE_BYTES += _pixmap_bytes(pixmap)
    while _THUMB_CACHE and (
        _THUMB_CACHE_BYTES > _THUMB_CACHE_MAX_BYTES
        or len(_THUMB_CACHE) > _THUMB_CACHE_MAX_ITEMS
    ):
        _evicted_key, evicted = _THUMB_CACHE.popitem(last=False)
        _THUMB_CACHE_BYTES -= _pixmap_bytes(evicted)


def clear_thumb_cache() -> None:
    """清空缩略图缓存（数据集整体更换 / 自检用）。"""
    global _THUMB_CACHE_BYTES
    _THUMB_CACHE.clear()
    _THUMB_CACHE_BYTES = 0


def load_thumb_image(path: str, size: int) -> "QImage":
    """解码并缩放到指定档位，返回 `QImage`（**只缩不放**）。

    走 `QImageReader.setScaledSize`：JPEG 可按比例快速降采样，避免「全尺寸解码
    再 Smooth 缩放」的双重开销与峰值内存（原图 1920×1080 单张约 23 ms，降到
    档位解码通常只要几毫秒）。不设置 `autoTransform`——与标注画布 / 原实现
    （`QPixmap(path)`）保持同一取向，避免缩略图与画布方向不一致。

    `QImage` 可在**工作线程**里构造（`QPixmap` 不行），后台预热走这个函数。
    """
    reader = QImageReader(str(path))
    source = reader.size()
    if source.isValid() and source.width() > 0 and source.height() > 0:
        target = source.scaled(
            QSize(int(size), int(size)), Qt.AspectRatioMode.KeepAspectRatio
        )
        if target.width() < source.width() or target.height() < source.height():
            reader.setScaledSize(target)
    return reader.read()


def load_thumb(path: str, size: int) -> QPixmap:
    """解码并缩放到指定档位（**只缩不放**；结果由调用方入缓存）。"""
    image = load_thumb_image(path, size)
    return QPixmap() if image.isNull() else QPixmap.fromImage(image)


class _WarmSignals(QObject):
    """后台解码结果的跨线程回传（`QRunnable` 自身不能带信号）。"""

    decoded = Signal(str, int, int, object)     # 路径 / 档位 / 代次 / QImage


class _DecodeJob(QRunnable):
    """一次缩略图后台解码（只做 `QImage` 解码与缩放，不碰界面）。"""

    def __init__(self, path: str, size: int, generation: int, signals: _WarmSignals):
        super().__init__()
        self._path = path
        self._size = int(size)
        self._generation = int(generation)
        self._signals = signals

    def run(self) -> None:  # noqa: D102 - QRunnable 约定
        try:
            image = load_thumb_image(self._path, self._size)
        except Exception:  # noqa: BLE001 - 后台解码失败不该带崩线程池
            image = None
        if image is not None and not image.isNull():
            self._signals.decoded.emit(
                self._path, self._size, self._generation, image
            )


class ThumbnailDelegate(QStyledItemDelegate):
    """绘制缩略图卡片。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.thumb = THUMB_MEDIUM
        # 显示增强：只影响显示，不写回文件（图库 / 检查页用）
        self.brightness = 0.0
        self.contrast = 0.0
        self.show_subset_text = False      # 数据集标签（右下角）展开为文字
        self.show_class_text = False       # 类别标签（右上角）展开为类别名
        self._adjust_cache: dict = {}

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 - Qt 命名
        return QSize(self.thumb + 18, self.thumb + 42)

    def _badge_scale(self) -> float:
        """角标缩放系数（以 `_REFERENCE_THUMB` 为基准）。"""
        return max(_MIN_SCALE, self.thumb / _REFERENCE_THUMB)

    # -----------------------------------------------------------
    # 显示增强（亮度 / 对比度）
    # -----------------------------------------------------------
    def set_display(
        self,
        brightness: float = 0.0,
        contrast: float = 0.0,
    ) -> None:
        self.brightness = normalize_display(brightness)
        self.contrast = normalize_display(contrast)
        self._adjust_cache.clear()

    def display_settings(self) -> dict:
        return {
            "brightness": self.brightness,
            "contrast": self.contrast,
        }

    def clear_display_cache(self) -> None:
        self._adjust_cache.clear()

    def prewarm(self, indexes, step=None) -> None:
        """预计算这些项的显示增强缓存（避免 paint 阶段集中运算卡界面）。

        ``step(done, total)`` 可选，用于耗时操作上报进度（见 ``BusyPopup``）。
        """
        if not self.needs_display_adjust():
            return
        total = len(indexes)
        for position, index in enumerate(indexes):
            if index.isValid():
                self._display_pixmap(index)
            if step is not None:
                step(position + 1, total)

    def needs_display_adjust(self) -> bool:
        return bool(self.brightness or self.contrast)

    def thumb_pixmap(self, index) -> "QPixmap | None":
        """档位缩略图（**按需解码**，结果入有界 LRU；不做显示增强）。

        条目里不再存 pixmap：绘制时按「路径 + 档位」取缓存，未命中才解码——
        1 万张也只有可见的几十张会被解码。
        """
        path = str(index.data(_PATH_ROLE) or "")
        if not path:
            return None
        size = int(self.thumb)
        key = (path, size)
        cached = _cache_get(key)
        if cached is not None:
            return cached
        pixmap = load_thumb(path, size)
        if pixmap.isNull():
            return None
        _cache_put(key, pixmap)
        return pixmap

    def _display_pixmap(self, index) -> "QPixmap | None":
        """绘制用缩略图：档位缩略图 + 可选显示增强（亮度 / 对比度）。

        增强结果按「图片 + 档位 + 参数」缓存，滚动时不会反复做整图运算。
        """
        base = self.thumb_pixmap(index)
        if base is None:
            return None
        if not self.needs_display_adjust():
            return base
        key = (
            str(index.data(_PATH_ROLE) or ""), int(self.thumb),
            round(float(self.brightness), 3), round(float(self.contrast), 3),
        )
        cached = self._adjust_cache.get(key)
        if cached is not None:
            return cached
        adjusted = adjust_pixmap(base, self.brightness, self.contrast)
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

        # 右上角类别标签：收起 = 实心三角（卡片右上角）；展开 = 类别名胶囊
        # （贴图片右上角内侧，与收起态同位），颜色取自该图所属类别颜色
        if annotated:
            class_color = QColor(str(index.data(_COLOR_ROLE) or "") or _COLOR_MARK)
            class_name = str(index.data(_CLASS_ROLE) or "")
            if self.show_class_text and class_name:
                self._paint_tag(
                    painter, class_name, class_color,
                    self._tag_rect(thumb_rect, scale, "top", class_name), scale,
                )
            else:
                self._paint_mark_triangle(painter, rect, class_color, mark_size)

        # 右下角数据集标签：收起 = 字母方块；展开 = 「训练 / 验证 / 测试」胶囊
        # （贴图片右下角内侧，与收起态同位）
        subset = str(index.data(_SUBSET_ROLE) or "")
        if subset:
            color = QColor(SPLIT_COLORS.get(_SUBSET_KEY.get(subset, ""), "#4A5459"))
            if self.show_subset_text:
                label = _SUBSET_LABEL.get(subset, subset)
                self._paint_tag(
                    painter, label, color,
                    self._tag_rect(thumb_rect, scale, "bottom", label), scale,
                )
            else:
                self._paint_subset_badge(
                    painter, option, subset, color, thumb_rect, scale,
                    badge_inset, subset_size,
                )

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

        self._paint_eval(painter, option, index, thumb_rect, scale)
        painter.restore()

    def _paint_subset_badge(
        self, painter, option, subset: str, color: QColor,
        thumb_rect: QRectF, scale: float, badge_inset: float, subset_size: float,
    ) -> None:
        """收起态：缩略图框右下角的字母方块（T / V / E）。"""
        box = QRectF(
            thumb_rect.right() - badge_inset - subset_size,
            thumb_rect.bottom() - badge_inset - subset_size,
            subset_size,
            subset_size,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(box, max(2.0, 3.0 * scale), max(2.0, 3.0 * scale))
        badge_font = QFont(option.font)
        badge_font.setPointSizeF(max(6.0, _SUBSET_FONT * scale))
        badge_font.setBold(True)
        painter.setFont(badge_font)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, subset)

    def _paint_mark_triangle(self, painter, rect: QRectF, color: QColor, size: float) -> None:
        """收起态：卡片右上角的已标注三角。"""
        path = QPainterPath()
        path.moveTo(rect.right() - 1, rect.top() + 1)
        path.lineTo(rect.right() - 1 - size, rect.top() + 1)
        path.lineTo(rect.right() - 1, rect.top() + 1 + size)
        path.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPath(path)

    def _tag_font(self, scale: float) -> QFont:
        """标签文字字体（绘制与宽度度量共用，保证胶囊长度与文字相符）。"""
        parent = self.parent()
        font = QFont(parent.font() if parent is not None else QFont())
        font.setPointSizeF(max(6.5, 9.0 * scale))
        font.setBold(True)
        return font

    @staticmethod
    def _tag_pad(scale: float) -> float:
        """标签胶囊内文字的水平留白（小档位下有下限）。"""
        return max(_TAG_MIN_PAD, _TAG_PAD_H * scale)

    def _tag_rect(
        self, thumb_rect: QRectF, scale: float, corner: str, text: str
    ) -> QRectF:
        """展开态标签胶囊矩形：**宽度按文字长度自适应**，右对齐贴图片框角内侧。

        绘制与命中检测共用同一几何（含同一字体度量），保证「点哪儿亮哪儿」，
        且胶囊长度与文字相符——短文字不拖长，超长文字限流到 ``_TAG_MAX_RATIO``
        后由绘制端省略。
        """
        height = max(12.0, _TAG_H * scale)
        pad = self._tag_pad(scale)
        # 用 int 度量（与绘制端 painter.fontMetrics 同源）+ 容差：避免 hinting
        # 取整造成「胶囊按 float 度量刚好够、绘制端却判定放不下」而误省略成 …
        text_w = QFontMetrics(self._tag_font(scale)).horizontalAdvance(text)
        width = text_w + pad * 2 + _TAG_SLACK
        width = min(width, thumb_rect.width() * _TAG_MAX_RATIO)
        width = max(width, height * 1.5)        # 下限：至少一个小圆胶囊
        inset = max(3.0, _BADGE_INSET * scale)
        top = (
            thumb_rect.top() + inset if corner == "top"
            else thumb_rect.bottom() - inset - height
        )
        return QRectF(thumb_rect.right() - inset - width, top, width, height)

    def _paint_tag(
        self, painter, text: str, color: QColor, box: QRectF, scale: float
    ) -> None:
        """标签胶囊：圆角底 + 居中白字（宽度已按文字自适应，超长才省略）。"""
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        radius = box.height() / 2.0
        painter.drawRoundedRect(box, radius, radius)
        painter.setFont(self._tag_font(scale))
        painter.setPen(QColor("#FFFFFF"))
        metrics = painter.fontMetrics()
        avail = int(box.width() - self._tag_pad(scale) * 2)
        # 宽度已按文字自适应（含容差）：只有超长文字（限流到 _TAG_MAX_RATIO
        # 之后仍放不下）才省略——否则完整显示，避免退化成一个 …
        shown = text
        if metrics.horizontalAdvance(text) > avail:
            shown = metrics.elidedText(text, Qt.TextElideMode.ElideRight, avail)
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, shown)

    def subset_hit_rect(self, rect: QRectF, subset: str) -> QRectF:
        """数据集标签的点击 / 悬停命中区域（与绘制一致，略放大便于点中）。"""
        scale = self._badge_scale()
        thumb_rect = self._thumb_rect(rect)
        if self.show_subset_text:
            label = _SUBSET_LABEL.get(subset, subset)
            return self._tag_rect(thumb_rect, scale, "bottom", label).adjusted(
                -3.0, -4.0, 3.0, 4.0
            )
        subset_size = _SUBSET_SIZE * scale
        badge_inset = max(3.0, _BADGE_INSET * scale)
        box = QRectF(
            thumb_rect.right() - badge_inset - subset_size,
            thumb_rect.bottom() - badge_inset - subset_size,
            subset_size,
            subset_size,
        )
        return box.adjusted(-3.0, -3.0, 3.0, 3.0)

    def class_hit_rect(self, rect: QRectF, class_name: str = "") -> QRectF:
        """右上角类别标签的命中区域（收起 = 三角；展开 = 类别名胶囊）。"""
        scale = self._badge_scale()
        thumb_rect = self._thumb_rect(rect)
        if self.show_class_text and class_name:
            return self._tag_rect(thumb_rect, scale, "top", class_name).adjusted(
                -3.0, -4.0, 3.0, 4.0
            )
        size = _MARK_SIZE * scale
        box = QRectF(rect.right() - 1 - size, rect.top() + 1, size, size)
        return box.adjusted(-3.0, -3.0, 3.0, 3.0)

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

    #: 超过这个张数才给载入进度弹窗（小集合不该闪一下）
    BUSY_THRESHOLD = 1500

    #: 后台预热线程数（2 足够：按屏排队，滚动时才补下一屏）
    _WARM_MAX_THREADS = 2
    #: 一次最多排队的行数（防止快速滚动时把队列灌爆）
    _WARM_MAX_JOBS = 80

    def __init__(self, parent=None):
        super().__init__(parent)
        # Ctrl + 滚轮缩放默认关闭，由需要它的页面打开（避免影响其它页面的固定尺寸）
        self._wheel_zoom = False
        # 本网格允许的缩放档位（页面可用 set_thumb_steps 收窄，与滑杆共用）
        self._steps: tuple[int, ...] = THUMB_STEPS
        # 当前图片路径表（`paths()` 直接返回它，避免每次刷新重建 1 万个字符串）
        self._paths: list[str] = []
        # 可见区**后台**预热：解码在线程池（QImage 可跨线程），结果回到 GUI 线程
        # 转 pixmap 入缓存；`_warm_generation` 用来丢弃滚动 / 换图后的过期结果
        self._warm_generation = 0
        self._warm_signals = _WarmSignals(self)
        self._warm_signals.decoded.connect(self._on_warm_decoded)
        self._warm_pool = QThreadPool(self)
        self._warm_pool.setMaxThreadCount(self._WARM_MAX_THREADS)
        self._delegate = ThumbnailDelegate(self)
        self.setItemDelegate(self._delegate)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setSelectionMode(QListView.SelectionMode.ExtendedSelection)
        self.setUniformItemSizes(True)
        self.setSpacing(T.SPACE_XS)
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
            classnames: 与 paths 等长的类别名（右上角标签展开时显示）。
        """

        def pick(values, index, default=""):
            return values[index] if values and index < len(values) else default

        self.blockSignals(True)
        self.setUpdatesEnabled(False)
        # 大集合给进度反馈（弹窗自身有 150ms 延迟，小集合不会闪）：万张级下
        # 「点一下没反应」最伤体验，哪怕只是建条目也要让用户看到在动（§4.89）
        total = len(paths)
        busy = (
            BusyPopup.scope(self.window(), f"正在载入 {total} 张缩略图")
            if total >= self.BUSY_THRESHOLD else None
        )
        try:
            self.clear()
            for index, raw in enumerate(paths):
                path = Path(raw)
                # 轻量条目：只有路径 + 角标数据；像素在绘制时按需解码（见文件头）
                item = QListWidgetItem(path.name)
                item.setData(_PATH_ROLE, str(path))
                item.setData(_ANNOTATED_ROLE, bool(pick(annotated, index, False)))
                item.setData(_COLOR_ROLE, str(pick(colors, index, "")))
                item.setData(_SUBSET_ROLE, str(pick(subsets, index, "")))
                item.setData(_MARKERS_ROLE, list(pick(markers, index, [])))
                item.setData(_EVAL_ROLE, pick(evals, index, {}))
                item.setData(_CLASS_ROLE, str(pick(classnames, index, "")))
                self.addItem(item)
                if busy is not None and index % 500 == 0:
                    busy.step(index + 1, total)
        finally:
            if busy is not None:
                busy.end()
            self.setUpdatesEnabled(True)
            self.blockSignals(False)
        self._paths = [str(raw) for raw in paths]
        self.doItemsLayout()
        self._schedule_warm()

    def set_annotated(
        self,
        flags: list,
        colors: list | None = None,
        markers: list | None = None,
        subsets: list | None = None,
        classnames: list | None = None,
    ) -> None:
        """就地更新角标（图片集合不变时用，避免重建全部缩略图）。

        只写条目数据 + 整体重绘一次：**不重建 tooltip**（悬停时按需算，见
        `viewportEvent`）——历史实现每次刷新为 1 万个 item 重建 tooltip，
        单这一步就是秒级。
        """
        self.blockSignals(True)
        self.setUpdatesEnabled(False)
        try:
            for index in range(self.count()):
                item = self.item(index)
                item.setData(
                    _ANNOTATED_ROLE,
                    bool(flags[index]) if index < len(flags) else False,
                )
                if colors is not None and index < len(colors):
                    item.setData(_COLOR_ROLE, str(colors[index]))
                if markers is not None and index < len(markers):
                    item.setData(_MARKERS_ROLE, list(markers[index]))
                if subsets is not None and index < len(subsets):
                    item.setData(_SUBSET_ROLE, str(subsets[index]))
                if classnames is not None and index < len(classnames):
                    item.setData(_CLASS_ROLE, str(classnames[index]))
        finally:
            self.setUpdatesEnabled(True)
            self.blockSignals(False)
        self.viewport().update()

    # -----------------------------------------------------------
    # 显示增强（亮度 / 对比度，仅影响显示）
    # -----------------------------------------------------------
    def set_display(
        self,
        brightness: float = 0.0,
        contrast: float = 0.0,
    ) -> None:
        self._delegate.set_display(brightness, contrast)
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
        if item.data(_ANNOTATED_ROLE):
            name = str(item.data(_CLASS_ROLE) or "")
            lines.append(f"类别：{name or '—'}")
        subset = str(item.data(_SUBSET_ROLE) or "")
        if subset:
            lines.append(f"数据集：{_SUBSET_NAME.get(subset, subset)}")
        return "\n".join(lines)

    def path_at(self, index: int) -> str:
        item = self.item(index)
        return str(item.data(_PATH_ROLE) or "") if item is not None else ""

    def paths(self) -> list[str]:
        """当前图片路径（缓存副本；`set_images` 时一次写好，刷新时不做 O(N) 遍历）。"""
        return list(self._paths)

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

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """点击类别（右上）/ 数据集（右下）标签：展开或收起**全部**同类标签。"""
        if event.button() == Qt.MouseButton.LeftButton:
            item = self.itemAt(event.pos())
            if item is not None:
                if self._hit_class(item, event.pos()):
                    self.set_class_text(not self._delegate.show_class_text)
                    event.accept()
                    return
                if self._hit_subset(item, event.pos()):
                    self.set_subset_text(not self._delegate.show_subset_text)
                    event.accept()
                    return
        super().mousePressEvent(event)

    def _option_rect(self, item) -> QRectF:
        """与 delegate 绘制一致的卡片矩形。"""
        return QRectF(self.visualRect(self.indexFromItem(item))).adjusted(
            2.0, 2.0, -2.0, -2.0
        )

    def _hit_class(self, item, pos) -> bool:
        """位置是否落在右上角类别标签上（未标注 / 未命中返回 False）。"""
        if not bool(item.data(_ANNOTATED_ROLE)):
            return False
        name = str(item.data(_CLASS_ROLE) or "")
        return self._delegate.class_hit_rect(
            self._option_rect(item), name
        ).contains(QPointF(pos))

    def _hit_subset(self, item, pos) -> bool:
        """位置是否落在右下角数据集标签上（无标签 / 未命中返回 False）。"""
        subset = str(item.data(_SUBSET_ROLE) or "")
        if not subset:
            return False
        return self._delegate.subset_hit_rect(
            self._option_rect(item), subset
        ).contains(QPointF(pos))

    def _class_tooltip(self, pos) -> str:
        """悬停右上角类别标签时的提示；标签外返回空串。"""
        item = self.itemAt(pos)
        if item is None or not self._hit_class(item, pos):
            return ""
        name = str(item.data(_CLASS_ROLE) or "")
        action = "收起标签" if self._delegate.show_class_text else "展开全部标签"
        return f"类别：{name or '—'}（点击{action}）"

    def _subset_tooltip(self, pos) -> str:
        """悬停右下角数据集标签时的提示；标签外返回空串（走默认整卡提示）。"""
        item = self.itemAt(pos)
        if item is None or not self._hit_subset(item, pos):
            return ""
        subset = str(item.data(_SUBSET_ROLE) or "")
        action = "收起标签" if self._delegate.show_subset_text else "展开全部标签"
        return f"数据集：{_SUBSET_NAME.get(subset, subset)}（点击{action}）"

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
        """调整缩略图尺寸档位（**O(1)**：条目里没有图标要重设）。

        档位进缓存键，换档位后旧尺寸条目自然随 LRU 淘汰；这里只清显示增强缓存
        （它按参数缓存，档位变了就作废）、重排布局并重绘可见区——历史实现在这里
        为 1 万个 item 重设 QIcon（秒级卡顿 + BusyPopup）。
        """
        size = int(size)
        if size == self._delegate.thumb:
            return
        self._delegate.thumb = size
        self._delegate.clear_display_cache()      # 档位变了，旧的显示增强缓存作废
        self.setIconSize(QSize(size, size))
        self.setGridSize(self._grid_size())
        self.doItemsLayout()
        self.viewport().update()
        self._schedule_warm()

    def thumb_size(self) -> int:
        return self._delegate.thumb

    def _grid_size(self) -> QSize:
        """格子尺寸（标签展开态贴在图片角落内，不改动卡片尺寸）。"""
        return QSize(int(self._delegate.thumb + 18), int(self._delegate.thumb + 42))

    def set_subset_text(self, shown: bool) -> None:
        """展开 / 收起右下角数据集标签文字（点击任一标签切换，对全部图像生效）。"""
        shown = bool(shown)
        if shown == self._delegate.show_subset_text:
            return
        self._delegate.show_subset_text = shown
        self.viewport().update()

    def subset_text_shown(self) -> bool:
        return bool(self._delegate.show_subset_text)

    def set_class_text(self, shown: bool) -> None:
        """展开 / 收起右上角类别标签文字（点击任一标签切换，对全部图像生效）。"""
        shown = bool(shown)
        if shown == self._delegate.show_class_text:
            return
        self._delegate.show_class_text = shown
        self.viewport().update()

    def class_text_shown(self) -> bool:
        return bool(self._delegate.show_class_text)

    def prewarm_visible(self, title: str = "正在应用显示效果") -> None:
        """预热**可见项**的显示增强缓存（应用显示参数后调用）。

        集中在这里一次算完并报进度，避免 paint 阶段逐张现算造成卡顿；
        未启用显示增强时直接返回（零开销）。
        """
        if not self._delegate.needs_display_adjust():
            return
        viewport = self.viewport().rect()
        visible = [
            self.indexFromItem(self.item(row))
            for row in range(self.count())
            if self.visualItemRect(self.item(row)).intersects(viewport)
        ]
        if not visible:
            return
        with BusyPopup.scope(self.window(), title) as busy:
            self._delegate.prewarm(visible, busy.step)

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
        """真实鼠标滚轮由视口接收，必须在这里拦截才生效。

        悬停提示也在这里**按需**生成：先给类别 / 数据集标签的命中提示，否则给
        该图片的路径 / 标注状态 / 数据集提示——条目上不再预存 tooltip。
        """
        if self._handle_wheel(event):
            return True
        if event.type() == QEvent.Type.ToolTip:
            text = self._class_tooltip(event.pos()) or self._subset_tooltip(
                event.pos()
            )
            if not text:
                item = self.itemAt(event.pos())
                if item is not None:
                    text = self._tooltip(item)
            if text:
                QToolTip.showText(event.globalPos(), text, self)
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

    # -----------------------------------------------------------
    # 可见区预热（分片，不阻塞输入）
    # -----------------------------------------------------------
    def _visible_rows(self, margin_rows: int = 3) -> list[int]:
        """可见行的行号（上下各带几行余量：预热要**提前一屏**备好，滚动才顺）。"""
        viewport = self.viewport().rect()
        visible = []
        for row in range(self.count()):
            rect = self.visualItemRect(self.item(row))
            if rect.bottom() < viewport.top() - rect.height() * margin_rows:
                continue
            if rect.top() > viewport.bottom() + rect.height() * margin_rows:
                break
            visible.append(row)
        return visible

    def _schedule_warm(self) -> None:
        """安排可见区（含上下各一行余量）的后台解码。

        解码放线程池（`QImage` 可跨线程；`QPixmap` 必须在 GUI 线程建），
        因此滚动时**不占用主线程**。已缓存的行直接跳过；一次最多排
        `_WARM_MAX_JOBS` 行，滚动时随窗口移动重新排队（旧代次的回包被丢弃）。
        """
        size = int(self._delegate.thumb)
        jobs = 0
        self._warm_generation += 1
        generation = self._warm_generation
        for row in self._visible_rows():
            if jobs >= self._WARM_MAX_JOBS:
                break
            item = self.item(row)
            if item is None:
                continue
            path = str(item.data(_PATH_ROLE) or "")
            if not path or _cache_get((path, size)) is not None:
                continue                        # 已缓存：无需再解码
            self._warm_pool.start(_DecodeJob(path, size, generation, self._warm_signals))
            jobs += 1

    def _on_warm_decoded(
        self, path: str, size: int, generation: int, image
    ) -> None:
        """后台解码完成（GUI 线程）：转 pixmap 入缓存并重绘。"""
        if generation != self._warm_generation:
            return                              # 滚动 / 换图后的过期结果
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            return
        _cache_put((str(path), int(size)), pixmap)
        if int(size) == int(self._delegate.thumb):
            self.viewport().update()

    def scrollContentsBy(self, dx: int, dy: int) -> None:  # noqa: N802 - Qt 命名
        """滚动时把预热批次换成新的可见行（滚动本身的绘制不受影响）。"""
        super().scrollContentsBy(dx, dy)
        self._schedule_warm()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """页面显示 / 隐藏时重排预热（不可见时不做无用解码）。"""
        super().showEvent(event)
        if self.isVisible():
            self._schedule_warm()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """视口尺寸变化（换档位 / 窗口缩放）后重排预热。"""
        super().resizeEvent(event)
        self._schedule_warm()

    @staticmethod
    def clear_cache() -> None:
        """清空缩略图缓存（数据集整体更换后调用，及时释放内存）。"""
        clear_thumb_cache()
