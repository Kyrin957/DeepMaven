"""设备标识归一化。

界面上设备下拉提供 `auto / cpu / cuda:0` 三种取值，但 Ultralytics 不接受
`"auto"`（会抛 `Invalid CUDA 'device=auto' requested`）。
本模块把界面取值翻译成 Ultralytics 可接受的形式：

    * 空 / auto / none  →  ""     由 Ultralytics 自动选择（有 GPU 用 GPU，否则 CPU）
    * cuda:0            →  "0"    Ultralytics 的 GPU 序号写法
    * cpu / 0 / 0,1     →  原样
"""

from __future__ import annotations


def normalize_device(device: str | None) -> str:
    """把设备标识转为 Ultralytics 可接受的值。"""
    if not device:
        return ""
    text = str(device).strip()
    lowered = text.lower()
    if lowered in ("auto", "default", "none", ""):
        return ""
    if lowered.startswith("cuda:"):
        return text[5:] or ""
    if lowered == "cuda":
        return ""
    return text
