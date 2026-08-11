"""应用入口：创建 QApplication 并启动主窗口。"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from qfluentwidgets import FluentIcon, FluentWindow, isDarkTheme, setTheme, Theme

from src.utils.config import ConfigManager
from src.utils.constants import APP_NAME, BRAND_COLOR, ORG_NAME
from src.utils.logger import setup_logger
from src.views.main_window import MainWindow

# 高分屏缩放支持
QApplication.setHighDpiScaleFactorRoundingPolicy(
    Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
)


def run() -> int:
    """启动桌面应用并返回退出码。"""
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)

    # 日志
    logger = setup_logger()
    logger.info("=== %s 启动 ===", APP_NAME)

    # 配置
    config = ConfigManager()
    config.set_accent_color(config.accent_color)  # 应用持久化主题色

    # 主题
    theme = config.theme
    if theme == "light":
        setTheme(Theme.LIGHT)
    elif theme == "dark":
        setTheme(Theme.DARK)
    else:
        setTheme(Theme.AUTO)

    window = MainWindow()
    window.show()
    exit_code = app.exec()

    logger.info("=== %s 退出 ===", APP_NAME)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(run())