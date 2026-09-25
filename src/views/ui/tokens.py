"""界面设计令牌（Design Tokens）。

集中定义间距、控件宽度、容器内边距等**布局常量**，取代散落在各页面里的
魔法数字。所有界面代码都应从这里取值，而不是写 `setFixedWidth(96)`。

为什么要这一层
--------------
之前每个页面各自写死尺寸（240 / 268 / 84 / 96 / 150 …），带来三类问题：

1. **改一处不影响别处**：想统一参数框宽度得翻遍 20 多个文件；
2. **旋钮/输入框宽度不随字体缩放**：高分屏（125% / 150%）下系统字体变宽，
   写死的宽度装不下内容，只能截断或错位；
3. **无法形成节奏**：间距忽大忽小，界面看起来"东一块西一块"。

这里的常量是**逻辑像素**（Qt 已按 devicePixelRatio 缩放），因此天然适配
高分屏的整数缩放；对"随字体度量变化"的宽度，请用 ``field_width()``。

单位约定
--------
间距一律取 4 的倍数（4 / 6 / 8 / 12 / 16 / 24），与 Fluent 栅格一致。
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

# ---------------------------------------------------------------------------
# 间距（spacing scale，4 的倍数）
# ---------------------------------------------------------------------------
SPACE_XXS = 2
SPACE_XS = 4
SPACE_SM = 6
SPACE_MD = 8
SPACE_ML = 10
SPACE_LG = 12
SPACE_XL = 16
SPACE_2XL = 20
SPACE_XXL = 24

# ---------------------------------------------------------------------------
# 容器内边距
# ---------------------------------------------------------------------------
# 卡片
CARD_PAD_H = 14
CARD_PAD_V = 12
# 页面内容区
PAGE_PAD_H = 24
PAGE_PAD_V = 16

# ---------------------------------------------------------------------------
# 侧栏宽度
# ---------------------------------------------------------------------------
SIDE_W_NARROW = 240        # 标注页左栏（缩略图为主）
SIDE_W = 268               # 通用信息/参数侧栏
SIDE_W_WIDE = 290          # 训练页左栏（含模型选择与训练记录）

# ---------------------------------------------------------------------------
# 数值输入（步进器）宽度策略
# ---------------------------------------------------------------------------
# 不直接写死像素：按"能容纳多少字符 + 右侧步进按钮"计算，字体变大时自动变宽，
# 从而避免高分屏下的截断与错位。
STEPPER_CHARS_NARROW = 6   # 百分比、通道数一类
STEPPER_CHARS = 8          # 常规数值（epochs / batch / 学习率）
STEPPER_CHARS_WIDE = 11    # 带后缀或大数值（图像尺寸 px）

# 步进器宽度下限（防止字符数极少时框太小）
STEPPER_MIN_W = 64
# 说明：**不设上限**。宽度必须由「文本 + 步进按钮开销」算出来，写死上限等于
# 在字体变大（Fluent 字体 14px、125% / 150% 缩放）时把数字裁掉——历史上
# 168px 的上限让训练页 / 标注页 / 评估页一批数值框只露得出 5 个字符。

# 步进器「除文本外」的占位宽度（逻辑像素）——它决定数值框里**真正能显示几个
# 字符**，必须按控件实际样式取值（见 ``stepper_chrome()``），不能凭感觉给一个
# 数。数值来自 qfluentwidgets 1.11 的离屏实测：
#   行内样式（SpinBox / DoubleSpinBox）：文本区左缩进 11 + 两个 31px 步进按钮
#       + 5px 间距 + 4px 右内边距 + 文本区右侧留白 10 = 92
#   紧凑样式（CompactSpinBox）：文本区左缩进 11 + 单个 26px 步进按钮 + 1 = 38
#   普通输入框（无步进按钮）：左右内边距 ≈ 28
# 历史故障：这里曾写成 42（按「两个按钮 31px」想当然），于是 ``field_width()``
# 每格少算 50px，数值框外观正常、数字却被裁掉——拆分页的分配表整列看不到数。
# ``scripts/ui_selfcheck.py`` 会按真实渲染复核该常量。
STEPPER_CHROME_W_INLINE = 92
STEPPER_CHROME_W_COMPACT = 38
STEPPER_CHROME_W_PLAIN = 28

# 标签列最小宽度（表单左侧文字）
LABEL_MIN_W = 72

# 下拉框 / 文本框 / 按钮组等控件的常用宽度（避免各处写死 88/112/160）
CTRL_W_SM = 88
CTRL_W_MD = 112
CTRL_W_LG = 160
CTRL_W_XL = 180

# 图标按钮边长
ICON_BTN_SM = 24
ICON_BTN_MD = 28

# 侧栏面板最小宽度（图库筛选栏等）
PANEL_MIN_W = 170
# 评估 / 导出页右栏面板固定宽度
PANEL_W = 300
# 项目管理页左侧操作栏宽度
PANEL_W_WIDE = 320
# 结果分页按钮宽度
PAGER_BTN_W = 38

# ---------------------------------------------------------------------------
# 行高 / 控件高
# ---------------------------------------------------------------------------
ROW_H = 30

# ---------------------------------------------------------------------------
# 圆角（与 Fluent 卡片一致）
# ---------------------------------------------------------------------------
RADIUS_SM = 4
RADIUS_MD = 6
RADIUS_LG = 8

# ---------------------------------------------------------------------------
# 配色（仅用于自绘控件；普通控件请交给 qfluentwidgets 主题）
# ---------------------------------------------------------------------------
# 画布 / 预览占位背景
CANVAS_BG = "#1B1B1B"
# 容器化分组框的描边与底纹（浅色，兼容明暗主题可自行覆盖）
GROUP_BORDER = "rgba(128, 128, 128, 0.28)"
GROUP_BG = "rgba(128, 128, 128, 0.06)"


# ---------------------------------------------------------------------------
# 宽度计算
# ---------------------------------------------------------------------------
def stepper_chrome(reference: QWidget) -> int:
    """步进器「除文本外」的占位宽度。

    按控件自身的样式选择开销：紧凑步进器（``CompactSpinBox``）只有一个按钮，
    比行内步进器省 54px——窄侧栏里的栅格必须用它，否则数值没有可见空间。
    """
    if hasattr(reference, "compactSpinButton"):
        return STEPPER_CHROME_W_COMPACT
    if hasattr(reference, "upButton") or hasattr(reference, "downButton"):
        return STEPPER_CHROME_W_INLINE
    return STEPPER_CHROME_W_PLAIN


def field_width(reference: QWidget, chars: int = STEPPER_CHARS) -> int:
    """按字体度量计算数值输入框宽度（字体/缩放变化时自动适配）。

    参数
    ----
    reference : 数值输入框本身（用它区分行内 / 紧凑样式），并用它的 fontMetrics
                作为测量基准。
    chars     : 需要容纳的字符数（含后缀，如 "2048 px" 为 7）。

    返回
    ----
    逻辑像素宽度 = 文本所需宽度 + ``stepper_chrome(reference)``，
    **不设上限**（上限会把数字裁掉，见常量区的说明），只保底下限。

    注意：调用时机要早于布局、但此时控件字体必须是最终字体——QFluentWidgets
    在构造时就 ``setFont`` 了，按控件自身度量即可；自检脚本
    （``scripts/ui_selfcheck.py``）会按真实渲染复核「文本区 ≥ 文字宽度」。
    """
    fm = reference.fontMetrics()
    text_w = fm.horizontalAdvance("0" * max(1, int(chars)))
    return max(STEPPER_MIN_W, text_w + stepper_chrome(reference))


__all__ = [
    "SPACE_XXS", "SPACE_XS", "SPACE_SM", "SPACE_MD", "SPACE_ML", "SPACE_LG",
    "SPACE_XL", "SPACE_2XL", "SPACE_XXL",
    "CARD_PAD_H", "CARD_PAD_V", "PAGE_PAD_H", "PAGE_PAD_V",
    "SIDE_W_NARROW", "SIDE_W", "SIDE_W_WIDE",
    "STEPPER_CHARS_NARROW", "STEPPER_CHARS", "STEPPER_CHARS_WIDE",
    "STEPPER_MIN_W", "LABEL_MIN_W", "ROW_H",
    "STEPPER_CHROME_W_INLINE", "STEPPER_CHROME_W_COMPACT", "STEPPER_CHROME_W_PLAIN",
    "CTRL_W_SM", "CTRL_W_MD", "CTRL_W_LG", "CTRL_W_XL",
    "ICON_BTN_SM", "ICON_BTN_MD",
    "PANEL_MIN_W", "PANEL_W",
    "PANEL_W_WIDE", "PAGER_BTN_W",
    "RADIUS_SM", "RADIUS_MD", "RADIUS_LG",
    "CANVAS_BG", "GROUP_BORDER", "GROUP_BG",
    "field_width", "stepper_chrome",
]
