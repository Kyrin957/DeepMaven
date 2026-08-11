"""Model 层 —— 数据模型与状态。"""

# 数据模型均为纯 Python 数据结构（dataclass），不依赖任何 UI 框架，
# 便于单元测试与跨层复用。业务状态由 viewmodels 层持有并暴露信号。