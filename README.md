# DeepMaven

基于 **PySide6** 的深度学习缺陷检测系统，参考 Halcon Deep Learning Tool 的设计理念，面向工业缺陷检测（划痕、异物等）场景，提供从项目管理、数据管理、模型训练到模型评估与模型导出的完整工作流。

## 功能模块

| 模块 | 主要功能 | 当前状态 |
|------|---------|---------|
| 项目管理 | 新建/打开/保存单文件项目（`.mprj`）、最近项目列表 | 已实现 |
| 数据管理 | 数据集导入扫描、类别统计、训练/验证/测试划分 | 界面完成，划分落盘与缩略图预览待补 |
| 模型管理 | YOLO 变体选择（YOLO11 / YOLO26，n~x）、自定义权重导入 | 界面完成，模型信息探测待接入 |
| 模型训练 | 参数配置、启动/停止、进度与指标、训练日志 | 界面完成，训练线程待接入 |
| 模型评估 | 图片/视频/相机输入、实时检测、结果与报告导出 | 界面完成，推理与报告导出待接入 |
| 模型导出 | PT / ONNX / TorchScript 导出 | 服务层就绪，界面执行待接入 |

## 技术栈

| 组件 | 选型 |
|------|------|
| GUI 框架 | PySide6 6.11 + PySide6-Fluent-Widgets 1.11（Fluent Design，支持亮暗主题） |
| 深度学习 | PyTorch + Ultralytics（YOLO11 / YOLO26：检测 / 旋转框 / 实例分割 / 分类）、Anomalib（异常检测）、自研轻量 U-Net（语义分割） |
| 图像处理 | OpenCV |
| 项目存储 | 自研单文件 `.mprj` 容器（加密清单 + ZIP 载荷 + 完整性校验） |
| 可视化 | Matplotlib |
| 配置持久化 | QSettings（主题、最近项目等） |
| 架构模式 | MVVM |

## 环境要求

- Python 3.14+
- 依赖清单：`requirements.txt`（CPU 版）；如需 CUDA 加速（cu130），参考 `requirements-gpu.txt`
- 预训练模型：**已随程序提供在 `models/`**（YOLO11 / YOLO26 全系列、MobileSAM、异常检测骨干，
  约 2 GB），开发、测试与发布运行都无需联网下载；清单与更新方法见 `models/README.md`

## 安装

### 基础安装（CPU 版 PyTorch）

```powershell
py -3.14 -m venv .venv
.venv\Scripts\activate
python -m pip install -U pip
pip install -r requirements.txt
```

### GPU 加速（可选，NVIDIA 显卡）

`requirements.txt` 默认安装 CPU 版 PyTorch。如需 CUDA 加速，先卸载 CPU 版，再按 GPU 清单安装：

```powershell
pip uninstall torch torchvision -y
pip install -r requirements-gpu.txt
pip install -r requirements.txt
```

> GPU 版 wheel 体积约 2 GB，官方源在境外、下载缓慢；国内推荐改用阿里云镜像（附可直接复制的安装命令），见 `requirements-gpu.txt` 内注释。

### 预训练模型（新机器 / 新克隆）

约 2 GB 模型不入 git（`.gitignore` 已忽略 `models/` 下的权重），新电脑克隆后
运行一次脚本即可拉齐（可重复运行，已存在的文件自动跳过、缺失的补齐）：

```powershell
python scripts/download_models.py            # 默认走国内加速镜像
python scripts/download_models.py --direct   # 官方源直连（境外网络）
```

脚本会下载 YOLO11 / YOLO26 全系列与 MobileSAM，并经 HF 镜像缓存异常检测骨干；
清单与手工下载方式见 `models/README.md`。

> 注意：Fluent 组件库必须使用 **PySide6 后端**的 `PySide6-Fluent-Widgets`。它与 PyQt5 后端的 `PyQt-Fluent-Widgets` 共用 `qfluentwidgets` 模块名，二者不可共存，误装会导致启动崩溃。

## 运行

```powershell
python main.py
```

## 项目结构

```
DeepMaven/
├── main.py                  # 应用入口
├── requirements.txt         # CPU 依赖清单
├── requirements-gpu.txt     # CUDA 依赖清单（可选）
├── 开发文档.md              # 开发方案与实施步骤
├── README.md
└── src/
    ├── app.py               # QApplication 启动、主题与配置初始化
    ├── models/              # 数据模型：Project / Dataset / TrainingConfig / ProjectFile
    ├── viewmodels/          # 业务逻辑：6 个 ViewModel（project / dataset / model / train / evaluate / export）
    ├── views/               # 界面：main_window + base_page + 6 个导航页
    ├── services/            # 服务：project_format / project_service / dataset_service / yolo_service / export_service
    │   └── backends/        # 后端适配层：base / yolo / anomalib（按任务查表取后端）
    └── utils/               # 工具：constants / tasks（任务注册表）/ config / logger
```

## 项目文件格式（`.mprj`）

项目以单一 `.mprj` 文件保存，内部为「加密 JSON 清单 + ZIP 载荷」结构，具备魔数识别、条目名混淆、逐文件 SHA256 完整性校验与格式版本守卫。格式细节见《开发文档.md》§6.4。

## 相关文档

| 文档 | 内容 |
|------|------|
| `开发文档.md` | 项目概述、技术选型、架构设计、界面布局、分步实施计划 |
| `requirements.txt` / `requirements-gpu.txt` | 依赖清单与安装说明 |
