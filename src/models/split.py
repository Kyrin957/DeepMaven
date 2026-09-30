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

from src.utils.constants import SPLIT_SUBDIRS


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _plan_percents(*ratios: float) -> tuple[int, int, int]:
    """比例 → 计划百分比（四舍五入，差额补到最大的一项以保证合计 100）。"""
    values = [max(0, int(round(float(value) * 100))) for value in ratios]
    gap = 100 - sum(values)
    if gap:
        index = values.index(max(values))
        values[index] = max(0, values[index] + gap)
    return tuple(values)  # type: ignore[return-value]


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

    # 异常检测：按类别指定各子集的数量（其余任务为空，仍按 train/val/test 比例）
    # {"normal": {"train": 175, "val": 38, "test": 37},
    #  "abnormal": {"train": 0, "val": 100, "test": 40}}
    # 「训练集只放良好图」由算法保证（abnormal 的 train 恒为 0），异常图进验证 / 测试。
    anomaly_counts: dict = field(default_factory=dict)

    # -----------------------------------------------------------
    # 展示与换算
    # -----------------------------------------------------------
    @property
    def ready(self) -> bool:
        """是否已生成过划分产物（有 data.yaml 即视为已生成）。"""
        return bool(self.data_yaml)

    @property
    def ratio_text(self) -> str:
        """划分文本：**已生成给实际张数**，未生成给计划百分比。

        已生成（有产物）时给 ``189/52/31`` 这样的**实际张数**——与拆分概览的
        「图像」列逐个对上；未生成时给按比例四舍五入、合计 100 的百分比。

        历史缺陷：这里固定输出 ``int(比例 × 100)``（截断），于是「按张数 191/54/27
        换算出来的比例」（0.702 / 0.199 / 0.099）显示成 ``70/19/9``——合计只有
        98，且与拆分概览按分层抽样算出的 189/52/31 对不上，看着像「没更新」。
        """
        if self.ready and self.counts:
            return "/".join(
                str(int(self.counts.get(sub, 0) or 0)) for sub in SPLIT_SUBDIRS
            )
        return "/".join(
            str(value)
            for value in _plan_percents(self.train, self.val, self.test)
        )

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
            "anomaly_counts": self.anomaly_counts,
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
            anomaly_counts=dict(data.get("anomaly_counts", {}) or {}),
        )
