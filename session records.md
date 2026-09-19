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

## Session 22 — 图库导入对齐 Halcon DLT（排序 / 反向 / 左右插入 / 初步标注）

**日期：** 2026-09-19

**用户要求：**
参照 Halcon DLT 的「打开图像」，导入时可选择按文件夹排序、**左侧 / 右侧**插入、**反向顺序**、
**初步标注为 OK / NG** —— 导入时就把图片的标注与分类一并做好。

**1. 新增「导入图像」选项弹窗（`views/dialogs/import_images_dialog.py`）**

| 控件 | 说明 |
|------|------|
| 图像来源 | 只读路径 + 「浏览…」（按入口切换为文件夹 / 多选图片）；拖拽进入时自动回填 |
| 读取顺序 | 排序依据 **按文件名 / 按修改时间** + **反向顺序** 勾选 |
| 插入位置 | **右侧（最后）/ 左侧（最前）**，单选；图库为空时整行置灰并提示 |
| 初步标注 | **不指定（无标签）/ OK / NG / 项目已有类别** 下拉 |
| 选项 | **跳过内容重复的图像（SHA256 去重）** |
| 预览 | 实时显示「共 N 张（首张：xxx）…插入到…，初步标注：…」；无图时禁用「导入」 |

**2. 多来源图库（数据层）**
- `Dataset` 新增 `source_paths`（多来源目录累加）+ `sources` / `source_label` 属性（旧项目自动由 `source_path` 回退）
- `DatasetService` 新增 `scan_images_multi / scan_labels_multi / build_label_index_multi /
  label_index_for / child_class_dirs_multi / sort_images / accumulate_classes / summarize_library`
- **顺序清单 `params["image_order"]`**：以清单为准（图库内容 = 历次导入的图片，清单顺序 = 图库顺序）；
  清单不存在时退回按来源目录递归扫描，**旧项目行为完全不变**
- `DatasetViewModel`：`_source_roots()`（带缓存，替代原先的单目录解析）、`_merge_order()`、
  `_relative_key()`（相对所在来源目录的路径）、`import_images/import_files(directory, options)`
- 初步标注写入 `params["class_overrides"]`，并**自动在类别表建类**（保证类别列表与「已标注」判定自洽）
- 跨批次去重：已有图片的 SHA256 走内存缓存，重复图片不入库（`duplicate_count` 累计）
- `split_dataset / preview_split` 新增 `images / label_index` 参数，**多来源图片清单可直接参与划分**
  （与界面上看到的图库完全一致）；`apply_split / preview_split` 改为传入合并后的清单

**3. 界面接线（`views/gallery_tab.py` / `data_widgets.py`）**
- 「导入文件夹」「导入图片」先选来源再弹选项窗；拖拽同样走选项窗
- 初步标注选 OK / NG 且项目无该类别时先建类再导入
- 数据集概览的「来源」显示多来源目录（「；」分隔）

**⚠️ 过程中修复的实现缺陷：**
- `_order_list()` 原先用 `_params_dict()` 读取 —— 该工具**只接受 dict**，而 `image_order` 是 list，
  导致顺序清单恒被读空（左侧 / 右侧插入、反向顺序全部失效）。改为直接读 `project.params`
- 顺序清单存在时若仍把「来源目录里多出来的图片」追加进图库，被去重跳过的图片会从目录扫描中"漏回来"；
  改为**清单为准**后去重才真正生效

**验收结果 ✅（offscreen 自检 70 项全通过）：**

| 检查组 | 项数 | 结果 |
|--------|------|------|
| 数据层（排序 / 反向 / 左右插入 / 时间排序 / 去重 / 初步标注 / 松散文件 / 持久化 / 划分回归 / 移除同步） | 50 | 全通过 |
| UI 端到端（真实主窗口：两次导入 → 图库 / 概览 / 类别卡 / 已标注 / 筛选栏 / 重开项目） | 20 | 全通过 |

要点：左侧+反向 → `n1,n0` 插到最前；重复内容被跳过且不入库；OK / NG 自动建类并计入已标注；
重开 `.mprj` 后顺序、类别与标注状态全部保持；`source_paths` 往返一致；划分总数与图库一致。

**说明与遗留 ⏳：**
- 顺序清单存在后，图库内容以**导入过的图片**为准：直接往来源文件夹里拷图片不会再自动出现，需再次导入
- 「按修改时间」使用的排序键为文件 mtime；同名不同目录的图片在项目内以「相对所在来源目录的路径」标识
- 相机采集仍未实现（见开发文档阶段二A）

**下一步计划：** 按需补充相机采集、图库按导入批次分组显示。

---

## Session 23 — 修复右键「移除所选图像」静默失败（只读图片）

**日期：** 2026-09-19

**问题现象：**
图库图像窗口右键「移除所选图像（N 张）」→ 确认后**界面毫无变化**，图片既没消失也没报错。

**排查过程：**
1. 用只读方式打开用户实际项目（`E:\Develop\Deep Maven\Test\异常检测测试.mprj`）：
   `dataset.source_path = E:\Develop\Deep Maven\bottle\train\good`，209 张图，无顺序清单
2. 用等价场景（临时目录）复现右键移除 → **正常删除**，说明代码路径本身没问题，问题在数据侧
3. 检查真实图片属性：

   ```
   000.png                 Attributes: ReadOnly, Archive      ← 只读
   bottle\train\good       Attributes: ReadOnly, Directory
   ```

   **根因**：素材是从压缩包解出来的，带「只读」属性。Windows 上 `Path.unlink()` 对只读文件
   抛 `PermissionError: [WinError 5]`；而 `remove_images()` 只 `logger.warning` 吞掉了异常，
   最后照样提示「已移除 0 张图片」—— 表现就是「点了没反应」。

