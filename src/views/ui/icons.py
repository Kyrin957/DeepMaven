"""应用图标库：qtawesome（Material Design Icons 为主）+ 明暗主题跟随。

为什么引入
----------
qfluentwidgets 的 FluentIcon 覆盖常用字形，但「首张 / 末张」一类缺少合适图标，
此前借用 Qt 媒体播放标准图标（实心 ⏮ / ⏭）：语义不对，且与页面里的线性
箭头混搭。qtawesome 是 Qt 生态主流的图标库封装（内置 Material Design Icons、
Font Awesome、Phosphor 等 9 套字体），这里把它收敛成**语义名**接口，
页面不直接写字体与图标名（见《开发文档.md》§4.28）。

用法
----
    from src.views.ui import bind_icon, icon, icon_button

    self.first_btn = icon_button(icon("first"), "首张图片", card,
                                 size=T.ICON_BTN_MD)
    bind_icon(self.first_btn, "first")   # 登记后随明暗主题自动重绘

约定
----
* 语义名 → 图标名集中在 ``_NAMES`` 维护；
* qtawesome 的图标颜色在创建时烘焙进 QIcon，主题切换后必须重设 ——
  ``bind_icon`` 会登记按钮并在 ``qconfig.themeChanged`` 时统一刷新；
  颜色与 FluentIcon 保持一致（亮色主题纯黑 #000000 / 暗色主题纯白 #FFFFFF）。
"""

from __future__ import annotations

import weakref

import qtawesome as qta
from PySide6.QtGui import QIcon
from qfluentwidgets import isDarkTheme, qconfig

# 与 FluentIcon 相同的明暗主题前景色（实测 FluentIcon 的渲染色）
_COLOR_LIGHT = "#000000"
_COLOR_DARK = "#FFFFFF"

# 语义名 → qtawesome 图标名（Material Design Icons）
_NAMES: dict[str, str] = {
    "first": "mdi.page-first",          # 首张
    "prev": "mdi.chevron-left",         # 上一张
    "next": "mdi.chevron-right",        # 下一张
    "last": "mdi.page-last",            # 末张
    "select-all": "mdi.select-all",     # 全选
    "zoom-in": "mdi.magnify-plus-outline",    # 放大
    "zoom-out": "mdi.magnify-minus-outline",  # 缩小
    "fit": "mdi.fit-to-page-outline",         # 适应窗口
    "actual-size": "mdi.numeric-1-box-outline",  # 原始大小（100%）
    "save-as": "mdi.content-save-outline",    # 另存为
    "reveal": "mdi.folder-open-outline",      # 打开所在位置
    "import": "mdi.file-import-outline",      # 导入文件（自定义权重等）
    "export": "mdi.file-export-outline",      # 导出文件（评估报告 / 模型）
    "locked": "mdi.lock",                     # 锁定（被训练引用的拆分等）
    "cursor-mouse": "mdi.cursor-default-outline",  # 鼠标光标（选择 / 绘制）
    "cursor-hand": "mdi.hand",                     # 手掌光标（左键拖动图像）
    "play": "mdi.play",                       # 开始 / 继续训练
    "pause": "mdi.pause",                     # 暂停训练
    "stop": "mdi.stop",                       # 停止训练
    "reset": "mdi.refresh",                   # 复位 / 重来
}

# 已登记「主题切换重绘」的按钮（弱引用，控件销毁后自动出列）
_bound: list[tuple[weakref.ReferenceType, str]] = []
_listener_ready = False


def icon(name: str, color: str | None = None) -> QIcon:
    """按语义名取图标；未登记的语义名按 qtawesome 原名透传（如 ``mdi.xxx``）。"""
    return qta.icon(_NAMES.get(name, name), color=color or _theme_color())


def bind_icon(button, name: str) -> None:
    """给按钮设置图标，并登记为「明暗主题切换时自动重绘」。

    同一个按钮重复登记时**更新语义名**（不再追加第二条记录）——供
    「图标随状态切换」的按钮改绑（如手掌 / 鼠标光标开关）。
    """
    button.setIcon(icon(name))
    for index, (reference, _old) in enumerate(_bound):
        if reference() is button:
            _bound[index] = (reference, name)
            _ensure_listener()
            return
    _bound.append((weakref.ref(button), name))
    _ensure_listener()


def refresh_icons() -> None:
    """按当前主题重绘所有已登记按钮的图标（主题切换回调）。"""
    alive: list[tuple[weakref.ReferenceType, str]] = []
    for reference, name in _bound:
        button = reference()
        if button is None:
            continue
        try:
            button.setIcon(icon(name))
        except RuntimeError:            # 底层 C++ 对象已销毁
            continue
        alive.append((reference, name))
    _bound[:] = alive


def _theme_color() -> str:
    return _COLOR_DARK if isDarkTheme() else _COLOR_LIGHT


def _ensure_listener() -> None:
    global _listener_ready
    if _listener_ready:
        return
    qconfig.themeChanged.connect(lambda *_args: refresh_icons())
    _listener_ready = True


__all__ = ["icon", "bind_icon", "refresh_icons"]
