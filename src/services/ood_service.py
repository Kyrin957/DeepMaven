"""分布外（OOD）检测：用训练好的模型提取特征，按距离判定「是不是训练分布里的图」。

思路（自研，不改动 Ultralytics）：

    1. 用「已知 / 正常」样本集提取特征（分类头的**前一层**输出）
    2. 统计各维均值与标准差，把样本距离的 95 分位作为阈值
    3. 新样本的距离超过阈值即判为分布外（OOD），可用于拦截「模型没见过的图」

统计结果（`OodStats`）可保存为 JSON，之后直接加载判定，不必重复拟合。
特征层（`OodService`）目前支持**分类模型**（YOLO 分类权重的 `model.model` 骨干 +
`classifier` 头）；检测 / 分割模型没有可直接取用的分类头，会给出明确提示。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from src.utils.logger import get_logger

logger = get_logger("ood")

# 距离阈值默认取训练样本距离的分位数
DEFAULT_PERCENTILE = 95.0


@dataclass
class OodStats:
    """OOD 判定所需的统计量（与模型无关，可单独加载）。"""

    dim: int = 0
    mean: list[float] = field(default_factory=list)
    std: list[float] = field(default_factory=list)
    threshold: float = 0.0
    samples: int = 0
    source: str = ""
    weights: str = ""

    # -----------------------------------------------------------
    def ready(self) -> bool:
        """统计量是否可用（已拟合且维度一致）。"""
        return bool(self.dim and len(self.mean) == self.dim
                    and len(self.std) == self.dim)

    def distance(self, features) -> float:
        """简化马氏距离（各维独立）：`sqrt(mean(((f - mean) / std)²))`。

        维度不符返回 -1（调用方按「无法判定」处理）。
        """
        import numpy as np

        if not self.ready():
            return -1.0
        array = np.asarray(features, dtype=np.float32).ravel()
        if array.size != self.dim:
            return -1.0
        delta = (array - np.asarray(self.mean, dtype=np.float32)) / np.maximum(
            np.asarray(self.std, dtype=np.float32), 1e-6
        )
        return float(np.sqrt(float(np.mean(delta * delta))))

    def is_ood(self, features) -> bool:
        value = self.distance(features)
        return bool(value >= 0.0 and value > self.threshold)

    # -----------------------------------------------------------
    @classmethod
    def from_features(
        cls, features: list, percentile: float = DEFAULT_PERCENTILE,
        source: str = "", weights: str = "",
    ) -> "OodStats":
        """由一组特征拟合统计量（阈值 = 距离分位数）。"""
        import numpy as np

        if not features:
            return cls()
        if hasattr(features[0], "__len__"):
            matrix = np.asarray([list(item) for item in features], dtype=np.float32)
        else:
            matrix = np.asarray(features, dtype=np.float32)
        if matrix.size == 0:
            return cls()
        if matrix.ndim == 1:
            matrix = matrix.reshape(1, -1)
        mean = matrix.mean(axis=0)
        std = np.maximum(matrix.std(axis=0), 1e-6)
        scale = np.abs((matrix - mean) / std)
        distances = np.sqrt(np.mean(scale * scale, axis=1))
        return cls(
            dim=int(matrix.shape[1]),
            mean=[float(value) for value in mean],
            std=[float(value) for value in std],
            threshold=float(np.percentile(distances, float(percentile))),
            samples=int(matrix.shape[0]),
            source=str(source),
            weights=str(weights),
        )

    # -----------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "dim": self.dim,
            "mean": list(self.mean),
            "std": list(self.std),
            "threshold": self.threshold,
            "samples": self.samples,
            "source": self.source,
            "weights": self.weights,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "OodStats":
        return cls(
            dim=int(data.get("dim", 0) or 0),
            mean=[float(value) for value in (data.get("mean") or [])],
            std=[float(value) for value in (data.get("std") or [])],
            threshold=float(data.get("threshold", 0.0) or 0.0),
            samples=int(data.get("samples", 0) or 0),
            source=str(data.get("source", "") or ""),
            weights=str(data.get("weights", "") or ""),
        )

    def save(self, path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return target

    @classmethod
    def load(cls, path) -> "OodStats":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


class OodService:
    """特征层：加载模型、提取特征、拟合统计量并判定。"""

    def __init__(
        self, weights: str = "", imgsz: int = 224, device: str = "auto"
    ):
        self.weights = str(weights or "")
        self.imgsz = max(32, int(imgsz or 224))
        self.device = str(device or "auto")
        self._model = None

    # -----------------------------------------------------------
    @staticmethod
    def is_available() -> bool:
        """是否可用（需要 torch / Ultralytics）。"""
        try:
            import ultralytics  # noqa: F401
        except ImportError:
            return False
        return True

    @staticmethod
    def supported(weights: str) -> bool:
        """该权重是否为可用的分类模型（其余任务不支持）。"""
        try:
            from ultralytics import YOLO

            module = getattr(YOLO(str(weights)).model, "model", None)
        except Exception:  # noqa: BLE001 - 加载失败按不支持处理
            return False
        return bool(hasattr(module, "classifier")) if module is not None else False

    # -----------------------------------------------------------
    def _load(self):
        """懒加载模型（只取分类骨干）。"""
        if self._model is not None:
            return self._model
        if not self.weights:
            raise ValueError("请先选择模型权重")
        from ultralytics import YOLO

        module = YOLO(self.weights).model
        if not hasattr(module, "classifier") or getattr(module, "model", None) is None:
            raise ValueError(
                "OOD 检测目前只支持分类模型（检测 / 分割没有可直接取用的分类头）"
            )
        self._model = module
        return self._model

    def _tensor(self, path):
        """读入一张图 → 归一化后的张量（与训练时的尺寸对齐）。"""
        import numpy as np
        import torch
        from PIL import Image

        with Image.open(path) as handle:
            image = handle.convert("RGB").resize((self.imgsz, self.imgsz))
        array = np.asarray(image, dtype=np.float32) / 255.0
        return torch.from_numpy(array).permute(2, 0, 1).unsqueeze(0)

    def features(self, path) -> list[float]:
        """提取一张图的特征向量（分类头之前的输出）。"""
        import torch

        module = self._load()
        device = self.device if self.device != "auto" else "cpu"
        tensor = self._tensor(path).to(device)
        module = module.to(device)
        module.eval()
        with torch.no_grad():
            output = module.model(tensor)
        features = output.flatten(1) if output.dim() > 2 else output
        return [float(value) for value in features[0].cpu().tolist()]

    # -----------------------------------------------------------
    def fit(
        self, image_dir, percentile: float = DEFAULT_PERCENTILE,
        progress=None, is_cancelled=None,
    ) -> OodStats:
        """用目录里的图片拟合统计量。"""
        from src.services.dataset_service import DatasetService

        images = DatasetService.scan_images_multi([str(image_dir or "")]) \
            if str(image_dir or "") else []
        if not images:
            raise ValueError("目录下没有可用图片")
        collected: list[list[float]] = []
        total = len(images)
        for index, path in enumerate(images):
            if is_cancelled is not None and is_cancelled():
                break
            try:
                collected.append(self.features(path))
            except Exception as exc:  # noqa: BLE001 - 单张失败不中断整体
                logger.debug("OOD 特征提取失败 %s: %s", path, exc)
            if progress is not None:
                progress(int(100 * (index + 1) / total), f"提取特征 {index + 1}/{total}")
        if not collected:
            raise ValueError("没有提取到任何特征")
        return OodStats.from_features(
            collected, percentile=percentile, source=str(image_dir),
            weights=self.weights,
        )

    def predict(self, paths: list, stats: OodStats) -> list[dict]:
        """对若干图片做 OOD 判定。"""
        results = []
        for raw in paths or []:
            path = Path(str(raw))
            try:
                features = self.features(path)
            except Exception as exc:  # noqa: BLE001
                results.append({
                    "path": str(path), "name": path.name,
                    "distance": -1.0, "ood": False, "error": str(exc),
                })
                continue
            distance = stats.distance(features)
            results.append({
                "path": str(path), "name": path.name,
                "distance": round(float(distance), 4),
                "ood": bool(distance >= 0 and distance > stats.threshold),
                "error": "",
            })
        return results
