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

## Session 03 — 单文件项目格式与服务层落地

**日期：** 2026-08-17

> 说明：本条依据当前代码实现与文件时间线回溯补录（该次会话当时未记录，日期为文件最后修改时间）。

**目标：**
将项目持久化方案由 SQLite 调整为自研单文件 `.mprj` 容器，并落地 models / services / viewmodels / views 四层的完整骨架。

**执行结果 ✅：**

| 层 | 文件 | 内容 |
|----|------|------|
| models | `project.py` | `Project` 扩展为完整数据容器（元信息 + 子模块配置 + 文件索引） |
| models | `project_file.py` | 新增 `ProjectFile`（归档条目索引）、`ClassDef`（缺陷类别） |
| models | `training.py` | 新增 `EvaluationConfig`、`ExportConfig` |
| models | `dataset.py` | 补齐数据集配置与统计字段 |
| services | `project_format.py` | 单文件容器编解码（`.mprj`） |
| services | `project_service.py` | 项目创建/打开/保存 + 归档文件增删读 + 释放为目录 + 最近项目 |
| utils | `constants.py` | 补入模型变体、任务类型、导出格式、数据集目录等常量 |
| views | `base_page.py`、六个导航页、`main_window.py` | 统一可滚动卡片布局；主窗根布局重构为「菜单栏 → 页面 → 状态栏」 |
| viewmodels | `project_vm.py` | 接通 `ProjectService`，管理当前项目与最近项目 |

**关键设计决策 —— 存储方案变更：**
- 原《开发文档.md》§2.1 的 SQLite 方案调整为**自研单文件 `.mprj` 容器**（格式见《开发文档.md》§6.4）。
- 格式要点：魔数 `DMJPRJ` + XOR 流式加密的 JSON 清单 + 条目名混淆的 ZIP 载荷 + 逐文件 SHA256 完整性校验 + 格式版本守卫。
- 数据模型不再依赖 SQLite；项目信息、数据集、训练记录与模型产物统一打包进 `.mprj`。

**遗留问题 ⏳（当时仍为存根）：**
- `DatasetService.split_dataset` 仅创建目录，文件复制未实现；`DatasetViewModel.apply_split` 为提示占位
- `TrainViewModel.start` 未接入 QThread 与 `YOLOService.train`
- `EvaluateViewModel.run_detection` 返回模拟结果，报告导出未实现
- `ExportViewModel.export` 未对接已就绪的 `ExportService`
- 数据管理页缩略图预览、训练页损失曲线未实现

**下一步计划：**
接入各模块真实业务逻辑（数据集划分落盘与归档、训练线程 QThread 接入 YOLOService、模型评估推理、模型导出执行）。

---

## Session 04 — 文档一致性整理

**日期：** 2026-09-17

**目标：**
整理项目文档与代码实现的一致性：统一《开发文档.md》中的存储方案表述、补齐 `session records.md` 缺失的会话记录、补写空的 `README.md`。

**执行过程：**
1. 通读全项目（models / services / viewmodels / views / utils），梳理各模块实现完成度
2. 将《开发文档.md》§2.1、§5 中的 SQLite 方案更新为单文件 `.mprj` 容器，并新增 §6.4 描述格式
3. 回溯补录 Session 03
4. 补写 `README.md`
5. 修正《开发文档.md》其余与实现不符处：§3.1 目录树补全、§4.1 导航顺序（评估→导出）、§5 阶段编号（重复的「第五阶段」改为「第六阶段」）、§5 主窗口类型（QMainWindow→FluentWindow）、§2.1 与 §4.1 导出格式补入 TorchScript
6. 进一步统一开发方案：§4.1 模型管理补入 YOLO26、§3.2 Service层职责补入项目存储、§5 阶段周次调整（评估第9周、导出第10-11周，使周次与阶段顺序一致）

**执行结果 ✅：**
- 《开发文档.md》存储方案已统一为 `.mprj`
- `session records.md` 补录至 Session 04
- `README.md` 补写完成
- 《开发文档.md》其余 8 处实现不一致已修正

**下一步计划：**
按确认的优先级推进功能闭环（数据管理 → 模型训练 → 模型评估 → 模型导出）。

---

## Session 05 — 数据管理闭环（划分落盘 + data.yaml + 项目归档）

**日期：** 2026-09-17

**目标：**
打通数据管理第一段闭环：数据集划分落盘为 YOLO 结构、生成 `data.yaml`，并批量归档进 `.mprj` 项目。

**执行过程：**
1. `DatasetService`：实现 `split_dataset` 真实复制（图片 + 同名标签 → `images`/`labels` 的 train/val/test）、`build_label_index` 同名配对、`load_class_names` 类别名解析、`write_data_yaml` 支持 `test`
2. `ProjectService`：新增 `add_files` 批量归档接口（单次重打包，同路径替换），避免逐个 `add_file` 的 O(n²)
3. `Dataset` 模型：新增 `output_path` / `data_yaml` 字段
4. `DatasetViewModel`：实现 `apply_split`（落盘 → `data.yaml` → 归档 → 回填 `training.data_yaml`），新增 `datasetReady` 信号
5. `ProjectViewModel`：新增 `service` 属性与 `notify_changed`
6. `MainWindow` / `DataTab`：注入项目服务、`datasetReady` → 训练页 `data_yaml`、统计信息增加「数据集配置」

**验证结果 ✅（offscreen 端到端）：**

| 检查项 | 结果 |
|--------|------|
| 划分比例 0.6/0.2/0.2（5 图） | train 3 / val 1 / test 1，标签全部配对 |
| data.yaml | 含 `path` / `train` / `val` / `test` / `names`，类别名由来源 data.yaml 解析为「划痕/异物」 |
| 重新打开 `.mprj` | 归档 11 个文件（image 5 / label 5 / config 1），可回读 |
| 训练配置回填 | `training.data_yaml` 已随归档一并持久化 |

**产物：**
- 划分结果落盘于项目同级「`<项目名>_dataset/`」
- 项目内归档路径：`dataset/images/{train,val,test}/`、`dataset/labels/{train,val,test}/`、`dataset/data.yaml`

**说明与遗留 ⏳：**
- 图片字节会一并存入 `.mprj`，每次保存整体重打包，大数据集下保存耗时增加（后续可优化为增量/分卷存储）
- `data.yaml` 的 `path` 为落盘工作目录绝对路径；若后续训练走 `extract_to_dir` 释放归档，需重写该字段

**下一步计划：**
模型训练闭环（QThread 训练线程接入 `YOLOService.train`，进度/指标/日志信号打通）。

---

## Session 06 — 数据与训练模块重新规划

**日期：** 2026-09-17

**目标：**
对照 Halcon DLT 的完整链路，重新细化数据与训练模块的功能规划，并完成开源资源选型（含协议评估）。

**执行过程：**
1. 对标 Halcon DLT 九环节链路（导入/采集 → 类别编辑 → 标注 → 数据检查 → 划分 → 增强 → 训练 → 评估 → 导出），定位缺口：**类别管理、图像标注、数据质检、数据增强**
2. 调研并核实 2026 年主流开源资源的现状与协议
3. 就四个关键决策与用户确认：
   - 标注方案：**内置自研（QGraphicsView）+ 标注格式互通**
   - 半自动预标注：**YOLO 预标注 + SAM 2 / MobileSAM 细化**
   - 异常检测：**引入 Anomalib**
   - 分发方式：**内部使用 / 本项目自身开源** → Ultralytics AGPL-3.0 无碍
4. 将规划写入《开发文档.md》

**《开发文档.md》变更：**

| 位置 | 变更 |
|------|------|
| §1.2 | 核心功能模块补齐 图像标注 / 模型评估 / 异常检测 |
| §2.1 | 新增 数据增强(Albumentations) / 分层划分(scikit-learn) / 图像标注(自研) / 半自动标注(SAM 2) / 图片去重(imagededup) / 异常检测(Anomalib) |
| §2.4 | 新增「开源资源选型与协议」，含 AGPL/GPL 风险结论与依赖安装节奏 |
| §3.3 | 新增「数据与训练模块目标结构」，列出规划新增的 views/widgets/services/models |
| §4.1 | 数据管理改为子页容器（导入/类别/质检/划分/增强），新增「图像标注」导航页 |
| §5 | 阶段二拆为 A/B/C/D，新增第七阶段（异常检测）与第八阶段（测试与打包），共八阶段十四周 |
| §7 | 补充标注器工作量、重依赖、误检、协议传染、AGPL 五项风险 |

