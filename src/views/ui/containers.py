"""容器化布局组件（设计系统基座）。

目标：把"一整排控件横向拉满"的旧布局，换成**模块化、容器化**的分组：

* ``ToolGroup``：把相关控件打包成一个整体，配合 ``FlowLayout`` 使用，
  空间不足时整组换行，绝不重叠；
* ``ParamGroup``：带标题的参数容器（参照 Halcon DLT 的设置分组），
  内部字段为「左标签 + 紧凑步进器」，左边对齐而非拉伸满行；
* ``FieldRow``：单行字段（标签 + 控件），供需要手工排布的场合复用。

设计原则（详见 `.codebuddy/rules/ui-design.mdc`）
------------------------------------------------
1. 参数输入框**有界**（按字符宽度），不 ``addStretch`` 拉满；
2. 相关控件**成组**，组与组之间留白，形成视觉层次；
3. 布局**可换行 / 可滚动**，不依赖单一固定分辨率。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import CaptionLabel

from src.views.ui import tokens as T


class ToolGroup(QWidget):
    """工具条中的一个逻辑分组（整体参与换行，不会被拆散）。"""

    def __init__(self, *widgets: QWidget, parent=None, spacing: int = T.SPACE_SM):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(spacing)
        for widget in widgets:
            if widget is not None:
                layout.addWidget(widget)

    def add(self, widget: QWidget) -> None:
        """向分组内追加控件。"""
        self.layout().addWidget(widget)


class FieldRow(QWidget):
    """单行字段：左标签（等宽对齐）+ 控件（有界宽度，左对齐不拉伸）。"""

    def __init__(
        self,
        label: str,
        control: QWidget,
        parent=None,
        label_width: int = T.LABEL_MIN_W,
    ):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(T.SPACE_MD)

        self.label = CaptionLabel(label, self)
        self.label.setMinimumWidth(label_width)
        row.addWidget(self.label)
        row.addWidget(control)
        row.addStretch(1)


class ParamGroup(QFrame):
    """带标题的参数容器（模块化分组，Halcon DLT 风格）。

    使用::

        group = ParamGroup("训练")
        group.add_field("训练轮数", epochs_spin)
        group.add_field("批次大小", batch_spin)
    """

    def __init__(self, title: str = "", parent=None, label_width: int = T.LABEL_MIN_W):
        super().__init__(parent)
        self._label_width = label_width
        self.setObjectName("paramGroup")
        self.setStyleSheet(
            "QFrame#paramGroup {"
            f"  border: 1px solid {T.GROUP_BORDER};"
            f"  border-radius: {T.RADIUS_MD}px;"
            "}"
        )

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(T.SPACE_LG, T.SPACE_MD, T.SPACE_LG, T.SPACE_LG)
        self._root.setSpacing(T.SPACE_SM)

        if title:
            self.title = CaptionLabel(title, self)
            font = self.title.font()
            font.setBold(True)
            self.title.setFont(font)
            self._root.addWidget(self.title)

        self._body = QVBoxLayout()
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.setSpacing(T.SPACE_SM)
        self._root.addLayout(self._body)

    # -----------------------------------------------------------
    # 添加内容
    # -----------------------------------------------------------
    def add_field(self, label: str, control: QWidget) -> FieldRow:
        """添加一行「标签 + 控件」并把控件限制为有界宽度。"""
        row = FieldRow(label, control, self, label_width=self._label_width)
        self._body.addWidget(row)
        return row

    def add_widget(self, widget: QWidget) -> None:
        self._body.addWidget(widget)

    def add_layout(self, layout) -> None:
        self._body.addLayout(layout)

    def add_fields(self, fields: list[tuple[str, QWidget]]) -> None:
        """批量添加字段；两列以上的情况见 ``add_grid``。"""
        for label, control in fields:
            self.add_field(label, control)

    def add_grid(self, fields: list[tuple[str, QWidget]], columns: int = 2) -> None:
        """以网格排布字段（窄容器里的紧凑排法）。"""
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(T.SPACE_LG)
        grid.setVerticalSpacing(T.SPACE_SM)
        for index, (label, control) in enumerate(fields):
            holder = FieldRow(label, control, self, label_width=self._label_width)
            grid.addWidget(holder, index // columns, index % columns)
        for column in range(columns):
            grid.setColumnStretch(column, 1)
        self._body.addLayout(grid)

    def add_stretch(self) -> None:
        self._body.addStretch(1)


def tool_separator(parent=None) -> QFrame:
    """工具条中的竖向细分隔线。"""
    line = QFrame(parent)
    line.setFrameShape(QFrame.Shape.VLine)
    line.setFixedWidth(1)
    line.setStyleSheet(f"color: {T.GROUP_BORDER};")
    line.setContentsMargins(0, 0, 0, 0)
    return line


__all__ = ["ToolGroup", "FieldRow", "ParamGroup", "tool_separator"]
