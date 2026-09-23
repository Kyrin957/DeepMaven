"""语义分割指标：逐类 IoU / Dice、mIoU 与像素准确率。

口径：掩码像素值即类别 id（0 = 背景），与数据集产物一致。
指标按混淆矩阵累计后一次算出（比逐图平均更稳定）。
"""

from __future__ import annotations

import numpy as np


def confusion(
    target: np.ndarray, prediction: np.ndarray, num_classes: int
) -> np.ndarray:
    """累计混淆矩阵（行 = 真值，列 = 预测），尺寸 [num_classes, num_classes]。"""
    target = np.asarray(target).astype(np.int64).ravel()
    prediction = np.asarray(prediction).astype(np.int64).ravel()
    valid = (target >= 0) & (target < num_classes)
    index = target[valid] * num_classes + prediction[valid]
    counts = np.bincount(index, minlength=num_classes * num_classes)
    return counts.reshape(num_classes, num_classes)


def scores(matrix: np.ndarray, class_names: list[str] | None = None) -> dict:
    """由混淆矩阵算 IoU / Dice / 像素准确率与 mIoU。

    背景（类别 0）计入像素准确率，但不计入 mIoU / Dice 均值——
    否则「大面积背景预测正确」会把缺失的缺陷类别完全掩盖。
    """
    matrix = np.asarray(matrix, dtype=np.float64)
    total_classes = matrix.shape[0]
    names = list(class_names or [f"class{i}" for i in range(total_classes)])

    intersections = np.diag(matrix)
    targets = matrix.sum(axis=1)        # 真值像素数
    predictions = matrix.sum(axis=0)    # 预测像素数
    unions = targets + predictions - intersections

    per_class: list[dict] = []
    ious: list[float] = []
    dices: list[float] = []
    for index in range(total_classes):
        union = unions[index]
        iou = float(intersections[index] / union) if union > 0 else 0.0
        denom = targets[index] + predictions[index]
        dice = float(2 * intersections[index] / denom) if denom > 0 else 0.0
        per_class.append({
            "id": index,
            "name": names[index] if index < len(names) else str(index),
            "iou": round(iou, 4),
            "dice": round(dice, 4),
            "support": int(targets[index]),
        })
        if index == 0:
            continue                    # 背景不参与均值
        if union > 0 or targets[index] > 0:
            ious.append(iou)
            dices.append(dice)

    pixels = matrix.sum()
    pixel_acc = float(intersections.sum() / pixels) if pixels > 0 else 0.0
    return {
        "miou": round(float(np.mean(ious)) if ious else 0.0, 4),
        "dice": round(float(np.mean(dices)) if dices else 0.0, 4),
        "pixel_acc": round(pixel_acc, 4),
        "per_class": per_class,
        "classes": max(0, total_classes - 1),
    }


def evaluate(
    targets: list[np.ndarray],
    predictions: list[np.ndarray],
    num_classes: int,
    class_names: list[str] | None = None,
) -> dict:
    """对多张掩码求指标（逐张累计混淆矩阵）。"""
    matrix = np.zeros((num_classes, num_classes), dtype=np.int64)
    for target, prediction in zip(targets, predictions):
        matrix += confusion(target, prediction, num_classes)
    result = scores(matrix, class_names)
    result["images"] = len(targets)
    result["matrix"] = matrix.tolist()
    return result
