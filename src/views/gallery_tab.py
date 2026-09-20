"""图库页：数据集导入、统计、类别 / 拆分 / 标记管理与缩略图浏览。

界面结构参照 Halcon DLT 的图库视图：
    左侧信息栏（数据导入 / 数据集概览 / 标签类别 / 数据集拆分映射 / 图像标记）
    右侧：图像窗口上方的浏览筛选栏 + 缩略图网格
          （右上角按类别颜色标角标，右下角标 T/V/E，左下角是图像标记色点）

交互：
    * 选中图像后，点类别行 → 改为该类别（「无标签」= 不给类别）
    * 选中图像后，点拆分映射行 → 改划到对应子集
    * 点图像标记 → 该图像没有则追加、已有则删除（类似备注）
    * 缩略图右键 → 打开文件所在位置 / 另存图像为 / 移除所选图像
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    Action,
    CaptionLabel,
    CardWidget,
    MessageBox,
    RoundMenu,
    ScrollArea,
    StrongBodyLabel,
)

from src.models.filter_rules import FilterRules
from src.viewmodels.category_vm import CategoryViewModel
from src.viewmodels.dataset_vm import DatasetViewModel
from src.views.data_widgets import ClassCard, ImportCard, StatsCard, side_column
from src.views.dialogs import (
    FilterRulesDialog,
    ImportImagesDialog,
    LabelStatsDialog,
    TagEditDialog,
)
from src.views.dialogs.tag_edit_dialog import DEFAULT_TAG_COLOR
from src.views.gallery_widgets import DisplayBar, FilterBar, SplitMapCard, TagCard
from src.views.widgets import THUMB_STEPS, ThumbnailGrid

# 尺寸档位与网格、尺寸滑杆共用同一套（见 views/widgets/thumbnail_grid.py）
_THUMB_STEPS = THUMB_STEPS
_IMAGE_FILTER = "图片 (*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp)"
_SIDE_WIDTH = 292


class GalleryTab(QWidget):
    """图库页（数据 / 图库导入与浏览）。"""

    requestAnnotate = Signal(str)      # 请求在标注页打开某张图片

    def __init__(
        self,
        dataset_vm: DatasetViewModel,
        category_vm: CategoryViewModel | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._vm = dataset_vm
        self._category_vm = category_vm
        # 本页不可见时只标记「待刷新」，切回本页再重建，避免每次数据变动都重画网格
        self._stale = True
        self.setAcceptDrops(True)
        self._build_ui()
        self._bind()

    # -----------------------------------------------------------
    # UI
    # -----------------------------------------------------------
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(12)

        self.import_card = ImportCard(self)
        self.stats_card = StatsCard(self)
        self.class_card = ClassCard(self._category_vm, self)
        self.split_map_card = SplitMapCard(self)
        self.tag_card = TagCard(self)

        side = side_column(
            self.import_card,
            self.stats_card,
            self.class_card,
            self.split_map_card,
            self.tag_card,
            width=_SIDE_WIDTH - 20,
        )
        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(side)
        scroll.setFixedWidth(_SIDE_WIDTH)
        root.addWidget(scroll)

        main = QVBoxLayout()
        main.setSpacing(10)

        # 浏览筛选放在图像窗口上方（原顶部的标题标识已移除）
        self.filter_bar = FilterBar(self)
        main.addWidget(self.filter_bar)

        # 显示增强（亮度 / 对比度 / 类别名），只影响显示
        self.display_bar = DisplayBar(self)
        main.addWidget(self.display_bar)

        self.grid = ThumbnailGrid(self)
        self.grid.set_wheel_zoom(True)      # Ctrl + 滚轮缩放缩略图
        main.addWidget(self.grid, 1)
        self._apply_display()

        self.hint = CaptionLabel("", self)
        self.hint.setWordWrap(True)
        main.addWidget(self.hint)
        root.addLayout(main, 1)

    # -----------------------------------------------------------
    # 绑定
    # -----------------------------------------------------------
    def _bind(self) -> None:
        self.import_card.folderRequested.connect(self._on_import_folder)
        self.import_card.filesRequested.connect(self._on_import_files)

        self.filter_bar.filterChanged.connect(self.refresh)
        self.filter_bar.textChanged.connect(lambda _t: self.refresh())
        self.filter_bar.thumbSizeChanged.connect(self._on_thumb_size)
        self.filter_bar.rulesRequested.connect(self._on_rules)
        self.filter_bar.rulesCleared.connect(self._on_rules_cleared)
        self.filter_bar.statsRequested.connect(self._on_stats)
        self.display_bar.changed.connect(self._apply_display)
        # Ctrl + 滚轮缩放后回同步滑杆
        self.grid.thumbSizeChanged.connect(self._on_grid_thumb_size)

        self.class_card.classClicked.connect(self._on_class_clicked)
        self.class_card.changed.connect(self.refresh)

        self.split_map_card.splitRequested.connect(self._on_split_requested)
        self.tag_card.tagToggled.connect(self._on_tag_toggled)
        self.tag_card.addRequested.connect(self._on_add_tag)
        self.tag_card.editRequested.connect(self._on_edit_tag)
        self.tag_card.deleteRequested.connect(self._on_delete_tag)

        self.grid.imageActivated.connect(lambda _index: self._update_side_state())
        self.grid.imageDoubleClicked.connect(self._on_double_click)
        self.grid.imageContextMenu.connect(self._on_context_menu)

        # 按需自动刷新：数据变化 / 后台任务完成 / 重新划分 / 类别颜色变化
        self._vm.datasetChanged.connect(lambda _value: self._on_external_change())
        self._vm.taskFinished.connect(lambda _text: self._on_external_change())
        self._vm.splitChanged.connect(lambda _split: self._on_external_change())
        if self._category_vm is not None:
            self._category_vm.classesChanged.connect(
                lambda _classes: self._on_external_change()
            )

    def _on_external_change(self) -> None:
        """数据侧的变动：本页可见就立即刷新，否则留到切回本页时再刷。"""
        if self.isVisible():
            self.refresh()
        else:
            self._stale = True

    # -----------------------------------------------------------
    # 自定义筛选规则 / 标签统计 / 显示增强
    # -----------------------------------------------------------
    def _apply_display(self) -> None:
        """把显示增强参数下发给缩略图网格（只影响显示）。"""
        self.grid.set_display(**self.display_bar.values())

    def _rule_options(self) -> dict:
        """规则弹窗的候选值（类别 / 标记；拆分与状态由模型自带候选）。"""
        classes = (
            [str(cls.name) for cls in self._category_vm.classes]
            if self._category_vm is not None else []
        )
        return {"classes": classes, "tags": self._vm.all_tag_names()}

    def _on_rules(self) -> None:
        """打开自定义筛选规则弹窗（确定后随项目保存）。"""
        dialog = FilterRulesDialog(
            self,
            tree=self._vm.filter_rules(),
            options=self._rule_options(),
            counter=lambda tree: self._vm.count_filtered(tree),
        )
        if dialog.exec():
            self._vm.set_filter_rules(dialog.result_tree())

    def _on_rules_cleared(self) -> None:
        self._vm.set_filter_rules({})

    def _stats_provider(self, scope: str) -> dict:
        """标签统计数据源（整体 / 选中集）。"""
        paths = self.grid.selected_paths() if scope == "selection" else None
        return self._vm.label_statistics(paths, scope)

    def _on_stats(self) -> None:
        scope = "selection" if self.grid.selected_paths() else "all"
        LabelStatsDialog(self, provider=self._stats_provider, scope=scope).exec()

    # -----------------------------------------------------------
    # 刷新
    # -----------------------------------------------------------
    def refresh(self) -> None:
        dataset = self._vm.dataset
        self.stats_card.set_dataset(dataset)

        counts = self._class_counts()
        self.class_card.set_counts(counts)
        if self._category_vm is not None:
            self.class_card.set_classes(list(self._category_vm.classes), counts)

        # 筛选候选自动跟随左侧面板：标签类别 / 数据集拆分 / 图像标记
        if self._category_vm is not None:
            self.filter_bar.set_classes(
                [cls.name for cls in self._category_vm.classes]
            )
        self.filter_bar.set_tag_names(self._vm.all_tag_names())
        paths = self._vm.filter_images(
            label=self.filter_bar.label_value(),
            mark=self.filter_bar.mark_value(),
            split=self.filter_bar.split_value(),
            text=self.filter_bar.text(),
            class_name=self.filter_bar.class_value(),
        )
        new_paths = [str(path) for path in paths]
        decorations = self._vm.gallery_decorations(paths)
        names = decorations.get("classnames") or []

        # 图片集合没变时只更新角标，避免每次都重建 390 张缩略图
        if new_paths and new_paths == self.grid.paths():
            self.grid.set_annotated(
                decorations["annotated"],
                decorations["colors"],
                decorations["markers"],
                decorations["subsets"],
            )
            self.grid.set_class_names(names)
        else:
            self.grid.set_images(
                new_paths,
                decorations["annotated"],
                decorations["colors"],
                decorations["subsets"],
                decorations["markers"],
                classnames=names,
            )

        total = len(self._vm.images())
        self.filter_bar.set_summary(len(new_paths), total)
        rules = FilterRules(self._vm.filter_rules())
        self.filter_bar.set_rule_summary(rules.active_count(), rules.describe())
        self.hint.setText(
            "尚未导入图像"
            if dataset is None
            else f"已选 {len(self.grid.selected_indexes())} / {len(new_paths)} 张"
        )
        self._update_side_state()
        self._stale = False

    def _update_side_state(self) -> None:
        """刷新拆分映射与标记面板（依赖图片集合、选中项与已挂标记数）。"""
        labels = self._vm.subset_labels()
        images = self._vm.images()
        position = {str(path): index for index, path in enumerate(images)}
        current = ""
        selected = self.grid.selected_paths()
        if selected:
            index = position.get(str(selected[0]))
            if index is not None and index < len(labels):
                current = labels[index]
        self.split_map_card.set_state(
            self._vm.split_name_label(),
            self._vm.split_summary(),
            current,
            self._vm.split_locked(),
        )

        counts: dict[str, int] = {}
        for values in self._vm.tag_map().values():
            for name in values:
                counts[name] = counts.get(name, 0) + 1
        self.tag_card.set_tags(
            self._vm.all_tag_names(),
            {item["name"]: item["color"] for item in self._vm.image_tag_defs()},
            counts,
        )

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        if self._stale:
            self.refresh()

    def _require_selection(self) -> list[str]:
        """取选中的图片；没选时给出提示并返回空。"""
        paths = self.grid.selected_paths()
        if not paths:
            self._vm.notify("请先在图像窗口中选中图像", "warning")
        return paths

    def _class_counts(self) -> dict:
        """各类别的实际图片数；检测任务额外统计未标注张数。"""
        counts = self._vm.class_distribution()
        if self._vm.split_layout() != "classify":
            flags = self._vm.annotated_flags()
            counts["未标注"] = sum(1 for flag in flags if not flag)
        return counts

    def _on_thumb_size(self, step: int) -> None:
        index = max(0, min(len(_THUMB_STEPS) - 1, int(step)))
        self.grid.set_thumb_size(_THUMB_STEPS[index])
        self._vm.notify(f"缩略图尺寸：{self.grid.thumb_size()}px")

    def _on_grid_thumb_size(self, size: int) -> None:
        """Ctrl + 滚轮缩放后，把尺寸滑杆同步到对应档位。"""
        self.filter_bar.set_thumb_size(int(size))
        self._vm.notify(f"缩略图尺寸：{int(size)}px")

    def _on_double_click(self, index: int) -> None:
        path = self.grid.path_at(index)
        if path:
            self.requestAnnotate.emit(path)

    # -----------------------------------------------------------
    # 赋值：类别 / 拆分 / 标记
    # -----------------------------------------------------------
    def _on_class_clicked(self, name: str) -> None:
        """点类别行 → 把选中图像改为该类别（"" = 无标签）。"""
        paths = self._require_selection()
        if paths:
            self._vm.set_class_for(paths, name)

    def _on_split_requested(self, split: str) -> None:
        """点拆分映射行 → 把选中图像改划到该子集。"""
        if self._vm.split_locked():
            self._vm.notify("该拆分已用于训练，无法更改", "warning")
            return
        paths = self._require_selection()
        if paths:
            self._vm.set_split_for(paths, split)

    def _on_tag_toggled(self, name: str) -> None:
        """点图像标记 → 选中图像没有则追加、已有则删除。"""
        paths = self._require_selection()
        if paths:
            self._vm.toggle_image_tag(paths, name)

    # -----------------------------------------------------------
    # 图像标记定义
    # -----------------------------------------------------------
    def _on_add_tag(self) -> None:
        selected = self.grid.selected_paths()
        dialog = TagEditDialog(
            self.window(), color=DEFAULT_TAG_COLOR, has_selection=bool(selected)
        )
        dialog.applied.connect(self._apply_new_tag)
        if not dialog.exec():
            return
        self._apply_new_tag(dialog.result_data())

    def _apply_new_tag(self, data: dict) -> None:
        """新增标记定义；勾了「为当前图像分配标记」时同时挂到选中图像。"""
        if not self._vm.add_image_tag(data["text"], data["color"]):
            return
        if data.get("assign") and self.grid.selected_paths():
            self._vm.toggle_image_tag(self.grid.selected_paths(), data["text"])
        self.refresh()

    def _on_edit_tag(self, name: str) -> None:
        target = next(
            (item for item in self._vm.image_tag_defs() if item["name"] == name), None
        )
        if target is None:
            return
        selected = self.grid.selected_paths()
        dialog = TagEditDialog(
            self.window(),
            text=target["name"],
            color=target["color"] or DEFAULT_TAG_COLOR,
            editing=True,
            has_selection=bool(selected),
        )
        dialog.applied.connect(
            lambda data: self._apply_edit_tag(name, data)
        )
        if not dialog.exec():
            return
        self._apply_edit_tag(name, dialog.result_data())

    def _apply_edit_tag(self, old_name: str, data: dict) -> None:
        if not self._vm.update_image_tag(old_name, data["text"], data["color"]):
            return
        if data.get("assign") and self.grid.selected_paths():
            self._vm.toggle_image_tag(self.grid.selected_paths(), data["text"])
        self.refresh()

    def _on_delete_tag(self, name: str) -> None:
        box = MessageBox(
            "删除图像标记",
            f"确定要删除标记「{name}」吗？\n\n该标记会从所有图像上一并摘除。",
            self.window(),
        )
        box.yesButton.setText("删除")
        box.cancelButton.setText("取消")
        if box.exec():
            self._vm.remove_image_tag(name)

    # -----------------------------------------------------------
    # 缩略图右键菜单
    # -----------------------------------------------------------
    def _on_context_menu(self, index: int, position) -> None:
        paths = self.grid.selected_paths()
        if not paths:
            return
        menu = RoundMenu(parent=self)
        menu.addAction(
            Action("打开文件所在位置", triggered=lambda: self._open_folder(paths))
        )
        menu.addAction(Action("另存图像为…", triggered=lambda: self._save_as(paths)))
        menu.addSeparator()
        menu.addAction(
            Action(
                f"从数据集移除（{len(paths)} 张）",
                triggered=lambda: self._remove_images(paths),
            )
        )
        menu.exec(position)

    def _open_folder(self, paths: list) -> None:
        directory = Path(paths[0]).parent
        if not directory.is_dir():
            self._vm.notify("图像所在目录不存在", "warning")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def _save_as(self, paths: list) -> None:
        source = Path(paths[0])
        target, _ = QFileDialog.getSaveFileName(
            self, "另存图像为", str(source), _IMAGE_FILTER
        )
        if target:
            self._vm.save_image_as(source, target)

    def _remove_images(self, paths: list) -> None:
        box = MessageBox(
            "移除所选图像",
            f"确定要把这 {len(paths)} 张图像从数据集中移除吗？\n\n"
            "只移出图库，本地文件不会被删除。",
            self.window(),
        )
        box.yesButton.setText("移除")
        box.cancelButton.setText("取消")
        if box.exec():
            self._vm.remove_images(paths)

    # -----------------------------------------------------------
    # 快捷键 / 拖放入口
    # -----------------------------------------------------------
    def select_all(self) -> None:
        """全选当前图库（Ctrl + A）。"""
        self.grid.selectAll()
        self._update_side_state()

    def remove_selected(self) -> None:
        """移除选中的图像（Del）。"""
        paths = self._require_selection()
        if paths:
            self._remove_images(paths)

    def import_paths(self, paths: list) -> None:
        """拖放导入：文件夹按目录导入，单张图片按「父目录 + 文件子集」导入。"""
        targets = [Path(p) for p in paths]
        folders = [p for p in targets if p.is_dir()]
        files = [p for p in targets if p.is_file()]
        for folder in folders:
            self._vm.import_images(str(folder))
        for parent in {p.parent for p in files}:
            subset = [str(p) for p in files if p.parent == parent]
            self._vm.import_images(str(parent), {"files": subset})
        self.refresh()

    # -----------------------------------------------------------
    # 导入
    # -----------------------------------------------------------
    def _on_import_folder(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择图片文件夹")
        if directory:
            self._import_dialog("folder", [directory])

    def _on_import_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择图片", "", _IMAGE_FILTER
        )
        if paths:
            self._import_dialog("files", paths)

    def _import_dialog(self, mode: str, selection: list) -> None:
        """打开「导入图像」选项弹窗，按选项完成排序 / 插入位置 / 初步标注。"""
        classes = (
            [cls.name for cls in self._category_vm.classes]
            if self._category_vm is not None else []
        )
        existing = self._vm.images()
        dialog = ImportImagesDialog(
            self.window(),
            mode=mode,
            initial=selection,
            has_dataset=bool(existing),
            classes=classes,
            existing_count=len(existing),
        )
        if not dialog.exec():
            return

        data = dialog.result_data()
        label = str(data.get("label") or "")
        if label:
            # OK / NG 这类快捷初步标注：项目里还没有对应类别时先建类再导入
            self._ensure_class(label)
        if str(data.get("mode")) == "folder":
            paths = list(data.get("paths") or [])
            if paths:
                self._vm.import_images(paths[0], data)
        else:
            self._vm.import_files(list(data.get("paths") or []), data)

    def _ensure_class(self, name: str) -> None:
        """确保项目类别表里存在该类别（初步标注用）。"""
        if self._category_vm is None:
            return
        if any(str(cls.name) == name for cls in self._category_vm.classes):
            return
        self._category_vm.add_class(name)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        paths = [url.toLocalFile() for url in event.mimeData().urls()]
        if not paths:
            return
        directories = [p for p in paths if Path(p).is_dir()]
        files = [p for p in paths if Path(p).is_file()]
        if files:
            self._import_dialog("files", files)
        elif directories:
            self._import_dialog("folder", [directories[0]])
        event.acceptProposedAction()
