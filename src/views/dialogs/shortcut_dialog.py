"""快捷键总览对话框（F1）。"""

from __future__ import annotations

from PySide6.QtWidgets import QTableWidgetItem
from qfluentwidgets import MessageBoxBase, SubtitleLabel, TableWidget


class ShortcutDialog(MessageBoxBase):
    """按分组列出当前程序注册的全部快捷键。"""

    def __init__(self, items: list, parent=None):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel("快捷键", self)
        self.viewLayout.addWidget(self.titleLabel)
        self.table = self._build_table(items)
        self.viewLayout.addWidget(self.table)

        self.yesButton.setText("关闭")
        self.cancelButton.hide()
        self.widget.setMinimumWidth(560)

    def _build_table(self, items: list) -> TableWidget:
        table = TableWidget(self)
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels(["分组", "功能", "快捷键"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        table.setRowCount(len(items))
        for row, (group, name, sequence) in enumerate(items):
            for column, text in enumerate((group, name, sequence)):
                table.setItem(row, column, QTableWidgetItem(str(text)))
        table.setMinimumSize(520, 420)
        return table
