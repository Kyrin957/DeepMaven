"""图像标记编辑弹窗（参照 Halcon DLT 的图像标记窗口）。

    文本 / 颜色（自选取色面板）/ 为当前图像分配标记 / 取消·确定·应用
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    CheckBox,
    FluentIcon,
    LineEdit,
    MessageBoxBase,
    PushButton,
    SubtitleLabel,
)

from src.views.dialogs.class_edit_dialog import PRESET_COLORS, ColorPicker

DEFAULT_TAG_COLOR = "#9DB5B2"


class TagEditDialog(MessageBoxBase):
    """图像标记编辑弹窗（新增 / 编辑共用）。

    用法：
        dialog = TagEditDialog(parent, text="asv", color="#9DB5B2")
        if dialog.exec():
            data = dialog.result_data()      # {"text", "color", "assign"}
    """

    applied = Signal(dict)      # 点「应用」时发出（弹窗不关闭）

    def __init__(
        self,
        parent=None,
        text: str = "",
        color: str = DEFAULT_TAG_COLOR,
        editing: bool = False,
        has_selection: bool = False,
    ):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel(
            "编辑图像标记" if editing else "新增图像标记", self
        )
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self._build_body(text, color, has_selection))

        # 该标签只用于操作反馈，不放说明文字
        self.hint = CaptionLabel("", self)
        self.hint.setWordWrap(True)
        self.viewLayout.addWidget(self.hint)

        self.widget.setMinimumWidth(460)
        self.cancelButton.setText("取消")
        self.yesButton.setText("确定")
        self.applyButton = PushButton(self.buttonGroup)
        self.applyButton.setText("应用")
        self.applyButton.setToolTip("应用但不关闭")
        self.buttonLayout.addWidget(self.applyButton)

        # 接管确认按钮：先校验再关闭
        try:
            self.yesButton.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.yesButton.clicked.connect(lambda: self._handle(close=True))
        self.applyButton.clicked.connect(lambda: self._handle(close=False))

    # -----------------------------------------------------------
    def _build_body(self, text: str, color: str, has_selection: bool) -> QWidget:
        holder = QWidget(self)
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        layout.addWidget(CaptionLabel("文本", holder))
        self.text_edit = LineEdit(holder)
        self.text_edit.setText(text)
        self.text_edit.setPlaceholderText("输入标记文本")
        layout.addWidget(self.text_edit)

        layout.addWidget(CaptionLabel("颜色", holder))
        row = QHBoxLayout()
        row.setSpacing(6)
        self.color_edit = LineEdit(holder)
        self.color_edit.setText(QColor(color).name().upper())
        self.color_edit.editingFinished.connect(self._on_color_text)
        row.addWidget(self.color_edit, 1)

        self.color_btn = PushButton(holder)
        self.color_btn.setIcon(FluentIcon.EDIT)
        self.color_btn.setToolTip("展开 / 收起取色面板")
        self.color_btn.clicked.connect(self._toggle_picker)
        row.addWidget(self.color_btn)
        layout.addLayout(row)

        presets = QHBoxLayout()
        presets.setSpacing(4)
        for value in PRESET_COLORS:
            presets.addWidget(self._preset_button(value, holder))
        presets.addStretch(1)
        layout.addLayout(presets)

        # 取色面板默认收起，点颜色按钮再展开（与参考界面一致）
        self.picker = ColorPicker(color, holder)
        self.picker.setVisible(False)
        self.picker.colorChanged.connect(self._on_picked)
        layout.addWidget(self.picker)

        self.assign_check = CheckBox("为当前图像分配标记", holder)
        self.assign_check.setChecked(bool(has_selection))
        self.assign_check.setEnabled(bool(has_selection))
        if not has_selection:
            self.assign_check.setToolTip("请先在图像窗口中选中图像")
        layout.addWidget(self.assign_check)
        return holder

    def _preset_button(self, value: str, parent) -> PushButton:
        button = PushButton(parent)
        button.setFixedSize(22, 22)
        button.setToolTip(value)
        button.setStyleSheet(
            f"PushButton {{ background: {value}; border: 1px solid #5A5A5A;"
            " border-radius: 3px; padding: 0; }"
            "PushButton:hover { border: 1px solid #FFFFFF; }"
        )
        button.clicked.connect(lambda _=False, v=value: self._apply_color(v))
        return button

    # -----------------------------------------------------------
    def _toggle_picker(self) -> None:
        # 用 isHidden 判断展开状态：弹窗本身未显示时 isVisible 恒为 False
        visible = self.picker.isHidden()
        self.picker.setVisible(visible)
        self.widget.adjustSize()

    def _apply_color(self, value: str) -> None:
        self.picker.set_color(value)
        self.color_edit.setText(self.picker.color_hex())
        self.hint.setText(f"已选择颜色 {self.picker.color_hex()}")

    def _on_picked(self, value: str) -> None:
        self.color_edit.setText(value)
        self.hint.setText(f"已选择颜色 {value}")

    def _on_color_text(self) -> None:
        text = self.color_edit.text().strip()
        if not text:
            return
        if not QColor(text).isValid():
            self.hint.setText("颜色值无效，形如 #9DB5B2")
            return
        self.picker.set_color(text)

    def _handle(self, close: bool) -> None:
        if not self.text_edit.text().strip():
            self.hint.setText("请输入标记文本")
            return
        data = self.result_data()
        if close:
            self.accept()
        else:
            self.applied.emit(data)
            self.hint.setText(f"已应用标记「{data['text']}」")

    # -----------------------------------------------------------
    def result_data(self) -> dict:
        return {
            "text": self.text_edit.text().strip(),
            "color": self.picker.color_hex(),
            "assign": self.assign_check.isChecked(),
        }
