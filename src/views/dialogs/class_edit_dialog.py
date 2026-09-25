"""类别编辑弹窗（参照 Halcon DLT 的类别编辑窗口）。

    ColorPicker      自选颜色面板：色相(横) × 明度(纵) 主色板 + 饱和度竖条
    ClassEditDialog  类别名称 / 颜色 / 快捷键，「新增」与「编辑」共用同一弹窗
"""

from __future__ import annotations

from PySide6.QtCore import QLineF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CaptionLabel,
    LineEdit,
    MessageBoxBase,
    PushButton,
    SubtitleLabel,
)

from src.views.ui import tokens as T

# 常用配色（一键选色；真正想要的颜色可在色板上自选）
PRESET_COLORS = (
    "#005FB8", "#0F7B0F", "#C42B1C", "#8764B8",
    "#C29300", "#00838F", "#D13438", "#E3008C",
)
DEFAULT_COLOR = "#66CCFF"


class ColorPicker(QWidget):
    """自选颜色面板。

    左侧方块横向为色相、纵向为明度；右侧竖条为饱和度。
    点击 / 拖动即可取色，并通过 ``colorChanged`` 广播 ``#RRGGBB``。
    """

    colorChanged = Signal(str)

    _BAR_WIDTH = 20
    _GAP = 10

    def __init__(self, color: str = DEFAULT_COLOR, parent=None):
        super().__init__(parent)
        self.setMinimumSize(280, 148)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._hue = 0.55
        self._sat = 0.6
        self._val = 1.0
        self._area = ""
        self.set_color(color, notify=False)

    # -----------------------------------------------------------
    # 几何
    # -----------------------------------------------------------
    def _square_rect(self) -> QRectF:
        width = max(60.0, float(self.width() - self._BAR_WIDTH - self._GAP))
        return QRectF(0.0, 0.0, width, float(self.height()))

    def _bar_rect(self) -> QRectF:
        width = float(self._BAR_WIDTH)
        return QRectF(float(self.width()) - width, 0.0, width, float(self.height()))

    # -----------------------------------------------------------
    # 颜色读写
    # -----------------------------------------------------------
    def color(self) -> QColor:
        return QColor.fromHsvF(min(self._hue, 0.999), self._sat, self._val)

    def color_hex(self) -> str:
        return self.color().name().upper()

    def set_color(self, value, notify: bool = True) -> None:
        """按 ``#RRGGBB`` 设定当前颜色（无效值忽略）。"""
        color = QColor(value)
        if not color.isValid():
            return
        hue = color.hueF()
        if hue >= 0:                      # 灰色 / 黑白没有色相，保留原色相
            self._hue = max(0.0, min(0.999, hue))
        self._sat = max(0.0, min(1.0, color.saturationF()))
        self._val = max(0.0, min(1.0, color.valueF()))
        self.update()
        if notify:
            self.colorChanged.emit(self.color_hex())

    # -----------------------------------------------------------
    # 交互
    # -----------------------------------------------------------
    def _pick(self, position) -> None:
        if self._area == "square":
            square = self._square_rect()
            self._hue = max(
                0.0, min(0.999, (position.x() - square.left()) / max(1.0, square.width()))
            )
            self._val = max(
                0.0,
                min(1.0, 1.0 - (position.y() - square.top()) / max(1.0, square.height())),
            )
        elif self._area == "bar":
            bar = self._bar_rect()
            self._sat = max(
                0.0, min(1.0, 1.0 - (position.y() - bar.top()) / max(1.0, bar.height()))
            )
        else:
            return
        self.update()
        self.colorChanged.emit(self.color_hex())

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        position = event.position()
        if self._square_rect().contains(position):
            self._area = "square"
        elif self._bar_rect().contains(position):
            self._area = "bar"
        else:
            self._area = ""
        self._pick(position)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if self._area and event.buttons():
            self._pick(event.position())

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._area = ""

    # -----------------------------------------------------------
    # 绘制
    # -----------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        square = self._square_rect()
        bar = self._bar_rect()

        # 主色板：横向色相彩虹
        hue = QLinearGradient(square.left(), 0.0, square.right(), 0.0)
        for step in range(7):
            ratio = step / 6.0
            hue.setColorAt(
                ratio, QColor.fromHsvF(min(0.999, ratio * 0.999), 1.0, 1.0)
            )
        painter.fillRect(square, hue)
        # 主色板：纵向明度（上白 → 纯色 → 下黑）
        shade = QLinearGradient(0.0, square.top(), 0.0, square.bottom())
        shade.setColorAt(0.0, QColor(255, 255, 255, 235))
        shade.setColorAt(0.5, QColor(0, 0, 0, 0))
        shade.setColorAt(1.0, QColor(0, 0, 0, 255))
        painter.fillRect(square, shade)

        # 饱和度竖条：纯色 → 灰
        level = max(0.25, self._val)
        saturation = QLinearGradient(0.0, bar.top(), 0.0, bar.bottom())
        saturation.setColorAt(0.0, QColor.fromHsvF(min(self._hue, 0.999), 1.0, level))
        saturation.setColorAt(1.0, QColor.fromHsvF(min(self._hue, 0.999), 0.0, level))
        painter.fillRect(bar, saturation)

        painter.setPen(QPen(QColor("#4A4A4A"), 1))
        painter.drawRect(square)
        painter.drawRect(bar)

        # 当前取值标记
        painter.setPen(QPen(QColor("#FFFFFF"), 2))
        marker_x = square.left() + self._hue * square.width()
        marker_y = square.top() + (1.0 - self._val) * square.height()
        painter.drawRect(QRectF(marker_x - 5.0, marker_y - 5.0, 10.0, 10.0))
        bar_y = bar.top() + (1.0 - self._sat) * bar.height()
        painter.drawLine(QLineF(bar.left(), bar_y, bar.right(), bar_y))


