"""未清理自动备份的版本选择弹窗。

正常情况下，关闭项目 / 退出程序时人工确认（保存或丢弃）后即清理
`<项目>.mprj.bak`；只有死机、强制结束等异常退出才会残留。再次打开
该项目文件时弹出本弹窗，列出项目文件与自动备份的修改时间，由人工
选择保留哪一个版本作为项目文件：

    「用备份版本」 → 备份覆盖项目文件（备份随后删除）
    「用项目文件版本」 → 直接丢弃备份
    取消 / Esc → 本次不打开（两个文件均保持原样）

    from src.views.dialogs.backup_dialog import ask_backup_version

    choice = ask_backup_version(self.window(), info)   # "backup" / "project" / ""
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    MessageBoxBase,
    PushButton,
    SubtitleLabel,
)

from src.utils import automation
from src.views.ui import tokens as T

BACKUP = "backup"
PROJECT = "project"


class BackupVersionDialog(MessageBoxBase):
    """「用备份版本 / 用项目文件版本 / 取消」三选对话框。"""

    def __init__(self, parent=None, info: dict | None = None):
        super().__init__(parent)
        info = info or {}
        self._choice = ""

        self.titleLabel = SubtitleLabel("发现未清理的自动备份", self)
        self.viewLayout.addWidget(self.titleLabel)
        self.hint = CaptionLabel("选择保留为项目文件的版本", self)
        self.viewLayout.addWidget(self.hint)

        table = QGridLayout()
        table.setHorizontalSpacing(T.SPACE_XL)
        table.setVerticalSpacing(T.SPACE_SM)
        for row, (name, key) in enumerate(
            (("项目文件", "project_time"), ("自动备份", "backup_time"))
        ):
            table.addWidget(CaptionLabel(name, self), row, 0)
            value = BodyLabel(str(info.get(key) or "—"), self)
            table.addWidget(value, row, 1)
        table.setColumnStretch(2, 1)
        self.viewLayout.addLayout(table)

        # 按钮行（左 → 右）：用备份版本（主）/ 用项目文件版本 / 取消——
        # 与其它按钮同 stretch=1 等大（见 UI 规则 §20）
        self.widget.setMinimumWidth(560)
        self.yesButton.setText("用备份版本")
        self.cancelButton.setText("取消")
        self.projectButton = PushButton("用项目文件版本", self.buttonGroup)
        self.buttonLayout.insertWidget(
            1, self.projectButton, 1, Qt.AlignmentFlag.AlignVCenter
        )
        self.yesButton.clicked.connect(self._on_backup)
        self.projectButton.clicked.connect(self._on_project)

    # -----------------------------------------------------------
    def exec_choice(self) -> str:
        """弹出并返回选择：``backup`` / ``project`` / ``""``（取消）。"""
        self._choice = ""
        if self.exec():
            return self._choice
        return ""

    def _on_backup(self) -> None:
        # accept 由 MessageBoxBase 处理，这里只记录选择
        self._choice = BACKUP

    def _on_project(self) -> None:
        self._choice = PROJECT
        self.accept()


def ask_backup_version(parent, info: dict) -> str:
    """询问用哪个版本作为项目文件；返回值同 ``exec_choice``。

    无人值守模式（`utils/automation`）下不弹窗，固定选**较新的一份**——自检
    脚本打开"带残留备份的项目"时不会卡住。
    """
    auto = automation.backup_choice(info)
    if auto is not None:
        return auto
    return BackupVersionDialog(parent, info).exec_choice()