**关键结论：**
- 现有「数据管理闭环」（导入统计 + 基础划分 + data.yaml + 归档）保留，作为阶段二A/D 的基础，不需要推倒重来
- X-AnyLabeling（GPL-3.0）与 Label Studio（AGPL-3.0）**禁止链接进代码**，只能以独立进程 + 标注文件交换方式配合
- FiftyOne 底层依赖 MongoDB，**不嵌入**，仅借鉴其质检思路

**下一步计划：**
按阶段二A 开始实现：图片导入与去重、类别管理、数据质检。

---

## Session 07 — 阶段二A：数据导入增强与类别管理

**日期：** 2026-09-17

**目标：**
实现《开发文档.md》阶段二A：图片导入增强（多选 / 拖拽 / 内容去重）、类别管理、数据质检，并把数据管理页重构为子页容器。

**执行过程：**
1. 模型层：`Dataset` 新增 `duplicate_count`；`Project` 新增 `class_names` / `next_class_id` / `find_class`
2. 新增 `CategoryService`：类别 CRUD、排序、颜色、classes.txt / data.yaml 导入导出
3. 新增 `QualityService`：dHash 感知哈希查重、Laplacian 清晰度、平均亮度曝光、YOLO 标签校验（空 / 越界 / 缺失 / 孤立）
4. `DatasetService`：新增 `file_sha256`、`dedupe_by_content`、`summarize_files`；`summarize` 支持去重统计
5. 新增 `CategoryViewModel`（变更即时持久化到 `.mprj`）；`DatasetViewModel` 增加 `import_files`、去重提示与质检辅助接口
6. 视图：`data_tab.py` 重构为子页容器，新增 `data_pages.py`（导入 / 类别 / 质检 / 划分四个子页）
7. 主窗口接线：新增 `CategoryViewModel`，`DataTab` 传入类别 VM

**验证结果 ✅（offscreen 端到端）：**

| 检查项 | 结果 |
|--------|------|
| 导入去重 | 5 张图（含 1 张内容重复）→ 4 张，重复计数 1 |
| 多文件导入 | 3 个文件 → 2 张（去重 1），来源目录正确识别 |
| 质检·重复 | 正确识别内容完全相同的图片组 |
| 质检·模糊 | 纯色图 Laplacian 方差 0 → 判定模糊 |
| 质检·曝光 | 平均亮度 5 → 判定过暗 |
| 质检·标签 | 缺失 / 空 / 坐标越界三类问题全部命中 |
| 类别 CRUD | 新增 → 上移 → 删除后 id 重排为 0..n-1 正确 |
| 类别导入导出 | classes.txt 往返一致 |
| 项目持久化 | 重开 `.mprj` 后类别名称与颜色完整保留 |
| UI 联动 | 新建项目后类别表自动刷新；新增类别即时写入表格 |

**已知限制 ⏳：**
- 感知哈希对「纯色 / 平坦」图像会误判为重复（dHash 全 0），真实照片场景影响很小
- 质检为同步执行，大数据集会在 UI 线程内耗时，后续随训练多线程方案一并异步化
- 「相机采集」「类别分布图」「尺寸异常检测」暂未实现

**下一步计划：**
阶段二B：图像标注（QGraphicsView 标注画布 + YOLO/COCO/VOC 格式互通 + 标注质检）。

---

## Session 08 — 阶段二B：图像标注

**日期：** 2026-09-17

**目标：** 实现内置标注器与标注格式互通。

**执行过程：**
1. `models/annotation.py`：归一化坐标的 `Annotation`（框 / 多边形）与 `ImageAnnotation`
2. `services/annotation_service.py`：YOLO txt 读写、COCO json 与 Pascal VOC 导入导出、图片尺寸读取
3. `viewmodels/annotate_vm.py`：图片列表、当前标注、切换自动保存、格式互通
4. `views/widgets/annotation_canvas.py`：QGraphicsView 画布（矩形 / 多边形绘制、选择、删除、滚轮缩放、中键平移）
5. `views/widgets/thumbnail_grid.py`：缩略图导航与已标注着色
6. `views/annotate_tab.py`：标注页装配；主窗口新增「图像标注」导航页

**验证结果 ✅：**

| 检查项 | 结果 |
|--------|------|
| YOLO 往返 | 框（5 字段）与多边形（6 字段）写出后可正确读回 |
| COCO 往返 | 框与多边形类型均保留 |
| VOC 往返 | 仅 bbox（VOC 无分割语义），符合预期 |
| 保存 / 自动保存 | 切换图片时自动落盘，已标注计数与标记正确 |
| 导出 YOLO | 输出全部已标注 txt |
| UI | 主窗口构建正常，画布模式可切换 |

**下一步计划：** 阶段二C：半自动预标注。

---

## Session 09 — 阶段二C：半自动预标注

**日期：** 2026-09-17

**目标：** 用 YOLO 预标注 + SAM 细大幅降低标注工作量。

**执行过程：**
1. `services/autolabel_service.py`：YOLO 批量预标注（`boxes_to_annotations` 归一化）、SAM 提示分割（`segment`）、掩码转多边形（`mask_to_polygons`）
2. `utils/workers.py`：通用后台任务线程 `FunctionWorker`
3. `AnnotateViewModel`：新增 `preannotate`（仅填充未标注图片，不覆盖人工标注）与 `refine_with_sam`（框 → 多边形），以及 task 系列信号
4. `AnnotateTab`：新增预标注工具条（检测权重、置信度、进度条、任务状态）

**验证结果 ✅：**

| 检查项 | 结果 |
|--------|------|
| 掩码转多边形 | 矩形掩码 → 4 顶点，坐标归一化正确 |
| 预标注（桩） | 后台任务写入 3 / 3 张，标记全部为已标注 |
| SAM 细化（桩） | 框被替换为 4 顶点多边形并落盘为 YOLO 分割格式 |
| UI | 预标注工具条就绪 |

**说明：** SAM 权重（`mobile_sam.pt`）由 Ultralytics 首次使用时自动下载；真实模型推理未在离线验证中执行。

**下一步计划：** 阶段二D。

---

## Session 10 — 阶段二D：分层划分与数据增强

**日期：** 2026-09-17

**目标：** 产出可直接训练的高质量数据集。

**执行过程：**
1. `DatasetService`：新增 `_label_classes`、`split_members`（按类别组合分层抽样），`split_dataset` 支持 `stratified`
2. `Dataset` 模型：新增 `stratified` 字段
3. `services/augment_service.py`：Albumentations 流水线、检测框与多边形同步变换、预览、离线批量增强
4. `DatasetViewModel`：新增 `set_stratified`、`augment_preview`、`augment_apply`（后台任务 + 项目归档）
5. 数据管理页新增「增强」子页；划分页新增「分层划分」开关
6. 安装 Albumentations 2.0.8 并写入 `requirements.txt`

**验证结果 ✅：**

| 检查项 | 结果 |
|--------|------|
| 分层划分 | 分层=True 时 val/test 均含类别 0、1；分层=False 时 val 仅含类别 0（小类别被随机分走） |
| 落盘 | 20 张 → train 12 / val 4 / test 4，标签全部配对 |
| 增强预览 | 预览图尺寸正确 |
| 离线增强 | 20 图 × 2 份 = 40 图 + 40 标签，归档 80 条（`augment/images`、`augment/labels`） |

**修正记录 ⚠️：** 多边形关键点必须使用**绝对像素坐标**传入 Albumentations（初版误传归一化坐标，导致多边形塌缩到图像角落）。

**下一步计划：** 阶段三 + 阶段四。

---

## Session 11 — 阶段三与阶段四：模型信息探测与训练闭环

**日期：** 2026-09-17

**目标：** 完成预训练模型信息探测，并打通训练闭环。

**执行过程：**
1. `YOLOService.info()`：补充参数量与网络层数
2. `ModelViewModel`：权重导入 / 官方权重加载后**后台探测**模型信息；`ModelTab` 展示任务、类别、参数量、层数
3. `services/train_worker.py`：训练子进程入口，按行输出 JSON 事件（phase / epoch / done / error）
4. `services/train_service.py`：基于 QProcess 的子进程管理（启动、输出解析、强制终止、汇总）
5. `TrainViewModel`：改写为子进程驱动，处理进度 / 指标 / 日志并登记训练产物
6. `TrainTab`：新增「数据集配置」行与「Epoch」显示，移除占位说明文字

