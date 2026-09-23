"""后端注册表：按任务查表取后端适配器。

新增一个后端（OCR / U-Net 等）时：在 `src/utils/tasks.py` 的对应任务
`backends` 里加上 key，并在本包内实现适配器后 `register()` 即可，
训练、校验、模型候选与产物识别都会自动走新后端（见《开发文档.md》§8.2.2）。
"""

from __future__ import annotations

from src.services.backends.anomalib import AnomalibBackend
from src.services.backends.base import (
    Artifact,
    BackendAdapter,
    EnvReport,
    Problem,
    pause_file,
)
from src.services.backends.ocr import OcrBackend
from src.services.backends.unet import UnetBackend
from src.services.backends.yolo import YoloBackend
from src.utils.tasks import backend_candidates

__all__ = [
    "Artifact",
    "BackendAdapter",
    "EnvReport",
    "Problem",
    "anomalib",
    "get",
    "all_backends",
    "for_task",
    "for_weights",
    "keys",
    "pause_file",
    "register",
    "resolve",
    "yolo",
]

# 默认后端（任务未声明或声明的后端未注册时回退）
DEFAULT_BACKEND = "yolo"

REGISTRY: dict[str, BackendAdapter] = {}


def register(adapter: BackendAdapter) -> BackendAdapter:
    """注册后端适配器（同 key 覆盖）。"""
    REGISTRY[adapter.key] = adapter
    return adapter


def get(key: str) -> BackendAdapter | None:
    """按 key 取后端适配器（未注册返回 None）。"""
    return REGISTRY.get(str(key))


def keys() -> list[str]:
    """已注册的后端 key（注册顺序）。"""
    return list(REGISTRY)


def all_backends() -> list[BackendAdapter]:
    return list(REGISTRY.values())


def for_task(task: str) -> BackendAdapter:
    """按任务的默认后端取适配器（任务的 backends 按优先级排列）。"""
    for key in backend_candidates(task):
        adapter = REGISTRY.get(key)
        if adapter is not None:
            return adapter
    return REGISTRY[DEFAULT_BACKEND]


def resolve(config) -> BackendAdapter:
    """按训练配置选择后端（第 1 期：由训练任务决定）。"""
    task = str(getattr(config, "task_type", "") or "")
    return for_task(task)


def for_weights(weights: str, task: str = "") -> BackendAdapter:
    """按权重后缀 + 任务选择后端：`.ckpt` 一律走 Anomalib（异常检测检查点）。"""
    if str(weights).lower().endswith(".ckpt"):
        adapter = REGISTRY.get("anomalib")
        if adapter is not None:
            return adapter
    return for_task(task or "detect")


register(YoloBackend())
register(AnomalibBackend())
register(UnetBackend())
register(OcrBackend())