**修复：**

| 位置 | 改动 |
|------|------|
| `DatasetService.remove_file()`（新增） | 先 `unlink()`；遇 `PermissionError` 则 `os.chmod(mode \| stat.S_IWRITE)` 后再删 —— 与资源管理器行为一致，兼容 ZIP 解出的只读素材 |
| `DatasetService._place()` | 覆盖划分产物前改用 `remove_file()`，避免只读硬链接导致「删除失败 → 退化为复制」 |
| `DatasetViewModel.remove_images()` | 删除失败**不再静默**：成功与失败分组处理，失败项保留在图库中，并给出 `error` 提示（含系统原因，如「另一个程序正在使用此文件」） |
| `DatasetViewModel._refresh_stats()`（新增） | 移除后重算数据集统计并写回 `project.dataset`，概览的图片 / 标签 / 类别数与图库立即一致（原先移除后概览仍显示旧数量） |

**验收结果 ✅（offscreen 自检 20 项全通过）：**

| 检查组 | 结果 |
|--------|------|
| 只读文件可被删除（`remove_file`） | ✅ |
| 被独占占用的文件抛 `OSError` | ✅ |
| 只读图库：4 张 → 移除 1 张 → 图库 3 张、磁盘文件已删、概览同步为 3 | ✅ |
| 真实主窗口：只读图库导入 3 张 → 右键移除 → 缩略图 2 张、概览 2、提示「已移除 1 张」 | ✅ |
| 删除失败场景：给出 `error` 提示、失败图片仍留在图库 | ✅ |
| 回归：顺序清单（左侧 + 反向）↔ 移除后清单清理、统计同步、文件删除 | ✅ |

**说明 ⏳：**
- 只有确实删不掉的图片（例如被其他程序占用）才会报错；只读属性已自动处理，无需用户手工去属性
- 图片所在目录的「只读」属性不影响删除（Windows 语义），未做处理
- 清理：自检脚本写入「最近项目」的临时记录已按「系统临时目录 + 文件不存在」条件回收

> ⚠️ **口径修正（见 Session 24）**：本条把「移除」实现成了「删除本地文件 + 清出数据集」，
> 与用户预期不符 —— 「移除」只应移出程序读取的图库，**不删除本地文件**。
> `DatasetService.remove_file()` 保留给划分产物覆盖使用，`remove_images()` 已改为不碰磁盘。

---

## Session 24 — 修正「移除」语义：只移出数据集，不删除本地文件

**日期：** 2026-09-19

**用户反馈：**
「移除」应当只是把图片从程序读取的图库中移出，**本地文件不删除**；实测本地文件被删掉了。

**修正：**

| 位置 | 改动 |
|------|------|
| `DatasetViewModel.remove_images()` | 不再删除任何文件：只把图片记入项目内的**「已移除清单」** `params["image_excludes"]`，并清掉它的类别覆盖 / 拆分映射 / 图像标记与哈希缓存，然后重算统计 |
| `DatasetViewModel.images()` | 图库清单在「来源目录 + 顺序清单」之外再**过滤已移除清单**，图库 / 标注 / 检查 / 拆分等页面口径统一 |
| `DatasetViewModel._exclude_list() / _excluded_paths() / _set_excludes()`（新增） | 已移除清单的读写（注意该值是**列表**，需直接读 `project.params`） |
| `DatasetViewModel._import()` | **重新导入**同一张图片即视为「回到数据集」，自动从已移除清单摘掉；统计重建同样排除已移除项 |
| `DatasetViewModel._refresh_stats()` | 统计口径改为按过滤后的图库重算（图片 / 标签 / 类别计数） |
| `augment_preview / augment_apply` | 改用 `images()`（数据增强不再包含已移除的图片） |
| `gallery_tab` | 右键项改为「**从数据集移除（N 张）**」；确认弹窗写明「本地文件不会被删除，之后重新导入即可恢复」；提示文案同步 |
| `DatasetService.remove_file()` | 保留（划分产物覆盖时仍需安全删除），不再被 `remove_images` 调用 |

**验收结果 ✅（offscreen 自检 24 项全通过）：**

| 检查组 | 结果 |
|--------|------|
| 只读图库移除：**本地文件仍在（只读属性也未改动）**、图库 4→3、概览同步 3、清单落盘 | ✅ |
| 重复移除同一张：仅 `warning` 提示，不重复记录 | ✅ |
| 重新导入同一文件夹：图片回到图库（4 张）且**顺序不变**、清单清空 | ✅ |
| 旧项目（无顺序清单，按目录扫描）：移除后图库 3→2，本地文件保留 | ✅ |
| 真实主窗口右键路径：缩略图 3、概览 3、提示「本地文件未删除，可重新导入恢复」 | ✅ |
| 重开 `.mprj`：移除状态保持，文件仍在；再导入后恢复 4 张 | ✅ |

**回归 ✅：** 导入选项检查 19 项全通过（排序 / 反向 / 左右插入 / 按修改时间 / 去重 / OK·NG 初步标注 / 多选文件 / 持久化 / 划分 / 移除后统计）。

**说明 ⏳：**
- 修复过程中发现并处理：移除后哈希缓存未失效 → 重新导入会被当成「重复图片」跳过；现在移除时同步清掉缓存条目
- 移除会一并清掉该图的图像标记 / 类别覆盖 / 拆分映射（这些是数据集内的记录），重新导入后需要重新打标
- 划分产物目录（`dataset/`）里的硬链接副本不会随移除变动，下次执行划分会自动重建
- 用户图库目录 `bottle\train\good` 经核对仍为 209 张，无文件丢失

