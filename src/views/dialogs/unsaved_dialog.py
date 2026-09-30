"""未保存改动询问对话框：保存 / 不保存 / 取消（由用户人工选择）。

关闭项目、切换 / 打开项目、退出程序前调用 ``ask_unsaved_changes()``——
**不静默保存、也不静默丢弃**：

    from src.views.dialogs.unsaved_dialog import ask_unsaved_changes

    choice = ask_unsaved_changes(self.window(), "退出程序")
    if choice == "cancel":
        return                      # 中止操作
    if choice == "save":
        save_all_pending()          # 保存全部未保存内容

关闭窗口 / Esc 一律视为「取消」（安全默认，不误丢数据）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from qfluentwidgets import BodyLabel, MessageBoxBase, PushButton, SubtitleLabel

SAVE = "save"
DISCARD = "discard"
CANCEL = "cancel"


class UnsavedChangesDialog(MessageBoxBase):
    """「保存 / 不保存 / 取消」三选对话框。"""

    def __init__(self, parent=None, action: str = "继续"):
        super().__init__(parent)
        self._choice = CANCEL

        self.titleLabel = SubtitleLabel("未保存的更改", self)
        self.bodyLabel = BodyLabel(
            f"当前项目有未保存的更改。「{action}」前是否保存？", self
        )
        self.bodyLabel.setWordWrap(True)
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.bodyLabel)
        # 与其余弹窗一致的最小宽度：三按钮有足够空间平分（不设时
        # show 阶段宽度被内容压小、按钮挤成一团）
        self.widget.setMinimumWidth(460)

        # 按钮行（左 → 右）：保存 / 不保存 / 取消
        # 右上角 ✕ 与 Esc 走 cancelButton 的 reject，即「取消」
        self.yesButton.setText("保存")
        self.cancelButton.setText("取消")
        # 用 qf 的 PushButton（原生 QPushButton 不在 DIALOG 样式内，
        # 高度比同类按钮矮一截——历史问题：按钮显小）
        self.discardButton = PushButton("不保存", self.buttonGroup)
        # 与 yes / cancel 同样 stretch=1：三按钮等大（无 stretch 时只有
        # 文字自然宽、明显偏小——MessageBoxBase 的按钮靠平分剩余宽度）
        self.buttonLayout.insertWidget(
            1, self.discardButton, 1, Qt.AlignmentFlag.AlignVCenter
        )

        self.yesButton.clicked.connect(self._on_save)
        self.discardButton.clicked.connect(self._on_discard)

    # -----------------------------------------------------------
    def exec_choice(self) -> str:
        """弹出并返回用户选择：``save`` / ``discard`` / ``cancel``。"""
        self._choice = CANCEL
        if self.exec():
            return self._choice
        return CANCEL

    def _on_save(self) -> None:
        # accept 由 MessageBoxBase 处理，这里只记录选择
        self._choice = SAVE

    def _on_discard(self) -> None:
        self._choice = DISCARD
        self.accept()


def ask_unsaved_changes(parent, action: str) -> str:
    """询问未保存改动；返回值同 :meth:`UnsavedChangesDialog.exec_choice`。"""
    return UnsavedChangesDialog(parent, action).exec_choice()
