"""标注 ViewModel：图片列表、当前标注、保存与格式互通。

标注以 **YOLO txt** 落盘到数据集来源目录的 `labels/` 下，
这样既符合 YOLO 约定，也能被后续的数据集划分与训练流程直接消费。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from src.models.annotation import POLYGON, Annotation, ImageAnnotation
from src.services.annotation_service import AnnotationService
from src.services.autolabel_service import (
    DEFAULT_DETECT_WEIGHTS,
    DEFAULT_SAM_WEIGHTS,
    AutoLabelService,
)
from src.services.dataset_service import DatasetService
from src.utils.constants import PROJECT_ANNOTATION
from src.utils.logger import get_logger
from src.utils.workers import FunctionWorker
from src.viewmodels.dataset_vm import DatasetViewModel
from src.viewmodels.project_vm import ProjectViewModel

logger = get_logger("annotate_vm")


class AnnotateViewModel(QObject):
    """图像标注业务逻辑。"""

    imageListChanged = Signal(list)      # list[Path]
    imageChanged = Signal(int)           # 当前图片索引
    annotationLoaded = Signal(object)    # ImageAnnotation（无图片时为 None）
    noteLoaded = Signal(str)             # 当前图片的备注文本
    statusChanged = Signal(int, int)     # 已标注张数, 总张数
    taskStarted = Signal(str)            # 后台任务开始（任务名）
    taskProgress = Signal(int, str)      # 百分比, 描述
    taskFinished = Signal(str)           # 完成描述
    taskFailed = Signal(str)             # 失败描述
    message = Signal(str, str)           # level, text

    def __init__(
        self,
        dataset_vm: DatasetViewModel | None = None,
        project_vm: ProjectViewModel | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._dataset_vm = dataset_vm
        self._project_vm = project_vm
        self._images: list[Path] = []
        self._index = -1
        self._current: ImageAnnotation | None = None
        self._dirty = False
        self._worker: FunctionWorker | None = None
        # 数据集变化（导入 / 重新划分）后自动重建图片列表
        if dataset_vm is not None:
            dataset_vm.datasetChanged.connect(self._on_dataset_changed)

    def _on_dataset_changed(self, _dataset) -> None:
        # 数据集已切换，旧的未保存改动不再回写（避免写错目录）
        self._dirty = False
        self.refresh()

    # -----------------------------------------------------------
    # 查询
    # -----------------------------------------------------------
    @property
    def images(self) -> list[Path]:
        return self._images

    @property
    def index(self) -> int:
        return self._index

    @property
    def current(self) -> ImageAnnotation | None:
        return self._current

    def current_path(self) -> Path | None:
        if 0 <= self._index < len(self._images):
            return self._images[self._index]
        return None

    def label_dir(self) -> Path | None:
        """标签输出目录：<数据集来源>/labels（源目录缺失时用归档恢复目录）。"""
        if self._dataset_vm is None:
            return None
        return self._dataset_vm.label_dir()

    def class_items(self) -> list:
        """当前项目的缺陷类别定义。"""
        project = self._project_vm.project if self._project_vm else None
        return list(project.classes) if project is not None else []

    # -----------------------------------------------------------
    # 备注（以 sidecar 文本落盘，避免每次编辑都重打包项目）
    # -----------------------------------------------------------
    def notes_dir(self) -> Path | None:
        """备注目录：项目文件同级的 `notes/`。

        不放在数据集来源目录里 —— 否则 `.txt` 备注会被数据集扫描当成
        YOLO 标签文件，污染类别列表与标注判定。
        """
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return None
        path = str(project.params.get("path", "") or "")
        if not path:
            return None
        return Path(path).parent / "notes"

    def load_note(self, image_path) -> str:
        """读取某张图片的备注。"""
        notes = self.notes_dir()
        if notes is None or image_path is None:
            return ""
        file = notes / f"{Path(image_path).stem}.txt"
        try:
            return file.read_text(encoding="utf-8") if file.is_file() else ""
        except OSError as exc:
            logger.warning("读取备注失败: %s", exc)
            return ""

    def save_note(self, image_path, text: str) -> None:
        """保存某张图片的备注（空内容则删除文件）。"""
        notes = self.notes_dir()
        if notes is None or image_path is None:
            return
        file = notes / f"{Path(image_path).stem}.txt"
        try:
            if text.strip():
                notes.mkdir(parents=True, exist_ok=True)
                file.write_text(text, encoding="utf-8")
            elif file.is_file():
                file.unlink()
        except OSError as exc:
            logger.warning("保存备注失败: %s", exc)

    def annotated_count(self) -> int:
        """已标注张数（与图库 / 检查页共用同一判定口径）。"""
        if self._dataset_vm is not None:
            return self._dataset_vm.annotated_count()
        label_dir = self.label_dir()
        if label_dir is None:
            return 0
        return sum(
            1 for image in self._images
            if DatasetService.label_has_content(
                AnnotationService.label_path_for(image, label_dir)
            )
        )

    def annotated_flags(self) -> list[bool]:
        """每张图片是否已标注（用于缩略图角标）。"""
        if self._dataset_vm is not None:
            return self._dataset_vm.annotated_flags()
        label_dir = self.label_dir()
        if label_dir is None:
            return [False] * len(self._images)
        return [
            DatasetService.label_has_content(
                AnnotationService.label_path_for(image, label_dir)
            )
            for image in self._images
        ]

    def notify(self, text: str, level: str = "info") -> None:
        """供界面反馈本地操作结果的提示通道。"""
        self.message.emit(level, text)

    def annotation_mode(self) -> str:
        """当前项目的标注方式：none / box / obb / polygon。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return "box"
        return PROJECT_ANNOTATION.get(project.model_type, "box")

    def mark_dirty(self) -> None:
        """画布等外部修改了标注内容后标记为待保存。"""
        self._dirty = True

    # -----------------------------------------------------------
    # 命令
    # -----------------------------------------------------------
    def refresh(self) -> None:
        """按当前数据集重建图片列表（源目录缺失时从项目归档恢复）。"""
        source = self._dataset_vm.resolved_source() if self._dataset_vm else None
        if source is None:
            self._images = []
            self._index = -1
            self._current = None
            self.imageListChanged.emit([])
            self.annotationLoaded.emit(None)
            self.statusChanged.emit(0, 0)
            return

        self._images = (
            self._dataset_vm.images(refresh=True)
            if self._dataset_vm is not None
            else DatasetService.scan_images(source)
        )
        self.imageListChanged.emit(list(self._images))
        if not self._images:
            self._index = -1
            self._current = None
            self.annotationLoaded.emit(None)
            self.statusChanged.emit(0, 0)
            return
        self._index = -1
        self.set_current(0)

    def set_image_by_path(self, path) -> int:
        """按路径定位图片并切换过去；未找到返回 -1。"""
        if path is None:
            return -1
        target = Path(path)
        for index, image in enumerate(self._images):
            if image == target:
                self.set_current(index)
                return index
        return -1

    def set_current(self, index: int) -> None:
        """切换当前图片（自动保存上一张的未保存改动）。"""
        if index < 0 or index >= len(self._images):
            return
        self._autosave()
        self._index = index
        path = self._images[index]
        width, height = AnnotationService.image_size(path)

        annotation = ImageAnnotation(name=path.name, width=width, height=height)
        label_dir = self.label_dir()
        if label_dir is not None:
            label_path = AnnotationService.label_path_for(path, label_dir)
            if label_path.is_file():
                annotation = AnnotationService.load_yolo(label_path, width, height)
                annotation.name = path.name
                annotation.width = annotation.width or width
                annotation.height = annotation.height or height

        self._current = annotation
        self._dirty = False
        self.imageChanged.emit(index)
        self.annotationLoaded.emit(annotation)
        self.noteLoaded.emit(self.load_note(path))
        self._emit_status()

    def next_image(self) -> None:
        if self._index + 1 < len(self._images):
            self.set_current(self._index + 1)

    def prev_image(self) -> None:
        if self._index - 1 >= 0:
            self.set_current(self._index - 1)

    def add_annotation(self, cls_id: int, kind: str, points: list) -> None:
        """向当前图片添加一个标注。"""
        if self._current is None:
            self.message.emit("warning", "请先导入数据集并选择图片")
            return
        self._current.items.append(
            Annotation(cls_id=cls_id, kind=kind, points=list(points))
        )
        self._dirty = True
        self.annotationLoaded.emit(self._current)

    def remove_annotation(self, index: int) -> None:
        """删除当前图片的第 index 个标注。"""
        if self._current is None or not (0 <= index < len(self._current.items)):
            return
        self._current.items.pop(index)
        self._dirty = True
        self.annotationLoaded.emit(self._current)

    def clear_annotations(self) -> None:
        if self._current is None:
            return
        self._current.items.clear()
        self._dirty = True
        self.annotationLoaded.emit(self._current)

    def save(self) -> None:
        """保存当前图片的标注。"""
        path = self.current_path()
        label_dir = self.label_dir()
        if path is None or label_dir is None or self._current is None:
            self.message.emit("warning", "请先导入数据集")
            return
        AnnotationService.save_yolo(
            self._current,
            AnnotationService.label_path_for(path, label_dir),
        )
        self._dirty = False
        self._emit_status()
        self.message.emit("success", f"标注已保存：{path.name}")

    # -----------------------------------------------------------
    # 半自动预标注
    # -----------------------------------------------------------
    def is_busy(self) -> bool:
        """是否有后台任务在运行。"""
        return self._worker is not None and self._worker.isRunning()

    def preannotate(
        self,
        weights: str = DEFAULT_DETECT_WEIGHTS,
        conf: float = 0.25,
        iou: float = 0.45,
        device: str = "auto",
        only_unlabeled: bool = True,
    ) -> None:
        """批量 YOLO 预标注；默认只填充尚未标注的图片，避免覆盖人工标注。"""
        if self.is_busy():
            self.message.emit("warning", "已有任务在运行")
            return
        label_dir = self.label_dir()
        if label_dir is None or not self._images:
            self.message.emit("warning", "请先导入数据集")
            return
        if not AutoLabelService.is_available():
            self.message.emit("error", "未安装 Ultralytics，无法执行预标注")
            return

        targets = [
            image for image in self._images
            if not (
                only_unlabeled
                and AnnotationService.label_path_for(image, label_dir).is_file()
            )
        ]
        if not targets:
            self.message.emit("info", "没有需要预标注的图片")
            return

        self._autosave()

        def job(progress, is_cancelled):
            detected = AutoLabelService.detect(
                weights, targets, conf=conf, iou=iou, device=device,
                progress=progress, is_cancelled=is_cancelled,
            )
            written = 0
            for image in targets:
                if is_cancelled():
                    break
                items = detected.get(image.name)
                if not items:
                    continue
                width, height = AnnotationService.image_size(image)
                annotation = ImageAnnotation(
                    name=image.name, width=width, height=height, items=items
                )
                AnnotationService.save_yolo(
                    annotation, AnnotationService.label_path_for(image, label_dir)
                )
                written += 1
            return f"预标注完成：写入 {written} / {len(targets)} 张"

        self._start_worker(job, "预标注")

    def refine_with_sam(
        self, index: int, weights: str = DEFAULT_SAM_WEIGHTS
    ) -> None:
        """用 SAM 把当前图片的第 index 个标注细化为多边形。"""
        if self.is_busy():
            self.message.emit("warning", "已有任务在运行")
            return
        if self._current is None or not (0 <= index < len(self._current.items)):
            self.message.emit("warning", "请先在画布或列表中选择一个标注")
            return
        path = self.current_path()
        if path is None:
            self.message.emit("warning", "请先导入数据集")
            return

        item = self._current.items[index]
        cls_id = item.cls_id
        width = self._current.width
        height = self._current.height
        if not width or not height:
            width, height = AnnotationService.image_size(path)
            self._current.width, self._current.height = width, height
        x1, y1, x2, y2 = item.bounds()
        box = [x1 * width, y1 * height, x2 * width, y2 * height]

        def job(progress, _is_cancelled):
            progress(20, "SAM 分割中")
            return AutoLabelService.segment(
                str(path), boxes=[box], weights=weights
            )

        def done(polygons):
            self._apply_sam_result(index, cls_id, polygons, path)

        self._start_worker(job, "SAM 细化", on_done=done)

    # -----------------------------------------------------------
    # 格式互通
    # -----------------------------------------------------------
    def import_annotations(self, path: str) -> None:
        """按文件类型导入标注：.json → COCO，.xml → VOC，目录 → YOLO。"""
        label_dir = self.label_dir()
        if label_dir is None:
            self.message.emit("warning", "请先导入数据集")
            return

        target = Path(path)
        try:
            if target.suffix.lower() == ".json":
                result = AnnotationService.import_coco(target)
                written = self._persist(result)
                detail = f"COCO 导入 {written} 张"
            elif target.suffix.lower() == ".xml":
                name_to_id = {c.name: c.cls_id for c in self.class_items()}
                annotation = AnnotationService.import_voc(target, name_to_id)
                written = self._persist({annotation.name: annotation})
                detail = f"VOC 导入 {written} 张"
            elif target.is_dir():
                written = 0
                for label in sorted(target.glob("*.txt")):
                    shutil.copy2(label, Path(label_dir) / label.name)
                    written += 1
                detail = f"YOLO 导入 {written} 个标签"
            else:
                self.message.emit("warning", "无法识别的标注格式")
                return
        except Exception as exc:  # noqa: BLE001 - 文件/解析异常类型较多
            logger.warning("导入标注失败: %s", exc)
            self.message.emit("error", f"导入标注失败：{exc}")
            return

        self._reload_current()
        self.message.emit("success", detail)

    def export_annotations(self, path: str, fmt: str) -> None:
        """导出标注：fmt 为 coco / voc / yolo。"""
        label_dir = self.label_dir()
        if label_dir is None:
            self.message.emit("warning", "请先导入数据集")
            return
        self._autosave()

        classes = [c.name for c in self.class_items()]
        try:
            if fmt == "coco":
                annotations = self._load_all()
                AnnotationService.export_coco(annotations, classes, path)
                detail = f"已导出 COCO：{path}"
            elif fmt == "voc":
                out_dir = Path(path)
                annotations = self._load_all()
                for annotation in annotations:
                    AnnotationService.export_voc(annotation, classes, out_dir)
                detail = f"已导出 VOC：{out_dir}（{len(annotations)} 个 xml）"
            else:
                out_dir = Path(path)
                out_dir.mkdir(parents=True, exist_ok=True)
                count = 0
                for image in self._images:
                    label = AnnotationService.label_path_for(image, label_dir)
                    if label.is_file():
                        shutil.copy2(label, out_dir / label.name)
                        count += 1
                detail = f"已导出 YOLO 标签 {count} 个：{out_dir}"
        except Exception as exc:  # noqa: BLE001 - 文件/序列化异常类型较多
            logger.warning("导出标注失败: %s", exc)
            self.message.emit("error", f"导出标注失败：{exc}")
            return
        self.message.emit("success", detail)

    # -----------------------------------------------------------
    # 内部
    # -----------------------------------------------------------
    def _load_all(self) -> list[ImageAnnotation]:
        """读取全部图片的标注（仅包含已有标签文件的图片）。"""
        label_dir = self.label_dir()
        result: list[ImageAnnotation] = []
        if label_dir is None:
            return result
        for image in self._images:
            label = AnnotationService.label_path_for(image, label_dir)
            if not label.is_file():
                continue
            width, height = AnnotationService.image_size(image)
            annotation = AnnotationService.load_yolo(label, width, height)
            annotation.name = image.name
            result.append(annotation)
        return result

    def _persist(self, result: dict) -> int:
        """把 {文件名: ImageAnnotation} 写为 YOLO 标签。"""
        label_dir = self.label_dir()
        if label_dir is None:
            return 0
        written = 0
        for name, annotation in result.items():
            AnnotationService.save_yolo(
                annotation, Path(label_dir) / f"{Path(name).stem}.txt"
            )
            written += 1
        return written

    def _autosave(self) -> None:
        if not self._dirty or self._current is None:
            return
        path = self.current_path()
        label_dir = self.label_dir()
        if path is None or label_dir is None:
            return
        AnnotationService.save_yolo(
            self._current,
            AnnotationService.label_path_for(path, label_dir),
        )
        self._dirty = False

    def _reload_current(self) -> None:
        if self._index >= 0:
            index = self._index
            self._index = -1
            self.set_current(index)

    def _emit_status(self) -> None:
        self.statusChanged.emit(self.annotated_count(), len(self._images))

    # -----------------------------------------------------------
    # 后台任务
    # -----------------------------------------------------------
    def _start_worker(self, job, name: str, on_done=None) -> None:
        """启动后台任务，并把进度 / 结果 / 错误转到界面。"""
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
        self._reload_current()

    def _on_task_failed(self, name: str, error: str) -> None:
        logger.warning("%s失败: %s", name, error)
        self.taskFailed.emit(f"{name}失败：{error}")

    def _apply_sam_result(self, index: int, cls_id: int, polygons: list, path) -> None:
        """用 SAM 多边形替换原标注。"""
        current_path = self.current_path()
        if current_path is None or current_path.name != Path(path).name:
            raise ValueError("图片已切换，结果未应用")
        if not polygons:
            raise ValueError("SAM 未返回有效轮廓")
        if self._current is None:
            return
        self._current.items.pop(index)
        for offset, points in enumerate(polygons):
            self._current.items.insert(
                index + offset,
                Annotation(cls_id=cls_id, kind=POLYGON, points=points),
            )
        self._dirty = True
        self.save()
