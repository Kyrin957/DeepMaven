"""DeepMaven —— 基于 PySide6 的深度学习缺陷检测系统。

采用 MVVM 架构：
    models/      Model 层 —— 数据模型与状态
    viewmodels/  ViewModel 层 —— 业务逻辑，连接 Model 与 View
    views/       View 层 —— Fluent Design 风格界面
    services/    Service 层 —— YOLO 训练/推理、数据集处理、模型导出等核心能力
    utils/       工具类 —— 日志、配置、常量
"""

__version__ = "0.1.0"
__app_name__ = "DeepMaven"