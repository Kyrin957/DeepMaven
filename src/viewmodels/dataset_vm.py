"""数据集 ViewModel：导入预览、统计、划分与归档。"""

from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from src.models.dataset import Dataset
from src.services.annotation_service import AnnotationService
from src.services.category_service import CategoryService
from src.services.quality_service import QualityService
from src.services.augment_service import AugmentService
from src.services.dataset_service import DatasetService
from src.utils.constants import (
    COVER_ENTRY_NAME,
    DEFAULT_AUGMENT_NAME,
    DEFAULT_SPLIT_NAME,
    IMAGE_EXTS,
    LABEL_EXTS,
    SPLIT_LABELS,
)
from src.utils.logger import get_logger
from src.utils.workers import FunctionWorker
from src.viewmodels.project_vm import ProjectViewModel

logger = get_logger("dataset_vm")

# 子集标记 → 划分名（与 thumbnail_grid 的 T / V / E 对应）
_SUBSET_NAME = {"T": "train", "V": "val", "E": "test"}


class DatasetViewModel(QObject):
    """数据管理页的业务逻辑。

    划分结果落盘到项目同级的 `dataset/` 目录（可在数据拆分页改名），
    图片以硬链接方式组织，不额外占用磁盘；项目文件只保存配置、标注与模型。
    """

    datasetChanged = Signal(object)      # Dataset
    datasetImported = Signal(object)     # Dataset
    splitChanged = Signal(tuple)         # (train, val, test) 比例
    datasetReady = Signal(str)           # 生成的 data.yaml 路径
    qualityReady = Signal(dict)          # 质检结果
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
        # 源目录不可用时，从项目归档释放出来的工作目录
        self._resolved_source: Path | None = None
        # 已验证可用的来源目录（避免逐张图片调用时反复全目录扫描）
        self._usable_sources: set[Path] = set()
        # 图片清单与标签索引缓存（数据量大，避免每次刷新都重新扫盘）
        self._images: list[Path] | None = None
        self._label_index: dict | None = None
        # 图片所属子集标记缓存（T/V/E）
        self._subsets: list[str] | None = None

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

    def set_split(
        self, train: float, val: float, test: float, quiet: bool = False
    ) -> None:
        """更新划分比例（应满足 train+val+test ≈ 1）。

        Args:
            quiet: 为 True 时不弹出提示（滑动条 / 数字框连续调整时使用）。
        """
        if self._dataset is not None:
            self._dataset.split_train = train
            self._dataset.split_val = val
            self._dataset.split_test = test
        self.splitChanged.emit((train, val, test))
        if not quiet:
            self.message.emit(
                "info", f"划分比例已更新：{train:.0%}/{val:.0%}/{test:.0%}"
            )

    def set_seed(self, seed: int, quiet: bool = True) -> None:
        """设置随机种子（保证划分可复现）。"""
        if self._dataset is not None:
            self._dataset.seed = int(seed)
        if not quiet:
            self.message.emit("info", f"随机种子已设为 {int(seed)}")

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
        source = self.resolved_source()
        if self._dataset is None or source is None:
            self.message.emit("warning", "请先导入数据集")
            return
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            self.message.emit("warning", "请先创建或打开项目")
            return

        mprj = Path(project.params.get("path", ""))
        out_dir = mprj.parent / self.split_name()
        split = (
            self._dataset.split_train,
            self._dataset.split_val,
            self._dataset.split_test,
        )
        # 分类任务输出 train/<类别>/ 目录结构，检测/分割输出 images+labels 结构
        layout = "classify" if project.model_type == "classify" else "detect"
        try:
            stats = self._service.split_dataset(
                source, out_dir,
                split=split, stratified=self._dataset.stratified, layout=layout,
            )
            if stats["total"] == 0:
                self.message.emit("warning", "来源目录中未找到图片")
                return

            if layout == "classify":
                # 分类任务：Ultralytics 直接以数据集目录作为 --data，按子目录扫描
                classes = list(self._service.child_class_dirs(source))
                config_path: Path = out_dir
            else:
                classes = self._service.load_class_names(source)
                if not classes:
                    # 回退：优先用项目类别定义，其次用数据集中观察到的类别 id
                    classes = project.class_names or self._dataset.class_names
                config_path = self._service.write_data_yaml(
                    out_dir, classes, "images/train", "images/val", "images/test"
                )

            # 先把划分结果写回模型层，再保存，保证这些字段一并落盘
            self._dataset.output_path = str(out_dir)
            self._dataset.data_yaml = str(config_path)
            self._dataset.class_names = classes
            project.training.data_yaml = str(config_path)
            # 项目文件只保存项目自身的数据（配置 / 标注 / 模型）：
            # 图像与标签留在磁盘数据集目录（硬链接，不额外占盘），
            # 不再把整份图像库复制进 .mprj —— 否则项目体积会翻倍且无必要。
            project.files = [
                record for record in project.files
                if not record.virtual_path.startswith("dataset/")
            ]
            project.touch()
            items = [
                item for item in self._collect_archive_items(out_dir)
                if item[0] == "config"
            ]
            # 顺便生成项目封面（最近项目卡片用），随本次保存一起写进 .mprj
            images = self.images()
            cover = self._service.make_cover(images[0]) if images else None
            if cover:
                items.append(("other", COVER_ENTRY_NAME, cover))
            if items:
                self._project_vm.service.add_files(project, items, save=True)
            else:
                self._project_vm.save_project()
        except (OSError, ValueError) as exc:
            logger.warning("数据集划分失败: %s", exc)
            self.message.emit("error", f"数据集划分失败：{exc}")
            return

        self.datasetChanged.emit(self._dataset)
        self._project_vm.notify_changed()
        self.datasetReady.emit(str(config_path))

        linked = int(stats.get("linked", 0))
        copied = int(stats.get("copied", 0))
        if linked and not copied:
            storage = f"；{linked} 张为链接，不额外占用磁盘"
        elif copied:
            storage = f"；{linked} 张链接 / {copied} 张复制（跨盘或权限不足）"
        else:
            storage = ""
        self.message.emit(
            "success",
            f"划分完成：train {stats['train']} / val {stats['val']} / "
            f"test {stats['test']}{storage}",
        )

    def load_from_project(self, project) -> None:
        """打开 / 新建项目后，从项目数据恢复数据集状态。"""
        self._resolved_source = None
        self._images = None
        self._subsets = None
        self._usable_sources.clear()
        dataset = getattr(project, "dataset", None) if project is not None else None
        if dataset is None or not (dataset.image_count or dataset.source_path):
            self._dataset = None
        else:
            self._dataset = dataset
        self.datasetChanged.emit(self._dataset)

    def resolved_source(self) -> Path | None:
        """返回可用的数据集来源目录。

        优先使用原始来源目录；若该目录已不存在（项目被移动或原始数据被清理），
        则从 `.mprj` 归档把图片释放到项目同级「<项目名>_files/」，保证项目自包含。

        目录可用性会缓存：全目录扫描只做一次，避免被「逐张图片」调用时退化成 O(n²)。
        """
        dataset = self._dataset
        if dataset is None:
            return None
        source = Path(dataset.source_path) if dataset.source_path else None
        if source is not None and source.is_dir():
            if source in self._usable_sources:
                return source
            if DatasetService.scan_images(source):
                self._usable_sources.add(source)
                return source
        return self._restore_from_archive()

    def run_quality_check(self) -> None:
        """后台执行数据质检：重复图片 / 模糊 / 曝光 / 标签校验。"""
        if self.is_busy():
            self.message.emit("warning", "已有任务在运行")
            return
        images = self.images()
        if not images:
            self.message.emit("warning", "请先导入数据集")
            return
        label_index = self.source_label_index()

        def job(progress, is_cancelled):
            progress(5, "查找重复图片")
            duplicates = QualityService.find_duplicates(images)
            if is_cancelled():
                return {}
            progress(40, "检测模糊图片")
            blurry = QualityService.find_blurry(images)
            if is_cancelled():
                return {}
            progress(70, "检测曝光异常")
            exposure = QualityService.find_exposure(images)
            if is_cancelled():
                return {}
            progress(88, "校验标签")
            labels = QualityService.check_labels(images, label_index)
            progress(100, "完成")
            return {
                "total": len(images),
                "duplicates": duplicates,
                "blurry": blurry,
                "exposure": exposure,
                "labels": labels,
            }

        def done(result):
            self.qualityReady.emit(result or {})
            self.message.emit("success", "数据质检完成")

        self._start_worker(job, "数据质检", on_done=done)

    def clear(self) -> None:
        self._dataset = None
        self._resolved_source = None
        self._images = None
        self._subsets = None
        self._usable_sources.clear()
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
        out_dir = mprj.parent / DEFAULT_AUGMENT_NAME

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
        """当前数据集的图片来源列表（源目录缺失时自动从归档恢复）。"""
        source = self.resolved_source()
        return DatasetService.scan_images(source) if source is not None else []

    def source_label_index(self) -> dict:
        """当前数据集的「图片主干 → 标签」索引。"""
        source = self.resolved_source()
        return DatasetService.build_label_index(source) if source is not None else {}

    # -----------------------------------------------------------
    # 图片清单与标注状态（图库 / 标注 / 标注检查页共用）
    # -----------------------------------------------------------
    def images(self, refresh: bool = False) -> list[Path]:
        """当前数据集的图片清单（带缓存）。"""
        if refresh or self._images is None:
            self._images = self.source_images()
        return self._images

    def label_dir(self) -> Path | None:
        """标注标签的落盘目录：<数据集来源>/labels。"""
        source = self.resolved_source()
        return (source / "labels") if source is not None else None

    def label_index(self, refresh: bool = False) -> dict:
        """「图片主干 → 标签文件」索引（带缓存）。"""
        if refresh or self._label_index is None:
            self._label_index = self.source_label_index()
        return self._label_index

    def annotated_flags(self) -> list[bool]:
        """每张图片是否「已标注」。

        - **分类任务**：图片位于某个类别目录下即为已标注（类别本身就是标注结果）
        - **检测 / 分割**：存在且非空的 YOLO 标签文件
        """
        images = self.images()
        if not images:
            return []

        if self.split_layout() == "classify":
            names = set(self._dataset.class_names) if self._dataset else set()
            if not names:
                source = self.resolved_source()
                names = set(
                    DatasetService.child_class_dirs(source) if source else {}
                )
            # 分类任务：图片落在某个类别目录里即为已标注
            # （类别被手工改为「无标签」后即为未标注）
            return [self.image_class(image) in names for image in images]

        label_dir = self.label_dir()
        if label_dir is None:
            return [False] * len(images)
        return [
            DatasetService.label_has_content(
                AnnotationService.label_path_for(image, label_dir)
            )
            for image in images
        ]

    def annotated_count(self) -> int:
        return sum(1 for flag in self.annotated_flags() if flag)

    def image_class(self, image) -> str:
        """图片所属类别名（分类看目录名，检测看标签首个类别 id）。

        返回空串表示「无标签」——手工指定过的图片以项目里的覆盖值为准。
        """
        override = self._params_dict("class_overrides")
        key = self._image_key(image)
        if key in override:
            return str(override[key] or "")
        layout = self.split_layout()
        if layout == "classify":
            return Path(image).parent.name
        return DatasetService.image_class_name(image, self.label_index(), layout)

    def filtered_images(
        self, mode: str = "all", class_name: str = ""
    ) -> list[tuple[Path, bool]]:
        """按 全部 / 已标注 / 未标注 + 类别 过滤，返回 (图片, 是否已标注)。"""
        flags = self.annotated_flags()
        pairs = list(zip(self.images(), flags))
        if class_name:
            pairs = [(p, f) for p, f in pairs if self.image_class(p) == class_name]
        if mode == "annotated":
            return [(path, True) for path, flag in pairs if flag]
        if mode == "unannotated":
            return [(path, False) for path, flag in pairs if not flag]
        return pairs

    def class_distribution(self) -> dict[str, int]:
        """各类别的实际图片数（按当前判定口径统计）。"""
        counts: dict[str, int] = {}
        for image in self.images():
            name = self.image_class(image)
            counts[name] = counts.get(name, 0) + 1
        return counts

    def class_counts(self) -> dict[str, int]:
        """各类别样本数（来自数据集统计）。"""
        return dict(self._dataset.class_counts) if self._dataset is not None else {}

    def image_colors(self) -> list[str]:
        """每张图片对应类别在项目类别表中的颜色（未定义时返回空串）。

        供缩略图右上角的「已标注」角标按类别着色。
        """
        project = self._project_vm.project if self._project_vm else None
        palette = {
            str(cls.name): str(cls.color)
            for cls in (project.classes if project is not None else [])
        }
        return [palette.get(self.image_class(image), "") for image in self.images()]

    def subset_labels(self) -> list[str]:
        """每张图片所属的数据集子集：T=训练 / V=验证 / E=测试 / 空=未划分。

        用当前数据集的划分参数（比例 / 随机种子 / 分层方式）复算一次划分，
        结果与 `split_dataset` 完全一致，因此不依赖划分产物目录是否存在。
        """
        images = self.images()
        if not images or self._dataset is None:
            return [""] * len(images)
        if self._subsets is not None and len(self._subsets) == len(images):
            return self._subsets

        marker = {"train": "T", "val": "V", "test": "E"}
        layout = self.split_layout()
        try:
            buckets = DatasetService.split_members(
                images,
                {} if layout == "classify" else self.label_index(),
                split=(
                    self._dataset.split_train,
                    self._dataset.split_val,
                    self._dataset.split_test,
                ),
                seed=self._dataset.seed,
                stratified=self._dataset.stratified,
                layout=layout,
            )
        except (OSError, ValueError) as exc:
            logger.warning("计算子集归属失败: %s", exc)
            return [""] * len(images)

        owner = {
            str(path): marker.get(name, "")
            for name, items in buckets.items()
            for path in items
        }
        overrides = self._params_dict("split_overrides")
        generated = self._split_generated()
        keys = self._keys(images)
        self._subsets = [
            str(overrides[key] or "") if key in overrides      # 手工映射优先
            else (owner.get(str(image), "") if generated else "")
            for key, image in zip(keys, images)
        ]
        return self._subsets

    def _split_generated(self) -> bool:
        """划分产物是否已生成。

        未执行划分前，除手工映射外一律视为「不在任何一个拆分集里」，
        避免把「预览出来的比例」当成已经生效的归属。
        """
        if self._dataset is None:
            return False
        output = str(self._dataset.output_path or "")
        return bool(output) and Path(output).is_dir()

    def decorations_for(self, paths: list) -> tuple[list[str], list[str]]:
        """给定图片序列 → （每张图的类别颜色, 数据集标记 T/V/E）。

        供缩略图网格的「类别色角标」与「右下角数据集标记」使用。
        """
        position = {str(path): index for index, path in enumerate(self.images())}
        colors = self.image_colors()
        subsets = self.subset_labels()

        def value(series: list, path) -> str:
            index = position.get(str(path))
            if index is None or index >= len(series):
                return ""
            return str(series[index])

        return (
            [value(colors, path) for path in paths],
            [value(subsets, path) for path in paths],
        )

    def gallery_decorations(self, paths: list) -> dict:
        """一次性取出缩略图需要的全部装饰（避免多次 O(n) 复算）。

        Returns:
            {"annotated": [...], "colors": [...], "subsets": [...], "markers": [[...]]}
        """
        images = self.images()
        position = {str(path): index for index, path in enumerate(images)}
        flags = self.annotated_flags()
        colors = self.image_colors()
        subsets = self.subset_labels()
        markers = self.marker_colors()

        def value(series: list, path, default):
            index = position.get(str(path))
            if index is None or index >= len(series):
                return default
            return series[index]

        return {
            "annotated": [bool(value(flags, p, False)) for p in paths],
            "colors": [str(value(colors, p, "")) for p in paths],
            "subsets": [str(value(subsets, p, "")) for p in paths],
            "markers": [list(value(markers, p, [])) for p in paths],
        }

    # -----------------------------------------------------------
    # 图像标记（tag）：类似备注，一张图可挂多个
    # -----------------------------------------------------------
    def image_tag_defs(self) -> list[dict]:
        """项目中已定义的图像标记：[{"name", "color"}, ...]。"""
        project = self._project_vm.project if self._project_vm else None
        raw = project.params.get("image_tags") if project is not None else None
        if not isinstance(raw, list):
            return []
        result: list[dict] = []
        for item in raw:
            if isinstance(item, dict) and str(item.get("name", "")).strip():
                result.append({
                    "name": str(item["name"]).strip(),
                    "color": str(item.get("color", "") or ""),
                })
        return result

    def tag_map(self) -> dict[str, list[str]]:
        """图片标识 → 已挂的标记名列表。"""
        raw = self._params_dict("image_tag_map")
        return {
            str(key): [str(v) for v in value]
            for key, value in raw.items() if isinstance(value, list)
        }

    def all_tag_names(self) -> list[str]:
        """全部标记名（已定义的 + 实际用到的），按名称排序。"""
        names = {item["name"] for item in self.image_tag_defs()}
        for values in self.tag_map().values():
            names.update(values)
        return sorted(names)

    def tag_color(self, name: str) -> str:
        """标记颜色（未定义颜色时返回空串，界面按无色处理）。"""
        for item in self.image_tag_defs():
            if item["name"] == name:
                return item["color"]
        return ""

    def image_tag_names_of(self, image) -> list[str]:
        """单张图片已挂的标记名列表。"""
        return list(self.tag_map().get(self._image_key(image), []))

    def marker_colors(self) -> list[list[str]]:
        """每张图片的标记颜色列表（供缩略图左下角色点）。"""
        mapping = self.tag_map()
        palette = {item["name"]: item["color"] for item in self.image_tag_defs()}
        colors: list[list[str]] = []
        for image in self.images():
            names = mapping.get(self._image_key(image), [])
            colors.append([palette.get(name, "") for name in names][:4])
        return colors

    def add_image_tag(self, name: str, color: str = "") -> bool:
        """新增图像标记定义。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            self.message.emit("warning", "请先创建或打开项目")
            return False
        name = name.strip()
        if not name:
            self.message.emit("warning", "请输入标记文本")
            return False
        defs = self.image_tag_defs()
        if any(item["name"] == name for item in defs):
            self.message.emit("warning", f"标记「{name}」已存在")
            return False
        defs.append({"name": name, "color": color})
        project.params["image_tags"] = defs
        self._commit_meta(f"已新增标记：{name}")
        return True

    def update_image_tag(self, old_name: str, name: str, color: str) -> bool:
        """修改标记的文本与颜色（同步更新已分配它的图片）。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return False
        name = name.strip()
        if not name:
            self.message.emit("warning", "请输入标记文本")
            return False
        defs = self.image_tag_defs()
        if any(item["name"] == name for item in defs if item["name"] != old_name):
            self.message.emit("warning", f"标记「{name}」已存在")
            return False
        for item in defs:
            if item["name"] == old_name:
                item["name"] = name
                item["color"] = color
                break
        project.params["image_tags"] = defs

        if name != old_name:
            mapping = self._params_dict("image_tag_map", create=True)
            for key, values in list(mapping.items()):
                if old_name in values:
                    mapping[key] = [name if v == old_name else v for v in values]
        self._commit_meta(f"标记已更新为「{name}」")
        return True

    def remove_image_tag(self, name: str) -> None:
        """删除标记定义，并从所有图片上摘除。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return
        project.params["image_tags"] = [
            item for item in self.image_tag_defs() if item["name"] != name
        ]
        mapping = self._params_dict("image_tag_map", create=True)
        for key, values in list(mapping.items()):
            left = [v for v in values if v != name]
            if left:
                mapping[key] = left
            else:
                mapping.pop(key, None)
        self._commit_meta(f"已删除标记：{name}")

    def toggle_image_tag(self, images: list, name: str) -> str:
        """给这些图片追加 / 移除标记：全都没有则追加，全都有则移除。

        Returns:
            "added" / "removed" / ""（无有效图片）。
        """
        mapping = self._params_dict("image_tag_map", create=True)
        keys = self._keys([Path(p) for p in images])
        if not keys:
            return ""
        remove = all(name in mapping.get(key, []) for key in keys)
        for key in keys:
            values = [v for v in mapping.get(key, []) if v != name] if remove else (
                list(mapping.get(key, []))
            )
            if not remove and name not in values:
                values.append(name)
            if values:
                mapping[key] = values
            else:
                mapping.pop(key, None)
        self._commit_meta(
            f"已{'移除' if remove else '追加'}标记「{name}」：{len(keys)} 张"
        )
        return "removed" if remove else "added"

    # -----------------------------------------------------------
    # 类别覆盖 / 数据集拆分映射
    # -----------------------------------------------------------
    def set_class_for(self, images: list, class_name: str) -> None:
        """把图片的类别改为指定类别（"" 表示「无标签」）。"""
        overrides = self._params_dict("class_overrides", create=True)
        keys = self._keys([Path(p) for p in images])
        if not keys:
            return
        for key in keys:
            overrides[key] = class_name
        self._commit_meta(
            f"已设为类别「{class_name or '无标签'}」：{len(keys)} 张"
        )

    def set_split_for(self, images: list, split: str) -> None:
        """把图片划入指定子集（train / val / test，"" = 不在任何一个拆分集里）。"""
        overrides = self._params_dict("split_overrides", create=True)
        marker = {"train": "T", "val": "V", "test": "E"}
        keys = self._keys([Path(p) for p in images])
        if not keys:
            return
        for key in keys:
            overrides[key] = marker.get(split, "")
        self._commit_meta(f"已划入「{SPLIT_LABELS.get(split, '未划分')}」：{len(keys)} 张")

    def split_summary(self) -> dict[str, int]:
        """各子集的图片数，键为 T / V / E / ""（未划分）。"""
        counts = {"T": 0, "V": 0, "E": 0, "": 0}
        for label in self.subset_labels():
            counts[label if label in counts else ""] += 1
        return counts

    def split_name_label(self) -> str:
        """当前使用的拆分名称（拆分映射下拉框显示用）。"""
        project = self._project_vm.project if self._project_vm else None
        return str(
            (project.params.get("split_name") if project is not None else "") or ""
        ).strip() or DEFAULT_SPLIT_NAME

    def split_locked(self) -> bool:
        """该拆分是否已被训练使用（使用后不允许再改）。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return False
        status = str(getattr(project.training, "status", "") or "")
        return bool(getattr(project.training, "data_yaml", "")) and status in (
            "running", "finished", "done", "success",
        )

    # -----------------------------------------------------------
    # 组合筛选：标签状态 / 图像标记 / 数据集划分 / 文本
    # -----------------------------------------------------------
    def filter_images(
        self,
        label: str = "all",
        mark: str = "all",
        split: str = "all",
        text: str = "",
    ) -> list[Path]:
        """按 标签状态 / 图像标记 / 数据集划分 / 文本 过滤图片。

        Args:
            label: all / annotated / unannotated。
            mark: all / tagged / untagged / 具体标记名。
            split: all / train / val / test / none（未划分）。
            text: 关键词，匹配文件名、类别名与标记名（不区分大小写）。
        """
        images = self.images()
        if not images:
            return []
        flags = self.annotated_flags()
        subsets = self.subset_labels()
        classes = [self.image_class(image) for image in images]
        tags = [self.image_tag_names_of(image) for image in images]
        keyword = text.strip().lower()

        result: list[Path] = []
        for index, image in enumerate(images):
            annotated = flags[index] if index < len(flags) else False
            subset = subsets[index] if index < len(subsets) else ""
            spans = tags[index] if index < len(tags) else []
            if label == "annotated" and not annotated:
                continue
            if label == "unannotated" and annotated:
                continue
            if mark == "tagged" and not spans:
                continue
            if mark == "untagged" and spans:
                continue
            if mark not in ("all", "tagged", "untagged") and mark not in spans:
                continue
            if split == "none" and subset:
                continue
            if split in ("train", "val", "test") and _SUBSET_NAME.get(subset) != split:
                continue
            if keyword and not (
                keyword in image.name.lower()
                or keyword in classes[index].lower()
                or any(keyword in span.lower() for span in spans)
            ):
                continue
            result.append(image)
        return result

    # -----------------------------------------------------------
    # 图片文件操作（右键菜单）
    # -----------------------------------------------------------
    def save_image_as(self, path, target: str) -> bool:
        """把图片另存为指定路径。"""
        try:
            shutil.copy2(str(path), target)
            return True
        except OSError as exc:
            logger.warning("另存图片失败: %s", exc)
            self.message.emit("error", f"另存图片失败：{exc}")
            return False

    def remove_images(self, paths: list) -> int:
        """从数据集与项目归档中移除图片，并清掉它们的标记 / 覆盖记录。

        Returns:
            实际删除的文件数。
        """
        project = self._project_vm.project if self._project_vm else None
        if project is None or not paths:
            return 0
        targets = [Path(p) for p in paths]
        names = {path.name for path in targets}
        keys = set(self._keys(targets))

        removed = 0
        for target in targets:
            try:
                if target.is_file():
                    target.unlink()
                    removed += 1
            except OSError as exc:
                logger.warning("删除图片失败 %s: %s", target, exc)

        # 归档条目按文件名匹配（划分产物中的文件名与来源保持一致）
        for record in [
            record for record in list(project.files)
            if record.kind == "image" and Path(record.virtual_path).name in names
        ]:
            project.files.remove(record)

        for key in keys:
            self._params_dict("class_overrides").pop(key, None)
            self._params_dict("split_overrides").pop(key, None)
            self._params_dict("image_tag_map").pop(key, None)

        project.touch()
        try:
            self._project_vm.service.save(project)
        except (OSError, ValueError) as exc:
            logger.warning("保存项目失败: %s", exc)
        self.invalidate_images()
        self.datasetChanged.emit(self._dataset)
        self._project_vm.notify_changed()
        self.message.emit("success", f"已移除 {removed} 张图片")
        return removed

    # -----------------------------------------------------------
    # 内部：图片标识与项目元信息
    # -----------------------------------------------------------
    def _image_key(self, image) -> str:
        """图片在项目内的稳定标识（相对数据集来源目录的路径）。"""
        source = self.resolved_source()
        path = Path(image)
        if source is not None:
            try:
                return path.relative_to(source).as_posix()
            except ValueError:
                pass
        return path.name

    def _keys(self, images: list) -> list[str]:
        """批量计算图片标识（复用同一次来源目录解析）。"""
        source = self.resolved_source()
        keys: list[str] = []
        for image in images:
            path = Path(image)
            if source is not None:
                try:
                    keys.append(path.relative_to(source).as_posix())
                    continue
                except ValueError:
                    pass
            keys.append(path.name)
        return keys

    def _params_dict(self, key: str, create: bool = False) -> dict:
        """读取项目 params 中的字典型元信息。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return {}
        value = project.params.get(key)
        if not isinstance(value, dict):
            if not create:
                return {}
            value = {}
            project.params[key] = value
        return value

    def _commit_meta(self, text: str) -> None:
        """把项目元信息（标记 / 拆分 / 类别覆盖）落盘并通知界面。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return
        project.touch()
        try:
            self._project_vm.service.save(project)
        except (OSError, ValueError) as exc:
            logger.warning("保存项目元信息失败: %s", exc)
            self.message.emit("error", f"保存失败：{exc}")
            return
        self.invalidate_images()
        self.datasetChanged.emit(self._dataset)
        self._project_vm.notify_changed()
        self.message.emit("success", text)

    def invalidate_images(self) -> None:
        """图片发生变化（新增标注 / 重新划分）后清空清单缓存。"""
        self._images = None
        self._label_index = None
        self._subsets = None

    # -----------------------------------------------------------
    # 拆分名称（决定划分产物的目录名）
    # -----------------------------------------------------------
    def split_name(self) -> str:
        """划分产物的目录名（默认 `dataset`，可在数据拆分页改名）。"""
        project = self._project_vm.project if self._project_vm else None
        stored = str((project.params.get("split_name") if project else "") or "").strip()
        return stored or DEFAULT_SPLIT_NAME

    def set_split_name(self, name: str) -> None:
        """设置拆分名称（用于划分产物的目录名）。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return
        project.params["split_name"] = name.strip()
        project.touch()

    def notify(self, text: str, level: str = "info") -> None:
        """供界面反馈本地操作结果的提示通道。"""
        self.message.emit(level, text)

    def split_layout(self) -> str:
        """按项目类型返回数据集结构：分类用目录结构，其余用 YOLO 检测结构。"""
        project = self._project_vm.project if self._project_vm else None
        if project is not None and project.model_type == "classify":
            return "classify"
        return "detect"

    def preview_split(self) -> dict:
        """不落盘地计算划分结果（供拆分页预览饼图与类别分布）。"""
        source = self.resolved_source()
        if source is None or self._dataset is None:
            return {}
        try:
            return DatasetService.preview_split(
                source,
                split=(
                    self._dataset.split_train,
                    self._dataset.split_val,
                    self._dataset.split_test,
                ),
                seed=self._dataset.seed,
                stratified=self._dataset.stratified,
                layout=self.split_layout(),
            )
        except (OSError, ValueError) as exc:
            logger.warning("划分预览失败: %s", exc)
            return {}

    # -----------------------------------------------------------
    # 内部
    # -----------------------------------------------------------
    def _set_dataset(self, dataset: Dataset) -> None:
        self._dataset = dataset
        self._resolved_source = None
        self._images = None
        self._subsets = None
        self._usable_sources.clear()
        # 项目直接持有同一份 Dataset 对象，并即时保存，避免忘记保存导致数据丢失
        project = self._project_vm.project if self._project_vm else None
        if project is not None:
            project.dataset = dataset
            # 项目还没有类别定义时，按数据集中的类别自动生成
            # （分类数据集没有标签文件，类别来自子目录名，否则类别页会一直是空的）
            if not project.classes and dataset.class_names:
                project.classes[:] = CategoryService.sync_from_dataset(dataset)
            project.touch()
            # 顺便生成项目封面（最近项目卡片用），随本次保存写进 .mprj
            images = self.images()
            cover = self._service.make_cover(images[0]) if images else None
            try:
                if cover:
                    self._project_vm.service.add_files(
                        project, [("other", COVER_ENTRY_NAME, cover)], save=True
                    )
                else:
                    self._project_vm.save_project()
            except (OSError, ValueError) as exc:
                logger.warning("保存数据集统计失败: %s", exc)
            self._project_vm.notify_changed()
        self.datasetImported.emit(dataset)
        self.datasetChanged.emit(dataset)
        self.message.emit(
            "success",
            f"导入完成：{dataset.image_count} 张图片，{dataset.class_count} 个类别"
            + (f"，剔重 {dataset.duplicate_count} 张" if dataset.duplicate_count else ""),
        )

    def _restore_from_archive(self) -> Path | None:
        """源目录不可用时，把归档中的数据集图片释放到磁盘并返回其目录。"""
        if self._resolved_source is not None and self._resolved_source.is_dir():
            return self._resolved_source
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return None
        paths = [
            record.virtual_path for record in project.files
            if record.kind == "image" and record.virtual_path.startswith("dataset/")
        ]
        if not paths:
            return None
        mprj = Path(project.params.get("path", ""))
        if not mprj.name:
            return None

        dest = mprj.parent / f"{mprj.stem}_files"
        try:
            payloads = self._project_vm.service.read_files(project, paths)
        except (KeyError, ValueError, OSError) as exc:
            logger.warning("从归档释放数据集失败: %s", exc)
            return None

        for virtual_path, data in payloads.items():
            target = dest / virtual_path
            if target.is_file():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

        source = dest / "dataset"
        if not source.is_dir():
            return None
        self._resolved_source = source
        logger.info("已从项目归档释放数据集到 %s", source)
        return source

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
