"""轻量图表组件：环形占比图、水平分布条与实时折线图（QPainter 自绘，深色主题）。

用于「数据拆分」页展示划分比例与类别分布、「模型训练」页展示实时训练曲线，
避免引入 Matplotlib 的重依赖和风格不一致问题。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

_COLOR_TEXT = "#E8E8E8"
_COLOR_TEXT_DIM = "#909090"
_COLOR_TRACK = "#333333"
_COLOR_EMPTY = "#5A5A5A"
_COLOR_GRID = "#3A3A3A"

# 分数直方图：按「异常样本占比」着色 + 三条参考阈值线
_COLOR_HIST_NORMAL = "#0F6CBD"      # 以正常样本为主
_COLOR_HIST_MIXED = "#B16CEA"       # 正常 / 异常混在一起
_COLOR_HIST_ABNORMAL = "#C42B1C"    # 以异常样本为主
_COLOR_HIST_MEDIAN = "#0F7B0F"      # 中位阈值
_COLOR_HIST_BEST = "#E8A33D"        # 最优阈值（F1 最大）

# 折线图配色（训练曲线用）
_COLOR_PLOT_BG = "#1B1B1B"
_COLOR_PLOT_GRID = "#333333"
_COLOR_PLOT_AXIS = "#4A4A4A"
_CHART_COLORS = ("#0F6CBD", "#0F7B0F", "#C42B1C", "#B16CEA", "#E8A33D")


class PieChart(QWidget):
    """环形图：展示各分项占比，中心显示合计。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(200)
        self._segments: list[tuple[str, float, str]] = []
        self._center_text = ""

    def set_data(
        self,
        segments: list[tuple[str, float, str]],
        center_text: str = "",
    ) -> None:
        """segments 为 (名称, 数值, 颜色) 列表。"""
        self._segments = [(name, max(0.0, float(value)), color)
                          for name, value, color in segments]
        self._center_text = center_text
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        total = sum(value for _, value, _ in self._segments)
        area = QRectF(self.rect()).adjusted(8, 8, -8, -8)
        size = min(area.width(), area.height() - 4)
        if size <= 20 or total <= 0:
            painter.setPen(QColor(_COLOR_TEXT_DIM))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "暂无数据")
            return

        box = QRectF(
            area.left() + (area.width() - size) / 2, area.top(), size, size
        )
        start = 90 * 16
        for _, value, color in self._segments:
            span = int(-value / total * 360 * 16)
            if span == 0:
                continue
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color))
            painter.drawPie(box, start, span)
            start += span

        # 中心镂空 + 合计文字
        inner = box.adjusted(size * 0.28, size * 0.28, -size * 0.28, -size * 0.28)
        painter.setBrush(QColor("#202020"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(inner)

        font = QFont(self.font())
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(_COLOR_TEXT))
        painter.drawText(inner, Qt.AlignmentFlag.AlignCenter, self._center_text)


class BarChart(QWidget):
    """水平条形图：每行一个类别，条长按占比绘制。"""

    ROW_HEIGHT = 26

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[tuple[str, float, float, str]] = []
        self.setMinimumHeight(self.ROW_HEIGHT * 3)

    def set_data(self, rows: list[tuple[str, float, float, str]]) -> None:
        """rows 为 (名称, 数值, 参考总量, 颜色) 列表。"""
        self._rows = list(rows)
        self.setMinimumHeight(max(self.ROW_HEIGHT * 3, self.ROW_HEIGHT * len(self._rows)))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if not self._rows:
            painter.setPen(QColor(_COLOR_TEXT_DIM))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "暂无数据")
            return

        label_width = 120.0
        value_width = 64.0
        left = label_width + 8
        right = max(left + 40, self.width() - value_width - 8)
        track_width = right - left

        font = QFont(self.font())
        font.setPointSizeF(max(7.5, font.pointSizeF()))
        painter.setFont(font)
        metrics = painter.fontMetrics()

        for index, (name, value, reference, color) in enumerate(self._rows):
            top = index * self.ROW_HEIGHT + 3
            bar_height = self.ROW_HEIGHT - 10

            painter.setPen(QColor(_COLOR_TEXT))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawText(
                QRectF(4, top, label_width - 8, bar_height),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                metrics.elidedText(name, Qt.TextElideMode.ElideRight, int(label_width) - 10),
            )

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(_COLOR_TRACK))
            painter.drawRoundedRect(QRectF(left, top, track_width, bar_height), 3, 3)

            ratio = (value / reference) if reference > 0 else 0.0
            width = track_width * min(1.0, max(0.0, ratio))
            if width > 0:
                painter.setBrush(QColor(color))
                painter.drawRoundedRect(QRectF(left, top, width, bar_height), 3, 3)

            painter.setPen(QColor(_COLOR_TEXT))
            painter.drawText(
                QRectF(right + 6, top, value_width - 6, bar_height),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                f"{int(value)}",
            )


