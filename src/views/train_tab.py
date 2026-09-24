"""模型训练导航页：训练参数、实时曲线与训练记录。

界面结构参照 Halcon DLT 的「训练」页：

    左侧：项目信息 + 模型/权重 + 开始训练 + 训练记录列表
    中央：设置（拆分 / 模型与权重 / 训练参数 / 数据增强）与结果（实时指标 + 曲线 + 日志）

训练过程中的 Loss 与评价指标以**实时折线图**呈现（纯 QPainter 自绘，见
`views/widgets/charts.py::LineChart`），不再是纯文本指标。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QStackedWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    DoubleSpinBox,
    LineEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    ScrollArea,
    SegmentedWidget,
    SpinBox,
    StrongBodyLabel,
    TableWidget,
    TextEdit,
)

from src.services.backends import for_task as backend_for_task
from src.utils.constants import (
    OPTIMIZERS,
    SPLIT_COLORS,
    SPLIT_LABELS,
    YOLO_MODEL_VARIANTS,
)
from src.utils.device import device_label, device_options
from src.viewmodels.dataset_vm import DatasetViewModel
from src.viewmodels.model_vm import ModelViewModel
from src.viewmodels.train_vm import TrainViewModel
from src.views.data_widgets import side_column
from src.views.dialogs import ImagePreviewDialog
from src.views.widgets import LegendList, LineChart, PieChart

# ---------------------------------------------------------------
# 参数表：(键, 标签, 最小值, 最大值, 默认值, 步长, 后缀)
# ---------------------------------------------------------------
_INT_PARAMS = [
    ("epochs", "训练轮数", 1, 10000, 100, 1, ""),
    ("batch", "批次大小", 1, 512, 16, 1, ""),
    ("imgsz", "图像尺寸", 32, 2048, 640, 32, " px"),
    ("workers", "数据线程", 0, 32, 4, 1, ""),
    ("seed", "随机种子", 0, 99999, 0, 1, ""),
    ("patience", "早停耐心值", 0, 1000, 50, 1, " 轮"),
    ("close_mosaic", "末轮关增强", 0, 100, 10, 1, " 轮"),
]
_FLOAT_PARAMS = [
    ("lr", "学习率", 0.00001, 1.0, 0.01, 0.001, 5),
    ("weight_decay", "权重衰减", 0.0, 1.0, 0.0005, 0.0001, 5),
    ("momentum", "动量", 0.0, 1.0, 0.937, 0.01, 3),
    ("warmup_epochs", "学习率预热", 0.0, 50.0, 3.0, 0.5, 1),
    ("dropout", "Dropout", 0.0, 0.9, 0.0, 0.05, 2),
]
_CHECK_PARAMS = [
    ("deterministic", "使用确定性算法"),
    ("cos_lr", "余弦学习率调度"),
    ("val", "每轮验证"),
    ("cache", "缓存图片"),
    ("rect", "矩形训练"),
    ("single_cls", "视为单一类别"),
    ("resume", "断点续训"),
]
_AUG_PARAMS = [
    ("hflip", "水平翻转", 0.0, 1.0, 0.5, 0.05),
    ("vflip", "垂直翻转", 0.0, 1.0, 0.0, 0.05),
    ("degrees", "旋转角度", 0.0, 180.0, 0.0, 5.0),
    ("scale", "缩放幅度", 0.0, 1.0, 0.5, 0.05),
    ("translate", "平移幅度", 0.0, 1.0, 0.1, 0.05),
    ("hsv_h", "色调变化", 0.0, 1.0, 0.015, 0.005),
    ("hsv_s", "饱和度变化", 0.0, 1.0, 0.7, 0.05),
    ("hsv_v", "亮度变化", 0.0, 1.0, 0.4, 0.05),
    ("mosaic", "马赛克", 0.0, 1.0, 1.0, 0.1),
    ("mixup", "混合", 0.0, 1.0, 0.0, 0.1),
]

_LOSS_COLORS = {"训练损失": "#0F6CBD", "验证损失": "#0F7B0F"}
_METRIC_COLORS = ("#0F6CBD", "#0F7B0F", "#C42B1C", "#B16CEA", "#E8A33D")


def _duration(seconds: float) -> str:
    """秒 → HH:MM:SS。"""
    total = max(0, int(seconds))
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


class TrainTab(QWidget):
    """模型训练页：参数设置、训练执行、实时曲线与结果。

    与拆分 / 检查等两栏页面一致，直接继承 `QWidget` 并自建根布局；
    `BasePage` 自带滚动区布局，两者叠加会导致根布局安装失败（界面空白）。
    """

    def __init__(
        self,
        vm: TrainViewModel,
        model_vm: ModelViewModel | None = None,
        dataset_vm: DatasetViewModel | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._vm = vm
        self._model_vm = model_vm
        self._dataset_vm = dataset_vm
        self._syncing = False
        self._int_spins: dict[str, SpinBox] = {}
        self._float_spins: dict[str, DoubleSpinBox] = {}
        self._checks: dict[str, CheckBox] = {}
        self._aug_spins: dict[str, DoubleSpinBox] = {}
        self._tile_values: dict[str, BodyLabel] = {}
        self._build_ui()
        self._bind()
        self._on_status(self._vm.config.status)
        self.sync_from_config()

    # -----------------------------------------------------------
    # 界面搭建
    # -----------------------------------------------------------
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(12)
        root.addWidget(self._build_side(), 0)
        root.addLayout(self._build_main(), 1)

    def _card(self, title: str) -> tuple[CardWidget, QVBoxLayout]:
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        if title:
            layout.addWidget(StrongBodyLabel(title, card))
        return card, layout

    # --------------------------------------------------- 左侧
    def _build_side(self) -> QWidget:
        project_card, project_layout = self._card("项目")
        self.project_name = BodyLabel("—", project_card)
        self.project_name.setWordWrap(True)
        project_layout.addWidget(self.project_name)
        self.project_meta = CaptionLabel("", project_card)
        project_layout.addWidget(self.project_meta)

        train_card, train_layout = self._card("模型训练")
        variant_row = QHBoxLayout()
        variant_row.addWidget(CaptionLabel("模型", train_card))
        self.variant_combo = ComboBox(train_card)
        for variant in YOLO_MODEL_VARIANTS:
            self.variant_combo.addItem(variant["label"], userData=variant["key"])
        variant_row.addWidget(self.variant_combo, 1)
        train_layout.addLayout(variant_row)

        self.variant_desc = CaptionLabel("", train_card)
        train_layout.addWidget(self.variant_desc)

        button_row = QHBoxLayout()
        self.start_btn = PrimaryPushButton("开始训练", train_card)
        self.pause_btn = PushButton("暂停", train_card)
        self.pause_btn.setToolTip("在当前轮结束后暂停，随时可继续（不丢失进度）")
        self.stop_btn = PushButton("停止", train_card)
        button_row.addWidget(self.start_btn, 1)
        button_row.addWidget(self.pause_btn)
        button_row.addWidget(self.stop_btn)
        train_layout.addLayout(button_row)

        reset_row = QHBoxLayout()
        self.reset_btn = PushButton("重置状态", train_card)
        reset_row.addWidget(self.reset_btn)
        self.continue_btn = PushButton("继续训练 (+50 轮)", train_card)
        self.continue_btn.setToolTip(
            "在最近一次训练的基础上追加 50 轮继续训练（断点续训，需保留 last.pt）"
        )
        reset_row.addWidget(self.continue_btn)
        reset_row.addStretch(1)
        train_layout.addLayout(reset_row)

        train_layout.addWidget(StrongBodyLabel("训练记录", train_card))
        self.history_list = TableWidget(train_card)
        self.history_list.setColumnCount(2)
        self.history_list.setHorizontalHeaderLabels(["时间", "最佳"])
        self.history_list.verticalHeader().setVisible(False)
        self.history_list.setFixedHeight(180)
        self.history_list.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.history_list.setSelectionBehavior(
            TableWidget.SelectionBehavior.SelectRows
        )
        train_layout.addWidget(self.history_list)

        return side_column(project_card, train_card, width=290)

    # --------------------------------------------------- 中央
    def _build_main(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(10)

        header = CardWidget(self)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 10, 16, 10)
        header_layout.setSpacing(12)
        self.tab_seg = SegmentedWidget(header)
        self.tab_seg.addItem("settings", "设置", onClick=lambda: self._switch(0))
        self.tab_seg.addItem("results", "结果", onClick=lambda: self._switch(1))
        self.tab_seg.addItem("compare", "对比", onClick=lambda: self._switch(2))
        self.tab_seg.setCurrentItem("settings")
        header_layout.addWidget(self.tab_seg)
        header_layout.addStretch(1)
        self.state_label = CaptionLabel("", header)
        header_layout.addWidget(self.state_label)
        column.addWidget(header)

        self.stack = QStackedWidget(self)
        self.stack.addWidget(self._build_settings_pane())
        self.stack.addWidget(self._build_results_pane())
        self.stack.addWidget(self._build_compare_pane())
        column.addWidget(self.stack, 1)
        return column

    def _switch(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        if index == 1:
            # 切到结果页时按最新数据重绘一次曲线
            self._on_curves(self._vm.curves())
        elif index == 2:
            # 切到对比页时按勾选的训练记录重建曲线
            self._refresh_compare()

    # --------------------------------------------------- 设置页
    def _build_settings_pane(self) -> QWidget:
        area = ScrollArea(self)
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(10)
        layout.addWidget(self._build_setup_card())
        layout.addWidget(self._build_split_card())
        layout.addWidget(self._build_model_card())
        layout.addWidget(self._build_param_card())
        layout.addWidget(self._build_aug_card())
        layout.addWidget(self._build_weight_card())
        layout.addStretch(1)
        area.setWidget(body)
        return area

    def _build_setup_card(self) -> CardWidget:
        """训练设置（setup）：命名保存多套训练参数，一键切换对比。"""
        self._syncing_setup = False
        card, layout = self._card("训练设置")

        pick_row = QHBoxLayout()
        self.setup_combo = ComboBox(card)
        self.setup_combo.currentIndexChanged.connect(self._on_setup_picked)
        pick_row.addWidget(self.setup_combo, 1)
        for text, slot, tip in (
            ("新建", self._on_setup_create, "以当前参数新建一套训练设置"),
            ("复制", self._on_setup_duplicate, "复制当前设置（含参数）"),
            ("删除", self._on_setup_delete, "删除当前设置（至少保留一套）"),
        ):
            button = PushButton(text, card)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            pick_row.addWidget(button)
        layout.addLayout(pick_row)

        name_row = QHBoxLayout()
        name_row.addWidget(CaptionLabel("名称", card))
        self.setup_name = LineEdit(card)
        self.setup_name.setPlaceholderText("设置名称")
        self.setup_name.editingFinished.connect(self._on_setup_renamed)
        name_row.addWidget(self.setup_name, 1)
        layout.addLayout(name_row)

        note_row = QHBoxLayout()
        note_row.addWidget(CaptionLabel("备注", card))
        self.setup_note = LineEdit(card)
        self.setup_note.setPlaceholderText("这套参数想验证什么（可选）")
        self.setup_note.editingFinished.connect(self._on_setup_renamed)
        note_row.addWidget(self.setup_note, 1)
        layout.addLayout(note_row)

        self.setup_hint = CaptionLabel(
            "切换设置会先保存当前参数，再载入所选设置（随项目保存）", card
        )
        self.setup_hint.setWordWrap(True)
        layout.addWidget(self.setup_hint)
        return card

    def _fill_setups(self) -> None:
        """刷新训练设置卡片（下拉 + 名称 / 备注）。"""
        setups = self._vm.setups()
        active = self._vm.active_setup_id()
        self._syncing_setup = True
        try:
            self.setup_combo.clear()
            for item in setups:
                label = str(item.get("name") or "设置")
                note = str(item.get("note") or "")
                self.setup_combo.addItem(
                    f"{label} · {note}" if note else label,
                    userData=int(item.get("setup_id", 0)),
                )
            if setups:
                index = next(
                    (
                        position for position, item in enumerate(setups)
                        if int(item.get("setup_id", 0)) == active
                    ),
                    0,
                )
                self.setup_combo.setCurrentIndex(index)
            current = self._vm.active_setup()
            self.setup_name.setText(str(current.get("name") or ""))
            self.setup_note.setText(str(current.get("note") or ""))
            for widget in (self.setup_combo, self.setup_name, self.setup_note):
                widget.setEnabled(bool(setups))
        finally:
            self._syncing_setup = False

    def _on_setup_picked(self, index: int) -> None:
        """切换训练设置：应用参数并同步界面。"""
        if self._syncing_setup or index < 0:
            return
        setup_id = int(self.setup_combo.itemData(index) or 0)
        if self._vm.apply_setup(setup_id):
            self._sync_widgets(self._vm.config)
            self._refresh_split()
            self._fill_setups()

    def _on_setup_create(self) -> None:
        self._vm.create_setup()
        self._sync_widgets(self._vm.config)
        self._fill_setups()

    def _on_setup_duplicate(self) -> None:
        self._vm.duplicate_setup(self._vm.active_setup_id())
        self._sync_widgets(self._vm.config)
        self._fill_setups()

    def _on_setup_delete(self) -> None:
        self._vm.delete_setup(self._vm.active_setup_id())
        self._sync_widgets(self._vm.config)
        self._refresh_split()
        self._fill_setups()

    def _on_setup_renamed(self) -> None:
        if self._syncing_setup:
            return
        self._vm.rename_setup(
            self._vm.active_setup_id(),
            self.setup_name.text().strip(),
            self.setup_note.text().strip(),
        )
        self._fill_setups()

    def _build_split_card(self) -> CardWidget:
        card, layout = self._card("数据集拆分")
        # 一个项目可以有多套拆分：训练前选择用哪一套
        pick_row = QHBoxLayout()
        pick_row.addWidget(CaptionLabel("拆分", card))
        self.split_combo = ComboBox(card)
        self.split_combo.currentIndexChanged.connect(self._on_split_picked)
        pick_row.addWidget(self.split_combo, 1)
        refresh_btn = PushButton("刷新", card)
        refresh_btn.clicked.connect(self._refresh_split)
        pick_row.addWidget(refresh_btn)
        layout.addLayout(pick_row)

        self.data_label = BodyLabel("—", card)
        self.data_label.setWordWrap(True)
        layout.addWidget(self.data_label)

        chart_row = QHBoxLayout()
        chart_row.setSpacing(12)
        self.pie = PieChart(card)
        self.pie.setFixedHeight(160)
        chart_row.addWidget(self.pie, 1)
        self.legend = LegendList(card)
        chart_row.addWidget(self.legend, 1)
        layout.addLayout(chart_row)
        return card

    def _build_model_card(self) -> CardWidget:
        card, layout = self._card("模型与权重")
        weights_row = QHBoxLayout()
        self.weights_edit = LineEdit(card)
        self.weights_edit.setPlaceholderText("自定义权重 (.pt)")
        self.weights_edit.setToolTip("留空则使用所选变体的官方预训练权重")
        self.weights_edit.textChanged.connect(
            lambda text: self._on_param_changed() if not self._syncing
            else None
        )
        weights_row.addWidget(self.weights_edit, 1)
        browse_btn = PushButton("浏览", card)
        browse_btn.clicked.connect(self._on_browse_weights)
        weights_row.addWidget(browse_btn)
        import_btn = PushButton("导入", card)
        import_btn.clicked.connect(self._on_import_weights)
        weights_row.addWidget(import_btn)
        layout.addLayout(weights_row)

        self.model_info = CaptionLabel("未导入权重", card)
        self.model_info.setWordWrap(True)
        layout.addWidget(self.model_info)
        return card

    def _build_param_card(self) -> CardWidget:
        card, layout = self._card("训练参数")
        form = QFormLayout()
        form.setSpacing(8)

        self.opt_combo = ComboBox(card)
        self.opt_combo.addItems(OPTIMIZERS)
        self.opt_combo.currentIndexChanged.connect(self._on_param_changed)
        form.addRow("求解器", self.opt_combo)

        device_row = QWidget(card)
        device_layout = QHBoxLayout(device_row)
        device_layout.setContentsMargins(0, 0, 0, 0)
        device_layout.setSpacing(8)
        self.device_combo = ComboBox(device_row)
        for option in device_options():
            self.device_combo.addItem(option, userData=option.split(" (")[0])
        self.device_combo.currentIndexChanged.connect(self._on_device_changed)
        device_layout.addWidget(self.device_combo, 1)
        self.device_hint = CaptionLabel("", device_row)
        device_layout.addWidget(self.device_hint)
        form.addRow("设备", device_row)

        for key, label, minimum, maximum, default, step, suffix in _INT_PARAMS:
            spin = SpinBox(card)
            spin.setRange(minimum, maximum)
            spin.setSingleStep(step)
            spin.setValue(default)
            if suffix:
                spin.setSuffix(suffix)
            spin.valueChanged.connect(self._on_param_changed)
            form.addRow(label, spin)
            self._int_spins[key] = spin

        for key, label, minimum, maximum, default, step, decimals in _FLOAT_PARAMS:
            spin = DoubleSpinBox(card)
            spin.setRange(minimum, maximum)
            spin.setSingleStep(step)
            spin.setDecimals(decimals)
            spin.setValue(default)
            spin.valueChanged.connect(self._on_param_changed)
            form.addRow(label, spin)
            self._float_spins[key] = spin

        layout.addLayout(form)

        check_grid = QGridLayout()
        check_grid.setSpacing(6)
        for index, (key, label) in enumerate(_CHECK_PARAMS):
            box = CheckBox(label, card)
            box.stateChanged.connect(self._on_param_changed)
            check_grid.addWidget(box, index // 3, index % 3)
            self._checks[key] = box
        layout.addLayout(check_grid)

        # 异常检测专属参数
        self.anomaly_row = QWidget(card)
        anomaly_layout = QHBoxLayout(self.anomaly_row)
        anomaly_layout.setContentsMargins(0, 0, 0, 0)
        self.anomaly_edit = LineEdit(self.anomaly_row)
        self.anomaly_edit.setPlaceholderText("含 normal/ 与 abnormal/ 的目录")
        anomaly_layout.addWidget(self.anomaly_edit, 1)
        anomaly_btn = PushButton("浏览", self.anomaly_row)
        anomaly_btn.clicked.connect(self._on_browse_anomaly)
        anomaly_layout.addWidget(anomaly_btn)
        self.anomaly_check = CheckBox("预训练权重", self.anomaly_row)
        self.anomaly_check.setChecked(True)
        anomaly_layout.addWidget(self.anomaly_check)
        layout.addWidget(self.anomaly_row)

        # 训练参数预览：按当前 imgsz 与增强参数生成拼图（letterbox + 增强示意）
        preview_row = QHBoxLayout()
        preview_btn = PushButton("预览参数", card)
        preview_btn.setToolTip(
            "按当前图像尺寸与增强参数生成预览拼图（letterbox + 增强示意）"
        )
        preview_btn.clicked.connect(self._on_preview_params)
        preview_row.addWidget(preview_btn)
        self.preview_hint = CaptionLabel("", card)
        self.preview_hint.setWordWrap(True)
        preview_row.addWidget(self.preview_hint, 1)
        layout.addLayout(preview_row)
        return card

    def _build_weight_card(self) -> CardWidget:
        """类别权重（参考）：按训练集频次平衡，便于判断数据是否均衡。"""
        card, layout = self._card("类别权重")
        self.weight_hint = CaptionLabel("", card)
        self.weight_hint.setWordWrap(True)
        layout.addWidget(self.weight_hint)

        self.weight_holder = QWidget(card)
        self.weight_grid = QGridLayout(self.weight_holder)
        self.weight_grid.setContentsMargins(0, 0, 0, 0)
        self.weight_grid.setSpacing(4)
        layout.addWidget(self.weight_holder)

        buttons = QHBoxLayout()
        balance_btn = PushButton("平衡 (BALANCE)", card)
        balance_btn.setToolTip(
            "按训练集各类别频次生成逆频次权重（均值归一化为 1）"
        )
        balance_btn.clicked.connect(self._on_balance_weights)
        buttons.addWidget(balance_btn)
        reset_btn = PushButton("重置", card)
        reset_btn.setToolTip("清除类别权重")
        reset_btn.clicked.connect(self._on_reset_weights)
        buttons.addWidget(reset_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return card

    def _fill_weights(self) -> None:
        """刷新类别权重展示（数量 / 权重 / 是否已应用）。"""
        while self.weight_grid.count():
            item = self.weight_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        rows = self._vm.class_weight_rows()
        if not rows:
            self.weight_hint.setText("暂无可统计的训练集（请先选择已生成的拆分）")
            return
        for index, row in enumerate(rows):
            self.weight_grid.addWidget(
                CaptionLabel(
                    f"{row['name']} · {row['count']} 个 · 权重 {row['weight']:.2f}",
                    self.weight_holder,
                ),
                index // 2, index % 2,
            )
        applied = all(row["applied"] for row in rows)
        self.weight_hint.setText(
            "已应用逆频次权重。说明：Ultralytics 当前不消费该权重，"
            "此项用于判断数据是否均衡、是否需要补样本或改用单类训练"
            if applied else
            "未设置：上面是按训练集频次算出的「建议」权重，"
            "点「平衡」后写入训练配置备查"
        )

    def _on_preview_params(self) -> None:
        """生成并查看训练参数预览拼图。"""
        path = self._vm.preview_params()
        if not path:
            return
        config = self._vm.config
        self.preview_hint.setText(f"预览已生成：{Path(path).name}")
        ImagePreviewDialog(
            self, path, "训练参数预览",
            f"imgsz {int(config.imgsz)} · 增强 {'开启' if config.augment else '关闭'}"
            " · 每格为 letterbox 后的训练输入示意（mosaic / mixup 未体现）",
        ).exec()

    def _on_balance_weights(self) -> None:
        self._vm.balance_class_weights()
        self._fill_weights()

    def _on_reset_weights(self) -> None:
        self._vm.reset_class_weights()
        self._fill_weights()

    def _build_aug_card(self) -> CardWidget:
        card, layout = self._card("数据增强")
        self.augment_check = CheckBox("启用图像增强", card)
        self.augment_check.setChecked(True)
        self.augment_check.stateChanged.connect(self._on_augment_toggled)
        layout.addWidget(self.augment_check)

        self.aug_holder = QWidget(card)
        grid = QGridLayout(self.aug_holder)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)
        for index, (key, label, minimum, maximum, default, step) in enumerate(
            _AUG_PARAMS
        ):
            box = QWidget(self.aug_holder)
            row = QHBoxLayout(box)
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(CaptionLabel(label, box))
            spin = DoubleSpinBox(box)
            spin.setRange(minimum, maximum)
            spin.setSingleStep(step)
            spin.setDecimals(3 if step < 0.01 else 2)
            spin.setValue(default)
            spin.valueChanged.connect(self._on_param_changed)
            row.addWidget(spin, 1)
            grid.addWidget(box, index // 3, index % 3)
            self._aug_spins[key] = spin
        layout.addWidget(self.aug_holder)
        return card

    # --------------------------------------------------- 结果页
    def _build_results_pane(self) -> QWidget:
        area = ScrollArea(self)
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(10)
        layout.addWidget(self._build_progress_card())
        layout.addWidget(self._build_curve_card())
        layout.addWidget(self._build_compare_card())
        layout.addWidget(self._build_log_card())
        layout.addStretch(1)
        area.setWidget(body)
        return area

    def _build_progress_card(self) -> CardWidget:
        card, layout = self._card("训练进度")
        self.progress_bar = ProgressBar(card)
        self.progress_bar.setRange(0, 100)
        layout.addWidget(self.progress_bar)

        tiles = [
            ("轮次", "epoch"), ("迭代", "iteration"), ("学习率", "lr"),
            ("训练损失", "loss"), ("验证损失", "val_loss"),
            ("已用时", "elapsed"), ("预计剩余", "eta"), ("最佳指标", "best"),
        ]
        grid = QGridLayout()
        grid.setSpacing(10)
        for index, (title, key) in enumerate(tiles):
            box = QWidget(card)
            column = QVBoxLayout(box)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(2)
            column.addWidget(CaptionLabel(title, box))
            value = BodyLabel("—", box)
            column.addWidget(value)
            grid.addWidget(box, index // 4, index % 4)
            self._tile_values[key] = value
        layout.addLayout(grid)
        return card

    def _build_curve_card(self) -> CardWidget:
        card, layout = self._card("训练曲线")
        self.loss_chart = LineChart(card, y_label="Loss")
        layout.addWidget(self.loss_chart)
        self.metric_chart = LineChart(card)
        layout.addWidget(self.metric_chart)
        return card

    def _build_compare_card(self) -> CardWidget:
        card, layout = self._card("指标对比")
        self.compare_table = TableWidget(card)
        self.compare_table.setColumnCount(3)
        self.compare_table.setHorizontalHeaderLabels(["指标", "上一轮", "最新"])
        self.compare_table.verticalHeader().setVisible(False)
        self.compare_table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.compare_table.setFixedHeight(170)
        self.compare_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.compare_table)
        return card

    def _build_log_card(self) -> CardWidget:
        card, layout = self._card("训练日志")
        self.log_edit = TextEdit(card)
        self.log_edit.setReadOnly(True)
        self.log_edit.setFixedHeight(150)
        layout.addWidget(self.log_edit)
        return card

    # --------------------------------------------------- 对比页
    def _build_compare_pane(self) -> QWidget:
        area = ScrollArea(self)
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(10)
        layout.addWidget(self._build_model_table_card())
        layout.addWidget(self._build_compare_curve_card())
        layout.addWidget(self._build_compare_metric_card())
        layout.addStretch(1)
        area.setWidget(body)
        return area

    def _build_model_table_card(self) -> CardWidget:
        card, layout = self._card("训练模型")
        self.model_table = TableWidget(card)
        self.model_table.setColumnCount(5)
        self.model_table.setHorizontalHeaderLabels(
            ["选择", "时间", "拆分", "设置", "最佳"]
        )
        self.model_table.verticalHeader().setVisible(False)
        self.model_table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.model_table.setFixedHeight(150)
        self.model_table.itemChanged.connect(self._on_model_check_changed)
        layout.addWidget(self.model_table)
        return card

    def _build_compare_curve_card(self) -> CardWidget:
        card, layout = self._card("曲线对比")
        self.compare_loss = LineChart(card, y_label="Loss")
        layout.addWidget(self.compare_loss)
        self.compare_metric = LineChart(card)
        layout.addWidget(self.compare_metric)
        return card

    def _build_compare_metric_card(self) -> CardWidget:
        card, layout = self._card("指标对比")
        self.compare_models = TableWidget(card)
        self.compare_models.setColumnCount(7)
        self.compare_models.setHorizontalHeaderLabels(
            ["时间", "拆分", "设置", "模型", "最佳指标", "最佳轮次", "最终 Loss"]
        )
        self.compare_models.verticalHeader().setVisible(False)
        self.compare_models.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.compare_models.setFixedHeight(170)
        self.compare_models.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.compare_models)
        return card

    # -----------------------------------------------------------
    # 交互
    # -----------------------------------------------------------
    def _bind(self) -> None:
        self.start_btn.clicked.connect(self._on_start)
        self.pause_btn.clicked.connect(self._on_pause_toggle)
        self.continue_btn.clicked.connect(self._on_continue_training)
        self.stop_btn.clicked.connect(self._vm.stop)
        self.reset_btn.clicked.connect(self._vm.reset)
        self.variant_combo.currentIndexChanged.connect(self._on_variant_changed)

        self._vm.statusChanged.connect(self._on_status)
        self._vm.progressChanged.connect(self._on_progress)
        self._vm.metricsChanged.connect(self._on_metrics)
        self._vm.curvesChanged.connect(self._on_curves)
        self._vm.logAppended.connect(self._on_log)
        self._vm.configChanged.connect(self._on_config)
        self._vm.historyChanged.connect(self._on_history)
        self.history_list.cellClicked.connect(self._on_history_clicked)

        if self._model_vm is not None:
            self._model_vm.modelInfo.connect(self._on_model_info)
            self._model_vm.taskStarted.connect(self._on_task_started)
            self._model_vm.taskFailed.connect(self._on_task_failed)
        if self._dataset_vm is not None:
            self._dataset_vm.datasetChanged.connect(lambda _d: self._refresh())
            self._dataset_vm.datasetReady.connect(lambda _p: self._refresh())
            self._dataset_vm.splitsChanged.connect(
                lambda _rows: self._refresh_split()
            )

    def _on_browse_weights(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择权重文件", "", "PyTorch Weight (*.pt)"
        )
        if path:
            self.weights_edit.setText(path)
            self._on_import_weights()

    def _on_import_weights(self) -> None:
        """导入自定义权重：交给模型 ViewModel 探测信息，同时写进训练配置。"""
        path = self.weights_edit.text().strip()
        if self._model_vm is not None and path:
            self._model_vm.import_weights(path)
        self._apply({"weights_path": path})

    def _on_browse_anomaly(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择异常检测数据目录")
        if directory:
            self.anomaly_edit.setText(directory)
            self._apply({"anomaly_root": directory})

    def _on_variant_changed(self, _index: int) -> None:
        if self._syncing:
            return
        key = str(self.variant_combo.currentData() or "")
        if not key:
            return
        if self._model_vm is not None:
            self._model_vm.select_variant(key)
        variant = next(
            (v for v in YOLO_MODEL_VARIANTS if v["key"] == key), None
        )
        if variant is not None:
            self.variant_desc.setText(f"{variant['desc']} · {variant['imgsz']}px")
        # 变体换了以后，官方权重路径要跟着换（自定义权重保留）
        current = self.weights_edit.text().strip()
        changes = {"model_key": key}
        if current and Path(current).name == f"{self._vm.config.model_key}.pt":
            self.weights_edit.setText("")
            changes["weights_path"] = ""
        self._apply(changes)

    def _on_device_changed(self, _index: int = 0) -> None:
        self._update_device_hint()
        self._on_param_changed()

    def _update_device_hint(self) -> None:
        """显示当前实际会用的设备（一眼确认有没有走 GPU）。"""
        device = str(self.device_combo.currentData() or "auto")
        self.device_hint.setText(device_label(device))

    def _on_augment_toggled(self, _state: int) -> None:
        enabled = self.augment_check.isChecked()
        self.aug_holder.setEnabled(enabled)
        self._on_param_changed()

    def _on_param_changed(self, *_args) -> None:
        if self._syncing:
            return
        self._apply(self._collect())

    def _apply(self, changes: dict) -> None:
        """写回训练配置（带同步标志，避免与界面回填互相触发）。"""
        self._syncing = True
        try:
            self._vm.update_config(**changes)
        finally:
            self._syncing = False

    def _collect(self) -> dict:
        """把界面上的全部参数收集为配置字典。"""
        values: dict = {
            "model_key": str(self.variant_combo.currentData() or ""),
            "weights_path": self.weights_edit.text().strip(),
            "optimizer": self.opt_combo.currentText(),
            "device": str(self.device_combo.currentData() or "auto"),
            "anomaly_root": self.anomaly_edit.text().strip(),
            "anomaly_pretrained": self.anomaly_check.isChecked(),
            "augment": self.augment_check.isChecked(),
        }
        for key, spin in self._int_spins.items():
            values[key] = spin.value()
        for key, spin in self._float_spins.items():
            values[key] = spin.value()
        for key, box in self._checks.items():
            values[key] = box.isChecked()
        for key, spin in self._aug_spins.items():
            values[key] = spin.value()
        return values

    def _on_start(self) -> None:
        self._apply(self._collect())
        self._vm.start()

    # -----------------------------------------------------------
    # 配置 / 状态同步
    # -----------------------------------------------------------
    def _on_config(self, config) -> None:
        self._sync_widgets(config)
        self._refresh()

    def sync_from_config(self) -> None:
        """项目类型 / 配置变化后刷新界面（main_window 打开项目时调用）。"""
        self._sync_widgets(self._vm.config)
        self._refresh()

    def _sync_widgets(self, config) -> None:
        self._syncing = True
        try:
            for key, spin in self._int_spins.items():
                spin.setValue(int(getattr(config, key, spin.value()) or 0))
            for key, spin in self._float_spins.items():
                spin.setValue(float(getattr(config, key, spin.value()) or 0.0))
            for key, box in self._checks.items():
                box.setChecked(bool(getattr(config, key, False)))
            for key, spin in self._aug_spins.items():
                spin.setValue(float(getattr(config, key, spin.value()) or 0.0))
            self.augment_check.setChecked(bool(getattr(config, "augment", True)))
            self.aug_holder.setEnabled(self.augment_check.isChecked())

            index = self.opt_combo.findText(str(getattr(config, "optimizer", "auto")))
            self.opt_combo.setCurrentIndex(max(0, index))
            device = str(getattr(config, "device", "auto"))
            device_index = self.device_combo.findData(device)
            if device_index < 0:
                self.device_combo.addItem(device, userData=device)
                device_index = self.device_combo.count() - 1
            self.device_combo.setCurrentIndex(device_index)
            self._update_device_hint()

            # 任务类型由项目类型决定：只切模型列表，不让用户改
            task = str(getattr(config, "task_type", "detect"))
            self._reload_variants(task)
            key_index = self.variant_combo.findData(str(getattr(config, "model_key", "")))
            if key_index >= 0:
                self.variant_combo.setCurrentIndex(key_index)
            self.weights_edit.setText(str(getattr(config, "weights_path", "")))
            self.anomaly_edit.setText(str(getattr(config, "anomaly_root", "")))
            self.anomaly_check.setChecked(
                bool(getattr(config, "anomaly_pretrained", True))
            )
            self.anomaly_row.setVisible(config.task_type == "anomaly")
        finally:
            self._syncing = False

    def _reload_variants(self, task: str) -> None:
        """按任务类型重建模型下拉（候选由任务对应的后端提供，避免重复添加）。"""
        signature = str(task)
        if getattr(self, "_variant_task", "") == signature:
            return
        previous = str(self.variant_combo.currentData() or "")
        self.variant_combo.clear()
        for candidate in backend_for_task(task).candidates(task):
            self.variant_combo.addItem(candidate["label"], userData=candidate["key"])
        self._variant_task = signature
        index = self.variant_combo.findData(previous)
        self.variant_combo.setCurrentIndex(max(0, index))

    def _refresh(self) -> None:
        """刷新项目信息、数据集拆分图与训练记录。"""
        project = self._vm.project
        config = self._vm.config
        if project is not None:
            self.project_name.setText(project.name)
            self.project_meta.setText(
                f"{_type_label(project.model_type)} · {config.model_key}"
            )
        else:
            self.project_name.setText("—")
            self.project_meta.setText("")
        if not self.variant_desc.text():
            variant = next(
                (v for v in YOLO_MODEL_VARIANTS if v["key"] == config.model_key), None
            )
            if variant is not None:
                self.variant_desc.setText(
                    f"{variant['desc']} · {variant['imgsz']}px"
                )
        self._refresh_split()
        self._on_history(self._vm.history())

    def _refresh_split(self) -> None:
        """拆分下拉 + 占比图（一个项目可有多套拆分，训练前先选一套）。"""
        config = self._vm.config
        splits = (
            self._dataset_vm.splits_ready() if self._dataset_vm is not None else []
        )
        self._syncing = True
        try:
            self.split_combo.clear()
            for row in splits:
                self.split_combo.addItem(str(row["label"]), userData=int(row["id"]))
            index = self.split_combo.findData(int(config.split_id))
            if index < 0 and splits:
                index = 0
            if index >= 0:
                self.split_combo.setCurrentIndex(index)
        finally:
            self._syncing = False

        row = next(
            (item for item in splits if int(item["id"]) == int(config.split_id)), None
        )
        if row is None and splits:
            row = splits[0]
        if row is not None and int(row["id"]) != int(config.split_id):
            self._syncing = True
            try:
                self._sync_split_config(row)
            finally:
                self._syncing = False

        if config.task_type == "anomaly":
            # 数据目录跟随当前拆分（拆分切换 / 重新划分后同步显示）
            self.anomaly_edit.setText(str(config.anomaly_root or ""))
            self.data_label.setText(config.anomaly_root or "未选择")
            self.pie.set_data([], "")
            self.legend.set_data([])
            return

        counts = (row or {}).get("counts") or {}
        total = int((row or {}).get("total") or 0)
        if total:
            segments, legend = [], []
            for name in ("train", "val", "test"):
                count = int(counts.get(name, 0))
                segments.append((SPLIT_LABELS[name], count, SPLIT_COLORS[name]))
                legend.append((SPLIT_COLORS[name], SPLIT_LABELS[name], f"{count}"))
            self.data_label.setText(f"{row['name']} · {total} 张")
            self.pie.set_data(segments, str(total))
            self.legend.set_data(legend)
            return

        # 未生成：用当前拆分参数的预览值
        preview = (
            self._dataset_vm.preview_split() if self._dataset_vm is not None else {}
        ) or {}
        subsets = preview.get("subsets") or {}
        preview_total = int(preview.get("total", 0) or 0)
        segments, legend = [], []
        for name in ("train", "val", "test"):
            count = int((subsets.get(name) or {}).get("count", 0))
            segments.append((SPLIT_LABELS[name], count, SPLIT_COLORS[name]))
            legend.append((SPLIT_COLORS[name], SPLIT_LABELS[name], f"{count}"))
        self.pie.set_data(segments, str(preview_total) if preview_total else "")
        self.legend.set_data(legend if preview_total else [])
        self.data_label.setText(
            f"{row['name']} · 未生成" if row else "未选择拆分"
        )
        self._fill_weights()
        self._fill_setups()

    def _on_split_picked(self, _index: int = 0) -> None:
        """在训练页切换拆分：同步项目当前拆分与训练配置。"""
        if self._syncing or self._dataset_vm is None:
            return
        split_id = self.split_combo.currentData()
        if split_id is None:
            return
        row = next(
            (item for item in self._dataset_vm.splits_ready()
             if int(item["id"]) == int(split_id)),
            None,
        )
        if row is None:
            return
        self._dataset_vm.select_split(int(split_id))
        self._syncing = True
        try:
            self._sync_split_config(row)
        finally:
            self._syncing = False
        self._refresh_split()

    def _sync_split_config(self, row: dict) -> None:
        """把选中的拆分写入训练配置（data.yaml 随拆分走）。"""
        split = (
            self._dataset_vm.split_by_id(int(row["id"]))
            if self._dataset_vm is not None else None
        )
        self._vm.update_config(
            split_id=int(row["id"]),
            split_name=str(row["name"]),
            data_yaml=str(getattr(split, "data_yaml", "") or ""),
        )

    def _on_history(self, records: list) -> None:
        """刷新左侧训练记录与「对比」页的模型列表。"""
        # 只有多套训练设置时才在记录里标注归属，单套设置下不增加噪音
        multi_setup = len(self._vm.setups()) > 1
        self.history_list.setRowCount(len(records))
        for row, record in enumerate(records):
            name = str(record.get("name", ""))
            if multi_setup and record.get("setup_name"):
                name = f"{name} · {record['setup_name']}"
            best = record.get("best_value") or 0.0
            label = str(record.get("best_label") or "")
            self.history_list.setItem(row, 0, QTableWidgetItem(name))
            self.history_list.setItem(
                row, 1,
                QTableWidgetItem(f"{label} {float(best):.4f}".strip()),
            )
        if not records:
            self.history_list.setRowCount(1)
            self.history_list.setItem(0, 0, QTableWidgetItem("暂无记录"))
            self.history_list.setItem(0, 1, QTableWidgetItem(""))

        self._syncing = True
        try:
            self.model_table.setRowCount(len(records))
            for row, record in enumerate(records):
                check = QTableWidgetItem()
                check.setFlags(
                    Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
                )
                # 默认勾选最近两次，便于直接横向对比
                check.setCheckState(
                    Qt.CheckState.Checked if row < 2 else Qt.CheckState.Unchecked
                )
                self.model_table.setItem(row, 0, check)
                self.model_table.setItem(
                    row, 1, QTableWidgetItem(str(record.get("name", "")))
                )
                self.model_table.setItem(
                    row, 2,
                    QTableWidgetItem(str(record.get("split_name") or "—")),
                )
                self.model_table.setItem(
                    row, 3,
                    QTableWidgetItem(str(record.get("setup_name") or "—")),
                )
                self.model_table.setItem(row, 4, QTableWidgetItem(
                    f"{record.get('best_label') or ''} "
                    f"{float(record.get('best_value') or 0):.4f}".strip()
                ))
            if not records:
                self.model_table.setRowCount(1)
                self.model_table.setItem(0, 0, QTableWidgetItem("暂无记录"))
        finally:
            self._syncing = False
        self._refresh_compare()

    # -----------------------------------------------------------
    # 多模型横向对比
    # -----------------------------------------------------------
    def _model_checked(self, row: int) -> bool:
        item = self.model_table.item(row, 0)
        return bool(
            item is not None
            and item.flags() & Qt.ItemFlag.ItemIsUserCheckable
            and item.checkState() == Qt.CheckState.Checked
        )

    def _on_model_check_changed(self, _item) -> None:
        if not self._syncing:
            self._refresh_compare()

    def _refresh_compare(self) -> None:
        """按勾选的训练记录重建对比曲线与指标表。"""
        records = self._vm.history()
        loss_series, metric_series, rows = [], [], []
        multi_setup = len(self._vm.setups()) > 1
        for index, record in enumerate(records):
            if not self._model_checked(index):
                continue
            curves = self._vm.run_curves(index)
            color = _METRIC_COLORS[len(rows) % len(_METRIC_COLORS)]
            label = str(record.get("name") or f"#{index + 1}")
            if multi_setup and record.get("setup_name"):
                label = f"{label} · {record['setup_name']}"
            loss = curves.get("loss")
            if loss:
                loss_series.append((label, color, loss[1]))
            metric = curves.get("metric")
            if metric:
                metric_series.append((f"{label} · {metric[0]}", color, metric[1]))
            rows.append(record)

        self.compare_loss.set_data(loss_series)
        self.compare_metric.set_data(metric_series)
        self._fill_compare_table(rows)

    def _fill_compare_table(self, rows: list) -> None:
        self.compare_models.setRowCount(len(rows))
        for index, record in enumerate(rows):
            best_label = str(record.get("best_label") or "")
            best_value = float(record.get("best_value") or 0.0)
            values = [
                str(record.get("name", "")),
                str(record.get("split_name") or "—"),
                str(record.get("setup_name") or "—"),
                str(record.get("model") or "—"),
                f"{best_label} {best_value:.4f}".strip(),
                str(record.get("best_epoch") or "—"),
                f"{float(record.get('loss') or 0):.4f}",
            ]
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                if str(record.get("results") or ""):
                    item.setToolTip(str(record.get("results")))
                self.compare_models.setItem(index, column, item)

    def _on_history_clicked(self, row: int, _column: int) -> None:
        self._vm.load_run(row)
        self._switch(1)

    def _on_model_info(self, info: dict) -> None:
        if not info:
            self.model_info.setText("未导入权重")
            return
        names = info.get("names") or {}
        labels = list(names.values()) if isinstance(names, dict) else list(names)
        self.model_info.setText(
            f"类别 {len(labels)} · 参数量 {int(info.get('params', 0)):,} · "
            f"层数 {int(info.get('layers', 0))}"
        )

    def _on_task_started(self, name: str) -> None:
        self.model_info.setText(name)

    def _on_task_failed(self, text: str) -> None:
        self.model_info.setText(text)

    def _on_status(self, status: str) -> None:
        alive = status in ("running", "paused")
        paused = status == "paused"
        self.start_btn.setEnabled(not alive)
        self.stop_btn.setEnabled(alive)
        self.pause_btn.setEnabled(alive)
        self.pause_btn.setText("继续" if paused else "暂停")
        self.continue_btn.setEnabled(not alive and bool(self._vm.history()))
        text = {
            "idle": "", "running": "训练中", "paused": "已暂停（轮边界）",
            "finished": "已完成", "error": "失败",
        }.get(status, status)
        self.state_label.setText(text)

    def _on_pause_toggle(self) -> None:
        """暂停 / 继续：按当前状态二选一。"""
        if self._vm.is_paused():
            self._vm.resume()
        else:
            self._vm.pause()

    def _on_continue_training(self) -> None:
        """在最近一次训练基础上追加轮数继续训练。"""
        self._vm.continue_training(50)

    def _on_progress(self, value: float) -> None:
        self.progress_bar.setValue(int(round(value * 100)))

    def _on_metrics(self, row: dict) -> None:
        """更新指标卡片与「上一轮 / 最新」对比表。"""
        if not row:
            for label in self._tile_values.values():
                label.setText("—")
            self.compare_table.setRowCount(0)
            return
        config = self._vm.config
        total = int(row.get("total") or config.epochs or 0)
        epochs = int(row.get("epoch") or 0)
        iterations = int(row.get("iterations") or 0)
        iteration = int(row.get("iteration") or 0)
        self._tile_values["epoch"].setText(f"{epochs} / {total}")
        self._tile_values["iteration"].setText(
            f"{iteration} / {iterations}" if iterations else str(iteration)
        )
        self._tile_values["lr"].setText(f"{float(row.get('lr') or 0):.6g}")
        self._tile_values["loss"].setText(f"{float(row.get('loss') or 0):.4f}")
        val_loss = float(row.get("val_loss") or 0.0)
        self._tile_values["val_loss"].setText(
            f"{val_loss:.4f}" if val_loss else "—"
        )
        self._tile_values["elapsed"].setText(
            _duration(float(row.get("elapsed") or 0.0))
        )
        eta = float(row.get("eta") or 0.0)
        self._tile_values["eta"].setText(_duration(eta) if eta else "—")
        label = str(row.get("main_label") or self._vm.main_label())
        best_epoch = int(row.get("best_epoch") or 0)
        best_value = float(row.get("best_value") or 0.0)
        self._tile_values["best"].setText(
            f"{label} {best_value:.4f}（Epoch {best_epoch}）" if best_epoch
            else "—"
        )
        self.progress_bar.setValue(int(round(float(row.get("progress") or 0) * 100)))
        self._update_compare(row)

    def _update_compare(self, row: dict) -> None:
        if row.get("partial"):
            return
        previous = row.get("previous") or {}
        entries = [
            ("训练损失", previous.get("loss"), row.get("loss")),
            ("验证损失", previous.get("val_loss"), row.get("val_loss")),
            (str(row.get("main_label") or "指标"),
             previous.get("main_value"), row.get("main_value")),
        ]
        label = str(row.get("main_label") or "")
        best_epoch = int(row.get("best_epoch") or 0)
        entries.append((
            f"最佳 {label}".strip(),
            previous.get("best_value"),
            f"{float(row.get('best_value') or 0):.4f}（Epoch {best_epoch}）"
            if best_epoch else row.get("best_value"),
        ))
        self.compare_table.setRowCount(len(entries))
        for index, (name, old, new) in enumerate(entries):
            self.compare_table.setItem(index, 0, QTableWidgetItem(str(name)))
            self.compare_table.setItem(
                index, 1, QTableWidgetItem(_cell(old))
            )
            self.compare_table.setItem(index, 2, QTableWidgetItem(_cell(new)))

    def _on_curves(self, payload: dict) -> None:
        """把曲线数据交给折线图（Loss + 主评价指标）。"""
        if not payload:
            self.loss_chart.clear()
            self.metric_chart.clear()
            return
        loss_series = [
            (name, _LOSS_COLORS.get(name, _METRIC_COLORS[0]), points)
            for name, points in payload.get("loss", [])
        ]
        metric_series = [
            (name, _METRIC_COLORS[index % len(_METRIC_COLORS)], points)
            for index, (name, points) in enumerate(payload.get("metric", []))
        ]
        self.loss_chart.set_data(loss_series)
        self.metric_chart.set_data(metric_series)

    def _on_log(self, line: str) -> None:
        self.log_edit.append(line)


def _cell(value) -> str:
    """对比表格里的数值格式化。"""
    if value is None or value == "":
        return "—"
    if isinstance(value, str):
        return value
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return str(value)


def _type_label(model_type: str) -> str:
    from src.utils.constants import project_type

    return str(project_type(model_type).get("label") or model_type)
