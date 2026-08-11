"""日志工具：统一配置 logging，输出到控制台与日志文件。"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from src.utils.constants import DATA_DIR

_LOGGER_NAME = "DeepMaven"


def setup_logger(
    level: int = logging.INFO,
    log_to_file: bool = True,
    log_dir: Path | None = None,
) -> logging.Logger:
    """初始化应用日志器。

    Args:
        level: 日志级别。
        log_to_file: 是否同时写入日志文件。
        log_dir: 日志文件目录，默认使用 data/logs。

    Returns:
        已配置的 logger 实例。
    """
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台 handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(fmt)
    logger.addHandler(console)

    # 文件 handler（滚动日志，单文件 5MB，保留 3 份）
    if log_to_file:
        log_dir = log_dir or (DATA_DIR / "logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "deepmaven.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)

    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """获取应用日志器（或子日志器）。"""
    if name:
        return logging.getLogger(f"{_LOGGER_NAME}.{name}")
    return logging.getLogger(_LOGGER_NAME)