**下一步计划：** 按需补充相机采集、图库按导入批次分组显示。

---

## Session 25 — 导入窗口：子文件夹树 → 标签 → 自动标注

**日期：** 2026-09-19

**用户要求：**
导入时窗口里要显示**子文件夹结构**，根据选择生成**预览标签**（标签与子文件夹对应），标签名可手工修改；
确定创建后**按标签自动标注**。

**1. 弹窗新增「子文件夹与标签」树（`views/dialogs/import_images_dialog.py`）**

用 `qfluentwidgets.TreeWidget` 三列展示：

| 列 | 内容 |
|----|------|
| 子文件夹 | 目录层级（所选文件夹标「（所选文件夹）」），**只显示含图的目录**，空目录不展示 |
| 图片 | 该目录**含子目录**的图片总数 |
| 标签 | 含图目录对应的标签，默认取文件夹名（所选文件夹自身留空 → 用「初步标注」兜底）；**双击可改名** |

- 每个目录带复选框：勾选/取消即决定是否导入该目录的图片；父子**三态联动**（全选 / 全不选 / 半选）
- 选项变化即时更新预览：`共 N 张图像；标签：good 4、bad 3、…；插入到…；重复内容将跳过；首张：xxx`
- 标签留空 → 退回「初步标注」下拉（不指定 / OK / NG / 已有类别），因此**扁平文件夹**（无子目录）
  仍沿用原来的用法

**2. 数据层（`viewmodels/dataset_vm.py`）**

| 选项键 | 说明 |
|--------|------|
| `label_map` | `{图片所在文件夹: 标签名}`：按图片**自己的目录**取标签 |
| `label` | 兜底标注（目录没填标签时使用） |
| `files` | 只导入这些图片（树里勾选后的子集），不再整目录扫描 |

- 导入时按 `label_map.get(图片所在文件夹) or label` 逐张写 `class_overrides`，并用到的标签**自动建类**
- 导入提示改为：`导入 N 张（插入到…），已按标签标注：good 4 张、bad 3 张…`
- 去重、左右插入、反向顺序、顺序清单等既有逻辑不变

**验收结果 ✅（offscreen 自检 42 项全通过）：**

| 检查组 | 结果 |
|--------|------|
| 树结构：根节点标「所选文件夹」、含子目录计数、空目录不显示、层级正确 | ✅ |
| 标签默认值：子目录取文件夹名、所选文件夹留空 | ✅ |
| 预览：总数 + 各标签分布（good 4 / bad 3 / ignore 2 / misc 1 / 未标注 1） | ✅ |
| 改名：双击改名即时反映到预览与 `label_map` | ✅ |
| 勾选：取消子目录 → 图片排除；取消父目录 → 子级联动；部分勾选 → 父节点半选 | ✅ |
| 真实主窗口导入 11 张：目录标签各自成类、根目录图片用「初步标注 OK」、已标注 11 / 类别分布正确 / 缩略图 11 | ✅ |

**回归 ✅（20 项全通过）：** 排序（文件名 / 修改时间）、反向、左右插入、内容去重、扁平文件夹的初步标注、
`files` 白名单子集导入、移除不删文件 + 重新导入恢复、划分预览与落盘。

**真实数据冒烟（只读，`E:\Develop\Deep Maven\bottle`）：**

```
bottle（所选文件夹） [355 张]
  ground_truth [63]   → broken_large / broken_small / contamination
  test         [83]   → broken_large / broken_small / contamination / good
  train       [209]   → good
预览：共 355 张；标签：broken_large 40、broken_small 44、contamination 42、good 229
取消勾选 ground_truth（掩码目录）→ 共 292 张，broken_large 20 / broken_small 22 / contamination 21 / good 229
```

即：类别标签由子目录名自动生成，掩码目录 `ground_truth` 可在树里一键排除 —— 正是该树的实际用途。

**说明 ⏳：**
- 重复导入同一批图片时不会重复入库，因此**不会覆盖已有的标注**；要改标注请在图库中选中图像后点类别行
- 文件模式下不显示树（按所选文件导入），仍用「初步标注」下拉

**下一步计划：** 按需补充相机采集、图库按导入批次分组显示。

---

## Session 26 — 导入标签真正生效 + 弹窗精简 + 筛选栏改下拉菜单

**日期：** 2026-09-19

**用户反馈（三件事）：**
1. 导入弹窗**暂时删除「初步标注」**；标签留空的文件夹下图片即「无标签」；删除文字描述
2. **导入后图片没有按子文件夹标签标注**（要求排查实现）
3. 图库页顶部筛选容器各筛选项改成**下拉框 + 勾选项**，并**自动带入左侧**的标签类别 / 数据集划分 / 图像标记

### 1. 排查「导入后没标注」—— 找到了真因

对照真实项目（`Test\异常检测测试.mprj`，**异常检测**项目）复现：

| 项目类型 | 类别是否正确写入 | 已标注判定 |
|----------|-----------------|-----------|
| 分类 classify | ✅ `good / broken…` | ✅ |
| 目标检测 detect | ✅ | ❌ 0（只看 YOLO txt） |
| 异常检测 anomaly | ✅ | ❌ 0（只看 YOLO txt） |

类别覆盖值其实**写进去了**（`class_overrides` 与类别分布都正确），但 `annotated_flags()` 对
非分类任务只看「是否存在 YOLO 标签文件」，于是图库把所有图片显示成**未标注**，看起来就像「没标注」。
另外概览的「类别数」恒为 0（`class_names` 只从标签文件 / 一级子目录推导，推不出嵌套结构的类别）。