class LegendList(QWidget):
    """图例列表：色块 + 名称 + 数值。"""

    ROW_HEIGHT = 24

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[tuple[str, str, str]] = []
        self.setMinimumHeight(self.ROW_HEIGHT * 3)

    def set_data(self, rows: list[tuple[str, str, str]]) -> None:
        """rows 为 (颜色, 名称, 数值文本) 列表。"""
        self._rows = list(rows)
        self.setMinimumHeight(max(self.ROW_HEIGHT * 3, self.ROW_HEIGHT * len(self._rows)))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not self._rows:
            painter.setPen(QColor(_COLOR_TEXT_DIM))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "暂无数据")
            return

        font = QFont(self.font())
        painter.setFont(font)
        metrics = painter.fontMetrics()
        for index, (color, name, value) in enumerate(self._rows):
            top = index * self.ROW_HEIGHT + 4
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(QRectF(4, top + 3, 12, 12), 2, 2)
            painter.setPen(QColor(_COLOR_TEXT))
            painter.drawText(
                QRectF(24, top, self.width() - 84, 18),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                metrics.elidedText(name, Qt.TextElideMode.ElideRight, max(20, self.width() - 90)),
            )
            painter.setPen(QColor(_COLOR_TEXT_DIM))
            painter.drawText(
                QRectF(self.width() - 62, top, 58, 18),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                value,
            )


