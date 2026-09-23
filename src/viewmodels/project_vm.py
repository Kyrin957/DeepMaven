"""项目 ViewModel：管理当前 .mprj 项目状态与最近项目列表。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

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
        recoverableChanged: 可恢复项目列表变化。
        message:         供界面提示的消息（(level, text)）。
    """

    projectChanged = Signal(object)      # Project
    recentUpdated = Signal(list)         # list[dict]
    recoverableChanged = Signal(list)    # list[dict]
    message = Signal(str, str)           # level, text

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._config = ConfigManager()
        self._service = ProjectService(self._config)
        # 自动保存：定时把项目写入 <项目>.mprj.bak（间隔取自偏好设置）
        self._autosave_timer = QTimer(self)
        self._autosave_timer.timeout.connect(self._on_autosave)
        self._apply_autosave_interval()

    # -----------------------------------------------------------
    # 查询
    # -----------------------------------------------------------
    @property
    def service(self) -> ProjectService:
        """底层项目服务（供其它 ViewModel 共享同一项目状态）。"""
        return self._service

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
        self._start_autosave()
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
        self._start_autosave()
        self.message.emit("success", f"已打开项目：{project.name}")
        return project

    def save_project(self, name: str = "", description: str | None = None) -> bool:
        """保存当前项目（可同时把名称 / 说明写回项目）。

        Args:
            name: 非空时更新项目名称。
            description: 非 None 时更新项目说明（空串表示清空）。

        Returns:
            是否保存成功。
        """
        project = self.project
        if project is None:
            self.message.emit("warning", "当前无项目可保存")
            return False
        if name.strip():
            project.name = name.strip()
        if description is not None:
            project.description = description
        try:
            self._service.save(project)
        except (OSError, ValueError, ProjectFormatError) as exc:
            self.message.emit("error", f"保存失败：{exc}")
            return False
        logger.info("保存项目: %s", self.current_path)
        # 名称可能变化，同步刷新最近项目与项目信息
        self.projectChanged.emit(project)
        self.recentUpdated.emit(self.recent_projects())
        self.message.emit("success", "项目已保存")
        return True

    # -----------------------------------------------------------
    # 自动保存与崩溃恢复
    # -----------------------------------------------------------
    def _apply_autosave_interval(self) -> None:
        """按偏好设置调整自动保存间隔（0 表示关闭）。"""
        seconds = self._config.autosave_seconds
        if seconds <= 0:
            self._autosave_timer.stop()
        else:
            self._autosave_timer.setInterval(seconds * 1000)

    def _start_autosave(self) -> None:
        self._apply_autosave_interval()
        if self._config.autosave_seconds > 0 and self.has_project():
            self._autosave_timer.start()

    def _on_autosave(self) -> None:
        """定时把当前项目写入备份文件（未修改则跳过）。"""
        project = self.project
        if project is None or not project.dirty:
            return
        self._service.autosave(project)
        self.recoverableChanged.emit(self.recoverable_projects())

    def load_preferences(self) -> None:
        """偏好设置变化后重新应用（自动保存间隔等）。"""
        self._start_autosave()

    def recoverable_projects(self) -> list[dict]:
        """最近项目里存在较新自动备份的项目（可恢复）。"""
        items: list[dict] = []
        for entry in self.recent_projects():
            path = str(entry.get("path") or "")
            info = ProjectService.recoverable(path) if path else None
            if info:
                info["name"] = str(entry.get("name") or Path(path).stem)
                items.append(info)
        return items

    def recover_project(self, backup: str) -> Project | None:
        """用自动备份恢复项目并打开它。"""
        try:
            target = ProjectService.recover(backup)
        except (OSError, ProjectFormatError) as exc:
            self.message.emit("error", f"恢复失败：{exc}")
            return None
        self.message.emit("success", "已从自动备份恢复")
        self.recoverableChanged.emit(self.recoverable_projects())
        return self.open_project(str(target))

    def discard_backup(self, backup: str) -> None:
        """忽略（删除）某个自动备份。"""
        ProjectService.discard_backup(backup)
        self.recoverableChanged.emit(self.recoverable_projects())

    def save_project_as(self, path: str) -> bool:
        """项目另存为，并把当前项目切换到新文件。"""
        if not self.has_project():
            self.message.emit("warning", "当前无项目可另存")
            return False
        try:
            target = self._service.save_as(path)
        except (OSError, ValueError, ProjectFormatError) as exc:
            self.message.emit("error", f"另存为失败：{exc}")
            return False
        logger.info("项目另存为: %s", target)
        self.projectChanged.emit(self.project)
        self.recentUpdated.emit(self.recent_projects())
        self.message.emit("success", f"项目已另存为：{target}")
        return True

    def close_project(self) -> None:
        """关闭当前项目（只清空内存状态，不删除文件）。"""
        if not self.has_project():
            self.message.emit("warning", "当前没有打开的项目")
            return
        self._service.close()
        self.projectChanged.emit(None)
        self.message.emit("info", "项目已关闭")

    def project_artifacts(self, path: str) -> dict:
        """删除前列出会一并清理的内容（供确认对话框）。

        未打开的项目会轻量读取清单，以便把旧版放在项目文件同级的
        拆分产物目录也列出来。
        """
        project = self._service.project if self.current_path == path else None
        if project is None:
            try:
                project, _cover = self._service.peek_project(path)
            except (ProjectFormatError, OSError, ValueError) as exc:
                logger.warning("读取项目清单失败（仅影响删除清单）：%s", exc)
                project = None
        return self._service.artifacts(path, project)

    def delete_project(self, path: str) -> None:
        """删除项目文件、项目文件夹（拆分 / 训练 / 导出产物）与备份。"""
        was_current = self.current_path == path
        project = self._service.project if was_current else None
        try:
            result = self._service.delete_project(path, project)
        except OSError as exc:
            self.message.emit("error", f"删除项目失败：{exc}")
            return

        remaining = [p for p in self.recent_projects() if p.get("path") != path]
        self._config.set("recent_projects", _json(remaining))
        self.recentUpdated.emit(self.recent_projects())

        failed = list(result.get("failed") or [])
        removed = list(result.get("removed") or [])
        if failed:
            self.message.emit("warning", f"部分内容未能删除：{failed[0][0]}")
        else:
            self.message.emit("success", f"项目已删除（{len(removed)} 项）")

        if was_current:
            self._service.close()
            self.projectChanged.emit(None)

    def update_meta(self, name: str = "", description: str = "") -> None:
        """更新项目名称 / 说明并立即保存。"""
        project = self.project
        if project is None:
            self.message.emit("warning", "请先创建或打开项目")
            return
        if name.strip():
            project.name = name.strip()
        project.description = description
        project.touch()
        try:
            self._service.save(project)
        except (OSError, ValueError, ProjectFormatError) as exc:
            self.message.emit("error", f"保存失败：{exc}")
            return
        self.projectChanged.emit(project)
        self.recentUpdated.emit(self.recent_projects())
        self.message.emit("success", "项目信息已更新")

    # -----------------------------------------------------------
    # 路径信息（供项目页展示）
    # -----------------------------------------------------------
    def project_dir(self) -> str:
        """当前项目所在目录。"""
        path = self.current_path
        return str(Path(path).parent) if path else ""

    def dataset_source(self) -> str:
        """当前项目的数据集来源目录。"""
        project = self.project
        if project is None:
            return ""
        return str(project.dataset.source_path or "")

    def notify_changed(self) -> None:
        """外部修改了当前项目（如数据集归档）后刷新界面。"""
        if self._service.project is not None:
            self.projectChanged.emit(self._service.project)

    def delete_recent(self, project_path: str) -> None:
        """从最近列表中移除一条记录。"""
        recent = [p for p in self.recent_projects() if p.get("path") != project_path]
        self._config.set("recent_projects", _json(recent))
        self.recentUpdated.emit(self.recent_projects())
        self.message.emit("info", "最近项目记录已移除")


def _json(value) -> str:
    import json
    return json.dumps(value, ensure_ascii=False)