**验证结果 ✅：**

| 检查项 | 结果 |
|--------|------|
| 子进程模块自检 | `python -m src.services.train_worker --help` 返回 0 |
| 参数构造 | 全部参数与 `--resume` 正确传递 |
| 事件解析 | JSON 事件与普通日志分流正确 |
| 训练全流程（假子进程） | 未设数据集时拒绝启动；启动 → running；进度 0 / 0.5 / 1.0；每轮指标回传；结束 finished |
| 产物归档 | `runs/best.pt`、`runs/last.pt`、`runs/results.csv` 写入 `.mprj` |
| 模型信息探测 | 后台返回任务 / 类别 / 参数量 / 层数 |
| UI 联动 | 训练页「数据集配置」可被数据划分结果回填 |

**说明：** 真实训练需实际数据与权重，离线验证以「假子进程」覆盖进程管理与事件协议；断点续训（`resume`）已支持，**暂停**未实现。

**下一步计划：**
阶段五 模型评估 → 阶段六 模型导出 → 阶段七 异常检测（Anomalib）→ 阶段八 测试与打包。

---

## Session 12 — 阶段五、六、七：模型评估、模型导出与异常检测

**日期：** 2026-09-17

**目标：** 完成模型评估、模型导出与基于 Anomalib 的异常检测。

**执行过程：**
1. `services/inference_service.py`：图片 / 视频检测推理、结果记录解析、标注图绘制
2. `services/report_service.py`：检测报告导出（CSV `utf-8-sig` / Excel）
3. `EvaluateViewModel` 重写：图片 / 视频 / 相机三种输入、后台任务、报告导出；`EvaluationConfig` 增加 `source_path`、`anomaly_model`
4. `EvaluateTab` 重写：输入配置、结果图 + 结果表格；权重为 `.ckpt` 时条件显示「异常模型」行
5. `ExportService`：导出产物自动移动到指定 `output_dir`；`ExportViewModel` / `ExportTab` 重写（imgsz、ONNX Opset、动态尺寸、图精简）
6. `services/anomalib_service.py` + `services/anomaly_worker.py`：异常检测训练与推理（子进程 + JSON 事件流）
7. `TrainingConfig` 增加 `anomaly_*` 参数；`TASK_TYPES` 增加 anomaly；`TrainService` 参数分支；`TrainTab` 任务类型联动（模型列表切换 + 参数行显隐）

**⚠️ 修复的重要 Bug —— qfluentwidgets ComboBox 用法：**
`ComboBox.addItem(text, icon=None, userData=None)`，而代码中一直写作 `addItem(text, key)`，
导致 key 被当成 icon 传入、`currentData()` 恒为 `None`。影响 8 处：项目创建（模型类型）、
训练页（任务类型 / 模型）、模型页（变体）、导出页（格式）、评估页（输入源）、标注页（导出格式 / 类别选择）。
已全部改为 `addItem(text, userData=key)` 并验证。

**验证结果 ✅：**

| 检查项 | 结果 |
|--------|------|
| 检测记录解析 | 类别名、置信度、坐标与宽高正确 |
| 报告导出 | CSV（3 行：表头 + 2 条）与 Excel 生成正常 |
| 评估 VM | 图片分支 2 条记录、视频分支 30 帧 / 4 条记录、报告可导出 |
| 导出 VM | `exportFinished` 返回导出路径 |
| ExportService 落位 | 产物移动到指定目录，源文件已移走 |
| Anomalib 可用性 | `is_available=True`，5 个推荐模型 |
| 离线回退 | 无网络时预训练骨干加载失败 → 自动随机初始化（`Padim` 构建成功） |
| 数据模块 | `Folder` 构建并 `setup()` 成功 |
| 异常子进程 | `python -m src.services.anomaly_worker --help` 返回 0 |
| 参数分支 | 异常走 `anomaly_worker`（含 `--no-pretrained`），检测走 `train_worker` |
| 校验分支 | 目录不存在时拒绝启动并给出提示；未设目录时 VM 拒绝启动 |
| 异常推理分支 | `.ckpt` 权重自动走 Anomalib；`.ckpt`/`.pt` 切换时评估页条件行正确显隐 |
| 下拉数据 | 7 处下拉 `currentData()` 全部返回正确 key（detect / yolo11n / pt / image / coco / anomaly 等） |
| 全应用自检 | 7 个导航页 + 5 个数据子页构建正常 |

**说明与待补 ⏳：**
- 真实推理 / 训练需实际权重与数据，离线验证以桩函数覆盖记录解析、报告、流程与协议
- Anomalib 骨干权重默认从 HuggingFace 下载；**离线环境**可关闭「预训练权重」选项
- 待补：检测结果图片导出、异常检测导出 ONNX、损失曲线与 GPU 显存监控、相机采集、类别分布图

**下一步计划：** 阶段八 测试与打包（PyInstaller / pyside6-deploy）。

---

## Session 13 — 果汁瓶三分类数据集全流程实测

**日期：** 2026-09-18

**目标：**
用真实的 Halcon 果汁瓶数据集跑通「创建项目 → 数据导入 → 标注 → 模型选择 → 训练 → 验证」全流程，检验实现完备性并修复暴露的问题。

**数据集：**
`D:\Project\Deep Maven\Anomaly\juice_bottle` —— good 250 / logical_anomaly 70 / structural_anomaly 70，共 390 张 256×512 RGB PNG，**无标注文件**（纯分类数据）。

**实测中发现并修复的 7 个问题：**

| # | 问题 | 影响 | 修复 |
|---|------|------|------|
| 1 | `device="auto"` 不是 Ultralytics 可接受的值 | **预标注与训练直接失败**（`Invalid CUDA 'device=auto' requested`） | 新增 `utils/device.py::normalize_device()`，在预标注 / 推理 / 训练参数三处归一化（auto→""、cuda:0→"0"） |
| 2 | 类别只从**标签文件**推导，分类数据没有标签 | 导入后类别数恒为 0 | `DatasetService.child_class_dirs()`：无标签时从子目录名推导类别 |
| 3 | 划分只输出检测结构 `images/ + labels/` | 分类训练无法使用（需要 `train/<类别>/`） | `split_dataset` / `split_members` 新增 `layout` 参数；`DatasetViewModel` 按 `project.model_type` 自动选择 |
| 4 | 训练页模型列表不区分任务后缀 | 分类任务会去训练检测权重 `yolo11n.pt` | 新增 `TASK_MODEL_SUFFIX`，拼出 `yolo11n-cls` / `yolo11n-seg` |
| 5 | 批量推理时 `result.path` 返回 `image0.jpg` 序号名 | **半自动预标注结果无法与图片对应，恒写入 0 张** | `AutoLabelService.detect`、`InferenceService.classify_images` 改为**按输入顺序**回填文件名 |
| 6 | 指标固定显示 mAP50 / Precision / Recall | 分类任务指标恒为 0，无法判断训练效果 | `TrainViewModel` 按任务类型映射指标（分类 top1/top5、异常 AUROC、检测 mAP），UI 标签动态化 |
| 7 | 分类 `loss_items` 是 dict（键为字符串） | loss 兜底逻辑抛 `TypeError` | 统一只累加数值项 |

**全流程实测结果 ✅：**

| 环节 | 结果 |
|------|------|
| 创建项目 | `果汁瓶三分类.mprj`（classify） |
| 数据导入 | 390 张 / 3 类 / 重复 0 |
| 数据划分 | train 273 / val 78 / test 39，**按类别严格分层**（175/50/25、49/14/7、49/14/7） |
| 项目归档 | 390 个图片条目；训练后追加 best.pt / last.pt / results.csv |
| 标注回归 | 矩形 `0 0.3 0.4 0.4 0.4`、多边形 `1 0.6 0.1 0.9 0.2 0.8 0.5`；COCO 导出→回读、VOC 导出通过 |
| 半自动预标注 | 真实推理写入 5/5 张，检出 COCO `bottle`(id 39) 框 |
| 模型选择 | YOLO11n-cls（2.81M 参数） |
| 训练 | 15 epochs / batch 16 / imgsz 224 / CPU，**val Top1 = 0.808**，约 2 分钟完成 |
| 产物归档 | `runs/best.pt`、`runs/last.pt`、`runs/results.csv` 自动写入 `.mprj` |
| 验证（留出测试集） | **33/39 = 84.62%**；good 96.0%、logical_anomaly 100%、structural_anomaly 28.6% |
| 单图评估 | `good` 置信度 0.9214；CSV / Excel 报告导出成功 |