class ConfusionMatrixView(QWidget):
    """混淆矩阵：行 = 真实类别，列 = 预测类别，对角线为正确预测。

    最后一行 / 列可选表示「误检」（无真实标签的预测）与「漏检」（未匹配的真实标签），
    与检测任务的统计口径一致。单元格按数量着色（对角线偏绿、错误偏红），
    点击单元格会发出 `cellClicked(真实下标, 预测下标)`，页面据此筛选缩略图。
    """

    cellClicked = Signal(int, int)

    MIN_HEIGHT = 240
    _CELL_MIN = 34.0
    _LABEL_W = 92.0
    _HEADER_H = 34.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(self.MIN_HEIGHT)
        self._matrix: list[list[int]] = []
        self._names: list[str] = []
        self._colors: list[str] = []
        self._boxes: list[tuple[int, int, QRectF]] = []
        self._selected: tuple[int, int] | None = None

    # -----------------------------------------------------------
    # 数据
    # -----------------------------------------------------------
    def set_data(
        self,
        matrix: list,
        class_names: list,
        colors: list | None = None,
    ) -> None:
        self._matrix = [[int(value) for value in row] for row in (matrix or [])]
        self._names = [str(name) for name in (class_names or [])]
        self._colors = list(colors or [])
        self._selected = None
        self.update()

    def selected_cell(self) -> tuple[int, int] | None:
        return self._selected

    def clear_selection(self) -> None:
        self._selected = None
        self.update()

    # -----------------------------------------------------------
    # 绘制
    # -----------------------------------------------------------
    def _color_for(self, index: int) -> str:
        if index < len(self._colors) and self._colors[index]:
            return str(self._colors[index])
        return _CHART_COLORS[index % len(_CHART_COLORS)]

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1.0))
        painter.setFont(font)
        metrics = painter.fontMetrics()

        matrix = self._matrix
        self._boxes = []
        if not matrix or not matrix[0]:
            painter.setPen(QColor(_COLOR_TEXT_DIM))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "暂无数据")
            return

        rows = len(matrix)
        cols = len(matrix[0])
        area = QRectF(self.rect())
        cell_w = max(self._CELL_MIN, (area.width() - self._LABEL_W - 8) / cols)
        cell_h = max(self._CELL_MIN, (area.height() - self._HEADER_H - 22) / rows)
        total = max(sum(sum(row) for row in matrix), 1)
        top = area.top() + self._HEADER_H

        # 列标题（预测类别）
        painter.setPen(QColor(_COLOR_TEXT_DIM))
        painter.drawText(
            QRectF(area.left(), area.top() + 2, self._LABEL_W, 14),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "真实 \\ 预测",
        )
        for col, name in enumerate(self._names):
            box = QRectF(area.left() + self._LABEL_W + col * cell_w,
                         area.top() + 16, cell_w, 16)
            painter.setPen(QColor(self._color_for(col)))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, metrics.elidedText(
                name, Qt.TextElideMode.ElideRight, int(cell_w) - 4))

        # 行 + 单元格
        for row in range(rows):
            y = top + row * cell_h
            name = self._names[row] if row < len(self._names) else ""
            label_box = QRectF(area.left(), y, self._LABEL_W - 6, cell_h)
            painter.setPen(QColor(self._color_for(row)))
            painter.drawText(
                label_box,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                metrics.elidedText(name, Qt.TextElideMode.ElideRight,
                                   int(self._LABEL_W) - 10),
            )
            for col in range(cols):
                x = area.left() + self._LABEL_W + col * cell_w
                box = QRectF(x, y, cell_w, cell_h)
                value = int(matrix[row][col]) if col < len(matrix[row]) else 0
                self._boxes.append((row, col, box))

                # 颜色：对角线绿、错误红、背景行列橙
                if row == col and col < len(self._names):
                    base = QColor(15, 123, 15, 40 + int(120 * value / total))
                elif row == len(self._names) or col == len(self._names):
                    base = QColor(196, 150, 20, 40 + int(120 * value / total))
                else:
                    base = QColor(196, 43, 28, 40 + int(120 * value / total))
                painter.fillRect(box.adjusted(1, 1, -1, -1), base)
                if self._selected == (row, col):
                    painter.setPen(QPen(QColor(_COLOR_TEXT), 2))
                    painter.drawRect(box.adjusted(1.5, 1.5, -1.5, -1.5))

                painter.setPen(QColor(_COLOR_TEXT) if value else QColor(_COLOR_TEXT_DIM))
                painter.drawText(box, Qt.AlignmentFlag.AlignCenter, str(value))

        # 底部说明
        painter.setPen(QColor(_COLOR_TEXT_DIM))
        painter.drawText(
            QRectF(area.left(), top + rows * cell_h + 4, area.width(), 16),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            f"总数 {total}",
        )

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        position = event.position()
        for row, col, box in self._boxes:
            if box.contains(position):
                self._selected = (row, col)
                self.update()
                self.cellClicked.emit(row, col)
                return
        super().mousePressEvent(event)


