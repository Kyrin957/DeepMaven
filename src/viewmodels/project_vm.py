"""项目 ViewModel：管理当前 .mprj 项目状态与最近项目列表。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from src.models.project import Project
from src.services.project_format import ProjectFormatError
from src.services.project_service import ProjectService
from src.utils.config import ConfigManager
from src.utils.logger import get_logger

logger = get_logger("project_vm")


class ProjectViewModel(QObject):
    """项目管理页的业务逻辑。

    对外暴露信号：
        projectChanged:  当前项目发生变更（打开/新建）。
        recentUpdated:   最近项目列表已更新。
        message:         供界面提示的消息（(level, text)）。
    """

    projectChanged = Signal(object)      # Project
    recentUpdated = Signal(list)         # list[dict]
    message = Signal(str, str)           # level, text

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._config = ConfigManager()
        self._service = ProjectService(self._config)

    # -----------------------------------------------------------
    # 查询
    # -----------------------------------------------------------
    @property
    def project(self) -> Project | None:
        return self._service.project

    @property
    def current_path(self) -> str:
        return self._service.current_path

    def has_project(self) -> bool:
        return self._service.has_project()

    def recent_projects(self) -> list[dict]:
        return self._service.recent_projects()

    # -----------------------------------------------------------
    # 命令（供 View 调用）
    # -----------------------------------------------------------
    def create_project(self, path: str, name: str, model_type: str = "detect",
                       description: str = "") -> Project:
        """新建项目：写入 .mprj 并设为当前项目。"""
        try:
            project = self._service.create_project(
                path=path, name=name, model_type=model_type, description=description
            )
        except OSError as exc:
            self.message.emit("error", f"创建项目失败：{exc}")
            return None
        logger.info("新建项目: %s (%s)", project.name, project.params.get("path"))
        self.projectChanged.emit(project)
        self.recentUpdated.emit(self.recent_projects())
        self.message.emit("success", f"项目已创建：{project.name}")
        return project

    def open_project(self, path: str) -> Project | None:
        """打开既有 .mprj 项目。"""
        try:
            project = self._service.open_project(path)
        except (ProjectFormatError, OSError) as exc:
            self.message.emit("error", f"打开项目失败：{exc}")
            return None
        logger.info("打开项目: %s", path)
        self.projectChanged.emit(project)
        self.recentUpdated.emit(self.recent_projects())
        self.message.emit("success", f"已打开项目：{project.name}")
        return project

    def save_project(self) -> None:
        """保存当前项目变更。"""
        if not self.has_project():
            self.message.emit("warning", "当前无项目可保存")
            return
        try:
            self._service.save()
        except (OSError, ValueError, ProjectFormatError) as exc:
            self.message.emit("error", f"保存失败：{exc}")
            return
        self.message.emit("success", "项目已保存")

    def delete_recent(self, project_path: str) -> None:
        """从最近列表中移除一条记录。"""
        recent = [p for p in self.recent_projects() if p.get("path") != project_path]
        self._config.set("recent_projects", _json(recent))
        self.recentUpdated.emit(self.recent_projects())
        self.message.emit("info", "最近项目记录已移除")


def _json(value) -> str:
    import json
    return json.dumps(value, ensure_ascii=False)