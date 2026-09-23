"""模型评估 ViewModel：图片 / 视频 / 相机检测与报告导出。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from src.models.training import EvaluationConfig
from src.services.anomalib_service import AnomalibService
from src.services.evaluation_service import EvaluationService
from src.services.inference_service import InferenceService
from src.services.report_service import ReportService
from src.services.segmentation_service import SegmentationService
from src.utils.constants import DATA_DIR, SPLIT_LABELS
from src.utils.image_ops import overlay_heatmap
from src.utils.logger import get_logger
from src.utils.tasks import eval_views as _eval_views
from src.utils.tasks import task_of as _task_of
from src.utils.workers import FunctionWorker
from src.viewmodels.project_vm import ProjectViewModel

logger = get_logger("evaluate_vm")

# 评估可视化（result.plot() 落盘）的缓存目录
_EVAL_PLOT_DIR = DATA_DIR / "eval"


def _sort_eval_rows(rows: list, key: str) -> list:
    """按排序键整理评估行（稳定排序，默认保持原始顺序）。"""
    key = str(key or "order")
    if key == "conf_desc":
        return sorted(rows, key=lambda row: -float(row.get("confidence") or 0.0))
    if key == "conf_asc":
        return sorted(rows, key=lambda row: float(row.get("confidence") or 0.0))
    if key == "iou_asc":
        return sorted(rows, key=lambda row: float(row.get("iou") or 0.0))
    if key == "wrong_first":
        return sorted(rows, key=lambda row: bool(row.get("correct")))
    return list(rows)


class EvaluateViewModel(QObject):
    """模型评估页的业务逻辑。"""

    configChanged = Signal(object)       # EvaluationConfig
    resultReady = Signal(dict)           # {annotated, records, count, elapsed}
    cameraFrame = Signal(object, list)   # 相机实时：标注图, 检测记录
    evaluationReady = Signal(dict)       # 数据集评估：行记录 / 混淆矩阵 / 指标
    rowSelected = Signal(dict)           # 当前评估记录（供图像详情展示）
    taskStarted = Signal(str)
    taskProgress = Signal(int, str)
    taskFinished = Signal(str)
    taskFailed = Signal(str)
    message = Signal(str, str)           # level, text

    def __init__(
        self,
        project_vm: ProjectViewModel | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._config = EvaluationConfig()
        self._project_vm = project_vm
        self._worker: FunctionWorker | None = None
        self._records: list = []
        self._eval: dict = {}            # 最近一次数据集评估结果
        self._eval_index: int = -1       # 当前查看的记录下标

    # -----------------------------------------------------------
    # 查询
    # -----------------------------------------------------------
    @property
    def config(self) -> EvaluationConfig:
        return self._config

    @property
    def project(self):
        """当前项目（未打开项目时为 None）。"""
        return self._project_vm.project if self._project_vm else None

    @property
    def records(self) -> list:
        return self._records

    def is_busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def load_from_project(self, project) -> None:
        """打开 / 新建项目后把评估参数绑定到项目。"""
        self._config = (
            project.evaluation if project is not None else EvaluationConfig()
        )
        # 项目切换后上一次的评估结果不再对应，清空避免误读
        self._eval = {}
        self._eval_index = -1
        self.evaluationReady.emit({})
        self.configChanged.emit(self._config)

    def _emit(self) -> None:
        """配置变更：标记项目待保存并广播。"""
        project = self._project_vm.project if self._project_vm else None
        if project is not None and self._config is project.evaluation:
            project.touch()
        self.configChanged.emit(self._config)

    # -----------------------------------------------------------
    # 配置
    # -----------------------------------------------------------
    def set_source(self, source_type: str) -> None:
        self._config.source_type = source_type
        self._emit()

    def set_weights(self, path: str) -> None:
        self._config.weights_path = path
        self._emit()

    def set_source_path(self, path: str) -> None:
        self._config.source_path = path
        self._emit()

    def set_thresholds(self, confidence: float, iou: float) -> None:
        self._config.confidence = confidence
        self._config.iou = iou
        self._emit()

    def set_device(self, device: str) -> None:
        self._config.device = device
        self._emit()

    def set_anomaly_model(self, name: str) -> None:
        if name:
            self._config.anomaly_model = name
            self._emit()

    # -----------------------------------------------------------
    # 数据集评估（带真实标签 → 指标 + 混淆矩阵）
    # -----------------------------------------------------------
    def eval_sources(self) -> list[dict]:
        """可用的评估集：项目内各拆分中「已生成且有图片」的 train / val / test。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return []
        sources: list[dict] = []
        for split in project.splits:
            if not split.ready:
                continue
            for subset in ("train", "val", "test"):
                count = int((split.counts or {}).get(subset, 0) or 0)
                if not count:
                    continue
                sources.append({
                    "split_id": split.split_id,
                    "split_name": split.name,
                    "subset": subset,
                    "count": count,
                    "label": f"{split.name} · {SPLIT_LABELS[subset]} · {count} 张",
                })
        return sources

    def set_eval_source(self, split_id: int, subset: str) -> None:
        """选择评估集（项目拆分 + 子集）；选择后清空自定义目录。"""
        self._config.eval_split_id = int(split_id)
        self._config.eval_subset = str(subset)
        self._config.eval_subsets = [str(subset)] if subset else []
        self._config.eval_folder = ""
        project = self._project_vm.project if self._project_vm else None
        split = project.split_by_id(int(split_id)) if project is not None else None
        self._config.eval_split_name = split.name if split is not None else ""
        self._emit()

    def eval_splits(self) -> list[dict]:
        """可评估的拆分（已生成且有图片），供「数据拆分」下拉使用。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return []
        result: list[dict] = []
        for split in project.splits:
            if not split.ready:
                continue
            counts = {
                key: int((split.counts or {}).get(key, 0) or 0)
                for key in ("train", "val", "test")
            }
            total = sum(counts.values())
            if not total:
                continue
            result.append({
                "split_id": split.split_id,
                "split_name": split.name,
                "counts": counts,
                "total": total,
                "label": f"{split.name} · {total} 张",
            })
        return result

    def eval_subsets(self) -> list[str]:
        """当前勾选的评估子集（默认验证集）。"""
        return list(self._config.eval_subsets) or [self._config.eval_subset or "val"]

    def set_eval_split(self, split_id: int) -> None:
        """选择评估所用的拆分（子集由多选框决定）。"""
        self._config.eval_split_id = int(split_id)
        self._config.eval_folder = ""
        project = self._project_vm.project if self._project_vm else None
        split = project.split_by_id(int(split_id)) if project is not None else None
        self._config.eval_split_name = split.name if split is not None else ""
        self._emit()

    def set_eval_subsets(self, names: list) -> None:
        """多选评估子集（训练 / 验证 / 测试）；选择后清空自定义目录。"""
        values = [str(item) for item in (names or []) if str(item)]
        if not values:
            return
        self._config.eval_subsets = values
        self._config.eval_subset = values[0]
        self._config.eval_folder = ""
        self._emit()

    def set_eval_random(self, enabled: bool) -> None:
        """上限内是否随机抽样（默认按顺序取前 N 张）。"""
        self._config.eval_random = bool(enabled)
        self._emit()

    def set_eval_seed(self, seed: int) -> None:
        self._config.eval_seed = int(seed)
        self._emit()

    def set_eval_folder(self, path: str) -> None:
        """直接指定评估目录（子目录名 = 真实类别）。"""
        self._config.eval_folder = str(path).strip()
        self._emit()

    def set_max_images(self, count: int) -> None:
        self._config.max_images = max(1, int(count))
        self._emit()

    # -----------------------------------------------------------
    # 结果排序 / 实例级视图
    # -----------------------------------------------------------
    def set_eval_sort(self, key: str) -> None:
        """设置结果排序：order / conf_desc / conf_asc / iou_asc / wrong_first。"""
        self._config.eval_sort = str(key or "order")
        self._sort_rows()
        self._emit()

    def eval_sort(self) -> str:
        return str(self._config.eval_sort or "order")

    def _sort_rows(self) -> None:
        """按当前排序重建行列表（保持当前选中行不变）。"""
        raw = self._eval.get("raw_rows")
        if raw is None:
            return
        rows = list(raw)
        current = None
        if 0 <= self._eval_index < len(self._eval.get("rows") or []):
            current = self._eval["rows"][self._eval_index]
        ordered = _sort_eval_rows(rows, self._config.eval_sort)
        self._eval["rows"] = ordered
        self._eval["instances"] = []          # 排序变了，实例视图需重建
        self._eval_index = (
            ordered.index(current) if current is not None and current in ordered else -1
        )

    def build_instances(self) -> None:
        """生成实例级条目（每个真实框 / 预测框一条，含裁剪小图）。"""
        rows = self._eval.get("rows") or []
        names = list(self._eval.get("class_names") or [])
        items: list[dict] = []
        for row_index, row in enumerate(rows):
            if "pairs" not in row or row.get("error"):
                continue
            path = str(row.get("path") or "")
            stem = Path(path).stem
            matched = dict(row.get("matches") or [])
            matched_preds = set(matched.values())
            fp_details = {
                int(detail.get("index", -1)): detail
                for detail in (row.get("fp_details") or [])
            }
            for gt_index, gt in enumerate(row.get("gt_boxes") or []):
                kind = "tp" if gt_index in matched else "fn"
                items.append({
                    "row": row_index, "kind": kind, "reason": "",
                    "class_name": str(gt.get("class_name") or ""),
                    "confidence": 1.0, "iou": 1.0 if kind == "tp" else 0.0,
                    "box": [gt.get("x1"), gt.get("y1"), gt.get("x2"), gt.get("y2")],
                    "path": EvaluationService.crop_instance(
                        path,
                        (gt.get("x1"), gt.get("y1"), gt.get("x2"), gt.get("y2")),
                        _EVAL_PLOT_DIR / f"inst_{stem}_gt{gt_index}.jpg",
                    ),
                    "name": f"{row.get('name')} · GT#{gt_index + 1}",
                    "label": str(gt.get("class_name") or ""),
                    "correct": kind == "tp",
                })
            for pred_index, pred in enumerate(row.get("detections") or []):
                detail = fp_details.get(pred_index) or {}
                if pred_index in matched_preds:
                    kind, reason = "tp", ""
                else:
                    kind, reason = "fp", str(detail.get("reason") or "")
                box = (
                    pred.get("x1"), pred.get("y1"), pred.get("x2"), pred.get("y2")
                )
                label = str(pred.get("class_name") or "")
                items.append({
                    "row": row_index, "kind": kind, "reason": reason,
                    "class_name": label,
                    "confidence": float(pred.get("confidence") or 0.0),
                    "iou": float(detail.get("iou") or (1.0 if kind == "tp" else 0.0)),
                    "box": [box[0], box[1], box[2], box[3]],
                    "path": EvaluationService.crop_instance(
                        path, box, _EVAL_PLOT_DIR / f"inst_{stem}_pd{pred_index}.jpg"
                    ),
                    "name": f"{row.get('name')} · 预测#{pred_index + 1}",
                    "label": label,
                    "correct": kind == "tp",
                })
        self._eval["instances"] = items

    def instance_items(self) -> list[dict]:
        """实例级视图数据（首次访问时生成裁剪小图）。"""
        if not self._eval.get("instances"):
            self.build_instances()
        return list(self._eval.get("instances") or [])

    def has_instances(self) -> bool:
        """当前评估结果是否支持实例级视图（检测族有框级配对）。"""
        return any("pairs" in row for row in (self._eval.get("rows") or []))

    # -----------------------------------------------------------
    # 异常检测后处理（分数直方图 / 分类阈值 / 分数容忍度）
    # -----------------------------------------------------------
    def is_anomaly_result(self) -> bool:
        """当前结果是否来自异常检测评估。"""
        return str(self._eval.get("task") or "") == "anomaly"

    def eval_imgsz(self) -> int:
        """评估输入尺寸（取项目训练配置，缺省 640），供预处理对比图使用。"""
        project = self._project_vm.project if self._project_vm else None
        training = getattr(project, "training", None)
        return int(getattr(training, "imgsz", 0) or 640)

    def anomaly_scores(self) -> list[dict]:
        """异常分数序列（供直方图）：{"score", "abnormal"（真实标签）}。"""
        rows = self._eval.get("raw_rows") or self._eval.get("rows") or []
        return [
            {
                "score": float(row.get("confidence") or 0.0),
                "abnormal": int(row.get("true_id") or 0) == 1,
            }
            for row in rows
        ]

    def anomaly_median(self) -> float:
        """中位阈值：异常分数的中位数（DLT 的默认分割点）。"""
        values = sorted(item["score"] for item in self.anomaly_scores())
        if not values:
            return 0.0
        middle = len(values) // 2
        if len(values) % 2:
            return round(values[middle], 4)
        return round((values[middle - 1] + values[middle]) / 2.0, 4)

    def anomaly_best_threshold(self) -> float:
        """最优阈值：以真实标签为基准搜索 F1 最大的分割点。"""
        scores = self.anomaly_scores()
        if not scores:
            return 0.0
        abnormal = sum(1 for item in scores if item["abnormal"])
        if not abnormal or abnormal == len(scores):
            return self.anomaly_median()
        best, best_f1 = scores[0]["score"], -1.0
        for candidate in sorted({item["score"] for item in scores}):
            tp = sum(
                1 for item in scores
                if item["abnormal"] and item["score"] >= candidate
            )
            fp = sum(
                1 for item in scores
                if not item["abnormal"] and item["score"] >= candidate
            )
            fn = abnormal - tp
            precision = tp / (tp + fp) if (tp + fp) else 0.0
            recall = tp / (tp + fn) if (tp + fn) else 0.0
            f1 = (
                2 * precision * recall / (precision + recall)
                if (precision + recall) else 0.0
            )
            if f1 > best_f1:
                best, best_f1 = candidate, f1
        return round(best, 4)

    def anomaly_threshold(self) -> float:
        """当前分类阈值（未设置时用中位阈值）。"""
        value = float(getattr(self._config, "anomaly_threshold", 0.0) or 0.0)
        return value if value > 0 else self.anomaly_median()

    def anomaly_tolerance(self) -> float:
        return float(getattr(self._config, "anomaly_tolerance", 0.0) or 0.0)

    def set_anomaly_threshold(self, value: float) -> None:
        """调整分类阈值并立即重判（分数不变，只改判定）。"""
        self._config.anomaly_threshold = max(0.0, float(value))
        self._apply_anomaly_decision()
        self._emit()

    def use_median_threshold(self) -> None:
        """回到中位阈值（清除自定义阈值）。"""
        self._config.anomaly_threshold = 0.0
        self._apply_anomaly_decision()
        self._emit()

    def use_best_threshold(self) -> None:
        """采用 F1 最优阈值。"""
        self._config.anomaly_threshold = self.anomaly_best_threshold()
        self._apply_anomaly_decision()
        self._emit()

    def set_anomaly_tolerance(self, value: float) -> None:
        """分数容忍度（0~0.5）：判定门槛抬高为 `阈值 × (1 + 容忍度)`。"""
        self._config.anomaly_tolerance = max(0.0, min(0.5, float(value)))
        self._apply_anomaly_decision()
        self._emit()

    def anomaly_min_size(self) -> int:
        """最小缺陷尺寸（像素）：小于该尺寸的高分区域视为噪声。"""
        return max(0, int(getattr(self._config, "anomaly_min_size", 0) or 0))

    def set_anomaly_min_size(self, pixels: int) -> None:
        """设置最小缺陷尺寸（需要热力图才能生效，否则按分数判定）。"""
        self._config.anomaly_min_size = max(0, int(pixels))
        self._apply_anomaly_decision()
        self._emit()

    def build_heatmap_preview(self, row: dict) -> str:
        """生成（并缓存）某条记录的热力图叠加图。"""
        cached = str(row.get("heatmap") or "")
        if cached and Path(cached).is_file():
            return cached
        path = str(row.get("path") or "")
        source = str(row.get("heat_map") or "")
        if not path or not source or not Path(source).is_file():
            return ""
        try:
            import numpy as np

            heat = np.load(source)
        except Exception as exc:  # noqa: BLE001 - 热力图缺失不影响其它功能
            logger.debug("读取热力图失败 %s: %s", source, exc)
            return ""
        written = overlay_heatmap(
            path, heat, _EVAL_PLOT_DIR / f"{Path(path).stem}_heat.jpg"
        )
        # 缓存写回「结果里的那一行」：预览拿到的是副本，只有回写真行才有效
        canonical = next(
            (
                item for item in (self._eval.get("rows") or [])
                if str(item.get("path") or "") == path
            ),
            row,
        )
        canonical["heatmap"] = written
        row["heatmap"] = written
        return written

    def gradcam_available(self) -> bool:
        """当前结果是否支持 Grad-CAM（分类模型 + 分类任务结果）。"""
        rows = self._eval.get("rows") or []
        weights = str(self._config.weights_path or "")
        if not rows or not weights or any("pairs" in row for row in rows):
            return False
        try:
            from src.services.gradcam_service import GradCamService

            return bool(GradCamService.supported(weights))
        except Exception:  # noqa: BLE001 - 依赖缺失时按不支持处理
            return False

    def build_gradcam_preview(self, row: dict) -> str:
        """生成（并缓存）某条记录的 Grad-CAM 热力图（仅分类模型可用）。"""
        cached = str(row.get("gradcam") or "")
        if cached and Path(cached).is_file():
            return cached
        path = str(row.get("path") or "")
        weights = str(self._config.weights_path or "")
        if not path or not weights:
            return ""
        try:
            from src.services.gradcam_service import GradCamService

            service = GradCamService(
                weights=weights, imgsz=self.eval_imgsz(),
                device=str(self._config.device or "auto"),
            )
            pred = row.get("pred_id")
            written = service.overlay(
                path,
                target=(int(pred) if pred is not None and int(pred) >= 0 else None),
                target_path=_EVAL_PLOT_DIR / f"{Path(path).stem}_cam.jpg",
            )
        except Exception as exc:  # noqa: BLE001 - 不支持时只是没有这张视图
            logger.info("Grad-CAM 不可用：%s", exc)
            return ""
        canonical = next(
            (
                item for item in (self._eval.get("rows") or [])
                if str(item.get("path") or "") == path
            ),
            row,
        )
        canonical["gradcam"] = written
        row["gradcam"] = written
        return written

    def _apply_anomaly_decision(self) -> None:
        """按阈值 + 容忍度 + 最小缺陷尺寸重判每张图，并重算矩阵与指标。"""
        rows = self._eval.get("rows") or []
        if not rows:
            return
        effective = self.anomaly_threshold() * (1.0 + self.anomaly_tolerance())
        min_size = self.anomaly_min_size()
        for row in rows:
            score = float(row.get("confidence") or 0.0)
            abnormal = score >= effective
            if abnormal and min_size > 0 and row.get("heat_map"):
                areas = EvaluationService.abnormal_regions_file(
                    row["heat_map"], 0.5
                )
                if not areas or max(areas) < min_size:
                    abnormal = False      # 只有零碎小区域 → 视为噪声
            row["pred_id"] = 1 if abnormal else 0
            row["pred_label"] = "异常" if abnormal else "正常"
            row["correct"] = (int(row.get("true_id") or 0) == 1) == abnormal
            row["probs"] = {
                "正常": round(min(1.0, max(0.0, 1.0 - score)), 6),
                "异常": round(min(1.0, max(0.0, score)), 6),
            }
        self._recompute()

    def build_preprocess_preview(self, row: dict) -> str:
        """生成「原始 | 预处理」对比图（按图片 + 尺寸缓存，重复查看不再重算）。"""
        cached = str(row.get("preprocess") or "")
        if cached and Path(cached).is_file():
            return cached
        path = str(row.get("path") or "")
        if not path:
            return ""
        size = self.eval_imgsz()
        target = _EVAL_PLOT_DIR / f"{Path(path).stem}_pre{size}.jpg"
        if target.is_file():
            row["preprocess"] = str(target)
            return str(target)
        written = EvaluationService.preprocess_preview(path, size, target)
        row["preprocess"] = written
        return written

    def class_colors(self, names: list) -> list[str]:
        """类别名 → 颜色（优先取项目里定义的类别颜色）。"""
        project = self._project_vm.project if self._project_vm else None
        palette: dict[str, str] = {}
        if project is not None:
            palette = {item.name: item.color for item in project.classes}
        fallback = ("#0F6CBD", "#0F7B0F", "#C42B1C", "#B16CEA", "#E8A33D", "#4A5459")
        return [
            palette.get(str(name)) or fallback[index % len(fallback)]
            for index, name in enumerate(names)
        ]

    @property
    def eval_result(self) -> dict:
        return dict(self._eval)

    def eval_views(self) -> tuple[str, ...]:
        """当前结果的视图集（任务注册表声明），界面据此显示对应结果卡片。"""
        task = str(self._eval.get("task") or "")
        if not task:
            project = self._project_vm.project if self._project_vm else None
            task = _task_of(project.model_type) if project is not None else "detect"
        return _eval_views(task)

    @property
    def eval_rows(self) -> list:
        return list(self._eval.get("rows") or [])

    @property
    def eval_metrics(self) -> dict:
        return dict(self._eval.get("metrics") or {})

    def eval_index(self) -> int:
        return self._eval_index

    def current_row(self) -> dict:
        rows = self._eval.get("rows") or []
        if 0 <= self._eval_index < len(rows):
            return dict(rows[self._eval_index])
        return {}

    def select_row(self, index: int) -> None:
        """选中某条评估记录（图像详情跟随）。"""
        rows = self._eval.get("rows") or []
        if not (0 <= index < len(rows)):
            return
        self._eval_index = int(index)
        self.rowSelected.emit(dict(rows[index]))

    def update_true_label(self, index: int, label: str) -> None:
        """修正某张图的真实类别并立即重算指标（对齐 Halcon 的「真实标签类别」）。"""
        rows = list(self._eval.get("rows") or [])
        names = list(self._eval.get("class_names") or [])
        if not (0 <= index < len(rows)) or label not in names:
            return
        row = rows[index]
        row["label"] = str(label)
        row["true_id"] = names.index(label)
        if "pairs" not in row:      # 分类任务：图级判定，直接重判对错
            row["correct"] = bool(label) and row.get("pred_label") == label
        self._recompute()
        self.rowSelected.emit(dict(row))

    def _recompute(self) -> None:
        """按当前行记录重算混淆矩阵与指标。"""
        if str(self._eval.get("task") or "") == "semantic":
            # 语义分割的指标是像素级统计，不能由图级行记录重算
            self.evaluationReady.emit(dict(self._eval))
            return
        rows = list(self._eval.get("rows") or [])
        names = list(self._eval.get("class_names") or [])
        with_background = bool(self._eval.get("with_background"))
        metrics = self._eval.get("metrics") or {}
        matrix = EvaluationService.build_matrix(rows, names, with_background)
        self._eval["matrix"] = matrix
        self._eval["metrics"] = EvaluationService.build_metrics(
            rows, names, matrix,
            float(metrics.get("elapsed") or 0.0), with_background,
            float(metrics.get("avg_ms") or 0.0),
        )
        self.evaluationReady.emit(dict(self._eval))

    def export_eval_report(self, path: str) -> None:
        """导出评估明细（图片 / 真实类别 / 预测类别 / 置信度 / 是否正确 / 用时）。"""
        rows = self._eval.get("rows") or []
        if not rows:
            self.message.emit("warning", "没有可导出的评估结果")
            return
        headers = ["图片", "真实类别", "预测类别", "置信度", "是否正确", "用时(ms)", "路径"]
        table = [
            [
                row.get("name", ""), row.get("label", ""),
                row.get("pred_label", ""), f"{float(row.get('confidence') or 0):.4f}",
                "正确" if row.get("correct") else "错误",
                f"{float(row.get('ms') or 0):.2f}", row.get("path", ""),
            ]
            for row in rows
        ]
        try:
            ReportService.write_table(path, headers, table, sheet="evaluation")
        except Exception as exc:  # noqa: BLE001 - 文件写入异常类型较多
            logger.warning("导出评估报告失败: %s", exc)
            self.message.emit("error", f"导出评估报告失败：{exc}")
            return
        self.message.emit("success", f"评估报告已导出：{len(table)} 条")

    def run_evaluation(self) -> None:
        """在评估集上跑一次完整评估。"""
        if self.is_busy():
            self.message.emit("warning", "已有任务在运行，请先停止")
            return
        weights = self._config.weights_path
        if not weights or not Path(weights).is_file():
            self.message.emit("warning", "请先选择模型权重")
            return
        project = self._project_vm.project if self._project_vm else None
        task = _task_of(project.model_type) if project is not None else "classify"
        if task == "anomaly" or Path(weights).suffix.lower() == ".ckpt":
            self._run_anomaly_evaluation(weights)
            return
        if task == "semantic":
            self._run_segmentation_evaluation(weights)
            return
        if not EvaluationService.is_available():
            self.message.emit("error", "未安装 Ultralytics，无法执行评估")
            return

        plan = self._eval_plan(task)
        if plan is None:
            return
        samples, class_names = plan
        conf = self._config.confidence
        iou = self._config.iou
        device = self._config.device
        plot_dir = self._prepare_plot_dir()

        def job(progress, is_cancelled):
            return EvaluationService.evaluate(
                weights, samples, task=task, class_names=class_names,
                conf=conf, iou=iou, device=device, plot_dir=plot_dir,
                progress=progress, is_cancelled=is_cancelled,
            )

        self._start_worker(job, "模型评估", on_done=self._apply_evaluation)

    def _eval_plan(self, task: str) -> tuple | None:
        """解析评估集 → (samples, class_names)。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            self.message.emit("warning", "请先创建或打开项目")
            return None
        classify = task == "classify"
        limit = max(1, int(self._config.max_images or 200))

        folder = self._config.eval_folder.strip()
        if folder:
            root = Path(folder)
            if not root.is_dir():
                self.message.emit("warning", "评估目录不存在")
                return None
            samples = EvaluationService.collect_samples(
                root, label_from_folder=classify
            )
            if not classify:
                EvaluationService.attach_boxes(samples, root / "labels")
            names = EvaluationService.read_class_names(root / "data.yaml")
            if classify and not names:
                names = sorted({str(item["label"]) for item in samples if item["label"]})
        else:
            split = project.split_by_id(self._config.eval_split_id) or project.active_split()
            if split is None or not split.ready:
                self.message.emit("warning", "请先选择已生成的拆分")
                return None
            root = Path(split.output_dir)
            subsets = list(self._config.eval_subsets) or [
                self._config.eval_subset or "val"
            ]
            samples = []
            for subset in subsets:
                if classify:
                    part = EvaluationService.collect_samples(
                        root / subset, label_from_folder=True
                    )
                else:
                    part = EvaluationService.collect_samples(
                        root / "images" / subset, label_from_folder=False
                    )
                    EvaluationService.attach_boxes(part, root / "labels" / subset)
                for item in part:
                    item["subset"] = str(subset)
                samples.extend(part)
            if classify:
                names = sorted({
                    str(item["label"]) for item in samples if item["label"]
                })
            else:
                names = EvaluationService.read_class_names(root / "data.yaml")
                if not names:
                    names = sorted({
                        str(item["label"]) for item in samples if item["label"]
                    })
        samples = self._sample_samples(samples, limit)
        if not samples:
            self.message.emit("warning", "评估集里没有图片")
            return None
        if not names:
            self.message.emit("warning", "无法确定类别，请检查数据集配置")
            return None
        return samples, names

    def _sample_samples(self, samples: list, limit: int) -> list:
        """按上限取样本：勾选「随机抽样」时随机取（固定种子可复现）。"""
        if len(samples) <= limit:
            return list(samples)
        if not self._config.eval_random:
            return list(samples[:limit])
        import random

        rng = random.Random(int(self._config.eval_seed or 0))
        picked = rng.sample(list(samples), int(limit))
        return sorted(picked, key=lambda item: str(item.get("path") or ""))

    @staticmethod
    def _prepare_plot_dir() -> Path:
        """清空并返回评估可视化缓存目录。"""
        directory = _EVAL_PLOT_DIR
        try:
            if directory.is_dir():
                for item in directory.iterdir():
                    if item.is_file():
                        item.unlink()
        except OSError as exc:  # noqa: BLE001 - 清理失败不影响评估
            logger.warning("清理评估缓存失败: %s", exc)
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _apply_evaluation(self, result: dict) -> None:
        self._eval = dict(result or {})
        self._eval["raw_rows"] = list(self._eval.get("rows") or [])
        self._eval["instances"] = []
        self._eval_index = -1
        self._sort_rows()          # 沿用上次的排序方式
        self.evaluationReady.emit(dict(self._eval))
        metrics = self._eval.get("metrics") or {}
        if str(self._eval.get("task") or "") == "semantic":
            text = f"评估完成：mIoU {float(metrics.get('miou') or 0) * 100:.1f}%"
        else:
            text = f"评估完成：准确率 {float(metrics.get('accuracy') or 0) * 100:.1f}%"
        self.message.emit("success", text)

    # -----------------------------------------------------------
    # 语义分割（U-Net）
    # -----------------------------------------------------------
    def _run_segmentation_evaluation(self, weights: str) -> None:
        """逐图预测掩码并与真值对比（mIoU / Dice / 像素准确率）。"""
        plan = self._segmentation_pairs()
        if plan is None:
            return
        pairs, names = plan
        # 叠加图配色按类别 id - 1 取色，因此去掉「背景」那一个
        palette = self.class_colors(names)[1:]
        plot_dir = self._prepare_plot_dir()

        def job(progress, _is_cancelled):
            return SegmentationService.evaluate_checkpoint(
                weights, pairs, names,
                plot_dir=plot_dir, palette=palette, progress=progress,
            )

        def done(payload: dict) -> None:
            metrics = dict(payload.get("metrics") or {})
            self._apply_evaluation({
                "task": "semantic",
                "class_names": payload.get("class_names") or names,
                "rows": payload.get("rows") or [],
                "matrix": metrics.get("matrix") or [],
                "with_background": False,
                "metrics": metrics,
            })

        self._start_worker(job, "语义分割评估", on_done=done)

    def _segmentation_pairs(self) -> tuple | None:
        """评估用的（图, 掩码）对与类别名（背景在最前）。"""
        project = self._project_vm.project if self._project_vm else None
        folder = self._config.eval_folder.strip()
        if folder:
            base = Path(folder)
            if not base.is_dir():
                self.message.emit("warning", "评估目录不存在")
                return None
        else:
            if project is None:
                self.message.emit("warning", "请先创建或打开项目")
                return None
            split = (
                project.split_by_id(self._config.eval_split_id) or project.active_split()
            )
            if split is None or not split.ready:
                self.message.emit("warning", "请先选择已生成的拆分")
                return None
            base = Path(split.output_dir)

        subsets = list(self._config.eval_subsets) or [self._config.eval_subset or "val"]
        pairs: list = []
        for subset in subsets:
            pairs.extend(SegmentationService.pairs_for(base, subset))
        pairs = self._sample_pairs(pairs, max(1, int(self._config.max_images or 200)))
        if not pairs:
            self.message.emit("warning", "评估集里没有「图 + 掩码」对")
            return None
        names = ["背景"] + list(getattr(project, "class_names", []) or [])
        return pairs, names

    def _sample_pairs(self, pairs: list, limit: int) -> list:
        """（图, 掩码）对的抽样（与图片抽样同一套随机种子规则）。"""
        if len(pairs) <= limit:
            return list(pairs)
        if not self._config.eval_random:
            return list(pairs[:limit])
        import random

        rng = random.Random(int(self._config.eval_seed or 0))
        return sorted(
            rng.sample(list(pairs), int(limit)), key=lambda item: str(item[0])
        )

    def _run_anomaly_evaluation(self, ckpt: str) -> None:
        """异常检测评估：以 normal / abnormal 目录名作为真实标签。"""
        if not AnomalibService.is_available():
            self.message.emit("error", "未安装 Anomalib，无法执行评估")
            return
        project = self._project_vm.project if self._project_vm else None
        folder = self._config.eval_folder.strip()
        if not folder and project is not None:
            folder = str(project.training.anomaly_root or "")
        root = Path(folder)
        if not folder or not root.is_dir():
            self.message.emit("warning", "请选择含 normal/ 与 abnormal/ 的目录")
            return
        model_name = self._config.anomaly_model or "Padim"

        def job(progress, _is_cancelled):
            progress(20, "异常检测推理中")
            results = AnomalibService.predict(ckpt, model_name, root)
            rows: list[dict] = []
            for item in results or []:
                path = Path(str(item.get("path", "")))
                parent = path.parent.name.lower()
                abnormal = "abnormal" in parent or parent in ("ng", "defect")
                pred_abnormal = int(item.get("label", 0)) == 1
                score = float(item.get("score", 0.0) or 0.0)
                rows.append({
                    "path": str(path), "name": path.name,
                    "label": "异常" if abnormal else "正常",
                    "true_id": 1 if abnormal else 0,
                    "pred_id": 1 if pred_abnormal else 0,
                    "pred_label": "异常" if pred_abnormal else "正常",
                    "confidence": round(score, 6),
                    "correct": abnormal == pred_abnormal,
                    "ms": 0.0,
                    "probs": {"正常": round(1.0 - score, 6), "异常": round(score, 6)},
                    "detections": [], "annotated": "",
                })
            progress(100, f"已评估 {len(rows)} 张")
            return rows

        def done(rows: list) -> None:
            names = ["正常", "异常"]
            matrix = EvaluationService.build_matrix(rows, names, False)
            self._apply_evaluation({
                "task": "anomaly", "class_names": names, "rows": rows,
                "matrix": matrix, "with_background": False,
                "metrics": EvaluationService.build_metrics(
                    rows, names, matrix, 0.0, False, 0.0
                ),
            })

        self._start_worker(job, "异常检测评估", on_done=done)

    # -----------------------------------------------------------
    # 异常检测（Anomalib）
    # -----------------------------------------------------------
    def _run_anomaly(self, ckpt: str) -> None:
        """用 Anomalib 检查点对图片 / 目录做异常检测。"""
        if not AnomalibService.is_available():
            self.message.emit("error", "未安装 Anomalib，无法执行异常检测")
            return
        source = self._config.source_path
        if not source or not Path(source).exists():
            self.message.emit("warning", "请选择要检测的图片或目录")
            return

        model_name = self._config.anomaly_model or "Padim"

        def job(progress, _is_cancelled):
            progress(20, "异常检测推理中")
            return AnomalibService.predict(ckpt, model_name, source)

        self._start_worker(job, "异常检测", on_done=self._apply_anomaly)

    def _apply_anomaly(self, results: list) -> None:
        records: list[dict] = []
        abnormal = 0
        for item in results or []:
            is_abnormal = int(item.get("label", 0)) == 1
            abnormal += 1 if is_abnormal else 0
            records.append({
                "class_name": "异常" if is_abnormal else "正常",
                "confidence": round(float(item.get("score", 0.0)), 4),
                "path": str(item.get("path", "")),
            })
        self._records = records
        self.resultReady.emit({
            "annotated": None,
            "records": records,
            "count": len(records),
            "abnormal": abnormal,
            "elapsed": 0.0,
        })
        self.message.emit(
            "success", f"异常检测完成：{abnormal} / {len(records)} 张判定为异常"
        )

    # -----------------------------------------------------------
    # 检测
    # -----------------------------------------------------------
    def run_detection(self) -> None:
        """按当前输入源执行一次检测（相机为连续检测）。"""
        if self.is_busy():
            self.message.emit("warning", "已有任务在运行，请先停止")
            return
        weights = self._config.weights_path
        if not weights:
            self.message.emit("warning", "请先选择模型权重")
            return

        # .ckpt 走 Anomalib 异常检测分支
        if Path(weights).suffix.lower() == ".ckpt":
            self._run_anomaly(weights)
            return

        if not InferenceService.is_available():
            self.message.emit("error", "未安装 Ultralytics / OpenCV，无法执行检测")
            return

        source_type = self._config.source_type
        if source_type == "camera":
            self.start_camera()
            return

        source = self._config.source_path
        if not source or not Path(source).exists():
            self.message.emit("warning", "请选择要检测的图片或视频")
            return

        conf = self._config.confidence
        iou = self._config.iou
        device = self._config.device

        def job(progress, is_cancelled):
            if source_type == "video":
                return InferenceService.detect_video(
                    weights, source, conf=conf, iou=iou, device=device,
                    progress=progress, is_cancelled=is_cancelled,
                )
            return InferenceService.detect_image(
                weights, source, conf=conf, iou=iou, device=device
            )

        def done(result):
            self._apply_result(result)

        self._start_worker(job, "缺陷检测", on_done=done)

    def start_camera(self) -> None:
        """打开相机并持续检测，直到调用 stop()。"""
        if self.is_busy():
            self.message.emit("warning", "已有任务在运行")
            return
        weights = self._config.weights_path
        conf = self._config.confidence
        iou = self._config.iou
        device = self._config.device
        index = int(self._config.source_path or 0)

        def job(progress, is_cancelled):
            import cv2

            model = InferenceService.load_model(weights)
            capture = cv2.VideoCapture(index)
            if not capture.isOpened():
                raise RuntimeError(f"无法打开相机（序号 {index}）")
            frames = 0
            try:
                while not is_cancelled():
                    ok, frame = capture.read()
                    if not ok:
                        break
                    results = model.predict(
                        source=frame, conf=conf, iou=iou, device=device, verbose=False
                    )
                    if results:
                        result = results[0]
                        records = InferenceService.extract_records(result)
                        self._records = records
                        self.cameraFrame.emit(
                            InferenceService.plot(result), records
                        )
                    frames += 1
                    progress(min(frames, 100), f"已检测 {frames} 帧")
            finally:
                capture.release()
            return {"frames": frames}

        self._start_worker(job, "相机检测")

    def stop(self) -> None:
        """停止当前检测任务（视频 / 相机）。"""
        if not self.is_busy():
            self.message.emit("warning", "当前没有正在进行的检测")
            return
        self._worker.cancel()
        self.message.emit("info", "已请求停止检测")

    # -----------------------------------------------------------
    # 报告导出
    # -----------------------------------------------------------
    def export_report(self, path: str) -> None:
        """按扩展名导出 CSV 或 Excel 报告。"""
        if not self._records:
            self.message.emit("warning", "没有可导出的检测结果")
            return

        try:
            if path.lower().endswith((".xlsx", ".xls")):
                ReportService.write_excel(path, self._records)
            else:
                ReportService.write_csv(path, self._records)
        except Exception as exc:  # noqa: BLE001 - 文件写入异常类型较多
            logger.warning("导出报告失败: %s", exc)
            self.message.emit("error", f"导出报告失败：{exc}")
            return
        self.message.emit("success", f"报告已导出：{path}")

    # -----------------------------------------------------------
    # 内部
    # -----------------------------------------------------------
    def _apply_result(self, result: dict) -> None:
        records = list(result.get("records") or [])
        self._records = records
        self.resultReady.emit({
            "annotated": result.get("annotated"),
            "records": records,
            "count": len(records),
            "elapsed": float(result.get("elapsed") or 0.0),
            "frames": result.get("frames"),
        })
        self.message.emit("success", f"检测完成：{len(records)} 个目标")

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
