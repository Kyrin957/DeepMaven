"""项目数据模型。

Project 为项目的完整数据容器，承载项目全部数据：
    元信息（名称、模型类型、描述、时间、状态）
    子模块配置（数据集、训练、评估、导出、预训练模型）
    文件索引（图像、标注、模型、运行产物等，见 ProjectFile）

持久化为单一 .mprj 文件（加密 manifest + ZIP 载荷）的职责由
services 层的 ProjectService / ProjectContainer 承担。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from src.models.dataset import Dataset
from src.models.project_file import ClassDef, ProjectFile
from src.models.split import DEFAULT_SPLIT_DIR, Split
from src.models.training import (
    EvaluationConfig,
    ExportConfig,
    TrainingConfig,
)
from src.utils.constants import APP_VERSION


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class Project:
    """一个 DeepMaven 项目（全部数据）。

    Attributes:
        name: 项目名称。
        model_type: 模型类型（detect / segment / classify）。
        description: 项目描述。
        created_at: 创建时间（ISO 字符串）。
        updated_at: 最近修改时间。
        status: 项目状态（draft / ready / training / finished）。
        app_version: 生成该项目的程序版本。
        dataset: 数据集配置与统计。
        training: 训练参数与实时状态。
        evaluation: 评估（推理）参数。
        export: 导出参数。
        model: 预训练模型信息。
        classes: 缺陷类别列表。
        params: 过程/扩展参数。
        files: 项目内文件索引。
    """

    name: str = "未命名项目"
    model_type: str = "detect"
    description: str = ""
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    status: str = "draft"
    app_version: str = APP_VERSION

    dataset: Dataset = field(default_factory=Dataset)
    training: TrainingConfig = field(default_factory=TrainingConfig)
    evaluation: EvaluationConfig = field(default_factory=EvaluationConfig)
    export: ExportConfig = field(default_factory=ExportConfig)
    model: dict = field(default_factory=dict)
    classes: list[ClassDef] = field(default_factory=list)
    # 一个项目可以有多套数据拆分（Halcon DLT 思路），训练时选用其中一套
    splits: list[Split] = field(default_factory=list)
    active_split_id: int = 0
    params: dict = field(default_factory=dict)
    files: list[ProjectFile] = field(default_factory=list)

    # 内存态：是否有未保存的变更（不参与序列化）
    _dirty: bool = field(default=False, compare=False, repr=False)

    # -----------------------------------------------------------
    # 便捷属性
    # -----------------------------------------------------------
    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def dirty(self) -> bool:
        """是否有未保存的变更。"""
        return self._dirty

    def find_file(self, virtual_path: str) -> ProjectFile | None:
        """按真实路径查找文件记录。"""
        for f in self.files:
            if f.virtual_path == virtual_path:
                return f
        return None

    def find_by_kind(self, kind: str) -> list[ProjectFile]:
        """按种类返回文件记录列表。"""
        return [f for f in self.files if f.kind == kind]

    # -----------------------------------------------------------
    # 缺陷类别操作
    # -----------------------------------------------------------
    @property
    def class_names(self) -> list[str]:
        """按类别 id 升序返回类别名。"""
        return [c.name for c in sorted(self.classes, key=lambda c: c.cls_id)]

    def next_class_id(self) -> int:
        """返回下一个可用的类别 id。"""
        return max((c.cls_id for c in self.classes), default=-1) + 1

    def find_class(self, cls_id: int) -> ClassDef | None:
        """按 id 查找类别定义。"""
        for c in self.classes:
            if c.cls_id == cls_id:
                return c
        return None

    # -----------------------------------------------------------
    # 数据拆分（一个项目可有多套）
    # -----------------------------------------------------------
    def split_by_id(self, split_id: int) -> Split | None:
        for split in self.splits:
            if split.split_id == int(split_id):
                return split
        return None

    def split_by_name(self, name: str) -> Split | None:
        for split in self.splits:
            if split.name == name:
                return split
        return None

    def next_split_id(self) -> int:
        return max((split.split_id for split in self.splits), default=-1) + 1

    def active_split(self) -> Split | None:
        """当前选中的拆分（不存在则回退到第一套）。"""
        return self.split_by_id(self.active_split_id) or (
            self.splits[0] if self.splits else None
        )

    def ensure_splits(self, layout: str = "") -> Split:
        """确保项目至少有一套拆分；旧项目按 Dataset 的划分字段迁移一套。"""
        if not self.splits:
            dataset = self.dataset
            name = str(self.params.get("split_name") or "").strip() or DEFAULT_SPLIT_DIR
            self.splits.append(Split(
                split_id=0,
                name=name,
                train=float(dataset.split_train or 0.7),
                val=float(dataset.split_val or 0.2),
                test=float(dataset.split_test or 0.1),
                seed=int(dataset.seed or 0),
                stratified=bool(dataset.stratified),
                layout=layout or ("classify" if self.model_type == "classify"
                                  else "detect"),
                output_dir=str(dataset.output_path or ""),
                data_yaml=str(dataset.data_yaml or ""),
                classes=list(dataset.class_names or []),
            ))
            self.active_split_id = self.splits[0].split_id
        if self.split_by_id(self.active_split_id) is None:
            self.active_split_id = self.splits[0].split_id
        return self.active_split()

    def touch(self) -> None:
        """标记项目已修改。"""
        self.updated_at = _now_iso()
        self._dirty = True

    def mark_saved(self) -> None:
        """标记已保存（清除未保存变更标记）。"""
        self._dirty = False

    # -----------------------------------------------------------
    # 序列化
    # -----------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "model_type": self.model_type,
            "description": self.description,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
            "app_version": self.app_version,
            "dataset": self.dataset.to_dict(),
            "training": self.training.to_dict(),
            "evaluation": self.evaluation.to_dict(),
            "export": self.export.to_dict(),
            "model": self.model,
            "classes": [c.to_dict() for c in self.classes],
            "splits": [split.to_dict() for split in self.splits],
            "active_split_id": self.active_split_id,
            "params": self.params,
            "files": [f.to_dict() for f in self.files],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        project = cls(
            name=data.get("name", "未命名项目"),
            model_type=data.get("model_type", "detect"),
            description=data.get("description", ""),
            created_at=data.get("created_at", _now_iso()),
            updated_at=data.get("updated_at", _now_iso()),
            status=data.get("status", "draft"),
            app_version=data.get("app_version", APP_VERSION),
            dataset=Dataset.from_dict(data.get("dataset", {})),
            training=TrainingConfig.from_dict(data.get("training", {})),
            evaluation=EvaluationConfig.from_dict(data.get("evaluation", {})),
            export=ExportConfig.from_dict(data.get("export", {})),
            model=data.get("model", {}),
            classes=[ClassDef.from_dict(c) for c in data.get("classes", [])],
            splits=[Split.from_dict(s) for s in data.get("splits", [])],
            active_split_id=data.get("active_split_id", 0),
            params=data.get("params", {}),
            files=[ProjectFile.from_dict(f) for f in data.get("files", [])],
        )
        # 旧项目（没有 splits 字段）按 Dataset 的划分字段迁移出一套拆分
        project.ensure_splits()
        return project

    # 兼容旧接口：保留 path 属性（指向 .mprj 文件）
    @property
    def path(self) -> Path:
        return Path(self.params.get("path", "")) if self.params.get("path") else Path()

    @property
    def is_valid(self) -> bool:
        return self.path.is_file()