"""窗口右侧显示调试栏（参照 Halcon DLT 的右侧图标栏）。

以**竖直图标按钮 + 悬浮面板（Flyout）**呈现图像显示调试项：
    亮度 / 对比度 / 类别名叠加 / 缩略图尺寸（可选） / 复位

**只影响显示，不改动图片数据。** 界面不写解释性文字，全部说明走 tooltip
（见 `.codebuddy/rules/ui-design/RULE.mdc` 的「文案与提示」规范）。

接口与横向显示条一致：``changed`` / ``values()`` / ``reset()``；
带尺寸时另有 ``thumbSizeChanged``（档位下标）与 ``set_thumb_size()``
（按像素同步档位，供 Ctrl + 滚轮缩放后回填）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    CheckBox,
    FluentIcon,
    Flyout,
    FlyoutViewBase,
    Slider,
    TransparentToolButton,
)

from src.views.ui import tokens as T
from src.views.ui.containers import icon_button


class _SliderPanel(FlyoutViewBase):
    """单个显示参数的悬浮面板：滑杆 + 读数。"""

    valueChanged = Signal(int)

    def __init__(
        self,
        minimum: int,
        maximum: int,
        value: int,
        text_for=None,
        tooltip: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._text_for = text_for or (lambda item: f"{item:+d}" if item else "0")
        row = QHBoxLayout(self)
        row.setContentsMargins(T.CARD_PAD_H, T.SPACE_ML, T.CARD_PAD_H, T.SPACE_MD)
        row.setSpacing(T.SPACE_MD)
        self.slider = Slider(Qt.Orientation.Horizontal, self)
        self.slider.setRange(int(minimum), int(maximum))
        self.slider.setValue(int(value))
        self.slider.setFixedWidth(T.CTRL_W_MD)
        if tooltip:
            self.slider.setToolTip(tooltip)
        row.addWidget(self.slider)
        self.readout = CaptionLabel(self._text_for(int(value)), self)
        self.readout.setToolTip(tooltip)
        row.addWidget(self.readout)
        self.setMinimumWidth(T.PANEL_MIN_W)
        self.slider.valueChanged.connect(self._on_value)

    def _on_value(self, value: int) -> None:
        self.readout.setText(self._text_for(int(value)))
        self.valueChanged.emit(int(value))


class _NamesPanel(FlyoutViewBase):
    """类别名叠加面板：勾选 + 名称框宽度。"""

    changed = Signal()

    def __init__(self, checked: bool, width_value: int, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(T.CARD_PAD_H, T.SPACE_ML, T.CARD_PAD_H, T.SPACE_MD)
        layout.setSpacing(T.SPACE_ML)
        self.check = CheckBox("类别名", self)
        self.check.setChecked(bool(checked))
        self.check.setToolTip("在缩略图上叠加类别名")
        layout.addWidget(self.check)

        row = QHBoxLayout()
        row.setSpacing(T.SPACE_MD)
        self.width_slider = Slider(Qt.Orientation.Horizontal, self)
        self.width_slider.setRange(25, 100)
        self.width_slider.setValue(int(width_value))
        self.width_slider.setFixedWidth(T.CTRL_W_MD)
        self.width_slider.setToolTip("类别名框宽度（占缩略图比例）")
        row.addWidget(self.width_slider)
        self.readout = CaptionLabel(f"{int(width_value)}%", self)
        self.readout.setToolTip("类别名框宽度（占缩略图比例）")
        row.addWidget(self.readout)
        layout.addLayout(row)
        self.setMinimumWidth(T.PANEL_MIN_W)

        self.check.toggled.connect(lambda _checked: self.changed.emit())
        self.width_slider.valueChanged.connect(self._on_width)

    def _on_width(self, value: int) -> None:
        self.readout.setText(f"{int(value)}%")
        self.changed.emit()


class DisplayDock(QFrame):
    """窗口右侧显示调试栏：竖直图标按钮 + 悬浮面板。

    参数（与旧横向显示条一致）：
        values() -> {"brightness", "contrast", "show_class_names", "name_width"}
        亮度 / 对比度为 -1.0 ~ 1.0 的比例值；name_width 为 0.25 ~ 1.0。
    """

    changed = Signal()
    thumbSizeChanged = Signal(int)      # 档位下标（与网格 / 旧滑杆一致）

    def __init__(self, parent=None, with_size: bool = False):
        super().__init__(parent)
        self.setObjectName("DisplayDock")
        self._thumb_steps: tuple[int, ...] = ()
        self._thumb_index = 0
        self._brightness = 0        # -100 ~ 100
        self._contrast = 0          # -100 ~ 100
        self._show_names = False
        self._name_width = 60       # 25 ~ 100

        layout = QVBoxLayout(self)
        layout.setContentsMargins(T.SPACE_XS, T.SPACE_SM, T.SPACE_XS, T.SPACE_SM)
        layout.setSpacing(T.SPACE_SM)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.brightness_btn = self._button(
            FluentIcon.BRIGHTNESS, "显示亮度（仅影响显示）", self._open_brightness
        )
        self.contrast_btn = self._button(
            FluentIcon.CONSTRACT, "显示对比度（仅影响显示）", self._open_contrast
        )
        self.names_btn = self._button(
            FluentIcon.FONT, "缩略图叠加类别名", self._open_names
        )
        for button in (self.brightness_btn, self.contrast_btn, self.names_btn):
            layout.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)

        self.size_btn = None
        if with_size:
            self.size_btn = self._button(
                FluentIcon.ZOOM, "缩略图尺寸（Ctrl + 滚轮）", self._open_size
            )
            layout.addWidget(self.size_btn, 0, Qt.AlignmentFlag.AlignHCenter)

        self.reset_btn = self._button(FluentIcon.SYNC, "复位显示", self.reset)
        layout.addWidget(self.reset_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)

        self.setFixedWidth(T.ICON_BTN_LG + 2 * T.SPACE_XS)

    # -----------------------------------------------------------
    # 取值 / 复位
    # -----------------------------------------------------------
    def values(self) -> dict:
        return {
            "brightness": self._brightness / 100.0,
            "contrast": self._contrast / 100.0,
            "show_class_names": bool(self._show_names),
            "name_width": self._name_width / 100.0,
        }

    def reset(self) -> None:
        """恢复默认显示参数。"""
        self._brightness = 0
        self._contrast = 0
        self._show_names = False
        self._name_width = 60
        self.changed.emit()

    def set_brightness(self, value: int) -> None:
        """直接设置亮度（-100 ~ 100；供偏好设置的默认值使用）。"""
        self._brightness = max(-100, min(100, int(value)))
        self.changed.emit()

    def set_contrast(self, value: int) -> None:
        """直接设置对比度（-100 ~ 100；供偏好设置的默认值使用）。"""
        self._contrast = max(-100, min(100, int(value)))
        self.changed.emit()

    # -----------------------------------------------------------
    # 缩略图尺寸
    # -----------------------------------------------------------
    def set_thumb_steps(self, steps) -> None:
        """配置尺寸档位（与网格 `set_thumb_steps` 保持一致）。"""
        values = sorted({int(step) for step in (steps or []) if int(step) > 0})
        if values:
            self._thumb_steps = tuple(values)
        if self._thumb_index >= len(self._thumb_steps):
            self._thumb_index = max(0, len(self._thumb_steps) - 1)

    def set_thumb_size(self, size: int) -> None:
        """按像素同步档位（Ctrl + 滚轮缩放后调用）。"""
        if not self._thumb_steps:
            return
        self._thumb_index = min(
            range(len(self._thumb_steps)),
            key=lambda i: abs(self._thumb_steps[i] - int(size)),
        )

    def thumb_size(self) -> int:
        if not self._thumb_steps:
            return 0
        return self._thumb_steps[self._thumb_index]

    # -----------------------------------------------------------
    # 悬浮面板
    # -----------------------------------------------------------
    def _button(self, icon, tip: str, on_click) -> TransparentToolButton:
        button = icon_button(icon, tip, self)
        button.clicked.connect(lambda _checked=False: on_click())
        return button

    def _open(self, panel: FlyoutViewBase, anchor: QWidget) -> None:
        Flyout.make(panel, target=anchor, parent=self)

    def _open_brightness(self) -> None:
        panel = _SliderPanel(
            -100, 100, self._brightness,
            tooltip="显示亮度（仅影响显示）", parent=self,
        )
        panel.valueChanged.connect(self._set_brightness)
        self._open(panel, self.brightness_btn)

    def _set_brightness(self, value: int) -> None:
        self._brightness = int(value)
        self.changed.emit()

    def _open_contrast(self) -> None:
        panel = _SliderPanel(
            -100, 100, self._contrast,
            tooltip="显示对比度（仅影响显示）", parent=self,
        )
        panel.valueChanged.connect(self._set_contrast)
        self._open(panel, self.contrast_btn)

    def _set_contrast(self, value: int) -> None:
        self._contrast = int(value)
        self.changed.emit()

    def _open_names(self) -> None:
        panel = _NamesPanel(self._show_names, self._name_width, parent=self)
        panel.changed.connect(lambda p=panel: self._apply_names(p))
        self._open(panel, self.names_btn)

    def _apply_names(self, panel: _NamesPanel) -> None:
        self._show_names = panel.check.isChecked()
        self._name_width = panel.width_slider.value()
        self.changed.emit()

    def _open_size(self) -> None:
        if not self._thumb_steps:
            return
        panel = _SliderPanel(
            0, len(self._thumb_steps) - 1, self._thumb_index,
            text_for=lambda index: f"{self._thumb_steps[int(index)]} px",
            tooltip="缩略图尺寸（Ctrl + 滚轮）", parent=self,
        )
        panel.valueChanged.connect(self._set_thumb_index)
        self._open(panel, self.size_btn)

    def _set_thumb_index(self, index: int) -> None:
        self._thumb_index = int(index)
        self.thumbSizeChanged.emit(int(index))
