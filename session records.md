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