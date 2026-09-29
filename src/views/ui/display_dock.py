"""窗口右侧显示调试栏（参照 Halcon DLT 的右侧图标栏）。

以**竖直图标按钮 + 悬浮面板（Flyout）**呈现图像显示调试项：
    亮度 / 对比度 / 缩略图尺寸（可选）

按钮**与栏等宽**（整块可点）；面板滑杆自带**可点击刻度**——亮度 / 对比度的
0 刻度画成醒目的主题色竖线（点它即复原），尺寸滑杆每档一个小圆点。

**只影响显示，不改动图片数据。** 界面不写解释性文字，全部说明走 tooltip
（见 `.codebuddy/rules/ui-design/RULE.mdc` 的「文案与提示」规范）。

接口与横向显示条一致：``changed`` / ``values()`` / ``reset()``；
带尺寸时另有 ``thumbSizeChanged``（档位下标）与 ``set_thumb_size()``
（按像素同步档位，供 Ctrl + 滚轮缩放后回填）。

``changed`` / ``thumbSizeChanged`` 都做了 ~80ms **去抖**：滑杆与读数即时响应，
重活（重绘 / 重建缩略图）在停稳后只做一次；需要同步生效时调用 ``flush()``。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    FluentIcon,
    Flyout,
    FlyoutViewBase,
    Slider,
    TransparentToolButton,
    isDarkTheme,
    themeColor,
)

from src.views.ui import tokens as T
from src.views.ui.containers import icon_button

# 亮度 / 对比度刻度（0 为强调刻度：主题色竖线，点击即复原）
_VALUE_TICKS = (-100, -50, 0, 50, 100)
_EMPHASIS_TICKS = (0,)

# 参数变化 → 广播去抖：滑杆 / 读数即时响应，重活（重绘 / 重算）延后一次
_APPLY_DELAY_MS = 80


class TickedSlider(Slider):
    """带可视刻度、可点击的滑杆（参照 Halcon DLT 的滑道刻度）。

    刻度绘制在轨道上；**点击刻度直接跳到该值**——强调刻度（亮度 / 对比度的
    0）画成主题色竖线，更醒目、命中范围也更大，点它即复原。点击 / 拖动其余
    位置保持默认行为（qfluentwidgets 点击轨道本就跳值，这里把刻度附近的命中
    收敛为精确整数）。``_drawHorizonTick`` 是 qfluentwidgets 预留给子类
    画刻度的钩子。
    """

    HIT_PX = 6           # 点击命中普通刻度的容差（逻辑像素）
    HIT_EMPHASIS_PX = 9  # 强调刻度（竖线）的命中容差更大，便于点击复原

    def __init__(self, ticks=(), emphasis=(), parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._ticks = tuple(sorted({int(tick) for tick in (ticks or [])}))
        self._emphasis = frozenset(int(tick) for tick in (emphasis or ()))

    # -----------------------------------------------------------
    def tick_x(self, tick: int) -> float:
        """刻度值 → 手柄中心的横坐标（与 Slider 的几何一致）。"""
        total = max(self.maximum() - self.minimum(), 1)
        half = self.handle.width() / 2
        return half + (tick - self.minimum()) / total * self.grooveLength

    def sync_handle(self) -> None:
        """按当前宽度与值校正手柄位置。

        qfluentwidgets 只在 ``setValue`` / ``resizeEvent`` 里重算手柄位置；
        首次定宽（``setFixedWidth`` 生效）时并不触发，导致手柄停在按旧宽度
        算出的位置——「手柄像 0、读数却是 50」的错位。面板显示后调一次本方法。
        """
        self._adjustHandlePos()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self._adjustHandlePos()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        self._adjustHandlePos()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        for tick in self._ticks:
            limit = self.HIT_EMPHASIS_PX if tick in self._emphasis else self.HIT_PX
            if abs(event.pos().x() - self.tick_x(tick)) <= limit:
                self.setValue(int(tick))
                self.clicked.emit(self.value())
                return
        super().mousePressEvent(event)

    def _drawHorizonTick(self, painter) -> None:  # noqa: N802 - Qt 命名
        if not self._ticks:
            return
        y = self.height() / 2
        painter.setPen(Qt.PenStyle.NoPen)
        for tick in self._ticks:
            if tick in self._emphasis:
                # 强调刻度：主题色竖线 + 白色描边——进度条同色时也能分辨
                painter.setPen(QPen(QColor(255, 255, 255, 210), 1.0))
                painter.setBrush(themeColor())
                painter.drawRoundedRect(
                    QRectF(self.tick_x(tick) - 1.5, y - 6.0, 3.0, 12.0), 1.5, 1.5
                )
                painter.setPen(Qt.PenStyle.NoPen)
            else:
                painter.setBrush(
                    QColor(255, 255, 255, 170)
                    if isDarkTheme() else QColor(0, 0, 0, 140)
                )
                painter.drawEllipse(QPointF(self.tick_x(tick), y), 1.8, 1.8)


class _SliderPanel(FlyoutViewBase):
    """单个显示参数的悬浮面板：滑杆（带可点击刻度）+ 读数。"""

    valueChanged = Signal(int)

    def __init__(
        self,
        minimum: int,
        maximum: int,
        value: int,
        ticks=(),
        emphasis=(),
        text_for=None,
        tooltip: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._text_for = text_for or (lambda item: f"{item:+d}" if item else "0")
        row = QHBoxLayout(self)
        row.setContentsMargins(T.CARD_PAD_H, T.SPACE_ML, T.CARD_PAD_H, T.SPACE_MD)
        row.setSpacing(T.SPACE_MD)
        self.slider = TickedSlider(ticks, emphasis, self)
        self.slider.setRange(int(minimum), int(maximum))
        self.slider.setValue(int(value))
        self.slider.setFixedWidth(T.CTRL_W_LG)
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

    def sync_sliders(self) -> None:
        """布局稳定后校正滑杆手柄位置（首次定宽时位置可能是旧的）。"""
        self.slider.sync_handle()


class DisplayDock(QFrame):
    """窗口右侧显示调试栏：竖直图标按钮 + 悬浮面板。

    参数：
        values() -> {"brightness", "contrast"}
        亮度 / 对比度为 -1.0 ~ 1.0 的比例值。
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

        # 去抖定时器：拖动滑杆时不逐帧触发页面重绘，停稳后广播一次
        self._notify_timer = QTimer(self)
        self._notify_timer.setSingleShot(True)
        self._notify_timer.setInterval(_APPLY_DELAY_MS)
        self._notify_timer.timeout.connect(self.changed.emit)
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.setInterval(_APPLY_DELAY_MS)
        self._thumb_timer.timeout.connect(
            lambda: self.thumbSizeChanged.emit(int(self._thumb_index))
        )

        layout = QVBoxLayout(self)
        # 零左右边距：按钮与栏等宽、整块可点（参照 Halcon DLT）
        layout.setContentsMargins(0, T.SPACE_XS, 0, T.SPACE_XS)
        layout.setSpacing(T.SPACE_XXS)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.brightness_btn = self._button(
            FluentIcon.BRIGHTNESS, "显示亮度（仅影响显示）", self._open_brightness
        )
        self.contrast_btn = self._button(
            FluentIcon.CONSTRACT, "显示对比度（仅影响显示）", self._open_contrast
        )
        for button in (self.brightness_btn, self.contrast_btn):
            layout.addWidget(button)

        self.size_btn = None
        if with_size:
            self.size_btn = self._button(
                FluentIcon.ZOOM, "缩略图尺寸（Ctrl + 滚轮）", self._open_size
            )
            layout.addWidget(self.size_btn)
        layout.addStretch(1)

        self.setFixedWidth(T.DOCK_W)

    # -----------------------------------------------------------
    # 取值 / 复位
    # -----------------------------------------------------------
    def _notify(self) -> None:
        """参数变化：重启去抖定时器（拖动中只广播一次，滑杆本身即时响应）。"""
        self._notify_timer.start()

    def flush(self) -> None:
        """立即广播待处理的参数变化（自检 / 需要同步生效的场合）。"""
        if self._notify_timer.isActive():
            self._notify_timer.stop()
            self.changed.emit()

    def values(self) -> dict:
        return {
            "brightness": self._brightness / 100.0,
            "contrast": self._contrast / 100.0,
        }

    def reset(self) -> None:
        """恢复默认显示参数（面板里点 0 刻度亦可单独复原亮度 / 对比度）。"""
        self._brightness = 0
        self._contrast = 0
        self._notify()

    def set_brightness(self, value: int) -> None:
        """直接设置亮度（-100 ~ 100；供偏好设置的默认值使用）。"""
        self._brightness = max(-100, min(100, int(value)))
        self._notify()

    def set_contrast(self, value: int) -> None:
        """直接设置对比度（-100 ~ 100；供偏好设置的默认值使用）。"""
        self._contrast = max(-100, min(100, int(value)))
        self._notify()

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
        # 与栏等宽、图标放大一档，整块可点（参照 Halcon DLT 的右侧图标栏）
        button.setFixedSize(T.DOCK_W, T.DOCK_W)
        button.setIconSize(QSize(T.DOCK_GLYPH, T.DOCK_GLYPH))
        button.clicked.connect(lambda _checked=False: on_click())
        return button

    def _open(self, panel: FlyoutViewBase, anchor: QWidget) -> None:
        Flyout.make(panel, target=anchor, parent=self)
        # 布局稳定后校正手柄位置：面板首次定宽时 qfluentwidgets 不重算，
        # 会出现「手柄像 0、读数却是 50」的错位（见 TickedSlider.sync_handle）
        QTimer.singleShot(0, lambda p=panel: p.sync_sliders())

    def _open_brightness(self) -> None:
        panel = _SliderPanel(
            -100, 100, self._brightness, ticks=_VALUE_TICKS,
            emphasis=_EMPHASIS_TICKS,
            tooltip="显示亮度（仅影响显示）", parent=self,
        )
        panel.valueChanged.connect(self._set_brightness)
        self._open(panel, self.brightness_btn)

    def _set_brightness(self, value: int) -> None:
        self._brightness = int(value)
        self._notify()

    def _open_contrast(self) -> None:
        panel = _SliderPanel(
            -100, 100, self._contrast, ticks=_VALUE_TICKS,
            emphasis=_EMPHASIS_TICKS,
            tooltip="显示对比度（仅影响显示）", parent=self,
        )
        panel.valueChanged.connect(self._set_contrast)
        self._open(panel, self.contrast_btn)

    def _set_contrast(self, value: int) -> None:
        self._contrast = int(value)
        self._notify()

    def _open_size(self) -> None:
        if not self._thumb_steps:
            return
        panel = _SliderPanel(
            0, len(self._thumb_steps) - 1, self._thumb_index,
            ticks=range(len(self._thumb_steps)),
            text_for=lambda index: f"{self._thumb_steps[int(index)]} px",
            tooltip="缩略图尺寸（Ctrl + 滚轮）", parent=self,
        )
        panel.valueChanged.connect(self._set_thumb_index)
        self._open(panel, self.size_btn)

    def _set_thumb_index(self, index: int) -> None:
        self._thumb_index = int(index)
        self._thumb_timer.start()       # 去抖：拖动尺寸滑杆时只重建一次缩略图
