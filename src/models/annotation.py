"""标注数据模型。

坐标统一使用 **归一化** 值（0~1），与 YOLO 标注格式一致，
显示与格式转换时再按图片宽高换算为像素。

- `box`：`points` 为两个对角点 [(x1,y1), (x2,y2)]
- `polygon`：`points` 为多边形顶点序列
"""

from __future__ import annotations

from dataclasses import dataclass, field

BOX = "box"
POLYGON = "polygon"


@dataclass
class Annotation:
    """一个标注对象（矩形框或多边形）。"""

    cls_id: int = 0
    kind: str = BOX
    points: list[tuple[float, float]] = field(default_factory=list)

    @property
    def is_box(self) -> bool:
        return self.kind == BOX

    @property
    def is_polygon(self) -> bool:
        return self.kind == POLYGON

    def bounds(self) -> tuple[float, float, float, float]:
        """返回归一化包围盒 (x1, y1, x2, y2)。"""
        if not self.points:
            return 0.0, 0.0, 0.0, 0.0
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return min(xs), min(ys), max(xs), max(ys)

    def to_dict(self) -> dict:
        return {
            "cls_id": self.cls_id,
            "kind": self.kind,
            "points": [[float(x), float(y)] for x, y in self.points],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Annotation":
        return cls(
            cls_id=int(data.get("cls_id", 0)),
            kind=data.get("kind", BOX),
            points=[(float(p[0]), float(p[1])) for p in data.get("points", [])],
        )


@dataclass
class ImageAnnotation:
    """一张图片的全部标注。"""

    name: str = ""                  # 图片文件名（项目内标识）
    width: int = 0
    height: int = 0
    items: list[Annotation] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.items)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "items": [item.to_dict() for item in self.items],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ImageAnnotation":
        return cls(
            name=data.get("name", ""),
            width=int(data.get("width", 0)),
            height=int(data.get("height", 0)),
            items=[Annotation.from_dict(item) for item in data.get("items", [])],
        )
