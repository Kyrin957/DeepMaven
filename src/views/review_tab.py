"""标注检查页：以缩略图排布复核标注质量与数据质量。

布局参照 Halcon DLT 的检查视图：
    左侧：已选择图像详情 / 标签类别 / 质检结果
    右侧：大卡片缩略图网格（已标注项带彩色角标与描边）
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    PrimaryPushButton,
    StrongBodyLabel,
)

from src.services.annotation_service import AnnotationService
from src.viewmodels.category_vm import CategoryViewModel
from src.viewmodels.dataset_vm import DatasetViewModel
from src.views.data_widgets import ClassCard, FilterCard, side_column
from src.views.widgets import (
    THUMB_LARGE,
    THUMB_MEDIUM,
    THUMB_SMALL,
    ThumbnailGrid,
)

_THUMB_STEPS = (THUMB_MEDIUM, THUMB_LARGE, THUMB_LARGE)


class ReviewTab(QWidget):
    """标注检查页。"""

    def __init__(
        self,
        dataset_vm: DatasetViewModel,
        category_vm: CategoryViewModel | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._vm = dataset_vm
        self._category_vm = category_vm
        self._filter = "all"
        self._class_filter = ""
        # 页面不可见时只记录待渲染的图片，切回本页再画
        self._pending_pairs: list = []
        self._stale = True
        self._build_ui()
        self._bind()
        self.refresh()

    # -----------------------------------------------------------
    # UI
    # -----------------------------------------------------------
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(12)

        root.addWidget(self._build_side())
        root.addLayout(self._build_main(), 1)

    def _build_side(self) -> QWidget:
        self.detail_card = CardWidget(self)
        layout = QVBoxLayout(self.detail_card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(5)
        layout.addWidget(StrongBodyLabel("已选择图像", self.detail_card))
        self.detail_name = BodyLabel("—", self.detail_card)
        self.detail_name.setWordWrap(True)
        layout.addWidget(self.detail_name)
        self.detail_path = CaptionLabel("路径：—", self.detail_card)
        self.detail_path.setWordWrap(True)
        layout.addWidget(self.detail_path)
        self.detail_class = CaptionLabel("类别：—", self.detail_card)
        layout.addWidget(self.detail_class)
        self.detail_state = CaptionLabel("状态：—", self.detail_card)
        layout.addWidget(self.detail_state)
        self.detail_count = CaptionLabel("标注对象：—", self.detail_card)
        layout.addWidget(self.detail_count)

        self.class_card = ClassCard(self._category_vm, self)
        self.class_card.setMaximumHeight(260)

        self.quality_card = CardWidget(self)
        quality_layout = QVBoxLayout(self.quality_card)
        quality_layout.setContentsMargins(14, 12, 14, 12)
        quality_layout.setSpacing(6)
        quality_layout.addWidget(StrongBodyLabel("质检", self.quality_card))
        self.quality_btn = PrimaryPushButton("一键质检", self.quality_card)
        quality_layout.addWidget(self.quality_btn)
        self.quality_list = QListWidget(self.quality_card)
        self.quality_list.setMinimumHeight(150)
        quality_layout.addWidget(self.quality_list)
        self.quality_hint = CaptionLabel(
            "检查重复图片、模糊、曝光异常与标签问题。", self.quality_card
        )
        self.quality_hint.setWordWrap(True)
        quality_layout.addWidget(self.quality_hint)

        return side_column(
            self.detail_card, self.class_card, self.quality_card, width=292
        )

    def _build_main(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(10)

        card = CardWidget(self)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(10)
        self.title = StrongBodyLabel("标注检查", card)
        layout.addWidget(self.title)
        layout.addStretch(1)
        self.count_label = CaptionLabel("", card)
        layout.addWidget(self.count_label)
        column.addWidget(card)

        self.filter_card = FilterCard(with_size=True, parent=self)
        column.addWidget(self.filter_card)

        self.grid = ThumbnailGrid(self)
        self.grid.set_thumb_size(THUMB_LARGE)
        column.addWidget(self.grid, 1)

        self.hint = CaptionLabel("", self)
        column.addWidget(self.hint)
        return column

    # -----------------------------------------------------------
    # 绑定
    # -----------------------------------------------------------
    def _bind(self) -> None:
        self.filter_card.filterChanged.connect(self._on_filter)
        self.filter_card.thumbSizeChanged.connect(self._on_thumb_size)
        self.grid.imageActivated.connect(self._on_selected)
        self.class_card.classClicked.connect(self._on_class_clicked)
        self.class_card.changed.connect(self.refresh)
        self.quality_btn.clicked.connect(self._vm.run_quality_check)
        self._vm.qualityReady.connect(self._on_quality)

        # 按需自动刷新（不再提供手动刷新按钮）
        self._vm.datasetChanged.connect(lambda _d: self.refresh())
        self._vm.taskFinished.connect(lambda _t: self.refresh())
        if self._category_vm is not None:
            self._category_vm.classesChanged.connect(lambda _c: self.refresh())

    # -----------------------------------------------------------
    # 刷新
    # -----------------------------------------------------------
    def refresh(self) -> None:
        counts = self._vm.class_distribution()
        if self._vm.split_layout() != "classify":
            flags = self._vm.annotated_flags()
            counts["未标注"] = sum(1 for flag in flags if not flag)
        if self._category_vm is not None:
            self.class_card.set_counts(counts)
            self.class_card.set_classes(list(self._category_vm.classes), counts)

        pairs = self._vm.filtered_images(self._filter, self._class_filter)
        self._pending_pairs = pairs
        # 本页不可见时不渲染缩略图，切回本页再画（避免打开项目就重建几百张图）
        if self.isVisible():
            self._render_grid()
        else:
            self._stale = True
        total = len(self._vm.images())
        annotated = self._vm.annotated_count()
        self.filter_card.set_summary(total, annotated)
        self.count_label.setText(f"显示 {len(pairs)} / {total} 张")

        condition = {
            "all": "全部", "annotated": "已标注", "unannotated": "未标注",
        }.get(self._filter, "全部")
        if self._class_filter:
            condition += f" · 类别「{self._class_filter}」"

        dataset = self._vm.dataset
        self.title.setText(f"标注检查 · {dataset.name}" if dataset else "标注检查")
        self.hint.setText(
            f"显示 {len(pairs)} / {total} 张（{condition}），已标注 {annotated} 张"
            f"（{0 if not total else round(annotated * 100 / total)}%）；"
            "点击缩略图查看详情。"
        )
        if self.isVisible():
            self._on_selected(self.grid.current_index())

    def _render_grid(self) -> None:
        """真正绘制缩略图（仅在页面可见时调用）。"""
        pairs = self._pending_pairs or []
        colors, subsets = self._vm.decorations_for([path for path, _ in pairs])
        self.grid.set_images(
            [path for path, _ in pairs],
            [flag for _, flag in pairs],
            colors,
            subsets,
        )
        # 没有选中项时默认选中第一张，保证详情面板不是空的
        if pairs and self.grid.current_index() < 0:
            self.grid.set_current_index(0)
        self._on_selected(self.grid.current_index())
        self._stale = False

    def refresh_marks(self) -> None:
        """只刷新「已标注」角标（切回本页时调用）。"""
        pairs = self._vm.filtered_images(self._filter, self._class_filter)
        if self.grid.count() != len(pairs):
            self._pending_pairs = pairs
            self._render_grid()
            return
        colors, _subsets = self._vm.decorations_for([path for path, _ in pairs])
        self.grid.set_annotated([flag for _, flag in pairs], colors)
        self.filter_card.set_summary(
            len(self._vm.images()), self._vm.annotated_count()
        )

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        if self._stale:
            self._render_grid()
        else:
            self.refresh_marks()

    def _on_filter(self, key: str) -> None:
        self._filter = key
        self._class_filter = ""
        self.refresh()

    def _on_class_clicked(self, name: str) -> None:
        """点击类别行 → 只看该类别；「全部类别」清除筛选显示全图。

        选中类别时重置状态筛选，避免两个条件取交集后结果为空。
        """
        self._class_filter = name
        if name:
            self._filter = "all"
            self.filter_card.set_filter("all")
        self.refresh()

    def _on_thumb_size(self, step: int) -> None:
        index = max(0, min(len(_THUMB_STEPS) - 1, int(step)))
        self.grid.set_thumb_size(_THUMB_STEPS[index])

    def _on_selected(self, index: int) -> None:
        path = self.grid.path_at(index)
        if not path:
            self.detail_name.setText("—")
            self.detail_path.setText("路径：—")
            self.detail_class.setText("类别：—")
            self.detail_state.setText("状态：—")
            self.detail_count.setText("标注对象：—")
            return

        image = Path(path)
        images = self._vm.images()
        flags = self._vm.annotated_flags()
        annotated = False
        if image in images:
            position = images.index(image)
            if position < len(flags):
                annotated = flags[position]

        if self._vm.split_layout() == "classify":
            count_text = "不适用（分类任务，按类别判定）"
        else:
            label_dir = self._vm.label_dir()
            count = 0
            if label_dir is not None:
                label = AnnotationService.label_path_for(image, label_dir)
                if label.is_file():
                    count = len(AnnotationService.load_yolo(label).items)
            count_text = f"{count} 个"

        self.detail_name.setText(image.name)
        self.detail_path.setText(f"路径：{image.parent}")
        self.detail_class.setText(f"类别：{self._vm.image_class(image)}")
        self.detail_state.setText("状态：已标注" if annotated else "状态：未标注")
        self.detail_count.setText(f"标注对象：{count_text}")

    # -----------------------------------------------------------
    # 质检
    # -----------------------------------------------------------
    def _on_quality(self, result: dict) -> None:
        self.quality_list.clear()
        if not result:
            return
        duplicates = result.get("duplicates") or []
        blurry = result.get("blurry") or []
        exposure = result.get("exposure") or []
        labels = result.get("labels") or {}

        rows = [
            f"重复图片：{len(duplicates)} 组",
            f"模糊图片：{len(blurry)} 张",
            f"曝光异常：{len(exposure)} 张",
            f"空标签：{len(labels.get('empty', []) or [])}",
            f"越界/非法：{len(labels.get('out_of_range', []) or [])}",
            f"缺失标签：{len(labels.get('missing', []) or [])}",
            f"孤立标签：{len(labels.get('orphan_label', []) or [])}",
        ]
        for text in rows:
            self.quality_list.addItem(QListWidgetItem(text))

        for group in duplicates[:3]:
            names = "、".join(Path(p).name for p in group[:3])
            self.quality_list.addItem(QListWidgetItem(f"  重复组：{names}"))
        for path, score in blurry[:3]:
            self.quality_list.addItem(
                QListWidgetItem(f"  模糊：{Path(path).name}（清晰度 {score:.0f}）")
            )
        for path, value in exposure[:3]:
            self.quality_list.addItem(
                QListWidgetItem(f"  曝光：{Path(path).name}（均值 {value:.0f}）")
            )
        self.quality_hint.setText("质检完成。")
