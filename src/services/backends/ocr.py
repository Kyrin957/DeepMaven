"""PaddleOCR 后端适配器（OCR）。

第 3 期现状：**数据链路已就绪**——文本框 + 转写标注（`Annotation.text`）、
det / rec 数据集产物（`DatasetService._write_ocr_products`）、识别质量指标
（`services/ocr_metrics.py`）；**训练与导出尚未接入**，因为 PaddleOCR 需要
PaddlePaddle 运行时，接入与验证需在有该环境的一侧进行（见《开发文档.md》§8.12）。

因此 `validate()` 会明确拦住训练请求并说明缺什么——**不会静默回退到 YOLO**
（那会用错误的训练路径消耗用户数据）。
"""

from __future__ import annotations

import importlib.util

from src.models.training import TrainingConfig
from src.services.backends.base import BackendAdapter, EnvReport, Problem

# OCR 需要的运行时（paddle 为引擎，paddleocr 为套件）
_REQUIRED = ("paddle", "paddleocr")


class OcrBackend(BackendAdapter):
    """PaddleOCR（文本检测 / 识别）。"""

    key = "ocr"
    label = "PaddleOCR"
    tasks = ("ocr",)

    # -----------------------------------------------------------
    # 环境
    # -----------------------------------------------------------
    def _missing(self) -> list[str]:
        missing: list[str] = []
        for name in _REQUIRED:
            try:
                if importlib.util.find_spec(name) is None:
                    missing.append(name)
            except (ImportError, ValueError):
                missing.append(name)
        return missing

    def is_available(self) -> bool:
        return not self._missing()

    def check_env(self) -> EnvReport:
        missing = self._missing()
        return EnvReport(
            key=self.key,
            label=self.label,
            available=not missing,
            missing=tuple(missing),
            detail="" if not missing else "未安装 " + "、".join(missing),
        )

    # -----------------------------------------------------------
    # 训练
    # -----------------------------------------------------------
    def validate(self, config: TrainingConfig) -> Problem | None:
        env = self.check_env()
        if not env.available:
            names = "、".join(env.missing)
            return Problem(
                message=f"OCR 训练需要 PaddleOCR 运行时（缺少 {names}）",
                hint=f"当前环境缺少 {names}",
            )
        return Problem(
            message="OCR 训练入口尚未接入（det / rec 数据集已就绪，可导出后交给 PaddleOCR 训练）",
            hint="OCR 训练尚未接入（数据集已就绪）",
        )

    def build_args(self, config: TrainingConfig) -> list[str]:
        raise NotImplementedError("OCR 训练入口尚未接入（见《开发文档.md》§8.12）")

    def candidates(self, task: str) -> list[dict]:
        """OCR 的子模型：文本检测与文本识别分别训练。"""
        return [
            {"key": "det", "label": "文本检测 (det)"},
            {"key": "rec", "label": "文本识别 (rec)"},
        ]

    # -----------------------------------------------------------
    # 导出
    # -----------------------------------------------------------
    def export_options(self) -> list[dict]:
        # 子模型导出（det / rec + 字典）随训练入口一起接入
        return []