**修复：**

| 位置 | 改动 |
|------|------|
| `DatasetViewModel.annotated_flags()` | 判定顺序改为：**① 类别覆盖值（覆盖值非空即已标注；显式空串=无标签即未标注）** ② 分类任务看类别目录 ③ 检测 / 分割 / 异常看 YOLO 标签文件 |
| `DatasetViewModel._sync_class_stats()`（新增） | 导入 / 移除后按图库**实际类别**回写 `dataset.class_names / class_counts`，概览的「类别数」不再是 0 |

实测（同一份嵌套数据，三种项目类型）：`已标注 4 / 4`、`dataset.class_names = [broken_large, contamination, good]`。
真实项目复检：`images 83`、类别分布 `broken_large 20 / broken_small 22 / contamination 21 / good 20`、**已标注 83 / 83**。

### 2. 弹窗精简（`views/dialogs/import_images_dialog.py`）

- **删除「初步标注」下拉**（`label` 选项不再由界面产生）
- **删除全部说明文字**（树下方提示、底部说明；底部提示仅保留校验用文案）
- **标签留空 = 无标签**：`label_map` 里的空串会被写成显式的空覆盖值
  —— 与「没有指定过」区分开，图库类别面板计入「无标签」、标注状态为未标注
- 默认值规则：子文件夹取文件夹名；所选文件夹**本身就是类别目录**（其下无含图子目录）时取文件夹名，
  否则（容器目录）留空 —— 这样扁平文件夹导入仍然得到原文件夹名类别，容器目录的散图则是无标签

### 3. 图库筛选栏重做（`views/gallery_widgets.py::FilterBar`）

三个 `DropDownPushButton` + `CheckableMenu`（勾选指示器）的下拉菜单，选项自动跟随左侧面板：

| 菜单 | 选项 |
|------|------|
| 标签 | 全部 / 已标注 / 未标注 ── 无标签 / **各标签类别**（左侧「标签类别」） |
| 数据集拆分 | 全部 / 训练 / 验证 / 测试 / 未划分（左侧「数据集拆分映射」） |
| 标记 | 全部 / 带标记 / 无标记 ── **各图像标记**（左侧「图像标记」） |

- 按钮文字与选中态反映当前档位（如「标签：good」；非默认值即高亮）
- `DatasetViewModel.filter_images()` 新增 `class_name` 参数（"all" / ""=无标签 / 类别名），`mark` 支持多个标记名
- `reset()` 复位并广播刷新；`set_classes()` / `set_tag_names()` 由图库页在每次刷新时注入候选值

**验收结果 ✅（offscreen 自检 66 项全通过）：**

| 检查组 | 项数 | 要点 |
|--------|------|------|
| 弹窗与自动标注 | 40 | 无初步标注控件 / 无说明文字 / 清空标签→无标签 2 张且判为未标注 / 其余 5 张已标注 / 概览类别数 2；筛选栏三个下拉、菜单含类别与标记、按类别 4 张、按无标签 2 张、按拆分 1 张、按未划分 6 张、按标记 1 张、复位 7 张 |
| 扁平 / 容器文件夹标签默认值 | 8 | 扁平 `good/` → 标签 good（清空则无标签）；容器 `bottle/` → 根标签空、子目录取文件夹名、散图无标签 |
| 导入与移除回归 | 18 | 排序 / 反向 / 左右插入 / 去重 / 文件白名单 / 文件夹标签建类 / 移除不删文件 / 重新导入恢复 / 划分 |

**说明与遗留 ⏳：**
- 「初步标注」仅从**界面**移除，`_import` 仍保留 `label` 兜底参数（脚本 / 后续接口可复用）
- 文件模式（多选图片）不显示树，因此该模式下不再能指定标签，导入后按标签文件 / 目录名判定
- 移除过哈希缓存、`image_excludes` 等机制不变

**下一步计划：** 按需补充相机采集、图库按导入批次分组显示。

---

## Session 27 — 筛选多选 + Ctrl 滚轮缩放 + 特大档位

**日期：** 2026-09-19

**用户要求：**
1. 图库筛选选项要**可多选**
2. 图像缩略图列表支持 **Ctrl + 滚轮**缩放
3. 缩略图最大档**再大一倍**

### 1. 筛选下拉改成多选（`views/gallery_widgets.py`）

把原来的 `CheckableMenu`（点一下即关闭、只能单选）换成 **Flyout 面板 + CheckBox**：

- 新增 `_FilterPanel(FlyoutViewBase)`：一组勾选项，可同时勾多个；首项「全部」互斥
  （勾「全部」清空其它；勾具体项取消「全部」；一个都不剩时自动回到「全部」）
- 点击面板内部**不会关闭**（`Qt.Popup` 只在点击外部时关闭），每次勾选即时刷新图库
- 按钮文案随选择变化：`标签：good` → `标签：已选 2 项`；非默认值即高亮
- `label_value() / class_value() / split_value() / mark_value()` 改为返回**列表**（空 → `"all"`）

**数据层**（`viewmodels/dataset_vm.py`）：`filter_images()` 新增 `_filter_list()` 归一化，
四个维度都接受「单值」或「值列表」：
- `label`：annotated / unannotated 任意组合
- `class_name`：类别名与 `""`（无标签）任意组合
- `split`：train / val / test / none
- `mark`：tagged / untagged / 具体标记名（命中任一即可）
- 失效的勾选（类别 / 标记被删除）会在刷新时自动摘除，不会出现「筛出空图库」

