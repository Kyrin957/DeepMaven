"""自定义筛选规则弹窗（参照 Halcon DLT 的筛选规则）。

每个条件是一行「字段 / 关系 / 值」，条件之间用 **满足全部（且）/ 满足任一（或）**
组合；「预览」按当前规则即时统计命中张数。规则随项目保存。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CaptionLabel,
    ComboBox,
    FluentIcon,
    LineEdit,
    MessageBoxBase,
    PushButton,
    ScrollArea,
    SubtitleLabel,
    TransparentToolButton,
)

from src.models.filter_rules import (
    FILTER_FIELDS,
    choices_for,
    default_op,
    field_label,
    new_condition,
    operators_for,
)

from src.views.ui import tokens as T


class _ConditionRow(QWidget):
    """一条筛选条件：字段 / 关系 / 值 / 删除。"""

    changed = Signal()
    removed = Signal(object)

    def __init__(self, parent=None, condition: dict | None = None, options: dict | None = None):
        super().__init__(parent)
        self._options = dict(options or {})
        self._syncing = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(T.SPACE_SM)

        self.field_box = ComboBox(self)
        for key, label, _kind in FILTER_FIELDS:
            self.field_box.addItem(label, userData=key)
        self.field_box.setFixedWidth(T.CTRL_W_MD)
        layout.addWidget(self.field_box)

        self.op_box = ComboBox(self)
        self.op_box.setFixedWidth(T.CTRL_W_SM)
        layout.addWidget(self.op_box)

        self.value_edit = LineEdit(self)
        self.value_edit.setPlaceholderText("值")
        self.value_edit.textChanged.connect(lambda _t: self.changed.emit())
        layout.addWidget(self.value_edit, 1)

        # 候选值选择器：选中的候选填入输入框，仍可手动输入其它值
        self.value_picker = ComboBox(self)
        self.value_picker.setFixedWidth(T.CTRL_W_MD)
        self.value_picker.setVisible(False)
        self.value_picker.currentIndexChanged.connect(self._on_pick)
        layout.addWidget(self.value_picker)

        self.remove_btn = TransparentToolButton(self)
        self.remove_btn.setIcon(FluentIcon.DELETE)
        self.remove_btn.setToolTip("删除该条件")
        self.remove_btn.setFixedSize(T.ICON_BTN_MD, T.ICON_BTN_MD)
        self.remove_btn.clicked.connect(lambda: self.removed.emit(self))
        layout.addWidget(self.remove_btn)

        self.field_box.currentIndexChanged.connect(self._on_field_changed)
        self.op_box.currentIndexChanged.connect(lambda _i: self.changed.emit())
        self.set_condition(condition or new_condition())

    def _on_pick(self, index: int) -> None:
        """候选值被选中 → 填入输入框（仍可手动改成其它值）。"""
        if index < 0:
            return
        text = str(self.value_picker.itemText(index) or "")
        if text:
            self.value_edit.setText(text)

    # -----------------------------------------------------------
    def _on_field_changed(self, _index: int) -> None:
        if self._syncing:
            return
        field = self.field()
        self._reload_operators(field)
        self._reload_value_editor(field)
        self.changed.emit()

    def _reload_operators(self, field: str) -> None:
        current = self.op()
        self._syncing = True
        self.op_box.clear()
        for op, label in operators_for(field):
            self.op_box.addItem(label, userData=op)
        self._syncing = False
        keys = [op for op, _label in operators_for(field)]
        self.op_box.setCurrentIndex(keys.index(current) if current in keys else 0)

    def _reload_value_editor(self, field: str) -> None:
        """按字段刷新候选值（选择 / 列表型字段给下拉，纯文本仍靠手输）。"""
        labels = [label for _key, label in choices_for(field)]
        labels += [
            str(name) for name in self._options.get(field, [])
            if str(name) not in labels
        ]
        self.value_picker.blockSignals(True)
        self.value_picker.clear()
        if labels:
            self.value_picker.addItems(labels)
            self.value_picker.setCurrentIndex(-1)
        self.value_picker.blockSignals(False)
        self.value_picker.setVisible(bool(labels))

    # -----------------------------------------------------------
    def field(self) -> str:
        return str(self.field_box.currentData() or "name")

    def op(self) -> str:
        return str(self.op_box.currentData() or "contains")

    def value(self) -> str:
        return self.value_edit.text().strip()

    def condition(self) -> dict:
        return {"field": self.field(), "op": self.op(), "value": self.value()}

    def set_condition(self, condition: dict) -> None:
        field = str(condition.get("field") or "name")
        op = str(condition.get("op") or default_op(field))
        value = str(condition.get("value") or "")
        self._syncing = True
        keys = [key for key, _label, _kind in FILTER_FIELDS]
        self.field_box.setCurrentIndex(keys.index(field) if field in keys else 0)
        self._syncing = False
        field = self.field()
        self._reload_operators(field)
        if op not in [item for item, _label in operators_for(field)]:
            op = default_op(field)
        self.op_box.setCurrentIndex(
            [item for item, _label in operators_for(field)].index(op)
        )
        self._reload_value_editor(field)
        self.value_edit.setText(value)

    def set_options(self, options: dict) -> None:
        self._options = dict(options or {})
        self._reload_value_editor(self.field())


class FilterRulesDialog(MessageBoxBase):
    """自定义筛选规则弹窗。

    用法：
        dialog = FilterRulesDialog(parent, tree=..., options=..., counter=...)
        if dialog.exec():
            tree = dialog.result_tree()
    """

    def __init__(
        self,
        parent=None,
        tree: dict | None = None,
        options: dict | None = None,
        counter=None,
    ):
        super().__init__(parent)
        self._counter = counter
        self._options = dict(options or {})
        tree = tree or {}

        self.titleLabel = SubtitleLabel("自定义筛选规则", self)
        self.viewLayout.addWidget(self.titleLabel)

        holder = QWidget(self)
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(T.SPACE_SM)

        logic_row = QHBoxLayout()
        logic_row.setSpacing(T.SPACE_MD)
        logic_row.addWidget(CaptionLabel("条件关系", holder))
        self.logic_box = ComboBox(holder)
        self.logic_box.addItem("满足全部条件（且）", userData="and")
        self.logic_box.addItem("满足任一条件（或）", userData="or")
        self.logic_box.setCurrentIndex(1 if str(tree.get("logic")) == "or" else 0)
        self.logic_box.setFixedWidth(T.CTRL_W_XL)
        self.logic_box.currentIndexChanged.connect(lambda _i: self._on_changed())
        logic_row.addWidget(self.logic_box)
        logic_row.addStretch(1)

        self.add_btn = PushButton("添加条件", holder)
        self.add_btn.setIcon(FluentIcon.ADD)
        self.add_btn.clicked.connect(lambda: self.add_condition())
        logic_row.addWidget(self.add_btn)
        layout.addLayout(logic_row)

        self.rows_holder = QWidget(holder)
        self.rows_layout = QVBoxLayout(self.rows_holder)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(T.SPACE_SM)
        self.rows_layout.addStretch(1)

        scroll = ScrollArea(holder)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self.rows_holder)
        scroll.setMinimumHeight(190)
        layout.addWidget(scroll)
        self.viewLayout.addWidget(holder)

        self.hint = CaptionLabel("", self)
        self.hint.setWordWrap(True)
        self.viewLayout.addWidget(self.hint)

        self.widget.setMinimumWidth(660)
        self.cancelButton.setText("取消")
        self.yesButton.setText("应用")

        for item in tree.get("conditions") or []:
            if isinstance(item, dict) and not isinstance(item.get("conditions"), list):
                self.add_condition(item)
        if not tree.get("conditions"):
            self.add_condition()
        self._on_changed(refresh_only=True)

    # -----------------------------------------------------------
    # 条件行
    # -----------------------------------------------------------
    def add_condition(self, condition: dict | None = None) -> None:
        row = _ConditionRow(self.rows_holder, condition, self._options)
        row.changed.connect(self._on_changed)
        row.removed.connect(self._remove_row)
        self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)
        self._on_changed()      # 预填好条件的行也要立刻刷新预览

    def _remove_row(self, row) -> None:
        row.setParent(None)
        row.deleteLater()
        if self.rows() == 0:
            self.add_condition()
        self._on_changed()

    def rows(self) -> list:
        return [
            self.rows_layout.itemAt(index).widget()
            for index in range(self.rows_layout.count())
            if isinstance(self.rows_layout.itemAt(index).widget(), _ConditionRow)
        ]

    # -----------------------------------------------------------
    def result_tree(self) -> dict:
        conditions = [
            row.condition()
            for row in self.rows()
            if str(row.condition().get("value") or "").strip()
        ]
        return {
            "logic": str(self.logic_box.currentData() or "and"),
            "conditions": conditions,
        }

    def _on_changed(self, refresh_only: bool = False) -> None:
        if self._counter is None:
            self.hint.setText("")
            return
        try:
            shown, total = self._counter(self.result_tree())
        except Exception as exc:  # noqa: BLE001 - 预览失败不应影响弹窗
            self.hint.setText(f"预览失败：{exc}")
            return
        self.hint.setText(f"预览：命中 {shown} / {total} 张")
