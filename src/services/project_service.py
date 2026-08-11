"""项目服务：SQLite 持久化项目元数据 + 项目目录创建。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.models.project import Project
from src.utils.constants import DATA_DIR, SQLITE_DB_PATH
from src.utils.logger import get_logger

logger = get_logger("project")


class ProjectService:
    """项目元数据的增删改查与磁盘目录管理。"""

    def __init__(self, db_path: str | Path | None = None):
        self._db_path = Path(db_path) if db_path else SQLITE_DB_PATH
        self._init_db()

    # -----------------------------------------------------------
    # 数据库初始化
    # -----------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    name        TEXT NOT NULL,
                    work_dir    TEXT NOT NULL,
                    model_type  TEXT NOT NULL DEFAULT 'detect',
                    description TEXT DEFAULT '',
                    created_at  TEXT,
                    updated_at  TEXT,
                    status      TEXT DEFAULT 'draft'
                )
                """
            )

    # -----------------------------------------------------------
    # CRUD
    # -----------------------------------------------------------
    def create(self, project: Project) -> int:
        """落库并返回项目 id。"""
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO projects (name, work_dir, model_type, description,
                                      created_at, updated_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project.name, project.work_dir, project.model_type,
                    project.description, project.created_at,
                    project.updated_at, project.status,
                ),
            )
            return int(cur.lastrowid)

    def get(self, project_id: int) -> Project | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        return Project.from_dict(dict(row)) if row else None

    def list_all(self) -> list[Project]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM projects ORDER BY updated_at DESC"
            ).fetchall()
        return [Project.from_dict(dict(r)) for r in rows]

    def update(self, project_id: int, project: Project) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE projects SET name=?, work_dir=?, model_type=?,
                       description=?, updated_at=?, status=?
                WHERE id=?
                """,
                (
                    project.name, project.work_dir, project.model_type,
                    project.description, project.updated_at, project.status,
                    project_id,
                ),
            )

    def delete(self, project_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))

    # -----------------------------------------------------------
    # 项目目录
    # -----------------------------------------------------------
    @staticmethod
    def create_project_dir(base_dir: str | Path, name: str) -> Path:
        """在 base_dir 下创建项目目录结构并返回路径。"""
        project_dir = Path(base_dir) / name
        for sub in ("images", "labels", "runs", "config"):
            (project_dir / sub).mkdir(parents=True, exist_ok=True)
        logger.info("创建项目目录: %s", project_dir)
        return project_dir