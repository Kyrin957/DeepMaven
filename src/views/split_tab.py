"""数据拆分页：划分比例配置、拆分预览。

布局参照 Halcon DLT 的拆分视图：
    左侧：拆分设置 / 拆分概览（环形图 + 图例）
    右侧：类别分布预览（条形图）、拆分产物

数据增强只在训练页做（在线增强），本页不提供离线增强。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
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
    LineEdit,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
)

from src.services.dataset_service import DatasetService
from src.utils.constants import SPLIT_COLORS, SPLIT_LABELS, UNLABELED_LABEL
from src.viewmodels.category_vm import CategoryViewModel
from src.viewmodels.dataset_vm import DatasetViewModel
from src.views.data_widgets import side_column
from src.views.ui import SafeCompactSpinBox, SafeSpinBox
from src.views.ui import tokens as T
from src.views.widgets import BarChart, LegendList, PieChart

_COLOR_UNLABELED = "#8A8A8A"
# 异常检测的类别显示名（对应 ClassDef.kind）
_KIND_LABELS = {"normal": "良好图像", "abnormal": "异常图像"}


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
        # 异常检测的按类别分配（表格数据源）
        self._anomaly_totals: dict[str, int] = {}
        self._anomaly_plan: dict[str, dict[str, int]] = {}
        self._build_ui()
        self._bind()
        self.refresh()

    # -----------------------------------------------------------
    # UI
    # -----------------------------------------------------------
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(T.SPACE_XL, T.SPACE_LG, T.SPACE_XL, T.SPACE_LG)
        root.setSpacing(T.SPACE_LG)

        root.addWidget(self._build_side())
        root.addLayout(self._build_main(), 1)

    def _build_side(self) -> QWidget:
        # 拆分列表：一个项目可以有多套拆分（横向比较不同划分）
        self.list_card = CardWidget(self)
        list_layout = QVBoxLayout(self.list_card)
        list_layout.setContentsMargins(T.CARD_PAD_H, T.CARD_PAD_V, T.CARD_PAD_H, T.CARD_PAD_V)
        list_layout.setSpacing(T.SPACE_SM)
        list_layout.addWidget(StrongBodyLabel("拆分列表", self.list_card))
        self.split_list = QListWidget(self.list_card)
        self.split_list.setMinimumHeight(120)
        self.split_list.currentRowChanged.connect(self._on_split_selected)
        list_layout.addWidget(self.split_list)

        tools = QHBoxLayout()
        tools.setSpacing(T.SPACE_SM)
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
        layout.setContentsMargins(T.CARD_PAD_H, T.CARD_PAD_V, T.CARD_PAD_H, T.CARD_PAD_V)
        layout.setSpacing(T.SPACE_SM)
        layout.addWidget(StrongBodyLabel("拆分设置", self.setting_card))

        name_row = QHBoxLayout()
        name_row.addWidget(CaptionLabel("拆分名称", self.setting_card))
        self.name_edit = LineEdit(self.setting_card)
        self.name_edit.setText("ExampleSplit")
        name_row.addWidget(self.name_edit, 1)
        layout.addLayout(name_row)

        self.ratio_spins: dict[str, SafeSpinBox] = {}
        self.ratio_rows: dict[str, QWidget] = {}
        for key in ("train", "val", "test"):
            row_widget = QWidget(self.setting_card)
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(T.SPACE_MD)
            row.addWidget(CaptionLabel(SPLIT_LABELS[key], row_widget))
            spin = SafeSpinBox(row_widget)
            spin.setRange(0, 100)
            spin.setSuffix(" %")
            spin.setValue({"train": 70, "val": 20, "test": 10}[key])
            spin.setFixedWidth(
                T.field_width(spin, T.STEPPER_CHARS_NARROW)
            )
            spin.valueChanged.connect(lambda _v: self._on_ratio_changed())
            row.addWidget(spin)
            row.addStretch(1)
            layout.addWidget(row_widget)
            self.ratio_spins[key] = spin
            self.ratio_rows[key] = row_widget

        seed_row = QHBoxLayout()
        seed_row.setSpacing(T.SPACE_MD)
        seed_row.addWidget(CaptionLabel("随机种子", self.setting_card))
        self.seed_spin = SafeSpinBox(self.setting_card)
        self.seed_spin.setRange(0, 99999)
        self.seed_spin.setFixedWidth(
            T.field_width(self.seed_spin, T.STEPPER_CHARS)
        )
        self.seed_spin.valueChanged.connect(lambda _v: self._on_seed_changed())
        seed_row.addWidget(self.seed_spin)
        seed_row.addStretch(1)
        layout.addLayout(seed_row)

        self.stratified_check = QCheckBox("按类别分层抽样（推荐）", self.setting_card)
        self.stratified_check.setChecked(True)
        self.stratified_check.stateChanged.connect(
            lambda _s: self._on_stratified_changed()
        )
        layout.addWidget(self.stratified_check)

        # 异常检测：按类别分配（良好 / 异常 × 训练 / 验证 / 测试）
        self.anomaly_card = self._build_anomaly_card()
        layout.addWidget(self.anomaly_card)

        button_row = QHBoxLayout()
        button_row.setSpacing(T.SPACE_SM)
        self.preview_btn = PushButton("刷新预览", self.setting_card)
        self.apply_btn = PrimaryPushButton("执行拆分", self.setting_card)
        button_row.addWidget(self.preview_btn)
        button_row.addWidget(self.apply_btn, 1)
        layout.addLayout(button_row)

        self.overview_card = CardWidget(self)
        overview_layout = QVBoxLayout(self.overview_card)
        overview_layout.setContentsMargins(T.CARD_PAD_H, T.CARD_PAD_V, T.CARD_PAD_H, T.CARD_PAD_V)
        overview_layout.setSpacing(T.SPACE_SM)
        overview_layout.addWidget(StrongBodyLabel("拆分概览", self.overview_card))
        self.pie = PieChart(self.overview_card)
        overview_layout.addWidget(self.pie)
        self.legend = LegendList(self.overview_card)
        overview_layout.addWidget(self.legend)

        return side_column(
            self.list_card, self.setting_card, self.overview_card, width=300
        )

    # -----------------------------------------------------------
    # 异常检测：按类别分配表（参照 DLT 的创建拆分）
    # -----------------------------------------------------------
    def _build_anomaly_card(self) -> QWidget:
        """良好 / 异常 × 训练 / 验证 / 测试 的数量与百分比。

        异常图的「训练」格禁用：异常检测只用正常样本训练，异常图进验证 /
        测试。约束用控件可用性表达，不写说明文字。

        窄侧栏（~270px）塞不下「4 个数值列并排」：行内步进器光按钮就占 71px/
        个，4 列要 300px 以上，硬塞的结果是数值被按钮挤没（历史故障）。这里
        按类别分块竖排，数值框改用**紧凑步进器**（省 54px/个）并按字体度量
        定宽（``tokens.field_width()``），字体 / 缩放变化时数字仍然完整可见。
        """
        holder = QWidget(self.setting_card)
        grid = QGridLayout(holder)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(T.SPACE_MD)
        grid.setVerticalSpacing(T.SPACE_SM)

        # 列头：数量 / 百分比（两类共用，避免每类重复一行表头）
        grid.addWidget(CaptionLabel("数量", holder), 0, 1)
        grid.addWidget(CaptionLabel("百分比", holder), 0, 2)

        self.anomaly_titles: dict[str, StrongBodyLabel] = {}
        self.anomaly_spins: dict[tuple, SafeCompactSpinBox] = {}
        row = 1
        for kind in ("normal", "abnormal"):
            title = StrongBodyLabel(_KIND_LABELS[kind], holder)
            grid.addWidget(title, row, 0, 1, 3)
            self.anomaly_titles[kind] = title
            row += 1
            for sub in ("train", "val", "test"):
                editable = not (kind == "abnormal" and sub == "train")
                grid.addWidget(CaptionLabel(SPLIT_LABELS[sub], holder), row, 0)
                count = SafeCompactSpinBox(holder)
                count.setRange(0, 999999)
                count.setFixedWidth(
                    T.field_width(count, T.STEPPER_CHARS_NARROW)
                )
                count.valueChanged.connect(
                    lambda _v, k=kind, s=sub: self._on_anomaly_edited(k, s, "count")
                )
                grid.addWidget(count, row, 1)
                percent = SafeCompactSpinBox(holder)
                percent.setRange(0, 100)
                percent.setSuffix(" %")
                percent.setFixedWidth(
                    T.field_width(percent, T.STEPPER_CHARS_NARROW)
                )
                percent.valueChanged.connect(
                    lambda _v, k=kind, s=sub: self._on_anomaly_edited(
                        k, s, "percent"
                    )
                )
                grid.addWidget(percent, row, 2)
                count.setEnabled(editable)
                percent.setEnabled(editable)
                self.anomaly_spins[(kind, sub, "count")] = count
                self.anomaly_spins[(kind, sub, "percent")] = percent
                row += 1

        grid.setColumnStretch(3, 1)          # 右列留白：数值框左对齐，不拉伸
        self.anomaly_hint = CaptionLabel("", holder)
        grid.addWidget(self.anomaly_hint, row, 0, 1, 4)
        return holder

    def _refresh_anomaly_card(self) -> None:
        """按任务显示：异常检测显示类别分配表，其余任务显示比例行。"""
        is_anomaly = self._vm.is_anomaly()
        for row in self.ratio_rows.values():
            row.setVisible(not is_anomaly)
        self.stratified_check.setVisible(not is_anomaly)
        self.anomaly_card.setVisible(is_anomaly)
        if not is_anomaly:
            return
        self._anomaly_totals = self._vm.anomaly_totals()
        self._anomaly_plan = self._vm.anomaly_allocation()
        for kind in ("normal", "abnormal"):
            self.anomaly_titles[kind].setText(
                f"{_KIND_LABELS[kind]} {int(self._anomaly_totals.get(kind, 0))}"
            )
        self.anomaly_hint.setText(
            f"已标注 / 全部：{int(self._anomaly_totals.get('labeled', 0))} / "
            f"{int(self._anomaly_totals.get('all', 0))}"
        )
        self._apply_anomaly_plan()

    def _apply_anomaly_plan(self) -> None:
        """把当前分配写回表格（期间屏蔽信号）。"""
        self._syncing = True
        try:
            for kind in ("normal", "abnormal"):
                total = int(self._anomaly_totals.get(kind, 0))
                rows = self._anomaly_plan.get(kind) or {}
                for sub in ("train", "val", "test"):
                    value = int(rows.get(sub, 0))
                    self.anomaly_spins[(kind, sub, "count")].setValue(value)
                    self.anomaly_spins[(kind, sub, "percent")].setValue(
                        int(round(value * 100 / total)) if total else 0
                    )
        finally:
            self._syncing = False

    def _rebalance(self, kind: str, changed: str, value: int) -> dict[str, int]:
        """改一格后重排同类其余行，使行和恒等于该类图片数。"""
        total = int(self._anomaly_totals.get(kind, 0))
        if kind == "abnormal" and changed == "train":
            value = 0
        value = max(0, min(total, int(value)))
        others = [sub for sub in ("train", "val", "test") if sub != changed]
        if kind == "abnormal":
            others = [sub for sub in others if sub != "train"]
        current = self._anomaly_plan.get(kind) or {}
        weights = {sub: max(0, int(current.get(sub, 0))) for sub in others}
        if not any(weights.values()):
            weights = {sub: 1 for sub in others}
        sizes = DatasetService._proportional_sizes(total - value, weights)
        rows = {sub: 0 for sub in ("train", "val", "test")}
        rows[changed] = value
        rows.update({sub: int(sizes.get(sub, 0)) for sub in others})
        return rows

    def _on_anomaly_edited(self, kind: str, sub: str, mode: str) -> None:
        """改数量或百分比：同类其余行按比例吸收差额后写回拆分。"""
        if self._syncing:
            return
        total = int(self._anomaly_totals.get(kind, 0))
        if mode == "count":
            value = int(self.anomaly_spins[(kind, sub, "count")].value())
        else:
            percent = int(self.anomaly_spins[(kind, sub, "percent")].value())
            value = int(round(total * percent / 100))
        self._anomaly_plan[kind] = self._rebalance(kind, sub, value)
        self._apply_anomaly_plan()
        self._vm.set_anomaly_allocation(self._anomaly_plan)

    def _build_main(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(T.SPACE_ML)

        card = CardWidget(self)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(T.SPACE_XL, T.SPACE_ML, T.SPACE_XL, T.SPACE_ML)
        layout.setSpacing(T.SPACE_ML)
        self.title = StrongBodyLabel("数据拆分", card)
        layout.addWidget(self.title)
        layout.addStretch(1)
        self.reload_btn = PushButton("刷新", card)
        layout.addWidget(self.reload_btn)
        column.addWidget(card)

        class_card = CardWidget(self)
        class_layout = QVBoxLayout(class_card)
        class_layout.setContentsMargins(T.SPACE_XL, T.SPACE_LG, T.SPACE_XL, T.SPACE_LG)
        class_layout.setSpacing(T.SPACE_SM)
        class_layout.addWidget(StrongBodyLabel("类别分布预览", class_card))
        self.class_hint = CaptionLabel("", class_card)
        class_layout.addWidget(self.class_hint)
        self.bar_chart = BarChart(class_card)
        class_layout.addWidget(self.bar_chart)
        column.addWidget(class_card)

        output_card = CardWidget(self)
        output_layout = QVBoxLayout(output_card)
        output_layout.setContentsMargins(T.SPACE_XL, T.SPACE_LG, T.SPACE_XL, T.SPACE_LG)
        output_layout.setSpacing(T.SPACE_SM)
        output_layout.addWidget(StrongBodyLabel("拆分产物", output_card))
        self.output_label = BodyLabel("—", output_card)
        self.output_label.setWordWrap(True)
        output_layout.addWidget(self.output_label)
        self.yaml_label = BodyLabel("—", output_card)
        self.yaml_label.setWordWrap(True)
        output_layout.addWidget(self.yaml_label)
        column.addWidget(output_card)
        return column

    # -----------------------------------------------------------
    # 绑定
    # -----------------------------------------------------------
    def _bind(self) -> None:
        self.reload_btn.clicked.connect(self.refresh)
        self.preview_btn.clicked.connect(self._on_preview_clicked)
        self.apply_btn.clicked.connect(self._on_apply)
        self.name_edit.editingFinished.connect(self._on_name_changed)

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
            # 分类 / 异常检测以目录本体作为数据集，不存在 data.yaml
            yaml_path = str(dataset.data_yaml or "")
            has_yaml = bool(yaml_path) and not Path(yaml_path).is_dir()
            self.yaml_label.setText(
                f"数据集配置：{yaml_path}" if has_yaml else "数据集配置：—"
            )
        else:
            self.output_label.setText("输出目录：—")
            self.yaml_label.setText("数据集配置：—")
        self.name_edit.setText(self._vm.split_name())
        self._syncing = False

        self._refresh_splits()
        self._apply_lock_state()
        self._refresh_preview()
        self._refresh_anomaly_card()

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
