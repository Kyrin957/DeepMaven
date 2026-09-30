"""项目服务：单一 .mprj 文件的创建、打开、保存与内部文件管理。

持有当前项目的内存态（ProjectContainer），负责：
    * create_project / open_project / save
    * 向归档添加/读取/移除文件（图像、标注、模型、运行产物等）
    * 将归档释放为目录（供 YOLO 训练等外部工具使用）
最近项目列表由 ConfigManager(QSettings) 按路径记录。
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime
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


def _force_remove(func, path, _exc_info) -> None:
    """rmtree 回调：只读文件先去掉只读属性再删（Windows 常见）。"""
    import os
    import stat as stat_module

    try:
        os.chmod(path, stat_module.S_IWRITE)
        func(path)
    except OSError as exc:  # noqa: BLE001 - 单条失败继续删其余
        logger.warning("删除 %s 失败：%s", path, exc)


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
        # 与 Halcon DLT 一致：项目文件旁边建同名目录存放拆分 / 训练 / 导出产物
        self.project_dir(path).mkdir(parents=True, exist_ok=True)
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
        # 人工保存 → 自动备份已完成使命：正常流程不留备份文件
        # （只有死机 / 强制结束等异常退出会残留，见 pending_backup）
        self.clear_backup(project.params["path"])
        logger.info("已保存项目: %s", project.params["path"])

    # -----------------------------------------------------------
    # 项目文件夹（参照 Halcon DLT：与项目文件同级的同名目录）
    # -----------------------------------------------------------
    @staticmethod
    def project_dir(path: str | Path) -> Path:
        """项目文件夹：`<项目文件所在目录>/<项目文件名（不含扩展名）>/`。

        其下分 `dataset/`（数据集拆分产物）与 `model/`（训练与导出产物）
        两个子目录；删除项目时一并清理（见 `artifacts()` / `delete_project()`）。
        """
        target = Path(path)
        return target.parent / target.stem

    @staticmethod
    def dataset_dir(path: str | Path) -> Path:
        """数据集拆分母目录：`<项目文件夹>/dataset/`（每套拆分一个子目录）。"""
        return ProjectService.project_dir(path) / "dataset"

    @staticmethod
    def model_dir(path: str | Path) -> Path:
        """模型产物母目录：`<项目文件夹>/model/`（训练产物与导出件）。"""
        return ProjectService.project_dir(path) / "model"

    # -----------------------------------------------------------
    # 路径引用重写 / 目录布局迁移
    # -----------------------------------------------------------
    @classmethod
    def _replace_prefix(cls, project: Project, old: Path, new: Path) -> int:
        """把项目里指向 old 目录内的路径引用改写为 new 前缀（返回改写条数）。

        覆盖：拆分产物（output_dir / data_yaml）、数据集镜像、训练配置
        （data_yaml / anomaly_root / project_dir / save_dir / 历史记录）、
        导出配置（output_dir）。仅做前缀匹配（带目录分隔符边界检查），
        不触碰来源图片目录等外部路径。
        """
        old_text = str(old)
        new_text = str(new)

        def rewrite(value):
            if not isinstance(value, str) or not value:
                return value, False
            if value == old_text:
                return new_text, True
            if value.startswith(old_text + os.sep) or value.startswith(
                old_text + "/"
            ):
                return new_text + value[len(old_text):], True
            return value, False

        changed = 0

        def apply(target, attr: str) -> None:
            nonlocal changed
            value, hit = rewrite(getattr(target, attr, ""))
            if hit:
                setattr(target, attr, value)
                changed += 1

        for split in getattr(project, "splits", []) or []:
            apply(split, "output_dir")
            apply(split, "data_yaml")
        dataset = getattr(project, "dataset", None)
        if dataset is not None:
            apply(dataset, "output_path")
            apply(dataset, "data_yaml")
        training = getattr(project, "training", None)
        if training is not None:
            apply(training, "data_yaml")
            apply(training, "anomaly_root")
            apply(training, "project_dir")
            apply(training, "save_dir")
            for record in getattr(training, "history", []) or []:
                if not isinstance(record, dict):
                    continue
                for key, value in list(record.items()):
                    text, hit = rewrite(value)
                    if hit:
                        record[key] = text
                        changed += 1
        export = getattr(project, "export", None)
        if export is not None:
            apply(export, "output_dir")
        return changed

    @classmethod
    def migrate_layout(cls, project: Project) -> list[str]:
        """把旧布局的产物目录迁移到 dataset/ 与 model/ 下（幂等）。

        旧布局（产物直接放在项目文件夹根下）：
            `<项目文件夹>/<拆分名>/`、`/runs/`、`/export/`
        新布局：
            `<项目文件夹>/dataset/<拆分名>/`、`/model/`、`/model/export/`

        只处理**项目文件夹之内**的目录；磁盘移动与引用改写同步进行，
        已迁移 / 不存在的路径直接跳过（重复打开安全）。返回迁移出的
        相对路径列表（空 = 无需迁移）。
        """
        path = str(project.params.get("path") or "").strip()
        if not path:
            return []
        root = cls.project_dir(Path(path))
        if not root.is_dir():
            return []
        dataset_root = cls.dataset_dir(path)
        model_root = cls.model_dir(path)
        moved: list[str] = []

        def relocate(old: Path, new: Path) -> None:
            """旧目录存在且新位置空闲时移动。

            处理「新位置在旧目录内部」的命名冲突：默认拆分名就是
            「dataset」，旧布局的 `<项目>/dataset/` 直接是拆分产物，
            而新布局要求它是母目录——先整体挪到中转名、再建母目录放回。
            """
            if not old.is_dir() or new.exists():
                return
            tmp = old.parent / f".{old.name}.migrate"
            try:
                if new.parent == old or str(new.parent).startswith(
                    str(old) + os.sep
                ):
                    shutil.move(str(old), str(tmp))
                    new.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(tmp), str(new))
                else:
                    new.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(old), str(new))
            except OSError as exc:
                logger.warning("项目目录迁移失败 %s -> %s: %s", old, new, exc)
                if tmp.is_dir() and not old.exists():
                    try:
                        shutil.move(str(tmp), str(old))   # 尽力回滚
                    except OSError:
                        pass
                return
            moved.append(str(new.relative_to(root)))
            # 引用随目录改写（冲突场景里旧路径同时是母目录、仍存在，
            # 不能依赖 realign 的"旧位置不存在"判断）
            cls._replace_prefix(project, old, new)

        def realign(old: Path, new: Path) -> None:
            """引用对齐：旧位置已不存在、新位置存在时改写路径引用。"""
            if old.exists() or not new.exists():
                return
            cls._replace_prefix(project, old, new)

        # 1) 拆分产物：<root>/<拆分名> → dataset/<拆分名>
        for split in getattr(project, "splits", []) or []:
            name = str(getattr(split, "name", "") or "").strip()
            if not name:
                continue
            relocate(root / name, dataset_root / name)
            realign(root / name, dataset_root / name)
        # 2) 训练产物：<root>/runs → model
        relocate(root / "runs", model_root)
        realign(root / "runs", model_root)
        # 3) 导出件：<root>/export → model/export
        relocate(root / "export", model_root / "export")
        realign(root / "export", model_root / "export")
        return moved

    @staticmethod
    def _is_inside(path: Path, parent: Path) -> bool:
        """path 是否位于 parent 之内（用于避免误删项目目录之外的用户数据）。"""
        try:
            path.resolve().relative_to(parent.resolve())
            return True
        except (ValueError, OSError):
            return False

    @classmethod
    def artifacts(cls, path: str | Path, project: Project | None = None) -> dict:
        """列出删除该项目会一并清理的内容（供确认对话框与删除使用）。

        Returns:
            {"file": 项目文件, "folder": 项目文件夹, "backup": 备份文件,
             "models": [训练产物], "extra": [文件夹之外的旧产物目录]}
        """
        target = Path(path)
        folder = cls.project_dir(target)
        models: list[Path] = []
        if folder.is_dir():
            for pattern in ("*.pt", "*.ckpt", "*.torchscript"):
                models.extend(
                    item for item in folder.rglob(pattern) if item.is_file()
                )

        # 旧版项目把拆分 / 增强产物放在项目文件同级目录，这里一并列出；
        # 只认「项目文件所在目录之内」的路径，避免误删用户放在别处的数据
        extra: list[Path] = []
        if project is not None:
            parent = target.parent
            for split in getattr(project, "splits", []) or []:
                raw = str(getattr(split, "output_dir", "") or "")
                candidate = Path(raw) if raw else None
                if candidate is None or not candidate.is_dir():
                    continue
                if cls._is_inside(candidate, folder):
                    continue
                if cls._is_inside(candidate, parent):
                    extra.append(candidate)

        return {
            "file": target,
            "folder": folder,
            "backup": cls.backup_path(target),
            "models": sorted(models),
            "extra": sorted({item for item in extra}),
        }

    @classmethod
    def delete_project(
        cls, path: str | Path, project: Project | None = None
    ) -> dict:
        """删除项目文件、项目文件夹与备份文件。

        Returns:
            {"removed": [已删除路径], "failed": [(路径, 原因)]}
        """
        info = cls.artifacts(path, project)
        removed: list[str] = []
        failed: list[tuple[str, str]] = []

        for folder in (info["folder"], *info["extra"]):
            if not folder.is_dir():
                continue
            try:
                shutil.rmtree(folder, onerror=_force_remove)
                removed.append(str(folder))
            except OSError as exc:
                failed.append((str(folder), str(exc)))

        for item in (info["backup"], info["file"]):
            if item is None or not item.is_file():
                continue
            try:
                item.unlink()
                removed.append(str(item))
            except OSError as exc:
                failed.append((str(item), str(exc)))

        logger.info(
            "已删除项目「%s」：%d 项%s",
            Path(path).stem, len(removed),
            f"，{len(failed)} 项失败" if failed else "",
        )
        return {"removed": removed, "failed": failed}

    # -----------------------------------------------------------
    # 自动保存与崩溃恢复
    # -----------------------------------------------------------
    @staticmethod
    def backup_path(path: str | Path) -> Path:
        """自动备份文件路径：<项目>.mprj.bak"""
        target = Path(path)
        return target.with_suffix(target.suffix + ".bak")

    def autosave(self, project: Project | None = None) -> Path | None:
        """把当前项目写入 `<项目>.bak`（不改变「未保存」标记）。

        清单重打包：ZIP 载荷字节复用、不重新压缩归档条目；写盘为原子
        替换（临时文件 + os.replace）。界面侧通常在后台线程调用
        `write_backup_payload`（见 ProjectViewModel 的自动备份）。

        Returns:
            备份文件路径；无法写入（无项目 / 文件不存在 / 无容器）时返回 None。
        """
        payload = self.autosave_payload(project)
        if payload is None:
            return None
        try:
            return ProjectService.write_backup_payload(payload)
        except (OSError, ValueError) as exc:
            logger.warning("自动保存失败: %s", exc)
            return None

    def autosave_payload(self, project: Project | None = None) -> dict | None:
        """自动备份所需的快照数据（主线程准备，可交后台线程）。

        只做 `project.to_dict()` 快照（毫秒级）与引用收集；几百 MB 的
        归档载荷以不可变 bytes 引用传递，主线程不做任何打包 / 压缩。

        Returns:
            {"manifest": dict, "source": bytes, "zip_sha256": str,
             "backup": Path} 或 None（无项目 / 文件不存在 / 无容器）。
        """
        project = project or self.project
        if project is None:
            return None
        raw = str(project.params.get("path", "") or "")
        path = Path(raw)
        if not path.is_file() or self._container is None:
            return None
        return {
            "manifest": project.to_dict(),
            "source": self._container.data,
            "zip_sha256": self._container.zip_sha256,
            "backup": self.backup_path(path),
        }

    @staticmethod
    def write_backup_payload(payload: dict) -> Path:
        """把快照写成 .bak（原子替换：临时文件 + os.replace）。

        只做打包与文件写入，输入全为不可变数据——可在后台线程安全调用。
        """
        data = ProjectContainer.pack_with_manifest(
            payload["source"], payload["manifest"], payload.get("zip_sha256", "")
        )
        backup = Path(payload["backup"])
        tmp = backup.with_name(backup.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, backup)
        logger.info("自动保存：%s", backup)
        return backup

    @staticmethod
    def clear_backup(project_path: str | Path) -> None:
        """删除项目旁的自动备份（人工已确认保存 / 不保存后调用）。

        正常关闭项目、退出程序的路径在人工决策后清理备份；只有死机 /
        强制结束等异常退出会残留 .bak（见 `pending_backup`）。
        """
        if not str(project_path or "").strip():
            return
        backup = ProjectService.backup_path(project_path)
        # 顺带清理原子写盘可能残留的 .tmp（写盘被中断等）
        for target in (backup, backup.with_name(backup.name + ".tmp")):
            if not target.is_file():
                continue
            try:
                target.unlink()
                logger.info("已清理自动备份: %s", target)
            except OSError as exc:
                logger.warning("清理自动备份失败 %s: %s", target, exc)

    @staticmethod
    def pending_backup(project_path: str | Path) -> dict | None:
        """项目旁的自动备份信息（上次会话异常退出的残留）。

        正常流程在保存 / 关闭项目 / 退出程序时会清理 .bak，因此它存在
        即意味着上次会话未正常结束。打开项目时据此提示人工选择版本。

        Returns:
            {"backup": str, "project_time": str, "backup_time": str} 或 None。
        """
        path = Path(project_path)
        if not path.is_file():
            return None
        backup = ProjectService.backup_path(path)
        if not backup.is_file():
            return None
        fmt = "%Y-%m-%d %H:%M:%S"
        try:
            return {
                "backup": str(backup),
                "project_time": datetime.fromtimestamp(
                    path.stat().st_mtime
                ).strftime(fmt),
                "backup_time": datetime.fromtimestamp(
                    backup.stat().st_mtime
                ).strftime(fmt),
            }
        except OSError:
            return None

    @staticmethod
    def recover(backup: str | Path) -> Path:
        """用自动备份覆盖项目文件（备份随后删除）。"""
        backup = Path(backup)
        if not backup.is_file():
            raise ProjectFormatError(f"备份文件不存在: {backup}")
        # 备份名形如 xxx.mprj.bak → 去掉 .bak 即项目文件
        target = Path(str(backup)[:-4])
        shutil.copy2(backup, target)
        backup.unlink()
        logger.info("已从自动备份恢复项目: %s", target)
        return target

    @staticmethod
    def discard_backup(backup: str | Path) -> None:
        path = Path(backup)
        if path.is_file():
            path.unlink()
            logger.info("已忽略自动备份: %s", path)

    def save_as(
        self,
        new_path: str | Path,
        project: Project | None = None,
        progress=None,
    ) -> Path:
        """把当前项目另存为新的 .mprj（连同项目文件夹一并复制）。

        复制 `<旧名>/` 整棵目录树（dataset 拆分产物、model 训练与导出
        产物）到 `<新名>/`，项目内指向旧文件夹的路径引用同步改写——
        只复制 .mprj 会让复制件打开后产物缺失 / 串回旧项目（历史缺陷）。

        Args:
            progress: 可选回调 ``(done, total)``，复制项目文件夹期间上报。

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
        old_folder = self.project_dir(previous) if previous else None
        new_folder = self.project_dir(target)
        # 引用改写：指向旧文件夹的产物路径改为新文件夹（写进新 .mprj）
        if old_folder is not None and old_folder != new_folder:
            self._replace_prefix(project, old_folder, new_folder)
        project.params["path"] = str(target)
        project.touch()
        self._container = self._container.rebuild(project)
        try:
            self._write(target, self._container.data)
        except OSError:
            # 写入失败则回滚，避免当前项目指向一个不存在的文件
            project.params["path"] = previous
            if old_folder is not None and old_folder != new_folder:
                self._replace_prefix(project, new_folder, old_folder)
            raise
        project.mark_saved()
        self._config.add_recent_project(str(target), project.name)
        # 保存动作已有人工确认：原路径旁的自动备份（若有）一并清理
        self.clear_backup(previous)
        # 连同项目文件夹复制（拆分数据集、训练模型等产物）
        copied = self.copy_project_folder(old_folder, new_folder, progress)
        logger.info(
            "项目另存为: %s -> %s（连同项目文件夹 %d 个文件）",
            previous, target, copied,
        )
        return target

    @staticmethod
    def copy_project_folder(old, new, progress=None) -> int:
        """复制项目文件夹（旧 → 新，合并式）；返回复制的文件数。

        Args:
            progress: 可选回调 ``(done, total)``（每 20 个文件 / 结束时上报）。
        """
        if old is None or not Path(old).is_dir():
            return 0
        old, new = Path(old), Path(new)
        if old == new:
            return 0
        items = [item for item in old.rglob("*") if item.is_file()]
        total = len(items)
        for index, item in enumerate(items, start=1):
            out = new / item.relative_to(old)
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, out)
            if progress is not None and (index % 20 == 0 or index == total):
                progress(index, total)
        return total

    def close(self) -> None:
        """关闭当前项目（只清空内存状态，不删除文件）。"""
        self._container = None
        logger.info("已关闭当前项目")

    @staticmethod
    def remove_project_file(path: str | Path) -> None:
        """只删除 .mprj 项目文件（保留项目文件夹）。"""
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
            project.mark_saved()      # 全量已写盘，未保存标记随之清零
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
            project.mark_saved()      # 全量已写盘，未保存标记随之清零
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
        project.mark_saved()      # 全量已写盘，未保存标记随之清零

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