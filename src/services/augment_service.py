"""数据增强服务：基于 Albumentations 的在线 / 离线增强。

支持检测框（YOLO bbox 格式）与分割多边形（关键点方式）的同步变换，
保证几何变换后标签与图像仍然对齐。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.models.annotation import Annotation, ImageAnnotation
from src.services.annotation_service import AnnotationService
from src.utils.logger import get_logger

logger = get_logger("augment")

ProgressFn = Callable[[int, str], None]


@dataclass
class AugmentConfig:
    """数据增强配置（概率均为 0~1）。"""

    hflip: float = 0.5          # 水平翻转
    vflip: float = 0.0          # 垂直翻转
    rotate: float = 15.0        # 旋转角度上限
    scale: float = 0.1          # 缩放幅度
    brightness: float = 0.2     # 亮度变化幅度
    contrast: float = 0.2       # 对比度变化幅度
    noise: float = 0.0          # 高斯噪声概率
    blur: float = 0.0           # 高斯模糊概率
    copies: int = 1             # 每张图生成的增强份数

    def to_dict(self) -> dict:
        return {
            "hflip": self.hflip, "vflip": self.vflip, "rotate": self.rotate,
            "scale": self.scale, "brightness": self.brightness,
            "contrast": self.contrast, "noise": self.noise, "blur": self.blur,
            "copies": self.copies,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AugmentConfig":
        return cls(
            hflip=data.get("hflip", 0.5), vflip=data.get("vflip", 0.0),
            rotate=data.get("rotate", 15.0), scale=data.get("scale", 0.1),
            brightness=data.get("brightness", 0.2),
            contrast=data.get("contrast", 0.2),
            noise=data.get("noise", 0.0), blur=data.get("blur", 0.0),
            copies=data.get("copies", 1),
        )


def _noop_progress(_percent: int, _text: str = "") -> None:
    pass


def _load_rgb(path: str | Path):
    """读取图片为 RGB numpy 数组。"""
    import numpy as np
    from PIL import Image

    with Image.open(path) as img:
        return np.asarray(img.convert("RGB"))


def _save_rgb(array, path: str | Path) -> None:
    from PIL import Image

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(array).save(path)


class AugmentService:
    """图像与标注的同步增强（静态方法）。"""

    # -----------------------------------------------------------
    # 可用性
    # -----------------------------------------------------------
    @staticmethod
    def is_available() -> bool:
        """Albumentations 是否可用。"""
        try:
            import albumentations  # noqa: F401
        except ImportError:
            return False
        return True

    # -----------------------------------------------------------
    # 流水线
    # -----------------------------------------------------------
    @staticmethod
    def build_pipeline(config: AugmentConfig, seed: int | None = None):
        """按配置构建 Albumentations 组合流水线。"""
        import albumentations as A

        transforms = []
        if config.hflip > 0:
            transforms.append(A.HorizontalFlip(p=config.hflip))
        if config.vflip > 0:
            transforms.append(A.VerticalFlip(p=config.vflip))
        if config.rotate > 0 or config.scale > 0:
            transforms.append(A.Affine(
                scale=(max(0.1, 1 - config.scale), 1 + config.scale),
                rotate=(-config.rotate, config.rotate),
                p=0.5,
            ))
        if config.brightness > 0 or config.contrast > 0:
            transforms.append(A.RandomBrightnessContrast(
                brightness_limit=config.brightness,
                contrast_limit=config.contrast,
                p=0.5,
            ))
        if config.noise > 0:
            transforms.append(A.GaussNoise(p=config.noise))
        if config.blur > 0:
            transforms.append(A.GaussianBlur(blur_limit=(3, 7), p=config.blur))

        return A.Compose(
            transforms,
            bbox_params=A.BboxParams(
                format="yolo", label_fields=["class_labels"], min_visibility=0.0,
            ),
            keypoint_params=A.KeypointParams(format="xy", remove_invisible=False),
            seed=seed,
        )

    # -----------------------------------------------------------
    # 单图增强
    # -----------------------------------------------------------
    @staticmethod
    def augment(
        image_rgb,
        annotations: list[Annotation],
        config: AugmentConfig,
        count: int = 1,
        seed: int = 0,
    ) -> list[tuple[object, list[Annotation]]]:
        """对一张图片生成 `count` 份增强样本。

        Returns:
            [(增强后的 RGB 数组, 增强后的标注列表), ...]
        """
        import albumentations as A  # noqa: F401 - 保证缺依赖时立刻报错

        height, width = image_rgb.shape[:2]
        boxes, labels, keypoints, groups = AugmentService._flatten(
            annotations, width, height
        )
        results: list[tuple[object, list[Annotation]]] = []

        for index in range(max(1, count)):
            pipeline = AugmentService.build_pipeline(config, seed=seed + index)
            transformed = pipeline(
                image=image_rgb,
                bboxes=boxes,
                class_labels=labels,
                keypoints=keypoints,
            )
            new_height, new_width = transformed["image"].shape[:2]
            items = AugmentService._rebuild(
                transformed, groups, new_width, new_height
            )
            results.append((transformed["image"], items))
        return results

    @staticmethod
    def _flatten(annotations: list[Annotation], width: int, height: int):
        """把标注拆成 bbox / keypoint 两组供 Albumentations 使用。

        注意：Albumentations 的 keypoint 使用**绝对像素坐标**，
        而 bbox 的 yolo 格式使用归一化坐标。
        """
        boxes, labels, keypoints, groups = [], [], [], []
        for index, item in enumerate(annotations):
            if item.is_box:
                x1, y1, x2, y2 = item.bounds()
                boxes.append([
                    (x1 + x2) / 2, (y1 + y2) / 2,
                    max(x2 - x1, 0.0), max(y2 - y1, 0.0),
                ])
                labels.append(index)
            else:
                start = len(keypoints)
                keypoints.extend(
                    (x * width, y * height) for x, y in item.points
                )
                groups.append({
                    "index": index,
                    "cls_id": item.cls_id,
                    "start": start,
                    "count": len(item.points),
                })
        return boxes, labels, keypoints, groups

    @staticmethod
    def _rebuild(transformed, groups, width: int, height: int) -> list[Annotation]:
        """把 Albumentations 的输出还原为归一化标注。"""
        items: list[Annotation] = []
        for box, label in zip(transformed["bboxes"], transformed["class_labels"]):
            cx, cy, box_w, box_h = (float(v) for v in box)
            items.append(Annotation(
                cls_id=int(label),
                kind="box",
                points=[
                    (max(cx - box_w / 2, 0.0), max(cy - box_h / 2, 0.0)),
                    (min(cx + box_w / 2, 1.0), min(cy + box_h / 2, 1.0)),
                ],
            ))

        keypoints = transformed.get("keypoints") or []
        for group in groups:
            chunk = keypoints[group["start"]:group["start"] + group["count"]]
            if len(chunk) != group["count"]:
                continue
            points = [
                (
                    min(max(float(point[0]) / max(width, 1), 0.0), 1.0),
                    min(max(float(point[1]) / max(height, 1), 0.0), 1.0),
                )
                for point in chunk
            ]
            items.append(Annotation(
                cls_id=group["cls_id"], kind="polygon", points=points,
            ))
        return items

    # -----------------------------------------------------------
    # 预览 / 离线增强
    # -----------------------------------------------------------
    @staticmethod
    def preview(
        image_path: str | Path, annotations: list[Annotation],
        config: AugmentConfig, count: int = 6, seed: int = 0,
    ) -> list:
        """生成若干增强预览图（RGB 数组列表）。"""
        image = _load_rgb(image_path)
        return [
            array
            for array, _items in AugmentService.augment(image, annotations, config, count, seed)
        ]

    @staticmethod
    def augment_dataset(
        images: list,
        label_index: dict,
        out_dir: str | Path,
        config: AugmentConfig,
        progress: ProgressFn | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> dict:
        """离线增强：为每个「已标注」图片生成 copies 份副本。

        输出结构：<out_dir>/images/ 与 <out_dir>/labels/。
        """
        progress = progress or _noop_progress
        is_cancelled = is_cancelled or (lambda: False)

        out_dir = Path(out_dir)
        images_dir = out_dir / "images"
        labels_dir = out_dir / "labels"
        images_dir.mkdir(parents=True, exist_ok=True)
        labels_dir.mkdir(parents=True, exist_ok=True)

        written = 0
        skipped = 0
        total = len(images) or 1
        for index, image_path in enumerate(images):
            if is_cancelled():
                break
            image_path = Path(image_path)
            label = label_index.get(image_path.stem)
            if label is None:
                skipped += 1
                continue

            width, height = AnnotationService.image_size(image_path)
            annotation = AnnotationService.load_yolo(label, width, height)
            if not annotation.items:
                skipped += 1
                continue

            array = _load_rgb(image_path)
            augmented = AugmentService.augment(
                array, annotation.items, config, config.copies, seed=index
            )
            for offset, (new_array, items) in enumerate(augmented):
                name = f"{image_path.stem}_aug{offset}{image_path.suffix}"
                _save_rgb(new_array, images_dir / name)
                AnnotationService.save_yolo(
                    ImageAnnotation(
                        name=name,
                        width=new_array.shape[1],
                        height=new_array.shape[0],
                        items=items,
                    ),
                    labels_dir / f"{name.rsplit('.', 1)[0]}.txt",
                )
                written += 1
            progress(
                int(100 * (index + 1) / total),
                f"已增强 {index + 1}/{total}",
            )

        logger.info("离线增强完成：新增 %s 张，跳过 %s 张", written, skipped)
        return {"written": written, "skipped": skipped}