### 2. Ctrl + 滚轮缩放缩略图（`views/widgets/thumbnail_grid.py`）

- `ThumbnailGrid` 新增 `wheelEvent`：按住 Ctrl 时按档位缩放并 `accept()`（不滚动列表），
  否则交给父类正常滚动；`set_wheel_zoom(True)` 由页面开启（默认关闭，不影响标注 / 检查页）
- 新增 `zoom(delta)` 与 `thumbSizeChanged` 信号；图库页收到信号后**回同步尺寸滑杆**并提示当前像素

### 3. 特大档位

- 尺寸档位由 3 档扩为 4 档：`88 / 124 / 176 / 352`（特大 = 原最大档 × 2）
- `MASTER_SIZE` 提升到 352（放大不糊）；主图缓存上限由 512 收敛为 192（主图变大，控制内存）
- 筛选栏尺寸滑杆范围改为 0–3；提示补充「也可在图像窗口按 Ctrl + 滚轮缩放」

**验收结果 ✅（offscreen 自检 41 项全通过）：**

| 检查组 | 要点 |
|--------|------|
| 多选菜单 | 三个菜单均以「全部」开头、含类别 / 标记 / 拆分档位；多选后按钮显示「已选 N 项」并高亮；空选择 = 全部 |
| 面板互斥 | 勾具体项自动取消「全部」；可同时勾多项；全不选自动回到「全部」；点「全部」清空其它 |
| 多选过滤 | good+bad → 4 张；good+无标签 → 3 张；已标注+未标注 → 全部 5 张；训练+测试 → 2 张；带标记+无标记 → 全部；复位 → 全部 |
| 档位 | 特大 = 2 × 大（176 → 352）；4 档序列正确；滑杆 0–3；主图缓存按最大档 |
| Ctrl + 滚轮 | 124 → 176 → 352 → 到顶不再变；下滚逐档回退、下限停 88；不带 Ctrl 不缩放；广播后滑杆同步（1 ↔ 2 ↔ 3） |

**真实项目只读冒烟（`Test\异常检测测试.mprj`）：**
图库 83 张 / 已标注 83；标签菜单 `全部 · 已标注 · 未标注 · 无标签 · broken_large · broken_small · contamination · good`；
标记菜单含用户标记 `asd`；滑杆 0–3；Ctrl + 滚轮 124 → 176 且滑杆同步；检查后项目 `dirty = False`（未改动用户文件）。

**说明与遗留 ⏳：**
- 排查过程中确认「good + 无标签」少一张是**测试脚本混用了两个 ViewModel**，功能本身正确
- Ctrl + 滚轮缩放目前只在图库页开启；标注 / 检查页仍为固定尺寸（如需可后续开启）
- 缩略图缓存按最大档存储，特大档下首屏解码稍慢（后续可按可见区域懒加载优化）

**下一步计划：** 按需补充相机采集、图库按导入批次分组显示。

---

## Session 28 — Ctrl 滚轮三页可用 + 角标放大一倍 + 通知时长加倍

**日期：** 2026-09-19

**用户反馈：**
1. **Ctrl + 滚轮在图像列表窗口里没生效**
2. 图片上的三个角标偏小，想**放大一倍**
3. 主窗口的信息通知停留时间**延长一倍**

### 1. Ctrl + 滚轮（两个原因叠加）

| 原因 | 处理 |
|------|------|
| 上一轮只在**图库页**开启了缩放，图像标注 / 标注检查页仍是固定尺寸（侧栏卡片标题就叫「图像列表」，用户很可能正是在那里试的） | 三个页面的缩略图网格全部开启：`gallery_tab` / `annotate_tab` / `review_tab` 均调用 `set_wheel_zoom(True)` |
| 只在 `wheelEvent` 里处理——真实鼠标滚轮事件由 **viewport（视口）** 接收，再经 `QAbstractScrollArea` 转发，路径不够稳 | 改为在 **`viewportEvent()`** 中拦截（Qt 文档指定的视口事件入口），`wheelEvent` 保留为兜底；两者共用 `_handle_wheel()`，避免重复缩放 |

- 检查页的尺寸档位表由 `(中, 大, 大)` 修正为 `(中, 大, 特大)`（原先第 3 档与第 2 档重复，等于只有两档）
- `FilterCard` 增加 `set_thumb_step()`，检查页收到 `thumbSizeChanged` 后同步尺寸滑杆（图库页上一轮已同步）

### 2. 三个角标放大一倍（`views/widgets/thumbnail_grid.py`）

抽成具名常量，按原尺寸 ×2：

| 角标 | 原 | 现 |
|------|----|----|
| 右上角「已标注」三角 | 11 | **22** |
| 右下角 T / V / E 方块 | 14 | **28**（字母字号同步放大到 `max(12, +2)`） |
| 左下角标记色点 | 6（间距 2） | **12**（间距 4） |

### 3. 通知时长

`MainWindow._show_message()` 的 InfoBar `duration`：**3000 → 6000**（其余提示不变；「关于」弹窗仍为常驻 `-1`）。

**验收结果 ✅（offscreen 自检 23 项全通过）：**

| 检查组 | 要点 |
|--------|------|
| 通知 | InfoBar 调用参数 `duration == 6000` |
| 角标 | 11→22 / 14→28 / 6→12 / 间距 2→4；放大后仍可正常绘制 |
| Ctrl + 滚轮 | 图库 / 检查 / 标注三页：**发送到 viewport** 的真实事件路径下 124→176→352、下滚回退；Ctrl+滚轮不改变滚动位置；不带 Ctrl 不缩放且正常滚动 |
| 滑杆同步 | 图库滑杆 1→2；检查页滑杆 1→0；档位表末项为特大 |

