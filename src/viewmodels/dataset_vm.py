"""数据集 ViewModel：导入预览、统计、划分与归档。"""

from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from src.models.dataset import Dataset
from src.models.filter_rules import UNLABELED_NAME, FilterRules
from src.models.split import Split
from src.services.annotation_service import AnnotationService
from src.services.category_service import CategoryService
from src.services.quality_service import QualityService
from src.services.dataset_service import DatasetService
from src.services.project_service import ProjectService
from src.utils.constants import (
    COVER_ENTRY_NAME,
    DEFAULT_SPLIT_NAME,
    IMAGE_EXTS,
    LABEL_EXTS,
    SPLIT_LABELS,
    SPLIT_SUBDIRS,
    UNLABELED_LABEL,
)
from src.utils.logger import get_logger
from src.utils.tasks import dataset_layout
from src.utils.workers import FunctionWorker
from src.viewmodels.project_vm import ProjectViewModel

logger = get_logger("dataset_vm")

# 子集标记 → 划分名（与 thumbnail_grid 的 T / V / E 对应）
_SUBSET_NAME = {"T": "train", "V": "val", "E": "test"}


def _filter_list(value) -> list[str] | None:
    """把筛选条件统一成列表；"all" / 空表示不筛选（返回 None）。

    支持单个字符串（兼容旧调用）与多选列表（筛选栏的下拉多选）。
    """
    if value is None or value == "all":
        return None
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [str(item) for item in value]
        return items or None
    return [str(value)]


