"""数据集数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class Dataset:
    """一个数据集的工作状态。

    记录数据集的来源、统计信息与划分比例，用于数据管理页展示。
    实际文件扫描与划分由 services 层的 DatasetService 完成。
    """

    name: str = "未命名数据集"
    source_path: str = ""           # 原始图片/标签来源目录
    image_count: int = 0
    label_count: int = 0
    class_names: list[str] = field(default_factory=list)  # 缺陷类别
    class_counts: dict[str, int] = field(default_factory=dict)  # 类别 -> 样本数

    # 划分比例（训练/验证/测试）
    split_train: float = 0.7
    split_val: float = 0.2
    split_test: float = 0.1

    created_at: str = field(default_factory=_now_iso)

    @property
    def total(self) -> int:
        return self.image_count

    @property
    def class_count(self) -> int:
        return len(self.class_names)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "source_path": self.source_path,
            "image_count": self.image_count,
            "label_count": self.label_count,
            "class_names": self.class_names,
            "class_counts": self.class_counts,
            "split_train": self.split_train,
            "split_val": self.split_val,
            "split_test": self.split_test,
            "created_at": self.created_at,
        }