**测试脚本（仓库外，便于复跑）：** `D:\Project\Deep Maven\Anomaly\_e2e\` 下 `step1_recon / step2_import_split / step3_annotate / step4_train / step5_evaluate`。

**观察与遗留 ⏳：**
- 训练页**缺少 imgsz 控件**（开发文档已列为训练参数），实测需在代码中设 224；分类任务若沿用默认 640 会显著拖慢训练
- 本机 Ultralytics 内置 curl 下载权重失败（TLS curl 35），需手动放置权重；GitHub / HuggingFace 直连正常，属环境问题
- `structural_anomaly` 仅 2/7 正确，是数据难度 + 仅 15 轮训练的共同结果
- 异常检测（Anomalib）链路本次未跑（数据为三分类，非 `normal/abnormal` 结构）

**下一步计划：** 补训练页 imgsz 控件；可选补跑异常检测链路（good→normal，两类异常→abnormal）。

---

## Session 14 — 修复「打开已有项目后界面全空」

**日期：** 2026-09-18

**问题现象：**
用程序打开 Session 13 实测生成的 `果汁瓶三分类.mprj`，**所有环节的图片、数据、状态全部为空**。

**根因（两个，缺一不可）：**

1. **数据从未写入项目**：`DatasetViewModel` 的数据集统计只存在于 VM 内存中，`import_images` / `apply_split` 都没有回写 `project.dataset`。实测产出的 `.mprj` 里 `dataset` 是空对象（`image_count=0, source_path=''`），`training` 也仍是默认的 `detect`。
   另外 `apply_split` 在 `add_files(save=True)` **之后**才设置 `output_path / data_yaml / class_names`，这几个字段必然丢失。
2. **打开项目时无人恢复状态**：`projectChanged` 只连了 `ProjectTab` 与状态栏，7 个子 ViewModel 全都没有「从 project 回填」的入口。

**修复方案：**

| 层次 | 改动 |
|------|------|
| 写入 | `DatasetViewModel._set_dataset` 把数据集写进 `project.dataset`（同一对象引用）并即时保存；`apply_split` 改为先写回字段再归档；`ModelViewModel._persist` 写回 `project.model`；`Train / Evaluate / Export` 三个 VM 的配置**直接绑定**到 `project.training / evaluation / export`，改动即进入项目 |
| 恢复 | 各 VM 新增 `load_from_project(project)`；`MainWindow._on_project_loaded` 统一分发（`dataset_vm → model_vm → train_vm → evaluate_vm → export_vm`），类别页与标注页分别自行监听 `projectChanged` / `datasetChanged` |
| 类别 | 导入数据集时若项目尚无类别定义，按数据集类别自动生成 `project.classes`（分类数据的类别来自子目录名，否则类别页恒空） |
| 自包含 | `DatasetViewModel.resolved_source()`：原始来源目录不存在时，把归档图片释放到「<项目名>_files/」再用；配套新增 `ProjectContainer.get_files_bytes` 与 `ProjectService.read_files` 批量读取（单次打开归档，避免逐条解析） |
| 闭环 | 训练产物自动作为「模型评估 / 模型导出」的默认权重 |
| 界面 | `MainWindow` 改为 `ModelViewModel(project_vm, ...)`、`ExportViewModel(project_vm, ...)` |

**验证结果 ✅（offscreen 真实主窗口，脚本 step6 / step7）：**

| 页面 | 打开前 | 打开后 |
|------|--------|--------|
| 项目页 | — | 名称 / 路径 / 类型 / 创建时间 / 内嵌文件 393 |
| 数据页·导入 | `—` | 来源、390 张、标签 0、类别 3、重复 0、类别列表 |
| 数据页·划分 | `—` | 划分输出目录、数据集配置 |
| 类别页 | `[]` | 3 个类别（含 id 与颜色） |
| 标注页 | 0 张 | 390 张，默认选中第 1 张 |
| 模型页 | 默认 | YOLO11n + 自定义权重路径 |
| 训练页 | `detect` | `classify / yolo11n-cls / epochs 30 / batch 16 / imgsz 224` |
| 评估页 | 空 | 训练产出的 `best.pt` |
| 项目归档 | — | `{image: 390, model: 2, run: 1}` |

- **自包含验证**：把 `dataset.source_path` 指向不存在目录后重新加载，成功从 `.mprj` 归档释放 390 张图片到 `<项目名>_files/dataset`，数据页/标注页/类别页均正常。
- **回归**：标注（预标注写入 5/5、手工标注、COCO/VOC 互通）与验证（留出测试集 33/39 = 84.62%）与修复前完全一致；训练复跑 top1 仍为 0.808。

**下一步计划：** 补训练页 imgsz 控件；可选补跑异常检测链路。

---

## Session 15 — 修复标注页「看不到图片」

**日期：** 2026-09-18

**问题现象：**
打开 `果汁瓶三分类.mprj` 后，图像标注页**看不到图片**（右侧画布一片深灰）。

**排查过程：**
1. 先读应用日志（`data/logs/deepmaven.log`）：10:45 成功打开项目 393 个文件，**无任何报错** → 信号链路没断
2. 复现：用真实主窗口打开项目，标注 ViewModel 有 390 张、缩略图网格 `count()=390`、缩略图图标非空 → 数据层是好的
3. 发现 `grid.isVisible()=False`、viewport 仅 184×28 —— 页面未显示时不会布局
4. 改用 QFluentWidgets 官方切页接口 `switchTo()` 后，缩略图网格显示正常（viewport 170×536）
5. 继续查右侧大画布，**定位到真正原因**

**根因：**
`AnnotationCanvas.set_image()` 用 `fitInView()` 自动适配缩放，但打开项目时标注页**还不可见**，画布视口尺寸是错的（98×418），算出缩放 **0.3672**，图片只显示 **94×188 像素**；切到标注页后视口变为 **791×536**，缩放**不会重算** —— 图片以 94×188 贴在深灰画布左上角，看上去就是「没有图片」。

**修复（`src/views/widgets/annotation_canvas.py`）：**

| 改动 | 说明 |
|------|------|
| `_fit()` | 抽出适配逻辑；视口尺寸无效（≤1）时跳过 |
| `showEvent` / `resizeEvent` | 自动适配状态下重新 `_fit()`（用 `QTimer.singleShot(0, …)` 等布局完成） |
| `_auto_fit` 标志 | `set_image` 置 True；用户滚轮缩放或中键平移后置 False，不再自动干预 |
| `_pixmap_item` | 保存当前图片项引用，供重适配使用 |

**验证结果 ✅：**

| 时点 | 画布视口 | 缩放 | 图片显示尺寸 |
|------|---------|------|-------------|
| 打开项目时（标注页不可见） | 98×418 | 0.3672 | 94×188（不可见） |
| 切到标注页（修复前） | 791×536 | 0.3672 | 94×188 ❌ |
| 切到标注页（修复后） | 791×536 | **1.0391** | **266×532** ✅ |

页面可见、缩略图 390 张且网格可见、状态栏「第 1 / 390 张 · 当前标注 0 个 · 已标注 0 / 390」。

**关于「数据管理里显示的导入路径是原始图像路径」：** 非 Bug。`Dataset.source_path` 记录的是**数据集导入来源**（原始素材目录）；项目自身已归档 390 张图片副本、属自包含。标注标签默认写入 `<来源目录>/labels/`，执行「数据集划分」时会一并归档进项目。

**下一步计划：** 视需要把标注的落盘目标改为项目内部目录。

---

## Session 16 — 标注环节前端重建（对齐 Halcon DLT）

**日期：** 2026-09-18

**目标：**
用户指出「数据管理页各 tab 没有功能实现、标注页信息量太少」，要求按 Halcon DLT 的信息架构重建标注环节，至少拆为「数据/图库导入 → 图像标注 → 标注检查 → 数据/图库拆分」四个主要页面。

**技能检索（按用户要求）：**
检索 SkillHub 语义搜索接口，返回的均为通用「编程专家」类技能（相关度 < 0.05），**没有桌面 UI / 标注工具方向的可用技能**，因此自行完成实现。

**界面结构（对齐 DLT）：**

```
导航顺序：项目管理 · 图库 · 图像标注 · 标注检查 · 数据拆分
          · 模型管理 · 模型训练 · 模型评估 · 模型导出（共 9 页）
