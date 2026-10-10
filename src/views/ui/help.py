"""卡片帮助：「?」按钮 + 参数说明弹窗（各页复用）。

用法::

    from src.views.ui import HelpEntry, card_title_row

    help_entries = [
        HelpEntry("训练轮数", "整个训练集被完整学习的次数", "先跑 50~100 轮看曲线",
                  "小数据集 100 轮起，收敛后靠早停收尾"),
    ]
    layout.addWidget(card_title_row(card, "训练参数", help_entries, intro="..."))

约定
----
* 按钮为 24px 的 `FluentIcon.HELP`（圆问号，参照 Halcon DLT 的参数说明入口），
  含义写 tooltip（§10）；
* 弹窗按「参数名 / 含义 / 调试 / 建议」四段组织——**帮助弹窗是允许写说明的
  地方**（§10 限制的是界面正文里的解释句），因此这里要把「怎么调」写清楚；
* 弹窗尺寸按屏幕可用区域收敛（§8 同类要求：不得超出屏幕）。
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QApplication, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon,
    MessageBoxBase,
    ScrollArea,
    StrongBodyLabel,
    SubtitleLabel,
    TransparentToolButton,
)

from src.views.ui import tokens as T
from src.views.ui.containers import ParamGroup


@dataclass(frozen=True)
class HelpEntry:
    """一条参数说明：名字 / 含义 / 怎么调 / 建议值。"""

    name: str
    what: str
    how: str = ""
    tip: str = ""


class ParamHelpDialog(MessageBoxBase):
    """参数说明弹窗：卡片标题旁的「?」打开，逐条列出含义与调试方法。"""

    def __init__(
        self,
        parent=None,
        title: str = "参数说明",
        entries: tuple[HelpEntry, ...] = (),
        intro: str = "",
        width: int = 560,
        height: int = 560,
    ):
        # qf 的 MessageBoxBase 需要一个 parent 窗口（内部按 parent 尺寸铺遮罩），
        # 没传时退回当前活动窗口，避免 `parent.width()` 崩在 None 上
        if parent is None:
            parent = QApplication.activeWindow()
        super().__init__(parent)
        self.titleLabel = SubtitleLabel(f"{title} · 参数说明", self)
        self.viewLayout.addWidget(self.titleLabel)

        if intro:
            label = CaptionLabel(intro, self)
            label.setWordWrap(True)
            self.viewLayout.addWidget(label)

        area = ScrollArea(self)
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        column = QVBoxLayout(body)
        column.setContentsMargins(0, 0, T.SPACE_SM, 0)
        column.setSpacing(T.SPACE_MD)
        for entry in entries:
            group = ParamGroup(entry.name, body)
            text = BodyLabel(entry.what, group)
            text.setWordWrap(True)
            group.add_widget(text)
            if entry.how:
                how = CaptionLabel(f"怎么调：{entry.how}", group)
                how.setWordWrap(True)
                group.add_widget(how)
            if entry.tip:
                tip = CaptionLabel(f"建议：{entry.tip}", group)
                tip.setWordWrap(True)
                group.add_widget(tip)
            column.addWidget(group)
        column.addStretch(1)
        area.setWidget(body)
        self.viewLayout.addWidget(area)

        # 尺寸按屏幕可用区域收敛（高分屏 / 小屏笔记本都放得下）
        screen = parent.screen() if hasattr(parent, "screen") else None
        available = screen.availableGeometry() if screen is not None else None
        if available is not None:
            width = min(width, max(360, available.width() - 80))
            height = min(height, max(280, available.height() - 120))
        self.widget.setFixedWidth(width)
        area.setMinimumHeight(height)
        self.cancelButton.setVisible(False)
        self.yesButton.setText("知道了")


def help_button(
    parent,
    title: str,
    entries: tuple[HelpEntry, ...],
    intro: str = "",
) -> TransparentToolButton:
    """卡片标题旁的「?」按钮：点击弹出该卡片的参数说明。"""
    button = TransparentToolButton(FluentIcon.HELP, parent)
    button.setFixedSize(T.ICON_BTN_SM, T.ICON_BTN_SM)
    button.setIconSize(QSize(T.ICON_GLYPH_SM, T.ICON_GLYPH_SM))
    button.setToolTip("参数说明")

    def open_help() -> None:
        dialog = ParamHelpDialog(
            button.window(), title=title, entries=tuple(entries), intro=intro
        )
        dialog.exec()

    button.clicked.connect(open_help)
    return button


def card_title_row(
    card,
    title: str,
    entries: tuple[HelpEntry, ...] | list[HelpEntry] | None = None,
    intro: str = "",
) -> QWidget:
    """卡片标题行：`标题 + [?]`（给了说明条目才出现按钮）。"""
    holder = QWidget(card)
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(T.SPACE_SM)
    row.addWidget(StrongBodyLabel(title, holder))
    if entries:
        row.addWidget(help_button(holder, title, entries, intro))
    row.addStretch(1)
    return holder