**真实项目只读冒烟（`Test\异常检测测试.mprj`）：**

```
GalleryTab   项=  83  Ctrl+滚轮 124 → 176  绘制=OK
ReviewTab    项=  83  Ctrl+滚轮 176 → 352  绘制=OK
AnnotateTab  项=  83  Ctrl+滚轮  88 → 124  绘制=OK
图库已标注: 83 / 83      项目已改动: False
```

**说明与遗留 ⏳：**
- 角标为固定像素尺寸（不随缩略图档位缩放），特大档下相对显得小；如需可改为按档位比例缩放
- 缩放仍以「档位」步进（88 / 124 / 176 / 352），不做连续缩放

**下一步计划：** 按需补充相机采集、图库按导入批次分组显示。

---

## Session 29 — 角标改为随缩略图档位等比缩放（176px 为基准）

**日期：** 2026-09-19

**用户反馈：**
上一轮把角标固定放大一倍后，缩略图缩到最小档时角标显大、放到最大档时又显小；
**176px 档位下看起来刚刚好**，希望角标跟着缩略图一起缩放。

**改动（`views/widgets/thumbnail_grid.py::ThumbnailDelegate`）：**

角标尺寸不再写死，改为「基准尺寸 × 缩放系数」，基准取 `_REFERENCE_THUMB = 176`（即 `THUMB_LARGE`，
该档位下的数值正是上一轮确定的大小）：

| 角标 | 基准（176px） | 88px | 124px | 352px |
|------|--------------|------|-------|-------|
| 右上角「已标注」三角 | 22 | 11 | 15.5 | 44 |
| 右下角 T / V / E 方块 | 28 | 14 | 19.7 | 56 |
| ↳ 字母字号 | 12 | 6 | 8.5 | 24 |
| 左下角标记色点（间距） | 12（4） | 6（2） | 8.5（2.8） | 24（8） |

- 新增 `_badge_scale()` 与具名常量 `_REFERENCE_THUMB / _MIN_SCALE / _SUBSET_FONT / _BADGE_INSET / _BADGE_BOTTOM / _TEXT_RESERVE`
- 角标距卡片边缘的间距同样按系数缩放（下限 3px），圆角半径同步
- 角标底边距底边取 `max(文件名一行 + 6px, 30 × 系数)` —— 文件名文字高度不缩放，
  这样小图时角标不会压到文件名一行

**验收结果 ✅（offscreen 自检 15 项全通过）：**

| 检查组 | 要点 |
|--------|------|
| 缩放系数 | 88→0.5 / 124→0.705 / 176→1.0 / 352→2.0 |
| 基准档 | 176px 下三角 22、方块 28、色点 12（与上一轮确定值一致） |
| 像素级 | 距卡片右上角 (8,8) 的采样点：176px 为角标粉 `(227,0,140)`，88px 为卡片底色 `(43,43,43)` → 三角确实随档位变化 |
| 绘制 | 四档位均可正常绘制 |
| 布局 | 小图时角标底边留白 ≥ 文件名一行 + 6px |

**真实项目只读冒烟（`Test\异常检测测试.mprj`，83 张）：** 图库 / 检查 / 标注三页在
88 / 124 / 176 / 352 四档下绘制均正常，`已标注 83 / 83`，项目 `dirty = False`。

**说明与遗留 ⏳：**
- 只缩放三个角标；**文件名文字**仍是固定字号（卡片高度、文字矩形按固定尺寸排版，避免布局连锁改动）
- 缩放仍是「档位」步进，不做连续缩放

**下一步计划：** 按需补充相机采集、图库按导入批次分组显示。

---

## Session 30 — 下边两个角标改贴「缩略图框」下角 + info 通知只写日志

**日期：** 2026-09-19

**用户反馈（两个问题）：**
1. 「数据集标签（T/V/E）」与「图像标记（左侧色点）」没有贴着缩略图框的下边两个角，
   导致缩放后位置相对图像变动、并侵入图像影响查看；
2. 窗口里**蓝色图标**的通知（info 级）改为只写后台日志，不弹前端通知。

**改动一：角标锚点由「卡片」改为「缩略图框」（`views/widgets/thumbnail_grid.py`）**

- 新增 `ThumbnailDelegate._thumb_rect()`：缩略图框（图片显示区域）= 卡片内缩 5px、
  底部再让出 28px 给文件名一行；`paint()` 中缩略图与两个下角标都以此框为基准。
- 常量整理：`_BADGE_INSET / _THUMB_INSET / _THUMB_TEXT_RESERVE`，
  删除只服务于旧锚点的 `_BADGE_BOTTOM / _TEXT_RESERVE`。
- 「右下角 T/V/E 方块」距框右边、下边各 `inset`；「左下角色点」距框左边、下边各 `inset`；
  `inset = max(3px, 7px × 缩放系数)` → **相对位置不随档位漂移**，也不会挤到文件名一行。
- 「右上角已标注三角」保持挂在卡片右上角（不侵入图片显示区域，保持原样）。

各档位下角标位置（相对于缩略图框右下角）：

| 档位 | 缩放 | 方块尺寸 | 距框角间距 |
|------|------|---------|-----------|
| 88px | 0.5 | 14 | 3.5 |
| 124px | 0.705 | 19.7 | 4.9 |
| 176px | 1.0 | 28 | 7 |
| 352px | 2.0 | 56 | 14 |

**改动二：info 级通知只写后台日志（`views/main_window.py::_show_message`）**

