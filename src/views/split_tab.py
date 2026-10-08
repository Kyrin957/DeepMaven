"""数据拆分页：划分比例配置、拆分预览。

布局参照 Halcon DLT 的拆分视图，按「管理 / 信息」分区、卡片式承载：
    左侧：拆分管理——拆分列表（新建 / 复制 / 删除）+ 拆分设置 + 执行拆分
    右侧：信息区——拆分概览（环形图 + 汇总表）/ 类别分布预览（堆叠条）

数据增强只在训练页做（在线增强），本页不提供离线增强。
"""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    MessageBox,
    PushButton,
    StrongBodyLabel,
)

from src.services.dataset_service import DatasetService
from src.utils.constants import SPLIT_COLORS, SPLIT_LABELS, UNLABELED_LABEL
from src.viewmodels.category_vm import CategoryViewModel
from src.viewmodels.dataset_vm import DatasetViewModel
from src.views.data_widgets import side_column
from src.views.dialogs import (
    CREATE_APPLY,
    MODE_CREATE,
    MODE_EDIT,
    SAVE_APPLY,
    SplitEditDialog,
)
from src.views.ui import SafeCompactSpinBox
from src.views.ui import tokens as T
from src.views.ui.icons import icon
from src.views.widgets import PieChart, StackedBarChart

# 异常检测的类别显示名（对应 ClassDef.kind）
_KIND_LABELS = {"normal": "良好图像", "abnormal": "异常图像"}

# 拆分列表行数据：锁定原因（"" / running / used）——被训练引用的拆分
# 在行右端绘制锁图标（见 _SplitListDelegate），按钮同步禁用
_LOCKED_ROLE = Qt.ItemDataRole.UserRole + 1


