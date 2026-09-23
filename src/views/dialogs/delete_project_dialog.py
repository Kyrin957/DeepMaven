"""删除项目确认对话框。

参照 Halcon DLT 的删除确认：列出会从文件系统中永久删除的项目文件、
项目文件夹（拆分 / 训练 / 导出产物）与其中的训练模型。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    MessageBoxBase,
    PushButton,
    SubtitleLabel,
)

# 列出的模型数量上限（超过则折叠为「另有 N 个」）
_MAX_MODELS = 6


class DeleteProjectDialog(MessageBoxBase):
    """删除项目确认。"""

    def __init__(self, name: str, info: dict, parent=None):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel(f"删除项目「{name}」", self)
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self._build_body(info))

        self.yesButton.setText("确认")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(620)

        folder = Path(str(info.get("folder") or ""))
        self.openButton = PushButton("打开项目文件夹", self.buttonGroup)
        self.openButton.setEnabled(folder.is_dir())
        self.openButton.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        )
        self.buttonLayout.insertWidget(0, self.openButton)
        self.buttonLayout.insertStretch(1)

    # -----------------------------------------------------------
    # 内容
    # -----------------------------------------------------------
    def _build_body(self, info: dict) -> QWidget:
        body = QWidget(self)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        layout.addWidget(BodyLabel("以下内容将被永久删除：", body))
        for item in self._paths(info):
            label = CaptionLabel(f"　{item}", body)
            label.setWordWrap(True)
            layout.addWidget(label)

        models = [Path(item) for item in (info.get("models") or [])]
        if models:
            layout.addWidget(BodyLabel(f"将同时删除 {len(models)} 个训练产物：", body))
            for item in models[:_MAX_MODELS]:
                label = CaptionLabel(f"　{item}", body)
                label.setWordWrap(True)
                layout.addWidget(label)
            if len(models) > _MAX_MODELS:
                layout.addWidget(
                    CaptionLabel(f"　…另有 {len(models) - _MAX_MODELS} 个", body)
                )
            layout.addWidget(CaptionLabel("如需保留，请先打开项目导出模型。", body))
        return body

    @staticmethod
    def _paths(info: dict) -> list[str]:
        """项目文件 / 文件夹 / 备份 / 旧版产物目录（只列实际存在的）。"""
        values: list[str] = []
        target = Path(str(info.get("file") or ""))
        if target.is_file():
            values.append(str(target))
        folder = Path(str(info.get("folder") or ""))
        if folder.is_dir():
            values.append(str(folder))
        backup = Path(str(info.get("backup") or ""))
        if str(backup) and backup.is_file():
            values.append(str(backup))
        values.extend(str(item) for item in (info.get("extra") or []))
        return values
