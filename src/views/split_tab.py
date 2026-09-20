"""数据拆分页：划分比例配置、拆分预览与数据增强。

布局参照 Halcon DLT 的拆分视图：
    左侧：拆分设置 / 拆分概览（环形图 + 图例）
    右侧：类别分布预览（条形图）、拆分产物、数据增强
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    LineEdit,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
)

from src.services.augment_service import AugmentConfig, AugmentService
from src.utils.constants import SPLIT_COLORS, SPLIT_LABELS, UNLABELED_LABEL
from src.viewmodels.category_vm import CategoryViewModel
from src.viewmodels.dataset_vm import DatasetViewModel
from src.views.data_widgets import side_column
from src.views.widgets import BarChart, LegendList, PieChart

_COLOR_UNLABELED = "#8A8A8A"


class SplitTab(QWidget):
    """数据拆分页。"""

    def __init__(
        self,
        dataset_vm: DatasetViewModel,
        category_vm: CategoryViewModel | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._vm = dataset_vm
        self._category_vm = category_vm
        self._syncing = False
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
        # 拆分列表：一个项目可以有多套拆分（横向比较不同划分）
        self.list_card = CardWidget(self)
        list_layout = QVBoxLayout(self.list_card)
        list_layout.setContentsMargins(14, 12, 14, 12)
        list_layout.setSpacing(6)
        list_layout.addWidget(StrongBodyLabel("拆分列表", self.list_card))
        self.split_list = QListWidget(self.list_card)
        self.split_list.setMinimumHeight(120)
        self.split_list.currentRowChanged.connect(self._on_split_selected)
        list_layout.addWidget(self.split_list)

        tools = QHBoxLayout()
        tools.setSpacing(6)
        self.add_split_btn = PushButton("新建", self.list_card)
        self.copy_split_btn = PushButton("复制", self.list_card)
        self.del_split_btn = PushButton("删除", self.list_card)
        self.add_split_btn.clicked.connect(self._on_add_split)
        self.copy_split_btn.clicked.connect(self._on_duplicate_split)
        self.del_split_btn.clicked.connect(self._on_delete_split)
        for button in (self.add_split_btn, self.copy_split_btn, self.del_split_btn):
            tools.addWidget(button)
        list_layout.addLayout(tools)

        self.setting_card = CardWidget(self)
        layout = QVBoxLayout(self.setting_card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        layout.addWidget(StrongBodyLabel("拆分设置", self.setting_card))

        name_row = QHBoxLayout()
        name_row.addWidget(CaptionLabel("拆分名称", self.setting_card))
        self.name_edit = LineEdit(self.setting_card)
        self.name_edit.setText("ExampleSplit")
        name_row.addWidget(self.name_edit, 1)
        layout.addLayout(name_row)

        self.ratio_spins: dict[str, QSpinBox] = {}
        for key in ("train", "val", "test"):
            row = QHBoxLayout()
            row.addWidget(CaptionLabel(SPLIT_LABELS[key], self.setting_card))
            spin = QSpinBox(self.setting_card)
            spin.setRange(0, 100)
            spin.setSuffix(" %")
            spin.setValue({"train": 70, "val": 20, "test": 10}[key])
            spin.valueChanged.connect(lambda _v: self._on_ratio_changed())
            row.addWidget(spin, 1)
            layout.addLayout(row)
            self.ratio_spins[key] = spin

        seed_row = QHBoxLayout()
        seed_row.addWidget(CaptionLabel("随机种子", self.setting_card))
        self.seed_spin = QSpinBox(self.setting_card)
        self.seed_spin.setRange(0, 99999)
        self.seed_spin.valueChanged.connect(lambda _v: self._on_seed_changed())
        seed_row.addWidget(self.seed_spin, 1)
        layout.addLayout(seed_row)

        self.stratified_check = QCheckBox("按类别分层抽样（推荐）", self.setting_card)
        self.stratified_check.setChecked(True)
        self.stratified_check.stateChanged.connect(
            lambda _s: self._on_stratified_changed()
        )
        layout.addWidget(self.stratified_check)

        button_row = QHBoxLayout()
        button_row.setSpacing(6)
        self.preview_btn = PushButton("刷新预览", self.setting_card)
        self.apply_btn = PrimaryPushButton("执行拆分", self.setting_card)
        button_row.addWidget(self.preview_btn)
        button_row.addWidget(self.apply_btn, 1)
        layout.addLayout(button_row)

        self.overview_card = CardWidget(self)
        overview_layout = QVBoxLayout(self.overview_card)
        overview_layout.setContentsMargins(14, 12, 14, 12)
        overview_layout.setSpacing(6)
        overview_layout.addWidget(StrongBodyLabel("拆分概览", self.overview_card))
        self.pie = PieChart(self.overview_card)
        overview_layout.addWidget(self.pie)
        self.legend = LegendList(self.overview_card)
        overview_layout.addWidget(self.legend)

        return side_column(
            self.list_card, self.setting_card, self.overview_card, width=300
        )

    def _build_main(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(10)

        card = CardWidget(self)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(10)
        self.title = StrongBodyLabel("数据拆分", card)
        layout.addWidget(self.title)
        layout.addStretch(1)
        self.reload_btn = PushButton("刷新", card)
        layout.addWidget(self.reload_btn)
        column.addWidget(card)

        class_card = CardWidget(self)
        class_layout = QVBoxLayout(class_card)
        class_layout.setContentsMargins(16, 12, 16, 12)
        class_layout.setSpacing(6)
        class_layout.addWidget(StrongBodyLabel("类别分布预览", class_card))
        self.class_hint = CaptionLabel("", class_card)
        class_layout.addWidget(self.class_hint)
        self.bar_chart = BarChart(class_card)
        class_layout.addWidget(self.bar_chart)
        column.addWidget(class_card)

        output_card = CardWidget(self)
        output_layout = QVBoxLayout(output_card)
        output_layout.setContentsMargins(16, 12, 16, 12)
        output_layout.setSpacing(6)
        output_layout.addWidget(StrongBodyLabel("拆分产物", output_card))
        self.output_label = BodyLabel("—", output_card)
        self.output_label.setWordWrap(True)
        output_layout.addWidget(self.output_label)
        self.yaml_label = BodyLabel("—", output_card)
        self.yaml_label.setWordWrap(True)
        output_layout.addWidget(self.yaml_label)
        column.addWidget(output_card)

        column.addWidget(self._build_augment_card(), 1)
        return column

    def _build_augment_card(self) -> CardWidget:
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)
        layout.addWidget(StrongBodyLabel("数据增强", card))

        self.aug_hint = CaptionLabel("", card)
        layout.addWidget(self.aug_hint)

        grid = QHBoxLayout()
        grid.setSpacing(14)
        self.aug_spins: dict[str, QDoubleSpinBox] = {}
        specs = [
            ("hflip", "水平翻转概率", 0.5, 0.0, 1.0, 0.05),
            ("vflip", "垂直翻转概率", 0.0, 0.0, 1.0, 0.05),
            ("rotate", "旋转角度上限", 15.0, 0.0, 180.0, 5.0),
            ("scale", "缩放幅度", 0.1, 0.0, 1.0, 0.05),
            ("brightness", "亮度幅度", 0.2, 0.0, 1.0, 0.05),
            ("contrast", "对比度幅度", 0.2, 0.0, 1.0, 0.05),
            ("noise", "高斯噪声概率", 0.0, 0.0, 1.0, 0.05),
            ("blur", "高斯模糊概率", 0.0, 0.0, 1.0, 0.05),
        ]
        left = QVBoxLayout()
        right = QVBoxLayout()
        for index, (key, text, value, low, high, step) in enumerate(specs):
            row = QHBoxLayout()
            row.addWidget(CaptionLabel(text, card))
            spin = QDoubleSpinBox(card)
            spin.setRange(low, high)
            spin.setSingleStep(step)
            spin.setDecimals(2)
            spin.setValue(value)
            row.addWidget(spin, 1)
            (left if index % 2 == 0 else right).addLayout(row)
            self.aug_spins[key] = spin
        grid.addLayout(left, 1)
        grid.addLayout(right, 1)

        copies_row = QHBoxLayout()
        copies_row.addWidget(CaptionLabel("每张生成份数", card))
        self.copies_spin = QSpinBox(card)
        self.copies_spin.setRange(1, 20)
        self.copies_spin.setValue(3)
        copies_row.addWidget(self.copies_spin, 1)
        grid.addLayout(copies_row, 1)
        layout.addLayout(grid)

        preview_row = QHBoxLayout()
        preview_row.setSpacing(8)
        self.preview_labels: list[QLabel] = []
        for _ in range(4):
            label = QLabel(card)
            label.setFixedSize(96, 96)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet("background:#1B1B1B; border-radius:4px;")
            preview_row.addWidget(label)
            self.preview_labels.append(label)
        preview_row.addStretch(1)
        self.aug_preview_btn = PushButton("预览增强效果", card)
        self.aug_apply_btn = PrimaryPushButton("生成增强样本", card)
        preview_row.addWidget(self.aug_preview_btn)
        preview_row.addWidget(self.aug_apply_btn)
        layout.addLayout(preview_row)
        return card

    # -----------------------------------------------------------
    # 绑定
    # -----------------------------------------------------------
    def _bind(self) -> None:
        self.reload_btn.clicked.connect(self.refresh)
        self.preview_btn.clicked.connect(self._on_preview_clicked)
        self.apply_btn.clicked.connect(self._on_apply)
        self.name_edit.editingFinished.connect(self._on_name_changed)
        self.aug_preview_btn.clicked.connect(self._on_aug_preview)
        self.aug_apply_btn.clicked.connect(self._on_aug_apply)

        self._vm.datasetChanged.connect(lambda _d: self.refresh())
        self._vm.taskFinished.connect(lambda _t: self.refresh())
        self._vm.splitsChanged.connect(lambda _rows: self.refresh())

    # -----------------------------------------------------------
    # 刷新
    # -----------------------------------------------------------
    def refresh(self) -> None:
        dataset = self._vm.dataset
        self.title.setText(f"数据拆分 · {dataset.name}" if dataset else "数据拆分")

        self._syncing = True
        if dataset is not None:
            self.ratio_spins["train"].setValue(int(round(dataset.split_train * 100)))
            self.ratio_spins["val"].setValue(int(round(dataset.split_val * 100)))
            self.ratio_spins["test"].setValue(int(round(dataset.split_test * 100)))
            self.seed_spin.setValue(int(dataset.seed))
            self.stratified_check.setChecked(bool(dataset.stratified))
            self.output_label.setText(f"输出目录：{dataset.output_path or '—'}")
            self.yaml_label.setText(f"数据集配置：{dataset.data_yaml or '—'}")
        else:
            self.output_label.setText("输出目录：—")
            self.yaml_label.setText("数据集配置：—")
        self.name_edit.setText(self._vm.split_name())
        self._syncing = False

        self._refresh_splits()
        self._apply_lock_state()
        self._refresh_preview()

    # -----------------------------------------------------------
    # 拆分列表（一个项目可有多套）
    # -----------------------------------------------------------
    def _refresh_splits(self) -> None:
        """刷新拆分列表并把当前拆分置为选中。"""
        rows = self._vm.splits_ready()
        active = self._vm.active_split()
        active_id = active.split_id if active is not None else 0
        self._syncing = True
        try:
            self.split_list.clear()
            for row in rows:
                parts = str(row["label"]).split(" · ")
                text = row["name"] + (f"  {parts[1]}" if len(parts) > 1 else "")
                if row["ready"]:
                    text += f"  {row['total']} 张"
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, int(row["id"]))
                self.split_list.addItem(item)
                if int(row["id"]) == active_id:
                    self.split_list.setCurrentRow(self.split_list.count() - 1)
        finally:
            self._syncing = False
        self.del_split_btn.setEnabled(len(rows) > 1)

    def _on_split_selected(self, row: int) -> None:
        if self._syncing or row < 0:
            return
        item = self.split_list.item(row)
        if item is None:
            return
        split_id = int(item.data(Qt.ItemDataRole.UserRole))
        active = self._vm.active_split()
        if active is not None and active.split_id == split_id:
            return
        self._vm.select_split(split_id)
        self.refresh()

    def _on_add_split(self) -> None:
        self._vm.add_split()
        self.refresh()

    def _on_duplicate_split(self) -> None:
        active = self._vm.active_split()
        if active is not None:
            self._vm.duplicate_split(active.split_id)
            self.refresh()

    def _on_delete_split(self) -> None:
        active = self._vm.active_split()
        if active is None:
            return
        box = MessageBox(
            "删除拆分",
            f"确定要删除拆分「{active.name}」吗？\n\n"
            "磁盘上的产物目录不会被删除。",
            self.window(),
        )
        box.yesButton.setText("删除")
        box.cancelButton.setText("取消")
        if box.exec():
            self._vm.remove_split(active.split_id)
            self.refresh()

    def _apply_lock_state(self) -> None:
        """已被训练使用的拆分：比例只读。"""
        locked = self._vm.split_locked()
        for spin in self.ratio_spins.values():
            spin.setEnabled(not locked)
        self.seed_spin.setEnabled(not locked)
        self.stratified_check.setEnabled(not locked)

    def _on_preview_clicked(self) -> None:
        self._refresh_preview()
        preview = self._vm.preview_split()
        total = int(preview.get("total", 0)) if preview else 0
        self._vm.notify(f"共 {total} 张待划分" if total else "没有可划分的图片")

    def _on_name_changed(self) -> None:
        if self._syncing:
            return
        self._vm.set_split_name(self.name_edit.text())
        resolved = self._vm.split_name()
        self.name_edit.setText(resolved)
        self._vm.notify(f"拆分名称：{resolved}")

    def _refresh_preview(self) -> None:
        preview = self._vm.preview_split()
        total = int(preview.get("total", 0)) if preview else 0
        if not preview or not total:
            self.pie.set_data([], "")
            self.legend.set_data([])
            self.bar_chart.set_data([])
            self.class_hint.setText("暂无数据")
            return

        subsets = preview.get("subsets") or {}
        segments = []
        legend = []
        for key in ("train", "val", "test"):
            count = int((subsets.get(key) or {}).get("count", 0))
            color = SPLIT_COLORS[key]
            segments.append((SPLIT_LABELS[key], count, color))
            legend.append((
                color, SPLIT_LABELS[key],
                f"{count}  ({count * 100 / total:.0f}%)",
            ))
        self.pie.set_data(segments, str(total))
        self.legend.set_data(legend)

        per_class = preview.get("per_class") or {}
        rows = []
        maximum = 0
        for name, info in per_class.items():
            maximum = max(maximum, int(info.get("total", 0)))
        for name, info in sorted(
            per_class.items(), key=lambda kv: -int(kv[1].get("total", 0))
        ):
            count = int(info.get("total", 0))
            color = _COLOR_UNLABELED if name == UNLABELED_LABEL else "#0F6CBD"
            rows.append((name, count, maximum or 1, color))
        self.bar_chart.set_data(rows)
        self.class_hint.setText(f"共 {len(per_class)} 个类别 · {total} 张")

    # -----------------------------------------------------------
    # 参数
    # -----------------------------------------------------------
    def _on_ratio_changed(self) -> None:
        if self._syncing:
            return
        values = [self.ratio_spins[k].value() for k in ("train", "val", "test")]
        total = sum(values)
        if total <= 0:
            return
        self._vm.set_split(
            values[0] / total, values[1] / total, values[2] / total, quiet=True
        )
        self._refresh_preview()

    def _on_seed_changed(self) -> None:
        if self._syncing:
            return
        self._vm.set_seed(self.seed_spin.value())
        self._refresh_preview()

    def _on_stratified_changed(self) -> None:
        if self._syncing:
            return
        self._vm.set_stratified(self.stratified_check.isChecked())
        self._refresh_preview()

    def _on_apply(self) -> None:
        self._vm.notify(
            f"正在按 {self.ratio_spins['train'].value()}/"
            f"{self.ratio_spins['val'].value()}/"
            f"{self.ratio_spins['test'].value()} 执行拆分…"
        )
        self._vm.apply_split()

    # -----------------------------------------------------------
    # 数据增强
    # -----------------------------------------------------------
    def _augment_config(self) -> AugmentConfig:
        return AugmentConfig(
            hflip=self.aug_spins["hflip"].value(),
            vflip=self.aug_spins["vflip"].value(),
            rotate=self.aug_spins["rotate"].value(),
            scale=self.aug_spins["scale"].value(),
            brightness=self.aug_spins["brightness"].value(),
            contrast=self.aug_spins["contrast"].value(),
            noise=self.aug_spins["noise"].value(),
            blur=self.aug_spins["blur"].value(),
            copies=self.copies_spin.value(),
        )

    def _on_aug_preview(self) -> None:
        if not AugmentService.is_available():
            self.aug_hint.setText("未安装 Albumentations")
            self._vm.notify("未安装 Albumentations，无法预览增强", "error")
            return
        arrays = self._vm.augment_preview(self._augment_config(), count=4)
        if not arrays:
            self.aug_hint.setText("没有已标注的图片")
            self._vm.notify("没有已标注的图片，无法预览增强", "warning")
            return
        for index, label in enumerate(self.preview_labels):
            if index < len(arrays):
                label.setPixmap(self._to_pixmap(arrays[index], 92))
            else:
                label.clear()
        self.aug_hint.setText(f"已生成 {len(arrays)} 张预览")
        self._vm.notify(f"已生成 {len(arrays)} 张增强预览")

    def _on_aug_apply(self) -> None:
        if not AugmentService.is_available():
            self.aug_hint.setText("未安装 Albumentations")
            self._vm.notify("未安装 Albumentations，无法执行增强", "error")
            return
        if self._vm.split_layout() == "classify":
            self.aug_hint.setText("分类任务不支持离线增强")
            self._vm.notify("分类任务不支持离线增强", "warning")
            return
        self._vm.augment_apply(self._augment_config())

    @staticmethod
    def _to_pixmap(array, size: int) -> QPixmap:
        height, width, channels = array.shape
        image = QImage(
            array.data, width, height, channels * width,
            QImage.Format.Format_RGB888,
        )
        return QPixmap.fromImage(image.copy()).scaled(
            size, size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
