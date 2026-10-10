"""设备标识解析：把界面取值翻译成训练 / 推理后端可接受的具体设备。

界面上的设备下拉是 `auto / cpu / cuda:0 (显卡名)`，而 Ultralytics 不接受 `"auto"`
（会抛 `Invalid CUDA 'device=auto' requested`），Anomalib 走 Lightning 的
`accelerator/devices`。本模块统一解析：

    * auto / 空        → 有可用 CUDA 时解析为 `"0"`，否则 `"cpu"`（显式，便于日志核对）
    * cuda:0 / cuda:1  → `"0"` / `"1"`（Ultralytics 的 GPU 序号写法）
    * cpu / mps / 0,1  → 原样

同时提供 `cuda_available / cuda_build_available / cuda_name / cpu_name /
cpu_threads / device_options / device_label / device_reason / device_help`，
供界面显示「当前实际会用到哪个设备（带型号）」，并在用不到 GPU 时**说明原因**
（CPU 版 PyTorch / 无可用 CUDA）。
"""

from __future__ import annotations

import os
import platform
import re
import sys


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


def cuda_build_available() -> bool:
    """PyTorch **是否带 CUDA 支持**（只看编译，不看驱动 / 显卡）。

    CPU 版 PyTorch（``2.13.0+cpu``）的 ``torch.version.cuda`` 为 ``None``、
    ``torch.backends.cuda.is_built()`` 为 ``False``——这时不论机器上有没有
    NVIDIA 显卡，``torch.cuda.is_available()`` 恒为 False（历史排查 §4.74）。
    """
    torch = _torch()
    if torch is None:
        return False
    try:
        built = bool(torch.backends.cuda.is_built())
    except Exception:  # noqa: BLE001 - 老版本 / 特殊构建
        built = False
    try:
        version = str(getattr(torch.version, "cuda", "") or "")
    except Exception:  # noqa: BLE001
        version = ""
    return built or bool(version)


def device_reason() -> str:
    """当前**用不到 GPU 的原因**（短句，供界面提示；能用时返回空串）。

    区分三种情况，避免都显示「未检测到 CUDA」而无法定位：
      * 没装 PyTorch → 「未安装 PyTorch」
      * 装的是 CPU 版 → 「PyTorch 为 CPU 版」（装 CUDA 版即可）
      * 有 CUDA 版但用不了 → 「未检测到可用 CUDA」（驱动 / 显卡侧）
    """
    torch = _torch()
    if torch is None:
        return "未安装 PyTorch"
    if cuda_available():
        return ""
    if not cuda_build_available():
        return "PyTorch 为 CPU 版"
    return "未检测到可用 CUDA"


def device_help() -> str:
    """设备提示的 tooltip：CPU 型号 + 按原因给出下一步（说明只走 tooltip，§10）。"""
    head = ""
    name = cpu_name()
    if name:
        threads = cpu_threads()
        head = f"处理器：{name}" + (f"（{threads} 线程）" if threads else "") + "\n"
    torch = _torch()
    if torch is None:
        return head + "未安装 PyTorch：pip install -r requirements.txt"
    if cuda_available():
        return head + "当前可用 GPU，训练默认走显卡。"
    if not cuda_build_available():
        return head + (
            "当前 PyTorch 是 CPU 版（无 CUDA 支持），装了显卡也不会用它训练。\n"
            "启用 GPU：pip uninstall torch torchvision\n"
            "再按 requirements-gpu.txt 安装 CUDA 版（国内可用其中给出的"
            "阿里云镜像地址），装完重启程序。"
        )
    return head + (
        "PyTorch 已带 CUDA 支持，但当前检测不到可用 GPU。\n"
        "请确认显卡驱动版本（nvidia-smi 可查）与显卡是否可见。"
    )


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


def _clean_cpu_name(name: str) -> str:
    """把 CPU 全名收敛成可读型号。

    ``AMD Ryzen 9 7950X 16-Core Processor`` → ``AMD Ryzen 9 7950X``；
    ``Intel(R) Core(TM) i9-13900K CPU @ 3.00GHz`` → ``Intel Core i9-13900K``。
    """
    text = re.sub(r"\((?:R|TM)\)", " ", str(name or ""), flags=re.IGNORECASE)
    text = re.sub(r"\b\d+\s*-\s*Core\b.*$", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bCPU\b.*$", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"@\s*[\d.]+\s*GHz", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bProcessor\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bwith Radeon Graphics\b", " ", text, flags=re.IGNORECASE)
    return " ".join(text.split())


def cpu_name() -> str:
    """CPU 型号（Windows 读注册表里的商品名，其它平台退回 platform.processor）。"""
    raw = ""
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as key:
                raw = str(winreg.QueryValueEx(key, "ProcessorNameString")[0])
        except OSError:
            raw = ""
    if not raw:
        raw = str(platform.processor() or "")
    return _clean_cpu_name(raw)


def cpu_threads() -> int:
    """逻辑处理器数量（线程数），取不到返回 0。"""
    try:
        return int(os.cpu_count() or 0)
    except (TypeError, ValueError):
        return 0


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
    """界面设备下拉候选项：**带型号**，一眼看清实际用的是哪块硬件。

    ``['auto', 'cpu (AMD Ryzen 9 7950X)', 'cuda:0 (NVIDIA GeForce RTX 5060 Ti)']``
    ——括号里的型号只是显示用，`normalize_device` 取括号前的主体。
    """
    name = cpu_name()
    options = ["auto", f"cpu ({name})" if name else "cpu"]
    if cuda_available():
        gpu = cuda_name(0)
        options.append(f"cuda:0 ({gpu})" if gpu else "cuda:0")
    return options


def device_label(device: str | None) -> str:
    """界面上显示的「实际使用的设备」。

    CPU 时带上**用不到 GPU 的原因**（CPU 版 PyTorch / 无可用 CUDA），
    便于一眼定位是驱动问题还是依赖问题（历史排查 §4.74）。
    """
    resolved = normalize_device(device)
    if resolved == "cpu":
        name = cpu_name()
        reason = device_reason()
        text = f"CPU · {name}" if name else "CPU"
        return f"{text}（{reason}）" if reason else text
    index = resolved.split(",")[0].strip()
    name = cuda_name(int(index)) if index.isdigit() else ""
    return f"GPU {index}" + (f" · {name}" if name else "")