- `info` 级（蓝色图标）**先写日志再直接返回**，不再弹 InfoBar；
  `success / warning / error` 行为不变（仍弹窗 + 写日志）。
- `views/annotate_vm.py`：「没有需要预标注的图片」由 info 改 **warning** ——
  这是「点了没反应」的关键反馈，降级为日志会丢可见反馈。
- 其它 info 消息（已选择模型 / 随机种子已设 / 缩略图尺寸 / 项目已关闭等）本身都有
  界面内反馈（选项、滑杆、状态文字），改日志后不影响操作判断。

**验收结果 ✅（offscreen 自检 24 项全通过）：**

| 检查组 | 要点 |
|--------|------|
| 角标贴框角 | 四档位扫描实际像素范围，方块贴框右下角、色点在框左下角，误差 ≤ 2.5px |
| 位置稳定 | 「距框角间距 ÷ 角标尺寸」四档位一致（相对位置不漂移） |
| 越界 | 角标不越出缩略图框、不压文件名一行 |
| 三角 | 右上角三角仍贴卡片角（88 / 176 两档同位置） |
| 通知 | info / notify() 默认 / info 级信号均不弹窗；success / warning / error 仍弹窗 |
| 日志 | 四档位消息都写入后台日志文件（`data/logs/deepmaven.log`） |

**真实项目只读冒烟（`Test\异常检测测试.mprj`，83 张）：** 图库 / 检查 / 标注三页在
88 / 124 / 176 / 352 四档下绘制均正常，`已标注 83 / 83`，项目 `dirty = False`。

**说明与遗留 ⏳：**
- 角标仍以 `176px` 为基准等比缩放（Session 29 结论不变）
- 若后续需要「蓝色通知」也能在界面看到，可加一个设置项开关（当前固定为只写日志）

**下一步计划：** 按需补充相机采集、图库按导入批次分组显示。

---

## Session 31 — 立「界面不放说明性文字」规范并全项目清理

**日期：** 2026-09-19

**用户反馈：** 很多功能的用法描述被写在前端上，界面不美观；要求总结成规范放到**账号级**
（本地所有项目通用）的开发规则里，并检测本项目后修正。

**一、规范已落到账号级长期知识（对所有项目生效）**

标题：`前端界面文案规范：界面不放说明性文字（所有项目通用）`（知识 id `58733369`），要点：

1. 界面只出现名词 / 短语标签（卡片标题、字段名、按钮名、选项名、表头），单个 ≤ 6 字；
2. 禁止把功能用法、操作步骤、注意事项写成整句放界面，禁止「（需安装 X）」「（可选）」等括注；
3. 空状态只用 ≤ 8 字短语（「尚未导入图像」「暂无标记」「—」），不写操作指引；
4. 卡片 / 面板里的 `CaptionLabel` 说明句一律删除；确需解释的参数放控件 tooltip（≤ 20 字，只讲含义）；
5. 反馈文案只讲结果与必要原因，一句 ≤ 20 字；成功后若界面已有其他反馈则不再发通知；
6. 确认弹窗：标题＝动作，正文一句话讲后果；
7. 新增界面文案前先自问「删掉是否影响使用」，能删就删，细节写进日志 / 文档 / tooltip。

同时在 `开发文档.md` 增补 `### 4.3 界面文案规范（不放说明性文字）`（含表格化示例），
作为本项目自己的落地依据。

**二、本项目检测与修正**

新增两个临时检测脚本（已删）：静态扫描（AST 找出界面层 ≥14 字的中文字面量）+
运行时扫描（真实项目打开后遍历各页可见控件与对话框，导出长文案）。

| 位置 | 处理 |
|------|------|
| `views/data_widgets.py` | 删除「导入文件夹」卡片说明句；「标签类别」卡片说明句改为空（该标签只做操作反馈） |
| `views/gallery_tab.py` | 空状态 →「尚未导入图像」；选中提示 →「已选 N / M 张」；移除确认只留「只移出图库，本地文件不会被删除。」 |
| `views/gallery_widgets.py` | 删除拆分卡片说明标签（含 `set_state` 的两处写入）；锁定提示 →「已用于训练，无法更改」；标记卡片 →「暂无标记」/ 空；标记 tooltip →「已挂 N 张 · 点击追加/删除 · 右键编辑」 |
| `views/review_tab.py` | 质检说明句删除；页头提示去掉「点击缩略图查看详情」；「不适用（分类任务…）」→「不适用」 |
| `views/split_tab.py` | 类别分布 / 数据增强说明句删除；空态 →「暂无数据」；汇总 →「共 N 个类别 · M 张」；增强的 4 条提示只留结果或原因 |
| `views/model_tab.py` | 权重占位 →「自定义权重 (.pt)」+ tooltip；模型信息空态 →「未导入权重」；变体说明不再重复下拉项里的描述 |
| `views/annotate_tab.py` | 模式提示、旋转提示、选中提示全部压到一句 ≤ 17 字 |
| 对话框 4 个 | 导入：复选框 →「跳过重复图像」+ tooltip、预览 →「插入右侧；跳过重复；首张 x.png」；新建项目：去括注、详情只留结构行；类别 / 标记编辑：说明句删除（标签只做校验反馈）、颜色错误 →「颜色值无效，形如 #66CCFF」 |
| `views/project_tab.py` | 删除 / 关闭确认正文压到一句话 |
| `utils/constants.py` | `PROJECT_TYPES` 的 `detail` / `note` 全部改为 ≤ 12 字短语；`EXPORT_FORMATS` 的 `desc` →「可继续训练 / 跨平台部署 / C++ 部署」 |
| 通知文案 | 移除「（可重新导入恢复）」「id 已重排，已有标注需核查」「下次拆分会输出到该目录」等后置说明 |

