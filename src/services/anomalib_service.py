"""异常检测服务：基于 Anomalib 的无监督异常检测。

与 YOLO 的检测 / 分割 / 分类并行，作为一个独立的**任务类型**：
仅需正常样本即可训练，推理输出异常分数与判定结果。

数据目录约定（`Folder` 数据模块）：
    <root>/normal/     正常样本（必需）
    <root>/abnormal/   异常样本（可选，用于评估）
"""

from __future__ import annotations

from pathlib import Path

from src.utils.constants import (
    ANOMALY_ABNORMAL_DIR,
    ANOMALY_NORMAL_DIR,
    DATA_DIR,
)
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

DEFAULT_NORMAL_DIR = ANOMALY_NORMAL_DIR
DEFAULT_ABNORMAL_DIR = ANOMALY_ABNORMAL_DIR

# ---------------------------------------------------------------
# 模型包（导出产物）文件约定
# ---------------------------------------------------------------
PACKAGE_FORMAT_VERSION = 1
PACKAGE_MODEL_FILE = "model.ckpt"           # 训练检查点（完整推理仍用它）
PACKAGE_STATE_FILE = "memory_bank.npz"      # 记忆库 / 均值协方差（部署端最近邻搜索）
PACKAGE_BACKBONE_FILE = "backbone.onnx"     # 特征提取骨干（尽力而为）
PACKAGE_CONFIG_FILE = "config.json"         # 模型名 / 骨干层 / 文件清单与 SHA256


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

    # -----------------------------------------------------------
    # 模型包导出
    # -----------------------------------------------------------
    @staticmethod
    def export_package(
        ckpt: str,
        model_name: str,
        out_dir: str | Path,
        imgsz: int = 256,
        progress=None,
    ) -> dict:
        """导出为可部署的「模型包」（记忆库与骨干分离存储）。

        产出（尽力而为，缺项写入 config.json 的 `backbone_onnx.reason`）：
            model.ckpt        训练检查点
            memory_bank.npz   记忆库 / 均值协方差
            backbone.onnx     特征提取骨干（需要 onnx 包）
            config.json       模型名、骨干层、文件清单与 SHA256

        Returns:
            {"dir": 包目录, "files": [文件路径], "note": 降级说明}
        """
        import json
        from datetime import datetime
        from hashlib import sha256

        source = Path(ckpt)
        if not source.is_file():
            raise FileNotFoundError(f"检查点不存在：{ckpt}")
        if source.suffix.lower() != ".ckpt":
            raise ValueError(f"模型包需要异常检测检查点（.ckpt）：{source.name}")
        target = Path(out_dir) / f"{source.stem}_package"
        target.mkdir(parents=True, exist_ok=True)

        _tick(progress, 10, "复制检查点")
        (target / PACKAGE_MODEL_FILE).write_bytes(source.read_bytes())

        _tick(progress, 30, "读取模型")
        model = AnomalibService.build_model(model_name)
        model = type(model).load_from_checkpoint(str(source))
        module = getattr(model, "model", model)

        _tick(progress, 50, "导出记忆库")
        state = _extract_state(module)
        if state:
            import numpy as np

            np.savez_compressed(target / PACKAGE_STATE_FILE, **state)

        _tick(progress, 70, "导出骨干")
        exported, reason = _export_backbone(
            module, target / PACKAGE_BACKBONE_FILE, int(imgsz)
        )

        _tick(progress, 90, "写入配置")
        config = {
            "format_version": PACKAGE_FORMAT_VERSION,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "model": str(model_name),
            "imgsz": int(imgsz),
            "backbone": _backbone_info(module),
            "state": {name: list(value.shape) for name, value in state.items()},
            "backbone_onnx": {"exported": bool(exported), "reason": str(reason)},
        }
        files = sorted(item for item in target.glob("*") if item.is_file())
        config["files"] = {
            item.name: sha256(item.read_bytes()).hexdigest() for item in files
        }
        (target / PACKAGE_CONFIG_FILE).write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        files = sorted(item for item in target.glob("*") if item.is_file())
        note = "" if exported else f"未生成 backbone.onnx：{reason}"
        logger.info("异常检测模型包已导出：%s（%d 个文件）", target, len(files))
        _tick(progress, 100, "模型包已导出")
        return {
            "dir": str(target),
            "files": [str(item) for item in files],
            "note": note,
        }


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


def _tick(progress, percent: int, text: str) -> None:
    """上报导出进度（未传回调时忽略）。"""
    if callable(progress):
        progress(int(percent), str(text))


def _extract_state(module) -> dict:
    """取记忆库 / 统计量（模型没有则返回空 dict）。"""
    try:
        import torch
    except ImportError:
        return {}
    state: dict = {}
    bank = getattr(module, "memory_bank", None)
    if isinstance(bank, torch.Tensor) and bank.numel():
        state["memory_bank"] = bank.detach().cpu().numpy()
    gaussian = getattr(module, "gaussian", None)
    for name in ("mean", "covariance", "inv_covariance"):
        value = getattr(gaussian, name, None)
        if isinstance(value, torch.Tensor):
            state[f"gaussian_{name}"] = value.detach().cpu().numpy()
    return state


def _backbone_info(module) -> dict:
    """骨干信息（特征层与骨干名称），取不到时返回空 dict。"""
    extractor = getattr(module, "feature_extractor", None)
    if extractor is None:
        return {}
    backbone = getattr(extractor, "backbone", None)
    name = ""
    if backbone is not None:
        config = getattr(backbone, "pretrained_cfg", None) or {}
        name = str(config.get("architecture") or type(backbone).__name__)
    layers = getattr(extractor, "layers", None)
    return {
        "extractor": type(extractor).__name__,
        "backbone": name,
        "layers": [str(item) for item in (layers or [])],
    }


def _export_backbone(module, target: Path, imgsz: int) -> tuple[bool, str]:
    """把特征提取骨干导出为 ONNX（需要 onnx 包，失败时返回原因）。"""
    try:
        import torch
    except ImportError as exc:
        return False, f"未安装 {exc.name}"
    try:
        import onnx  # noqa: F401
    except ImportError as exc:
        return False, f"未安装 {exc.name}"
    extractor = getattr(module, "feature_extractor", None)
    if extractor is None:
        return False, "模型没有特征提取骨干"

    class _Backbone(torch.nn.Module):
        """只保留第一个输出特征图，便于 ONNX 导出。"""

        def __init__(self, wrapped):
            super().__init__()
            self.wrapped = wrapped

        def forward(self, x):
            out = self.wrapped(x)
            if isinstance(out, dict):
                out = next(iter(out.values()))
            elif isinstance(out, (list, tuple)):
                out = out[0]
            return out

    try:
        torch.onnx.export(
            _Backbone(extractor).eval(),
            torch.zeros(1, 3, int(imgsz), int(imgsz)),
            str(target),
            opset_version=12,
            input_names=["input"],
            output_names=["features"],
            dynamic_axes={"input": {0: "batch", 2: "height", 3: "width"}},
        )
    except Exception as exc:  # noqa: BLE001 - ONNX 导出失败不应中断打包
        logger.warning("骨干 ONNX 导出失败: %s", exc)
        try:
            if target.exists():
                target.unlink()
        except OSError:
            pass
        return False, f"{type(exc).__name__}: {exc}"
    return True, ""


def _to_list(value) -> list:
    """把张量 / 标量 / 列表统一转为 Python 列表。"""
    if value is None:
        return []
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]
