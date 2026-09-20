"""评估服务：在带真实标签的评估集上统计精度指标与混淆矩阵。

支持三类任务：

* **分类（classify）**：预测类别与「目录名 = 真实类别」比对，保留各类别概率；
* **检测 / 分割 / 旋转框**：预测框与标签框按「同类别 + IoU 贪心匹配」统计 TP / FP / FN；
* **异常检测（Anomalib）**：以 normal / abnormal 目录为真实标签，比对异常判定。

对外只产出结构化结果（逐图记录 + 综合指标 + 混淆矩阵），界面据此绘制
综合指标、混淆矩阵、结果缩略图与图像详情。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from src.utils.constants import IMAGE_EXTS
from src.utils.device import normalize_device
from src.utils.logger import get_logger

logger = get_logger("evaluation")

# 混淆矩阵中「未匹配的预测」与「未匹配的真实标签」两个额外行列
BACKGROUND = "误检"
MISSED = "漏检"

ProgressFn = Callable[[int, str], None]
CancelFn = Callable[[], bool]


def _noop_progress(_percent: int, _text: str = "") -> None:
    pass


def _noop_cancel() -> bool:
    return False


def iou_xyxy(a: tuple, b: tuple) -> float:
    """两个 xyxy 框的 IoU。"""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    inter_w = min(ax2, bx2) - max(ax1, bx1)
    inter_h = min(ay2, by2) - max(ay1, by1)
    if inter_w <= 0 or inter_h <= 0:
        return 0.0
    inter = inter_w * inter_h
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return float(inter / union) if union > 0 else 0.0


# 误检细分（与 Halcon DLT 的 FP Class / Background / Localization / Duplicate /
# Multiple 五类对齐）：定位不准与重复检出分开统计，结论才可用。
FP_CLASS = "class"                  # 类别错：与其它类别真实框显著重叠
FP_BACKGROUND = "background"        # 无重叠：附近没有真实框
FP_LOCALIZATION = "localization"    # 定位不准：同类真实框存在但 IoU 不达标
FP_DUPLICATE = "duplicate"          # 重复：同一真实框已被更高置信度的预测占用
FP_MULTIPLE = "multiple"            # 多重错误：同时与多个真实框显著重叠

FP_REASONS: tuple[tuple[str, str], ...] = (
    (FP_CLASS, "类别错"),
    (FP_BACKGROUND, "无重叠"),
    (FP_LOCALIZATION, "定位不准"),
    (FP_DUPLICATE, "重复"),
    (FP_MULTIPLE, "多重错误"),
)
FP_REASON_LABELS: dict[str, str] = dict(FP_REASONS)

# 判定「疑似定位不准 / 多重重叠」的部分重叠下限
PARTIAL_IOU = 0.10


def match_boxes(gt: list, pred: list, iou_threshold: float = 0.45) -> dict:
    """同类别贪心匹配预测框与真实框，并把误检细分为五类。

    Args:
        gt: [(class_id, x1, y1, x2, y2), ...]
        pred: [(class_id, x1, y1, x2, y2, confidence), ...]（按置信度降序传入更佳）

    Returns:
        {
          "pairs": [(真实类别, 预测类别), ...],
          "matches": [(真实下标, 预测下标), ...],
          "fp_classes": [预测类别, ...],
          "fn_classes": [真实类别, ...],
          "fp_details": [{"index", "class_id", "confidence", "box", "reason", "iou"}],
          "fn_details": [{"index", "class_id", "box", "best_iou"}],
        }
    """
    ordered = sorted(
        range(len(pred)),
        key=lambda index: -(float(pred[index][5]) if len(pred[index]) > 5 else 0.0),
    )
    used = [False] * len(gt)
    pairs: list[tuple[int, int]] = []
    matches: list[tuple[int, int]] = []
    fp_classes: list[int] = []
    fp_details: list[dict] = []
    fn_classes: list[int] = []
    fn_details: list[dict] = []

    def box_of(item) -> tuple:
        return (float(item[1]), float(item[2]), float(item[3]), float(item[4]))

    for pred_index in ordered:
        item = pred[pred_index]
        pred_cls = int(item[0])
        confidence = float(item[5]) if len(item) > 5 else 0.0
        box = box_of(item)
        free_index, free_iou = -1, 0.0        # 尚未占用的同类真实框（可匹配）
        used_iou = 0.0                        # 已被占用的同类真实框（重复判定）
        same_iou = 0.0                        # 任意同类真实框（定位不准判定）
        other_iou = 0.0                       # 其它类别真实框（类别错判定）
        overlapped = 0                        # 显著重叠的真实框个数
        for index, truth in enumerate(gt):
            score = iou_xyxy(box, box_of(truth))
            if score >= PARTIAL_IOU:
                overlapped += 1
            if int(truth[0]) == pred_cls:
                same_iou = max(same_iou, score)
                if used[index]:
                    used_iou = max(used_iou, score)
                elif score > free_iou:
                    free_index, free_iou = index, score
            elif score > other_iou:
                other_iou = score

        if free_index >= 0 and free_iou >= iou_threshold:
            used[free_index] = True
            pairs.append((int(gt[free_index][0]), pred_cls))
            matches.append((free_index, pred_index))
            continue

        # 以下都是误检，按 DLT 的口径细分原因
        if used_iou >= iou_threshold:
            reason = FP_DUPLICATE
        elif other_iou >= iou_threshold:
            reason = FP_CLASS
        elif same_iou >= PARTIAL_IOU:
            reason = FP_LOCALIZATION
        elif overlapped >= 2:
            reason = FP_MULTIPLE
        else:
            reason = FP_BACKGROUND
        fp_classes.append(pred_cls)
        fp_details.append({
            "index": int(pred_index),
            "class_id": pred_cls,
            "confidence": round(confidence, 6),
            "box": [round(value, 2) for value in box],
            "reason": reason,
            "iou": round(max(same_iou, other_iou), 4),
        })

    for index, truth in enumerate(gt):
        if used[index]:
            continue
        truth_cls = int(truth[0])
        truth_box = box_of(truth)
        best = max(
            (
                iou_xyxy(truth_box, box_of(item))
                for item in pred if int(item[0]) == truth_cls
            ),
            default=0.0,
        )
        fn_classes.append(truth_cls)
        fn_details.append({
            "index": int(index),
            "class_id": truth_cls,
            "box": [round(value, 2) for value in truth_box],
            "best_iou": round(float(best), 4),
        })
    return {
        "pairs": pairs,
        "matches": matches,
        "fp_classes": fp_classes,
        "fn_classes": fn_classes,
        "fp_details": fp_details,
        "fn_details": fn_details,
    }


class EvaluationService:
    """评估集推理 + 指标统计。"""

    # -----------------------------------------------------------
    # 可用性
    # -----------------------------------------------------------
    @staticmethod
    def is_available() -> bool:
        try:
            import ultralytics  # noqa: F401
        except ImportError:
            return False
        return True

    # -----------------------------------------------------------
    # 评估集收集
    # -----------------------------------------------------------
    @staticmethod
    def list_images(directory: Path) -> list[Path]:
        """目录下的图片（按名称排序，不递归）。"""
        if not directory.is_dir():
            return []
        return sorted(
            (path for path in directory.iterdir()
             if path.is_file() and path.suffix.lower() in IMAGE_EXTS),
            key=lambda path: path.name.lower(),
        )

    @staticmethod
    def collect_samples(source, label_from_folder: bool = True) -> list[dict]:
        """收集评估样本。

        Args:
            source: 图片目录 / 单张图片。
            label_from_folder: 子目录名作为真实类别（分类 / 异常检测的目录约定）。

        Returns:
            [{"path": Path, "label": 真实类别名, "boxes": [(cls, x1, y1, x2, y2)]}]
        """
        root = Path(source)
        samples: list[dict] = []
        if root.is_file():
            samples.append({"path": root, "label": "", "boxes": []})
            return samples
        if not root.is_dir():
            return samples

        subdirs = [item for item in sorted(root.iterdir()) if item.is_dir()]
        folders = subdirs if (label_from_folder and subdirs) else []
        if folders:
            for folder in folders:
                for image in EvaluationService.list_images(folder):
                    samples.append({"path": image, "label": folder.name, "boxes": []})
        else:
            for image in EvaluationService.list_images(root):
                samples.append({"path": image, "label": "", "boxes": []})
        return samples

    @staticmethod
    def load_yolo_label(label_path: Path, width: int, height: int) -> list[tuple]:
        """读取 YOLO 标签 → [(class_id, x1, y1, x2, y2)]。

        兼容检测（4 值）、旋转框 / 分割（4 点或更多点，取外接矩形）。
        """
        boxes: list[tuple] = []
        try:
            text = Path(label_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return boxes
        for line in text.splitlines():
            fields = line.split()
            if len(fields) < 5:
                continue
            try:
                class_id = int(float(fields[0]))
                values = [float(value) for value in fields[1:]]
            except ValueError:
                continue
            if len(values) == 4:
                cx, cy, bw, bh = values
                xs = [cx - bw / 2, cx + bw / 2]
                ys = [cy - bh / 2, cy + bh / 2]
            elif len(values) >= 6 and len(values) % 2 == 0:
                xs = values[0::2]
                ys = values[1::2]
            else:
                continue
            x1 = max(0.0, min(xs) * width)
            y1 = max(0.0, min(ys) * height)
            x2 = min(float(width), max(xs) * width)
            y2 = min(float(height), max(ys) * height)
            if x2 > x1 and y2 > y1:
                boxes.append((class_id, x1, y1, x2, y2))
        return boxes

    @staticmethod
    def read_image_size(path: Path) -> tuple[int, int]:
        """图片尺寸（读不到时返回 (0, 0)）。"""
        try:
            from PIL import Image

            with Image.open(path) as image:
                return int(image.width), int(image.height)
        except Exception:  # noqa: BLE001 - 尺寸读取失败不应中断评估
            return 0, 0

    @staticmethod
    def read_class_names(data_yaml: Path) -> list[str]:
        """从 data.yaml 读取类别名（读取失败或格式不符时返回空）。"""
        try:
            import yaml

            data = yaml.safe_load(Path(data_yaml).read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001 - 缺少 yaml / 文件损坏都不应中断评估
            return []
        names = data.get("names") if isinstance(data, dict) else None
        if isinstance(names, dict):
            try:
                return [str(names[key]) for key in sorted(names, key=int)]
            except (TypeError, ValueError):
                return [str(value) for value in names.values()]
        if isinstance(names, list):
            return [str(item) for item in names]
        return []

    @staticmethod
    def attach_boxes(samples: list, label_dir: Path | None) -> None:
        """为样本补上真实框（检测 / 分割任务用 labels/<stem>.txt）。"""
        if label_dir is None or not Path(label_dir).is_dir():
            return
        for sample in samples:
            image = Path(sample["path"])
            label = Path(label_dir) / f"{image.stem}.txt"
            if not label.is_file():
                continue
            width, height = EvaluationService.read_image_size(image)
            if width and height:
                sample["boxes"] = EvaluationService.load_yolo_label(
                    label, width, height
                )

    # -----------------------------------------------------------
    # 指标
    # -----------------------------------------------------------
    @staticmethod
    def build_matrix(rows: list, class_names: list, with_background: bool) -> list:
        """按逐图记录累计混淆矩阵（行 = 真实，列 = 预测）。"""
        size = len(class_names) + (1 if with_background else 0)
        matrix = [[0] * size for _ in range(size)]
        last = size - 1
        for row in rows:
            pairs = row.get("pairs")
            if pairs is not None:
                # 检测族：框级配对；误检进最后一行、漏检进最后一列
                for true_id, pred_id in pairs:
                    matrix[true_id][pred_id] += 1
                if with_background:
                    for pred_id in row.get("fp_classes") or []:
                        matrix[last][pred_id] += 1
                    for true_id in row.get("fn_classes") or []:
                        matrix[true_id][last] += 1
                continue
            true_id = int(row.get("true_id", -1))
            pred_id = int(row.get("pred_id", -1))
            if 0 <= true_id < size and 0 <= pred_id < size:
                matrix[true_id][pred_id] += 1
        return matrix

    @staticmethod
    def build_metrics(
        rows: list, class_names: list, matrix: list, elapsed: float,
        with_background: bool, avg_ms: float = 0.0,
    ) -> dict:
        """由混淆矩阵汇总综合指标与逐类指标。"""
        size = len(class_names) + (1 if with_background else 0)
        last = size - 1
        per_class = []
        for class_id, name in enumerate(class_names):
            tp = matrix[class_id][class_id]
            # 误检 = 该列（预测类别）除对角外的全部（含「误检」行）；
            # 漏检 = 该行（真实类别）除对角外的全部（含「漏检」列）
            fp = sum(matrix[cls][class_id] for cls in range(size)) - tp
            fn = sum(matrix[class_id]) - tp
            precision = tp / (tp + fp) if (tp + fp) else 0.0
            recall = tp / (tp + fn) if (tp + fn) else 0.0
            f1 = (2 * precision * recall / (precision + recall)
                  if (precision + recall) else 0.0)
            per_class.append({
                "class_id": class_id,
                "name": name,
                "support": tp + fn,
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "precision": round(precision, 4),
                "recall": round(recall, 4),
                "f1": round(f1, 4),
            })

        scored = [item for item in per_class if item["support"] or item["tp"] or item["fp"]]
        macro = {
            key: (round(sum(item[key] for item in scored) / len(scored), 4)
                  if scored else 0.0)
            for key in ("precision", "recall", "f1")
        }
        total = len(rows)
        correct = sum(1 for row in rows if row.get("correct"))
        wrong = total - correct
        accuracy = (correct / total) if total else 0.0
        return {
            "total": total,
            "correct": correct,
            "wrong": wrong,
            "accuracy": round(accuracy, 4),
            "top1_error": round(1.0 - accuracy, 4),
            "precision": macro["precision"],
            "recall": macro["recall"],
            "f1": macro["f1"],
            "avg_ms": round(avg_ms, 3),
            "elapsed": round(elapsed, 3),
            "per_class": per_class,
            "fp_summary": EvaluationService.summarize_fp(rows),
            "fn_total": sum(len(row.get("fn_details") or []) for row in rows),
        }

    @staticmethod
    def summarize_fp(rows: list) -> dict:
        """误检细分汇总：五类各自数量 + 合计（检测族才有值）。"""
        summary = {key: 0 for key, _label in FP_REASONS}
        for row in rows:
            for detail in row.get("fp_details") or []:
                reason = str(detail.get("reason") or "")
                if reason in summary:
                    summary[reason] += 1
        summary["total"] = sum(summary.values())
        return summary

    # -----------------------------------------------------------
    # 评估主流程
    # -----------------------------------------------------------
    @staticmethod
    def evaluate(
        weights: str,
        samples: list,
        task: str = "classify",
        class_names: list | None = None,
        conf: float = 0.25,
        iou: float = 0.45,
        device: str = "auto",
        plot_dir: Path | None = None,
        progress: ProgressFn | None = None,
        is_cancelled: CancelFn | None = None,
    ) -> dict:
        """逐图推理并与真实标签比对。

        Returns:
            {"rows": [...], "class_names": [...], "matrix": [[...]],
             "metrics": {...}, "task": task, "with_background": bool}
        """
        progress = progress or _noop_progress
        is_cancelled = is_cancelled or _noop_cancel
        from ultralytics import YOLO

        model = YOLO(weights)
        device_value = normalize_device(device)
        classify = task == "classify"
        plot_dir = Path(plot_dir) if plot_dir else None
        if plot_dir is not None:
            plot_dir.mkdir(parents=True, exist_ok=True)

        rows: list[dict] = []
        names = list(class_names or [])
        started = time.perf_counter()
        total_ms = 0.0
        total = len(samples)

        for index, sample in enumerate(samples):
            if is_cancelled():
                break
            path = Path(sample["path"])
            begin = time.perf_counter()
            try:
                results = model.predict(
                    source=str(path), conf=conf, iou=iou,
                    device=device_value, verbose=False,
                )
            except Exception as exc:  # noqa: BLE001 - 单张失败不应中断整体评估
                logger.warning("评估推理失败 %s: %s", path.name, exc)
                rows.append({
                    "path": str(path), "name": path.name,
                    "label": str(sample.get("label", "")),
                    "true_id": -1, "pred_id": -1, "pred_label": "",
                    "confidence": 0.0, "correct": False, "ms": 0.0,
                    "probs": {}, "detections": [], "annotated": "",
                    "error": str(exc),
                })
                continue
            elapsed_ms = (time.perf_counter() - begin) * 1000
            total_ms += elapsed_ms
            result = results[0] if results else None
            if result is not None and not names:
                names = [
                    str(value) for _key, value in sorted(
                        (getattr(result, "names", {}) or {}).items()
                    )
                ]
            row = EvaluationService._row_from_result(
                result, sample, path, task, names, iou, elapsed_ms, plot_dir
            )
            rows.append(row)
            progress(
                int(100 * (index + 1) / total) if total else 100,
                f"已评估 {index + 1}/{total}",
            )

        elapsed = time.perf_counter() - started
        avg_ms = (total_ms / len(rows)) if rows else 0.0
        matrix = EvaluationService.build_matrix(rows, names, not classify)
        metrics = EvaluationService.build_metrics(
            rows, names, matrix, elapsed, not classify, avg_ms
        )
        return {
            "task": task,
            "class_names": names,
            "rows": rows,
            "matrix": matrix,
            "metrics": metrics,
            "with_background": not classify,
        }

    # -----------------------------------------------------------
    # 图像渲染（GT 叠加 / 实例裁剪）
    # -----------------------------------------------------------
    @staticmethod
    def _name_of(names: list, class_id) -> str:
        index = int(class_id)
        return str(names[index]) if 0 <= index < len(names) else str(index)

    @staticmethod
    def _save_image(image, target) -> str:
        target = Path(target)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            image.save(target, quality=88)
        except (OSError, ValueError) as exc:
            logger.warning("保存可视化失败 %s: %s", target, exc)
            return ""
        return str(target)

    @staticmethod
    def render_overlay(
        path, truth: list, detections: list, names: list, target
    ) -> str:
        """把**真实框（绿实线）**与**预测框（橙虚线）**画在同一张图上。

        供评估页的「GT 叠加」视图使用：真实框与预测框并排对比，一眼能看出
        是漏检、误检还是定位不准。
        """
        try:
            from PIL import Image as PILImage
        except ImportError:
            logger.warning("未安装 Pillow，无法生成 GT 叠加图")
            return ""
        try:
            with PILImage.open(path) as handle:
                image = handle.convert("RGB")
        except (OSError, ValueError) as exc:
            logger.warning("读取图像失败 %s: %s", path, exc)
            return ""
        from PIL import ImageDraw

        draw = ImageDraw.Draw(image)
        font = _load_font(max(11, min(22, image.width // 40)))
        for class_id, x1, y1, x2, y2 in truth:
            _draw_box(draw, (x1, y1, x2, y2), _GT_COLOR, dashed=False)
            _draw_tag(
                draw, f"GT {EvaluationService._name_of(names, class_id)}",
                float(x1), float(y1), _GT_COLOR, font,
            )
        for class_id, x1, y1, x2, y2, confidence in detections:
            _draw_box(draw, (x1, y1, x2, y2), _PRED_COLOR, dashed=True)
            _draw_tag(
                draw,
                f"{EvaluationService._name_of(names, class_id)} "
                f"{float(confidence):.2f}",
                float(x1), float(y2), _PRED_COLOR, font, below=True,
            )
        line = getattr(font, "size", 14) + 12
        _draw_tag(draw, "真实框（绿实线）", 6, 6, _GT_COLOR, font, below=True)
        _draw_tag(draw, "预测框（橙虚线）", 6, 6 + line, _PRED_COLOR, font, below=True)
        return EvaluationService._save_image(image, target)

    @staticmethod
    def preprocess_preview(path, imgsz: int, target) -> str:
        """生成「原始图 | 训练预处理后」对比图（letterbox + 灰色填充）。

        训练时图片会等比缩放到 `imgsz` 的方形画布并补灰边，这里按同一口径
        处理一遍，用于确认尺寸设置是否会把目标缩得过小。
        """
        try:
            from PIL import Image as PILImage
        except ImportError:
            logger.warning("未安装 Pillow，无法生成预处理对比图")
            return ""
        try:
            with PILImage.open(path) as handle:
                source = handle.convert("RGB")
        except (OSError, ValueError) as exc:
            logger.warning("读取图像失败 %s: %s", path, exc)
            return ""
        from PIL import ImageDraw

        size = max(64, min(1024, int(imgsz or 640)))

        def letterbox(image, pad_color) -> object:
            ratio = min(
                size / max(1, image.width), size / max(1, image.height)
            )
            inner = image.resize((
                max(1, int(round(image.width * ratio))),
                max(1, int(round(image.height * ratio))),
            ))
            board = PILImage.new("RGB", (size, size), pad_color)
            board.paste(
                inner, ((size - inner.width) // 2, (size - inner.height) // 2)
            )
            return board

        gap = max(8, size // 40)
        canvas = PILImage.new("RGB", (size * 2 + gap, size), (24, 24, 24))
        canvas.paste(letterbox(source, (24, 24, 24)), (0, 0))
        canvas.paste(letterbox(source, (114, 114, 114)), (size + gap, 0))
        draw = ImageDraw.Draw(canvas)
        font = _load_font(max(12, size // 22))
        _draw_tag(
            draw, f"原始 {source.width}×{source.height}", 6, 6,
            (46, 204, 113), font, below=True,
        )
        _draw_tag(
            draw, f"预处理 {size}×{size}", size + gap + 6, 6,
            (243, 156, 18), font, below=True,
        )
        return EvaluationService._save_image(canvas, target)

    @staticmethod
    def abnormal_regions(heat, threshold: float = 0.5) -> list[int]:
        """热力图里超过阈值的连通区域面积（降序，单位：像素）。

        用于「最小缺陷尺寸」后处理：小于该尺寸的高分区域多半是噪声，
        不应把整张图判为异常。
        """
        try:
            import cv2
            import numpy as np
        except ImportError:
            return []
        array = np.squeeze(np.nan_to_num(
            np.asarray(heat, dtype=np.float32), nan=0.0
        ))
        if array.ndim != 2 or array.size == 0:
            return []
        low, high = float(array.min()), float(array.max())
        span = max(1e-9, high - low)
        mask = (((array - low) / span) >= float(threshold)).astype(np.uint8)
        if not mask.any():
            return []
        count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, 8)
        areas = [
            int(stats[index, cv2.CC_STAT_AREA])
            for index in range(1, int(count))
        ]
        return sorted(areas, reverse=True)

    @staticmethod
    def abnormal_regions_file(path, threshold: float = 0.5) -> list[int]:
        """从保存的热力图（.npy）统计连通区域面积（读不到时返回空）。"""
        try:
            import numpy as np

            return EvaluationService.abnormal_regions(np.load(str(path)), threshold)
        except Exception as exc:  # noqa: BLE001 - 热力图不可用则退化为按分数判定
            logger.debug("读取热力图失败 %s: %s", path, exc)
            return []

    @staticmethod
    def crop_instance(path, box, target, padding: int = 8) -> str:
        """按框裁剪出实例小图（供「实例级」视图展示）。"""
        try:
            from PIL import Image as PILImage

            with PILImage.open(path) as handle:
                image = handle.convert("RGB")
        except Exception as exc:  # noqa: BLE001 - 裁剪失败不影响评估
            logger.debug("裁剪实例失败 %s: %s", path, exc)
            return ""
        x1, y1, x2, y2 = (float(value) for value in box)
        left = max(0, int(x1) - padding)
        top = max(0, int(y1) - padding)
        right = min(image.width, int(x2) + padding)
        bottom = min(image.height, int(y2) + padding)
        if right - left < 4 or bottom - top < 4:
            return ""
        return EvaluationService._save_image(
            image.crop((left, top, right, bottom)), target
        )

    # -----------------------------------------------------------
    # 单图结果解析
    # -----------------------------------------------------------
    @staticmethod
    def _row_from_result(
        result, sample: dict, path: Path, task: str, names: list,
        iou: float, elapsed_ms: float, plot_dir: Path | None,
    ) -> dict:
        """把一次推理结果转成评估记录。"""
        label = str(sample.get("label", ""))
        row: dict = {
            "path": str(path), "name": path.name, "label": label,
            "subset": str(sample.get("subset") or ""),
            "true_id": names.index(label) if label in names else -1,
            "pred_id": -1, "pred_label": "", "confidence": 0.0,
            "correct": False, "ms": round(elapsed_ms, 3), "probs": {},
            "detections": [], "annotated": "",
        }
        if result is None:
            return row

        probs = getattr(result, "probs", None)
        if task == "classify" or probs is not None:
            values = []
            data = getattr(probs, "data", None)
            if data is not None:
                try:
                    values = [float(value) for value in data.tolist()]
                except (TypeError, ValueError):
                    values = []
            if not values:
                return row
            pred_id = int(max(range(len(values)), key=lambda i: values[i]))
            row["probs"] = {
                (names[i] if i < len(names) else str(i)): round(value, 6)
                for i, value in enumerate(values)
            }
            row["pred_id"] = pred_id
            row["pred_label"] = names[pred_id] if pred_id < len(names) else str(pred_id)
            row["confidence"] = round(values[pred_id], 6)
            row["correct"] = row["pred_label"] == label and bool(label)
            row["detections"] = [{
                "class_name": row["pred_label"], "confidence": row["confidence"],
            }]
        else:
            boxes = getattr(result, "boxes", None)
            detections: list[tuple] = []
            if boxes is not None and len(boxes):
                xyxy = boxes.xyxy.cpu().numpy()
                scores = boxes.conf.cpu().numpy()
                classes = boxes.cls.cpu().numpy().astype(int)
                for index in range(len(classes)):
                    x1, y1, x2, y2 = (float(value) for value in xyxy[index])
                    detections.append((
                        int(classes[index]), x1, y1, x2, y2, float(scores[index])
                    ))
            truth = [
                (int(item[0]), item[1], item[2], item[3], item[4])
                for item in (sample.get("boxes") or [])
            ]
            matched = match_boxes(truth, detections, iou)
            row["pairs"] = matched["pairs"]
            row["matches"] = matched["matches"]
            row["fp_classes"] = matched["fp_classes"]
            row["fn_classes"] = matched["fn_classes"]
            row["fp_details"] = matched["fp_details"]
            row["fn_details"] = matched["fn_details"]
            row["tps"] = len(matched["pairs"])
            row["fps"] = len(matched["fp_classes"])
            row["fns"] = len(matched["fn_classes"])
            row["gt_boxes"] = [
                {
                    "class_name": EvaluationService._name_of(names, item[0]),
                    "x1": round(float(item[1]), 1), "y1": round(float(item[2]), 1),
                    "x2": round(float(item[3]), 1), "y2": round(float(item[4]), 1),
                }
                for item in truth
            ]
            row["correct"] = bool(truth or detections) and not row["fps"] and not row["fns"]
            row["confidence"] = round(
                max((item[5] for item in detections), default=0.0), 6
            )
            row["iou"] = round(
                max((float(item.get("iou") or 0.0) for item in matched["fp_details"]),
                    default=(1.0 if not matched["fn_details"] else 0.0)),
                4,
            )
            row["pred_label"] = (
                (names[detections[0][0]] if detections[0][0] < len(names)
                 else str(detections[0][0]))
                if detections else ""
            )
            row["detections"] = [
                {
                    "class_name": (names[int(item[0])] if int(item[0]) < len(names)
                                   else str(int(item[0]))),
                    "confidence": round(float(item[5]), 6),
                    "x1": round(float(item[1]), 1), "y1": round(float(item[2]), 1),
                    "x2": round(float(item[3]), 1), "y2": round(float(item[4]), 1),
                }
                for item in detections
            ]
            if plot_dir is not None:
                row["overlay"] = EvaluationService.render_overlay(
                    path, truth, detections, names,
                    Path(plot_dir) / f"{path.stem}_gt.jpg",
                )

        # 保存预测可视化（供缩略图 / 图像详情展示）
        if plot_dir is not None:
            try:
                import cv2

                plotted = result.plot()
                target = Path(plot_dir) / f"{path.stem}.jpg"
                cv2.imwrite(str(target), plotted)
                row["annotated"] = str(target)
            except Exception as exc:  # noqa: BLE001 - 可视化失败不影响指标
                logger.warning("保存评估可视化失败 %s: %s", path.name, exc)
        return row


# -----------------------------------------------------------
# 画框小工具（Pillow 实现：支持中文标签与虚线框）
# -----------------------------------------------------------
_GT_COLOR = (46, 204, 113)      # 真实框：绿色实线
_PRED_COLOR = (243, 156, 18)    # 预测框：橙色虚线
_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhl.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/PingFang.ttc",
)


def _load_font(size: int):
    """加载支持中文的字体（找不到时退回 Pillow 默认字体）。"""
    from PIL import ImageFont

    for candidate in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, size)
        except (OSError, ValueError):
            continue
    return ImageFont.load_default()


def _draw_box(draw, box, color, width: int = 2, dashed: bool = False,
              dash: int = 9) -> None:
    """画矩形框（`dashed=True` 时画虚线）。"""
    x1, y1, x2, y2 = (float(value) for value in box)
    if not dashed:
        draw.rectangle([x1, y1, x2, y2], outline=color, width=width)
        return
    for start, end in (
        ((x1, y1), (x2, y1)), ((x2, y1), (x2, y2)),
        ((x2, y2), (x1, y2)), ((x1, y2), (x1, y1)),
    ):
        span = max(abs(end[0] - start[0]), abs(end[1] - start[1]))
        if span <= 0:
            continue
        steps = max(1, int(span // dash))
        for step in range(0, steps + 1, 2):
            low = min(1.0, step * dash / span)
            high = min(1.0, (step + 1) * dash / span)
            draw.line(
                [
                    start[0] + (end[0] - start[0]) * low,
                    start[1] + (end[1] - start[1]) * low,
                    start[0] + (end[0] - start[0]) * high,
                    start[1] + (end[1] - start[1]) * high,
                ],
                fill=color, width=width,
            )


def _draw_tag(draw, text: str, x: float, y: float, color, font,
              below: bool = False) -> None:
    """在框角上画一个带底色的小标签（`below=True` 时标签画在 y 下方）。"""
    if not text:
        return
    try:
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        width, height = right - left, bottom - top
    except (AttributeError, TypeError):
        width, height = len(str(text)) * 8, 12
    height = max(14.0, float(height) + 6)
    origin = float(y) if below else max(0.0, float(y) - height)
    draw.rectangle([x, origin, x + width + 8, origin + height], fill=color)
    draw.text((x + 4, origin + 2), text, fill=(255, 255, 255), font=font)
