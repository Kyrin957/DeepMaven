r"""预训练模型的本地目录约定与解析。

程序用到的预训练权重**随程序发布**，放在项目目录内（``models/供应商/模型``），
使开发、测试与最终用户都不需要联网下载：

    models/ultralytics/yolo11n.pt            YOLO 官方权重（检测，训练起点）
    models/ultralytics/yolo11n-cls.pt        —— 分类 / 分割（-seg）/ 旋转框（-obb）
    models/ultralytics/yolo11n-seg.pt
    models/ultralytics/yolo11n-obb.pt
    models/ultralytics/yolo26x.pt            同上前缀 yolo26n/s/m/l/x
    models/ultralytics/mobile_sam.pt         SAM 预标注细化用的轻量分割模型
    models/anomalib/huggingface/             Anomalib 骨干（timm）的 HuggingFace 缓存
    models/anomalib/torch/                   torch.hub / torchvision 权重缓存

清单与下载方法见 ``models/README.md``。

用法::

    from src.utils.pretrained import apply_model_env, resolve_pretrained

    apply_model_env()                                  # 进程入口调用一次
    weights = resolve_pretrained("yolo11n-cls.pt")     # → models/ultralytics/...
"""

from __future__ import annotations

import os
from pathlib import Path

from src.utils.constants import MODELS_DIR

# 供应商目录（与 models/README.md 的约定一致）
ULTRALYTICS_DIR = MODELS_DIR / "ultralytics"
ANOMALIB_DIR = MODELS_DIR / "anomalib"
# Anomalib 依赖的两个缓存根（随程序发布，避免用户下载骨干权重）
HF_HOME_DIR = ANOMALIB_DIR / "huggingface"
TORCH_HOME_DIR = ANOMALIB_DIR / "torch"

# 官方权重默认下载源（重新下载时用，见 models/README.md）
ULTRALYTICS_RELEASE = "https://github.com/ultralytics/assets/releases/download/v8.4.0"


def resolve_pretrained(name: str | Path) -> str:
    """把官方权重名解析为项目内路径；项目内没有时原样返回。

    只处理「纯文件名」输入（``yolo11n.pt`` / ``mobile_sam.pt``）：

    * 命中的名字 → ``models/<供应商>/<文件>`` 的完整路径（离线可用）；
    * 用户自定义权重、带目录的路径 → 原样返回（不改变用户意图）；
    * 项目内缺失的官方权重 → 原样返回（交由 Ultralytics 自行下载）。
    """
    text = str(name or "").strip()
    if not text:
        return text
    if Path(text).name != text:          # 带目录：按调用方给的路径处理
        return text
    for directory in (ULTRALYTICS_DIR, ANOMALIB_DIR):
        candidate = directory / text
        if candidate.is_file():
            return str(candidate)
    return text


def apply_model_env() -> None:
    """把模型缓存目录指向项目内 ``models/``（发布后离线可用）。

    * ``HF_HOME``：timm / HuggingFace 权重（Anomalib 各模型的骨干）
    * ``TORCH_HOME``：torch.hub / torchvision 权重
    * ``HF_ENDPOINT``：缺权重时的下载镜像（不覆盖用户已有设置）

    幂等，可在 GUI 入口与各 worker 子进程重复调用。开发期若项目内缺少
    某个权重，仍允许联网补齐（不强制离线）。
    """
    for directory in (MODELS_DIR, ULTRALYTICS_DIR, ANOMALIB_DIR,
                      HF_HOME_DIR, TORCH_HOME_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(HF_HOME_DIR)
    os.environ["TORCH_HOME"] = str(TORCH_HOME_DIR)
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
