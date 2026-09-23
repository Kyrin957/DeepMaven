"""预测结果契约（Box / Prediction）。

各后端把推理输出统一成该结构，供**导出后精度回归**、报告与后续 OCR 复用；
现有服务里的原始字段（`class_id / confidence / x1...` 与 `score / label`）
仍在界面链路里使用，逐步迁移到本契约（见《开发文档.md》§8.2.3）。

按任务只填对应字段：检测 / 分割填 `boxes`，分类填 `probs` + `cls_id` + `conf`，
异常检测填 `score` + `label`（+ `heat_map`），OCR 追加 `text`。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Box:
    """一个检测框（像素坐标：左上 / 右下）。"""

    x1: float = 0.0
    y1: float = 0.0
    x2: float = 0.0
    y2: float = 0.0
    cls_id: int = 0
    conf: float = 0.0
    text: str = ""                      # OCR：框内文本（第 3 期）

    @property
    def xyxy(self) -> tuple[float, float, float, float]:
        return (self.x1, self.y1, self.x2, self.y2)

    @property
    def width(self) -> float:
        return abs(self.x2 - self.x1)

    @property
    def height(self) -> float:
        return abs(self.y2 - self.y1)

    def to_dict(self) -> dict:
        return {
            "x1": self.x1, "y1": self.y1, "x2": self.x2, "y2": self.y2,
            "cls_id": self.cls_id, "conf": self.conf, "text": self.text,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Box":
        return cls(
            x1=float(data.get("x1") or 0.0),
            y1=float(data.get("y1") or 0.0),
            x2=float(data.get("x2") or 0.0),
            y2=float(data.get("y2") or 0.0),
            cls_id=int(data.get("cls_id") or 0),
            conf=float(data.get("conf") or 0.0),
            text=str(data.get("text") or ""),
        )


@dataclass
class Prediction:
    """一张图的预测结果。"""

    image: str = ""
    task: str = ""
    backend: str = ""
    # 检测 / 分割 / 旋转框
    boxes: list[Box] = field(default_factory=list)
    # 分类
    probs: list[float] = field(default_factory=list)
    cls_id: int = -1
    conf: float = 0.0
    # 异常检测
    score: float = 0.0
    label: int = 0
    heat_map: str = ""
    # 语义分割：预测掩码（PNG 路径，0 = 背景）
    mask: str = ""
    # OCR（第 3 期）
    text: str = ""

    @property
    def positive(self) -> bool:
        """是否有正向输出：异常判定为异常，分类 / 检测有结果。"""
        if self.task == "anomaly":
            return int(self.label) == 1
        return bool(self.boxes) or int(self.cls_id) >= 0

    @property
    def confidence(self) -> float:
        """统一置信度：异常取分数，检测取最高框置信度。"""
        if self.task == "anomaly":
            return float(self.score)
        if self.boxes:
            return max(float(box.conf) for box in self.boxes)
        return float(self.conf)

    def to_dict(self) -> dict:
        return {
            "image": self.image, "task": self.task, "backend": self.backend,
            "boxes": [box.to_dict() for box in self.boxes],
            "probs": list(self.probs), "cls_id": self.cls_id, "conf": self.conf,
            "score": self.score, "label": self.label, "heat_map": self.heat_map,
            "mask": self.mask, "text": self.text,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Prediction":
        return cls(
            image=str(data.get("image") or ""),
            task=str(data.get("task") or ""),
            backend=str(data.get("backend") or ""),
            boxes=[Box.from_dict(item) for item in data.get("boxes") or []],
            probs=[float(value) for value in data.get("probs") or []],
            cls_id=int(data.get("cls_id") if data.get("cls_id") is not None else -1),
            conf=float(data.get("conf") or 0.0),
            score=float(data.get("score") or 0.0),
            label=int(data.get("label") or 0),
            heat_map=str(data.get("heat_map") or ""),
            mask=str(data.get("mask") or ""),
            text=str(data.get("text") or ""),
        )
