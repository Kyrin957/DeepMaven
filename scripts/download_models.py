#!/usr/bin/env python
r"""跨机开发：一键下载全部预训练模型到 ``models/<供应商>/``。

新电脑 clone 仓库后（或在任意缺失模型的机器上）运行一次即可：

    python scripts/download_models.py            # 默认优先国内加速镜像
    python scripts/download_models.py --direct   # 官方源直连（境外网络）

下载内容（与 ``src/utils/pretrained.py`` 的约定一致，清单见 ``models/README.md``）：

    models/ultralytics/   YOLO11 / YOLO26 全系列（n~x × 检测/分割/分类/旋转框）
                          + mobile_sam.pt，共 41 个文件
    models/anomalib/      异常检测骨干（timm resnet18 / wide_resnet50_2，
                          经 HF 镜像缓存；需已安装依赖）

特性：

* **可重复运行**：已存在且大小合理的文件自动跳过，缺失的补齐；
* **多通道**：优先 ``curl --ssl-no-revoke``（规避 Windows 证书吊销检查
  导致的下载失败），无 curl 时回退 urllib；镜像失败自动换直连；
* **清单唯一来源**：直接读取 ``YOLO_MODEL_VARIANTS`` / ``TASK_MODEL_SUFFIX``，
  新模型上线后无需改本脚本。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

# 允许 `python scripts/download_models.py`（把仓库根加入模块搜索路径）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.worker_utils import use_utf8_stdio  # noqa: E402
from src.utils.constants import TASK_MODEL_SUFFIX, YOLO_MODEL_VARIANTS  # noqa: E402
from src.utils.pretrained import (  # noqa: E402
    HF_HOME_DIR,
    ULTRALYTICS_DIR,
    ULTRALYTICS_RELEASE,
    apply_model_env,
)

# 国内 GitHub 加速镜像（实测约 3 MB/s，比直连快一倍）
MIRROR = "https://gh-proxy.com/"
# 小于该体积视为下载不完整（与 Ultralytics 的 min_bytes 判定一致）
MIN_BYTES = 100_000
# 额外权重（预标注细化用的轻量分割模型）
EXTRA_NAMES = ("mobile_sam.pt",)
# 异常检测骨干（timm 权重名；Padim/Fastflow 用前者，Patchcore/ReverseDistillation 用后者）
BACKBONES = ("resnet18", "wide_resnet50_2")


def model_names() -> list[str]:
    """需要下载的全部官方权重名（与界面模型候选一一对应）。"""
    suffixes = ("", *sorted(set(TASK_MODEL_SUFFIX.values())))
    names = [
        f"{variant['key']}{suffix}.pt"
        for variant in YOLO_MODEL_VARIANTS
        for suffix in suffixes
    ]
    return names + [name for name in EXTRA_NAMES if name not in names]


def _curl() -> str | None:
    return shutil.which("curl") or shutil.which("curl.exe")


def _fetch(url: str, target: Path, curl: str | None) -> bool:
    """下载单个 URL 到目标文件；先写 .part 再改名，失败清理。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    partial.unlink(missing_ok=True)
    try:
        if curl:
            code = subprocess.run(
                [curl, "--ssl-no-revoke", "-s", "-L", "--retry", "2",
                 "--max-time", "900", "-o", str(partial), url],
                check=False,
            ).returncode
            if code != 0:
                return False
        else:
            with urllib.request.urlopen(url, timeout=180) as response:
                partial.write_bytes(response.read())
    except Exception:  # noqa: BLE001 - 任一通道失败都换下一个源
        partial.unlink(missing_ok=True)
        return False
    if partial.is_file() and partial.stat().st_size >= MIN_BYTES:
        partial.replace(target)
        return True
    partial.unlink(missing_ok=True)
    return False


def download(target: Path, name: str, use_mirror: bool = True) -> bool:
    """下载一个官方权重（已存在且完整时跳过）。"""
    if target.is_file() and target.stat().st_size >= MIN_BYTES:
        print(f"  跳过 {name}（已存在 {target.stat().st_size / 1e6:.1f} MB）")
        return True
    url = f"{ULTRALYTICS_RELEASE}/{name}"
    sources = [f"{MIRROR}{url}", url] if use_mirror else [url, f"{MIRROR}{url}"]
    curl = _curl()
    for source in sources:
        host = source.split("/")[2]
        print(f"  下载 {name} ← {host} …", end=" ", flush=True)
        if _fetch(source, target, curl):
            print(f"OK（{target.stat().st_size / 1e6:.1f} MB）")
            return True
        print("失败，换源重试")
    print(f"  ✗ {name} 下载失败")
    return False


def download_backbones() -> bool:
    """下载异常检测骨干（timm）；依赖未装或缓存已有时给出提示。"""
    print("\n== 异常检测骨干（timm）==")
    try:
        import timm  # noqa: PLC0415 - 依赖装好后才有
    except ImportError:
        print("  跳过：未安装 timm（先按 requirements.txt 安装依赖后重跑）")
        return True
    ok = True
    for name in BACKBONES:
        print(f"  检查 {name} …", end=" ", flush=True)
        try:
            timm.create_model(name, pretrained=True)
            print("OK")
        except Exception as exc:  # noqa: BLE001 - 离线且无缓存时给出原因
            print(f"失败：{type(exc).__name__}: {exc}")
            ok = False
    return ok


def main() -> int:
    # Windows 中文控制台默认 GBK：先切 UTF-8（argparse 的中文帮助也需正常输出）
    use_utf8_stdio()

    parser = argparse.ArgumentParser(description="下载全部预训练模型到 models/")
    parser.add_argument("--direct", action="store_true",
                        help="优先官方源直连（默认优先国内加速镜像）")
    parser.add_argument("--skip-backbones", action="store_true",
                        help="跳过异常检测骨干（timm）")
    args = parser.parse_args()

    # 目录与环境（HF_HOME / TORCH_HOME 指向项目内，timm 缓存随之落位）
    apply_model_env()

    names = model_names()
    print(f"目标目录：{ULTRALYTICS_DIR}")
    print(f"\n== Ultralytics 权重（{len(names)} 个）==")
    failed = [
        name for name in names
        if not download(ULTRALYTICS_DIR / name, name, use_mirror=not args.direct)
    ]

    backbones_ok = args.skip_backbones or download_backbones()

    print()
    if failed:
        print(f"✗ 失败 {len(failed)} 个：{', '.join(failed)}")
        print("  可重跑本脚本补齐；或参考 models/README.md 手工下载。")
        return 1
    if not backbones_ok:
        print("✗ 骨干权重未就绪（HF_HOME=%s）" % HF_HOME_DIR)
        return 1
    print("√ 全部预训练模型就绪（models/ 可直接随程序发布）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
