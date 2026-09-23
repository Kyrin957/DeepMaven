"""标注数据模型。

坐标统一使用 **归一化** 值（0~1），与 YOLO 标注格式一致，
显示与格式转换时再按图片宽高换算为像素。

- `box`：`points` 为两个对角点 [(x1,y1), (x2,y2)]
- `polygon`：`points` 为多边形顶点序列
- `text`：文本框（OCR），`points` 同 `box`，`text` 存转写内容
"""

from __future__ import annotations

from dataclasses import dataclass, field

BOX = "box"
POLYGON = "polygon"
MASK = "mask"       # 掩码实例（点列为其外轮廓，与多边形同坐标口径）
TEXT = "text"       # OCR 文本框（坐标同 box，`text` 为转写内容）


@dataclass
class Annotation:
    """一个标注对象（矩形框或多边形）。"""

    cls_id: int = 0
    kind: str = BOX
    points: list[tuple[float, float]] = field(default_factory=list)
    text: str = ""                      # OCR 文本框的转写内容

    @property
    def is_box(self) -> bool:
        return self.kind == BOX

    @property
    def is_polygon(self) -> bool:
        return self.kind == POLYGON

    @property
    def is_mask(self) -> bool:
        return self.kind == MASK

    @property
    def is_text(self) -> bool:
        return self.kind == TEXT

    @property
    def is_rect(self) -> bool:
        """矩形口径（矩形框与文本框同坐标，落盘与评估都按框处理）。"""
        return self.kind in (BOX, TEXT)

    @property
    def center(self) -> tuple[float, float]:
        """归一化中心点：文本框旁路存储的锚点。"""
        x1, y1, x2, y2 = self.bounds()
        return ((x1 + x2) / 2, (y1 + y2) / 2)

    def set_center(self, center: tuple[float, float]) -> None:
        """按中心点平移（保持宽高）：文本框锚点回填时使用。"""
        cx, cy = float(center[0]), float(center[1])
        x1, y1, x2, y2 = self.bounds()
        half_w = abs(x2 - x1) / 2
        half_h = abs(y2 - y1) / 2
        self.points = [(cx - half_w, cy - half_h), (cx + half_w, cy + half_h)]

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
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Annotation":
        return cls(
            cls_id=int(data.get("cls_id", 0)),
            kind=data.get("kind", BOX),
            points=[(float(p[0]), float(p[1])) for p in data.get("points", [])],
            text=str(data.get("text") or ""),
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