**三、验收结果 ✅**

- 静态扫描：界面层 ≥14 字中文文案由 **105 条 → 51 条**，剩余全部是文件过滤器、
  校验 / 错误通知、占位符、短 tooltip、结构化规格行（均符合规范）。
- 运行时扫描（真实项目 `Test\异常检测测试.mprj`，10 个页面 + 导入 / 类别 / 标记 / 新建项目
  4 个对话框）：可见长文案由 **13 条 → 12 条**，且逐条确认全部是**数据 / 状态**
  （文件路径、版本号、「共 83 张 · 已标注 83 张」、字段名「训练轮数 (epochs)：」等），
  不含任何说明性语句。
- 全部页面与对话框均可正常渲染，项目 `dirty = False`（只读验证，未改动用户文件）。
- lint：清理后 `src` 全目录 0 错误（顺带修正 `main_window.closeEvent` 参数名与基类不一致的
  Pylance 报错，与本任务无关的一行修正）。

**说明与遗留 ⏳：**
- 规则本体在账号级知识里；若希望某些界面保留解释性文案，需要显式说明（默认按规范删）
- 检测脚本为一次性工具，未纳入仓库（需要时可重新生成）

**下一步计划：** 按需补充相机采集、图库按导入批次分组显示。

---

## Session 32 — 修复「Ctrl + 滚轮缩放后尺寸滑杆不跟着走」

**日期：** 2026-09-19

**用户反馈：** 用 Ctrl + 滚轮缩放缩略图后，右上角「尺寸滑杆」的旋钮不动（截图：滑杆 + 「显示 83 / 83 张」）。

**一、根因（最小复现，已实测）**

`FilterBar.set_thumb_step()` / `FilterCard.set_thumb_step()` 用
`blockSignals(True) → setValue() → blockSignals(False)` 同步。
qfluentwidgets 的 `Slider` 是**靠自身 `valueChanged` 驱动 `SliderHandle` 位置**的，
一旦阻塞信号：数值变了、旋钮不动。

| 调用方式 | value=0 / 1 / 2 / 3 时旋钮 x |
|---------|------------------------------|
| 直接 `setValue(v)` | 0 / 59 / 118 / 178 ✅ 正常跟随 |
| `blockSignals` + `setValue(v)` | 178 / 178 / 178 / 178 ❌ 卡死不动 |
| `blockSignals` + `setValue` + `update()` | 仍为 178 ❌ 无效 |

**顺带发现（检查页）**：网格 4 档（88 / 124 / 176 / 352）而滑杆只有 3 档（0~2 硬编码），
且页面档位表只有 3 个值 → 缩到 88px 时 `index` 回退成 1，旋钮反而跳到中间；
初始态也不一致（网格 176、旋钮指 352）。

**二、修复**

1. `views/widgets/thumbnail_grid.py`：新增 `set_thumb_steps()` / `thumb_steps()` / `thumb_index()`，
   `zoom()` 改为使用**本网格自己的档位**（页面可收窄），Ctrl + 滚轮与滑杆共用同一套档位。
2. `views/gallery_widgets.py::FilterBar`、`views/data_widgets.py::FilterCard`：
   - 档位来自共享常量 `THUMB_STEPS`（不再各自硬编码档位数），新增 `set_thumb_steps()`；
   - `set_thumb_step(index)` → **`set_thumb_size(size)`**：按尺寸同步，页面不再自己算下标；
   - 同步时用短标志 `_syncing_thumb` 忽略本次回调，**不再用 `blockSignals`**（页面侧处理幂等，不会来回打架）；
   - 初始值取本页档位中的 `THUMB_MEDIUM` / `THUMB_LARGE`。
3. 页面：`gallery_tab` 直接用共享 `THUMB_STEPS`；`review_tab` 给网格与滑杆都
   `set_thumb_steps((124, 176, 352))` 并同步初始档位（该页不再能缩到 88px）。

**三、验收结果 ✅（offscreen 自检 12 项全通过，像素级）**

| 页面 | 网格尺寸 → 滑杆值 / 旋钮 x |
|------|---------------------------|
| 图库（4 档） | 88→0/0、124→1/22、176→2/45、352→3/68 |
| 检查（3 档） | 124→0/0、176→1/561、352→2/1123 |

- 旋钮位置随数值**单调变化**（真·跟着走）；滑杆值与网格尺寸一一对应；尺寸不超出本页档位；
  反向（拖滑杆 → 网格）正常；两页初始态一致。
- 全页冒烟：9 个页面渲染正常，标注页滚轮缩放 88→124px 正常（无滑杆），
  图库页往返缩放回落到 88px 且滑杆值 0，项目 `dirty = False`（只读验证）。
- lint：`src` 全目录 0 错误。

**四、同类风险排查（已实测，未改动）**

- `CheckBox`：`blockSignals` + `setChecked` 视觉正常 ✅ 无需处理。
- `ComboBox`：`blockSignals` + `setCurrentIndex` **显示文本不刷新** ❌（与滑杆同源）。
  涉及：`train_tab`（任务类型 / 模型变体下拉同步）、`annotate_tab`（类别下拉重建）、
  `gallery_widgets`（数据集拆分名称下拉）。建议后续同样改为标志位写法——**本次未动，
  待确认后再统一处理**。

**说明与遗留 ⏳：**
- 三页的尺寸档位现在以 `THUMB_STEPS` 为准，页面要收窄必须同时给网格与滑杆设同一套

**下一步计划：** 按需补充相机采集、图库按导入批次分组显示。

---