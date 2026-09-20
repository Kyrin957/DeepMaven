"""设备标识解析：把界面取值翻译成训练 / 推理后端可接受的具体设备。

界面上的设备下拉是 `auto / cpu / cuda:0 (显卡名)`，而 Ultralytics 不接受 `"auto"`
（会抛 `Invalid CUDA 'device=auto' requested`），Anomalib 走 Lightning 的
`accelerator/devices`。本模块统一解析：

    * auto / 空        → 有可用 CUDA 时解析为 `"0"`，否则 `"cpu"`（显式，便于日志核对）
    * cuda:0 / cuda:1  → `"0"` / `"1"`（Ultralytics 的 GPU 序号写法）
    * cpu / mps / 0,1  → 原样

同时提供 `cuda_available / cuda_name / device_options / device_label`，
供界面显示「当前实际会用到哪个设备」。
"""

from __future__ import annotations


def _torch():
    """惰性导入 torch（未安装时返回 None）。"""
    try:
        import torch
    except ImportError:
        return None
    return torch


def cuda_available() -> bool:
    """CUDA 是否可用（torch 缺失或驱动异常时视为不可用）。"""
    torch = _torch()
    if torch is None:
        return False
    try:
        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001 - 驱动异常不应带崩界面
        return False


def cuda_count() -> int:
    """可用显卡数量。"""
    torch = _torch()
    if torch is None:
        return 0
    try:
        return int(torch.cuda.device_count()) if torch.cuda.is_available() else 0
    except Exception:  # noqa: BLE001
        return 0


def cuda_name(index: int = 0) -> str:
    """显卡名称（取不到返回空串）。"""
    if index < 0 or index >= cuda_count():
        return ""
    try:
        return str(_torch().cuda.get_device_name(index))
    except Exception:  # noqa: BLE001
        return ""


def _base(device: str | None) -> str:
    """去掉界面上的括注（如 `cuda:0 (RTX 4070)` → `cuda:0`）。"""
    return str(device or "").split("(")[0].strip()


def normalize_device(device: str | None) -> str:
    """把设备标识转为后端可接受的具体设备（auto 会解析成 0 / cpu）。"""
    text = _base(device)
    lowered = text.lower()
    if lowered in ("", "auto", "default", "none"):
        return "0" if cuda_available() else "cpu"
    if lowered.startswith("cuda:"):
        return text[5:].strip() or "0"
    if lowered == "cuda":
        return "0"
    return text


def device_options() -> list[str]:
    """界面设备下拉候选项（有显卡时带上显卡名，便于一眼确认）。"""
    options = ["auto", "cpu"]
    if cuda_available():
        name = cuda_name(0)
        options.append(f"cuda:0 ({name})" if name else "cuda:0")
    return options


def device_label(device: str | None) -> str:
    """界面上显示的「实际使用的设备」。"""
    resolved = normalize_device(device)
    if resolved == "cpu":
        return "CPU（未检测到 CUDA）" if not cuda_available() else "CPU"
    index = resolved.split(",")[0].strip()
    name = cuda_name(int(index)) if index.isdigit() else ""
    return f"GPU {index}" + (f" · {name}" if name else "")
