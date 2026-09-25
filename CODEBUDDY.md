# DeepMaven — 项目约定（AI 协作入口）

基于 **PySide6 6.11 + QFluentWidgets 1.11** 的深度学习缺陷检测桌面系统。
MVVM 架构：`models/`（数据）→ `services/`（业务/训练）→ `viewmodels/`（状态）
→ `views/`（界面）；界面设计与实施细节见 `开发文档.md`。

- 运行环境：`.venv`（Python 3.14），依赖见 `requirements.txt`
- 启动：`.venv\Scripts\python.exe main.py`
- 离屏自检：`$env:QT_QPA_PLATFORM="offscreen"` 后运行脚本，不得只靠肉眼判断

## 界面硬约束（不可违反）

1. **禁止单行塞满控件**——工具条 / 操作栏必须用 `FlowLayout` + `ToolGroup`
   分组，宽度不足时整组换行，绝不允许控件互相重叠。
2. **数值输入框必须定宽**——用 `tokens.field_width()`，禁止 `QFormLayout`
   把参数框拉伸填满整行。
3. **参数输入框滚轮不改值**——一律 `SafeSpinBox` / `SafeDoubleSpinBox`；
   不得移除 `app.py` 中的全局 `WheelGuard`。仅显示类控件可豁免。
4. **尺寸只用设计令牌**——间距 / 宽度 / 圆角一律取 `src/views/ui/tokens.py`，
   禁止写死魔法数字。
5. **不许写显式最小/固定高度**——高度需求只能用提示值（`sizeHint` /
   `minimumSizeHint` / `heightForWidth`）表达，可变行数工具条用
   `FlowContainer`。`setMinimumHeight` 会**击穿页面的 `Ignored` 策略**，
   把窗口最小高度顶到超过屏幕、标题栏被顶出可视区（§4.4，反复复发过）。
   窗口最小尺寸必须 ≤ 屏幕可用区域。
6. **不要写死明暗主题色**——常规控件交给 QFluentWidgets 主题，自绘控件用
   tokens 的中性色。

## 动界面代码前

先读 `.codebuddy/rules/ui-design/RULE.mdc`（完整规范 9 节 + 自检方法），
参考实现：`views/annotate_tab.py::_build_toolbar`、
`views/train_tab.py::_build_param_card`、基座 `views/ui/`。

## 约定

- 界面文案只用于标识与反馈，不写功能说明（见 `开发文档.md` §4.3）。
- 任何页面的内容都不得顶大窗口最小尺寸（见 §4.4）；
  离屏自检必须包含 §4.4 回归断言（窗口最小高度 ≤ 屏幕可用高度、
  `FlowContainer.minimumHeight() == 0`）。
- 界面改动完成后做离屏自检，并在 `开发文档.md` 增补对应章节。
