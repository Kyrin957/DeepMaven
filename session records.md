# Session Records

> 本文件记录每次开发会话的标题、目标与执行结果，便于后续查询回溯。

---

## Session 01 — 项目开发环境搭建

**日期：** 2026-08-10

**目标：**
根据《开发文档.md》完成 DeepMaven（基于 PySide6 的深度学习缺陷检测系统）开发环境搭建：Python 3.14、PySide6 6.11、QFluentWidgets 组件库、OpenCV，并安装适用 YOLOv26 的 Ultralytics 与 PyTorch 框架，生成 requirements 文档。

**执行过程：**
1. 定位并确认 Python 3.14.4（含 pip 26.0.1）
2. 在项目根目录创建虚拟环境 `.venv`
3. 升级 pip 至 26.2.1
4. 安装 GUI 依赖：PySide6、QFluentWidgets、OpenCV
5. 安装深度学习依赖：PyTorch、Ultralytics
6. 补充安装 openpyxl（用于检测结果 Excel 导出）
7. 验证全部组件导入及 YOLOv26 支持

**执行结果 ✅：**

| 组件 | 版本 | 状态 |
|------|------|------|
| Python | 3.14.4 | ✅ |
| PySide6 / Qt | 6.11.1 | ✅ |
| QFluentWidgets | 1.11.3 | ✅ |
| OpenCV | 5.0.0 | ✅ |
| NumPy | 2.5.2 | ✅ |
| PyTorch | 2.13.0 (CPU) | ✅ |
| Ultralytics | 8.4.117 | ✅ 支持 YOLOv26 |
| Matplotlib | 3.11.1 | ✅ |
| OpenPyXL | 3.1.5 | ✅ |

**生成的文档/文件：**
- `requirements.txt` — CPU 版依赖清单（含安装说明）
- `requirements-gpu.txt` — CUDA 版 PyTorch 依赖（可选 GPU 加速）
- `.gitignore` — 忽略 `.venv/`、`runs/`、`datasets/` 等产物

**关键结论与注意事项：**
- YOLOv26 支持已确认：`yolo26n.yaml` 模型成功加载，任务类型为 detect
- PyTorch 当前为 **CPU 版**（`2.13.0+cpu`），CUDA 为可选；有 NVIDIA GPU 时按 `requirements-gpu.txt` 说明切换
- QFluentWidgets 安装时自动带入 `PyQt5` / `PyQt5-Frameless-Window` 作为传递依赖（无边框窗口支持），运行时走 PySide6 后端，可忽略但不可强行卸载

**下一步计划：**
按 MVVM 架构搭建项目骨架（`src/models`、`src/viewmodels`、`src/views`、`src/services`），实现主窗口与左侧导航栏。

---

## Session 02 — 项目骨架搭建与主窗口实现

**日期：** 2026-08-11

**目标：**
根据《开发文档.md》按 MVVM 架构搭建 DeepMaven 项目骨架，基于 PySide6 + QFluentWidgets 实现主窗口、左侧导航栏、顶部菜单栏、底部状态栏与六个导航页 UI。

**执行过程：**
1. 按 MVVM 搭建 `src/` 目录：`models`、`viewmodels`、`views`、`services`、`utils`
2. 实现六页导航 UI：项目管理、数据管理、模型管理、模型训练、模型评估、模型导出
3. 实现主窗口（顶部菜单栏 + Fluent 主窗口 + 底部状态栏）
4. 实现应用入口 `main.py` / `src/app.py`，串接主题、配置与日志
5. 无头模式（offscreen）逐项验证：导入、构造、信号、退出

**⚠️ 关键修复 —— QFluentWidgets 绑定后端错误:**
- Session 01 安装的 `PyQt-Fluent-Widgets` 是 **PyQt5 后端**，其 `qfluentwidgets` 源码硬编码 `from PyQt5.Qt*` 导入，与我们的 PySide6 应用混合绑定导致**任何组件构造即崩溃**（静默退出码 127，无 traceback）。
- 修复：卸载 `PyQt-Fluent-Widgets`，改装 **`PySide6-Fluent-Widgets==1.11.3`**（同版本号，共用 `qfluentwidgets` 模块名，二者不可共存）。
- 已同步修正 `requirements.txt` 并更新错误注释。

**其它实现要点：**
- `FluentWindow` 在 QFluentWidgets 中为非 `QMainWindow` 的自绘无边框 `QWidget`（自带标题栏与左侧导航），因此 `MainWindow` 采用顶层容器 `QWidget`，纵向排布：`QMenuBar（顶）→ FluentWindow（中，含左侧导航+右侧导航页）→ 状态栏（底）`。
- `addSubInterface` 要求每个页面设置非空 ASCII `objectName`（如 `projectTab`）。
- 导航跳转方法为 `navigationInterface.setCurrentItem(...)`（旧版 `navigateTo` 已移除）。
- `InfoBar.success/error/...` 为 classmethod，须用 `getattr(InfoBar, level)(...)` 调用；`InfoBarPosition` 无 `CENTER`，用 `TOP`。
- `models/` 中 `datetime.now()` 的 `_now_iso` 须定义在 dataclass 之前（`field(default_factory=...)` 在类定义期求值）。

**验证结果 ✅：**
- offscreen 构造 `MainWindow` 成功，六页全部注册进导航
- 菜单栏（文件/视图/帮助）、状态栏就绪
- 各 ViewModel 的 `create_project`、`import_images`、`select_variant`、`start`、`run_detection`、`export`、主题切换、InfoBar 提示均通过
- 应用入口完整启动并正常退出（exit 0）

**产物：**
- `src/` 完整 MVVM 骨架（models / viewmodels / views / services / utils）
- `main.py`、`src/app.py` 应用入口
- 修正后的 `requirements.txt`（PySide6-Fluent-Widgets）

**下一步计划：**
接入各模块真实业务逻辑（数据集导入扫描、SQLite 项目持久化完善、训练线程 QThread 接入 YOLOService、模型导出执行）。

---