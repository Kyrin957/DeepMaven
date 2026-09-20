"""应用入口：解析命令行参数、创建 QApplication 并启动主窗口。

命令行参数（对齐 DLT 的常用启动参数）：

    --project <path>        启动后直接打开指定 `.mprj` 项目
    --reset-preferences     启动前恢复默认偏好设置
    --version               打印版本号后退出（不启动界面）
    --log-level <LEVEL>     日志级别：DEBUG / INFO / WARNING / ERROR
"""

from __future__ import annotations

import argparse
import logging
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from qfluentwidgets import FluentIcon, FluentWindow, isDarkTheme, setTheme, Theme

from src.utils.config import ConfigManager
from src.utils.constants import APP_NAME, APP_VERSION, BRAND_COLOR, ORG_NAME
from src.utils.logger import setup_logger
from src.views.main_window import MainWindow

# 高分屏缩放支持
QApplication.setHighDpiScaleFactorRoundingPolicy(
    Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
)


def parse_args(argv: list) -> tuple[argparse.Namespace, list]:
    """解析本程序的命令行参数（未知参数原样返回给 Qt）。"""
    parser = argparse.ArgumentParser(
        prog=APP_NAME.lower(),
        description=f"{APP_NAME} 桌面端：数据标注 → 训练 → 评估 → 导出",
    )
    parser.add_argument("--project", "-p", default="",
                        help="启动后打开指定的 .mprj 项目")
    parser.add_argument("--reset-preferences", action="store_true",
                        help="恢复默认偏好设置")
    parser.add_argument("--version", action="store_true", help="显示版本号并退出")
    parser.add_argument("--log-level", default="",
                        choices=["", "DEBUG", "INFO", "WARNING", "ERROR"],
                        help="日志级别（默认 INFO）")
    args, rest = parser.parse_known_args(list(argv))
    return args, rest


def run(argv: list | None = None) -> int:
    """启动桌面应用并返回退出码。"""
    argv = list(sys.argv if argv is None else argv)
    args, rest = parse_args(argv[1:])
    if args.version:
        print(f"{APP_NAME} {APP_VERSION}")
        return 0

    app = QApplication([argv[0], *rest])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)

    # 配置（先读配置，日志的滚动份数 / 单文件上限由偏好设置决定）
    config = ConfigManager()
    if args.reset_preferences:
        config.reset_preferences()
    config.set_accent_color(config.accent_color)  # 应用持久化主题色

    level = getattr(logging, args.log_level) if args.log_level else logging.INFO
    logger = setup_logger(
        level=level,
        backup_count=config.log_backup_count,
        max_bytes=config.log_max_mb * 1024 * 1024,
    )
    logger.info("=== %s %s 启动 ===", APP_NAME, APP_VERSION)
    if args.project:
        logger.info("命令行指定项目：%s", args.project)

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

    # 打开项目：命令行优先，其次按偏好打开最近项目
    target = str(args.project or "")
    if target:
        from pathlib import Path

        if Path(target).is_file():
            window.project_vm.open_project(target)
        else:
            logger.warning("命令行指定的项目不存在：%s", target)
    elif config.open_last_project:
        recent = config.recent_projects()
        if recent:
            target = str(recent[0].get("path") or "")
            from pathlib import Path

            if target and Path(target).is_file():
                window.project_vm.open_project(target)
            else:
                logger.info("最近项目不存在，跳过自动打开：%s", target)

    exit_code = app.exec()

    logger.info("=== %s 退出 ===", APP_NAME)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(run())
