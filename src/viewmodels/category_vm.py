"""类别管理 ViewModel：缺陷类别的增删改、排序与导入导出。

类别定义存储在 `Project.classes` 中，随 `.mprj` 持久化；
每次变更即时保存，避免用户忘记保存导致类别丢失。
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from src.services.category_service import CategoryService
from src.utils.logger import get_logger
from src.viewmodels.project_vm import ProjectViewModel

logger = get_logger("category_vm")


class CategoryViewModel(QObject):
    """缺陷类别管理业务逻辑。"""

    classesChanged = Signal(list)        # list[ClassDef]
    message = Signal(str, str)           # level, text

    def __init__(
        self,
        project_vm: ProjectViewModel | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._project_vm = project_vm
        # 新建 / 打开项目后刷新类别表
        if project_vm is not None:
            project_vm.projectChanged.connect(self._on_project_changed)

    def _on_project_changed(self, _project) -> None:
        self.classesChanged.emit(list(self.classes))

    # -----------------------------------------------------------
    # 查询
    # -----------------------------------------------------------
    @property
    def classes(self) -> list:
        project = self._project()
        return project.classes if project is not None else []

    # -----------------------------------------------------------
    # 命令
    # -----------------------------------------------------------
    def add_class(self, name: str, color: str = "") -> None:
        """新增类别。"""
        project = self._require_project()
        if project is None:
            return
        try:
            cls = CategoryService.add(project.classes, name, color)
        except ValueError as exc:
            self.message.emit("warning", str(exc))
            return
        self._commit(f"已新增类别：{cls.name}")

    def rename_class(self, cls_id: int, name: str) -> None:
        """重命名类别。"""
        project = self._require_project()
        if project is None:
            return
        try:
            CategoryService.rename(project.classes, cls_id, name)
        except (ValueError, KeyError) as exc:
            self.message.emit("warning", str(exc))
            return
        self._commit(f"类别已重命名为：{name.strip()}")

    def set_color(self, cls_id: int, color: str) -> None:
        """设置类别颜色。"""
        project = self._require_project()
        if project is None:
            return
        try:
            CategoryService.set_color(project.classes, cls_id, color)
        except KeyError as exc:
            self.message.emit("warning", str(exc))
            return
        self._commit("类别颜色已更新")

    def remove_class(self, cls_id: int) -> None:
        """删除类别（id 会重排）。"""
        project = self._require_project()
        if project is None:
            return
        CategoryService.remove(project.classes, cls_id)
        self._commit("类别已删除，id 已重排")

    def move_class(self, cls_id: int, delta: int) -> None:
        """上移 / 下移类别。"""
        project = self._require_project()
        if project is None:
            return
        try:
            CategoryService.move(project.classes, cls_id, delta)
        except KeyError as exc:
            self.message.emit("warning", str(exc))
            return
        self._commit("类别顺序已调整")

    def import_from_file(self, path: str) -> None:
        """从 classes.txt / data.yaml 导入类别（覆盖现有类别）。"""
        project = self._require_project()
        if project is None:
            return
        try:
            if path.lower().endswith((".yaml", ".yml")):
                classes = CategoryService.import_data_yaml(path)
            else:
                classes = CategoryService.import_classes_txt(path)
        except Exception as exc:  # noqa: BLE001 - 读取/解析异常类型较多
            logger.warning("导入类别失败: %s", exc)
            self.message.emit("error", f"导入类别失败：{exc}")
            return
        if not classes:
            self.message.emit("warning", "文件中未找到类别")
            return
        project.classes[:] = classes
        self._commit(f"已导入 {len(classes)} 个类别")

    def export_to_file(self, path: str) -> None:
        """导出为 classes.txt。"""
        project = self._require_project()
        if project is None:
            return
        try:
            CategoryService.export_classes_txt(path, project.classes)
        except OSError as exc:
            self.message.emit("error", f"导出类别失败：{exc}")
            return
        self.message.emit("success", f"类别已导出：{path}")

    def sync_from_dataset(self, dataset) -> None:
        """按数据集中出现的类别 id 生成类别定义（覆盖现有类别）。"""
        project = self._require_project()
        if project is None:
            return
        if dataset is None or not dataset.class_names:
            self.message.emit("warning", "当前数据集没有可用的类别信息")
            return
        project.classes[:] = CategoryService.sync_from_dataset(dataset)
        self._commit(f"已从数据集同步 {len(project.classes)} 个类别")

    # -----------------------------------------------------------
    # 内部
    # -----------------------------------------------------------
    def _project(self):
        return self._project_vm.project if self._project_vm else None

    def _require_project(self):
        project = self._project()
        if project is None:
            self.message.emit("warning", "请先创建或打开项目")
        return project

    def _commit(self, text: str) -> None:
        """标记改动并即时保存，然后广播变更。"""
        project = self._project()
        project.touch()
        try:
            self._project_vm.service.save(project)
        except (OSError, ValueError) as exc:
            logger.warning("保存类别失败: %s", exc)
            self.message.emit("error", f"保存类别失败：{exc}")
            return
        self.classesChanged.emit(list(project.classes))
        self._project_vm.notify_changed()
        self.message.emit("success", text)
