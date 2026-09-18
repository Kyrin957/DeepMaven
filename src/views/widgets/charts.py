"""轻量图表组件：环形占比图与水平分布条（QPainter 自绘，深色主题）。

用于「数据拆分」页展示划分比例与类别分布，避免引入 Matplotlib 的重依赖
和风格不一致问题。
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

_COLOR_TEXT = "#E8E8E8"
_COLOR_TEXT_DIM = "#909090"
_COLOR_TRACK = "#333333"
_COLOR_EMPTY = "#5A5A5A"


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
