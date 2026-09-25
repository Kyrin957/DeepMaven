"""导航页基类与通用布局辅助。

为每个导航页提供统一的「可滚动内容区」外壳，
并将内容组织为 CardWidget 卡片，保证 Fluent 风格一致。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import (
    CardWidget,
    ScrollArea,
    StrongBodyLabel,
)

from src.views.ui import tokens as T


class BasePage(QWidget):
    """导航页基类：可滚动内容区。"""

    def __init__(self, parent=None):
        super().__init__(parent)

        # 外层布局
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 可滚动内容区
        self.scroll_area = ScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_body = QWidget()
        self.content_layout = QVBoxLayout(self.scroll_body)
        self.content_layout.setContentsMargins(
            T.PAGE_PAD_H, T.SPACE_XL, T.PAGE_PAD_H, T.SPACE_XXL
        )
        self.content_layout.setSpacing(T.SPACE_XL)
        self.content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll_area.setWidget(self.scroll_body)
        root.addWidget(self.scroll_area)

    # -----------------------------------------------------------
    # 便捷方法
    # -----------------------------------------------------------
    def add_card(self, title: str) -> tuple[CardWidget, QVBoxLayout]:
        """向内容区添加一个卡片，返回 (card, card_layout)。"""
        card = CardWidget(self.scroll_body)
        card.vlayout = QVBoxLayout(card)
        card.vlayout.setContentsMargins(
            T.SPACE_2XL, T.SPACE_XL, T.SPACE_2XL, T.SPACE_XL
        )
        card.vlayout.setSpacing(T.SPACE_LG)
        if title:
            card.vlayout.addWidget(StrongBodyLabel(title))
        self.content_layout.addWidget(card)
        return card, card.vlayout

    def add_spacer(self) -> None:
        self.content_layout.addStretch(1)