class HistogramView(QWidget):
    """分数直方图：按真实类别着色，并叠加当前 / 中位 / 最优阈值线。

    用于异常检测评估：直方图能直观看出正常与异常样本的重叠程度，
    拖动（或点击）即可调整分类阈值，`thresholdChanged` 广播新阈值供页面重判。
    """

    thresholdChanged = Signal(float)

    MIN_HEIGHT = 156
    _BINS = 24
    _PAD_LEFT = 30.0
    _PAD_RIGHT = 10.0
    _PAD_TOP = 12.0
    _PAD_BOTTOM = 20.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(self.MIN_HEIGHT)
        self._scores: list[dict] = []
        self._threshold = 0.0
        self._median = 0.0
        self._best = 0.0
        self._dragging = False

    # -----------------------------------------------------------
    def set_data(
        self,
        scores: list,
        threshold: float = 0.0,
        median: float = 0.0,
        best: float = 0.0,
    ) -> None:
        """scores: [{"score": 异常分数, "abnormal": 真实是否异常}, ...]。"""
        self._scores = [
            {
                "score": float(item.get("score") or 0.0),
                "abnormal": bool(item.get("abnormal")),
            }
            for item in (scores or [])
        ]
        self._threshold = float(threshold or 0.0)
        self._median = float(median or 0.0)
        self._best = float(best or 0.0)
        self.update()

    def range(self) -> tuple[float, float]:
        """横轴范围（按实际分数，最小 0~1，避免除零）。"""
        values = [item["score"] for item in self._scores]
        if not values:
            return 0.0, 1.0
        low, high = min(0.0, min(values)), max(1.0, max(values))
        return low, high

    def _value_at(self, x: float, area: QRectF) -> float:
        low, high = self.range()
        span = max(1e-9, high - low)
        ratio = min(1.0, max(0.0, (x - area.left()) / max(1.0, area.width())))
        return low + span * ratio

    # -----------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1.0))
        painter.setFont(font)

        area = QRectF(self.rect())
        plot = QRectF(
            area.left() + self._PAD_LEFT, area.top() + self._PAD_TOP,
            max(20.0, area.width() - self._PAD_LEFT - self._PAD_RIGHT),
            max(20.0, area.height() - self._PAD_TOP - self._PAD_BOTTOM),
        )
        painter.setPen(QPen(QColor(_COLOR_GRID), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(plot)

        if not self._scores:
            painter.setPen(QColor(_COLOR_TEXT_DIM))
            painter.drawText(area, Qt.AlignmentFlag.AlignCenter, "暂无数据")
            return

        low, high = self.range()
        span = max(1e-9, high - low)
        bins = [0] * self._BINS
        abnormal_bins = [0] * self._BINS
        for item in self._scores:
            index = int((item["score"] - low) / span * (self._BINS - 1e-9))
            index = max(0, min(self._BINS - 1, index))
            bins[index] += 1
            if item["abnormal"]:
                abnormal_bins[index] += 1
        peak = max(bins) or 1
        bar_width = plot.width() / self._BINS

        for index, count in enumerate(bins):
            if not count:
                continue
            height = plot.height() * count / peak
            box = QRectF(
                plot.left() + index * bar_width + 1.0,
                plot.bottom() - height,
                max(1.0, bar_width - 2.0),
                height,
            )
            ratio = abnormal_bins[index] / count
            painter.setPen(Qt.PenStyle.NoPen)
            if ratio >= 0.5:
                painter.setBrush(QColor(_COLOR_HIST_ABNORMAL))
            elif ratio > 0.0:
                painter.setBrush(QColor(_COLOR_HIST_MIXED))
            else:
                painter.setBrush(QColor(_COLOR_HIST_NORMAL))
            painter.drawRect(box)

        for value, color, dashed, label in (
            (self._median, _COLOR_HIST_MEDIAN, True, "中位"),
            (self._best, _COLOR_HIST_BEST, True, "最优"),
            (self._threshold, _COLOR_TEXT, False, ""),
        ):
            if not value:
                continue
            x = plot.left() + (value - low) / span * plot.width()
            if x < plot.left() or x > plot.right():
                continue
            pen = QPen(QColor(color), 1.6)
            pen.setStyle(Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine)
            painter.setPen(pen)
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            if label:
                painter.setPen(QColor(color))
                painter.drawText(
                    QRectF(x - 20.0, plot.top() - 11.0, 40.0, 11.0),
                    Qt.AlignmentFlag.AlignCenter, label,
                )

        painter.setPen(QColor(_COLOR_TEXT_DIM))
        painter.drawText(
            QRectF(plot.left(), plot.bottom() + 2.0, plot.width() / 2, 16.0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            f"{low:.3f}",
        )
        painter.drawText(
            QRectF(plot.center().x(), plot.bottom() + 2.0, plot.width() / 2, 16.0),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"{high:.3f}",
        )
        painter.drawText(
            QRectF(plot.left() - self._PAD_LEFT + 2.0, plot.top(), self._PAD_LEFT, 12.0),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            str(peak),
        )

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if not self._scores:
            return
        self._dragging = True
        self._emit_at(event.position().x())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if self._dragging:
            self._emit_at(event.position().x())

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._dragging = False

    def _emit_at(self, x: float) -> None:
        area = QRectF(self.rect()).adjusted(
            self._PAD_LEFT, 0.0, -self._PAD_RIGHT, 0.0
        )
        self.thresholdChanged.emit(round(self._value_at(x, area), 4))


def format_number(value: float) -> str:
    """按数量级选择小数位，避免小数值显示成 0。"""
    magnitude = abs(float(value))
    if magnitude == 0:
        return "0"
    if magnitude >= 100:
        return f"{value:.0f}"
    if magnitude >= 10:
        return f"{value:.1f}"
    if magnitude >= 1:
        return f"{value:.2f}"
    if magnitude >= 0.01:
        return f"{value:.3f}"
    if magnitude >= 1e-4:
        return f"{value:.4f}"
    return f"{value:.1e}"


class LineChart(QWidget):
    """多序列折线图：X 轴为训练轮次，Y 轴自适应，支持边训练边追加。

    用法::

        chart = LineChart(y_label="Loss")
        chart.set_data([
            ("训练", "#0F6CBD", [(1, 1.2), (2, 0.8)]),
            ("验证", "#0F7B0F", [(1, 1.4), (2, 0.9)]),
        ])

    数据为空时显示空状态；鼠标悬停会显示该轮次各序列的读数。
    """

    MIN_HEIGHT = 186

    def __init__(self, parent=None, x_label: str = "Epoch", y_label: str = ""):
        super().__init__(parent)
        self.setMinimumHeight(self.MIN_HEIGHT)
        self.setMouseTracking(True)
        self._series: list[tuple[str, str, list[tuple[float, float]]]] = []
        self._x_label = x_label
        self._y_label = y_label
        self._hover_x: float | None = None
        self._layout = None

    # ----------------------------------------------------------- 数据
    def set_data(
        self,
        series: list[tuple[str, str, list[tuple[float, float]]]],
        x_label: str | None = None,
        y_label: str | None = None,
    ) -> None:
        """替换全部序列并重绘。series 为 (名称, 颜色, [(x, y), ...]) 列表。"""
        cleaned: list[tuple[str, str, list[tuple[float, float]]]] = []
        for index, (name, color, points) in enumerate(series or []):
            items = [(float(x), float(y)) for x, y in (points or [])]
            if not items:
                continue
            cleaned.append((
                str(name),
                str(color) or _CHART_COLORS[index % len(_CHART_COLORS)],
                items,
            ))
        self._series = cleaned
        if x_label is not None:
            self._x_label = x_label
        if y_label is not None:
            self._y_label = y_label
        self.update()

    def clear(self) -> None:
        self._series = []
        self._hover_x = None
        self._layout = None
        self.update()

    def has_data(self) -> bool:
        return any(points for _, _, points in self._series)

    # ----------------------------------------------------------- 悬停读数
    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        x = self._nearest_x(event.position().x())
        if x != self._hover_x:
            self._hover_x = x
            self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if self._hover_x is not None:
            self._hover_x = None
            self.update()

    def _nearest_x(self, px: float) -> float | None:
        if self._layout is None:
            return None
        box, x_min, x_max, _, _ = self._layout
        if box.width() <= 0:
            return None
        ratio = min(1.0, max(0.0, (px - box.left()) / box.width()))
        target = x_min + (x_max - x_min) * ratio
        values = {x for _, _, points in self._series for x, _ in points}
        if not values:
            return None
        return min(values, key=lambda item: abs(item - target))

    # ----------------------------------------------------------- 绘制
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        area = QRectF(self.rect())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(_COLOR_PLOT_BG))
        painter.drawRoundedRect(area.adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)

        font = QFont(self.font())
        font.setPointSizeF(max(7.5, font.pointSizeF() - 0.5))
        painter.setFont(font)
        metrics = painter.fontMetrics()

        if not self.has_data():
            self._layout = None
            painter.setPen(QColor(_COLOR_TEXT_DIM))
            painter.drawText(area, Qt.AlignmentFlag.AlignCenter, "暂无数据")
            return

        legend_height = 20.0
        box = QRectF(
            area.left() + 58.0,
            area.top() + legend_height + 6.0,
            max(20.0, area.width() - 58.0 - 16.0),
            max(20.0, area.height() - legend_height - 44.0),
        )

        xs = [x for _, _, points in self._series for x, _ in points]
        ys = [y for _, _, points in self._series for _, y in points]
        x_min, x_max = min(xs), max(xs)
        if x_max - x_min < 1e-9:
            x_min, x_max = x_min - 0.5, x_max + 0.5
        y_min, y_max = min(0.0, min(ys)), max(ys)
        if y_max - y_min < 1e-9:
            y_max = y_min + 1.0
        y_max += (y_max - y_min) * 0.08
        self._layout = (box, x_min, x_max, y_min, y_max)

        def px_of(value: float) -> float:
            return box.left() + (value - x_min) / (x_max - x_min) * box.width()

        def py_of(value: float) -> float:
            return box.bottom() - (value - y_min) / (y_max - y_min) * box.height()

        # 网格 + Y 轴刻度
        grid_pen = QPen(QColor(_COLOR_PLOT_GRID), 1)
        axis_pen = QPen(QColor(_COLOR_PLOT_AXIS), 1)
        ticks = 4
        for index in range(ticks + 1):
            value = y_min + (y_max - y_min) * index / ticks
            y = py_of(value)
            painter.setPen(axis_pen if index == 0 else grid_pen)
            painter.drawLine(QPointF(box.left(), y), QPointF(box.right(), y))
            painter.setPen(QColor(_COLOR_TEXT_DIM))
            painter.drawText(
                QRectF(area.left() + 2, y - 8, 50, 16),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                format_number(value),
            )

        # X 轴刻度与轴名
        x_ticks = min(6, max(2, int(round(x_max - x_min))))
        for index in range(x_ticks + 1):
            value = x_min + (x_max - x_min) * index / x_ticks
            painter.setPen(QColor(_COLOR_TEXT_DIM))
            painter.drawText(
                QRectF(px_of(value) - 20, box.bottom() + 2, 40, 15),
                Qt.AlignmentFlag.AlignCenter,
                f"{value:.0f}",
            )
        painter.setPen(QColor(_COLOR_TEXT_DIM))
        painter.drawText(
            QRectF(box.right() - 80, box.bottom() + 18, 80, 15),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            self._x_label,
        )
        if self._y_label:
            painter.drawText(
                QRectF(area.left() + 6, area.top() + 2, 90, 16),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                self._y_label,
            )

        # 图例（右上角，从右往左排）
        legend_right = box.right()
        for name, color, _ in reversed(self._series):
            text_width = metrics.horizontalAdvance(name)
            entry_width = text_width + 26
            left = legend_right - entry_width
            if left < box.left():
                break
            center_y = area.top() + legend_height / 2 + 2
            painter.setPen(QPen(QColor(color), 2))
            painter.drawLine(
                QPointF(left, center_y), QPointF(left + 14, center_y)
            )
            painter.setPen(QColor(_COLOR_TEXT))
            painter.drawText(
                QRectF(left + 18, area.top() + 3, text_width + 6, legend_height),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                name,
            )
            legend_right = left - 14

        # 曲线
        for name, color, points in self._series:
            path = QPainterPath()
            for index, (x, y) in enumerate(points):
                point = QPointF(px_of(x), py_of(y))
                if index == 0:
                    path.moveTo(point)
                else:
                    path.lineTo(point)
            painter.setPen(QPen(
                QColor(color), 1.8,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            ))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
            if len(points) == 1:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(color))
                painter.drawEllipse(
                    QPointF(px_of(points[0][0]), py_of(points[0][1])), 3.0, 3.0
                )

        if self._hover_x is not None:
            self._draw_hover(painter, box, px_of, py_of, metrics)

    def _draw_hover(self, painter, box, px_of, py_of, metrics) -> None:
        """悬停读数：竖线 + 各序列取值 + 数值面板。"""
        x = px_of(self._hover_x)
        painter.setPen(QPen(QColor("#6A6A6A"), 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(x, box.top()), QPointF(x, box.bottom()))

        entries: list[tuple[str, str]] = []
        for name, color, points in self._series:
            value = next(
                (y for point_x, y in points if abs(point_x - self._hover_x) < 1e-9),
                None,
            )
            if value is None:
                continue
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color))
            painter.drawEllipse(QPointF(x, py_of(value)), 2.8, 2.8)
            entries.append((color, f"{name} {format_number(value)}"))

        if not entries:
            return
        width = max(metrics.horizontalAdvance(text) for _, text in entries) + 18
        panel = QRectF(box.left() + 6, box.top() + 6, width, len(entries) * 15 + 8)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 170))
        painter.drawRoundedRect(panel, 4, 4)
        for index, (color, text) in enumerate(entries):
            painter.setPen(QColor(color))
            painter.drawText(
                QRectF(panel.left() + 8, panel.top() + 4 + index * 15,
                       panel.width() - 16, 15),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                text,
            )
