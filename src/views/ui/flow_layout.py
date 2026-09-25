"""可换行流式布局（FlowLayout）与其宿主控件（FlowContainer）。

解决"一排控件塞不下就重叠"的根本问题
------------------------------------
Qt 的 ``QHBoxLayout`` 在空间不足时**不会换行**，只会把子项压缩到最小尺寸；
一旦控件的最小宽度之和超过可用宽度（窄窗口、或高分屏下字体变宽），
Qt 就只能让它们互相覆盖——这正是标注页工具栏出现
「浏览｜矩形｜多边形｜存标」文字与指示条重叠的原因。

``FlowLayout`` 让子项**从左到右排列，放不下时自动折到下一行**，
因此界面在任意窗口宽度 / 缩放比例下都不会重叠，只会变高。

⚠ 关键约束：不要用 ``setMinimumHeight`` 给宿主表达"折行后的高度"
-----------------------------------------------------------------
折行高度与**宽度相关**，而 ``QLayout::minimumSize`` 与宽度无关（只能给出一行高）。
若在布局回调里给宿主 ``setMinimumHeight()``，就相当于向父布局注入了一个
**显式最小尺寸**——Qt 的 ``qSmartMinSize()`` 会**无视 ``QSizePolicy.Ignored``
强行采用显式最小值**，于是"页面最小高度沿布局层层累加、窗口被撑得比屏幕还高、
顶部标题栏被顶出可视区"的故障（见开发文档 §4.4）会重新出现。

正确做法见 ``FlowContainer``：只重写 ``sizeHint`` / ``minimumSizeHint``
（**提示值**，随宽度重算、受尺寸策略正常约束），绝不写显式最小值。
"""

from __future__ import annotations

from PySide6.QtCore import QMargins, QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QSizePolicy, QWidget


class FlowLayout(QLayout):
    """从左到右流动排列、空间不足自动换行的布局。"""

    def __init__(self, parent=None, margin: int = 0, spacing: int = 8):
        super().__init__(parent)
        self._items: list = []
        self._spacing = spacing
        self.setContentsMargins(QMargins(margin, margin, margin, margin))

    # -----------------------------------------------------------
    # QLayout 接口
    # -----------------------------------------------------------
    def addItem(self, item) -> None:  # noqa: N802 - Qt 命名
        self._items.append(item)
        self.invalidate()

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802 - Qt 命名
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):  # noqa: N802 - Qt 命名
        if 0 <= index < len(self._items):
            item = self._items.pop(index)
            self.invalidate()
            return item
        return None

    def expandingDirections(self):  # noqa: N802 - Qt 命名
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt 命名
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt 命名
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 - Qt 命名
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802 - Qt 命名
        """与宽度无关的最小尺寸：单个子项的最大最小尺寸（即"一行"的尺寸）。"""
        size = QSize()
        for item in self._items:
            if item.isEmpty():
                continue
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(
            margins.left() + margins.right(), margins.top() + margins.bottom()
        )
        return size

    # -----------------------------------------------------------
    # 内部：排列
    # -----------------------------------------------------------
    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        """按 rect 宽度排布子项；test_only 时只算高度不移动控件。"""
        margins = self.contentsMargins()
        effective = rect.adjusted(
            margins.left(), margins.top(), -margins.right(), -margins.bottom()
        )
        x = effective.x()
        y = effective.y()
        line_height = 0

        for item in self._items:
            if item.isEmpty():
                # 隐藏的分组（如非掩码模式下的画笔工具）不占位
                continue
            hint = item.sizeHint()
            next_x = x + hint.width() + self._spacing
            if next_x - self._spacing > effective.right() + 1 and line_height > 0:
                # 本行放不下：换行
                x = effective.x()
                y = y + line_height + self._spacing
                next_x = x + hint.width() + self._spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())

        return y + line_height - rect.y() + margins.bottom()


class FlowContainer(QWidget):
    """``FlowLayout`` 的宿主控件：高度随折行自适应，且**不写显式最小尺寸**。

    用法::

        container = FlowContainer(card, spacing=T.SPACE_MD,
                                  margins=(T.SPACE_XL, T.SPACE_MD, T.SPACE_XL, T.SPACE_MD))
        container.add(ToolGroup(...))
        card_layout.addWidget(container)

    高度如何生效：重写 ``minimumSizeHint`` / ``sizeHint`` 返回**折行所需高度**，
    并给尺寸策略打开 ``HeightForWidth``。父布局按提示值分配空间，
    因此内容不会被压扁裁剪；同时因为**没有显式最小尺寸**，页面注册时的
    ``QSizePolicy.Ignored`` 依然有效，最小高度不会传导到窗口（§4.4）。
    """

    def __init__(self, parent=None, spacing: int = 8, margins=None):
        super().__init__(parent)
        self._flow = FlowLayout(self, spacing=spacing)
        if margins is not None:
            self._flow.setContentsMargins(*margins)
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    # -----------------------------------------------------------
    def flow(self) -> FlowLayout:
        return self._flow

    def add(self, widget) -> None:
        """加入一个子项（通常是一个 ``ToolGroup``）。"""
        self._flow.addWidget(widget)

    def wrapped_height(self, width: int | None = None) -> int:
        """给定宽度下折行所需的高度（宽度缺省用当前宽度）。"""
        if width is None:
            width = self.width()
        if width <= 0:
            # 尚未定位：先给"一行"的高度，等真正拿到宽度再重算
            return self._flow.minimumSize().height()
        return self._flow.heightForWidth(width)

    # -----------------------------------------------------------
    # 宽高提示（提示值，随宽度变化；不写显式最小值）
    # -----------------------------------------------------------
    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        base = self._flow.minimumSize()
        return QSize(base.width(), self.wrapped_height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        return QSize(0, self.wrapped_height())

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt 命名
        return self._flow.heightForWidth(width)


__all__ = ["FlowLayout", "FlowContainer"]
