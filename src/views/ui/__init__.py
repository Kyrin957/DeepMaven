"""UI 设计系统基座。

集中提供界面开发所需的**布局原语**与**令牌**，让各页面的界面代码不再各自
写死魔法数字，也不再出现"一排控件放不下就重叠"的问题。

导出
----
* ``tokens``      设计令牌（间距 / 宽度 / 内边距）
* ``FlowLayout``  可换行流式布局（解决控件重叠）
* ``WheelGuard``  全局滚轮防护（参数框不再被滚轮误改）
* ``SafeSpinBox`` / ``SafeDoubleSpinBox``  滚轮安全的输入框
* ``ParamGroup`` / ``ToolGroup`` / ``FieldRow``  容器化布局组件

界面开发约定见 `.codebuddy/rules/ui-design.mdc`。
"""

from src.views.ui import tokens
from src.views.ui.containers import FieldRow, ParamGroup, ToolGroup, tool_separator
from src.views.ui.flow_layout import FlowContainer, FlowLayout
from src.views.ui.wheel_guard import (
    ALLOW_WHEEL_ATTR,
    SafeDoubleSpinBox,
    SafeSpinBox,
    WheelGuard,
)

__all__ = [
    "tokens",
    "FlowLayout",
    "FlowContainer",
    "WheelGuard",
    "SafeSpinBox",
    "SafeDoubleSpinBox",
    "ALLOW_WHEEL_ATTR",
    "ParamGroup",
    "ToolGroup",
    "FieldRow",
    "tool_separator",
]
