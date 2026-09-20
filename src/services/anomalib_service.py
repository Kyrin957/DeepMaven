"""异常检测服务：基于 Anomalib 的无监督异常检测。

与 YOLO 的检测 / 分割 / 分类并行，作为一个独立的**任务类型**：
仅需正常样本即可训练，推理输出异常分数与判定结果。

数据目录约定（`Folder` 数据模块）：
    <root>/normal/     正常样本（必需）
    <root>/abnormal/   异常样本（可选，用于评估）
"""

from __future__ import annotations

from pathlib import Path

from src.utils.constants import DATA_DIR
from src.utils.logger import get_logger

logger = get_logger("anomalib")

# 异常热力图缓存目录（原始分数图，供评估页「热图」视图与最小缺陷尺寸使用）
ANOMALY_HEAT_DIR = DATA_DIR / "anomaly"

# 推荐模型（兼顾速度与效果）
MODEL_KEYS = [
    "Padim",
    "Patchcore",
    "EfficientAd",
    "Fastflow",
    "ReverseDistillation",
]

DEFAULT_NORMAL_DIR = "normal"
DEFAULT_ABNORMAL_DIR = "abnormal"


class AnomalibService:
    """Anomalib 训练与推理（静态方法，依赖懒加载）。"""

    # -----------------------------------------------------------
    # 可用性
    # -----------------------------------------------------------
    @staticmethod
    def is_available() -> bool:
        try:
            import anomalib  # noqa: F401
        except ImportError:
            return False
        return True

    @staticmethod
    def available_models() -> list[str]:
        return list(MODEL_KEYS)

    # -----------------------------------------------------------
    # 构建
    # -----------------------------------------------------------
    @staticmethod
    def build_model(name: str, pretrained: bool = True):
        """按名称构建 Anomalib 模型。

        pretrained=False 时不加载骨干预训练权重，便于**离线环境**使用
        （多数模型默认会从 HuggingFace 下载骨干权重）。
        """
        import inspect

        import anomalib.models as models

        model_class = getattr(models, name, None)
        if model_class is None:
            raise ValueError(f"未知异常检测模型：{name}")
        if "pre_trained" in inspect.signature(model_class.__init__).parameters:
            return model_class(pre_trained=pretrained)
        return model_class()

    @staticmethod
    def build_datamodule(
        root: str | Path,
        normal_dir: str = DEFAULT_NORMAL_DIR,
        abnormal_dir: str = DEFAULT_ABNORMAL_DIR,
        batch: int = 8,
        seed: int = 0,
    ):
        """构建 Folder 数据模块。"""
        from anomalib.data import Folder

        return Folder(
            name="deepmaven",
            root=str(root),
            normal_dir=normal_dir,
            abnormal_dir=abnormal_dir,
            train_batch_size=batch,
            eval_batch_size=batch,
            num_workers=0,
            seed=seed,
        )

    # -----------------------------------------------------------
    # 推理
    # -----------------------------------------------------------
    @staticmethod
    def predict(ckpt: str, model_name: str, data_path: str | Path) -> list[dict]:
        """用已训练模型对图片或目录推理。

        Returns:
            [{"path": 图片路径, "score": 异常分数, "label": 0 正常 / 1 异常}, ...]
        """
        from anomalib.engine import Engine

        model = AnomalibService.build_model(model_name)
        model = type(model).load_from_checkpoint(ckpt)
        engine = Engine()
        predictions = engine.predict(
            model=model, data_path=str(data_path), return_predictions=True
        )

        results: list[dict] = []
        for batch in predictions or []:
            paths = list(getattr(batch, "image_path", []) or [])
            scores = _to_list(getattr(batch, "pred_score", None))
            labels = _to_list(getattr(batch, "pred_label", None))
            for index, path in enumerate(paths):
                results.append({
                    "path": str(path),
                    "score": float(scores[index]) if index < len(scores) else 0.0,
                    "label": int(labels[index]) if index < len(labels) else 0,
                })
            # 异常热力图：有则落盘（原始分数图 .npy），供评估页「热图」视图与
            # 「最小缺陷尺寸」后处理使用；没有该输出时不影响推理结果
            _save_heat_maps(batch, paths, results)
        logger.info("异常检测推理完成：%s 张", len(results))
        return results


def _save_heat_maps(batch, paths: list, results: list) -> None:
    """尽量把 Anomalib 的 anomaly_map 存成 .npy（没有该输出时静默跳过）。"""
    raw = getattr(batch, "anomaly_map", None)
    if raw is None:
        return
    try:
        import numpy as np
    except ImportError:
        return
    directory = ANOMALY_HEAT_DIR
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("创建热力图目录失败: %s", exc)
        return
    for index, path in enumerate(paths):
        try:
            array = np.asarray(raw[index].detach().cpu().squeeze())
        except Exception as exc:  # noqa: BLE001 - 取不到就跳过这张
            logger.debug("读取异常热力图失败 %s: %s", path, exc)
            continue
        if array.ndim != 2:
            continue
        target = directory / f"{Path(path).stem}.npy"
        try:
            np.save(target, array.astype("float32"))
        except OSError as exc:
            logger.warning("保存热力图失败 %s: %s", target, exc)
            continue
        if index < len(results):
            results[index]["heat_map"] = str(target)


def _to_list(value) -> list:
    """把张量 / 标量 / 列表统一转为 Python 列表。"""
    if value is None:
        return []
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]