class ClassEditDialog(MessageBoxBase):
    """类别编辑弹窗（新增 / 编辑共用）。

    用法：
        dialog = ClassEditDialog(parent, name="good", color="#FFFF00")
        if dialog.exec():
            data = dialog.result_data()      # {"name": ..., "color": "#RRGGBB"}
    """

    def __init__(
        self,
        parent=None,
        name: str = "",
        color: str = DEFAULT_COLOR,
        cls_id: int | None = None,
        taken=(),
    ):
        super().__init__(parent)
        self._taken = {str(item).strip().lower() for item in taken if str(item).strip()}
        self._editing = cls_id is not None

        self.titleLabel = SubtitleLabel(
            "编辑类别" if self._editing else "新增类别", self
        )
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self._build_body(name, color, cls_id))

        # 该标签只用于操作反馈，不放说明文字
        self.hint = CaptionLabel("", self)
        self.hint.setWordWrap(True)
        self.viewLayout.addWidget(self.hint)

        self.widget.setMinimumWidth(620)
        self.yesButton.setText("确定")
        self.cancelButton.setText("取消")
        # 接管确认按钮：先校验再关闭
        try:
            self.yesButton.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.yesButton.clicked.connect(self._on_confirm)

    # -----------------------------------------------------------
    def _build_body(self, name: str, color: str, cls_id: int | None) -> QWidget:
        holder = QWidget(self)
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(T.SPACE_XL)

        form = QVBoxLayout()
        form.setSpacing(T.SPACE_SM)

        form.addWidget(CaptionLabel("名称", holder))
        self.name_edit = LineEdit(holder)
        self.name_edit.setText(name)
        self.name_edit.setPlaceholderText("输入类别名称")
        form.addWidget(self.name_edit)

        form.addWidget(CaptionLabel("颜色", holder))
        self.color_edit = LineEdit(holder)
        self.color_edit.setText(QColor(color).name().upper())
        self.color_edit.editingFinished.connect(self._on_color_text)
        form.addWidget(self.color_edit)

        presets = QHBoxLayout()
        presets.setSpacing(T.SPACE_XS)
        for value in PRESET_COLORS:
            presets.addWidget(self._preset_button(value, holder))
        presets.addStretch(1)
        form.addLayout(presets)

        form.addWidget(CaptionLabel("快捷键", holder))
        self.shortcut_edit = LineEdit(holder)
        self.shortcut_edit.setReadOnly(True)
        self.shortcut_edit.setText(
            f"自动分配：{cls_id + 1}" if cls_id is not None else "新增后自动分配"
        )
        form.addWidget(self.shortcut_edit)
        form.addStretch(1)
        row.addLayout(form, 1)

        self.picker = ColorPicker(color, holder)
        self.picker.colorChanged.connect(self._on_picked)
        row.addWidget(self.picker, 1)
        return holder

    def _preset_button(self, value: str, parent) -> PushButton:
        button = PushButton(parent)
        button.setFixedSize(T.ICON_BTN_SM, T.ICON_BTN_SM)
        button.setToolTip(value)
        button.setStyleSheet(
            f"PushButton {{ background: {value}; border: 1px solid #5A5A5A;"
            " border-radius: 3px; padding: 0; }"
            "PushButton:hover { border: 1px solid #FFFFFF; }"
        )
        button.clicked.connect(lambda _=False, v=value: self._apply(v))
        return button

    # -----------------------------------------------------------
    def _apply(self, value: str) -> None:
        self.picker.set_color(value)
        self.color_edit.setText(self.picker.color_hex())
        self.hint.setText(f"已选择颜色 {self.picker.color_hex()}")

    def _on_picked(self, value: str) -> None:
        self.color_edit.setText(value)
        self.hint.setText(f"已选择颜色 {value}")

    def _on_color_text(self) -> None:
        text = self.color_edit.text().strip()
        if not text:
            return
        if not QColor(text).isValid():
            self.hint.setText("颜色值无效，形如 #66CCFF")
            return
        self.picker.set_color(text)

    def _on_confirm(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            self.hint.setText("请输入类别名称")
            return
        if name.lower() in self._taken:
            self.hint.setText(f"类别「{name}」已存在，请换一个名称")
            return
        self.accept()

    # -----------------------------------------------------------
    def result_data(self) -> dict:
        return {
            "name": self.name_edit.text().strip(),
            "color": self.picker.color_hex(),
        }
