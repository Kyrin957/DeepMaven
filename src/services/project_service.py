"""项目服务：单一 .mprj 文件的创建、打开、保存与内部文件管理。

持有当前项目的内存态（ProjectContainer），负责：
    * create_project / open_project / save
    * 向归档添加/读取/移除文件（图像、标注、模型、运行产物等）
    * 将归档释放为目录（供 YOLO 训练等外部工具使用）
最近项目列表由 ConfigManager(QSettings) 按路径记录。
"""

from __future__ import annotations

from pathlib import Path

from src.models.project import Project
from src.utils.constants import COVER_ENTRY_NAME
from src.models.project_file import ENTRY_KIND, ProjectFile
from src.services.project_format import (
    ProjectContainer,
    ProjectFormatError,
    sha256_bytes,
    to_entry_id,
)
from src.utils.config import ConfigManager
from src.utils.logger import get_logger

logger = get_logger("project")


class ProjectService:
    """.mprj 项目文件的管理。"""

    def __init__(self, config: ConfigManager | None = None):
        self._config = config or ConfigManager()
        self._container: ProjectContainer | None = None

    # -----------------------------------------------------------
    # 当前项目状态
    # -----------------------------------------------------------
    @property
    def project(self) -> Project | None:
        return self._container.project if self._container else None

    @property
    def current_path(self) -> str:
        return self.project.params.get("path", "") if self.project else ""

    def has_project(self) -> bool:
        return self._container is not None

    # -----------------------------------------------------------
    # 创建 / 打开 / 保存
    # -----------------------------------------------------------
    def create_project(
        self,
        path: str | Path,
        name: str,
        model_type: str = "detect",
        description: str = "",
    ) -> Project:
        """新建项目并写入 .mprj 文件。"""
        path = self._ensure_mprj(path)
        project = Project(
            name=name or Path(path).stem,
            model_type=model_type,
            description=description,
        )
        project.params["path"] = str(path)
        self._container = ProjectContainer.create(project, {})
        self._write(path, self._container.data)
        self._config.add_recent_project(str(path), project.name)
        logger.info("新建项目: %s (%s)", project.name, path)
        return project

    def open_project(self, path: str | Path) -> Project:
        """打开 .mprj 项目并校验。"""
        path = Path(path)
        if not path.is_file():
            raise ProjectFormatError(f"项目文件不存在: {path}")
        container = ProjectContainer.open(path.read_bytes())
        container.project.params["path"] = str(path)
        self._container = container
        self._config.add_recent_project(str(path), container.project.name)
        logger.info("打开项目: %s", path)
        return container.project

    def save(self, project: Project | None = None) -> None:
        """将当前项目重新打包写回 .mprj。"""
        project = project or self.project
        if project is None or not project.params.get("path"):
            raise ValueError("当前无项目可保存")
        if self._container is None:
            self._container = ProjectContainer.create(project, {})
        project.touch()
        self._container = self._container.rebuild(project)
        self._write(project.params["path"], self._container.data)
        project.mark_saved()
        logger.info("已保存项目: %s", project.params["path"])

    def save_as(self, new_path: str | Path, project: Project | None = None) -> Path:
        """把当前项目另存为新的 .mprj，并把当前项目切换到新位置。

        Returns:
            实际写入的项目文件路径。
        """
        project = project or self.project
        if project is None:
            raise ValueError("当前无项目可另存")
        if self._container is None:
            self._container = ProjectContainer.create(project, {})

        target = self._ensure_mprj(new_path)
        previous = str(project.params.get("path", "") or "")
        project.params["path"] = str(target)
        project.touch()
        self._container = self._container.rebuild(project)
        try:
            self._write(target, self._container.data)
        except OSError:
            # 写入失败则回滚，避免当前项目指向一个不存在的文件
            project.params["path"] = previous
            raise
        project.mark_saved()
        self._config.add_recent_project(str(target), project.name)
        logger.info("项目另存为: %s -> %s", previous, target)
        return target

    def close(self) -> None:
        """关闭当前项目（只清空内存状态，不删除文件）。"""
        self._container = None
        logger.info("已关闭当前项目")

    @staticmethod
    def delete_project(path: str | Path) -> None:
        """删除 .mprj 项目文件。"""
        target = Path(path)
        if target.is_file():
            target.unlink()
            logger.info("已删除项目文件: %s", target)

    @staticmethod
    def peek_project(path: str | Path) -> tuple[Project | None, bytes]:
        """轻量读取项目清单与封面（跳过逐文件哈希校验），供最近项目预览。

        Returns:
            (Project 或 None, 封面 JPEG 字节)。
        """
        try:
            target = Path(path)
            if not target.is_file():
                return None, b""
            container = ProjectContainer.open(target.read_bytes(), verify=False)
            project = container.project
            project.params["path"] = str(target)
            cover = b""
            record = project.find_file(COVER_ENTRY_NAME)
            if record is not None:
                cover = container.get_file_bytes(record.entry_id)
            return project, cover
        except (ProjectFormatError, OSError, KeyError, ValueError, TypeError) as exc:
            logger.warning("读取项目信息失败 %s: %s", path, exc)
            return None, b""

    @classmethod
    def peek_cover(cls, path: str | Path) -> bytes:
        """只读取项目内的封面缩略图，失败返回空字节。"""
        _project, cover = cls.peek_project(path)
        return cover

    # -----------------------------------------------------------
    # 归档内部文件管理
    # -----------------------------------------------------------
    def add_file(
        self,
        project: Project | None,
        kind: str,
        virtual_path: str,
        data: bytes,
        save: bool = True,
    ) -> ProjectFile:
        """向项目添加一个文件并登记索引。

        Args:
            project: 目标项目（默认当前项目）。
            kind: 条目种类（见 project_file.ENTRY_KIND）。
            virtual_path: 项目内真实路径，如 "images/train/a.jpg"。
            data: 文件字节。
            save: 是否立即写回磁盘。
        """
        if kind not in ENTRY_KIND:
            raise ValueError(f"未知条目种类: {kind}")
        project = project or self.project
        if project is None:
            raise ValueError("当前无项目")

        entry_id = to_entry_id(data)
        record = ProjectFile(
            kind=kind,
            virtual_path=virtual_path,
            entry_id=entry_id,
            size=len(data),
            sha256=sha256_bytes(data),
        )
        project.files.append(record)
        project.touch()

        # 重打包：读取既有文件字节 + 新文件
        files = self._collect_file_bytes(project, extra={entry_id: data})
        self._container = ProjectContainer.create(project, files)
        if save:
            self._write(project.params["path"], self._container.data)
        return record

    def add_files(
        self,
        project: Project | None,
        items: list[tuple[str, str, bytes]],
        save: bool = True,
    ) -> list[ProjectFile]:
        """批量添加文件（单次重打包）。

        相比逐个调用 add_file，本方法把所有新文件一次性打包写回，
        避免每加一个文件就重打包一次（O(n²)）。

        Args:
            project: 目标项目（默认当前项目）。
            items: (kind, virtual_path, data) 序列。
            save: 是否立即写回磁盘。

        Returns:
            新增的文件记录列表。
        """
        project = project or self.project
        if project is None:
            raise ValueError("当前无项目")

        by_path = {f.virtual_path: f for f in project.files}
        new_bytes: dict[str, bytes] = {}
        records: list[ProjectFile] = []
        for kind, virtual_path, data in items:
            if kind not in ENTRY_KIND:
                raise ValueError(f"未知条目种类: {kind}")
            entry_id = to_entry_id(data)
            record = ProjectFile(
                kind=kind,
                virtual_path=virtual_path,
                entry_id=entry_id,
                size=len(data),
                sha256=sha256_bytes(data),
            )
            # 同一路径重复写入时替换旧记录（如重新划分数据集）
            old = by_path.get(virtual_path)
            if old is not None:
                project.files = [f for f in project.files if f is not old]
            project.files.append(record)
            by_path[virtual_path] = record
            new_bytes[entry_id] = data
            records.append(record)

        if not records:
            return records
        project.touch()
        files = self._collect_file_bytes(project, extra=new_bytes)
        self._container = ProjectContainer.create(project, files)
        if save and project.params.get("path"):
            self._write(project.params["path"], self._container.data)
        logger.info("批量写入项目文件 %s 个", len(records))
        return records

    def get_file_bytes(self, entry_id: str) -> bytes:
        if self._container is None:
            raise ValueError("当前无项目")
        return self._container.get_file_bytes(entry_id)

    def read_file(self, project: Project | None, virtual_path: str) -> bytes:
        """按真实路径读取文件内容。"""
        project = project or self.project
        record = project.find_file(virtual_path)
        if record is None:
            raise KeyError(f"项目内不存在: {virtual_path}")
        return self._container.get_file_bytes(record.entry_id)

    def read_files(
        self, project: Project | None, virtual_paths: list[str]
    ) -> dict[str, bytes]:
        """批量按真实路径读取内容（用于从归档恢复数据集）。"""
        project = project or self.project
        if project is None:
            raise ValueError("当前无项目")
        entry_of: dict[str, str] = {}
        for path in virtual_paths:
            record = project.find_file(path)
            if record is not None:
                entry_of[record.entry_id] = path
        raw = self._container.get_files_bytes(list(entry_of))
        return {virtual: raw[entry_id] for entry_id, virtual in entry_of.items()}

    def remove_file(self, project: Project | None, virtual_path: str) -> None:
        """移除一条文件记录并重打包。"""
        project = project or self.project
        target = project.find_file(virtual_path)
        if target is None:
            return
        project.files = [f for f in project.files if f is not target]
        project.touch()
        files = self._collect_file_bytes(project, extra={})
        self._container = ProjectContainer.create(project, files)
        self._write(project.params["path"], self._container.data)

    def extract_to_dir(self, project: Project | None, dest: str | Path) -> Path:
        """将归档内文件释放为目录（按虚拟路径还原）。"""
        project = project or self.project
        if project is None:
            raise ValueError("当前无项目")
        dest = Path(dest)
        for record in project.files:
            bytes_ = self._container.get_file_bytes(record.entry_id)
            out = dest / record.virtual_path
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(bytes_)
        logger.info("项目已释放到: %s", dest)
        return dest

    # -----------------------------------------------------------
    # 最近项目
    # -----------------------------------------------------------
    def recent_projects(self) -> list[dict]:
        """最近项目列表（按路径，来自 QSettings）。"""
        return self._config.recent_projects()

    # -----------------------------------------------------------
    # 内部
    # -----------------------------------------------------------
    def _collect_file_bytes(
        self, project: Project, extra: dict[str, bytes]
    ) -> dict[str, bytes]:
        """收集 project.files 引用的全部字节（extra 优先，用于注入新文件）。"""
        files: dict[str, bytes] = {}
        if self._container is not None:
            with self._container._open_zip(self._container._zip_bytes()) as zf:
                for entry_id in {f.entry_id for f in project.files}:
                    if entry_id in extra:
                        files[entry_id] = extra[entry_id]
                    else:
                        files[entry_id] = zf.read(entry_id)
        for entry_id, content in extra.items():
            files.setdefault(entry_id, content)
        return files

    @staticmethod
    def _write(path: str | Path, data: bytes) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    @staticmethod
    def _ensure_mprj(path: str | Path) -> Path:
        path = Path(path)
        if path.suffix.lower() != ".mprj":
            path = path.with_suffix(".mprj")
        return path