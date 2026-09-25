"""标签统计弹窗（参照 Halcon DLT 的标签统计）。

按 **标签类别 / 数据集拆分 / 图像标记** 统计数量与占比，并给出已标注 / 未标注
统计；可切换统计范围（整体 / 当前选中图像）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CaptionLabel,
    ComboBox,
    MessageBoxBase,
    SubtitleLabel,
)

from src.models.filter_rules import UNLABELED_NAME

from src.views.ui import tokens as T

_TABLE_HEIGHT = 132
_HEADERS = ("名称", "数量", "占比")


def _table(parent) -> QTableWidget:
    table = QTableWidget(0, len(_HEADERS), parent)
    table.setHorizontalHeaderLabels(list(_HEADERS))
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    table.horizontalHeader().setSectionResizeMode(
        0, QHeaderView.ResizeMode.Stretch
    )
    table.horizontalHeader().setSectionResizeMode(
        1, QHeaderView.ResizeMode.ResizeToContents
    )
    table.horizontalHeader().setSectionResizeMode(
        2, QHeaderView.ResizeMode.ResizeToContents
    )
    table.setMinimumHeight(_TABLE_HEIGHT)
    return table


class LabelStatsDialog(MessageBoxBase):
    """标签统计弹窗。

    用法：
        dialog = LabelStatsDialog(parent, provider=lambda scope: stats)
        dialog.exec()
    """

    def __init__(self, parent=None, provider=None, scope: str = "all"):
        super().__init__(parent)
        self._provider = provider

        self.titleLabel = SubtitleLabel("标签统计", self)
        self.viewLayout.addWidget(self.titleLabel)

        holder = QWidget(self)
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(T.SPACE_SM)

        scope_row = QHBoxLayout()
        scope_row.setSpacing(T.SPACE_MD)
        scope_row.addWidget(CaptionLabel("统计范围", holder))
        self.scope_box = ComboBox(holder)
        self.scope_box.addItem("全部图像", userData="all")
        self.scope_box.addItem("当前选中图像", userData="selection")
        self.scope_box.setFixedWidth(T.CTRL_W_LG)
        self.scope_box.currentIndexChanged.connect(lambda _i: self.reload())
        scope_row.addWidget(self.scope_box)
        scope_row.addStretch(1)
        layout.addLayout(scope_row)

        layout.addWidget(CaptionLabel("标签类别", holder))
        self.class_table = _table(holder)
        layout.addWidget(self.class_table)

        layout.addWidget(CaptionLabel("数据集拆分", holder))
        self.split_table = _table(holder)
        self.split_table.setMinimumHeight(112)
        layout.addWidget(self.split_table)

        layout.addWidget(CaptionLabel("图像标记", holder))
        self.tag_table = _table(holder)
        layout.addWidget(self.tag_table)

        self.viewLayout.addWidget(holder)

        self.summary = CaptionLabel("", self)
        self.summary.setWordWrap(True)
        self.viewLayout.addWidget(self.summary)

        self.widget.setMinimumWidth(560)
        self.cancelButton.setVisible(False)
        self.yesButton.setText("关闭")
        self._select(scope)
        self.reload()

    # -----------------------------------------------------------
    def _select(self, scope: str) -> None:
        target = 0 if str(scope) != "selection" else 1
        self.scope_box.blockSignals(True)
        self.scope_box.setCurrentIndex(target)
        self.scope_box.blockSignals(False)

    def current_scope(self) -> str:
        return str(self.scope_box.currentData() or "all")

    def reload(self) -> None:
        """按当前范围重新统计。"""
        if self._provider is None:
            return
        stats = self._provider(self.current_scope()) or {}
        self._fill(self.class_table, stats.get("classes") or [], UNLABELED_NAME)
        self._fill(self.split_table, stats.get("splits") or [])
        self._fill(self.tag_table, stats.get("tags") or [], "（无标记）")
        total = int(stats.get("total") or 0)
        annotated = int(stats.get("annotated") or 0)
        share = (annotated / total * 100) if total else 0.0
        self.summary.setText(
            f"共 {total} 张 · 已标注 {annotated}（{share:.1f}%）· "
            f"未标注 {max(0, total - annotated)}"
        )

    @staticmethod
    def _fill(table: QTableWidget, rows: list, empty_name: str = "") -> None:
        table.setRowCount(len(rows))
        for index, item in enumerate(rows):
            name = str(item.get("name") or "")
            if empty_name and not name:
                name = empty_name
            values = (
                name,
                str(int(item.get("count") or 0)),
                f"{float(item.get('share') or 0) * 100:.1f}%",
            )
            for column, text in enumerate(values):
                cell = QTableWidgetItem(text)
                if column:
                    cell.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                color = str(item.get("color") or "")
                if column == 0 and color and QColor(color).isValid():
                    cell.setForeground(QColor(color))
                table.setItem(index, column, cell)
        table.setVisible(bool(rows))
