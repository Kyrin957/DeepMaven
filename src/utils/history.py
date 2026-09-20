"""撤销 / 重做：进程级命令栈。

用法（在各 ViewModel 里改动数据前先登记）：

```python
from src.utils.history import stack

stack().push(
    "修改标注",
    lambda: self._restore(path, before),
    lambda: self._restore(path, after),
    key=f"ann:{path}",     # 同一个 key 的连续改动合并为一步（如拖动、连续输入）
)
```

设计要点：
* 命令只存「做什么」，不关心数据怎么改 —— 各 VM 用快照闭包实现即可；
* `key` 相同且相邻时合并（保留最早的 undo 与最新的 redo），避免一次拖动产生几十步；
* 栈是单例（与 logger 一致），步数上限默认 100。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QObject, Signal

from src.utils.logger import get_logger

logger = get_logger("history")

DEFAULT_LIMIT = 100


@dataclass
class Command:
    """一步可撤销操作。"""

    label: str
    undo_fn: Callable[[], None]
    redo_fn: Callable[[], None]
    key: str = ""


class CommandStack(QObject):
    """撤销 / 重做栈。"""

    changed = Signal()          # 栈内容变化（供界面刷新可用状态）

    def __init__(self, limit: int = DEFAULT_LIMIT, parent: QObject | None = None):
        super().__init__(parent)
        self._limit = max(1, int(limit))
        self._done: list[Command] = []
        self._undone: list[Command] = []

    # -----------------------------------------------------------
    # 查询
    # -----------------------------------------------------------
    def can_undo(self) -> bool:
        return bool(self._done)

    def can_redo(self) -> bool:
        return bool(self._undone)

    def undo_label(self) -> str:
        return self._done[-1].label if self._done else ""

    def redo_label(self) -> str:
        return self._undone[-1].label if self._undone else ""

    def depth(self) -> tuple[int, int]:
        """(可撤销步数, 可重做步数)。"""
        return len(self._done), len(self._undone)

    def status_text(self) -> str:
        """供状态栏 / 提示展示的一行文本。"""
        parts = []
        if self.can_undo():
            parts.append(f"可撤销：{self.undo_label()}")
        if self.can_redo():
            parts.append(f"可重做：{self.redo_label()}")
        return " · ".join(parts)

    # -----------------------------------------------------------
    # 入栈 / 执行
    # -----------------------------------------------------------
    def push(
        self,
        label: str,
        undo_fn: Callable[[], None],
        redo_fn: Callable[[], None],
        key: str = "",
    ) -> None:
        """登记一步操作。

        Args:
            label: 展示给用户的动作名（如「删除标注」）。
            undo_fn: 回退动作（尚未执行的，只注册）。
            redo_fn: 重做动作（尚未执行的，只注册）。
            key: 非空且与栈顶命令相同则合并（保留旧 undo + 新 redo）。
        """
        if key and self._done and self._done[-1].key == key:
            self._done[-1] = Command(label, self._done[-1].undo_fn, redo_fn, key)
        else:
            self._done.append(Command(label, undo_fn, redo_fn, key))
        if len(self._done) > self._limit:
            del self._done[: len(self._done) - self._limit]
        self._undone.clear()
        self.changed.emit()

    # -----------------------------------------------------------
    # 撤销 / 重做
    # -----------------------------------------------------------
    def undo(self) -> str:
        """撤销一步，返回命令名（无可撤销时返回空串）。"""
        if not self._done:
            return ""
        command = self._done.pop()
        try:
            command.undo_fn()
        except Exception as exc:  # noqa: BLE001 - 回退失败不应让界面崩掉
            logger.warning("撤销「%s」失败: %s", command.label, exc)
            self.changed.emit()
            return ""
        self._undone.append(command)
        self.changed.emit()
        return command.label

    def redo(self) -> str:
        """重做一步，返回命令名（无可重做时返回空串）。"""
        if not self._undone:
            return ""
        command = self._undone.pop()
        try:
            command.redo_fn()
        except Exception as exc:  # noqa: BLE001 - 重做失败不应让界面崩掉
            logger.warning("重做「%s」失败: %s", command.label, exc)
            self.changed.emit()
            return ""
        self._done.append(command)
        self.changed.emit()
        return command.label

    def clear(self) -> None:
        self._done.clear()
        self._undone.clear()
        self.changed.emit()


_STACK = CommandStack()


def stack() -> CommandStack:
    """全局撤销栈（各 VM 共用）。"""
    return _STACK
