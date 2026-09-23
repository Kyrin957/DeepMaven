"""缺陷类别管理服务：增删改、排序、颜色与导入导出。

类别定义存放在 `Project.classes`（随 `.mprj` 持久化），
**列表顺序即标签 id**，必须保持 0..n-1 连续，否则 YOLO 标签文件中的
类别索引会与之失配。
"""

from __future__ import annotations

from pathlib import Path

from src.models.project_file import ClassDef
from src.utils.logger import get_logger

logger = get_logger("category")

# 默认类别配色（按 id 循环取用）
DEFAULT_PALETTE = [
    "#005FB8", "#0F7B0F", "#C42B1C", "#9D5D00", "#7A3E9D",
    "#00838F", "#B4009E", "#4D6A00", "#8A5A00", "#5C5C5C",
]


def _id_sort_key(value) -> tuple:
    """类别 id 排序键：数字优先，其余按字符串。"""
    try:
        return (0, int(value))
    except (TypeError, ValueError):
        return (1, str(value))


class CategoryService:
    """缺陷类别列表的操作（静态方法，不持有状态）。"""

    # -----------------------------------------------------------
    # 查询
    # -----------------------------------------------------------
    @staticmethod
    def default_color(index: int) -> str:
        """按序号返回默认颜色。"""
        return DEFAULT_PALETTE[index % len(DEFAULT_PALETTE)]

    # -----------------------------------------------------------
    # 增删改
    # -----------------------------------------------------------
    @staticmethod
    def add(classes: list[ClassDef], name: str, color: str = "") -> ClassDef:
        """新增类别，id 取当前最大值 +1。"""
        name = name.strip()
        if not name:
            raise ValueError("类别名不能为空")
        new_id = max((c.cls_id for c in classes), default=-1) + 1
        cls = ClassDef(
            cls_id=new_id,
            name=name,
            color=color or CategoryService.default_color(new_id),
        )
        classes.append(cls)
        return cls

    @staticmethod
    def rename(classes: list[ClassDef], cls_id: int, name: str) -> None:
        """重命名类别。"""
        name = name.strip()
        if not name:
            raise ValueError("类别名不能为空")
        for cls in classes:
            if cls.cls_id == cls_id:
                cls.name = name
                return
        raise KeyError(f"类别不存在: {cls_id}")

    @staticmethod
    def set_color(classes: list[ClassDef], cls_id: int, color: str) -> None:
        """设置类别颜色。"""
        for cls in classes:
            if cls.cls_id == cls_id:
                cls.color = color
                return
        raise KeyError(f"类别不存在: {cls_id}")

    @staticmethod
    def set_kind(classes: list[ClassDef], cls_id: int, kind: str) -> None:
        """设置类别类型（异常检测：normal=良好 / abnormal=异常，"" 为未指定）。"""
        for cls in classes:
            if cls.cls_id == cls_id:
                cls.kind = str(kind)
                return
        raise KeyError(f"类别不存在: {cls_id}")

    @staticmethod
    def remove(classes: list[ClassDef], cls_id: int) -> None:
        """删除类别并重排 id（保持 0..n-1 连续）。"""
        classes[:] = [c for c in classes if c.cls_id != cls_id]
        CategoryService.renumber(classes)

    @staticmethod
    def renumber(classes: list[ClassDef]) -> None:
        """按当前顺序把 cls_id 重排为 0..n-1。"""
        for index, cls in enumerate(classes):
            cls.cls_id = index

    @staticmethod
    def move(classes: list[ClassDef], cls_id: int, delta: int) -> int:
        """按 delta 上下移动类别，返回移动后的位置（越界则不动）。"""
        ids = [c.cls_id for c in classes]
        if cls_id not in ids:
            raise KeyError(f"类别不存在: {cls_id}")
        old = ids.index(cls_id)
        new = old + delta
        if new < 0 or new >= len(classes):
            return old
        classes.insert(new, classes.pop(old))
        CategoryService.renumber(classes)
        return new

    # -----------------------------------------------------------
    # 构建 / 导入 / 导出
    # -----------------------------------------------------------
    @staticmethod
    def from_names(names: list[str]) -> list[ClassDef]:
        """由名称列表构建类别定义（id = 顺序）。"""
        return [
            ClassDef(cls_id=index, name=str(name),
                     color=CategoryService.default_color(index))
            for index, name in enumerate(names)
        ]

    @staticmethod
    def sync_from_dataset(dataset) -> list[ClassDef]:
        """按数据集统计中的类别 id 生成类别定义（名称暂用 id 本身）。"""
        result: list[ClassDef] = []
        for index, raw in enumerate(sorted(dataset.class_names, key=_id_sort_key)):
            try:
                cls_id = int(raw)
            except (TypeError, ValueError):
                cls_id = index
            result.append(ClassDef(
                cls_id=cls_id,
                name=str(raw),
                color=CategoryService.default_color(cls_id),
            ))
        return result

    @staticmethod
    def import_classes_txt(path: str | Path) -> list[ClassDef]:
        """从 classes.txt 导入类别（每行一个名称，行号即 id）。"""
        path = Path(path)
        lines = path.read_text(encoding="utf-8").splitlines()
        names = [line.strip() for line in lines if line.strip()]
        logger.info("导入类别 %s 个：%s", len(names), path)
        return CategoryService.from_names(names)

    @staticmethod
    def import_data_yaml(path: str | Path) -> list[ClassDef]:
        """从 data.yaml / data.yml 的 names 字段导入类别。"""
        import yaml

        path = Path(path)
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        names = data.get("names")
        if isinstance(names, dict):
            try:
                keys = sorted(names, key=lambda k: int(k))
            except (TypeError, ValueError):
                keys = sorted(names, key=lambda k: str(k))
            values = [names[k] for k in keys]
        elif isinstance(names, list):
            values = list(names)
        else:
            values = []
        logger.info("导入类别 %s 个：%s", len(values), path)
        return CategoryService.from_names([str(v) for v in values])

    @staticmethod
    def export_classes_txt(path: str | Path, classes: list[ClassDef]) -> Path:
        """导出为 classes.txt（每行一个名称，行号即 id）。"""
        path = Path(path)
        ordered = sorted(classes, key=lambda c: c.cls_id)
        path.write_text("\n".join(c.name for c in ordered) + "\n", encoding="utf-8")
        logger.info("导出类别 %s 个：%s", len(ordered), path)
        return path
