"""配置管理：基于 QSettings 的跨会话配置持久化。"""

import json
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Signal

from src.utils.constants import APP_NAME, BRAND_COLOR, ORG_NAME


class ConfigManager(QObject):
    """应用配置的读取与持久化。

    内部使用 QSettings 保存到本地 INI 文件，跨会话保留用户偏好
    （如主题、主题色、最近项目列表等）。
    """

    # 主题或主题色变化时发出，供界面刷新
    themeChanged = Signal(str)          # "light" / "dark"
    accentColorChanged = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._settings = QSettings(ORG_NAME, APP_NAME)

        # 默认值
        defaults = {
            "app/theme": "auto",          # light / dark / auto
            "app/accent_color": BRAND_COLOR,
            "recent_projects": "[]",      # JSON 数组字符串
        }
        for key, value in defaults.items():
            if not self._settings.contains(key):
                self._settings.setValue(key, value)

    # -----------------------------------------------------------
    # 通用读写
    # -----------------------------------------------------------
    def get(self, key: str, default=None):
        return self._settings.value(key, default)

    def set(self, key: str, value) -> None:
        self._settings.setValue(key, value)

    # -----------------------------------------------------------
    # 主题
    # -----------------------------------------------------------
    @property
    def theme(self) -> str:
        return str(self._settings.value("app/theme", "auto"))

    def set_theme(self, theme: str) -> None:
        self._settings.setValue("app/theme", theme)
        self.themeChanged.emit(theme)

    @property
    def accent_color(self) -> str:
        return str(self._settings.value("app/accent_color", BRAND_COLOR))

    def set_accent_color(self, color: str) -> None:
        self._settings.setValue("app/accent_color", color)
        self.accentColorChanged.emit(color)

    # -----------------------------------------------------------
    # 最近项目
    # -----------------------------------------------------------
    def recent_projects(self) -> list[dict]:
        try:
            raw = self._settings.value("recent_projects", "[]")
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []

    def add_recent_project(self, project_path: str, name: str = "") -> None:
        recent = self.recent_projects()
        # 去重：同一路径只保留最新一次
        recent = [p for p in recent if p.get("path") != project_path]
        recent.insert(0, {"path": project_path, "name": name, "time": _now_iso()})
        self._settings.setValue("recent_projects", json.dumps(recent[:10]))

    def clear_recent_projects(self) -> None:
        self._settings.setValue("recent_projects", "[]")


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")