```

| 页面 | 布局 | 主要功能 |
|------|------|---------|
| **图库** | 左侧栏（数据集概览 / 标签类别 / 浏览筛选）+ 右侧缩略图网格 | 文件夹 / 多选 / **拖拽**导入、类别 CRUD、全部/已标注/未标注筛选、三档缩略图、双击进入标注页 |
| **图像标注** | 左（当前图像 + 图像列表）中（工具条 + 预标注条 + 画布）右（导航器 / 显示 / 标注对象 / 备注） | 浏览·矩形·多边形模式、类别下拉、导航器拖动平移、亮度/对比度、标注对象列表、备注、半自动预标注与 SAM 细化、COCO/VOC/YOLO 互通 |
| **标注检查** | 左侧栏（已选择图像详情 / 标签类别 / 质检）+ 右侧大卡片网格 | 缩略图复核、筛选、**一键质检**（重复/模糊/曝光/标签）后台执行 |
| **数据拆分** | 左侧栏（拆分设置 / 拆分概览）+ 右侧（类别分布 / 拆分产物 / 数据增强） | 比例与随机种子、**环形图**占比、**类别分布条形图**、不落盘预览、执行拆分、增强预览与生成 |

**新增 / 重建的组件：**

| 文件 | 说明 |
|------|------|
| `views/widgets/thumbnail_grid.py` | 重写：自定义 Delegate 绘制卡片（图片 + 文件名 + **已标注粉色角标** + 选中描边），三档尺寸，主图缓存 |
| `views/widgets/navigator.py` | 新增：整图缩略总览 + 可拖动视口框 |
| `views/widgets/charts.py` | 新增：环形占比图 / 水平条形图 / 图例（QPainter 自绘，无 Matplotlib 依赖） |
| `views/widgets/annotation_canvas.py` | 扩展：亮度/对比度（cv2 实时调整）、`viewChanged`、归一化视口读写、适应窗口 |
| `views/data_widgets.py` | 新增：数据集概览 / 标签类别 / 浏览筛选三个共用面板 |
| `views/gallery_tab.py` `annotate_tab.py` `review_tab.py` `split_tab.py` | 新增 / 重写为上述四页 |
| `services/dataset_service.py` | 新增 `preview_split()`（不落盘计算划分结果，供饼图与类别分布预览） |
| `viewmodels/dataset_vm.py` | 新增 `images()` 缓存、`label_dir()`、`annotated_flags()`、`filtered_images()`、`class_counts()`、`split_layout()`、`preview_split()`、`run_quality_check()`（后台质检）、`set_seed()`、`set_split(quiet=)` |
| `viewmodels/annotate_vm.py` | 新增 `set_image_by_path()`、备注读写（`notes/` 目录 sidecar + `noteLoaded` 信号） |
| `models/dataset.py` | 新增 `seed` 字段（划分可复现） |
| `views/main_window.py` | 导航改为 9 页；图库双击 → 切到标注页并定位 |

**移除：** `views/data_tab.py`、`views/data_pages.py`（旧数据管理页与五个子页，功能已由新四页覆盖）。

**验收结果 ✅（offscreen 真实主窗口 + 打开实际项目）：**

| 页面 | 实测 |
|------|------|
| 图库 | 标题「图库 · juice_bottle」；缩略图 390 张；概览 390/0/3/0；类别列表 3 行；筛选「共 390 张 · 已标注 0 张」 |
| 图像标注 | 图像列表 390；画布视口 657×496、缩放 0.961、图片 256×512 已适配；导航器已载入；顶部条「第 1 / 390 张 · 未标注」；类别 3 项 |
| 标注检查 | 390 张（档位 176）；已选择详情正常；提示「共 390 张，已标注 0 张（0%）」 |
| 数据拆分 | 比例 70/20/10、种子 0、分层开；预览 total 390 → train 273 / val 78 / test 39；类别分布 good 250 / logical_anomaly 70 / structural_anomaly 70；饼图 3 分片、图例 3、条形图 3 行；增强 8 参数 + 份数 |
| 导航 | 9 页全部注册成功 |
| 回归 | 标注流程（手工标注 → YOLO 落盘 → COCO/VOC 互通 → 真实预标注 5/5）保持不变 |

**下一步计划：** 按需补充标注页的框选复制/批量套用、检查页的逐张确认流程。

---

## Session 17 — 数据环节三项问题修复（标注判定 / 项目体积 / 按钮可用性）

**日期：** 2026-09-18

**用户反馈：**
1. 图片按项目数据应当已有标注，前端却显示「未标注」，怀疑标注记录与图像路径冲突
2. 观察到导入的图像库被复制到项目目录，认为不必要
3. 新页面上许多按钮「好像都没有实现」

**问题 1 — 标注判定口径错误（确认为真问题）**
分类数据集的「标注」就是图片所属类别（good / logical_anomaly / structural_anomaly），
而 `annotated_flags()` 只按「是否存在 YOLO txt」判断 —— 分类数据没有 txt，于是 390 张全部显示为未标注。

修复：`DatasetViewModel.annotated_flags()` 改为**按任务类型判定**
- 分类任务：图片位于类别目录下即为已标注（类别即标注结果）
- 检测 / 分割：存在且非空的 YOLO 标签文件（新增 `DatasetService.label_has_content`）

`AnnotateViewModel` 的判定改为委托 `DatasetViewModel`，四个页面口径统一。
结果：**已标注 390 / 390**，类别分布 good 250 / logical_anomaly 70 / structural_anomaly 70。

**问题 2 — 图像被复制两份（确认归档那份多余）**
修复前项目目录：
- `果汁瓶三分类.mprj` **63.6 MB**（归档内含 390 张图）
- `果汁瓶三分类_dataset` **58.0 MB**（划分产物，复制自原始图像库）

修复：
1. **`.mprj` 不再内嵌图像**：`apply_split` 保存前剔除 `dataset/` 前缀条目，只归档 `data.yaml` 等配置；
   项目文件只承载项目自身数据（配置 / 标注 / 模型）
2. **划分产物改用硬链接**：新增 `DatasetService._place()`，同卷优先 `os.link`，
   跨卷或失败回退 `shutil.copy2`；Ultralytics 需要的 train/val/test 结构不变，但不额外占盘

效果：`.mprj` **63.6 MB → 5.5 MB**；归档条目变为 `{model: 2, run: 1}`；
划分目录 **390 / 392 文件为硬链接**（另 2 个是 Ultralytics 生成的 `train.cache` / `val.cache`）。

**问题 3 — 按钮「点了没反应」（部分为真问题）**

| 位置 | 问题 | 修复 |
|------|------|------|
| 图库 / 检查 · 类别行 | 点击只改提示文字，并未筛选 | 实现按类别筛选（分类按目录名、检测按标签首类别），再次点击取消 |
| 图库 / 检查 · 筛选组合 | 先选「未标注」再点类别 → 交集为空，像是失效 | 选中类别时自动把状态筛选重置为「全部」 |
| 类别卡 · 换颜色 | 颜色按 `cls_id % 8` 取，重复点击不变 | 每次点击轮换下一个配色并给出提示 |
| 类别卡 · 增删改排序 | 未选中时点击无反馈 | 未选中时**禁用**相关按钮，选中后提示当前类别 |
| 拆分页 · 拆分名称 | 输入无任何效果 | 生效为划分产物目录名，并持久化到项目 |
| 拆分页 · 刷新预览 | 无反馈 | 提示「已刷新预览：共 N 张待划分」 |
| 拆分页 · 数据增强 | 分类任务点预览/生成无反应 | 明确提示「分类任务没有框/多边形标注，暂不支持离线增强」 |
| 标注页 · 适应窗口 / 重置显示 / 删除选中 / 保存备注 | 反馈弱 | 统一走 `notify()` → 全局 InfoBar + 工具条提示 |
| 各页 | 缺页面级反馈通道 | 新增 `DatasetViewModel.notify()` / `AnnotateViewModel.notify()` |
| 检查页 · 详情面板 | 进入页面时为空 | 默认选中第一张 |

**验证结果 ✅（offscreen 真实主窗口 + 实际项目）：**
- 控件连接审计：图库 / 检查页未连接控件为 **0**；标注页与拆分页的「未连接」项均为**按需读取**的输入框（类别下拉、置信度、增强参数），非缺失功能
- 功能级验证：**43 项全部通过、0 项失败**
  - 图库：筛选取值、缩略图两档切换、类别筛选 250 张、取消筛选、概览与类别卡
  - 标注：模式切换、上一张/下一张/跳转第 50 张、亮度 ±40、对比度 -30、重置、适应窗口、删除提示、备注落盘与回读、类别切换、状态条与底部进度
  - 检查：详情填充、未标注筛选、类别筛选 70 张、取消、质检结果渲染
  - 拆分：名称生效、比例 80/10/10 → train 312、随机种子、分层开关、刷新预览、增强提示、真实执行拆分

**下一步计划：** 按需补充标注页的批量复制/套用、检查页的逐张确认流程。

### 补充 — 求证「划分是否必须复制图片」

用户质疑：既然图像库位置由用户指定，划分只需按图片名绑定，为何还要复制一份。

**查证（Ultralytics 源码 + 实跑 `check_det_dataset` / `check_cls_dataset`）：**

| 任务 | 能否只用 `.txt` 图片清单 | 依据 |
|------|------------------------|------|
| **分类** | ❌ 不能 | `ClassificationDataset` 直接包装 `torchvision.datasets.ImageFolder`，实跑传入 `.txt` 报 `ValueError: Classification datasets must be a directory (data="…") not a file` |
| **检测 / 分割** | ⚠️ 清单可以被接受，但仍需 `images/` 结构 | `data.yaml` 的 `train/val` 可指向 `.txt`（实跑通过），但标签路径由 `img2label_paths()` 从图片路径推导：`sa, sb = "/images/", "/labels/"`，即把路径里的 `/images/` 换成 `/labels/`，**并不读标签清单** |

**结论**：ImageFolder / 标签推导都要求「按 Ultralytics 认的目录结构落盘」，因此目录结构是必需的；
但**数据本身不必复制** —— 已用硬链接实现零拷贝。

**实测磁盘占用（390 张 256×512 PNG）：**
```
原始图像库          : 57.9 MB
划分目录（名义）    : 58.0 MB（392 个文件）
  其中硬链接共享    : 57.9 MB   ← 与原始库共用同一份数据块
  实际新增占用      :  0.0 MB   ← 仅 Ultralytics 生成的 train.cache / val.cache（40 KB）
