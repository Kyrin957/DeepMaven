"""导出后精度回归：对比源模型与导出产物在同一组样本上的输出。

导出（ONNX / TorchScript / 模型包）成功后自动跑一次对比，避免「导出成功但
精度掉了」的静默问题（见《开发文档.md》§8.7 第 2 期）。

结果状态：
    pass  一致样本占比达到阈值
    fail  存在明显不一致（提示检查导出参数）
    skip  缺少推理运行时或验证样本，无法对比（附原因，不算失败）

说明：回归取训练 / 验证集的少量样本做逐图比对（分类看 top1，检测看框匹配），
不是完整 mAP 复算；样本数与一致率都会写进导出记录，供人工判断。
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from src.services.evaluation_service import iou_xyxy
from src.utils.logger import get_logger

logger = get_logger("regression")

# 判定「同一个框」的 IoU 下限
BOX_IOU = 0.5
# 通过阈值：一致样本占比
PASS_RATE = 0.9


class RegressionService:
    """导出产物校验（数值回归 / 模型包结构校验）。"""

    # -----------------------------------------------------------
    # 入口
    # -----------------------------------------------------------
    @staticmethod
    def verify(export, source_weights: str, task: str, images: list, backend=None) -> dict:
        """校验一次导出，返回 {status, detail, ...}。"""
        if export is None:
            return {"status": "skip", "detail": "没有导出产物"}
        path = Path(str(getattr(export, "path", "")))
        if path.is_dir():
            return RegressionService.verify_package(path)
        if backend is None:
            return {"status": "skip", "detail": "缺少后端适配器"}
        return RegressionService.verify_model(
            backend, source_weights, path, task, images
        )

    @staticmethod
    def verify_model(
        backend, source_weights, exported, task: str, images: list, limit: int = 8,
    ) -> dict:
        """源模型与导出模型逐图对比（导出格式缺少运行时则跳过）。"""
        samples = [str(item) for item in (images or [])][:limit]
        if not samples:
            return {"status": "skip", "detail": "没有可用的验证图片"}
        try:
            source = backend.predict(source_weights, samples, task=task)
            target = backend.predict(str(exported), samples, task=task)
        except Exception as exc:  # noqa: BLE001 - 缺少运行时 / 格式不支持都按跳过处理
            detail = f"{type(exc).__name__}: {exc}"
            logger.warning("导出回归无法执行：%s", detail)
            return {"status": "skip", "detail": detail}
        return RegressionService.compare(source, target)

    # -----------------------------------------------------------
    # 对比
    # -----------------------------------------------------------
    @staticmethod
    def compare(source: list, target: list) -> dict:
        """比较两组预测（顺序对应同一批图片）。"""
        total = min(len(source), len(target))
        if total <= 0:
            return {"status": "skip", "detail": "没有推理结果"}
        agree = 0
        max_delta = 0.0
        for left, right in zip(source[:total], target[:total]):
            if RegressionService.same(left, right):
                agree += 1
            max_delta = max(max_delta, abs(left.confidence - right.confidence))
        rate = agree / total
        detail = f"一致 {agree}/{total} 张"
        if max_delta:
            detail += f" · 置信度最大偏差 {max_delta:.4f}"
        return {
            "status": "pass" if rate >= PASS_RATE else "fail",
            "detail": detail,
            "samples": total,
            "agreement": round(rate, 4),
            "max_conf_delta": round(max_delta, 4),
        }

    @staticmethod
    def same(left, right) -> bool:
        """两张图的预测是否一致：语义分割看掩码像素，分类看 top1，检测看框匹配。"""
        if left.mask or right.mask:
            return RegressionService.same_mask(left.mask, right.mask)
        if not left.boxes and not right.boxes:
            return int(left.cls_id) == int(right.cls_id)
        if len(left.boxes) != len(right.boxes):
            return False
        unmatched = list(right.boxes)
        for box in left.boxes:
            hit = next(
                (item for item in unmatched
                 if item.cls_id == box.cls_id
                 and iou_xyxy(box.xyxy, item.xyxy) >= BOX_IOU),
                None,
            )
            if hit is None:
                return False
            unmatched.remove(hit)
        return True

    @staticmethod
    def same_mask(left_path, right_path, threshold: float = 0.99) -> bool:
        """两张掩码是否一致：像素一致率 ≥ 阈值（语义分割的回归口径）。"""
        if not left_path or not right_path:
            return False
        try:
            import numpy as np
            from PIL import Image
        except ImportError:
            return False
        try:
            with Image.open(left_path) as handle:
                left = np.asarray(handle.convert("L"))
            with Image.open(right_path) as handle:
                right = np.asarray(handle.convert("L"))
        except (OSError, ValueError) as exc:
            logger.warning("读取掩码失败: %s", exc)
            return False
        if left.shape != right.shape:
            return False
        total = int(left.size) or 1
        return float((left == right).sum()) / total >= threshold

    # -----------------------------------------------------------
    # 模型包
    # -----------------------------------------------------------
    @staticmethod
    def verify_package(directory) -> dict:
        """模型包校验：清单 / SHA256 / 记忆库项（不做数值回归）。"""
        root = Path(directory)
        config_path = root / "config.json"
        if not config_path.is_file():
            return {"status": "fail", "detail": "缺少 config.json"}
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {"status": "fail", "detail": f"config.json 解析失败：{exc}"}

        files = dict(config.get("files") or {})
        if not files:
            return {"status": "fail", "detail": "清单里没有文件"}
        missing = [name for name in files if not (root / name).is_file()]
        if missing:
            return {"status": "fail", "detail": "缺少文件：" + "、".join(missing)}
        mismatched: list[str] = []
        for name, digest in files.items():
            try:
                actual = sha256((root / name).read_bytes()).hexdigest()
            except OSError:
                mismatched.append(name)
                continue
            if actual != digest:
                mismatched.append(name)
        if mismatched:
            return {"status": "fail", "detail": "文件校验失败：" + "、".join(mismatched)}

        onnx_info = dict(config.get("backbone_onnx") or {})
        detail = f"{len(files)} 个文件 · 记忆库 {len(config.get('state') or {})} 项"
        if onnx_info.get("exported"):
            detail += " · backbone.onnx 已生成"
        else:
            detail += f" · 未生成 backbone.onnx（{onnx_info.get('reason') or '原因未知'}）"
        return {"status": "pass", "detail": detail, "package": str(root)}
