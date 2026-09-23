"""快捷键：统一注册、分组登记与总览文本。

各页面把自己的快捷键交给 `ShortcutManager` 注册，注册时带上「分组 + 功能名」，
`F1` 打开的总览对话框据此列出全部快捷键（避免散落各处后无从查起）。

约定：
* 页面级快捷键注册在页面控件上（`WidgetWithChildrenShortcut`），只在焦点位于该页时生效；
* 画布内的数字 / 方向键等由 `AnnotationCanvas.keyPressEvent` 处理，
  避免与输入框冲突（在输入框里打字不应触发选类别）。
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QKeySequence, QShortcut

from src.utils.logger import get_logger

logger = get_logger("shortcuts")

# 快捷键总览的分组顺序
GROUP_ORDER = ["通用", "项目", "图库 / 检查", "标注", "训练 / 评估", "显示"]


class ShortcutManager(QObject):
    """注册并记录快捷键。"""

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        # (分组, 功能, 快捷键, 标签)：标签用于整组撤销（如页面切换键位重建）
        self._items: list[tuple[str, str, str, str]] = []
        self._shortcuts: list[tuple[str, QShortcut]] = []

    # -----------------------------------------------------------
    # 注册
    # -----------------------------------------------------------
    def register(
        self,
        widget,
        sequence: str,
        callback: Callable[[], None],
        name: str,
        group: str = "通用",
        global_scope: bool = False,
        tag: str = "",
    ) -> QShortcut:
        """在 `widget` 上注册快捷键并登记到总览。

        Args:
            global_scope: True 时在整个窗口生效（如页面切换、撤销），否则仅焦点在该控件内生效。
            tag: 分组标签，可按标签整组撤销（见 `remove_tag`）。
        """
        shortcut = QShortcut(QKeySequence(sequence), widget)
        shortcut.setContext(
            Qt.ShortcutContext.WindowShortcut if global_scope
            else Qt.ShortcutContext.WidgetWithChildrenShortcut
        )
        shortcut.activated.connect(callback)
        self._shortcuts.append((str(tag), shortcut))
        self._record(group, name, sequence, tag)
        return shortcut

    def remove_tag(self, tag: str) -> None:
        """撤销该标签下的全部快捷键（含总览登记），用于键位动态重建。"""
        self._items = [row for row in self._items if row[3] != tag]
        keep: list[tuple[str, QShortcut]] = []
        for item_tag, shortcut in self._shortcuts:
            if item_tag == tag:
                shortcut.setEnabled(False)
                shortcut.deleteLater()
                continue
            keep.append((item_tag, shortcut))
        self._shortcuts = keep

    def record(self, group: str, name: str, sequence: str, tag: str = "") -> None:
        """只登记到总览（快捷键本身由菜单 QAction 等提供）。"""
        pretty = QKeySequence(sequence).toString(QKeySequence.SequenceFormat.NativeText)
        self._items.append((group, name, pretty or sequence, str(tag)))

    def _record(self, group: str, name: str, sequence: str, tag: str = "") -> None:
        self.record(group, name, sequence, tag)

    # -----------------------------------------------------------
    # 总览
    # -----------------------------------------------------------
    def items(self) -> list[tuple[str, str, str]]:
        """[(分组, 功能, 快捷键)]，按分组顺序排列。"""
        order = {name: index for index, name in enumerate(GROUP_ORDER)}
        rows = sorted(
            self._items,
            key=lambda row: (order.get(row[0], len(order)), row[2], row[1]),
        )
        return [(group, name, sequence) for group, name, sequence, _tag in rows]

    def count(self) -> int:
        return len(self._items)
