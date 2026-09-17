"""后台任务线程：把耗时操作放到 QThread，保持界面响应。

被执行的函数签名约定为：

    fn(progress: Callable[[int, str], None], is_cancelled: Callable[[], bool]) -> object
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QThread, Signal


class FunctionWorker(QThread):
    """在后台线程执行一个可调用对象，并转发进度 / 结果 / 错误。"""

    progress = Signal(int, str)      # 百分比 0~100, 描述
    finishedOk = Signal(object)      # 返回值
    failed = Signal(str)             # 错误信息

    def __init__(self, fn: Callable, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._cancelled = False

    def cancel(self) -> None:
        """请求取消（由被执行的函数自查）。"""
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def run(self) -> None:  # noqa: D102 - QThread 约定
        try:
            result = self._fn(self._emit_progress, lambda: self._cancelled)
        except Exception as exc:  # noqa: BLE001 - 后台任务异常需回传界面
            self.failed.emit(str(exc))
            return
        self.finishedOk.emit(result)

    def _emit_progress(self, percent: int, text: str = "") -> None:
        self.progress.emit(int(percent), text)