class _SplitListDelegate(QStyledItemDelegate):
    """拆分列表项：被训练引用的拆分在行右端追加锁图标。"""

    ICON_SIZE = 16

    def paint(self, painter, option, index) -> None:  # noqa: N802 - Qt 命名
        super().paint(painter, option, index)
        if not index.data(_LOCKED_ROLE):
            return
        rect = QRect(
            option.rect.right() - self.ICON_SIZE - 6,
            option.rect.center().y() - self.ICON_SIZE // 2,
            self.ICON_SIZE,
            self.ICON_SIZE,
        )
        icon("locked").paint(painter, rect)


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
        root.setContentsMargins(T.PAGE_PAD_H, T.PAGE_PAD_V, T.PAGE_PAD_H, T.PAGE_PAD_V)
        root.setSpacing(T.SPACE_LG)

        root.addWidget(self._build_side())
        root.addWidget(self._build_main(), 1)

    def _build_side(self) -> QWidget:
        # 拆分列表：一个项目可以有多套拆分（横向比较不同划分）
        self.list_card = CardWidget(self)
        list_layout = QVBoxLayout(self.list_card)
        list_layout.setContentsMargins(T.CARD_PAD_H, T.CARD_PAD_V, T.CARD_PAD_H, T.CARD_PAD_V)
        list_layout.setSpacing(T.SPACE_SM)
        list_layout.addWidget(StrongBodyLabel("拆分列表", self.list_card))
        self.split_list = QListWidget(self.list_card)
        self.split_list.setMinimumHeight(120)
        self.split_list.setItemDelegate(_SplitListDelegate(self.split_list))
        self.split_list.currentRowChanged.connect(self._on_split_selected)
        list_layout.addWidget(self.split_list)

        tools = QHBoxLayout()
        tools.setSpacing(T.SPACE_SM)
        self.add_split_btn = PushButton("新建", self.list_card)
        self.edit_split_btn = PushButton("编辑", self.list_card)
        self.copy_split_btn = PushButton("复制", self.list_card)
        self.del_split_btn = PushButton("删除", self.list_card)
        self.add_split_btn.clicked.connect(self._on_add_split)
        self.edit_split_btn.clicked.connect(self._on_edit_split)
        self.copy_split_btn.clicked.connect(self._on_duplicate_split)
        self.del_split_btn.clicked.connect(self._on_delete_split)
        for button in (
            self.add_split_btn, self.edit_split_btn,
            self.copy_split_btn, self.del_split_btn,
        ):
            tools.addWidget(button)
        list_layout.addLayout(tools)

        # 异常检测：按类别分配（良好 / 异常 × 训练 / 验证 / 测试）——
        # 参数编辑自成一张卡、仅异常任务可见；拆分信息（名称 / 比例 / 张数）
        # 都在列表行里，不再另设「当前拆分」信息卡
        self.anomaly_card = CardWidget(self)
        anomaly_layout = QVBoxLayout(self.anomaly_card)
        anomaly_layout.setContentsMargins(
            T.CARD_PAD_H, T.CARD_PAD_V, T.CARD_PAD_H, T.CARD_PAD_V
        )
        anomaly_layout.setSpacing(T.SPACE_SM)
        anomaly_layout.addWidget(StrongBodyLabel("类别分配", self.anomaly_card))
        anomaly_layout.addWidget(self._build_anomaly_card())

        # 左栏只做「拆分管理」：拆分列表（+ 异常任务的类别分配）；
        # 拆分概览 / 类别分布等信息卡在右侧主区（见 _build_main）
        return side_column(
            self.list_card, self.anomaly_card, width=T.SIDE_W_WIDER
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
        holder = QWidget(self.anomaly_card)
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
        """按任务显示：异常检测显示类别分配表，其余任务隐藏（参数在弹窗）。"""
        is_anomaly = self._vm.is_anomaly()
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

    def _build_main(self) -> QWidget:
        """右侧信息区（滚动 + 卡片式）：拆分概览 / 类别分布。

        卡片顶对齐、统一内边距与间距，底部留白；内容超高时本区自身滚动——
        页面尺寸策略为 Ignored，最小高度不得沿布局传导到窗口
        （与 `data_widgets.side_column` 的同款处理，见 UI 规则 §5）。
        """
        content = QWidget()
        column = QVBoxLayout(content)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(T.SPACE_ML)

        column.addWidget(self._build_overview_card(content))
        column.addWidget(self._build_class_card(content))
        column.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll.viewport().setAutoFillBackground(False)
        scroll.setWidget(content)
        return scroll

    # -----------------------------------------------------------
    # 右侧信息卡
    # -----------------------------------------------------------
    def _build_overview_card(self, parent: QWidget) -> QWidget:
        """拆分概览：环形图 + 汇总表（参照 DLT 的「数据拆分细节」）。

        环形图固定宽度、汇总表用控件排版（数值不会被遮挡），内容靠左、
        不随卡片宽度铺散；表底是分隔线 + 合计行。
        """
        card = CardWidget(parent)
        self.overview_card = card
        layout = QVBoxLayout(card)
        layout.setContentsMargins(
            T.CARD_PAD_H, T.CARD_PAD_V, T.CARD_PAD_H, T.CARD_PAD_V
        )
        layout.setSpacing(T.SPACE_SM)
        layout.addWidget(StrongBodyLabel("拆分概览", card))

        body = QHBoxLayout()
        body.setSpacing(T.SPACE_XL)
        self.pie = PieChart(card)
        self.pie.setFixedWidth(T.CTRL_W_XL)
        body.addWidget(self.pie)

        table = QGridLayout()
        table.setHorizontalSpacing(T.SPACE_MD)
        table.setVerticalSpacing(T.SPACE_SM)
        table.setColumnMinimumWidth(1, T.LABEL_MIN_W)

        dataset_head = CaptionLabel("数据集", card)
        table.addWidget(dataset_head, 0, 0, 1, 2)
        count_head = CaptionLabel("图像", card)
        count_head.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        table.addWidget(count_head, 0, 2)
        percent_head = CaptionLabel("百分比", card)
        percent_head.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        table.addWidget(percent_head, 0, 3)

        self.summary_rows: dict[str, dict[str, QLabel]] = {}
        for row, key in enumerate(("train", "val", "test"), start=1):
            chip = QLabel(card)
            chip.setFixedSize(12, 12)
            chip.setStyleSheet(
                f"background: {SPLIT_COLORS[key]}; border-radius: 2px;"
            )
            name = BodyLabel(SPLIT_LABELS[key], card)
            count = CaptionLabel("0", card)
            count.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            percent = CaptionLabel("0 %", card)
            percent.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            table.addWidget(chip, row, 0)
            table.addWidget(name, row, 1)
            table.addWidget(count, row, 2)
            table.addWidget(percent, row, 3)
            self.summary_rows[key] = {"count": count, "percent": percent}

        divider = QWidget(card)
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background: {T.GROUP_BORDER};")
        table.addWidget(divider, 4, 0, 1, 4)

        total_name = StrongBodyLabel("拆分中的图像", card)
        self.summary_total = CaptionLabel("0", card)
        self.summary_total.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.summary_percent = CaptionLabel("0 %", card)
        self.summary_percent.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        table.addWidget(total_name, 5, 0, 1, 2)
        table.addWidget(self.summary_total, 5, 2)
        table.addWidget(self.summary_percent, 5, 3)

        body.addLayout(table)
        body.addStretch(1)          # 内容靠左：不随卡片宽度铺散
        layout.addLayout(body)
        return card

    def _build_class_card(self, parent: QWidget) -> QWidget:
        """类别分布预览：每个类别一行堆叠条（训练 / 验证 / 测试分段）。"""
        card = CardWidget(parent)
        self.class_card = card
        layout = QVBoxLayout(card)
        layout.setContentsMargins(
            T.CARD_PAD_H, T.CARD_PAD_V, T.CARD_PAD_H, T.CARD_PAD_V
        )
        layout.setSpacing(T.SPACE_SM)
        layout.addWidget(StrongBodyLabel("类别分布预览", card))
        self.class_hint = CaptionLabel("", card)
        layout.addWidget(self.class_hint)
        self.bar_chart = StackedBarChart(card)
        layout.addWidget(self.bar_chart)
        return card

    # -----------------------------------------------------------
    # 绑定
    # -----------------------------------------------------------
    def _bind(self) -> None:
        self._vm.datasetChanged.connect(lambda _d: self.refresh())
        self._vm.taskFinished.connect(lambda _t: self.refresh())
        self._vm.splitsChanged.connect(lambda _rows: self.refresh())

    # -----------------------------------------------------------
    # 刷新
    # -----------------------------------------------------------
    def refresh(self) -> None:
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
                reason = str(row.get("lock_reason") or "")
                item.setData(_LOCKED_ROLE, bool(reason))
                if reason:
                    # 光标信息说明拆分状态（行右端另有锁图标）
                    item.setToolTip(
                        "该拆分正在训练中，无法编辑或删除"
                        if reason == "running"
                        else "该拆分已用于训练，无法编辑或删除"
                    )
                self.split_list.addItem(item)
                if int(row["id"]) == active_id:
                    self.split_list.setCurrentRow(self.split_list.count() - 1)
        finally:
            self._syncing = False
        # 按钮可用性：只有被训练引用（训练中 / 暂停 / 已训练过）的拆分
        # 不可编辑与删除——不再要求「至少保留一套」（可删光回到空态）
        reason = self._vm.split_lock_reason()
        has_selection = bool(rows) and self.split_list.currentRow() >= 0
        self.edit_split_btn.setEnabled(has_selection and not reason)
        self.edit_split_btn.setToolTip(
            "该拆分正在训练中，无法编辑" if reason == "running"
            else "该拆分已用于训练，无法编辑" if reason
            else ""
        )
        self.del_split_btn.setEnabled(has_selection and not reason)
        self.del_split_btn.setToolTip(
            "该拆分正在训练中，无法删除" if reason == "running"
            else "该拆分已用于训练，无法删除" if reason
            else ""
        )

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
        """新建拆分：弹窗确认名称 / 分配，可一键创建并执行拆分。"""
        dialog = SplitEditDialog(
            self.window(),
            mode=MODE_CREATE,
            name=self._vm.suggest_split_name(),
            total=len(self._vm.images()),
            ratios=(0.7, 0.2, 0.1),
            seed=0,
            stratified=True,
        )
        choice = dialog.exec_choice()
        if not choice:
            return
        values = dialog.values()
        split_id = self._vm.add_split(values["name"])
        if split_id < 0:
            return
        self._apply_split_values(split_id, values)
        self.refresh()
        if choice == CREATE_APPLY:
            self._vm.apply_split()

    def _on_edit_split(self) -> None:
        """编辑当前拆分：弹窗确认后保存，可选重新执行拆分。"""
        split = self._vm.active_split()
        if split is None:
            self._vm.notify("请先创建或打开项目", "warning")
            return
        reason = self._vm.split_lock_reason(split.split_id)
        if reason:
            self._vm.notify(
                "该拆分正在训练中，无法编辑" if reason == "running"
                else "该拆分已用于训练，无法编辑",
                "warning",
            )
            return
        dialog = SplitEditDialog(
            self.window(),
            mode=MODE_EDIT,
            name=split.name,
            total=len(self._vm.images()),
            ratios=(split.train, split.val, split.test),
            seed=int(split.seed),
            stratified=bool(split.stratified),
        )
        choice = dialog.exec_choice()
        if not choice:
            return
        values = dialog.values()
        self._apply_split_values(split.split_id, values)
        self.refresh()
        if choice == SAVE_APPLY:
            self._vm.apply_split()

    def _apply_split_values(self, split_id: int, values: dict) -> None:
        """把弹窗结果写入拆分（名称 / 比例 / 自定义种子 / 分层）。"""
        self._vm.rename_split(split_id, values["name"])
        self._vm.set_split(*values["ratios"], quiet=True)
        if values.get("seed") is not None:
            self._vm.set_seed(values["seed"], quiet=True)
        self._vm.set_stratified(values["stratified"])

    def _on_duplicate_split(self) -> None:
        active = self._vm.active_split()
        if active is not None:
            self._vm.duplicate_split(active.split_id)
            self.refresh()

    def _on_delete_split(self) -> None:
        active = self._vm.active_split()
        if active is None:
            return
        reason = self._vm.split_lock_reason(active.split_id)
        if reason:
            self._vm.notify(
                "该拆分正在训练中，无法删除" if reason == "running"
                else "该拆分已用于训练，无法删除",
                "warning",
            )
            return
        box = MessageBox(
            "删除拆分",
            f"确定要删除拆分「{active.name}」吗？\n\n"
            "磁盘上的产物目录将一并删除。",
            self.window(),
        )
        box.yesButton.setText("删除")
        box.cancelButton.setText("取消")
        if box.exec():
            self._vm.remove_split(active.split_id)
            self.refresh()

    def _apply_lock_state(self) -> None:
        """已被训练使用的拆分：参数只读（编辑 / 删除按钮见 _refresh_splits）。"""
        if self._vm.split_locked():
            for spin in self.anomaly_spins.values():
                spin.setEnabled(False)

    def _refresh_preview(self) -> None:
        # 没有拆分时不预览：预览基于 Dataset 的下沉比例计算，会显示成
        # 「已有一套 70/20/10 拆分」的假象（历史缺陷——新建项目看似自带
        # 一套 dataset 拆分）；预览从用户「新建」拆分后才开始
        preview = (
            self._vm.preview_split()
            if self._vm.active_split() is not None
            else {}
        )
        subsets = preview.get("subsets") or {}
        counts = {
            key: int((subsets.get(key) or {}).get("count", 0))
            for key in ("train", "val", "test")
        }
        # 「拆分中的图像」= 三个子集之和：手工改划到「未划分」的图片不在其中
        # （概览与百分比都用这个口径，否则分母比分子之和大、百分比合计不到 100 %）
        total = sum(counts.values())
        if not preview or not total:
            self.pie.set_data([], "")
            for row in self.summary_rows.values():
                row["count"].setText("0")
                row["percent"].setText("0 %")
            self.summary_total.setText("0")
            self.summary_percent.setText("0 %")
            self.bar_chart.set_data([])
            self.class_hint.setText("暂无数据")
            return

        # 概览：环形图 + 汇总表（三行 + 合计）
        segments = []
        for key in ("train", "val", "test"):
            segments.append((SPLIT_LABELS[key], counts[key], SPLIT_COLORS[key]))
            self.summary_rows[key]["count"].setText(str(counts[key]))
            self.summary_rows[key]["percent"].setText(
                f"{counts[key] * 100 / total:.0f} %"
            )
        self.pie.set_data(segments, str(total))
        self.summary_total.setText(str(total))
        self.summary_percent.setText("100 %")

        # 类别分布：每类别一行堆叠条（训练 / 验证 / 测试分段）；
        # 行序与「标签类别」管理一致：未标注在前、其余按项目类别定义顺序
        per_class = preview.get("per_class") or {}
        order = [UNLABELED_LABEL, *self._vm.class_name_list()]
        rank = {name: index for index, name in enumerate(order)}
        rows = []
        for name, info in sorted(
            per_class.items(),
            key=lambda kv: (rank.get(kv[0], len(order)), kv[0]),
        ):
            rows.append((
                name,
                int(info.get("total", 0)),
                [
                    (SPLIT_LABELS[key], int(info.get(key, 0) or 0),
                     SPLIT_COLORS[key])
                    for key in ("train", "val", "test")
                ],
            ))
        self.bar_chart.set_data(rows)
        self.class_hint.setText(f"共 {len(per_class)} 个类别 · {total} 张")


