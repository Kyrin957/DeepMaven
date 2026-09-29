"""置顶小弹窗：同步耗时操作的进度提示（BusyPopup）。

只有**必要的同步耗时操作**（批量重算、全量重绘、尺寸档位切换、大图显示增强
等）才用它；后台任务（训练 / 推理 / 导出）继续用 worker 进程 + 页面内进度条。
界面上不写解释性文字（UI 规则 §10），标题用功能名。

用法：

    from src.views.ui import BusyPopup

    with BusyPopup.scope(self.window(), "正在应用显示效果") as busy:
        for position, item in enumerate(items):
            ...
            busy.step(position + 1, len(items))

三条体验约束（不要绕过）：

* **延迟显示**：短于 ``SHOW_DELAY_MS``（150ms）的操作不弹窗，避免快速操作
  闪烁；
* **只刷重绘**：``step()`` 内部 ``processEvents(ExcludeUserInputEvents)``——
  弹窗 / 滑杆读数能实时重绘，但不会因用户点击造成操作重入；
* **不抢焦点**：无边框 + Tool + 置顶，显示时自动居中于父窗口。
"""

from __future__ import annotations

from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    IndeterminateProgressBar,
    ProgressBar,
)

from src.views.ui import tokens as T


class BusyPopup(QWidget):
    """置顶小弹窗：功能名 + 进度条（确定 / 不确定两种形态）。"""

    SHOW_DELAY_MS = 150     # 低于该时长的操作不显示（避免闪烁）
    PANEL_W = 240

    def __init__(self, parent=None, title: str = ""):
        super().__init__(parent)
        self._title = str(title or "正在处理")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedWidth(self.PANEL_W)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        card = CardWidget(self)
        layout.addWidget(card)
        inner = QVBoxLayout(card)
        inner.setContentsMargins(
            T.CARD_PAD_H, T.CARD_PAD_V, T.CARD_PAD_H, T.CARD_PAD_V
        )
        inner.setSpacing(T.SPACE_SM)
        self.label = BodyLabel(self._title, card)
        inner.addWidget(self.label)
        self.bar = ProgressBar(card)                    # 确定进度
        self.bar.setVisible(False)
        inner.addWidget(self.bar)
        self.pulse = IndeterminateProgressBar(card)     # 不确定进度
        inner.addWidget(self.pulse)

        self._running = False
        self._delay = QTimer(self)
        self._delay.setSingleShot(True)
        self._delay.setInterval(self.SHOW_DELAY_MS)
        self._delay.timeout.connect(self._show_now)

    # -----------------------------------------------------------
    # 生命周期
    # -----------------------------------------------------------
    @classmethod
    def scope(cls, parent, title: str = "") -> "BusyPopup":
        """上下文用法：``with BusyPopup.scope(window, "标题") as busy: ...``"""
        return cls(parent, title).begin()

    def begin(self) -> "BusyPopup":
        """开始操作：延迟 ``SHOW_DELAY_MS`` 后显示（期间界面照常响应）。"""
        self._running = True
        self.bar.setVisible(False)
        self.pulse.setVisible(True)
        self.pulse.start()
        self._delay.start()
        return self

    def step(self, done: int | None = None, total: int | None = None) -> None:
        """上报进度并保持界面重绘（不处理输入事件，防重入）。"""
        if total:
            self._set_progress(int(done or 0), int(total))
        if self.isVisible():
            self._move_to_parent()
        QApplication.processEvents(
            QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
        )

    def end(self) -> None:
        """结束操作并收起弹窗（快操作从未显示过则什么都不做）。"""
        self._running = False
        self._delay.stop()
        self.pulse.stop()
        self.hide()

    def __enter__(self) -> "BusyPopup":
        return self

    def __exit__(self, *_exc) -> bool:
        self.end()
        return False

    # -----------------------------------------------------------
    # 内部
    # -----------------------------------------------------------
    def _set_progress(self, done: int, total: int) -> None:
        if total <= 0:
            return
        if not self.bar.isVisible():        # 首次拿到总数：切成确定进度条
            self.pulse.stop()
            self.pulse.setVisible(False)
            self.bar.setRange(0, int(total))
            self.bar.setVisible(True)
        elif self.bar.maximum() != int(total):
            self.bar.setRange(0, int(total))
        self.bar.setValue(max(0, min(int(done), int(total))))
        self.label.setText(f"{self._title}（{done} / {total}）")

    def _show_now(self) -> None:
        if not self._running:
            return
        self._move_to_parent()
        self.show()
        self.raise_()

    def _move_to_parent(self) -> None:
        parent = self.parentWidget()
        top = parent.window() if parent is not None else None
        if top is None:
            return
        geo = top.frameGeometry()
        self.adjustSize()
        self.move(
            geo.center().x() - self.width() // 2,
            geo.center().y() - self.height() // 2,
        )
