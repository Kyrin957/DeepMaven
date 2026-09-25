"""可换行流式布局（FlowLayout）。

解决"一排控件塞不下就重叠"的根本问题
------------------------------------
Qt 的 ``QHBoxLayout`` 在空间不足时**不会换行**，只会把子项压缩到最小尺寸；
一旦控件的最小宽度之和超过可用宽度（窄窗口、或高分屏下字体变宽），
Qt 就只能让它们互相覆盖——这正是标注页工具栏出现
「浏览｜矩形｜多边形｜存标」文字与指示条重叠的原因。

``FlowLayout`` 让子项**从左到右排列，放不下时自动折到下一行**，
因此界面在任意窗口宽度 / 缩放比例下都不会重叠，只会变高。

用法
----
把每个"逻辑分组"包成一个 ``QWidget`` 再加进来，分组整体折行、不会被拆散::

    flow = FlowLayout(spacing=8)
    flow.addWidget(tool_group)      # 工具切换组
    flow.addWidget(class_group)     # 类别选择组
    flow.addWidget(action_group)    # 保存/导出组
"""

from __future__ import annotations

from PySide6.QtCore import QMargins, QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QSizePolicy


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
        self._sync_host_min_height(rect.width())

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        return self.minimumSize()

    # -----------------------------------------------------------
    # 宿主控件最小高度（关键：避免折行后被压扁裁剪）
    # -----------------------------------------------------------
    def _sync_host_min_height(self, width: int) -> None:
        """按当前宽度把"折行所需高度"写回宿主控件的最小高度。

        为什么必须这么做：``QLayout::minimumSize`` 是**与宽度无关**的，
        只能给出"一行的高度"。父布局（如 QVBoxLayout）在做空间分配时以
        最小高度为压缩下限，一旦竖直方向空间紧张（例如同页还有需要
        360px 的画布），就会把折行后的卡片压回一行高，内容被裁掉。

        这里在每次几何变化后回写最小高度；宽度不变时值也不变，
        因此不会产生布局震荡。
        """
        host = self.parentWidget()
        if host is None or width <= 0:
            return
        needed = self.heightForWidth(width)
        if needed > 0 and host.minimumHeight() != needed:
            host.setMinimumHeight(needed)

    def minimumSize(self) -> QSize:  # noqa: N802 - Qt 命名
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


__all__ = ["FlowLayout"]
