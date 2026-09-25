"""参数输入框的滚轮防护。

问题
----
Qt ``QAbstractSpinBox``（数字输入框）默认**鼠标悬停时滚轮即改值**，
qfluentwidgets 也未覆写该行为。在长表单里滚动页面时，光标一旦掠过某个
输入框，数值就被静默改掉，而用户并不知道原值是多少——这是数据事故。

策略
----
* **参数类**输入框：滚轮**永不改值**；
* 滚轮事件**转交给最近的滚动区**（``QAbstractScrollArea``），
  因此页面滚动依旧顺滑，不会"卡在输入框上"；
* **显示类**控件（亮度 / 对比度 / 缩略图尺寸等）走 ``Slider``，不受影响；
  若确需保留某个输入框的滚轮改值，把属性 ``_allow_wheel_edit`` 设为 ``True``。

两层防护
--------
1. ``WheelGuard``：装在 ``QApplication`` 上的全局事件过滤器，覆盖所有
   （含第三方弹窗与历史遗留）输入框，**无需逐处修改**；
2. ``SafeSpinBox`` / ``SafeDoubleSpinBox``：组件级子类，自带同样行为，
   供设计系统内部与需要独立复用的场合使用。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QAbstractSpinBox,
    QApplication,
    QLineEdit,
)

from qfluentwidgets import CompactSpinBox as _QfwCompactSpinBox
from qfluentwidgets import DoubleSpinBox as _QfwDoubleSpinBox
from qfluentwidgets import SpinBox as _QfwSpinBox

#: 置为 True 可让某个输入框恢复"滚轮改值"（仅建议用于显示类控件）
ALLOW_WHEEL_ATTR = "_allow_wheel_edit"


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _as_spin_box(obj) -> QAbstractSpinBox | None:
    """把事件接收者归一化为它所属的 ``QAbstractSpinBox``。

    QSpinBox / QDoubleSpinBox 内部有一个 ``QLineEdit`` 子控件承接滚轮，
    Qt 再把它转发给输入框；因此这里要同时覆盖两种接收者。
    """
    if isinstance(obj, QAbstractSpinBox):
        return obj
    if isinstance(obj, QLineEdit):
        parent = obj.parentWidget()
        if isinstance(parent, QAbstractSpinBox):
            return parent
    return None


def _scroll_ancestor(widget) -> QAbstractScrollArea | None:
    """向上寻找最近的滚动区。"""
    node = widget.parentWidget()
    while node is not None:
        if isinstance(node, QAbstractScrollArea):
            return node
        node = node.parentWidget()
    return None


def _forward_wheel(area: QAbstractScrollArea, event) -> None:
    """把滚轮事件转发给滚动区的视口，保持页面滚动。"""
    viewport = area.viewport()
    if viewport is None:
        return
    center = viewport.rect().center()
    forwarded = QWheelEvent(
        QPointF(center),
        QPointF(viewport.mapToGlobal(center)),
        event.pixelDelta(),
        event.angleDelta(),
        event.buttons(),
        event.modifiers(),
        event.phase(),
        event.inverted(),
    )
    try:
        QApplication.sendEvent(viewport, forwarded)
    except Exception:  # noqa: BLE001 - 转发失败不应影响主流程
        pass


def block_wheel_value_change(spin: QAbstractSpinBox, event) -> bool:
    """核心逻辑：拦截滚轮改值，并把滚动交给祖先滚动区。

    返回 ``True`` 表示"该事件已处理、不要改值"；``False`` 表示放行默认行为。
    """
    if getattr(spin, ALLOW_WHEEL_ATTR, False):
        return False
    area = _scroll_ancestor(spin)
    if area is not None:
        _forward_wheel(area, event)
    return True


# ---------------------------------------------------------------------------
# 组件级：滚轮安全的输入框
# ---------------------------------------------------------------------------
class _SelfGuardedSpin:
    """组件级防护混入：滚轮不改值，滚动交给所在滚动区。"""

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if block_wheel_value_change(self, event):  # type: ignore[arg-type]
            event.accept()
            return
        super().wheelEvent(event)  # type: ignore[misc]


def _make_safe(cls):
    """把 qfluentwidgets 的输入框类包装为带滚轮防护的子类。"""

    class _Safe(_SelfGuardedSpin, cls):  # type: ignore[misc, valid-type]
        """带滚轮防护的输入框：滚轮不再改值。"""

    _Safe.__name__ = f"Safe{cls.__name__}"
    _Safe.__qualname__ = _Safe.__name__
    _Safe.__doc__ = f"{cls.__name__} 的滚轮安全版本：滚轮不再改值。"
    return _Safe


#: 设计系统统一使用的整型 / 浮点输入框（参数类控件请一律用这两个）
SafeSpinBox = _make_safe(_QfwSpinBox)
SafeDoubleSpinBox = _make_safe(_QfwDoubleSpinBox)


class SafeCompactSpinBox(_SelfGuardedSpin, _QfwCompactSpinBox):  # type: ignore[misc]
    """紧凑步进器（滚轮安全）：窄侧栏栅格里的数值框用它。

    相比行内步进器少占 54px（只有一个按钮），是「几个数值并排塞进 ~270px」
    的唯一可行选择；``tokens.field_width()`` 会按它自动少算开销。

    另有一处刻意偏离默认行为：``CompactSpinBox`` 一聚焦就弹出步进浮层，
    在密集表格里会盖住相邻行，这里改为**只在点右侧按钮时**弹出。
    """

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        # 直接落到 QAbstractSpinBox：跳过 CompactSpinBoxBase.focusInEvent 的
        # _showFlyout()——表格里点一下数值框就弹浮层会遮挡相邻行；
        # 步进仍可用右侧按钮的浮层。
        QAbstractSpinBox.focusInEvent(self, event)


# ---------------------------------------------------------------------------
# 全局级：应用事件过滤器
# ---------------------------------------------------------------------------
class WheelGuard(QObject):
    """装在 ``QApplication`` 上的全局滚轮防护。

    覆盖所有输入框（含未改用 Safe* 子类的历史代码与第三方弹窗），
    因此即便某个页面漏改，也不会发生滚轮误改数据。
    """

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt 命名
        if event.type() != QEvent.Type.Wheel:
            return False
        spin = _as_spin_box(obj)
        if spin is None or isinstance(spin, _SelfGuardedSpin):
            # Safe* 子类自带防护，自行处理，避免重复拦截
            return False
        if block_wheel_value_change(spin, event):
            event.accept()
            return True
        return False


__all__ = [
    "ALLOW_WHEEL_ATTR",
    "WheelGuard",
    "SafeSpinBox",
    "SafeDoubleSpinBox",
    "SafeCompactSpinBox",
    "block_wheel_value_change",
]
