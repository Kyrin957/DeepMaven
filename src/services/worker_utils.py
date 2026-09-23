"""训练子进程的公共工具：事件输出、UTF-8 标准输出、轮边界暂停。

事件协议（父进程 `TrainService` / `TrainViewModel` 按此解析）：
    {"type": "phase", "text": "..."}                            阶段提示
    {"type": "iteration", ...}                                  批次级进度
    {"type": "epoch", "epoch": 1, "total": 100, "metrics": {}}   每轮指标
    {"type": "metrics", "metrics": {}}                          汇总指标
    {"type": "status", "status": "paused" | "running"}          暂停 / 继续
    {"type": "done", "save_dir": ..., "best": ..., "last": ...}  完成（含产物）
    {"type": "error", "text": "..."}                            失败
"""

from __future__ import annotations

import json
import sys
import time


def emit(event: dict) -> None:
    """向父进程输出一行 JSON 事件。"""
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def use_utf8_stdio() -> None:
    """把标准输出 / 错误切到 UTF-8（Windows 中文控制台默认 GBK 会中断输出）。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            continue


def wait_if_paused(pause_file: str, poll: float = 0.5) -> bool:
    """若「暂停标记文件」存在则阻塞等待（在轮边界调用）。

    Returns:
        是否真的暂停过（供上报状态用）。
    """
    if not pause_file:
        return False
    from pathlib import Path

    marker = Path(pause_file)
    if not marker.exists():
        return False
    emit({"type": "status", "status": "paused", "text": "已暂停（等待继续）"})
    while marker.exists():
        time.sleep(max(0.05, float(poll)))
    emit({"type": "status", "status": "running", "text": "已继续训练"})
    return True