```

**本轮改进：**
- `_place()` 回退链完善为 **硬链接 → 软链接 → 复制**（跨卷 / 权限不足时依次降级），并新增 `_count_place()` 统计
- 划分完成的提示会显示存储方式：`390 张为链接，不额外占用磁盘` / 或提示有多少张走了复制

### 补充 — 产物目录命名统一

划分产物目录不再带项目名前缀，固定为 `dataset`（可在数据拆分页改名）：
- `constants.py` 新增 `DEFAULT_SPLIT_NAME = "dataset"` / `DEFAULT_AUGMENT_NAME = "augment"`
- `DatasetViewModel.split_name()` 默认值改为 `dataset`；增强产物目录同步由 `<项目名>_augment` 改为 `augment`
- 已迁移现有项目：`果汁瓶三分类_dataset` → `dataset`，并更新 `dataset.output_path` / `dataset.data_yaml` / `training.data_yaml` / `params.split_name`

迁移后项目目录：
```
果汁瓶三分类/
├── 果汁瓶三分类.mprj     5.5 MB
├── dataset/              划分产物（train/val/test，390 张硬链接）
├── model/best.pt         训练产出的模型
└── pretrained/           预训练起点权重
```

验收：拆分页名称显示 `dataset`、输出目录与数据集配置均指向 `...\dataset`；
划分结构 train 175/49/49、val 50/14/14、test 25/7/7；硬链接 390；图库 390 张且已标注 390。

---

## Session 18 — 项目管理页重建（对齐 Halcon DLT）

**日期：** 2026-09-18

**目标：**
参照 Halcon DLT 重新开发项目管理：左右结构（左侧新建/打开/项目信息/关闭，右侧最近项目卡片），
新建项目时选择深度学习任务类型，并由项目类型限定后续环节（标注方式、模型选择等）。

**1. 项目类型定义（`constants.PROJECT_TYPES`）**

| 类型 | 标注方式 | 训练模型后缀 | 当前支持 |
|------|---------|------------|---------|
| 分类 | 无需框选 | `-cls` | ✅ |
| 异常检测 | 无需框选 | （Anomalib） | ✅ |
| 对象检测（轴对齐矩形） | 矩形 | — | ✅ |
| 对象检测·旋转框 | 矩形 + 角度微调 | `-obb` | ✅ |
| 实例分割 | 多边形 | `-seg` | ✅ |
| 语义分割 | 多边形 | — | ⏳ 规划中（后端未提供） |
| Deep OCR | — | — | ⏳ 规划中 |

每个类型带 `annotation`（none/box/obb/polygon）、`model_suffix`、`supported`、说明与注意事项。

**2. 创建新项目对话框（`views/dialogs/new_project_dialog.py`）**
- 从现有数据集创建（可选）：选择目录并统计图片/类别数
- 深度学习方法网格（3 列）+ 右侧说明面板（说明、标注方式、训练模型、注意事项）
- 规划中的类型只展示说明、不可选中
- 项目名称（自动联动文件路径）/ 项目文件路径 + 浏览 / 项目说明 / 保存图像库路径
- 取消 · 创建项目（含路径校验：扩展名补全、重复路径拦截、目录可创建）

**3. 项目管理页重建（`views/project_tab.py`）**
- 左侧：新建项目 / 打开项目；当前（或选中）项目信息卡片 —— 名称、说明（可编辑 + 应用修改）、
  类型、项目文件、图像库路径、标签类别（带计数）、已标记图像 n/N、创建/修改时间、
  程序/文件版本、日志路径；底部「关闭项目」
- 右侧：最近的项目卡片网格 —— 封面缩略图 + 项目名；单击选中并展示其信息，**双击打开**，
  **右键菜单**：打开项目 / 打开项目所在文件夹 / 移除最近项目记录 / 删除项目文件（带二次确认）
- 封面：划分或导入数据集时用 PIL 生成 256px JPEG 并归档为 `cover.jpg`，最近项目卡片直接读取

**4. 服务层**
- `ProjectService`：`close()`、`delete_project()`、`peek_project()`（轻量读清单+封面，跳过逐文件哈希）
- `ProjectContainer.open(data, verify=True)`：新增 `verify` 参数，预览场景可跳过逐条哈希校验
- `ProjectViewModel`：`close_project()`、`delete_project()`、`update_meta()`、`project_dir()`、`dataset_source()`

**5. 项目类型驱动后续环节**
- 训练：`TrainViewModel.load_from_project` 由项目类型推导训练任务；训练页下拉自动同步并锁定
- 标注：`AnnotateViewModel.annotation_mode()` → 标注页自动切换工具（无框选 / 矩形 / 多边形），
  旋转框类型的项目显示「旋转选中」控件
- `AnnotationCanvas.rotate_selected()`：把选中矩形绕中心旋转（矩形自动转四点多边形 ——
  这正是 YOLO 旋转框 OBB 的标注格式，因此无需额外数据格式支持），新增 `annotationChanged` 信号

**6. 修复一个真 Bug（备注文件污染数据集扫描）**
标注页的图片备注原本存为 `<数据集来源>/notes/*.txt`，被 `scan_labels` 当成 YOLO 标签读取，
备注文字被解析成「类别名」，导致 `class_names` 被污染、全部图片判定为未标注。
修复三处：
1. 备注目录移到**项目文件同级**的 `notes/`（不再放进数据源目录）
2. `scan_labels` 跳过路径中含 `notes` 分段的文件
3. `_accumulate_classes` 只接受首字段为整数的行（非 YOLO 标签行一律忽略）

并重新统计了现有项目：`class_names` 恢复为 3 个类别、`label_count` 归零、已标注 390/390。

**验收结果 ✅（offscreen 真实主窗口）：**
- 项目管理页 40 项检查全部通过：对话框（7 个方法卡、切换、规划中不可选、路径校验）、
  项目信息（名称/类型/文件/图像库/类别计数/已标记 390·390/时间/版本/日志/封面）、
  项目类型驱动（图库结构、训练任务、训练页下拉、标注方式、旋转控件显隐）、
  旋转框（模式为矩形、旋转后转四点）、关闭项目（各 VM 复位）、重新打开 + 编辑说明
- 回归：数据四页审计 44 项全部通过；标注与验证流程不变

**遗留：**
- `Anomaly\果汁瓶三分类\果汁瓶三分类_dataset\` 是审计脚本早期用旧命名生成的残留目录（390 个硬链接文件），
  需手动删除（清理命令因批量删除安全阈值被拦截）

**下一步计划：** 按需补充项目类型的语义分割 / Deep OCR 支持，以及最近项目的分组与搜索。

---

## Session 19 — 项目页按钮与文件菜单调整

**日期：** 2026-09-18

**用户要求：**
1. 删除「应用修改」按钮
2. 在「新建项目 / 打开项目」之下追加「保存项目」「项目另存为」
3. 「关闭项目」按钮显示为启用状态
4. 主窗口「文件」菜单暂定为：保存项目 / 项目另存为 / 关闭项目 / 退出
5. 最近项目容器去掉「刷新」按钮，改为按需自动刷新
6. 检测到项目文件不存在的记录不再显示
7. 按钮补充合适的图标

**改动：**

| 位置 | 改动 |
|------|------|
| `ProjectService` | 新增 `save_as(new_path)`：把当前项目写到新 `.mprj`、**切换当前项目路径**、写入最近列表；写失败时回滚路径，避免当前项目指向不存在的文件 |
| `ProjectViewModel` | `save_project(name, description)` 支持在保存时一并写回名称 / 说明并返回成功与否；新增 `save_project_as(path)`；保存后广播 `projectChanged` + `recentUpdated` |
| `ProjectTab · 操作区` | 按钮改为 **新建项目(ADD) / 打开项目(FOLDER) / 保存项目(SAVE) / 项目另存为(SAVE_AS)**，全部带图标；移除「应用修改」（名称 / 说明现在由「保存项目」一并写回） |
| `ProjectTab · 关闭项目` | 加 `CLOSE` 图标；**移除破坏 Fluent 样式的自定义 QSS**（原先设置浅色文字导致看起来像禁用）；按是否已打开项目启用 / 禁用，未打开项目时「保存 / 另存为 / 关闭」统一禁用 |
| `ProjectTab · 最近项目` | 移除「刷新」按钮；`recentUpdated` / `showEvent` / `projectChanged` 三处按需刷新（打开程序、新建、打开、保存、另存为、移除记录、删除项目） |
| `ProjectTab · 过滤` | 项目文件不存在的记录不再显示（只隐藏，不清理存储，避免外置盘临时离线被永久删除） |
| `MainWindow · 文件菜单` | 改为 **保存项目 / 项目另存为 / 关闭项目 / 退出**，全部带图标；因为菜单栏创建早于导航页，用 `_save_project` / `_save_project_as` / `_close_project` 三个转发方法在触发时再取页面；`self.file_menu` 保存菜单引用 |

**踩坑记录：**
- `QAction.setIcon()` 只接受 `QIcon`，传 `FluentIcon` 会报 `TypeError`，必须用 `FluentIcon.X.icon()` 转换（`PushButton.setIcon()` 则可直接传 `FluentIcon`）
- Qt 包装对象生命周期：`menu_bar.actions()[0].menu()` 这种链式调用会因临时对象被回收导致 `QMenu already deleted`，需先持有 action 引用

**验收结果 ✅（offscreen 真实主窗口，脚本 step14）：40 项全部通过**
- 按钮文案与图标（新建 / 打开 / 保存 / 另存为 / 关闭 5 个图标均非空）
- 「应用修改」「刷新」按钮已不存在
- 未打开项目时保存 / 另存为 / 关闭禁用；打开后启用
- 注入一条指向不存在文件的历史记录 → 不显示（卡片全部指向真实文件）
- 保存项目：说明写回项目文件成功
- 项目另存为：新文件生成、当前项目切到新路径、原文件保留、进入最近列表、卡片刷新、信息面板与名称编辑框同步
- 关闭项目：项目状态清空、三个按钮禁用、信息面板复位、卡片选中态清除
- 文件菜单选项恰为 `['保存项目', '项目另存为', '关闭项目', '退出']`，且均有图标

**回归：** 项目管理（step13）40 项、数据四页（step10）44 项全部通过；lint 零错误。

**下一步计划：** 按需补充项目类型的语义分割 / Deep OCR 支持，以及最近项目的分组与搜索。

---

## Session 20 — 图库页与标签类别的界面重构

**日期：** 2026-09-18

**用户要求（对照 Halcon DLT 的参考界面）：**
1. 图库页的导入按钮全部移到左侧
2. 取消「刷新」按钮，改为在适当时机自动刷新
3. 标签类别相关按钮改用图标表示
4. 「新增」「顺序排列」按钮位于列表上方
5. 重命名与颜色编辑放进一个小的弹窗（新增 / 编辑按钮共用），颜色改为自选
6. 「编辑」「删除」按钮位于每个类别行后面
7. 不做类别筛选时显示全图，并提供无标签图像筛选
8. 已标注图像的右上角标注颜色对应各自的类别颜色
9. 缩略图右下角用 T / V / E 表示所属数据集（train / val / test）

**改动：**

| 文件 | 改动 |
|------|------|
| `views/dialogs/class_edit_dialog.py`（新增） | `ColorPicker` 自选颜色面板：主色板横向色相彩虹 + 纵向明度，右侧饱和度竖条，点击 / 拖动取色；`ClassEditDialog`：名称 / 颜色（色板 + 8 个预设色块 + `#RRGGBB` 手输，三者双向同步）/ 快捷键（自动分配的类别 id），新增与编辑共用 |
| `views/data_widgets.py` | 新增 `ImportCard`（导入文件夹 / 导入图片，带图标）；`ClassCard` 重写：列表上方 **新增(ADD) + 上移(UP) + 下移(DOWN)** 图标按钮，列表首行 **「全部类别」**，每个类别行尾 **编辑(EDIT) / 删除(DELETE)** 图标按钮（`ClassRow` 行组件），弹出统一编辑弹窗；`LineEdit` / `UNLABELED_LABEL` / `class_chip` 等失效代码清理 |
| `views/gallery_tab.py` | 左侧栏顺序：**数据导入 → 数据集概览 → 标签类别 → 浏览筛选**；工具栏去掉导入与刷新按钮（改为显示「显示 N / M 张」）；点类别行直接筛选、「全部类别」清除筛选；类别颜色变化自动刷新 |
| `views/review_tab.py` | 同步去掉刷新按钮、同样传类别颜色与数据集标记、点击类别不再需要二次点击取消 |
| `views/widgets/thumbnail_grid.py` | 新增 `_COLOR_ROLE` / `_SUBSET_ROLE`；`set_images(paths, annotated, colors, subsets)`、`set_annotated(flags, colors)`；右上角角标改用该图**类别颜色**（缺省回退粉色）；右下角绘制 **T / V / E** 圆角标记（用 `SPLIT_COLORS` 的 train/val/test 配色）；悬停提示补充「数据集：train（训练）」 |
| `viewmodels/dataset_vm.py` | 新增 `image_colors()`（每图对应类别颜色）、`subset_labels()`（复算划分得到 T/V/E，带缓存，随 `invalidate_images` 失效）、`decorations_for(paths)`（一次取出两个并行序列，供两个页面共用） |

**关键实现说明：**
- T/V/E 归属**不用**去翻划分产物目录，而是用当前数据集的划分参数（比例 / 随机种子 / 分层方式）调 `DatasetService.split_members` 复算一遍 —— 与 `split_dataset` 的算法完全一致，因此原始图片目录没做划分也能正确标注归属，且划分产物不存在时依然可用。
- 去掉的「二次点击取消筛选」由「全部类别」行取代，筛选入口只有一个语义，不会出现「点了没反应」。

**验收结果 ✅（offscreen 真实主窗口，脚本 step15）：49 项全部通过**
- 导入按钮在左侧栏且位于概览之上；图库页已无 `folder_btn` / `refresh_btn`
- 新增 / 上移 / 下移均为纯图标按钮（无文字、图标非空）；未选类别时排序禁用
- 列表 4 行（全部类别 + 3 类别），「全部类别」行没有编辑/删除按钮，3 个类别行各有 2 个图标按钮
- 默认显示 390 张；点「good」→ 250 张；点「全部类别」→ 390 张；未标注筛选 → 0 张（分类数据集每张都在类别目录里）；已标注筛选 → 390 张
- 角标颜色 = 所属类别颜色（`#005FB8`）；子集标记只含 T/V/E，数量 **T=273 / V=78 / E=39**，与 7:2:1 分层划分完全一致；悬停提示含「数据集：train（训练）」
- 弹窗：标题随新增/编辑变化、名称与颜色回填、色板取色写入颜色框、手输 `#00FF88` 生效、非法值给出提示、空名称与重名均被拦截、预设色块可用
- 通过类别卡片真实完成 **新增 → 编辑（改名 + 改色）→ 删除**，删除后行数恢复 4、类别清单完全还原
- 刷新后仍显示全图且角标保留类别颜色

**回归（全部 0 失败）：** step8 界面冒烟、step9 修复核对、step10 数据四页 44 项、step11 任务清单、step13 项目管理 40 项、step14 项目页按钮与菜单 40 项、step15 图库 49 项；lint 零错误。

**说明与遗留 ⏳：**
- 「标注检查」页也一并去掉了刷新按钮并同步了角标配色（与图库页保持一致），若希望保留可改回
- 分类数据集下「未标注」恒为 0（每张图都在类别目录里，本身就是已标注），这是口径正确而非缺陷
- 颜色面板为自绘的 DLT 风格取色板，未使用 Qt 标准 `QColorDialog`

**下一步计划：** 按需补充项目类型的语义分割 / Deep OCR 支持，以及最近项目的分组与搜索。

---

## Session 21 — 图库筛选/拆分映射/图像标记重构 + 打开项目性能修复

**日期：** 2026-09-18

**用户要求：**
1. 浏览筛选移到图像窗口上方，原有的顶部标识全部不要；筛选项为 **按标签 / 按标记 / 按拆分 / 文本筛选**
2. 左侧标签类别增加「无标签（无色）」；**点击标签类别不再筛选图像**
3. 新增「数据集拆分映射」面板（拆分名称 + 锁定提示 + 不在任何一个拆分集里 / 训练 / 验证 / 测试）
4. 新增「图像标记」面板与编辑弹窗（文本 / 颜色 / 为当前图像分配标记 / 取消·确定·应用）
5. 选中图像后点类别、拆分、标记即赋值；标记为「无则追加、有则删除」（类似备注）
6. 缩略图右键菜单：打开文件所在位置 / 另存图像为 / 移除所选图像
7. **测试方式要改**：不要每次轮询全部图片，图片相关抽查即可

**改动：**

| 文件 | 改动 |
|------|------|
| `viewmodels/dataset_vm.py` | 新增图像标记（`image_tag_defs / tag_map / all_tag_names / tag_color / marker_colors / add·update·remove_image_tag / toggle_image_tag`）、类别覆盖（`set_class_for`）、拆分映射（`set_split_for / split_summary / split_name_label / split_locked`）、组合筛选 `filter_images(label, mark, split, text)`、`gallery_decorations()`、`save_image_as()`、`remove_images()`；图片统一用「相对来源目录的路径」作为项目内标识 `_image_key` |
| `views/gallery_widgets.py`（新增） | `FilterBar`（三个档位按钮在候选值之间循环 + 文本筛选 + 缩略图尺寸 + 计数）、`SplitMapCard`、`TagCard`（标记色块 + 右键编辑/删除） |
| `views/dialogs/tag_edit_dialog.py`（新增） | 图像标记编辑弹窗：文本 / 颜色（取色面板可展开）/ 为当前图像分配标记 / 取消·确定·应用 |
| `views/data_widgets.py` | `ClassCard`：首行改为「无标签」（空心方框 = 无色），点击类别行只广播 `classClicked`（是否筛选 / 赋值由页面决定） |
| `views/gallery_tab.py` | 左侧：数据导入 → 数据集概览 → 标签类别 → 数据集拆分映射 → 图像标记（可滚动）；右侧：**筛选栏在图像窗口上方**，原顶部标识移除；串联赋值、右键菜单、标记弹窗 |
| `views/widgets/thumbnail_grid.py` | 多选（ExtendedSelection）、`selected_paths()`、右键 `imageContextMenu`、标记色点（`_MARKERS_ROLE`）、主图缓存 |
| `views/review_tab.py` / `views/annotate_tab.py` | 同步新的筛选/角标口径；**页面不可见时不渲染缩略图** |
| `views/dialogs/class_edit_dialog.py` | `_toggle_picker` 改用 `isHidden()` 判断（弹窗未显示时 `isVisible()` 恒为 False） |

**性能修复（本次最大的收获）🐌→⚡**

用户反馈测试太慢，顺手用 cProfile 定位到**一个真实的 O(n²) 缺陷**：

> `DatasetViewModel.resolved_source()` 每次调用都会 `DatasetService.scan_images()` 全目录扫描一遍；
> 而它又被「每张图片」的 `_image_key()` 调用 —— 打开项目触发了 **3129 次目录扫描**。

| 操作 | 修复前 | 修复后 |
|------|--------|--------|
| 打开项目 | **15.57 s** | **0.28 s**（55×） |
| 图库首次渲染 390 张 | **14.92 s** | **1.34 s**（11×） |
| 标注检查页首批渲染 | ~11 s | 0.05 s |

修复手段：
1. `resolved_source()` 增加可用目录缓存 `_usable_sources`，全目录扫描只做一次
2. 缩略图增加主图缓存（`_MASTER_CACHE`，容量 512），避免每次刷新都重新读盘解码
3. 图库 / 标注检查 / 图像标注三页改为**「不可见时只标记待刷新，切回本页再渲染」**

**测试方式调整（按要求）：**
- 新增 `step16_meta.py`（10 张临时数据集）：图像标记 / 类别覆盖 / 拆分映射 / 组合筛选 / 图片增删的数据层自测 —— **4 s**
- 新增 `step17_gallery2.py`（9 张临时数据集）：筛选栏 / 无标签行 / 拆分映射 / 标记 / 多选 / 右键菜单的界面验收 —— **6 s**
- 新增 `step18_spot.py`（真实项目**抽查**）：不再渲染整页，先用文本筛选把渲染量压到个位数再抽查角标与侧栏 —— **2 s**（原来 66 s）
- 重脚本（step8~step15）改为按需运行，不再每次全跑

**验收结果 ✅（全部 0 失败）**

| 脚本 | 结果 | 耗时 |
|------|------|------|
| step16 数据层 | 58 项 | 4.1 s |
| step17 图库界面 | 65 项 | 6.1 s |
| step18 真实项目抽查 | 37 项 | 2.2 s |
| step8 界面冒烟 / step13 项目管理 / step14 项目页菜单 | 全通过 | 3~4 s |

抽查要点：T/V/E 数量 **273 / 78 / 39** 与 7:2:1 一致；角标颜色来自类别表；点击标记「追加 → 再点删除」；拆分映射「不在任何一个拆分集里」计数与高亮跟随选中图像；原始 390 张数据未被修改。

**口径修正 ⚠️：** `subset_labels()` 现在只在**划分产物已生成**时才报 T/V/E，未执行划分前除手工映射外一律算「不在任何一个拆分集里」—— 避免把「预览比例」当成已生效的归属。

**说明与遗留 ⏳：**
- 「标签类别」的「异常类别」分组（DLT 用 良好/异常 给类别分类）未实现：`ClassDef` 目前没有「类别类型」字段
- 数据层的标记 / 拆分映射 / 类别覆盖都存进 `project.params`，随 `.mprj` 持久化
- 增删图片会同时清理归档副本与它的标记 / 覆盖记录

**下一步计划：** 按需补充项目类型的语义分割 / Deep OCR 支持，以及最近项目的分组与搜索。

---