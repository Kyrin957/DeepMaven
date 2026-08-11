"""项目数据模型。

Project 为纯数据模型，定义项目在磁盘上所需的最小元数据；
持久化到 SQLite 的职责由 services 层的 ProjectService 承担。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class Project:
    """一个 DeepMaven 项目。

    Attributes:
        name: 项目名称。
        work_dir: 项目工作目录（保存图片、标注、模型等）。
        model_type: 模型类型（如 detect / segment / classify）。
        description: 项目描述。
        created_at: 创建时间（ISO 字符串）。
        updated_at: 最近修改时间。
        status: 项目状态（draft / ready / training / finished）。
    """

    name: str = "未命名项目"
    work_dir: str = ""
    model_type: str = "detect"
    description: str = ""
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    status: str = "draft"

    # -----------------------------------------------------------
    # 便捷属性
    # -----------------------------------------------------------
    @property
    def path(self) -> Path:
        return Path(self.work_dir)

    @property
    def is_valid(self) -> bool:
        """项目是否具备有效的工作目录。"""
        return bool(self.work_dir) and Path(self.work_dir).is_dir()

    # -----------------------------------------------------------
    # 序列化
    # -----------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "work_dir": self.work_dir,
            "model_type": self.model_type,
            "description": self.description,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        return cls(
            name=data.get("name", "未命名项目"),
            work_dir=data.get("work_dir", ""),
            model_type=data.get("model_type", "detect"),
            description=data.get("description", ""),
            created_at=data.get("created_at", _now_iso()),
            updated_at=data.get("updated_at", _now_iso()),
            status=data.get("status", "draft"),
        )

    def touch(self) -> None:
        """标记项目已修改。"""
        self.updated_at = _now_iso()