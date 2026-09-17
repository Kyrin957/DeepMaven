"""数据集 ViewModel：导入预览、统计、划分与归档。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from src.models.dataset import Dataset
from src.services.annotation_service import AnnotationService
from src.services.augment_service import AugmentService
from src.services.dataset_service import DatasetService
from src.utils.constants import IMAGE_EXTS, LABEL_EXTS
from src.utils.logger import get_logger
from src.utils.workers import FunctionWorker
from src.viewmodels.project_vm import ProjectViewModel

logger = get_logger("dataset_vm")


class DatasetViewModel(QObject):
    """数据管理页的业务逻辑。

    划分结果落盘到项目同级的「<项目名>_dataset」目录，并批量归档进 .mprj 项目。
    """

    datasetChanged = Signal(object)      # Dataset
    datasetImported = Signal(object)     # Dataset
    splitChanged = Signal(tuple)         # (train, val, test) 比例
    datasetReady = Signal(str)           # 生成的 data.yaml 路径
    taskStarted = Signal(str)            # 后台任务开始（任务名）
    taskProgress = Signal(int, str)      # 百分比, 描述
    taskFinished = Signal(str)           # 完成描述
    taskFailed = Signal(str)             # 失败描述
    message = Signal(str, str)           # level, text

    def __init__(
        self,
        project_vm: ProjectViewModel | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._service = DatasetService()
        self._project_vm = project_vm
        self._dataset: Dataset | None = None
        self._worker: FunctionWorker | None = None

    @property
    def dataset(self) -> Dataset | None:
        return self._dataset

    # -----------------------------------------------------------
    # 命令
    # -----------------------------------------------------------
    def import_images(self, directory: str) -> Dataset:
        """导入图片文件夹并生成统计，返回去重后的数据集。"""
        dataset = self._service.summarize(directory)
        self._set_dataset(dataset)
        logger.info(
            "导入数据集 %s: %s 张图片, %s 个标签, 去重 %s 张",
            dataset.name, dataset.image_count, dataset.label_count,
            dataset.duplicate_count,
        )
        return dataset

    def import_files(self, paths: list) -> Dataset | None:
        """按选中的文件列表导入并生成统计。"""
        if not paths:
            return None
        dataset = self._service.summarize_files(paths)
        self._set_dataset(dataset)
        logger.info(
            "从文件导入 %s 张图片, %s 个标签, 去重 %s 张",
            dataset.image_count, dataset.label_count, dataset.duplicate_count,
        )
        return dataset

    def set_split(self, train: float, val: float, test: float) -> None:
        """更新划分比例（应满足 train+val+test ≈ 1）。"""
        if self._dataset is not None:
            self._dataset.split_train = train
            self._dataset.split_val = val
            self._dataset.split_test = test
        self.splitChanged.emit((train, val, test))
        self.message.emit("info", f"划分比例已更新：{train:.0%}/{val:.0%}/{test:.0%}")

    def set_stratified(self, enabled: bool) -> None:
        """切换是否按类别分层抽样。"""
        enabled = bool(enabled)
        if self._dataset is not None:
            self._dataset.stratified = enabled
        self.message.emit(
            "info", f"分层划分：{'开启' if enabled else '关闭'}"
        )

    def apply_split(self) -> None:
        """执行数据集划分：落盘为 YOLO 结构 + 生成 data.yaml + 归档进项目。"""
        if self._dataset is None or not self._dataset.source_path:
            self.message.emit("warning", "请先导入数据集")
            return
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            self.message.emit("warning", "请先创建或打开项目")
            return

        mprj = Path(project.params.get("path", ""))
        out_dir = mprj.parent / f"{mprj.stem}_dataset"
        split = (
            self._dataset.split_train,
            self._dataset.split_val,
            self._dataset.split_test,
        )
        try:
            stats = self._service.split_dataset(
                self._dataset.source_path, out_dir,
                split=split, stratified=self._dataset.stratified,
            )
            if stats["total"] == 0:
                self.message.emit("warning", "来源目录中未找到图片")
                return
            classes = self._service.load_class_names(self._dataset.source_path)
            if not classes:
                # 回退：优先用项目类别定义，其次用数据集中观察到的类别 id
                classes = (
                    project.class_names
                    or self._dataset.class_names
                )
            yaml_path = self._service.write_data_yaml(
                out_dir, classes, "images/train", "images/val", "images/test"
            )
            # 回填训练配置并归档（随本次 add_files 一并持久化）
            project.training.data_yaml = str(yaml_path)
            items = self._collect_archive_items(out_dir)
            self._project_vm.service.add_files(project, items, save=True)
        except (OSError, ValueError) as exc:
            logger.warning("数据集划分失败: %s", exc)
            self.message.emit("error", f"数据集划分失败：{exc}")
            return

        self._dataset.output_path = str(out_dir)
        self._dataset.data_yaml = str(yaml_path)
        self._dataset.class_names = classes
        self.datasetChanged.emit(self._dataset)
        self._project_vm.notify_changed()
        self.datasetReady.emit(str(yaml_path))
        self.message.emit(
            "success",
            f"划分完成：train {stats['train']} / val {stats['val']} / test {stats['test']}",
        )

    def clear(self) -> None:
        self._dataset = None
        self.datasetChanged.emit(None)

    # -----------------------------------------------------------
    # 数据增强
    # -----------------------------------------------------------
    def is_busy(self) -> bool:
        """是否有后台任务在运行。"""
        return self._worker is not None and self._worker.isRunning()

    def augment_preview(self, config, count: int = 6) -> list:
        """对第一张「已标注」图片生成增强预览（RGB 数组列表）。"""
        if not AugmentService.is_available():
            self.message.emit("error", "未安装 Albumentations，无法预览")
            return []
        images = self.source_images()
        if not images:
            self.message.emit("warning", "请先导入数据集")
            return []

        label_index = self.source_label_index()
        for image in images:
            label = label_index.get(image.stem)
            if label is None:
                continue
            width, height = AnnotationService.image_size(image)
            annotation = AnnotationService.load_yolo(label, width, height)
            if not annotation.items:
                continue
            return AugmentService.preview(image, annotation.items, config, count)

        self.message.emit("warning", "没有找到已标注的图片")
        return []

    def augment_apply(self, config) -> None:
        """离线增强：为已标注图片生成增强副本并归档进项目。"""
        if self.is_busy():
            self.message.emit("warning", "已有任务在运行")
            return
        if not AugmentService.is_available():
            self.message.emit("error", "未安装 Albumentations，无法执行增强")
            return
        images = self.source_images()
        if not images:
            self.message.emit("warning", "请先导入数据集")
            return
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            self.message.emit("warning", "请先创建或打开项目")
            return

        label_index = self.source_label_index()
        mprj = Path(project.params.get("path", ""))
        out_dir = mprj.parent / f"{mprj.stem}_augment"

        def job(progress, is_cancelled):
            stats = AugmentService.augment_dataset(
                images, label_index, out_dir, config,
                progress=progress, is_cancelled=is_cancelled,
            )
            if not is_cancelled() and stats["written"]:
                items = self._collect_archive_items(out_dir, prefix="augment")
                self._project_vm.service.add_files(project, items, save=True)
            return stats

        def done(stats):
            self._project_vm.notify_changed()
            self.message.emit(
                "success",
                f"增强完成：新增 {stats['written']} 张，跳过 {stats['skipped']} 张",
            )

        self._start_worker(job, "数据增强", on_done=done)

    # -----------------------------------------------------------
    # 质检辅助
    # -----------------------------------------------------------
    def source_images(self) -> list:
        """当前数据集的图片来源列表。"""
        if self._dataset is None or not self._dataset.source_path:
            return []
        return DatasetService.scan_images(self._dataset.source_path)

    def source_label_index(self) -> dict:
        """当前数据集的「图片主干 → 标签」索引。"""
        if self._dataset is None or not self._dataset.source_path:
            return {}
        return DatasetService.build_label_index(self._dataset.source_path)

    # -----------------------------------------------------------
    # 内部
    # -----------------------------------------------------------
    def _set_dataset(self, dataset: Dataset) -> None:
        self._dataset = dataset
        self.datasetImported.emit(dataset)
        self.datasetChanged.emit(dataset)
        self.message.emit(
            "success",
            f"导入完成：{dataset.image_count} 张图片，{dataset.class_count} 个类别"
            + (f"，剔重 {dataset.duplicate_count} 张" if dataset.duplicate_count else ""),
        )

    @staticmethod
    def _collect_archive_items(
        out_dir: Path, prefix: str = "dataset"
    ) -> list[tuple[str, str, bytes]]:
        """把输出目录读取为可归档的 (kind, virtual_path, data) 列表。"""
        items: list[tuple[str, str, bytes]] = []
        for path in sorted(out_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(out_dir).as_posix()
            suffix = path.suffix.lower()
            if suffix in IMAGE_EXTS:
                kind = "image"
            elif suffix in LABEL_EXTS:
                kind = "label"
            elif path.name in ("data.yaml", "data.yml"):
                kind = "config"
            else:
                kind = "other"
            items.append((kind, f"{prefix}/{rel}", path.read_bytes()))
        return items

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

    def _on_task_failed(self, name: str, error: str) -> None:
        logger.warning("%s失败: %s", name, error)
        self.taskFailed.emit(f"{name}失败：{error}")
