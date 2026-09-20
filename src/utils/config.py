"""配置管理：基于 QSettings 的跨会话配置持久化。"""

import json
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Signal

from src.utils.constants import APP_NAME, BRAND_COLOR, ORG_NAME


class ConfigManager(QObject):
    """应用配置的读取与持久化。

    内部使用 QSettings 保存到本地 INI 文件，跨会话保留用户偏好
    （主题、主题色、最近项目、界面与训练默认参数等）。
    """

    # 主题或主题色变化时发出，供界面刷新
    themeChanged = Signal(str)          # "light" / "dark"
    accentColorChanged = Signal(str)
    preferencesChanged = Signal()        # 偏好设置变化（界面据此应用新值）

    # 偏好设置项：键 → 默认值（顺序即界面顺序，见 PreferencesDialog）
    PREFERENCES = {
        "pref/default_project_dir": "",     # 空表示用「文档」目录
        "pref/max_recent": 10,              # 最近项目数量 0~50
        "pref/open_last_project": False,    # 启动时打开最近项目
        "pref/cpu_threads": 4,              # 训练 CPU 线程数
        "pref/wheel_inverted": False,       # 滚轮缩放方向反向
        "pref/crosshair": True,             # 标注十字准线
        "pref/crosshair_opacity": 100,      # 十字准线不透明度 %
        "pref/region_opacity": 50,          # 标注区域不透明度 %
        "pref/default_brightness": 0,       # 图像默认亮度
        "pref/default_contrast": 0,         # 图像默认对比度
        "pref/autosave_seconds": 60,        # 自动保存间隔（秒），0 = 关闭
        "pref/show_pixel_value": True,      # 按住 Shift 显示像素值
        "pref/log_backup_count": 10,        # 滚动日志保留份数
        "pref/log_max_mb": 5,               # 单个日志文件上限（MB）
    }

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._settings = QSettings(ORG_NAME, APP_NAME)

        # 默认值
        defaults = {
            "app/theme": "auto",          # light / dark / auto
            "app/accent_color": BRAND_COLOR,
            "recent_projects": "[]",      # JSON 数组字符串
            **self.PREFERENCES,
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
    # 偏好设置
    # -----------------------------------------------------------
    def pref(self, key: str):
        """读取偏好项（带类型归一化）。"""
        default = self.PREFERENCES.get(key)
        raw = self._settings.value(key, default)
        if isinstance(default, bool):
            if isinstance(raw, str):
                return raw.strip().lower() in ("1", "true", "yes", "on")
            return bool(raw)
        if isinstance(default, int):
            try:
                return int(raw)
            except (TypeError, ValueError):
                return int(default or 0)
        return raw if raw is not None else default

    def set_pref(self, key: str, value, notify: bool = False) -> None:
        self._settings.setValue(key, value)
        if notify:
            self.preferencesChanged.emit()

    def notify_preferences_changed(self) -> None:
        self.preferencesChanged.emit()

    # 常用偏好项的语义化访问
    @property
    def default_project_dir(self) -> str:
        value = str(self.pref("pref/default_project_dir") or "").strip()
        if value:
            return value
        return str(Path.home() / "Documents")

    @property
    def max_recent(self) -> int:
        return max(0, min(50, int(self.pref("pref/max_recent") or 0)))

    @property
    def open_last_project(self) -> bool:
        return bool(self.pref("pref/open_last_project"))

    @property
    def cpu_threads(self) -> int:
        return max(1, min(64, int(self.pref("pref/cpu_threads") or 1)))

    @property
    def wheel_inverted(self) -> bool:
        return bool(self.pref("pref/wheel_inverted"))

    @property
    def crosshair(self) -> bool:
        return bool(self.pref("pref/crosshair"))

    @property
    def crosshair_opacity(self) -> int:
        return max(15, min(100, int(self.pref("pref/crosshair_opacity") or 100)))

    @property
    def region_opacity(self) -> int:
        return max(0, min(100, int(self.pref("pref/region_opacity") or 50)))

    @property
    def default_brightness(self) -> int:
        return max(-100, min(100, int(self.pref("pref/default_brightness") or 0)))

    @property
    def default_contrast(self) -> int:
        return max(-100, min(100, int(self.pref("pref/default_contrast") or 0)))

    @property
    def autosave_seconds(self) -> int:
        return max(0, min(600, int(self.pref("pref/autosave_seconds") or 0)))

    @property
    def show_pixel_value(self) -> bool:
        return bool(self.pref("pref/show_pixel_value"))

    @property
    def log_backup_count(self) -> int:
        """日志保留份数（1~50）。"""
        return max(1, min(50, int(self.pref("pref/log_backup_count") or 10)))

    @property
    def log_max_mb(self) -> int:
        """单个日志文件上限（1~100 MB）。"""
        return max(1, min(100, int(self.pref("pref/log_max_mb") or 5)))

    def reset_preferences(self) -> None:
        """恢复默认偏好设置（不动最近项目列表与主题）。"""
        for key, value in self.PREFERENCES.items():
            self._settings.setValue(key, value)
        self.preferencesChanged.emit()

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
        limit = self.max_recent
        self._settings.setValue(
            "recent_projects", json.dumps(recent[:limit] if limit else [])
        )

    # -----------------------------------------------------------
    # 日志目录
    # -----------------------------------------------------------
    @staticmethod
    def log_dir() -> str:
        """日志所在目录（供帮助菜单打开）。"""
        from src.utils.constants import DATA_DIR

        return str(DATA_DIR / "logs")

    def clear_recent_projects(self) -> None:
        self._settings.setValue("recent_projects", "[]")


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")