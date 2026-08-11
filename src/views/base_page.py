"""导航页基类与通用布局辅助。

为每个导航页提供统一的「标题 + 描述 + 可滚动内容区」外壳，
并将内容组织为 CardWidget 卡片，保证 Fluent 风格一致。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ScrollArea,
    StrongBodyLabel,
    TitleLabel,
)


class BasePage(QWidget):
    """导航页基类：顶部标题 + 可滚动内容区。"""

    def __init__(self, title: str, description: str = "", parent=None):
        super().__init__(parent)
        self._title = title
        self._description = description

        # 外层布局
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 标题头
        header = _Header(self._title, self._description)
        root.addWidget(header)

        # 可滚动内容区
        self.scroll_area = ScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_body = QWidget()
        self.content_layout = QVBoxLayout(self.scroll_body)
        self.content_layout.setContentsMargins(24, 16, 24, 24)
        self.content_layout.setSpacing(16)
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
        card.vlayout.setContentsMargins(20, 16, 20, 16)
        card.vlayout.setSpacing(12)
        if title:
            card.vlayout.addWidget(StrongBodyLabel(title))
        self.content_layout.addWidget(card)
        return card, card.vlayout

    def add_spacer(self) -> None:
        self.content_layout.addStretch(1)


class _Header(QWidget):
    """页面标题头。"""

    def __init__(self, title: str, description: str, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 12)
        layout.setSpacing(4)
        layout.addWidget(TitleLabel(title))
        if description:
            layout.addWidget(CaptionLabel(description))
        btm = QWidget(self)
        btm.setFixedHeight(1)
        layout.addWidget(btm)