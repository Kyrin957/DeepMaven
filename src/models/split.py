"""数据拆分数据模型。

参照 Halcon DLT 的思路：**一个项目可以创建多套数据拆分**。每套拆分享有
自己的划分比例、随机种子、分层方式与产物目录 / data.yaml，训练时选择用
哪一套，从而横向比较「不同拆分 + 多次训练」的成果。

`Split` 只是配置与统计的载体，实际文件扫描、去重与落盘由
`services/dataset_service.py` 完成。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 默认拆分目录名（未命名时的兜底）
DEFAULT_SPLIT_DIR = "dataset"


@dataclass
class Split:
    """一套数据拆分。"""

    split_id: int = 0
    name: str = DEFAULT_SPLIT_DIR        # 同时作为产物目录名
    created_at: str = field(default_factory=_now_iso)

    # 划分参数
    train: float = 0.7
    val: float = 0.2
    test: float = 0.1
    seed: int = 0
    stratified: bool = True
    layout: str = "detect"               # detect / classify

    # 产物与统计（执行拆分后写入）
    output_dir: str = ""
    data_yaml: str = ""
    counts: dict[str, int] = field(default_factory=dict)   # train / val / test
    classes: list[str] = field(default_factory=list)
    locked: bool = False                 # 已被训练使用（比例不再改动）

    # -----------------------------------------------------------
    # 展示与换算
    # -----------------------------------------------------------
    @property
    def ready(self) -> bool:
        """是否已生成过划分产物（有 data.yaml 即视为已生成）。"""
        return bool(self.data_yaml)

    @property
    def ratio_text(self) -> str:
        return f"{int(self.train * 100)}/{int(self.val * 100)}/{int(self.test * 100)}"

    def total(self) -> int:
        return sum(int(value) for value in self.counts.values())

    def mark_generated(
        self, output_dir: str, data_yaml: str, stats: dict, classes: list
    ) -> None:
        """记录一次划分产物与统计。"""
        self.output_dir = str(output_dir)
        self.data_yaml = str(data_yaml)
        self.counts = {
            key: int(stats.get(key, 0)) for key in ("train", "val", "test")
        }
        self.classes = [str(name) for name in (classes or [])]

    # -----------------------------------------------------------
    # 序列化
    # -----------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "split_id": self.split_id,
            "name": self.name,
            "created_at": self.created_at,
            "train": self.train,
            "val": self.val,
            "test": self.test,
            "seed": self.seed,
            "stratified": self.stratified,
            "layout": self.layout,
            "output_dir": self.output_dir,
            "data_yaml": self.data_yaml,
            "counts": self.counts,
            "classes": self.classes,
            "locked": self.locked,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Split":
        return cls(
            split_id=data.get("split_id", 0),
            name=data.get("name", DEFAULT_SPLIT_DIR),
            created_at=data.get("created_at", _now_iso()),
            train=data.get("train", 0.7),
            val=data.get("val", 0.2),
            test=data.get("test", 0.1),
            seed=data.get("seed", 0),
            stratified=data.get("stratified", True),
            layout=data.get("layout", "detect"),
            output_dir=data.get("output_dir", ""),
            data_yaml=data.get("data_yaml", ""),
            counts=dict(data.get("counts", {}) or {}),
            classes=list(data.get("classes", []) or []),
            locked=data.get("locked", False),
        )
