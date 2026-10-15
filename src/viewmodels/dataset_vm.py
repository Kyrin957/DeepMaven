"""数据集 ViewModel：导入预览、统计、划分与归档。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Signal

from src.models.dataset import Dataset
from src.models.split import Split
from src.services.annotation_service import AnnotationService
from src.services.category_service import CategoryService
from src.services.quality_service import QualityService
from src.services.dataset_service import DatasetService, SplitCancelled
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
from src.utils.workers import ThreadTask
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
        self._worker: ThreadTask | None = None
        # 「未保存标注落盘」回调（MainWindow 装配时接标注页）：执行拆分 /
        # 质检这类要按**标签文件**工作的流程，必须先把内存里的标注写盘
        self._flush_annotations: Callable[[], None] | None = None
        # 源目录不可用时，从项目归档释放出来的工作目录
        self._resolved_source: Path | None = None
        # 来源目录列表缓存（避免逐张图片调用时反复解析路径）
        self._roots_cache: list[Path] | None = None
        # 已有图片的「文件大小 → {路径: (mtime, 内容哈希)}」索引（导入去重用）
        self._media_index: dict[int, dict[str, tuple[int, str]]] | None = None
        # 图片清单与标签索引缓存（数据量大，避免每次刷新都重新扫盘）
        self._images: list[Path] | None = None
        self._label_index: dict | None = None
        # 图片所属子集标记缓存（T/V/E）
        self._subsets: list[str] | None = None
        # 标注页推送的未落盘标注状态（{图片绝对路径: 是否已标注}）——
        # 让缩略图角标与「已标注」统计与内存中尚未保存的改动保持一致
        self._pending_annotations: dict[str, bool] = {}
        # 元数据索引（万张级刷新不再逐图读盘 / stat，见《开发文档》§4.89）：
        # `_meta_revision` 是「元数据可能已变」的版本号，所有按版本缓存的索引
        # 都存 (revision, 值)，失效只需 +1（比逐处清缓存更不容易漏）
        self._meta_revision = 0
        self._class_index: tuple[int, dict[str, str]] | None = None
        self._annotated_index: tuple[int, list[bool]] | None = None
        self._known_names_index: tuple[int, set[str]] | None = None
        # 位置索引（清单对象 + 路径→行号，检查页切图用）与相对键索引（标记用）
        self._position_index: tuple | None = None
        self._key_index: tuple | None = None
        # 划分预览结果缓存（签名 = 元数据版本 + 清单 + 拆分参数，见 preview_split）
        self._preview_cache: tuple | None = None

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

        # 3) 去重：批内重复 + 与图库已有图片内容重复（SHA256；先按文件大小预筛，
        #    只对撞大小的候选算哈希，见 `_filter_duplicates`）
        dedupe = bool(options.get("dedupe", True))
        accepted, skipped, _hashes = self._filter_duplicates(ordered, dedupe)
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
        # 去重索引由 `_filter_duplicates` 就地更新（新图片带哈希），这里不再回写
        logger.info(
            "导入数据集 %s: 新增 %s 张，跳过重复 %s 张，合计 %s 张",
            dataset.name, len(accepted), skipped, dataset.image_count,
        )
        return dataset

    def _filter_duplicates(
        self, images: list, dedupe: bool, _known: dict | None = None
    ) -> tuple[list[Path], int, dict]:
        """按内容哈希过滤重复图片（**先按文件大小预筛**，只对撞大小的候选算哈希）。

        内容相同的文件大小必然相同，因此「与图库已有图片重复」只可能发生在
        大小相同的候选里；万张级项目据此把「导入前对全库算一遍 SHA256」
        （实测 ≈ 数秒）降到几乎为零（§4.89）。

        Returns:
            (可导入的图片, 跳过的张数, 新图片的 {路径: 哈希})。
        """
        if not dedupe:
            return [Path(p) for p in images], 0, {}

        known: dict[int, dict[str, tuple[int, str]]] = self._existing_media()
        seen = {
            digest for group in known.values()
            for _mtime, digest in group.values() if digest
        }
        accepted: list[Path] = []
        hashes: dict[str, str] = {}
        skipped = 0
        for image in images:
            path = Path(image)
            try:
                info = path.stat()
            except OSError as exc:
                logger.warning("读取失败 %s: %s", path, exc)
                continue
            size = int(info.st_size)
            group = known.get(size)
            if group is None:
                # 图库里没有同尺寸的图片 → 不可能是重复内容，免算哈希
                accepted.append(path)
                continue
            # 同一路径且签名未变 → 这张图**本来就在图库里**（同一份文件），
            # 直接判为重复、连哈希都不用算：「重新导入同一批图片」不再把几万个
            # 文件重读一遍（§4.89）。
            # 注：已从数据集移除的图片会随 `_media_index` 一起作废，因此「移除后
            # 再导入」仍能重新进来。
            entry = group.get(str(path))
            if entry is not None and entry[0] == int(info.st_mtime_ns):
                skipped += 1
                continue
            # 撞大小但签名不同（改了内容 / 换了文件）：算哈希按内容判重
            try:
                digest = self._service.file_sha256(path)
            except OSError as exc:
                logger.warning("读取失败 %s: %s", path, exc)
                continue
            if digest in seen:
                skipped += 1
                continue
            seen.add(digest)
            group[str(path)] = (int(info.st_mtime_ns), digest)
            accepted.append(path)
            hashes[str(path)] = digest
        return accepted, skipped, hashes

    def _existing_media(self) -> dict[int, dict[str, tuple[int, str]]]:
        """已有图片按**文件大小**分组：{字节数: {路径: (mtime_ns, 内容哈希)}}。

        只做 `stat`（不读内容）；哈希按需填充（撞大小才需要），并缓存在实例上，
        导入多次不会重复算（§4.89）。
        """
        if self._media_index is None:
            index: dict[int, dict[str, tuple[int, str]]] = {}
            for image in self.images():
                try:
                    info = image.stat()
                except OSError:
                    continue
                index.setdefault(int(info.st_size), {})[str(image)] = (
                    int(info.st_mtime_ns), ""
                )
            self._media_index = index
        return self._media_index

    def _known_hashes(self) -> dict[str, str]:
        """已有图片的 {路径: 内容哈希}（按需补齐；导入去重已不依赖它）。"""
        result: dict[str, str] = {}
        for group in self._existing_media().values():
            for path, (mtime_ns, digest) in list(group.items()):
                if not digest:
                    try:
                        digest = self._service.file_sha256(path)
                    except OSError:
                        continue
                    group[path] = (mtime_ns, digest)
                result[path] = digest
        return result

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
        """项目内的全部拆分（新建项目默认为空，由「新建」显式创建）。"""
        project = self._project_vm.project if self._project_vm else None
        return list(project.splits) if project is not None else []

    def splits_ready(self) -> list[dict]:
        """拆分列表的展示数据（下拉 / 列表通用）。

        ``lock_reason``："" = 可编辑 / 删除；running = 训练中 / 暂停（配置
        指向该拆分）；used = 完成过训练——界面据此禁用按钮并画锁图标。
        """
        return [
            {
                "id": split.split_id,
                "name": split.name,
                "label": f"{split.name} · {split.ratio_text}"
                         + (f" · {split.total()} 张" if split.ready else ""),
                "ready": split.ready,
                "locked": split.locked,
                "lock_reason": self.split_lock_reason(split.split_id),
                "counts": {key: int(value) for key, value in split.counts.items()},
                "total": split.total(),
            }
            for split in self.splits()
        ]

    def active_split(self):
        """当前选中的拆分（无拆分时为 None）。"""
        project = self._project_vm.project if self._project_vm else None
        return project.active_split() if project is not None else None

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
        """重命名拆分（名称同时用作划分产物的目录名，磁盘目录一并改名）。"""
        project = self._project_vm.project if self._project_vm else None
        split = project.split_by_id(split_id) if project is not None else None
        if project is None or split is None:
            return ""
        title = (name or "").strip() or split.name
        other = project.split_by_name(title)
        if other is not None and other.split_id != split.split_id:
            self.message.emit("warning", f"拆分名「{title}」已存在")
            return split.name
        old_name = split.name
        split.name = title
        if title != old_name:
            # 产物目录随名称走：重命名而不是下次执行拆分时新建一个目录
            # （历史缺陷：改名后执行拆分新增目录、旧目录残留）
            self._rename_split_dirs(project, split, old_name)
        if project.active_split() is split:
            project.params["split_name"] = title
            project.training.split_name = title
        project.touch()
        self._emit_splits()
        return title

    def _rename_split_dirs(self, project, split, old_name: str) -> None:
        """产物目录随拆分改名（项目内目录直接移动，路径引用同步更新）。"""
        path_text = str(project.params.get("path") or "").strip()
        if not path_text:
            return
        root = ProjectService.project_dir(Path(path_text))
        dataset_root = ProjectService.dataset_dir(Path(path_text))
        # 兼容两种布局：新（<root>/dataset/<名>）与旧（<root>/<名>）
        old_dir = next(
            (
                candidate
                for candidate in (
                    dataset_root / old_name,
                    root / old_name,
                )
                if candidate.is_dir()
            ),
            dataset_root / old_name,
        )
        new_dir = dataset_root / split.name
        if not old_dir.is_dir() or new_dir.exists():
            return
        try:
            shutil.move(str(old_dir), str(new_dir))
        except OSError as exc:
            logger.warning("拆分目录改名失败 %s -> %s: %s", old_dir, new_dir, exc)
            return

        old_text, new_text = str(old_dir), str(new_dir)

        def _sub(value: str) -> str:
            text = str(value or "")
            if text.startswith(old_text):
                return new_text + text[len(old_text):]
            return text

        split.output_dir = _sub(split.output_dir)
        split.data_yaml = _sub(split.data_yaml)
        dataset = project.dataset
        dataset.output_path = _sub(dataset.output_path)
        dataset.data_yaml = _sub(dataset.data_yaml)
        project.training.data_yaml = _sub(project.training.data_yaml)
        project.training.anomaly_root = _sub(
            getattr(project.training, "anomaly_root", "")
        )

    def suggest_split_name(self) -> str:
        """给「新建拆分」弹窗的默认名称（拆分N，重名自动让位）。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return ""
        split_id = project.next_split_id()
        title = f"拆分{split_id + 1}"
        if project.split_by_name(title) is not None:
            title = f"{title} ({split_id + 1})"
        return title

    def remove_split(self, split_id: int) -> bool:
        """删除一套拆分：连同磁盘上的产物目录（来源数据不动）。

        被训练锁定的拆分不允许删除（训练中 / 完成过训练）——守卫在这里
        兜底，界面同时禁用删除按钮；来源目录（数据集本体）绝不删除。
        """
        project = self._project_vm.project if self._project_vm else None
        split = project.split_by_id(split_id) if project is not None else None
        if project is None or split is None:
            return False
        reason = self.split_lock_reason(split.split_id)
        if reason:
            self.message.emit(
                "warning",
                "该拆分正在训练中，无法删除" if reason == "running"
                else "该拆分已用于训练，无法删除",
            )
            return False
        # 不再要求「至少保留一套」：只有被训练引用的拆分不可删除
        # （running / paused / used，见 split_lock_reason 守卫）
        removed = self._remove_split_dirs(project, split)
        project.splits = [
            item for item in project.splits if item.split_id != split.split_id
        ]
        fallback = project.splits[0] if project.splits else None
        if project.active_split_id == split.split_id:
            project.active_split_id = fallback.split_id if fallback else 0
            # 当前拆分换人后同步兼容副本，避免界面残留已删除的拆分名；
            # 删光拆分时清掉副本，回到「未创建拆分」状态
            if fallback is not None:
                project.params["split_name"] = fallback.name
            else:
                project.params.pop("split_name", None)
        # 训练配置引用被删拆分时回退到剩余拆分（无剩余则清空，由
        # 后端 validate 引导用户重新创建）
        try:
            training_split = int(getattr(project.training, "split_id", -1) or -1)
        except (TypeError, ValueError):
            training_split = -1
        if training_split == split.split_id:
            project.training.split_id = fallback.split_id if fallback else 0
            project.training.split_name = fallback.name if fallback else ""
            project.training.data_yaml = (
                fallback.data_yaml if fallback else ""
            )
        # 异常检测的数据集根目录指向该拆分产物时一并清空（避免悬空路径）
        root = str(getattr(project.training, "anomaly_root", "") or "").strip()
        if root and root.lower() in {str(path).lower() for path in removed}:
            project.training.anomaly_root = ""
        project.touch()
        self._mirror_split(project.active_split())
        self._emit_splits()
        detail = f"（已清理产物目录 {len(removed)} 个）" if removed else ""
        self.message.emit("success", f"已删除拆分「{split.name}」{detail}")
        return True

    def _remove_split_dirs(self, project, split) -> list:
        """删除拆分的产物目录（返回实际删除的路径；来源目录绝不删）。"""
        root = ProjectService.project_dir(
            Path(str(project.params.get("path") or ""))
        )
        sources = {
            str(Path(raw)).lower()
            for raw in (getattr(project.dataset, "sources", []) or [])
        }
        dataset_root = ProjectService.dataset_dir(
            Path(str(project.params.get("path") or ""))
        )
        candidates = [
            str(getattr(split, "output_dir", "") or "").strip(),
            str(dataset_root / split.name) if root else "",
            str(root / split.name) if root else "",
        ]
        removed: list = []
        seen: set[str] = set()
        for raw in candidates:
            if not raw:
                continue
            target = Path(raw)
            key = str(target).lower()
            if key in seen or key in sources:
                continue
            seen.add(key)
            if not target.is_dir():
                continue
            try:
                shutil.rmtree(target)
            except OSError as exc:
                logger.warning("删除拆分产物目录失败 %s: %s", target, exc)
                continue
            removed.append(target)
        return removed

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
        """执行数据集划分：**后台线程**落盘 + 生成 data.yaml + 归档进项目。

        万张级下「算法 + 1.5 万文件落盘」是秒级重活（实测首次 14.6 s、二次
        15.3 s 全在主线程），改为 worker + 进度：主线程只做输入快照与结果写入，
        页面接 `taskProgress` 显示进度、`taskFinished` 后刷新（§4.90）。
        失败 / 取消时旧产物保持不动（新产物整份丢弃）。
        """
        source = self.resolved_source()
        if self._dataset is None or source is None:
            self.message.emit("warning", "请先导入数据集")
            return
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            self.message.emit("warning", "请先创建或打开项目")
            return
        if project.active_split() is None:
            self.message.emit("warning", "请先在拆分列表新建一套拆分")
            return

        if self._worker is not None and self._worker.isRunning():
            self.message.emit("warning", "已有任务在运行，请稍候")
            return

        # 未保存的标注**先落盘**：拆分产物按「主干 → 标签文件」配对，内存里的
        # pending 不写盘就会整批丢标签（历史缺陷 §4.91：标注 → 拆分 → 产物
        # 一个标签都没有）；回调由 MainWindow 装配时接标注页
        if self._flush_annotations is not None:
            self._flush_annotations()

        mprj = Path(project.params.get("path", ""))
        # 产物写进项目文件夹的 dataset 子目录（删除项目时一并清理）
        out_dir = ProjectService.dataset_dir(mprj) / self.split_name()
        # 分类任务输出 train/<类别>/ 目录结构，检测/分割输出 images+labels 结构
        layout = self.split_layout()
        # 图库可能由多个文件夹累加而成，划分按界面上看到的图片清单进行
        library = self.images()
        # 标注刚落盘 → **强制重建**标签索引（缓存在落盘前就建好的话是空的）
        label_index = self.label_index(refresh=True)
        # 异常检测：按「良好 / 异常」分配，训练集只放良好图
        kinds, counts = self._anomaly_inputs()
        # 手工拆分映射：图库 / 标注页指定的图片优先落位（与预览同口径）
        split_overrides = self._resolved_overrides("split_overrides", library)

        # 输入一律**快照**成纯数据：worker 在线程里跑算法与落盘，不碰 VM / Qt
        # 对象（万张级下这是秒级重活，主线程只留信号与状态写入，见 §4.90）
        context = {
            "source": str(source),
            "roots": [str(item) for item in self._source_roots()],
            "out_dir": str(out_dir),
            "library": [str(path) for path in library],
            "label_index": {
                str(stem): str(path) for stem, path in (label_index or {}).items()
            },
            "split": (
                self._dataset.split_train,
                self._dataset.split_val,
                self._dataset.split_test,
            ),
            "seed": int(self._dataset.seed),
            "stratified": bool(self._dataset.stratified),
            "layout": layout,
            "kinds": dict(kinds or {}),
            "counts": {
                str(kind): dict(rows) for kind, rows in (counts or {}).items()
            },
            "overrides": dict(split_overrides or {}),
            "class_fallback": [
                str(name) for name in (project.class_names or self._dataset.class_names)
            ],
        }
        self._start_worker(
            lambda progress, is_cancelled: self._split_job(
                context, progress, is_cancelled
            ),
            "执行拆分",
            on_done=self._apply_split_result,
        )

    @staticmethod
    def _split_job(context: dict, progress, is_cancelled) -> dict:
        """worker：预检查 + 落盘 + 归档项（**纯数据 / 文件系统，不碰界面**）。

        「执行拆分」是秒级重活（万张级：算法 ~0.1 s + 1.5 万文件落盘 ~3 s），
        放在后台线程跑，主线程只接进度与结果（§4.90）。
        """
        service = DatasetService
        source = Path(context["source"])
        out_dir = Path(context["out_dir"])
        layout = str(context["layout"])
        library = [Path(path) for path in context["library"]]
        label_index = {
            str(stem): Path(path)
            for stem, path in (context["label_index"] or {}).items()
        }
        kinds = dict(context["kinds"] or {})
        counts = dict(context["counts"] or {})
        overrides = dict(context["overrides"] or {})
        split = tuple(context["split"])
        if is_cancelled():
            return {"cancelled": True}

        # 预检查：验证集为空时后端会直接失败（val=None），这里提前拦住
        try:
            buckets = service.split_members(
                library,
                {} if layout == "classify" else label_index,
                split=split, seed=int(context["seed"]),
                stratified=bool(context["stratified"]), layout=layout,
                kinds=kinds or None, counts=counts or None,
                overrides=overrides or None,
            )
        except (OSError, ValueError):
            buckets = {}
        if buckets and (not buckets["train"] or not buckets["val"]):
            return {
                "warning": "训练集或验证集为空，请调整分配数量或增加图片"
                if kinds else "验证集为空，请调整拆分比例或增加图片"
            }

        def report(done: int, total: int) -> None:
            percent = int(done * 100 / total) if total else 0
            progress(percent, f"正在写入 {done} / {total}")

        try:
            stats = service.split_dataset(
                source, out_dir,
                # seed 必须与预览 / 预检查同源：不传时 split_dataset 走默认 0，
                # 自定义种子的拆分就会出现「概览（按种子 N）与落盘产物（按 0）」
                # 成员不同的隐性错位（张数相同、分到各子集的图片不同）
                split=split, seed=int(context["seed"]),
                stratified=bool(context["stratified"]), layout=layout,
                images=library, label_index=label_index or None,
                kinds=kinds or None, counts=counts or None,
                overrides=overrides or None,
                progress=report, cancel=is_cancelled,
            )
        except SplitCancelled:
            return {"cancelled": True}
        except (OSError, ValueError) as exc:
            logger.warning("数据集划分失败: %s", exc)
            return {"error": str(exc)}
        if stats.get("cancelled"):
            return {"cancelled": True}
        if stats["total"] == 0:
            return {"warning": "来源目录中未找到图片"}

        if layout == "classify":
            # 分类任务：Ultralytics 直接以数据集目录作为 --data，按子目录扫描
            classes = [
                str(item)
                for item in service.child_class_dirs_multi(
                    [Path(root) for root in context["roots"]]
                )
            ]
            config_path: Path = out_dir
        elif layout == "anomaly_folder":
            # 异常检测没有 YAML：产物目录本身即数据集
            # （normal / normal_test / abnormal，Anomalib Folder 约定）
            classes = []
            config_path = out_dir
        else:
            classes = [str(name) for name in service.load_class_names(source)]
            if not classes:
                # 回退：优先用项目类别定义，其次用数据集中观察到的类别 id
                classes = [str(name) for name in context["class_fallback"]]
            config_path = Path(service.write_data_yaml(
                out_dir, classes, "images/train", "images/val", "images/test"
            ))

        # 归档项只取**数据配置**（data.yaml）：历史实现把整个产物目录读成归档项
        # （1.5 万个文件、含每张图的字节）再筛掉 image / label，万张级白扫 1.7 s
        config_item = None
        if config_path.is_file():
            try:
                relative = config_path.relative_to(out_dir).as_posix()
            except ValueError:
                relative = config_path.name
            config_item = ("config", f"dataset/{relative}", config_path.read_bytes())
        # 顺便生成项目封面（最近项目卡片用），随本次保存一起写进 .mprj
        cover = service.make_cover(library[0]) if library else None
        return {
            "stats": stats,
            "classes": classes,
            "config_path": str(config_path),
            "out_dir": str(out_dir),
            "layout": layout,
            "config_item": config_item,
            "cover": cover,
        }

    def _apply_split_result(self, payload: dict) -> None:
        """worker 结果回**主线程**：写拆分 / 数据集字段、归档配置、发信号。"""
        if payload.get("cancelled"):
            self.message.emit("warning", "已取消执行拆分")
            return
        if payload.get("warning"):
            self.message.emit("warning", str(payload["warning"]))
            return
        if payload.get("error"):
            self.message.emit("error", f"数据集划分失败：{payload['error']}")
            return

        project = self._project_vm.project if self._project_vm else None
        if project is None or self._dataset is None:
            return
        stats = dict(payload.get("stats") or {})
        classes = list(payload.get("classes") or [])
        config_path = Path(str(payload.get("config_path") or ""))
        out_dir = Path(str(payload.get("out_dir") or ""))
        layout = str(payload.get("layout") or "")

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
        items: list[tuple[str, str, bytes]] = []
        if payload.get("config_item"):
            items.append(tuple(payload["config_item"]))
        if payload.get("cover"):
            items.append(("other", COVER_ENTRY_NAME, payload["cover"]))
        try:
            if items:
                self._project_vm.service.add_files(project, items, save=True)
            else:
                self._project_vm.save_project()
        except (OSError, ValueError) as exc:
            logger.warning("划分产物归档失败: %s", exc)

        # 子集归属：产物已生成，**立刻按新产物算一次并缓存**——否则执行完的
        # 各页刷新会各自复算一遍（万张级每次 ~0.26 s，§4.90）
        self._subsets = None
        self.subset_labels()
        self.datasetChanged.emit(self._dataset)
        self._project_vm.notify_changed()
        self.splitsChanged.emit(self.splits_ready())
        self.datasetReady.emit(str(config_path))

        # 存储明细只进日志：界面反馈只讲结果与各子集张数（UI 规则 §10）
        logger.info(
            "划分产物落盘：链接 %s 张 / 复制 %s 张（跨盘或权限不足时复制）",
            int(stats.get("linked", 0)), int(stats.get("copied", 0)),
        )
        # 与拆分概览 / 拆分列表同一口径（实际张数），便于对照
        self.message.emit(
            "success",
            "划分完成："
            + " / ".join(
                f"{SPLIT_LABELS[sub]} {stats[sub]}"
                for sub in ("train", "val", "test")
            ),
        )

    def load_from_project(self, project) -> None:
        """打开 / 新建项目后，从项目数据恢复数据集状态。

        **缓存必须全部作废**（含跨项目残留）：标签索引、位置 / 相对键 / 类别 /
        已标注 / 已知类别名索引、划分预览缓存，以及上一项目的 pending 标注
        ——它们都可能是上一个项目（或上一次会话）的状态，不清就会把 A 项目
        的判定套到 B 项目上（历史缺陷 §4.91）。
        """
        self._resolved_source = None
        self._roots_cache = None
        self._media_index = None
        self._images = None
        self._subsets = None
        self._label_index = None
        self._position_index = None
        self._key_index = None
        self._class_index = None
        self._annotated_index = None
        self._known_names_index = None
        self._preview_cache = None
        self._pending_annotations = {}
        dataset = getattr(project, "dataset", None) if project is not None else None
        if dataset is None or not (dataset.image_count or dataset.source_path):
            self._dataset = None
        else:
            self._dataset = dataset
        if project is not None:
            # 拆分不再自动补建（新建项目从空开始）；旧格式项目的历史迁移
            # 在 Project.from_dict 完成
            split = project.active_split()
            # 兼容副本「当前拆分名」跟随模型：副本过期时，凡按下沉字段显示的
            # 位置都会残留上一次的拆分名（如删除当前拆分后未同步）
            if split is not None:
                project.params["split_name"] = split.name
            self._mirror_split(split)
            self.splitsChanged.emit(self.splits_ready())
        self._touch_meta()               # 索引全部作废后统一升版本
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
        """后台执行数据质检：重复图片 / 模糊 / 曝光 / 标签校验（按任务差异）。"""
        if self.is_busy():
            self.message.emit("warning", "已有任务在运行")
            return
        images = self.images()
        if not images:
            self.message.emit("warning", "请先导入数据集")
            return
        # 质检按**磁盘上的标签文件**校验：内存里的未保存标注先落盘（§4.91）
        if self._flush_annotations is not None:
            self._flush_annotations()
        label_index = self.source_label_index()
        # 项目任务决定标签校验口径（分类 / 异常检测无 YOLO 标签体系，
        # 分割按多边形点列、旋转框按四角点校验——见 QualityService）
        project = self._project_vm.project if self._project_vm else None
        task = str(getattr(project, "model_type", "") or "detect")

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
            labels = QualityService.check_labels(images, label_index, task)
            progress(100, "完成")
            return {
                "total": len(images),
                "task": task,
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
        self._media_index = None
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
        self._media_index = None
        self._images = None
        self._label_index = None
        self._subsets = None
        self._position_index = None
        self._key_index = None
        self._touch_meta()

    def _touch_meta(self) -> None:
        """标记「元数据可能已变」：让按版本缓存的索引（类别 / 已标注 / 类别名）

        在下次读取时重建。调用点覆盖全部可能改变元数据的通道——图片集合变化
        （导入 / 移除）、类别覆盖值 / 标记 / 拆分归属改动（`_commit_meta`）、
        标注页推送的 pending 状态（含落盘后的推送）、数据集对象更替。

        **标签索引一并失效**：标注落盘走的就是 pending 推送这条路，标签文件
        在两次推送之间可能新增 / 改写——不清的话「主干 → 标签」索引还是旧的
        （历史缺陷 §4.91：标注后执行拆分，产物按陈旧索引配对，一个标签都
        拷不进去，看起来就是「标签丢了」）。
        """
        self._meta_revision += 1
        self._label_index = None

    def _class_map(self) -> dict[str, str]:
        """图片路径 → 原始类别名（一次建好，按元数据版本复用）。

        万张级项目里图库 / 检查页一次刷新要多次取「每张图的类别」（分布统计、
        类别颜色、类别名标签、筛选），历史实现每次都重新判定一遍（含标签读盘）
        ——索引化后这些遍历只剩字典查找（§4.89）。
        """
        revision = self._meta_revision
        cached = self._class_index
        if cached is not None and cached[0] == revision:
            return cached[1]
        mapping = {
            str(image): self.image_class(image) for image in self.images()
        }
        self._class_index = (revision, mapping)
        return mapping

    def image_position(self, image) -> int:
        """图片在清单中的位置（-1 = 不在清单里）。

        检查页每次点击 / 方向键切图都要定位当前图，历史实现用
        `images.index()`（O(N)），万张级下每次点击多花几十毫秒。
        """
        images = self.images()
        cached = self._position_index
        if cached is not None and cached[0] is images:
            return cached[1].get(str(image), -1)
        mapping = {str(item): index for index, item in enumerate(images)}
        self._position_index = (images, mapping)
        return mapping.get(str(image), -1)

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

    def set_flush_annotations(self, flush: Callable[[], None] | None) -> None:
        """注入「未保存标注落盘」回调（MainWindow 装配时接标注页）。

        执行拆分 / 数据质检这类按**标签文件**工作的流程，开始前必须把标注页
        内存里的 pending 标注写盘，否则它们不参与（历史缺陷 §4.91）。
        """
        self._flush_annotations = flush

    def set_pending_annotations(self, mapping: dict | None) -> None:
        """接收标注页推送的未落盘标注状态（{绝对路径: 是否已标注}）。

        缩略图角标与「已标注」统计据此合并内存中尚未保存的改动——
        选「不保存」退出后，下次打开读到的是修改前的磁盘状态。
        """
        incoming = {
            str(key): bool(value) for key, value in (mapping or {}).items()
        }
        if incoming == self._pending_annotations:
            return
        self._pending_annotations = incoming
        # pending 变化会影响「已标注」判定；标注**落盘**后也是走这条路推送
        # （`annotate_vm.save()` → `_sync_pending_flags`），因此标签文件改写
        # 带来的类别 / 已标注变化在这里一并失效
        self._touch_meta()

    def is_annotated(self, image) -> bool:
        """单张图片是否「已标注」（与 :meth:`annotated_flags` 同一判定口径）。

        判定顺序：
            1. 内存中有未保存改动（标注页 pending）→ 以内存状态为准；
            2. 项目里有该图片的**类别覆盖值**（导入时按子文件夹标签写入 / 手工
               指定）→ 覆盖值非空即已标注，显式为空的「无标签」即未标注；
            3. **整图任务（分类 / 异常检测）**：类别本身就是标注结果——图片
               落在已知类别目录（或类别覆盖值非空）即已标注，**没有 YOLO
               标签文件**；
            4. **框选 / 分割 / 旋转框 / OCR**：存在且非空的 YOLO 标签文件。
        """
        image = Path(image)
        pending = self._pending_annotations.get(str(image))
        if pending is not None:
            return bool(pending)
        overrides = self._params_dict("class_overrides")
        roots = self._source_roots()
        key = self._relative_key(image, roots)
        if key in overrides:
            return bool(str(overrides[key] or "").strip())
        if self.split_layout() in ("classify", "anomaly_folder"):
            # 已知类别 = 数据集统计里的类别 ∪ 项目类别表（空表时用目录名兜底）
            names = set(self._dataset.class_names) if self._dataset else set()
            project = self._project_vm.project if self._project_vm else None
            if project is not None:
                names.update(str(cls.name) for cls in project.classes)
            if not names:
                names = set(DatasetService.child_class_dirs_multi(roots))
            return bool(self.image_class(image) in names)
        label_dir = self.label_dir()
        return bool(
            label_dir is not None
            and DatasetService.label_has_content(
                AnnotationService.label_path_for(image, label_dir)
            )
        )

    def annotated_flags(self) -> list[bool]:
        """每张图片是否「已标注」（判定口径见 :meth:`is_annotated`）。

        按元数据版本缓存：一次刷新里图库 / 检查页会取它两三次（角标、计数、
        筛选），万张级下每次都是 N 次判定 + N 次 stat（§4.89）。
        """
        revision = self._meta_revision
        cached = self._annotated_index
        if cached is not None and cached[0] == revision:
            return list(cached[1])
        flags = [self.is_annotated(image) for image in self.images()]
        self._annotated_index = (revision, flags)
        return list(flags)

    def annotated_count(self) -> int:
        return sum(1 for flag in self.annotated_flags() if flag)

    def _known_class_names(self) -> set[str]:
        """已知类别名：数据集统计到的类别 ∪ 项目类别表（按元数据版本缓存）。

        分类 / 异常检测项目里 `image_class` 每张图都会问它一次，历史实现每次
        重建两个集合并做并集（万张级下就是上万次冗余集合运算）。
        """
        revision = self._meta_revision
        cached = self._known_names_index
        if cached is not None and cached[0] == revision:
            return set(cached[1])
        names = {
            str(item) for item in (getattr(self._dataset, "class_names", []) or [])
        }
        project = self._project_vm.project if self._project_vm else None
        names.update(
            str(item.name) for item in (getattr(project, "classes", []) or [])
        )
        result = {name for name in names if name}
        self._known_names_index = (revision, result)
        return set(result)

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
            classes = self._class_map()          # 索引化：不再逐图判定类别
            pairs = [
                (p, f) for p, f in pairs if classes.get(str(p), "") == class_name
            ]
        if mode == "annotated":
            return [(path, True) for path, flag in pairs if flag]
        if mode == "unannotated":
            return [(path, False) for path, flag in pairs if not flag]
        return pairs

    def class_distribution(self) -> dict[str, int]:
        """各类别的实际图片数（按当前判定口径统计，走类别索引）。"""
        counts: dict[str, int] = {}
        for name in self._class_map().values():
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
        classes = self._class_map()          # 索引化：不再逐图判定类别
        return [palette.get(name, "") for name in classes.values()]

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
                overrides=self._resolved_overrides("split_overrides", images),
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
        colors = self.image_colors()
        subsets = self.subset_labels()

        def value(series: list, path) -> str:
            index = self.image_position(path)      # O(1)，不重建全表字典
            if index < 0 or index >= len(series):
                return ""
            return str(series[index])

        return (
            [value(colors, path) for path in paths],
            [value(subsets, path) for path in paths],
        )

    def class_names_for(self, paths: list) -> list[str]:
        """给定图片序列 → 每张图的类别名（缩略图右上角标签展开时显示）。

        取缓存好的类别名（`_class_name_of` 只是数字 id → 名称的映射，逐条算也
        很便宜），**不再为每张图重新判定类别**（历史实现即使只要几打路径，
        也会对全部图片各判定一次 + 读标签）。
        """
        classes = self._class_map()
        return [
            self._class_name_of(classes.get(str(path), "")) for path in paths
        ]

    def gallery_decorations(self, paths: list) -> dict:
        """一次性取出缩略图需要的全部装饰（避免多次 O(n) 复算）。

        Returns:
            {"annotated": [...], "colors": [...], "subsets": [...], "markers": [[...]]}
        """
        flags = self.annotated_flags()
        colors = self.image_colors()
        subsets = self.subset_labels()
        markers = self.marker_colors()
        classes = self._class_map()          # 索引：不再逐图判定类别
        names = {
            key: self._class_name_of(value) for key, value in classes.items()
        }

        def value(series: list, path, default):
            index = self.image_position(path)
            if index < 0 or index >= len(series):
                return default
            return series[index]

        return {
            "annotated": [bool(value(flags, p, False)) for p in paths],
            "colors": [str(value(colors, p, "")) for p in paths],
            "subsets": [str(value(subsets, p, "")) for p in paths],
            "markers": [list(value(markers, p, [])) for p in paths],
            # 类别名是「路径 → 名称」映射（不是按行号的下标序列）
            "classnames": [str(names.get(str(p), "")) for p in paths],
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
        """每张图片的标记颜色列表（供缩略图左下角色点）。

        批量走相对键索引（`_key_map`）：历史实现逐图算 `_image_key`，万张级下
        每次刷新都要重复一遍（§4.89）。
        """
        mapping = self.tag_map()
        palette = {item["name"]: item["color"] for item in self.image_tag_defs()}
        return [
            [palette.get(name, "") for name in mapping.get(key, [])][:4]
            for key in self._key_map().values()
        ]

    def _key_map(self) -> dict[str, str]:
        """图片路径 → 项目内相对键（一次建好，随图片清单对象复用）。"""
        images = self.images()
        cached = self._key_index
        if cached is not None and cached[0] is images:
            return cached[1]
        roots = self._source_roots()
        mapping = {
            str(image): self._relative_key(image, roots) for image in images
        }
        self._key_index = (images, mapping)
        return mapping

    def _tag_lists(self) -> list[list[str]]:
        """每张图片的标记名列表（批量；`filter_images` 用）。"""
        mapping = self.tag_map()
        return [list(mapping.get(key, [])) for key in self._key_map().values()]

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
        """把图片划入指定子集（train / val / test，"" = 不在任何一个拆分集里）。

        **已在目标子集的图片不写覆盖**：写覆盖会把「按比例算出来的归属」变成
        手工固定，剩余图片重新参与分配，于是「点图片当前所在的档位」反而把别的
        图片挪走（历史缺陷：20 张按 0.7/0.2/0.1 划分成 14/4/2，对训练集图片再点
        一次「训练」变成 14/3/3——验证集少一张、测试集多一张）。全部图片都已在
        该档位时不做任何改动，也不产生未保存标记。
        """
        targets = [Path(p) for p in images]
        keys = self._keys(targets)
        if not keys:
            return
        wanted = {"train": "T", "val": "V", "test": "E"}.get(split, "")
        label = SPLIT_LABELS.get(split, "未划分")
        current = self.subset_labels()
        position = {str(path): index for index, path in enumerate(self.images())}
        pending: list[str] = []
        for key, image in zip(keys, targets):
            index = position.get(str(image))
            if index is not None and index < len(current) and current[index] == wanted:
                continue                  # 已在该子集：保持算法分配的归属
            pending.append(key)
        if not pending:
            self.message.emit("info", f"所选图像已在「{label}」")
            return
        overrides = self._params_dict("split_overrides", create=True)
        for key in pending:
            overrides[key] = wanted
        self.invalidate_images()          # 覆盖值变了：清缓存后重算实际分布
        self._sync_split_counts()
        self._commit_meta(f"已划入「{label}」：{len(pending)} 张")

    def _sync_split_counts(self) -> None:
        """把手工改动后的**实际张数**写回当前拆分（列表 / 概览跟随更新）。

        只同步「张数」这一份统计：拆分比例（`train / val / test`）仍是**划分
        依据**、只在新建 / 编辑拆分时设定——跟随改动会导致下次复算的边界移动，
        其余图片又被重排（正是「改一张、别人跟着换位」的来源）。
        """
        split = self.active_split()
        if split is None:
            return
        summary = self.split_summary()
        split.counts = {
            "train": int(summary.get("T", 0)),
            "val": int(summary.get("V", 0)),
            "test": int(summary.get("E", 0)),
        }

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
        没有拆分时返回空串（界面显示「未创建拆分」占位）。
        """
        return self.split_name() if self.active_split() is not None else ""

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

    def anomaly_allocation(
        self, stored: dict | None = None, ratios: tuple | None = None
    ) -> dict[str, dict[str, int]]:
        """按类别（良好 / 异常）的分配数量（行和恒等于该类图片数）。

        Args:
            stored: 已保存的分配 ``{kind: {子集: 数量}}``；缺省取**当前拆分**的
                `Split.anomaly_counts`，为空时按 `ratios` 给建议值。
            ratios: 建议值的比例（缺省取数据集当前比例）。

        异常图的训练数量恒为 0（异常检测只用正常样本训练，异常图进验证 /
        测试）——与 `DatasetService._allocate_by_kind` 同一约定。
        """
        stored = (
            (getattr(self.active_split(), "anomaly_counts", None) or {})
            if stored is None else (stored or {})
        )
        totals = self.anomaly_totals()
        dataset = self._dataset
        ratio = ratios or (
            (dataset.split_train, dataset.split_val, dataset.split_test)
            if dataset is not None else (0.7, 0.2, 0.1)
        )
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

    def split_lock_reason(self, split_id: int | None = None) -> str:
        """拆分（缺省当前拆分）的锁定原因："" = 可改；running / used。

        锁定只按**该拆分**是否被训练使用判定（与 Halcon 一致）：
          * `running`：训练进行中（含暂停），且训练配置指向该拆分；
          * `used`：该拆分完成过训练（`Split.locked`，训练完成时按
            ``config.split_id`` 置位；旧项目兼容训练记录里的 split_id）。
        执行拆分（生成产物）**不锁定**——新建拆分只要没训练过，比例与映射
        都可自由调整（历史缺陷：`training` 的镜像字段被误当「已训练使用」，
        新建拆分执行一次拆分后即被误锁）。
        """
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return ""
        split = (
            project.split_by_id(split_id) if split_id is not None
            else self.active_split()
        )
        if split is None:
            return ""
        training = project.training
        status = str(getattr(training, "status", "") or "")
        if status in ("running", "paused"):
            try:
                active_id = int(getattr(training, "split_id", -1) or -1)
            except (TypeError, ValueError):
                active_id = -1
            if active_id == int(split.split_id):
                return "running"
        if split.locked:
            return "used"
        for record in list(getattr(training, "history", []) or []):
            try:
                used = int(record.get("split_id", -1) or -1)
            except (TypeError, ValueError):
                used = -1
            if used == int(split.split_id):
                return "used"
        return ""

    def split_locked(self, split_id: int | None = None) -> bool:
        """拆分（缺省当前拆分）是否已被训练锁定（见 `split_lock_reason`）。"""
        return bool(self.split_lock_reason(split_id))

    # -----------------------------------------------------------
    # 组合筛选：标签状态 / 图像标记 / 数据集划分 / 文本
    # -----------------------------------------------------------
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

    def filter_images(
        self,
        label="all",
        mark="all",
        split="all",
        text: str = "",
        class_name="all",
    ) -> list[Path]:
        """按 标签状态 / 类别 / 图像标记 / 数据集划分 / 文本 过滤图片。

        Args:
            label: all / annotated / unannotated（可多选）。
            mark: all / tagged / untagged / 具体标记名（可多选，命中任一即可）。
            split: all / train / val / test / none（未划分，可多选）。
            text: 关键词，匹配文件名、类别名与标记名（不区分大小写）。
            class_name: all / ""（无标签）/ 具体类别名（可多选）。

        除 `text` 外均支持「单个值」或「值列表」；"all" / 空列表表示不筛选。
        """
        images = self.images()
        if not images:
            return []
        flags = self.annotated_flags()
        subsets = self.subset_labels()
        classes = list(self._class_map().values())   # 索引：不再逐图判定类别
        tags = self._tag_lists()                     # 批量：不再逐图算相对键
        keyword = text.strip().lower()
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

        # 去重索引整份作废（下面 `invalidate_images()` 里置空）：被移除的图片
        # 若留在索引里，重新导入时会被当成「重复图片」而跳过
        self._media_index = None

        # 同步数据集统计（概览 / 类别数的图片数都要跟着变）
        self._refresh_stats(project)

        project.touch()
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

    def _resolved_overrides(self, key: str, images: list) -> dict:
        """项目内的手工覆盖（相对 key）→ ``{图片绝对路径: 值}``。

        ``key`` 取 "split_overrides"（T / V / E 映射）或 "class_overrides"
        （类别名，"" = 无标签）；值统一转成字符串，供服务层按绝对路径查询。
        """
        overrides = self._params_dict(key)
        if not overrides:
            return {}
        return {
            str(image): str(overrides[item_key] or "")
            for image, item_key in zip(images, self._keys(images))
            if item_key in overrides
        }

    def class_name_list(self) -> list:
        """类别 id → 名称表（标签里的数字 id 在统计 / 预览中显示为名称）。"""
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return []
        return list(project.class_names)

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
        """登记项目元信息改动（标记 / 拆分 / 类别覆盖）。

        未保存，随「保存项目」/ 退出询问统一落盘——选「不保存」即可回退。
        """
        project = self._project_vm.project if self._project_vm else None
        if project is None:
            return
        project.touch()
        self.invalidate_images()
        self.datasetChanged.emit(self._dataset)
        self._project_vm.notify_changed()
        self.message.emit("success", text)

    def invalidate_images(self) -> None:
        """图片发生变化（新增标注 / 重新划分）后清空清单缓存与元数据索引。"""
        self._images = None
        self._label_index = None
        self._subsets = None
        self._position_index = None
        self._key_index = None
        self._media_index = None
        self._touch_meta()

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
        """不落盘地计算划分结果（供拆分页预览饼图与类别分布）。

        与执行拆分**同口径**：手工拆分映射优先落位、手工类别覆盖参与类别
        统计、类别 id 显示为名称——图库 / 标注页改完类别或映射，预览随之
        更新（历史缺陷：预览不看这两个覆盖，改完「无标签」不变化）。
        """
        images = self.images()
        if not images or self._dataset is None:
            return {}
        layout = self.split_layout()
        kinds, counts = self._anomaly_inputs()
        # 同一份输入（元数据版本 + 图片清单 + 拆分参数 + 覆盖）只算一次：
        # 执行拆分后的各页刷新会各自求一次预览，万张级每次 ~0.2 s（§4.90）
        signature = (
            self._meta_revision,
            len(images),
            str(images[0]) if images else "",
            str(images[-1]) if images else "",
            layout,
            self._dataset.split_train,
            self._dataset.split_val,
            self._dataset.split_test,
            int(self._dataset.seed),
            bool(self._dataset.stratified),
        )
        if self._preview_cache is not None and self._preview_cache[0] == signature:
            return dict(self._preview_cache[1])
        try:
            result = DatasetService.preview_split(
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
                overrides=self._resolved_overrides("split_overrides", images),
                class_overrides=self._resolved_overrides(
                    "class_overrides", images
                ),
                class_names=self.class_name_list(),
            )
        except (OSError, ValueError) as exc:
            logger.warning("划分预览失败: %s", exc)
            return {}
        self._preview_cache = (signature, result)
        return dict(result)

    # -----------------------------------------------------------
    # 内部
    # -----------------------------------------------------------
    def _set_dataset(self, dataset: Dataset, notice: str = "") -> None:
        self._dataset = dataset
        self._resolved_source = None
        self._roots_cache = None
        self._media_index = None
        self._images = None
        self._label_index = None
        self._subsets = None
        self._touch_meta()
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
        """启动后台任务，并把进度 / 结果 / 错误转到界面。

        用 `ThreadTask`（标准库线程）而不是 QThread：本数据 VM 的后台任务
        （执行拆分 / 数据质检）都是**文件操作密集**的，某些环境下 QThread
        里的文件调用会被拖慢 4000×（155 ms/次 stat，§4.90 的历史排查）；
        标准库线程无此问题，接口与 QThread 版一致。
        """
        worker = ThreadTask(job, self)
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
