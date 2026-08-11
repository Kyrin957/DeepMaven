"""项目 ViewModel：管理当前项目状态与最近项目列表。"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from src.models.project import Project
from src.services.project_service import ProjectService
from src.utils.logger import get_logger

logger = get_logger("project_vm")


class ProjectViewModel(QObject):
    """项目管理页的业务逻辑。

    对外暴露信号：
        projectChanged:  当前项目发生变更（打开/新建/保存）。
        recentUpdated:   最近项目列表已更新。
        message:         供界面提示的消息（(level, text)）。
    """

    projectChanged = Signal(object)      # Project
    recentUpdated = Signal(list)         # list[dict]
    message = Signal(str, str)           # level, text

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._service = ProjectService()
        self._project: Project | None = None

    # -----------------------------------------------------------
    # 查询
    # -----------------------------------------------------------
    @property
    def project(self) -> Project | None:
        return self._project

    def has_project(self) -> bool:
        return self._project is not None

    def recent_projects(self) -> list[dict]:
        return self._service.list_all()

    # -----------------------------------------------------------
    # 命令（供 View 调用）
    # -----------------------------------------------------------
    def create_project(self, name: str, work_dir: str, model_type: str = "detect",
                       description: str = "") -> Project:
        """新建项目：落库并设为当前项目。"""
        project = Project(
            name=name or "未命名项目",
            work_dir=work_dir,
            model_type=model_type,
            description=description,
        )
        project_id = self._service.create(project)
        self._project = project
        logger.info("新建项目 #%s: %s (%s)", project_id, project.name, project.work_dir)
        self.projectChanged.emit(project)
        self.recentUpdated.emit(self.recent_projects())
        self.message.emit("success", f"项目已创建：{project.name}")
        return project

    def open_project(self, project_id: int) -> Project | None:
        """打开既有项目。"""
        project = self._service.get(project_id)
        if project is None:
            self.message.emit("error", "项目不存在或已被删除")
            return None
        self._project = project
        logger.info("打开项目 #%s", project_id)
        self.projectChanged.emit(project)
        self.message.emit("success", f"已打开项目：{project.name}")
        return project

    def open_by_dir(self, work_dir: str) -> Project | None:
        """按工作目录打开/创建项目。"""
        project = Project(name=work_dir.rsplit("/", 1)[-1], work_dir=work_dir)
        project_id = self._service.create(project)
        return self.open_project(project_id)

    def save_project(self) -> None:
        """保存当前项目变更。"""
        if self._project is None:
            self.message.emit("warning", "当前无项目可保存")
            return
        self._project.touch()
        self.message.emit("success", "项目已保存")

    def delete_project(self, project_id: int) -> None:
        """删除项目记录。"""
        self._service.delete(project_id)
        if self._project is not None and self._project.work_dir:
            pass
        self.recentUpdated.emit(self.recent_projects())
        self.message.emit("info", "项目记录已删除")