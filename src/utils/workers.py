"""后台任务线程：把耗时操作放到工作线程，保持界面响应。

被执行的函数签名约定为：

    fn(progress: Callable[[int, str], None], is_cancelled: Callable[[], bool]) -> object

两种执行器（**接口一致**，可互换）：

* `FunctionWorker`：`QThread` 实现（默认选择）；
* `ThreadTask`：标准库 `threading.Thread` 实现——**文件操作密集**的任务用它：
  本机实测（Windows + IDE 文件系统钩子）主线程跑 Qt 事件循环时，`QThread` 里
  的 `os.stat` 被拖到 **155 ms/次**（标准库线程 39 µs/次，差 4000×），万级
  文件的落盘任务会因此「看起来卡死」（历史排查 §4.90）。信号仍由 Qt 队列连接
  回到 GUI 线程，用法与 `FunctionWorker` 完全相同。
"""

from __future__ import annotations

import threading
from typing import Callable

from PySide6.QtCore import QObject, QThread, Signal


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


class ThreadTask(QObject):
    """在**标准库线程**里执行可调用对象，用 Qt 信号回报（与 `FunctionWorker` 同接口）。

    文件操作密集的后台任务（如「执行拆分」的万级硬链接落盘）走这个：见模块
    文档的性能说明。对象本身留在 GUI 线程，信号跨线程由 Qt 队列连接投递，
    因此 `finishedOk` / `failed` 的处理函数仍在 GUI 线程执行（可直接改界面）。
    """

    progress = Signal(int, str)
    finishedOk = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._cancelled = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """启动工作线程（守护线程：进程退出时不阻塞）。"""
        self._thread = threading.Thread(
            target=self._run, name="deepmaven-task", daemon=True
        )
        self._thread.start()

    def cancel(self) -> None:
        """请求取消（由被执行的函数自查）。"""
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def isRunning(self) -> bool:  # noqa: N802 - 与 QThread 同接口
        return bool(self._thread is not None and self._thread.is_alive())

    def wait(self, timeout_ms: int = 0) -> bool:
        """等待任务结束（毫秒；0 = 一直等），返回是否已结束。"""
        if self._thread is None:
            return True
        self._thread.join(timeout_ms / 1000 if timeout_ms else None)
        return not self._thread.is_alive()

    def _run(self) -> None:
        try:
            result = self._fn(self._emit_progress, lambda: self._cancelled)
        except Exception as exc:  # noqa: BLE001 - 后台任务异常需回传界面
            self.failed.emit(str(exc))
            return
        self.finishedOk.emit(result)

    def _emit_progress(self, percent: int, text: str = "") -> None:
        self.progress.emit(int(percent), text)
