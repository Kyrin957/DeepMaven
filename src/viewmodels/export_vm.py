"""模型导出 ViewModel：模型/优化选项选择、后台导出与模型报告生成。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from src.models.training import ExportConfig
from src.services.evaluation_service import FP_REASON_LABELS
from src.services.export_service import ExportService
from src.services.report_service import ModelReport
from src.utils.constants import (
    EXPORT_FORMATS,
    PROJECT_TYPES,
    SPLIT_LABELS,
    TASK_MODEL_SUFFIX,
    YOLO_MODEL_VARIANTS,
)
from src.utils.device import device_label
from src.utils.logger import get_logger
from src.utils.workers import FunctionWorker
from src.viewmodels.project_vm import ProjectViewModel

logger = get_logger("export_vm")

# 导出记录保留条数
_HISTORY_LIMIT = 20


def _format_label(key: str) -> str:
    item = next((fmt for fmt in EXPORT_FORMATS if fmt["key"] == key), None)
    return str((item or {}).get("label") or key)


def _task_label(model_type: str) -> str:
    item = next((t for t in PROJECT_TYPES if t["key"] == model_type), None)
    return str((item or {}).get("label") or model_type)


def _variant_label(model_key: str) -> str:
    """模型变体显示名（分类 / 分割等带后缀时回退到原名）。"""
    key = str(model_key or "")
    for suffix in ("-cls", "-seg", "-obb"):
        if key.endswith(suffix):
            base = next((v for v in YOLO_MODEL_VARIANTS
                         if key.startswith(v["key"])), None)
            return f"{base['label']}{suffix}" if base else key
    item = next((v for v in YOLO_MODEL_VARIANTS if v["key"] == key), None)
    return str((item or {}).get("label") or key or "—")


def _size_mb(path: str | Path) -> float:
    try:
        return Path(path).stat().st_size / (1024 * 1024)
    except OSError:
        return 0.0


class ExportViewModel(QObject):
    """模型导出页的业务逻辑。"""

    configChanged = Signal(object)       # ExportConfig
    selectionChanged = Signal(dict)      # 当前导出对象（模型）信息
    historyChanged = Signal(list)        # 导出记录
    exportStarted = Signal()
    exportFinished = Signal(str)         # 导出文件路径
    reportFinished = Signal(str)         # 报告文件路径
    taskStarted = Signal(str)
    taskProgress = Signal(int, str)
    taskFinished = Signal(str)
    taskFailed = Signal(str)
    message = Signal(str, str)           # level, text

    def __init__(
        self,
        project_vm: ProjectViewModel | None = None,
        evaluate_vm: QObject | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._project_vm = project_vm
        self._evaluate_vm = evaluate_vm
        self._config = ExportConfig(output_dir="runs/export")
        self._worker: FunctionWorker | None = None

    def load_from_project(self, project) -> None:
        """打开 / 新建项目后把导出参数绑定到项目。"""
        self._config = (
            project.export if project is not None
            else ExportConfig(output_dir="runs/export")
        )
        self.configChanged.emit(self._config)
        self.selectionChanged.emit(self.selection())
        self.historyChanged.emit(self.export_history())

    @property
    def project(self):
        """当前项目（未打开项目时为 None）。"""
        return self._project_vm.project if self._project_vm else None

    def _emit(self) -> None:
        """配置变更：标记项目待保存并广播。"""
        project = self._project_vm.project if self._project_vm else None
        if project is not None and self._config is project.export:
            project.touch()
        self.configChanged.emit(self._config)

    @property
    def config(self) -> ExportConfig:
        return self._config

    def is_busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    # -----------------------------------------------------------
    # 配置
    # -----------------------------------------------------------
    def set_format(self, fmt: str) -> None:
        self._config.format = fmt
        self._emit()

    def set_weights(self, path: str) -> None:
        self._config.weights_path = path
        self._emit()
        self.selectionChanged.emit(self.selection())

    def set_for_inference(self, enabled: bool) -> None:
        """针对推断优化：关闭动态尺寸并启用图精简。"""
        self._config.for_inference = bool(enabled)
        self._emit()

    def set_for_api(self, enabled: bool) -> None:
        """针对 API 接口优化：导出支持变尺寸 / 变批量输入的模型。"""
        self._config.for_api = bool(enabled)
        self._emit()

    def set_half(self, enabled: bool) -> None:
        """半精度（FP16）导出：体积更小 / 更快，但需要 GPU 运行时。"""
        self._config.half = bool(enabled)
        self._emit()

    # -----------------------------------------------------------
    # 导出对象（模型）与项目概览
    # -----------------------------------------------------------
    def records(self) -> list[dict]:
        """可导出的训练记录（新 → 旧）。"""
        project = self.project
        history = list(getattr(project.training, "history", []) or []) if project else []
        records: list[dict] = []
        for record in reversed(history):
            weights = str(record.get("best_weights") or "")
            if not weights:
                continue
            value = float(record.get("best_value") or 0)
            records.append({
                **record,
                "weights": weights,
                "exists": Path(weights).is_file(),
                "label": (
                    f"{record.get('name') or '--'} · "
                    f"{record.get('best_label') or '指标'} {value:.3f}"
                ),
            })
        return records

    def select_record(self, index: int) -> bool:
        """选中某次训练的产物作为导出对象。"""
        records = self.records()
        if not (0 <= index < len(records)):
            return False
        self._config.weights_path = records[index]["weights"]
        self._emit()
        self.selectionChanged.emit(self.selection())
        return True

    def selection(self) -> dict:
        """当前导出对象的信息（供「模型概览」展示）。"""
        project = self.project
        weights = str(self._config.weights_path or "")
        record = next(
            (item for item in self.records() if item["weights"] == weights), None
        )
        training = getattr(project, "training", None) if project is not None else None
        exists = bool(weights) and Path(weights).is_file()
        model_key = str((record or {}).get("model") or getattr(training, "model_key", ""))
        task = str((record or {}).get("task") or getattr(training, "task_type", ""))
        if task and not model_key.endswith(TASK_MODEL_SUFFIX.get(task, "")):
            model_key = f"{model_key}{TASK_MODEL_SUFFIX.get(task, '')}"
        return {
            "weights": weights,
            "exists": exists,
            "name": str((record or {}).get("name") or (Path(weights).stem if weights else "")),
            "source": "训练记录" if record else ("手动选择" if weights else "未选择"),
            "variant": _variant_label(model_key),
            "task": _task_label(task) if task else "—",
            "imgsz": int(getattr(training, "imgsz", 0) or 0),
            "device": device_label(str(getattr(training, "device", "")) or "auto"),
            "epochs": (record or {}).get("total") or getattr(training, "epochs", 0),
            "best_label": str((record or {}).get("best_label") or ""),
            "best_value": float((record or {}).get("best_value") or 0.0),
            "best_epoch": (record or {}).get("best_epoch") or 0,
            "split_name": str(
                (record or {}).get("split_name")
                or getattr(training, "split_name", "")
                or ""
            ),
            "size_mb": _size_mb(weights) if exists else 0.0,
        }

    def split_summary(self) -> dict:
        """数据拆分分布（优先用选中模型所用那一套拆分）。"""
        project = self.project
        if project is None:
            return {}
        record = next(
            (item for item in self.records()
             if item["weights"] == self._config.weights_path), None
        )
        split = None
        if record is not None and record.get("split_id") is not None:
            split = project.split_by_id(int(record.get("split_id") or 0))
        split = split or project.active_split()
        if split is None:
            return {}
        counts = {key: int((split.counts or {}).get(key, 0) or 0)
                  for key in ("train", "val", "test")}
        return {
            "name": split.name,
            "counts": counts,
            "total": sum(counts.values()),
        }

    def evaluation(self) -> dict:
        """最近一次评估结果（在评估页完成，未评估则为不可用）。"""
        result = getattr(self._evaluate_vm, "eval_result", None)
        result = result if isinstance(result, dict) else {}
        rows = result.get("rows") or []
        if not rows:
            return {"available": False}
        config = getattr(self._evaluate_vm, "config", None)
        metrics = result.get("metrics") or {}
        subset = str(getattr(config, "eval_subset", "") or "")
        split_name = str(getattr(config, "eval_split_name", "") or "")
        label = " · ".join(
            part for part in (
                split_name or "目录",
                SPLIT_LABELS.get(subset, subset),
                f"{len(rows)} 张",
            ) if part
        )
        return {
            "available": True,
            "label": label,
            "subset": subset,
            "split_name": split_name,
            "class_names": list(result.get("class_names") or []),
            "matrix": list(result.get("matrix") or []),
            "with_background": bool(result.get("with_background")),
            "metrics": metrics,
            "per_class": list(metrics.get("per_class") or []),
            "samples": self._evaluation_samples(result, rows),
        }

    @staticmethod
    def _evaluation_samples(result: dict, rows: list, limit: int = 3) -> list[dict]:
        """报告用的样本图：正确 / 误检 / 漏检 各取前若干张（优先带可视化）。"""
        correct: list[dict] = []
        wrong: list[dict] = []
        missed: list[dict] = []
        for row in rows:
            path = str(
                row.get("overlay") or row.get("annotated") or row.get("path") or ""
            )
            if not path:
                continue
            caption = (
                f"{row.get('name') or ''} · 真实 {row.get('label') or '—'}"
                f" · 预测 {row.get('pred_label') or '—'}"
            )
            if "pairs" not in row:      # 分类 / 异常：整图判定
                target = correct if row.get("correct") else wrong
                target.append({"kind": "correct" if row.get("correct") else "fp",
                               "path": path, "caption": caption})
                continue
            if row.get("fps"):
                reasons = "、".join(sorted({
                    FP_REASON_LABELS.get(str(item.get("reason")), "误检")
                    for item in (row.get("fp_details") or [])
                }))
                wrong.append({
                    "kind": "fp", "path": path,
                    "caption": f"{caption} · 误检（{reasons}）",
                })
            if row.get("fns"):
                missed.append({
                    "kind": "fn", "path": path,
                    "caption": f"{caption} · 漏检 {int(row.get('fns') or 0)} 个",
                })
            if row.get("correct"):
                correct.append({"kind": "correct", "path": path, "caption": caption})
        return correct[:limit] + wrong[:limit] + missed[:limit]

    def export_history(self) -> list[dict]:
        return list(self._config.history or [])

    def latest_export(self) -> dict:
        history = self.export_history()
        return history[-1] if history else {}

    # -----------------------------------------------------------
    # 模型报告
    # -----------------------------------------------------------
    def report_context(self) -> dict:
        """组装报告上下文（供 `ModelReport` 排版）。"""
        project = self.project
        selection = self.selection()
        split = self.split_summary()
        evaluation = self.evaluation()
        latest = self.latest_export()
        return {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "project": {
                "name": getattr(project, "name", "") or "",
                "task": _task_label(getattr(project, "model_type", "") or ""),
                "model_type": getattr(project, "model_type", "") or "",
                "classes": list(getattr(project, "class_names", []) or []),
                "created_at": getattr(project, "created_at", "") or "",
                "updated_at": getattr(project, "updated_at", "") or "",
                "app_version": getattr(project, "app_version", "") or "",
            },
            "model": {
                **selection,
                "split": split.get("name", ""),
            },
            "split": {
                "name": split.get("name", ""),
                "counts": split.get("counts", {}),
                "total": split.get("total", 0),
            },
            "evaluation": evaluation,
            "export": {
                "format": _format_label(str(latest.get("format") or self._config.format)),
                "path": str(latest.get("path") or ""),
                "size_mb": float(latest.get("size") or 0.0),
                "time": str(latest.get("time") or ""),
                "for_inference": bool(self._config.for_inference),
                "for_api": bool(self._config.for_api),
            },
        }

    def build_report(self, path: str) -> None:
        """生成模型报告（HTML / Markdown）。"""
        project = self.project
        if project is None:
            self.message.emit("warning", "请先创建或打开项目")
            return
        try:
            written = ModelReport.write(path, self.report_context())
        except Exception as exc:  # noqa: BLE001 - 文件写入异常类型较多
            logger.warning("生成模型报告失败: %s", exc)
            self.message.emit("error", f"生成报告失败：{exc}")
            return
        self.reportFinished.emit(str(written))
        self.message.emit("success", f"报告已生成：{Path(written).name}")

    def set_output_dir(self, path: str) -> None:
        self._config.output_dir = path
        self._emit()

    def set_imgsz(self, value: int) -> None:
        self._config.imgsz = int(value)
        self._emit()

    def set_opset(self, value: int) -> None:
        self._config.opset = int(value)
        self._emit()

    def set_dynamic(self, enabled: bool) -> None:
        self._config.dynamic = bool(enabled)
        self._emit()

    def set_simplify(self, enabled: bool) -> None:
        self._config.simplify = bool(enabled)
        self._emit()

    # -----------------------------------------------------------
    # 执行
    # -----------------------------------------------------------
    def export(self) -> None:
        """在后台线程执行模型导出。"""
        if self.is_busy():
            self.message.emit("warning", "已有任务在运行")
            return
        if not self._config.weights_path:
            self.message.emit("warning", "请先选择待导出的模型")
            return
        if not Path(self._config.weights_path).is_file():
            self.message.emit("error", "权重文件不存在")
            return

        config = self._config
        config.apply_options()      # 把「优化方向」换算成 dynamic / simplify
        self.exportStarted.emit()

        def job(progress, _is_cancelled):
            progress(10, f"导出为 {_format_label(config.format)}")
            return ExportService().export(config)

        def done(path):
            self._remember_export(str(path))
            self.exportFinished.emit(str(path))
            self.message.emit("success", f"导出完成：{Path(str(path)).name}")

        self._start_worker(job, "模型导出", on_done=done)

    def _remember_export(self, path: str) -> None:
        """记录一次导出（时间 / 格式 / 路径 / 大小）。"""
        self._config.history.append({
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "format": str(self._config.format),
            "path": str(path),
            "size": round(_size_mb(path), 3),
            "for_inference": bool(self._config.for_inference),
            "for_api": bool(self._config.for_api),
        })
        if len(self._config.history) > _HISTORY_LIMIT:
            del self._config.history[:-_HISTORY_LIMIT]
        self._emit()
        self.historyChanged.emit(self.export_history())

    # -----------------------------------------------------------
    # 内部
    # -----------------------------------------------------------
    def _start_worker(self, job, name: str, on_done=None) -> None:
        worker = FunctionWorker(job, self)
        worker.progress.connect(self.taskProgress.emit)
        worker.finishedOk.connect(
            lambda payload: self._on_task_done(name, payload, on_done)
        )
        worker.failed.connect(lambda error: self._on_task_failed(name, error))
        self._worker = worker
        self.taskStarted.emit(name)
        worker.start()

    def _on_task_done(self, name: str, payload, on_done) -> None:
        if on_done is not None:
            try:
                on_done(payload)
            except Exception as exc:  # noqa: BLE001 - 结果应用异常需回传界面
                logger.warning("%s结果应用失败: %s", name, exc)
                self.taskFailed.emit(f"{name}失败：{exc}")
                return
            self.taskFinished.emit(f"{name}完成")
        else:
            self.taskFinished.emit(str(payload) if payload else f"{name}完成")

    def _on_task_failed(self, name: str, error: str) -> None:
        logger.warning("%s失败: %s", name, error)
        self.taskFailed.emit(f"{name}失败：{error}")
