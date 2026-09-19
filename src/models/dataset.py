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
    实际文件扫描、去重与划分由 services 层的 DatasetService 完成。
    """

    name: str = "未命名数据集"
    source_path: str = ""           # 原始图片/标签来源目录（首个来源，兼容旧项目）
    source_paths: list[str] = field(default_factory=list)  # 全部来源目录（可多次导入累加）
    output_path: str = ""           # 划分结果的落盘目录
    data_yaml: str = ""             # 生成的 data.yaml 路径
    image_count: int = 0
    label_count: int = 0
    duplicate_count: int = 0        # 内容重复（SHA256 相同）而被剔除的图片数
    class_names: list[str] = field(default_factory=list)  # 缺陷类别
    class_counts: dict[str, int] = field(default_factory=dict)  # 类别 -> 样本数

    # 划分比例（训练/验证/测试）
    split_train: float = 0.7
    split_val: float = 0.2
    split_test: float = 0.1
    stratified: bool = True         # 是否按类别分层抽样
    seed: int = 0                   # 随机种子（保证划分可复现）

    created_at: str = field(default_factory=_now_iso)

    @property
    def total(self) -> int:
        return self.image_count

    @property
    def class_count(self) -> int:
        return len(self.class_names)

    @property
    def sources(self) -> list[str]:
        """全部来源目录（旧项目只有单个 source_path 时自动兼容）。"""
        values = [str(p) for p in self.source_paths if str(p).strip()]
        if not values and self.source_path:
            values = [self.source_path]
        return values

    @property
    def source_label(self) -> str:
        """来源目录的展示文本（多个来源用「；」分隔）。"""
        return "；".join(self.sources)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "source_path": self.source_path,
            "source_paths": self.source_paths,
            "output_path": self.output_path,
            "data_yaml": self.data_yaml,
            "image_count": self.image_count,
            "label_count": self.label_count,
            "duplicate_count": self.duplicate_count,
            "class_names": self.class_names,
            "class_counts": self.class_counts,
            "split_train": self.split_train,
            "split_val": self.split_val,
            "split_test": self.split_test,
            "stratified": self.stratified,
            "seed": self.seed,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Dataset":
        return cls(
            name=data.get("name", "未命名数据集"),
            source_path=data.get("source_path", ""),
            source_paths=list(data.get("source_paths", []) or []),
            output_path=data.get("output_path", ""),
            data_yaml=data.get("data_yaml", ""),
            image_count=data.get("image_count", 0),
            label_count=data.get("label_count", 0),
            duplicate_count=data.get("duplicate_count", 0),
            class_names=data.get("class_names", []),
            class_counts=data.get("class_counts", {}),
            split_train=data.get("split_train", 0.7),
            split_val=data.get("split_val", 0.2),
            split_test=data.get("split_test", 0.1),
            stratified=data.get("stratified", True),
            seed=data.get("seed", 0),
            created_at=data.get("created_at", _now_iso()),
        )
