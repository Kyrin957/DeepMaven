"""无人值守开关：把「必须由人工决策」的弹窗换成固定答案。

界面自检 / 批量脚本在**真机**上跑时无人点击，弹窗会把脚本挂死在事件循环里
（历史故障：自检脚本收尾 ``win.close()`` 撞上「未保存的更改」询问，进程一直
等点击）。打开本开关后：

* 未保存询问 → 固定 **「保存」**（不丢数据，也不静默丢弃）；
* 异常退出残留备份的版本询问 → 固定选**较新的一份**。

只在**显式置位环境变量**时生效（``DEEPMaven_AUTOMATION=1``），默认关闭——
产品行为与交互一个字都不变。自检脚本在开头置位即可（判断发生在弹窗函数
内部，运行时读环境变量，不依赖导入顺序）：

    import os
    os.environ["DEEPMaven_AUTOMATION"] = "1"   # 自检：无人值守
    from src.views.main_window import MainWindow
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

#: 置位本环境变量（``1`` / ``true`` / ``yes`` / ``on``，大小写不敏感）即打开
AUTOMATION_ENV = "DEEPMaven_AUTOMATION"

#: 备份版本询问里的两个选项（与 `views/dialogs/backup_dialog` 同值）
_BACKUP = "backup"
_PROJECT = "project"
_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def automation_enabled() -> bool:
    """是否处于无人值守模式（仅自检 / 批量脚本置位；产品默认关闭）。"""
    return str(os.environ.get(AUTOMATION_ENV, "")).strip().lower() in {
        "1", "true", "yes", "on",
    }


def unsaved_choice() -> str | None:
    """未保存询问的自动答案；``None`` = 照常弹窗（默认行为）。"""
    if not automation_enabled():
        return None
    logger.info("无人值守（%s）：未保存改动按「保存」处理", AUTOMATION_ENV)
    return "save"


def backup_choice(info: dict | None = None) -> str | None:
    """备份版本询问的自动答案；``None`` = 照常弹窗（默认行为）。

    选**较新的一份**（异常退出的备份通常比项目文件新）；时间解析失败时保守
    选「用项目文件版本」，同样不会丢备份。
    """
    if not automation_enabled():
        return None
    info = info or {}
    try:
        backup_time = datetime.strptime(
            str(info.get("backup_time") or ""), _TIME_FORMAT
        )
        project_time = datetime.strptime(
            str(info.get("project_time") or ""), _TIME_FORMAT
        )
    except ValueError:
        choice = _PROJECT
    else:
        choice = _BACKUP if backup_time >= project_time else _PROJECT
    logger.info(
        "无人值守（%s）：备份版本按「%s」处理（项目 %s / 备份 %s）",
        AUTOMATION_ENV, choice,
        info.get("project_time"), info.get("backup_time"),
    )
    return choice
