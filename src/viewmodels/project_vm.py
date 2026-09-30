"""项目 ViewModel：管理当前 .mprj 项目状态与最近项目列表。"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from src.models.project import Project
from src.services.project_format import ProjectFormatError
from src.services.project_service import ProjectService
from src.utils.config import ConfigManager
from src.utils.logger import get_logger
from src.utils.workers import FunctionWorker

logger = get_logger("project_vm")

# 自动备份心跳（毫秒）：只做 O(1) 代际比较；真正的打包 / 写盘在后台线程
_BACKUP_TICK_MS = 1000


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
        # 「自动备份版本选择」选择器（由界面层注入，见 set_backup_prompt）
        self._backup_prompt = None
        # 自动备份：1s 心跳 + 「改动停手 N 秒后才写」防抖（N 取自偏好设置）。
        # 打包 / 写盘在后台线程，大项目也不会卡界面（见 §4.52）。
        self._backup_timer = QTimer(self)
        self._backup_timer.setInterval(_BACKUP_TICK_MS)
        self._backup_timer.timeout.connect(self._on_backup_tick)
        self._debounce_seconds = self._config.autosave_seconds
        self._seen_revision = 0          # 最近观察到的改动代际
        self._written_revision = -1      # 已写入备份的代际
        self._changed_at = 0.0           # 最近一次改动的时刻（monotonic）
        self._backup_worker = None       # 后台写盘线程（保留引用防回收）
        self._backup_generation = 0      # 保存 / 关闭等人工决策后 +1，作废在途写入
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
        self._clear_previous_backup()     # 人工已完成旧项目的保存 / 丢弃决策
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
        """打开既有 .mprj 项目。

        若检测到异常退出残留的自动备份（`<项目>.mprj.bak`），先由人工
        对比两个文件的修改时间、选择保留哪个版本（界面层注入选择器；
        未注入时不动备份、直接打开项目文件版本）。
        """
        pending = ProjectService.pending_backup(path)
        if pending and self._backup_prompt is not None:
            choice = str(self._backup_prompt(pending) or "")
            if not choice:
                return None
            if choice == "backup":
                try:
                    path = str(ProjectService.recover(pending["backup"]))
                except (OSError, ProjectFormatError) as exc:
                    self.message.emit("error", f"恢复自动备份失败：{exc}")
                    return None
                self.message.emit("success", "已用自动备份版本")
            else:
                ProjectService.discard_backup(pending["backup"])
        # 切换项目：旧项目的自动备份随人工决策一并清理（目标自身除外）
        self._clear_previous_backup(keep=path)
        try:
            project = self._service.open_project(path)
        except (ProjectFormatError, OSError) as exc:
            self.message.emit("error", f"打开项目失败：{exc}")
            return None
        # 旧目录布局（产物散在项目文件夹根下）迁移到 dataset/ 与 model/
        moved = self._service.migrate_layout(project)
        if moved:
            project.touch()          # 引用改写随下次保存落盘
        logger.info("打开项目: %s", path)
        self.projectChanged.emit(project)
        self.recentUpdated.emit(self.recent_projects())
        self._start_autosave()
        self.message.emit("success", f"已打开项目：{project.name}")
        if moved:
            self.message.emit(
                "info", "项目目录已整理为 dataset/ 与 model/（保存项目以写入改动）"
            )
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
        self._backup_generation += 1     # 人工已保存：作废在途备份写入
        # 名称可能变化，同步刷新最近项目与项目信息
        self.projectChanged.emit(project)
        self.recentUpdated.emit(self.recent_projects())
        self.message.emit("success", "项目已保存")
        return True

    # -----------------------------------------------------------
    # 自动备份（.bak）：改动停手后延迟写、后台线程打包
    # -----------------------------------------------------------
    def _apply_autosave_interval(self) -> None:
        """按偏好设置启停备份心跳（0 = 关闭；其余为停手后的等待秒数）。"""
        self._debounce_seconds = self._config.autosave_seconds
        if self._debounce_seconds <= 0:
            self._backup_timer.stop()
        elif self.has_project():
            self._backup_timer.start()

    def _start_autosave(self) -> None:
        """项目打开 / 新建后重置备份跟踪并启动心跳。"""
        project = self.project
        self._seen_revision = project.revision if project is not None else 0
        self._changed_at = 0.0
        self._written_revision = -1
        self._backup_generation += 1     # 作废旧项目的在途写入
        self._apply_autosave_interval()

    def _on_backup_tick(self) -> None:
        """心跳（1s）：改动停手 `_debounce_seconds` 秒后写入备份。

        只做代际与时间比较（O(1)）；同一代改动至多写一次，之后的空转
        心跳直接返回（历史实现每 60 秒无条件全量重压缩一次，卡且费 IO）。
        """
        project = self.project
        if project is None or not project.dirty:
            return
        revision = int(project.revision)
        now = time.monotonic()
        if revision != self._seen_revision:
            # 有新改动：重置防抖窗口（等操作停手后再写）
            self._seen_revision = revision
            self._changed_at = now
            return
        worker = self._backup_worker
        if worker is not None and worker.isRunning():
            return                        # 上一轮写盘还在进行
        if revision == self._written_revision:
            return                        # 这一代已写过
        if now - self._changed_at < self._debounce_seconds:
            return                        # 停手时间不足
        self._start_backup_write(project, revision)

    def _start_backup_write(self, project: Project, revision: int) -> None:
        """后台写备份：快照在主线程备好，线程只做清单重打包 + 写盘。"""
        payload = self._service.autosave_payload(project)
        if payload is None:
            return
        generation = self._backup_generation
        worker = FunctionWorker(
            lambda progress, cancelled: ProjectService.write_backup_payload(
                payload
            ),
            self,
        )
        worker.finishedOk.connect(
            lambda path: self._on_backup_written(path, revision, generation)
        )
        worker.failed.connect(
            lambda error: self._on_backup_written(
                None, revision, generation, str(error)
            )
        )
        self._backup_worker = worker
        worker.start()

    def _on_backup_written(
        self, path, revision: int, generation: int, error: str = ""
    ) -> None:
        """写盘结束（主线程回调）：失败仅记日志；期间人工已保存 / 关闭则作废。"""
        if error:
            logger.warning("自动保存失败: %s", error)
            return
        if generation != self._backup_generation:
            # 写入期间用户已保存 / 关闭（人工已决策）：把刚落盘的备份删掉
            if path is not None:
                ProjectService.discard_backup(path)
            return
        self._written_revision = revision

    def shutdown_backup(self, timeout_ms: int = 3000) -> None:
        """退出 / 关闭项目前等待在途备份收尾（先停心跳，再等线程结束）。"""
        self._backup_timer.stop()
        worker = self._backup_worker
        if worker is not None and worker.isRunning():
            worker.wait(timeout_ms)

    def load_preferences(self) -> None:
        """偏好设置变化后重新应用（自动备份延迟等）。"""
        self._apply_autosave_interval()

    def set_backup_prompt(self, prompt) -> None:
        """注入「自动备份版本选择」选择器（由界面层提供）。

        Args:
            prompt: ``(info: dict) -> str``，返回 "backup" / "project" /
                ""（取消打开）；未注入时打开项目不询问、保留备份不动。
        """
        self._backup_prompt = prompt

    def discard_current_backup(self) -> None:
        """清理当前项目的自动备份（正常保存 / 退出人工已确认后调用）。"""
        self._backup_generation += 1     # 作废在途的后台写入
        current = self.current_path
        if current:
            ProjectService.clear_backup(current)

    def _clear_previous_backup(self, keep: str = "") -> None:
        """替换项目前清理旧项目的自动备份（人工已完成保存 / 丢弃决策）。"""
        current = self.current_path
        if not current:
            return
        if keep and Path(current).resolve() == Path(keep).resolve():
            return
        ProjectService.clear_backup(current)

    def save_project_as(self, path: str, progress=None) -> bool:
        """项目另存为（连同项目文件夹一并复制），并把当前项目切换到新文件。

        Args:
            progress: 可选回调 ``(done, total)``，复制项目文件夹期间上报。
        """
        if not self.has_project():
            self.message.emit("warning", "当前无项目可另存")
            return False
        had_folder = bool(self.current_path) and Path(
            ProjectService.project_dir(self.current_path)
        ).is_dir()
        try:
            target = self._service.save_as(path, progress=progress)
        except (OSError, ValueError, ProjectFormatError) as exc:
            self.message.emit("error", f"另存为失败：{exc}")
            return False
        logger.info("项目另存为: %s", target)
        self._backup_generation += 1     # 人工已保存：作废在途备份写入
        self.projectChanged.emit(self.project)
        self.recentUpdated.emit(self.recent_projects())
        detail = "（连同项目文件夹已复制）" if had_folder else ""
        self.message.emit("success", f"项目已另存为：{target}{detail}")
        return True

    def close_project(self) -> None:
        """关闭当前项目（人工已确认保存 / 丢弃：备份一并清理，文件保留）。"""
        if not self.has_project():
            self.message.emit("warning", "当前没有打开的项目")
            return
        self.shutdown_backup()           # 等在途写盘收尾，再清理（避免又写出）
        self.discard_current_backup()
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