class DatasetViewModel(QObject):
    """数据管理页的业务逻辑。

    划分结果落盘到项目同级的 `dataset/` 目录（可在数据拆分页改名），
    图片以硬链接方式组织，不额外占用磁盘；项目文件只保存配置、标注与模型。
    """

    datasetChanged = Signal(object)      # Dataset
    datasetImported = Signal(object)     # Dataset
    splitChanged = Signal(tuple)         # (train, val, test) 比例
    splitsChanged = Signal(list)         # 项目内的拆分列表（多套拆分）
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
        # 来源目录列表缓存（避免逐张图片调用时反复解析路径）
        self._roots_cache: list[Path] | None = None
        # 已有图片的内容哈希缓存（导入去重时避免重复读取大图）
        self._hash_cache: dict[str, str] | None = None
        # 图片清单与标签索引缓存（数据量大，避免每次刷新都重新扫盘）
        self._images: list[Path] | None = None
        self._label_index: dict | None = None
        # 图片所属子集标记缓存（T/V/E）
        self._subsets: list[str] | None = None
        # 图像元信息（宽 / 高 / 通道数）与标签信息（数量 / 类别）缓存
        # —— 仅供自定义筛选规则与标签统计按需读取
        self._meta_cache: dict[str, dict] = {}
        self._label_info_cache: dict[str, dict] = {}

    @property
    def dataset(self) -> Dataset | None:
        return self._dataset

    # -----------------------------------------------------------
    # 命令
    # -----------------------------------------------------------
    def import_images(
        self, directory: str, options: dict | None = None
    ) -> Dataset | None:
        """导入图片文件夹并生成统计，返回累加后的数据集。

        Args:
            directory: 图片文件夹（递归扫描）。
            options: 导入选项（见 `_import`），为空时按默认值导入。
                带 `files` 时只导入其中的图片（子文件夹勾选后的子集）。
        """
        directory = Path(directory)
        if not directory.is_dir():
            self.message.emit("warning", "请选择有效的图片文件夹")
            return None
        options = dict(options or {})
        subset = options.get("files")
        if subset:
            images = [
                Path(p) for p in subset
                if Path(p).is_file() and Path(p).suffix.lower() in IMAGE_EXTS
            ]
        else:
            images = self._service.scan_images(directory)
        if not images:
            self.message.emit("warning", f"目录中未找到图片：{directory}")
            return None
        return self._import(images, options, roots=[directory])

    def import_files(
        self, paths: list, options: dict | None = None
    ) -> Dataset | None:
        """按选中的文件列表导入并生成统计。

        所选文件所在目录会登记为来源目录（与「导入文件夹」保持一致），
        因此后续排查 / 重扫的目录范围是可预期的。
        """
        images = [
            Path(p) for p in (paths or [])
            if Path(p).is_file() and Path(p).suffix.lower() in IMAGE_EXTS
        ]
        if not images:
            self.message.emit("warning", "请选择图片文件")
            return None
        roots: list[Path] = []
        for image in images:
            if image.parent not in roots:
                roots.append(image.parent)
        return self._import(images, options, roots=roots)

    def _import(
        self, images: list, options: dict | None, roots: list
    ) -> Dataset | None:
        """导入的公共实现：去重 → 初步标注 → 合并顺序 → 重建统计。

        options 支持的键：
            label_map   dict：{图片所在文件夹: 类别名}，用于按子文件夹自动标注
            class_kinds dict：{类别名: "normal" / "abnormal"}，异常检测的类别类型
            label       str：兜底标注（文件夹没填类别时用它；"" = 不标注）
            files       list：只导入这些图片（来源文件夹下的子集）
            dedupe      bool：是否跳过与图库中已有图片内容重复的图片
            annotate    bool：是否在导入时按上面的映射标注（False = 只导入图片）
        """
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            self.message.emit("warning", "请先创建或打开项目")
            return None
        options = dict(options or {})

        # 1) 导入顺序：按所选范围的自然顺序（读取顺序不作为导入选项）
        ordered = [Path(path) for path in images]

        # 2) 来源目录：已有目录 + 本次新增目录
        merged_roots: list[Path] = []
        for raw in list(self._dataset.sources if self._dataset else []) + [
            str(root) for root in roots
        ]:
            path = Path(raw)
            if not path.is_dir():
                continue
            if any(str(path) == str(item) for item in merged_roots):
                continue
            merged_roots.append(path)
        if not merged_roots:
            merged_roots = [Path(root) for root in roots]

        # 3) 去重：批内重复 + 与图库已有图片内容重复（SHA256）
        dedupe = bool(options.get("dedupe", True))
        known = self._known_hashes() if dedupe else {}
        accepted, skipped, hashes = self._filter_duplicates(ordered, dedupe, known)
        if not accepted:
            self.message.emit(
                "warning",
                f"没有可导入的新图片（{skipped} 张与图库内容重复）" if skipped
                else "没有可导入的图片",
            )
            return None

        # 4) 自动标注：图片所在文件夹 → 类别（label_map），没填类别即「无标签」；
        #    annotate=False 时只导入图片，不写类别
        annotate = bool(options.get("annotate", True))
        label_map = {
            str(folder): str(name)
            for folder, name in (options.get("label_map") or {}).items()
        } if annotate else {}
        fallback = str(options.get("label", "") or "").strip() if annotate else ""
        kinds = {
            str(name): str(kind)
            for name, kind in (options.get("class_kinds") or {}).items()
            if str(kind) in ("normal", "abnormal")
        } if annotate else {}
        applied: dict[str, int] = {}
        if label_map or fallback:
            overrides = self._params_dict("class_overrides", create=True)
            for image in accepted:
                folder = str(image.parent)
                if folder in label_map:
                    name = label_map[folder].strip()
                elif fallback:
                    name = fallback
                else:
                    continue
                # 允许写空串：显式表示「无标签」（区分于「没有指定过」）
                overrides[self._relative_key(image, merged_roots)] = name
                if name:
                    applied[name] = applied.get(name, 0) + 1
            # 类别体系里没有的先建类，保证「已标注」判定与类别列表自洽；
            # 颜色由 CategoryService 按名称语义 / 类别类型初始化（已有类不改）
            for name in applied:
                if not any(str(cls.name) == name for cls in project.classes):
                    CategoryService.add(
                        project.classes, name, kind=kinds.get(name, "")
                    )
        # 异常检测的类别类型（良好 / 异常）：良好与异常下都可以有多个类别
        for name, kind in kinds.items():
            target = next(
                (cls for cls in project.classes if str(cls.name) == name), None
            )
            if target is None:
                target = CategoryService.add(project.classes, name, kind=kind)
            CategoryService.set_kind(project.classes, target.cls_id, kind)

        # 5) 合并顺序：新图片追加到图库末尾
        order = self._order_list() or [str(path) for path in self.images()]
        new_paths = [str(path) for path in accepted]
        merged_order = order + new_paths
        project.params["image_order"] = list(dict.fromkeys(merged_order))

        # 重新导入的图片视为「回到数据集」，从已移除清单里摘掉
        excludes = self._exclude_list()
        if excludes:
            revived = set(new_paths)
            self._set_excludes(
                [item for item in excludes if item not in revived]
            )

        # 6) 重建统计（图片清单 / 标签索引 / 类别分布；仍排除已移除的图片）
        excluded = self._excluded_paths()
        library = [
            path
            for path in self._merge_order(
                merged_roots, project.params["image_order"]
            )
            if str(path) not in excluded
        ]
        label_index = self._service.label_index_for(library, merged_roots)
        previous = self._dataset
        dataset = self._service.summarize_library(
            merged_roots,
            images=library,
            label_index=label_index,
            name=previous.name if previous is not None else "",
            duplicate_count=(previous.duplicate_count if previous is not None else 0)
            + skipped,
        )
        self._inherit_settings(dataset, previous)

        notice = f"导入 {len(accepted)} 张"
        if skipped:
            notice += f"，跳过内容重复 {skipped} 张"
        if applied:
            brief = "、".join(
                f"{name} {count} 张"
                for name, count in sorted(applied.items())[:4]
            )
            if len(applied) > 4:
                brief += f" 等 {len(applied)} 类"
            notice += f"，已按类别标注：{brief}"
        elif annotate is False:
            notice += "，未标注"
        self._set_dataset(dataset, notice=notice)
        if dedupe:
            self._hash_cache = {**known, **hashes}
        logger.info(
            "导入数据集 %s: 新增 %s 张，跳过重复 %s 张，合计 %s 张",
            dataset.name, len(accepted), skipped, dataset.image_count,
        )
        return dataset

    def _filter_duplicates(
        self, images: list, dedupe: bool, known: dict
    ) -> tuple[list[Path], int, dict]:
        """按内容哈希过滤重复图片。

        Returns:
            (可导入的图片, 跳过的张数, 新图片的 {路径: 哈希})。
        """
        if not dedupe:
            return [Path(p) for p in images], 0, {}

        seen = set(known.values())
        accepted: list[Path] = []
        hashes: dict[str, str] = {}
        skipped = 0
        for image in images:
            path = Path(image)
            try:
                digest = self._service.file_sha256(path)
            except OSError as exc:
                logger.warning("读取失败 %s: %s", path, exc)
                continue
            if digest in seen:
                skipped += 1
                continue
            seen.add(digest)
            accepted.append(path)
            hashes[str(path)] = digest
        return accepted, skipped, hashes

    def _known_hashes(self) -> dict[str, str]:
        """已有图片的 {路径: 内容哈希}（带缓存，导入去重用）。"""
        if self._hash_cache is None:
            cached: dict[str, str] = {}
            for image in self.images():
                try:
                    cached[str(image)] = self._service.file_sha256(image)
                except OSError:
                    continue
            self._hash_cache = cached
        return self._hash_cache

    @staticmethod
    def _inherit_settings(dataset: Dataset, previous: Dataset | None) -> None:
        """把上一份数据集的划分配置与产物信息带到新统计上（导入不重置划分设置）。"""
        if previous is None:
            return
        dataset.name = previous.name or dataset.name
        dataset.split_train = previous.split_train
        dataset.split_val = previous.split_val
        dataset.split_test = previous.split_test
        dataset.stratified = previous.stratified
        dataset.seed = previous.seed
        dataset.output_path = previous.output_path
        dataset.data_yaml = previous.data_yaml
        dataset.created_at = previous.created_at

    # -----------------------------------------------------------
    # 图库顺序（多来源目录按显式顺序合并）
    # -----------------------------------------------------------
    def _order_list(self) -> list[str]:
        """显式图片顺序清单（多文件夹 / 多选导入后用于稳定排序）。

        注意：这里直接读取 `project.params`，因为该值是**列表**而不是字典。
        """
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return []
        raw = project.params.get("image_order")
        if not isinstance(raw, (list, tuple)):
            return []
        return [str(item) for item in raw if str(item).strip()]

    def _exclude_list(self) -> list[str]:
        """已从数据集移除的图片清单（本地文件保留，只是不再读进程序）。

        注意：这里直接读取 `project.params`，因为该值是**列表**而不是字典。
        """
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return []
        raw = project.params.get("image_excludes")
        if not isinstance(raw, (list, tuple)):
            return []
        return [str(item) for item in raw if str(item).strip()]

    def _excluded_paths(self) -> set[str]:
        """移除清单的规范化路径集合（用于过滤图库）。"""
        return {str(Path(item)) for item in self._exclude_list()}

    def _set_excludes(self, paths) -> None:
        """写入移除清单（保持原有顺序，自动去重）。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return
        project.params["image_excludes"] = list(dict.fromkeys(str(p) for p in paths))

    @staticmethod
    def _merge_order(roots: list, order: list) -> list[Path]:
        """来源目录扫描结果 + 显式顺序清单 → 最终图片序列。

        顺序清单存在时**以清单为准**：图库内容 = 历次导入的图片，
        清单顺序即图库顺序（支持左侧 / 右侧插入与多文件夹累加）。
        清单不存在时（旧项目）退回按来源目录递归扫描。
        """
        scanned = DatasetService.scan_images_multi(roots)
        if not order:
            return scanned
        index = {str(path): path for path in scanned}
        result: list[Path] = []
        for raw in order:
            path = Path(raw)
            key = str(path)
            if key in index:
                result.append(index[key])
            elif path.is_file():
                result.append(path)
        return result

    @staticmethod
    def _relative_key(image, roots: list) -> str:
        """图片在项目内的稳定标识（相对来源目录的路径；目录外则用绝对路径）。"""
        path = Path(image)
        for root in roots:
            try:
                return path.relative_to(root).as_posix()
            except ValueError:
                continue
        return path.as_posix()

    # -----------------------------------------------------------
    # 数据拆分（一个项目可有多套拆分）
    # -----------------------------------------------------------
    def splits(self) -> list:
        """项目内的全部拆分（旧项目会自动迁移出一套）。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return []
        project.ensure_splits(self.split_layout())
        return list(project.splits)

    def splits_ready(self) -> list[dict]:
        """拆分列表的展示数据（下拉 / 列表通用）。"""
        return [
            {
                "id": split.split_id,
                "name": split.name,
                "label": f"{split.name} · {split.ratio_text}"
                         + (f" · {split.total()} 张" if split.ready else ""),
                "ready": split.ready,
                "locked": split.locked,
                "counts": {key: int(value) for key, value in split.counts.items()},
                "total": split.total(),
            }
            for split in self.splits()
        ]

    def active_split(self):
        """当前选中的拆分。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return None
        project.ensure_splits(self.split_layout())
        return project.active_split()

    def split_by_id(self, split_id: int):
        project = self._project_vm.project if self._project_vm else None
        return project.split_by_id(split_id) if project is not None else None

    def select_split(self, split_id: int) -> None:
        """切换当前拆分：镜像到 Dataset，并让训练配置跟随。"""
        project = self._project_vm.project if self._project_vm else None
        split = project.split_by_id(split_id) if project is not None else None
        if project is None or split is None:
            return
        project.active_split_id = split.split_id
        project.params["split_name"] = split.name
        project.training.split_id = split.split_id
        project.training.split_name = split.name
        if split.data_yaml:
            project.training.data_yaml = split.data_yaml
        self._sync_anomaly_root(split)
        project.touch()
        self._mirror_split(split)
        self._emit_splits()
        self.splitChanged.emit((split.train, split.val, split.test))

    def add_split(self, name: str = "", base_split_id: int | None = None) -> int:
        """新建一套拆分（默认沿用当前拆分的比例与随机种子）。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            self.message.emit("warning", "请先创建或打开项目")
            return -1
        project.ensure_splits(self.split_layout())
        base = (
            project.split_by_id(base_split_id) if base_split_id is not None
            else project.active_split()
        )
        split_id = project.next_split_id()
        title = (name or "").strip() or f"拆分{split_id + 1}"
        if project.split_by_name(title) is not None:
            title = f"{title} ({split_id + 1})"
        project.splits.append(Split(
            split_id=split_id,
            name=title,
            train=base.train if base else 0.7,
            val=base.val if base else 0.2,
            test=base.test if base else 0.1,
            seed=base.seed if base else 0,
            stratified=base.stratified if base else True,
            layout=self.split_layout(),
        ))
        project.active_split_id = split_id
        project.params["split_name"] = title
        project.touch()
        self._mirror_split(project.active_split())
        self._emit_splits()
        self.message.emit("success", f"已新建拆分「{title}」")
        return split_id

    def duplicate_split(self, split_id: int) -> int:
        """复制一套拆分（沿用其比例与随机种子，产物需重新生成）。"""
        project = self._project_vm.project if self._project_vm else None
        source = project.split_by_id(split_id) if project is not None else None
        if project is None or source is None:
            return -1
        return self.add_split(f"{source.name} 副本", base_split_id=split_id)

    def rename_split(self, split_id: int, name: str) -> str:
        """重命名拆分（名称同时用作划分产物的目录名）。"""
        project = self._project_vm.project if self._project_vm else None
        split = project.split_by_id(split_id) if project is not None else None
        if project is None or split is None:
            return ""
        title = (name or "").strip() or split.name
        other = project.split_by_name(title)
        if other is not None and other.split_id != split.split_id:
            self.message.emit("warning", f"拆分名「{title}」已存在")
            return split.name
        split.name = title
        if project.active_split() is split:
            project.params["split_name"] = title
            project.training.split_name = title
        project.touch()
        self._emit_splits()
        return title

    def remove_split(self, split_id: int) -> bool:
        """删除一套拆分（磁盘上的产物目录保留）。"""
        project = self._project_vm.project if self._project_vm else None
        split = project.split_by_id(split_id) if project is not None else None
        if project is None or split is None:
            return False
        if len(project.splits) <= 1:
            self.message.emit("warning", "至少保留一套拆分")
            return False
        project.splits = [
            item for item in project.splits if item.split_id != split.split_id
        ]
        if project.active_split_id == split.split_id:
            project.active_split_id = project.splits[0].split_id
            # 当前拆分换人后同步兼容副本，避免界面残留已删除的拆分名
            project.params["split_name"] = project.splits[0].name
        project.touch()
        self._mirror_split(project.active_split())
        self._emit_splits()
        self.message.emit("success", f"已删除拆分「{split.name}」")
        return True

    def mark_active_split_used(self) -> None:
        """把当前拆分标记为「已被训练使用」（比例不再改动）。"""
        split = self.active_split()
        project = self._project_vm.project if self._project_vm else None
        if split is None or project is None:
            return
        split.locked = True
        project.touch()
        self._emit_splits()

    def _emit_splits(self) -> None:
        self.datasetChanged.emit(self._dataset)
        self.splitsChanged.emit(self.splits_ready())

    def _mirror_split(self, split) -> None:
        """把拆分参数镜像到 Dataset（兼容既有界面 / 统计 / 预览）。"""
        if split is None or self._dataset is None:
            return
        dataset = self._dataset
        dataset.split_train = float(split.train)
        dataset.split_val = float(split.val)
        dataset.split_test = float(split.test)
        dataset.stratified = bool(split.stratified)
        dataset.seed = int(split.seed)
        dataset.output_path = split.output_dir
        dataset.data_yaml = split.data_yaml
        if split.classes:
            dataset.class_names = list(split.classes)
        self._subsets = None

    def set_split(
        self, train: float, val: float, test: float, quiet: bool = False
    ) -> None:
        """更新当前拆分的划分比例（应满足 train+val+test ≈ 1）。

        Args:
            quiet: 为 True 时不弹出提示（滑动条 / 数字框连续调整时使用）。
        """
        split = self.active_split()
        if split is not None:
            split.train = float(train)
            split.val = float(val)
            split.test = float(test)
            project = self._project_vm.project if self._project_vm else None
            if project is not None:
                project.touch()
            self._mirror_split(split)
        elif self._dataset is not None:
            self._dataset.split_train = train
            self._dataset.split_val = val
            self._dataset.split_test = test
            self._subsets = None
        self.splitChanged.emit((train, val, test))
        if not quiet:
            self.message.emit(
                "info", f"划分比例已更新：{train:.0%}/{val:.0%}/{test:.0%}"
            )

    def set_seed(self, seed: int, quiet: bool = True) -> None:
        """设置当前拆分的随机种子（保证划分可复现）。"""
        split = self.active_split()
        if split is not None:
            split.seed = int(seed)
            self._mirror_split(split)
            project = self._project_vm.project if self._project_vm else None
            if project is not None:
                project.touch()
        elif self._dataset is not None:
            self._dataset.seed = int(seed)
        if not quiet:
            self.message.emit("info", f"随机种子已设为 {int(seed)}")

    def set_stratified(self, enabled: bool) -> None:
        """切换当前拆分是否按类别分层抽样。"""
        enabled = bool(enabled)
        split = self.active_split()
        if split is not None:
            split.stratified = enabled
            self._mirror_split(split)
            project = self._project_vm.project if self._project_vm else None
            if project is not None:
                project.touch()
        elif self._dataset is not None:
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
        # 产物写进项目文件夹（与 Halcon DLT 一致：删除项目时一并清理）
        out_dir = ProjectService.project_dir(mprj) / self.split_name()
        split = (
            self._dataset.split_train,
            self._dataset.split_val,
            self._dataset.split_test,
        )
        # 分类任务输出 train/<类别>/ 目录结构，检测/分割输出 images+labels 结构
        layout = self.split_layout()
        # 图库可能由多个文件夹累加而成，划分按界面上看到的图片清单进行
        library = self.images()
        label_index = self.label_index()
        # 异常检测：按「良好 / 异常」分配，训练集只放良好图
        kinds, counts = self._anomaly_inputs()

        # 预检查：验证集为空时后端会直接失败（val=None），这里提前拦住
        try:
            buckets = DatasetService.split_members(
                library,
                {} if layout == "classify" else label_index,
                split=split,
                seed=self._dataset.seed,
                stratified=self._dataset.stratified,
                layout=layout,
                kinds=kinds,
                counts=counts,
            )
        except (OSError, ValueError):
            buckets = {}
        if buckets and (not buckets["train"] or not buckets["val"]):
            self.message.emit(
                "warning",
                "训练集或验证集为空，请调整分配数量或增加图片"
                if kinds else "验证集为空，请调整拆分比例或增加图片",
            )
            return
        try:
            stats = self._service.split_dataset(
                source, out_dir,
                split=split, stratified=self._dataset.stratified, layout=layout,
                images=library, label_index=label_index,
                kinds=kinds, counts=counts,
            )
            if stats["total"] == 0:
                self.message.emit("warning", "来源目录中未找到图片")
                return

            if layout == "classify":
                # 分类任务：Ultralytics 直接以数据集目录作为 --data，按子目录扫描
                classes = list(self._service.child_class_dirs_multi(self._source_roots()))
                config_path: Path = out_dir
            elif layout == "anomaly_folder":
                # 异常检测没有 YAML：产物目录本身即数据集
                # （normal / normal_test / abnormal，Anomalib Folder 约定）
                classes = []
                config_path = out_dir
            else:
                classes = self._service.load_class_names(source)
                if not classes:
                    # 回退：优先用项目类别定义，其次用数据集中观察到的类别 id
                    classes = project.class_names or self._dataset.class_names
                config_path = self._service.write_data_yaml(
                    out_dir, classes, "images/train", "images/val", "images/test"
                )

            # 划分结果写进当前拆分（一个项目可有多套），并镜像到 Dataset
            split = self.active_split()
            if split is not None:
                split.layout = layout
                split.mark_generated(out_dir, config_path, stats, classes)
                # 异常检测：训练数据目录指向本拆分产物，训练 / 评估直接用这一套
                self._sync_anomaly_root(split)
            self._dataset.output_path = str(out_dir)
            self._dataset.data_yaml = str(config_path)
            self._dataset.class_names = classes
            project.training.data_yaml = str(config_path)
            project.training.split_id = split.split_id if split else 0
            project.training.split_name = split.name if split else ""
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

        self._subsets = None
        self.datasetChanged.emit(self._dataset)
        self._project_vm.notify_changed()
        self.splitsChanged.emit(self.splits_ready())
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
        self._roots_cache = None
        self._hash_cache = None
        self._images = None
        self._subsets = None
        dataset = getattr(project, "dataset", None) if project is not None else None
        if dataset is None or not (dataset.image_count or dataset.source_path):
            self._dataset = None
        else:
            self._dataset = dataset
        if project is not None:
            # 旧项目没有拆分列表：这里补一套出来，并让界面按当前拆分显示
            split = project.ensure_splits(self.split_layout())
            # 兼容副本「当前拆分名」跟随模型：副本过期时，凡按下沉字段显示的
            # 位置都会残留上一次的拆分名（如删除当前拆分后未同步）
            if split is not None:
                project.params["split_name"] = split.name
            self._mirror_split(split)
            self.splitsChanged.emit(self.splits_ready())
        self.datasetChanged.emit(self._dataset)

    def _source_roots(self) -> list[Path]:
        """当前数据集的全部来源目录（图库可由多个文件夹累加而成，带缓存）。

        目录解析会缓存：避免被「逐张图片」调用时退化成 O(n²)。
        全部来源都不可用时，回退到从项目归档释放出来的工作目录。
        """
        if self._roots_cache is not None:
            return self._roots_cache
        roots: list[Path] = []
        seen: set[str] = set()
        dataset = self._dataset
        if dataset is not None:
            for raw in dataset.sources:
                path = Path(raw)
                key = str(path).lower()
                if key in seen or not path.is_dir():
                    continue
                seen.add(key)
                roots.append(path)
        if not roots:
            restored = self._restore_from_archive()
            if restored is not None:
                roots = [restored]
        self._roots_cache = roots
        return roots

    def resolved_source(self) -> Path | None:
        """返回可用的主来源目录（标注标签的落盘基准）。

        优先使用原始来源目录；若该目录已不存在（项目被移动或原始数据被清理），
        则从 `.mprj` 归档把图片释放到项目同级「<项目名>_files/」，保证项目自包含。
        """
        roots = self._source_roots()
        if roots:
            self._resolved_source = roots[0]
            return roots[0]
        return None

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
        self._roots_cache = None
        self._hash_cache = None
        self._images = None
        self._label_index = None
        self._subsets = None
        self.datasetChanged.emit(None)

    # -----------------------------------------------------------
    # 后台任务状态
    # -----------------------------------------------------------
    def is_busy(self) -> bool:
        """是否有后台任务在运行。"""
        return self._worker is not None and self._worker.isRunning()

    # -----------------------------------------------------------
    # 质检辅助
    # -----------------------------------------------------------
    def source_images(self) -> list:
        """来源目录里可读取的图片（多来源目录 + 显式顺序，未过滤「已移除」）。"""
        return self._merge_order(self._source_roots(), self._order_list())

    def source_label_index(self) -> dict:
        """当前数据集的「图片主干 → 标签」索引（多来源目录合并）。"""
        return self._service.label_index_for(self.source_images(), self._source_roots())

    # -----------------------------------------------------------
    # 图片清单与标注状态（图库 / 标注 / 标注检查页共用）
    # -----------------------------------------------------------
    # -----------------------------------------------------------
    # 图像位置：基础路径 / 缺失检查 / 重定位
    # -----------------------------------------------------------
    def source_roots(self) -> list[Path]:
        """当前存在的来源目录（供界面展示）。"""
        return list(self._source_roots())

    def base_path(self) -> str:
        """项目记录的基础路径（未设置时取首个来源目录）。"""
        project = self._project_vm.project if self._project_vm else None
        if project is not None:
            value = str(project.params.get("image_base_path") or "").strip()
            if value:
                return value
        roots = self._source_roots()
        return str(roots[0]) if roots else ""

    def set_base_path(self, path: str) -> None:
        """记录图像基础路径（随项目保存）。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return
        project.params["image_base_path"] = str(path).strip()
        project.touch()

    def missing_images(self) -> list[Path]:
        """已扫描到但磁盘上不存在的图片。"""
        return [path for path in self.images() if not Path(path).is_file()]

    def expected_image_count(self) -> int:
        """项目应有的图像数。

        来源目录整盘搬走时扫描为空，仅靠扫描无法判断「少了多少」，
        因此以「`.mprj` 图像归档条目」与「数据集统计的导入张数」作为基准，
        与扫描结果取最大值。
        """
        project = self._project_vm.project if self._project_vm else None
        archived = len(project.find_by_kind("image")) if project is not None else 0
        recorded = int(getattr(self._dataset, "image_count", 0) or 0)
        return max(archived, recorded, len(self.images()))

    def missing_count(self) -> int:
        """缺失图像数量。"""
        return max(0, self.expected_image_count() - len(self.images()))

    def relocate_images(self, new_root: str) -> dict:
        """把缺失图像重定位到新根目录。

        与 DLT 一致：要求文件名与目录结构保持一致，**全部找到**才生效；
        否则只报告数量、不改动来源。
        """
        root = Path(new_root)
        result = {"found": 0, "total": self.missing_count(), "relocated": False}
        if not root.is_dir() or result["total"] <= 0:
            return result

        names = {path.name.lower() for path in self.images()}
        found_names = {
            image.name.lower()
            for image in root.rglob("*")
            if image.is_file() and image.suffix.lower() in IMAGE_EXTS
        }
        if names:
            found = len(names & found_names)
            total = len(names)
        else:
            # 来源目录已整体搬走：按新目录里的图片数与应有数量比对
            found = len(found_names)
            total = max(result["total"], found)
        result["found"] = found
        result["total"] = total
        if total <= 0 or found < total:
            return result

        dataset = self._dataset
        if dataset is None:
            return result
        remapped = self._remap_recorded_paths(root)
        dataset.source_path = str(root)
        dataset.source_paths = [str(root)]
        self._invalidate_caches()
        result["remapped"] = remapped
        project = self._project_vm.project if self._project_vm else None
        if project is not None:
            project.params["image_base_path"] = str(root)
            project.touch()
        self.datasetChanged.emit(dataset)
        result["relocated"] = True
        return result

    def _remap_recorded_paths(self, root: Path) -> int:
        """把项目里记录的旧图片路径改写到新根目录。

        图库顺序（`image_order`）、移除清单（`image_excludes`）与按路径索引的
        元数据（如标记映射）都保存的是绝对路径；来源搬移后必须一并改写，
        否则「重定位」后图库仍是空的。这里按「图片文件名 + 尾部目录」匹配，
        递归处理列表 / 字典，返回改写的条目数。
        """
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return 0
        index: dict[str, list[Path]] = {}
        for candidate in root.rglob("*"):
            if candidate.is_file() and candidate.suffix.lower() in IMAGE_EXTS:
                index.setdefault(candidate.name.lower(), []).append(candidate)
        if not index:
            return 0

        counter = {"count": 0}

        def walk(node):
            if isinstance(node, str):
                path = Path(node)
                if path.suffix.lower() not in IMAGE_EXTS:
                    return node
                target = self._match_relocated(path, index)
                if target is None:
                    return node
                counter["count"] += 1
                return str(target)
            if isinstance(node, list):
                return [walk(item) for item in node]
            if isinstance(node, dict):
                return {walk(key): walk(value) for key, value in node.items()}
            return node

        project.params = walk(project.params)
        project.touch()
        return counter["count"]

    @staticmethod
    def _match_relocated(image: Path, index: dict) -> Path | None:
        """按文件名 + 尾部目录名匹配重定位目标（目录层级一致者优先）。"""
        candidates = index.get(image.name.lower(), [])
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        wanted = [part.lower() for part in image.parts[-3:-1]]

        def score(candidate: Path) -> int:
            parents = [part.lower() for part in candidate.parts[-4:-2]]
            return sum(
                1 for offset, name in enumerate(wanted)
                if name in parents[offset: offset + 2]
            )

        return max(candidates, key=score)

    def _invalidate_caches(self) -> None:
        """来源目录变化后清空各类缓存。"""
        self._resolved_source = None
        self._roots_cache = None
        self._hash_cache = None
        self._images = None
        self._label_index = None
        self._subsets = None
        self._meta_cache = {}
        self._label_info_cache = {}

    def images(self, refresh: bool = False) -> list[Path]:
        """当前数据集的图片清单（带缓存，已排除从数据集移除的图片）。"""
        if refresh or self._images is None:
            excluded = self._excluded_paths()
            self._images = [
                path for path in self.source_images() if str(path) not in excluded
            ]
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

        判定顺序：
            1. 项目里有该图片的**类别覆盖值**（导入时按子文件夹标签写入 / 手工指定）
               → 覆盖值非空即已标注，显式为空的「无标签」即未标注
            2. **分类任务**：图片位于某个类别目录下即为已标注（类别本身就是标注结果）
            3. **检测 / 分割 / 异常**：存在且非空的 YOLO 标签文件
        """
        images = self.images()
        if not images:
            return []
        overrides = self._params_dict("class_overrides")
        roots = self._source_roots()

        if self.split_layout() == "classify":
            # 已知类别 = 数据集统计里的类别 ∪ 项目类别表
            names = set(self._dataset.class_names) if self._dataset else set()
            project = self._project_vm.project if self._project_vm else None
            if project is not None:
                names.update(str(cls.name) for cls in project.classes)
            if not names:
                names = set(DatasetService.child_class_dirs_multi(roots))
            flags: list[bool] = []
            for image in images:
                key = self._relative_key(image, roots)
                if key in overrides:
                    flags.append(bool(str(overrides[key] or "").strip()))
                else:
                    # 图片落在某个类别目录里即为已标注
                    flags.append(self.image_class(image) in names)
            return flags

        label_dir = self.label_dir()
        flags = []
        for image in images:
            key = self._relative_key(image, roots)
            if key in overrides:
                flags.append(bool(str(overrides[key] or "").strip()))
                continue
            flags.append(
                label_dir is not None
                and DatasetService.label_has_content(
                    AnnotationService.label_path_for(image, label_dir)
                )
            )
        return flags

    def annotated_count(self) -> int:
        return sum(1 for flag in self.annotated_flags() if flag)

    def _known_class_names(self) -> set[str]:
        """已知类别名：数据集统计到的类别 ∪ 项目类别表。"""
        names = {
            str(item) for item in (getattr(self._dataset, "class_names", []) or [])
        }
        project = self._project_vm.project if self._project_vm else None
        names.update(
            str(item.name) for item in (getattr(project, "classes", []) or [])
        )
        return {name for name in names if name}

    def _is_source_root(self, directory: Path) -> bool:
        """目录是否为数据集来源目录（多来源导入时就是各个类别目录）。"""
        text = str(directory)
        return any(text == str(root) for root in self._source_roots())

    def image_class(self, image) -> str:
        """图片所属类别名（分类看目录名，检测看标签首个类别 id）。

        返回空串表示「无标签」——手工指定过的图片以项目里的覆盖值为准；
        分类任务里直接放在来源根目录下（不在任何类别子目录里）的图片也算无标签。
        """
        override = self._params_dict("class_overrides")
        key = self._image_key(image)
        if key in override:
            return str(override[key] or "")
        layout = self.split_layout()
        if layout == "classify":
            parent = Path(image).parent
            known = self._known_class_names()
            if known and self._is_source_root(parent) and parent.name not in known:
                return ""
            return parent.name
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

    def _sync_class_stats(self, dataset: Dataset) -> None:
        """把图库中**实际用到的类别**写回数据集统计（概览的「类别数」）。

        类别来源依次为：导入时按子文件夹标签写入的类别、手工指定的类别、
        YOLO 标签文件、分类目录名 —— 与图库里的判定口径完全一致，
        避免出现「图库显示 3 类、概览显示 0 类」的不一致。
        """
        counts = {
            name: count
            for name, count in self.class_distribution().items()
            if name and name != UNLABELED_LABEL
        }
        if counts:
            dataset.class_names = list(counts)
            dataset.class_counts = dict(counts)

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
        kinds, counts = self._anomaly_inputs()
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
                kinds=kinds,
                counts=counts,
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

    def class_names_for(self, paths: list) -> list[str]:
        """给定图片序列 → 每张图的类别名（供缩略图叠加类别名）。"""
        names = {
            str(image): self._class_name_of(self.image_class(image))
            for image in self.images()
        }
        return [names.get(str(path), "") for path in paths]

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
        names = [
            self._class_name_of(self.image_class(image)) for image in images
        ]

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
            "classnames": [str(value(names, p, "")) for p in paths],
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
        """当前使用的拆分名称（拆分映射下拉框显示用）。

        与 :meth:`split_name` 同源，都以模型中的当前拆分为准；
        ``params["split_name"]`` 只是兼容旧项目的下沉副本，不作为显示依据
        ——副本过期时图库会显示上一次的拆分名，与数据拆分页对不上。
        """
        return self.split_name()

    # -----------------------------------------------------------
    # 异常检测：按类别分配（良好 / 异常）
    # -----------------------------------------------------------
    def _class_kind(self, name: str) -> str:
        """类别的类型（normal / abnormal，"" 为未指定）。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return ""
        for cls in project.classes:
            if str(cls.name) == str(name):
                return str(getattr(cls, "kind", "") or "")
        return ""

    def anomaly_kinds(self) -> dict[str, str]:
        """每张图片的类别归属：normal / abnormal（未标注的图片不在其中）。

        类别类型取自项目类别表（导入弹窗按文件夹指定）；类别类型未指定的
        按「良好」处理——导入弹窗的默认值就是「良好」，无类别的图片不参与
        分配（与 DLT 的「已标注 / 全部」一致）。
        """
        kinds: dict[str, str] = {}
        for image in self.images():
            name = self.image_class(image)
            if not name:
                continue
            kind = self._class_kind(name)
            kinds[str(image)] = kind if kind == "abnormal" else "normal"
        return kinds

    def anomaly_totals(self) -> dict[str, int]:
        """分配的分母：各类别图片数与「已标注 / 全部」。"""
        images = self.images()
        kinds = self.anomaly_kinds()
        normal = sum(1 for kind in kinds.values() if kind == "normal")
        abnormal = sum(1 for kind in kinds.values() if kind == "abnormal")
        return {
            "normal": normal,
            "abnormal": abnormal,
            "labeled": normal + abnormal,
            "all": len(images),
        }

    def anomaly_allocation(self) -> dict[str, dict[str, int]]:
        """当前拆分按类别的分配数量（行和恒等于该类图片数）。

        未配置过时给一份按当前比例的建议值；异常图的训练数量恒为 0
        （异常检测只用正常样本训练，异常图进验证 / 测试）。
        """
        stored = getattr(self.active_split(), "anomaly_counts", None) or {}
        totals = self.anomaly_totals()
        dataset = self._dataset
        ratio = (
            dataset.split_train, dataset.split_val, dataset.split_test
        ) if dataset is not None else (0.7, 0.2, 0.1)
        plan: dict[str, dict[str, int]] = {}
        for kind in ("normal", "abnormal"):
            weights = {
                sub: max(0, int((stored.get(kind) or {}).get(sub, 0)))
                for sub in SPLIT_SUBDIRS
            }
            if not any(weights.values()):
                weights = {
                    sub: max(0, round(float(ratio[index]) * 1000))
                    for index, sub in enumerate(SPLIT_SUBDIRS)
                }
            if kind == "abnormal":
                weights["train"] = 0
            sizes = DatasetService._proportional_sizes(
                int(totals.get(kind, 0)), weights
            )
            plan[kind] = {sub: int(sizes.get(sub, 0)) for sub in SPLIT_SUBDIRS}
        return plan

    def set_anomaly_allocation(self, plan: dict) -> None:
        """保存按类别的分配数量到当前拆分，并让比例字段跟随分配结果。"""
        split = self.active_split()
        project = self._project_vm.project if self._project_vm else None
        if split is None or project is None:
            return
        stored: dict[str, dict[str, int]] = {}
        for kind in ("normal", "abnormal"):
            rows = (plan or {}).get(kind) or {}
            stored[kind] = {
                sub: max(0, int(rows.get(sub, 0))) for sub in SPLIT_SUBDIRS
            }
        stored["abnormal"]["train"] = 0        # 训练集只放良好图
        split.anomaly_counts = stored

        total = sum(sum(rows.values()) for rows in stored.values())
        shares = {
            sub: sum(rows[sub] for rows in stored.values()) / total if total else 0.0
            for sub in SPLIT_SUBDIRS
        }
        split.train, split.val, split.test = (
            shares["train"], shares["val"], shares["test"]
        )
        if self._dataset is not None:
            self._dataset.split_train = shares["train"]
            self._dataset.split_val = shares["val"]
            self._dataset.split_test = shares["test"]
        project.touch()
        self._subsets = None
        self._emit_splits()

    def _sync_anomaly_root(self, split) -> None:
        """异常检测：训练数据目录跟随当前拆分产物。

        用户自选的外部目录（不属于本项目的任何拆分产物）保持不动；
        空值或指向本项目其它拆分产物时，改用当前拆分的产物目录。
        """
        project = self._project_vm.project if self._project_vm else None
        if project is None or split is None or not self.is_anomaly():
            return
        out_dir = str(getattr(split, "output_dir", "") or "").strip()
        if not out_dir:
            return
        known = {
            str(item.output_dir).strip()
            for item in project.splits
            if str(getattr(item, "output_dir", "") or "").strip()
        }
        current = str(getattr(project.training, "anomaly_root", "") or "").strip()
        if current and current not in known:
            return
        project.training.anomaly_root = out_dir

    def _anomaly_inputs(self) -> tuple[dict | None, dict | None]:
        """异常检测的分配输入（其余任务返回 (None, None)，走比例分配）。"""
        if not self.is_anomaly():
            return None, None
        kinds = self.anomaly_kinds()
        if not kinds:
            return None, None
        plan = self.anomaly_allocation()
        return kinds, {kind: dict(rows) for kind, rows in plan.items()}

    def split_locked(self) -> bool:
        """当前拆分是否已被训练使用（使用后不再改比例）。"""
        split = self.active_split()
        if split is not None and split.locked:
            return True
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return False
        # 兼容旧项目：训练已完成，且用的正是当前拆分的 data.yaml
        status = str(getattr(project.training, "status", "") or "")
        trained_yaml = str(getattr(project.training, "data_yaml", "") or "")
        current_yaml = str(getattr(split, "data_yaml", "") or "")
        return (
            bool(trained_yaml)
            and trained_yaml == current_yaml
            and status in ("running", "finished", "done", "success")
        )

    # -----------------------------------------------------------
    # 组合筛选：标签状态 / 图像标记 / 数据集划分 / 文本
    # -----------------------------------------------------------
    # -----------------------------------------------------------
    # 图像元信息 / 备注 / 标签（供自定义筛选规则与标签统计使用）
    # -----------------------------------------------------------
    def image_meta(self, image) -> dict:
        """图像元信息：{"width", "height", "channels"}。

        只读文件头（PIL 不解码像素），结果按路径缓存 —— 只有用到尺寸 / 通道数的
        筛选规则才会走这里，避免无谓的解码开销。
        """
        key = str(image)
        cached = self._meta_cache.get(key)
        if cached is not None:
            return dict(cached)
        meta = {"width": 0, "height": 0, "channels": 0}
        try:
            from PIL import Image as PILImage

            with PILImage.open(key) as handle:
                meta["width"], meta["height"] = (int(value) for value in handle.size)
                meta["channels"] = len(handle.getbands())
        except Exception as exc:  # noqa: BLE001 - 无法识别的图片按 0 处理
            logger.debug("读取图像元信息失败 %s: %s", key, exc)
        self._meta_cache[key] = dict(meta)
        return dict(meta)

    @staticmethod
    def _read_label_summary(label_path) -> tuple[int, list[int]]:
        """读标签文件：返回 (标注数量, 类别 id 列表)。"""
        if label_path is None:
            return 0, []
        try:
            text = Path(label_path).read_text(encoding="utf-8")
        except OSError:
            return 0, []
        count = 0
        ids: set[int] = set()
        for line in text.splitlines():
            parts = line.split()
            if not parts:
                continue
            count += 1
            try:
                ids.add(int(float(parts[0])))
            except ValueError:
                continue
        return count, sorted(ids)

    def _class_name_of(self, value) -> str:
        """类别 id / 名称 → 显示用类别名（无法映射时原样返回）。"""
        text = str(value)
        if not text.lstrip("-").isdigit():
            return text
        class_id = int(text)
        dataset = self._dataset
        names = [str(item) for item in (getattr(dataset, "class_names", []) or [])]
        if 0 <= class_id < len(names):
            return names[class_id]
        project = self._project_vm.project if self._project_vm else None
        for item in getattr(project, "classes", []) or []:
            raw = getattr(item, "class_id", None)
            try:
                same = raw is not None and int(raw) == class_id
            except (TypeError, ValueError):
                same = False
            if same:
                return str(item.name)
        return text

    def label_info(self, image) -> dict:
        """图片的标注数量与标注类别：{"count", "classes"}（带缓存）。"""
        key = self._image_key(image)
        cached = self._label_info_cache.get(key)
        if cached is not None:
            return dict(cached)
        info = {"count": 0, "classes": []}
        if self.split_layout() == "classify":
            name = self.image_class(image)
            if name and name != UNLABELED_LABEL:
                info = {"count": 1, "classes": [name]}
        else:
            label = self.label_index().get(Path(image).stem)
            count, ids = self._read_label_summary(label)
            if count:
                info = {
                    "count": count,
                    "classes": [self._class_name_of(item) for item in ids],
                }
        self._label_info_cache[key] = dict(info)
        return dict(info)

    def notes_dir(self) -> Path | None:
        """图片备注目录（项目文件同级的 `notes/`，与标注页保持同一口径）。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return None
        path = str(project.params.get("path", "") or "")
        return (Path(path).parent / "notes") if path else None

    def image_note(self, image) -> str:
        """图片备注文本（没有备注返回空串）。"""
        directory = self.notes_dir()
        if directory is None:
            return ""
        file = directory / f"{Path(image).stem}.txt"
        try:
            return file.read_text(encoding="utf-8").strip() if file.is_file() else ""
        except OSError:
            return ""

    # -----------------------------------------------------------
    # 自定义筛选规则
    # -----------------------------------------------------------
    def filter_rules(self) -> dict:
        """项目里保存的自定义筛选规则（无规则返回空字典）。"""
        tree = self._params_dict("filter_rules")
        return dict(tree) if isinstance(tree, dict) else {}

    def set_filter_rules(self, tree) -> None:
        """保存自定义筛选规则（随项目落盘）。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            self.message.emit("warning", "请先创建或打开项目")
            return
        project.params["filter_rules"] = dict(tree or {})
        self._commit_meta("筛选规则已更新")

    def count_filtered(self, rules: dict) -> tuple[int, int]:
        """规则预览：返回 (命中张数, 总张数)。"""
        return len(self.filter_images(rules=rules)), len(self.images())

    # -----------------------------------------------------------
    # 标签统计
    # -----------------------------------------------------------
    def label_statistics(self, paths: list | None = None, scope: str = "all") -> dict:
        """标签统计：类别 / 数据集拆分 / 图像标记 的数量与占比 + 标注覆盖。

        Args:
            paths: 限定统计的图片（选中集）；None 表示全部图片。
            scope: 只作为结果里的标记透传（all / selection）。
        """
        images = self.images()
        if paths is None:
            selected = list(images)
        else:
            wanted = {str(item) for item in paths}
            selected = [image for image in images if str(image) in wanted]
        total = len(selected)
        flags = self.annotated_flags()
        subsets = self.subset_labels()
        position = {str(image): index for index, image in enumerate(images)}
        project = self._project_vm.project if self._project_vm else None
        class_colors = {
            str(item.name): str(item.color or "")
            for item in (getattr(project, "classes", []) or [])
        }

        classes: dict[str, int] = {}
        splits: dict[str, int] = {}
        tags: dict[str, int] = {}
        annotated = 0
        tagged = 0
        for image in selected:
            index = position.get(str(image), -1)
            if 0 <= index < len(flags) and flags[index]:
                annotated += 1
            name = self.image_class(image)
            label_name = self._class_name_of(name) if name else ""
            classes[label_name] = classes.get(label_name, 0) + 1
            subset = subsets[index] if 0 <= index < len(subsets) else ""
            split_key = _SUBSET_NAME.get(subset, "none")
            splits[split_key] = splits.get(split_key, 0) + 1
            names = self.image_tag_names_of(image)
            if names:
                tagged += 1
            for tag in names:
                tags[tag] = tags.get(tag, 0) + 1

        def share(count: int) -> float:
            return (count / total) if total else 0.0

        class_rows = [
            {"name": key, "count": classes[key], "share": share(classes[key]),
             "color": class_colors.get(key, "")}
            for key in sorted(classes, key=lambda item: (-classes[item], str(item)))
            if key
        ]
        if "" in classes:      # 「无标签」固定排在最后
            class_rows.append({
                "name": "", "count": classes[""],
                "share": share(classes[""]), "color": "",
            })

        split_labels = {"train": "训练", "val": "验证", "test": "测试", "none": "未划分"}
        split_rows = [
            {"name": split_labels[key], "count": splits.get(key, 0),
             "share": share(splits.get(key, 0))}
            for key in ("train", "val", "test", "none")
            if splits.get(key, 0)
        ]

        tag_rows = [
            {"name": key, "count": tags[key], "share": share(tags[key]),
             "color": self.tag_color(key)}
            for key in sorted(tags, key=lambda item: (-tags[item], str(item)))
        ]
        missing = total - tagged
        if missing > 0:
            tag_rows.append({"name": "", "count": missing, "share": share(missing),
                             "color": ""})
        return {
            "scope": str(scope),
            "total": total,
            "annotated": annotated,
            "classes": class_rows,
            "splits": split_rows,
            "tags": tag_rows,
        }

    def filter_images(
        self,
        label="all",
        mark="all",
        split="all",
        text: str = "",
        class_name="all",
        rules: dict | None = None,
    ) -> list[Path]:
        """按 标签状态 / 类别 / 图像标记 / 数据集划分 / 文本 过滤图片。

        Args:
            label: all / annotated / unannotated（可多选）。
            mark: all / tagged / untagged / 具体标记名（可多选，命中任一即可）。
            split: all / train / val / test / none（未划分，可多选）。
            text: 关键词，匹配文件名、类别名与标记名（不区分大小写）。
            class_name: all / ""（无标签）/ 具体类别名（可多选）。
            rules: 自定义筛选规则（条件树）；None 表示用项目里保存的规则。

        除 `text` 外均支持「单个值」或「值列表」；"all" / 空列表表示不筛选。
        """
        images = self.images()
        if not images:
            return []
        flags = self.annotated_flags()
        subsets = self.subset_labels()
        classes = [self.image_class(image) for image in images]
        tags = [self.image_tag_names_of(image) for image in images]
        keyword = text.strip().lower()

        # 自定义筛选规则：只在规则真正用到某个字段时才去读图像信息
        ruleset = FilterRules(self.filter_rules() if rules is None else rules)
        wanted = set() if ruleset.is_empty() else ruleset.fields()
        metas = [self.image_meta(image) for image in images] if wanted & {
            "width", "height", "channels"
        } else []
        infos = [self.label_info(image) for image in images] if wanted & {
            "label_count", "classes"
        } else []
        notes = [self.image_note(image) for image in images] if "comment" in wanted else []
        annotated_wanted = _filter_list(label)
        class_wanted = _filter_list(class_name)
        split_wanted = _filter_list(split)
        mark_wanted = _filter_list(mark)

        result: list[Path] = []
        for index, image in enumerate(images):
            annotated = flags[index] if index < len(flags) else False
            subset = subsets[index] if index < len(subsets) else ""
            spans = tags[index] if index < len(tags) else []
            if annotated_wanted is not None:
                state = "annotated" if annotated else "unannotated"
                if state not in annotated_wanted:
                    continue
            if class_wanted is not None and classes[index] not in class_wanted:
                continue
            if mark_wanted is not None:
                hit = ("tagged" in mark_wanted and spans) or (
                    "untagged" in mark_wanted and not spans
                )
                if not hit:
                    hit = any(name in spans for name in mark_wanted)
                if not hit:
                    continue
            if split_wanted is not None:
                current = _SUBSET_NAME.get(subset, "none")
                if current not in split_wanted:
                    continue
            if keyword and not (
                keyword in image.name.lower()
                or keyword in classes[index].lower()
                or any(keyword in span.lower() for span in spans)
            ):
                continue
            if not ruleset.is_empty() and not ruleset.match({
                "name": image.name,
                "path": str(image),
                "state": "annotated" if annotated else "unannotated",
                "comment": notes[index] if index < len(notes) else "",
                "tags": spans,
                "classes": infos[index]["classes"] if index < len(infos) else [],
                "split": _SUBSET_NAME.get(subset, "none") or "none",
                "label_count": infos[index]["count"] if index < len(infos) else 0,
                "width": metas[index]["width"] if index < len(metas) else 0,
                "height": metas[index]["height"] if index < len(metas) else 0,
                "channels": metas[index]["channels"] if index < len(metas) else 0,
            }):
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
        """把图片从**数据集**中移除（只影响程序读取的图库，不删除本地文件）。

        图片会记入项目内的「已移除清单」，图库 / 标注 / 检查 / 拆分等页面
        随即不再读取它们；本地文件保持原样，重新导入即可恢复。

        Returns:
            实际移除的图片数。
        """
        project = self._project_vm.project if self._project_vm else None
        if project is None or not paths:
            return 0
        targets = [Path(p) for p in paths]
        # 已经不在图库里的图片不必重复记录
        current = {str(path) for path in self.images()}
        gone = [target for target in targets if str(target) in current]
        if not gone:
            self.message.emit("warning", "所选图片已不在数据集中")
            return 0

        keys = set(self._keys(gone))
        for key in keys:
            self._params_dict("class_overrides").pop(key, None)
            self._params_dict("split_overrides").pop(key, None)
            self._params_dict("image_tag_map").pop(key, None)

        excludes = self._exclude_list()
        excludes.extend(str(target) for target in gone)
        self._set_excludes(excludes)

        # 内容哈希缓存里也要摘掉，否则重新导入时会被当成「重复图片」而跳过
        if self._hash_cache is not None:
            for target in gone:
                self._hash_cache.pop(str(target), None)

        # 同步数据集统计（概览 / 类别数的图片数都要跟着变）
        self._refresh_stats(project)

        project.touch()
        try:
            self._project_vm.service.save(project)
        except (OSError, ValueError) as exc:
            logger.warning("保存项目失败: %s", exc)
            self.message.emit("error", f"保存项目失败：{exc}")
        self.invalidate_images()
        self.datasetChanged.emit(self._dataset)
        self._project_vm.notify_changed()
        self.message.emit(
            "success",
            f"已从数据集移除 {len(gone)} 张（本地文件未删除）",
        )
        return len(gone)

    def _refresh_stats(self, project) -> None:
        """按当前图库内容重算数据集统计（图片 / 标签 / 类别计数）。"""
        self.invalidate_images()
        self._roots_cache = None
        roots = self._source_roots()
        images = self.images(refresh=True)
        label_index = self.label_index(refresh=True)
        previous = self._dataset
        dataset = self._service.summarize_library(
            roots,
            images=images,
            label_index=label_index,
            name=previous.name if previous is not None else "",
            duplicate_count=previous.duplicate_count if previous is not None else 0,
        )
        self._inherit_settings(dataset, previous)
        self._dataset = dataset
        self._sync_class_stats(dataset)
        project.dataset = dataset

    # -----------------------------------------------------------
    # 内部：图片标识与项目元信息
    # -----------------------------------------------------------
    def _image_key(self, image) -> str:
        """图片在项目内的稳定标识（相对数据集来源目录的路径）。"""
        return self._relative_key(image, self._source_roots())

    def _keys(self, images: list) -> list[str]:
        """批量计算图片标识（复用同一次来源目录解析）。"""
        roots = self._source_roots()
        return [self._relative_key(image, roots) for image in images]

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
        self._label_info_cache = {}

    # -----------------------------------------------------------
    # 拆分名称（决定划分产物的目录名）
    # -----------------------------------------------------------
    def split_name(self) -> str:
        """当前拆分的名称（同时是划分产物的目录名）。"""
        split = self.active_split()
        if split is not None and split.name:
            return split.name
        project = self._project_vm.project if self._project_vm else None
        stored = str((project.params.get("split_name") if project else "") or "").strip()
        return stored or DEFAULT_SPLIT_NAME

    def set_split_name(self, name: str) -> None:
        """设置当前拆分的名称（用于划分产物的目录名）。"""
        split = self.active_split()
        if split is None:
            return
        resolved = self.rename_split(split.split_id, name)
        project = self._project_vm.project if self._project_vm else None
        if project is not None:
            project.params["split_name"] = resolved
            project.touch()

    def notify(self, text: str, level: str = "info") -> None:
        """供界面反馈本地操作结果的提示通道。"""
        self.message.emit(level, text)

    def is_anomaly(self) -> bool:
        """项目是否为异常检测（导入弹窗据此显示「类别类型」列）。"""
        project = self._project_vm.project if self._project_vm else None
        return str(getattr(project, "model_type", "")) == "anomaly"

    def split_layout(self) -> str:
        """按项目类型返回数据集结构（声明在任务注册表）。

        已实现五种：分类（目录结构）、检测（images / labels）、OCR（检测结构
        + det / rec 产物）、语义分割（images + masks）、异常检测
        （normal / normal_test / abnormal）；未实现的一律回退为检测结构。
        """
        project = self._project_vm.project if self._project_vm else None
        layout = dataset_layout(project.model_type) if project is not None else "detect"
        if layout in ("classify", "ocr_det_rec", "mask", "anomaly_folder"):
            return layout
        return "detect"

    def preview_split(self) -> dict:
        """不落盘地计算划分结果（供拆分页预览饼图与类别分布）。"""
        images = self.images()
        if not images or self._dataset is None:
            return {}
        layout = self.split_layout()
        kinds, counts = self._anomaly_inputs()
        try:
            return DatasetService.preview_split(
                self.resolved_source(),
                split=(
                    self._dataset.split_train,
                    self._dataset.split_val,
                    self._dataset.split_test,
                ),
                seed=self._dataset.seed,
                stratified=self._dataset.stratified,
                layout=layout,
                images=images,
                label_index={} if layout == "classify" else self.label_index(),
                kinds=kinds,
                counts=counts,
            )
        except (OSError, ValueError) as exc:
            logger.warning("划分预览失败: %s", exc)
            return {}

    # -----------------------------------------------------------
    # 内部
    # -----------------------------------------------------------
    def _set_dataset(self, dataset: Dataset, notice: str = "") -> None:
        self._dataset = dataset
        self._resolved_source = None
        self._roots_cache = None
        self._hash_cache = None
        self._images = None
        self._label_index = None
        self._subsets = None
        self._sync_class_stats(dataset)
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
            notice or (
                f"导入完成：{dataset.image_count} 张图片，{dataset.class_count} 个类别"
                + (f"，剔重 {dataset.duplicate_count} 张" if dataset.duplicate_count else "")
            ),
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

        dest = ProjectService.project_dir(mprj) / "files"
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
