"""标注服务：YOLO / COCO / Pascal VOC 的读写与格式转换。

项目内以 **YOLO txt** 为准（训练直接消费）；COCO / VOC 仅作导入导出互通，
用于兼容 X-AnyLabeling、CVAT、Label Studio 等外部工具的产物。
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from src.models.annotation import BOX, POLYGON, TEXT, Annotation, ImageAnnotation
from src.utils.logger import get_logger

logger = get_logger("annotation")


class AnnotationService:
    """标注文件读写与格式转换（静态方法）。"""

    # -----------------------------------------------------------
    # 通用
    # -----------------------------------------------------------
    @staticmethod
    def image_size(path: str | Path) -> tuple[int, int]:
        """读取图片尺寸 (宽, 高)，失败返回 (0, 0)。"""
        from PIL import Image

        try:
            with Image.open(path) as img:
                return int(img.width), int(img.height)
        except Exception as exc:  # noqa: BLE001 - 图片损坏或格式异常
            logger.warning("读取图片尺寸失败 %s: %s", path, exc)
            return 0, 0

    @staticmethod
    def label_path_for(image_path: str | Path, label_dir: str | Path) -> Path:
        """按 YOLO 约定返回图片对应的标签文件路径。"""
        return Path(label_dir) / f"{Path(image_path).stem}.txt"

    # -----------------------------------------------------------
    # OCR 文本（旁路存储）
    # -----------------------------------------------------------
    @staticmethod
    def text_sidecar_path(label_path: str | Path) -> Path:
        """文本框转写的旁路文件路径。

        YOLO txt 的一行只有「类别 + 坐标」，放不下转写文本，因此单独存放：
        `<标签目录>/texts/<图片主干>.txt`，每行 `cx cy<TAB>文本`，
        以**归一化中心点**为锚点；框被移动或删除后该条转写即失效（不会串到别的框）。
        """
        label_path = Path(label_path)
        return label_path.parent / "texts" / f"{label_path.stem}.txt"

    @staticmethod
    def save_texts(annotation: ImageAnnotation, label_path: str | Path) -> Path | None:
        """写出文本转写；没有文本框时删除旧旁路文件。"""
        items = [item for item in annotation.items if item.is_text]
        path = AnnotationService.text_sidecar_path(label_path)
        if not items:
            if path.exists():
                try:
                    path.unlink()
                except OSError as exc:
                    logger.warning("删除文本框旁路文件失败 %s: %s", path, exc)
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        for item in items:
            cx, cy = item.center
            text = str(item.text or "").replace("\t", " ").replace("\n", " ")
            lines.append(f"{cx:.6f} {cy:.6f}\t{text}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    @staticmethod
    def load_texts(
        annotation: ImageAnnotation, label_path: str | Path, tolerance: float = 2e-3
    ) -> int:
        """把旁路文件里的转写按中心点绑回矩形项（并标记为文本框），返回绑定条数。"""
        path = AnnotationService.text_sidecar_path(label_path)
        if not path.is_file():
            return 0
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.warning("读取文本框旁路文件失败 %s: %s", path, exc)
            return 0

        entries: list[tuple[float, float, str]] = []
        for line in lines:
            head, _, text = line.partition("\t")
            parts = head.split()
            if len(parts) < 2:
                continue
            try:
                entries.append((float(parts[0]), float(parts[1]), text))
            except ValueError:
                continue

        bound = 0
        used: set[int] = set()
        for item in annotation.items:
            if not item.is_rect:
                continue
            cx, cy = item.center
            for index, (ex, ey, text) in enumerate(entries):
                if index in used:
                    continue
                if abs(ex - cx) <= tolerance and abs(ey - cy) <= tolerance:
                    used.add(index)
                    item.kind = TEXT
                    item.text = text
                    bound += 1
                    break
        return bound

    # -----------------------------------------------------------
    # YOLO
    # -----------------------------------------------------------
    @staticmethod
    def load_yolo(
        label_path: str | Path, width: int = 0, height: int = 0
    ) -> ImageAnnotation:
        """读取 YOLO 标签：5 字段为矩形框，>= 6 且为偶数为多边形。"""
        label_path = Path(label_path)
        annotation = ImageAnnotation(
            name=label_path.stem, width=width, height=height
        )
        if not label_path.is_file():
            return annotation

        try:
            lines = label_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.warning("读取标签失败 %s: %s", label_path, exc)
            return annotation

        for line in lines:
            parts = line.split()
            if len(parts) < 5:
                continue
            try:
                cls_id = int(float(parts[0]))
                values = [float(v) for v in parts[1:]]
            except ValueError:
                continue

            if len(values) == 4:
                cx, cy, w, h = values
                annotation.items.append(Annotation(
                    cls_id=cls_id,
                    kind=BOX,
                    points=[(cx - w / 2, cy - h / 2), (cx + w / 2, cy + h / 2)],
                ))
            elif len(values) >= 6 and len(values) % 2 == 0:
                points = [(values[i], values[i + 1]) for i in range(0, len(values), 2)]
                annotation.items.append(Annotation(
                    cls_id=cls_id, kind=POLYGON, points=points,
                ))
        # 文本框转写（OCR）：按中心点绑回矩形项
        AnnotationService.load_texts(annotation, label_path)
        return annotation

    @staticmethod
    def save_yolo(annotation: ImageAnnotation, label_path: str | Path) -> Path:
        """写出 YOLO 标签文件。"""
        label_path = Path(label_path)
        label_path.parent.mkdir(parents=True, exist_ok=True)

        lines: list[str] = []
        for item in annotation.items:
            if item.is_rect:
                x1, y1, x2, y2 = item.bounds()
                values = [
                    (x1 + x2) / 2, (y1 + y2) / 2, abs(x2 - x1), abs(y2 - y1),
                ]
            else:
                values = [value for point in item.points for value in point]
            if not values:
                continue
            lines.append(
                " ".join([str(item.cls_id)] + [f"{v:.6f}" for v in values])
            )

        label_path.write_text(
            "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
        )
        # 文本框转写单独落盘（YOLO txt 无承载字段）
        AnnotationService.save_texts(annotation, label_path)
        return label_path

    # -----------------------------------------------------------
    # COCO
    # -----------------------------------------------------------
    @staticmethod
    def export_coco(
        annotations: list[ImageAnnotation], classes: list[str], out_path: str | Path
    ) -> Path:
        """导出为 COCO json（category_id 由 cls_id + 1 得到）。"""
        out_path = Path(out_path)
        images, records, categories = [], [], []
        for index, name in enumerate(classes):
            categories.append({"id": index + 1, "name": name, "supercategory": ""})

        ann_id = 1
        for img_id, annotation in enumerate(annotations, 1):
            width = annotation.width or 1
            height = annotation.height or 1
            images.append({
                "id": img_id,
                "file_name": annotation.name,
                "width": annotation.width,
                "height": annotation.height,
            })
            for item in annotation.items:
                x1, y1, x2, y2 = item.bounds()
                box_w = (x2 - x1) * width
                box_h = (y2 - y1) * height
                record = {
                    "id": ann_id,
                    "image_id": img_id,
                    "category_id": item.cls_id + 1,
                    "bbox": [
                        round(x1 * width, 2), round(y1 * height, 2),
                        round(box_w, 2), round(box_h, 2),
                    ],
                    "area": round(box_w * box_h, 2),
                    "iscrowd": 0,
                }
                if item.is_polygon:
                    record["segmentation"] = [[
                        round(value, 2)
                        for point in item.points
                        for value in (point[0] * width, point[1] * height)
                    ]]
                records.append(record)
                ann_id += 1

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(
                {"images": images, "annotations": records, "categories": categories},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        logger.info("导出 COCO：%s（%s 张图 / %s 个对象）",
                    out_path, len(images), len(records))
        return out_path

    @staticmethod
    def import_coco(json_path: str | Path) -> dict[str, ImageAnnotation]:
        """读取 COCO json，返回 {图片文件名: ImageAnnotation}。"""
        data = json.loads(Path(json_path).read_text(encoding="utf-8"))
        cat_to_cls = {
            cat["id"]: int(cat["id"]) - 1 for cat in data.get("categories", [])
        }

        result: dict[str, ImageAnnotation] = {}
        by_id: dict = {}
        for info in data.get("images", []):
            name = Path(str(info.get("file_name", ""))).name
            by_id[info["id"]] = name
            result[name] = ImageAnnotation(
                name=name,
                width=int(info.get("width", 0) or 0),
                height=int(info.get("height", 0) or 0),
            )

        for record in data.get("annotations", []):
            name = by_id.get(record.get("image_id"))
            if name is None or name not in result:
                continue
            annotation = result[name]
            width = annotation.width or 1
            height = annotation.height or 1
            category_id = record.get("category_id", 1)
            cls_id = cat_to_cls.get(category_id, int(category_id) - 1)

            seg = record.get("segmentation")
            if isinstance(seg, list) and seg and isinstance(seg[0], list) and len(seg[0]) >= 6:
                flat = seg[0]
                points = [
                    (flat[i] / width, flat[i + 1] / height)
                    for i in range(0, len(flat) - 1, 2)
                ]
                annotation.items.append(Annotation(
                    cls_id=cls_id, kind=POLYGON, points=points,
                ))
            else:
                bx, by, bw, bh = (list(record.get("bbox", [0, 0, 0, 0])) + [0, 0, 0, 0])[:4]
                annotation.items.append(Annotation(
                    cls_id=cls_id,
                    kind=BOX,
                    points=[
                        (bx / width, by / height),
                        ((bx + bw) / width, (by + bh) / height),
                    ],
                ))
        logger.info("导入 COCO：%s（%s 张图）", json_path, len(result))
        return result

    # -----------------------------------------------------------
    # Pascal VOC
    # -----------------------------------------------------------
    @staticmethod
    def export_voc(
        annotation: ImageAnnotation, classes: list[str], out_dir: str | Path
    ) -> Path:
        """导出为 Pascal VOC xml（每个标注对象输出一个 bndbox）。"""
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        root = ET.Element("annotation")
        ET.SubElement(root, "filename").text = annotation.name
        size = ET.SubElement(root, "size")
        ET.SubElement(size, "width").text = str(annotation.width)
        ET.SubElement(size, "height").text = str(annotation.height)
        ET.SubElement(size, "depth").text = "3"

        for item in annotation.items:
            obj = ET.SubElement(root, "object")
            name = (
                classes[item.cls_id]
                if 0 <= item.cls_id < len(classes)
                else str(item.cls_id)
            )
            ET.SubElement(obj, "name").text = name
            ET.SubElement(obj, "difficult").text = "0"
            x1, y1, x2, y2 = item.bounds()
            bnd = ET.SubElement(obj, "bndbox")
            ET.SubElement(bnd, "xmin").text = str(max(0, round(x1 * annotation.width)))
            ET.SubElement(bnd, "ymin").text = str(max(0, round(y1 * annotation.height)))
            ET.SubElement(bnd, "xmax").text = str(round(x2 * annotation.width))
            ET.SubElement(bnd, "ymax").text = str(round(y2 * annotation.height))

        out_path = out_dir / f"{Path(annotation.name).stem}.xml"
        ET.ElementTree(root).write(out_path, encoding="utf-8", xml_declaration=True)
        return out_path

    @staticmethod
    def import_voc(
        xml_path: str | Path, name_to_id: dict[str, int]
    ) -> ImageAnnotation:
        """读取 Pascal VOC xml，类别名按 name_to_id 映射为 cls_id。"""
        xml_path = Path(xml_path)
        root = ET.parse(xml_path).getroot()
        size = root.find("size")
        width = int(size.findtext("width") or 0) if size is not None else 0
        height = int(size.findtext("height") or 0) if size is not None else 0
        annotation = ImageAnnotation(
            name=root.findtext("filename") or xml_path.stem,
            width=width,
            height=height,
        )
        safe_w = width or 1
        safe_h = height or 1

        for obj in root.findall("object"):
            name = (obj.findtext("name") or "").strip()
            cls_id = name_to_id.get(name)
            if cls_id is None:
                continue
            bnd = obj.find("bndbox")
            if bnd is None:
                continue
            try:
                x1 = float(bnd.findtext("xmin"))
                y1 = float(bnd.findtext("ymin"))
                x2 = float(bnd.findtext("xmax"))
                y2 = float(bnd.findtext("ymax"))
            except (TypeError, ValueError):
                continue
            annotation.items.append(Annotation(
                cls_id=cls_id,
                kind=BOX,
                points=[(x1 / safe_w, y1 / safe_h), (x2 / safe_w, y2 / safe_h)],
            ))
        return annotation
