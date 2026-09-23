"""项目内文件索引数据模型。

ProjectFile 描述 .mprj 归档内的一条文件记录：真实虚拟路径、归档内的混淆条目名、
大小与校验值。真实路径只存在于（加密的）manifest 中，归档条目名为哈希 ID，
从而剥离表头也无法直接还原目录结构。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# 支持的条目种类
ENTRY_KIND = {"image", "label", "model", "run", "config", "thumbnail", "other"}


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class ProjectFile:
    """一条归档文件记录。

    Attributes:
        kind: 条目种类（见 ENTRY_KIND）。
        virtual_path: 项目内真实路径，如 "images/train/a.jpg"。
        entry_id: 归档内混淆条目名，如 "f_<sha256[:16]>.bin"。
        size: 文件字节数。
        sha256: 文件内容 SHA256 校验。
        created_at: 导入时间（ISO 字符串）。
    """

    kind: str = "other"
    virtual_path: str = ""
    entry_id: str = ""
    size: int = 0
    sha256: str = ""
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "virtual_path": self.virtual_path,
            "entry_id": self.entry_id,
            "size": self.size,
            "sha256": self.sha256,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectFile":
        return cls(
            kind=data.get("kind", "other"),
            virtual_path=data.get("virtual_path", ""),
            entry_id=data.get("entry_id", ""),
            size=data.get("size", 0),
            sha256=data.get("sha256", ""),
            created_at=data.get("created_at", _now_iso()),
        )


@dataclass
class ClassDef:
    """一个缺陷类别定义。

    Attributes:
        kind: 类别类型（异常检测专用）：""（未指定）/ "normal"（良好）/
            "abnormal"（异常）；良好与异常下都可以有多个类别。
    """

    cls_id: int = 0
    name: str = ""
    color: str = "#66CCFF"
    kind: str = ""

    def to_dict(self) -> dict:
        return {
            "cls_id": self.cls_id,
            "name": self.name,
            "color": self.color,
            "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ClassDef":
        return cls(
            cls_id=data.get("cls_id", 0),
            name=data.get("name", ""),
            color=data.get("color", "#66CCFF"),
            kind=data.get("kind", ""),
        )