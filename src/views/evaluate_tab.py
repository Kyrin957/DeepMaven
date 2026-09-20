"""模型评估导航页：数据集评估（指标 / 混淆矩阵 / 结果缩略图 / 图像详情）与单图推理。

布局参照 Halcon DLT 的评估视图：

    左栏：评估配置（模型权重 / 评估集 / 设备 / 阈值 / 图片上限）+ 数据概览（正确·错误环形图）
    中央：综合指标（错误预测、准确率、F1、平均精确率 / 召回率、推断时间）、
          混淆矩阵（行 = 真实、列 = 预测，点击单元格可筛选）、
          结果缩略图（对错边框 + 置信度，可筛选、可翻页）
    右栏：图像详情（真实类别可修正、预测类别 / 置信度、各类别概率、原图 / 预测图）
          + 类别指标（样本数 / 精确率 / 召回率 / F1）

「推理」分页保留原有的图片 / 视频 / 相机检测能力。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
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
    LineEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    ScrollArea,
    SegmentedWidget,
    Slider,
    SpinBox,
    StrongBodyLabel,
    TableWidget,
)

from src.services.anomalib_service import AnomalibService
from src.services.evaluation_service import (
    BACKGROUND,
    FP_REASON_LABELS,
    FP_REASONS,
    MISSED,
)
from src.utils.constants import PLOT_COLORS
from src.viewmodels.evaluate_vm import EvaluateViewModel
from src.views.data_widgets import side_column
from src.views.widgets import (
    BarChart,
    ConfusionMatrixView,
    HistogramView,
    LegendList,
    PieChart,
    ThumbnailGrid,
)

# 评估结果筛选
_FILTERS = [("all", "全部"), ("wrong", "错误"), ("right", "正确")]
# 结果排序（对齐 DLT：误检高置信、正确低置信往往说明标注有误）
_SORTS = [
    ("order", "顺序"),
    ("conf_desc", "置信度 ↓"),
    ("conf_asc", "置信度 ↑"),
    ("iou_asc", "IoU ↑"),
    ("wrong_first", "错误优先"),
]
# 展示粒度：整图 / 实例（每个框单独成块）
_VIEWS = [("image", "整图"), ("instance", "实例")]
_EVAL_COLORS_OK = "#0F7B0F"
_EVAL_COLORS_BAD = "#C42B1C"


class EvaluateTab(QWidget):
    """模型评估页。"""

    def __init__(self, vm: EvaluateViewModel, parent=None):
        super().__init__(parent)
        self._vm = vm
        self._syncing = False
        self._rows: list[dict] = []          # 评估结果全部记录
        self._view_indexes: list[int] = []   # 当前筛选后展示的记录下标
        self._cell_filter: tuple[int, int] | None = None
        self._preview_mode = "pred"          # pred / raw / overlay（GT 叠加）
        self._instance_mode = False          # 展示粒度：整图 / 实例
        self._instances: list[dict] = []     # 当前可见的实例条目
        self._build_ui()
        self._bind()
        self._on_config(self._vm.config)

    # -----------------------------------------------------------
    # 界面
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

    # --------------------------------------------------- 左栏
    def _build_side(self) -> QWidget:
        config_card, layout = self._card("评估配置")

        row = QHBoxLayout()
        self.weights_edit = LineEdit(config_card)
        self.weights_edit.setPlaceholderText("模型权重 (.pt / .ckpt)")
        row.addWidget(self.weights_edit, 1)
        weights_btn = PushButton("浏览", config_card)
        weights_btn.clicked.connect(self._on_browse_weights)
        row.addWidget(weights_btn)
        layout.addLayout(row)

        self.history_combo = ComboBox(config_card)
        self.history_combo.setPlaceholderText("最近训练")
        self.history_combo.currentIndexChanged.connect(self._on_pick_history)
        layout.addWidget(self.history_combo)

        form = QFormLayout()
        form.setSpacing(8)
        self.source_combo = ComboBox(config_card)
        self.source_combo.currentIndexChanged.connect(self._on_eval_source_picked)
        form.addRow("数据拆分", self.source_combo)

        # 评估图像集可多选（训练 / 验证 / 测试），对齐 DLT 的评估设置
        subsets_row = QHBoxLayout()
        subsets_row.setSpacing(10)
        self.subset_checks: dict[str, CheckBox] = {}
        for key, text in (("train", "训练"), ("val", "验证"), ("test", "测试")):
            box = CheckBox(text, config_card)
            box.toggled.connect(self._on_subsets_changed)
            subsets_row.addWidget(box)
            self.subset_checks[key] = box
        subsets_row.addStretch(1)
        form.addRow("图像集", subsets_row)

        random_row = QHBoxLayout()
        self.random_check = CheckBox("随机抽样", config_card)
        self.random_check.setToolTip("达到图片上限时随机抽取（相同种子结果可复现）")
        self.random_check.toggled.connect(self._on_random_changed)
        random_row.addWidget(self.random_check)
        self.seed_spin = SpinBox(config_card)
        self.seed_spin.setRange(0, 999999)
        self.seed_spin.setToolTip("随机种子（相同种子结果可复现）")
        self.seed_spin.valueChanged.connect(self._on_seed_changed)
        random_row.addWidget(self.seed_spin)
        random_row.addStretch(1)
        form.addRow("抽样", random_row)

        folder_row = QHBoxLayout()
        self.folder_edit = LineEdit(config_card)
        self.folder_edit.setPlaceholderText("或指定目录")
        folder_row.addWidget(self.folder_edit, 1)
        folder_btn = PushButton("浏览", config_card)
        folder_btn.clicked.connect(self._on_browse_folder)
        folder_row.addWidget(folder_btn)
        form.addRow("自定义", folder_row)

        self.device_combo = ComboBox(config_card)
        self.device_combo.addItems(["auto", "cpu", "cuda:0"])
        form.addRow("设备", self.device_combo)

        self.limit_spin = SpinBox(config_card)
        self.limit_spin.setRange(1, 5000)
        self.limit_spin.setValue(200)
        self.limit_spin.setSuffix(" 张")
        form.addRow("图片上限", self.limit_spin)

        self.anomaly_combo = ComboBox(config_card)
        for name in AnomalibService.available_models():
            self.anomaly_combo.addItem(name, userData=name)
        form.addRow("异常模型", self.anomaly_combo)
        layout.addLayout(form)

        self.conf_slider, self.conf_val = self._make_threshold(
            "置信度", 0.25, layout, config_card
        )
        self.iou_slider, self.iou_val = self._make_threshold(
            "IOU", 0.45, layout, config_card
        )

        buttons = QHBoxLayout()
        self.run_btn = PrimaryPushButton("开始评估", config_card)
        self.stop_btn = PushButton("停止", config_card)
        self.export_btn = PushButton("导出报告", config_card)
        buttons.addWidget(self.run_btn)
        buttons.addWidget(self.stop_btn)
        buttons.addWidget(self.export_btn)
        self.ood_btn = PushButton("OOD 检测", config_card)
        self.ood_btn.setToolTip(
            "用已知样本拟合特征分布，判定新图是否为分布外样本（需分类模型）"
        )
        self.ood_btn.clicked.connect(self._on_ood)
        buttons.addWidget(self.ood_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.progress_bar = ProgressBar(config_card)
        self.progress_bar.setRange(0, 100)
        layout.addWidget(self.progress_bar)
        self.task_label = CaptionLabel("", config_card)
        layout.addWidget(self.task_label)

        # 数据概览：正确 / 错误环形图
        overview_card, overview_layout = self._card("数据概览")
        self.pie = PieChart(overview_card)
        self.pie.setFixedHeight(160)
        overview_layout.addWidget(self.pie)
        self.legend = LegendList(overview_card)
        overview_layout.addWidget(self.legend)

        # 异常后处理：分数直方图 + 分类阈值 / 分数容忍度（仅异常任务显示）
        self.anomaly_card, anomaly_layout = self._card("异常检测")
        self.hist_view = HistogramView(self.anomaly_card)
        self.hist_view.thresholdChanged.connect(self._on_hist_threshold)
        anomaly_layout.addWidget(self.hist_view)

        threshold_row = QHBoxLayout()
        threshold_row.addWidget(CaptionLabel("分类阈值", self.anomaly_card))
        self.anomaly_value = CaptionLabel("—", self.anomaly_card)
        threshold_row.addWidget(self.anomaly_value, 1)
        anomaly_layout.addLayout(threshold_row)
        self.anomaly_slider = Slider(Qt.Orientation.Horizontal, self.anomaly_card)
        self.anomaly_slider.setRange(0, 100)
        self.anomaly_slider.valueChanged.connect(self._on_anomaly_threshold)
        anomaly_layout.addWidget(self.anomaly_slider)

        threshold_buttons = QHBoxLayout()
        median_btn = PushButton("中位阈值", self.anomaly_card)
        median_btn.setToolTip("用所有样本异常分数的中位数作为阈值")
        median_btn.clicked.connect(lambda: self._vm.use_median_threshold())
        threshold_buttons.addWidget(median_btn)
        best_btn = PushButton("最优阈值", self.anomaly_card)
        best_btn.setToolTip("按真实标签搜索 F1 最大的分割点")
        best_btn.clicked.connect(lambda: self._vm.use_best_threshold())
        threshold_buttons.addWidget(best_btn)
        threshold_buttons.addStretch(1)
        anomaly_layout.addLayout(threshold_buttons)

        tolerance_row = QHBoxLayout()
        tolerance_row.addWidget(CaptionLabel("分数容忍度", self.anomaly_card))
        self.tolerance_value = CaptionLabel("0%", self.anomaly_card)
        tolerance_row.addWidget(self.tolerance_value, 1)
        anomaly_layout.addLayout(tolerance_row)
        self.tolerance_slider = Slider(Qt.Orientation.Horizontal, self.anomaly_card)
        self.tolerance_slider.setRange(0, 50)
        self.tolerance_slider.setToolTip("容忍度越大，越少的样本被判为异常")
        self.tolerance_slider.valueChanged.connect(self._on_anomaly_tolerance)
        anomaly_layout.addWidget(self.tolerance_slider)

        min_size_row = QHBoxLayout()
        min_size_row.addWidget(CaptionLabel("最小缺陷尺寸", self.anomaly_card))
        self.min_size_spin = SpinBox(self.anomaly_card)
        self.min_size_spin.setRange(0, 100000)
        self.min_size_spin.setSuffix(" px")
        self.min_size_spin.setToolTip(
            "小于该尺寸的高分区域视为噪声（需要异常热力图；无热力图时按分数判定）"
        )
        self.min_size_spin.valueChanged.connect(self._on_min_size)
        min_size_row.addWidget(self.min_size_spin, 1)
        anomaly_layout.addLayout(min_size_row)

        self.anomaly_hint = CaptionLabel("", self.anomaly_card)
        self.anomaly_hint.setWordWrap(True)
        anomaly_layout.addWidget(self.anomaly_hint)
        self.anomaly_card.setVisible(False)

        return side_column(
            config_card, overview_card, self.anomaly_card, width=310
        )

    def _make_threshold(self, label, default, layout, parent):
        row = QHBoxLayout()
        row.addWidget(CaptionLabel(label, parent))
        value = CaptionLabel(f"{default:.2f}", parent)
        row.addWidget(value, 1)
        slider = Slider(Qt.Orientation.Horizontal, parent)
        slider.setRange(0, 100)
        slider.setValue(int(default * 100))
        slider.valueChanged.connect(
            lambda raw: value.setText(f"{raw / 100.0:.2f}")
        )
        layout.addLayout(row)
        layout.addWidget(slider)
        return slider, value

    # --------------------------------------------------- 中央
    def _build_main(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(10)

        header = CardWidget(self)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 10, 16, 10)
        self.tab_seg = SegmentedWidget(header)
        self.tab_seg.addItem("eval", "评估", onClick=lambda: self._switch(0))
        self.tab_seg.addItem("demo", "推理", onClick=lambda: self._switch(1))
        self.tab_seg.setCurrentItem("eval")
        header_layout.addWidget(self.tab_seg)
        header_layout.addStretch(1)
        self.state_label = CaptionLabel("", header)
        header_layout.addWidget(self.state_label)
        column.addWidget(header)

        self.stack = QStackedWidget(self)
        self.stack.addWidget(self._build_eval_pane())
        self.stack.addWidget(self._build_demo_pane())
        column.addWidget(self.stack, 1)
        return column

    def _switch(self, index: int) -> None:
        self.stack.setCurrentIndex(index)

    def _build_eval_pane(self) -> QWidget:
        pane = QWidget(self)
        layout = QHBoxLayout(pane)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addLayout(self._build_center_column(), 1)
        layout.addWidget(self._build_detail_column(), 0)
        return pane

    def _build_center_column(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(10)
        column.addWidget(self._build_metric_card())
        column.addWidget(self._build_matrix_card())
        column.addWidget(self._build_images_card(), 1)
        return column

    def _build_metric_card(self) -> CardWidget:
        card, layout = self._card("综合指标")
        tiles = [
            ("错误预测", "wrong"), ("准确率", "accuracy"), ("Top-1 错误率", "top1"),
            ("平均精确率", "precision"), ("平均召回率", "recall"), ("F1 分数", "f1"),
            ("平均推断时间", "avg_ms"), ("已用时间", "elapsed"),
        ]
        grid = QGridLayout()
        grid.setSpacing(10)
        self._tiles: dict[str, BodyLabel] = {}
        for index, (title, key) in enumerate(tiles):
            box = QWidget(card)
            inner = QVBoxLayout(box)
            inner.setContentsMargins(0, 0, 0, 0)
            inner.setSpacing(2)
            inner.addWidget(CaptionLabel(title, box))
            value = BodyLabel("—", box)
            inner.addWidget(value)
            grid.addWidget(box, index // 4, index % 4)
            self._tiles[key] = value
        layout.addLayout(grid)

        # 误检细分（类别错 / 无重叠 / 定位不准 / 重复 / 多重错误）—— 仅检测族显示
        self.fp_row = QWidget(card)
        fp_layout = QHBoxLayout(self.fp_row)
        fp_layout.setContentsMargins(0, 0, 0, 0)
        fp_layout.setSpacing(10)
        self._fp_tiles: dict[str, BodyLabel] = {}
        for key, label in FP_REASONS + (("total", "合计"),):
            box = QWidget(self.fp_row)
            inner = QVBoxLayout(box)
            inner.setContentsMargins(0, 0, 0, 0)
            inner.setSpacing(2)
            inner.addWidget(CaptionLabel(f"误检 · {label}", box))
            value = BodyLabel("—", box)
            inner.addWidget(value)
            fp_layout.addWidget(box)
            self._fp_tiles[key] = value
        fp_layout.addStretch(1)
        self.fp_row.setVisible(False)
        layout.addWidget(self.fp_row)
        return card

    def _build_matrix_card(self) -> CardWidget:
        card, layout = self._card("混淆矩阵")
        self.matrix_view = ConfusionMatrixView(card)
        self.matrix_view.cellClicked.connect(self._on_cell_clicked)
        layout.addWidget(self.matrix_view)
        self.matrix_hint = CaptionLabel("", card)
        layout.addWidget(self.matrix_hint)
        return card

    def _build_images_card(self) -> CardWidget:
        card, layout = self._card("图像")
        tools = QHBoxLayout()
        self.filter_combo = ComboBox(card)
        for key, text in _FILTERS:
            self.filter_combo.addItem(text, userData=key)
        self.filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        tools.addWidget(self.filter_combo)

        self.class_combo = ComboBox(card)
        self.class_combo.addItem("全部类别", userData=None)
        self.class_combo.currentIndexChanged.connect(self._on_filter_changed)
        tools.addWidget(self.class_combo)

        self.sort_combo = ComboBox(card)
        for key, text in _SORTS:
            self.sort_combo.addItem(text, userData=key)
        self.sort_combo.setToolTip(
            "结果排序：误检高置信 / 正确低置信往往说明标注有误"
        )
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        tools.addWidget(self.sort_combo)

        self.view_combo = ComboBox(card)
        for key, text in _VIEWS:
            self.view_combo.addItem(text, userData=key)
        self.view_combo.setToolTip("展示粒度：整图 / 实例（每个预测框与真实框单独成块）")
        self.view_combo.currentIndexChanged.connect(self._on_view_changed)
        tools.addWidget(self.view_combo)
        tools.addStretch(1)

        self.page_label = CaptionLabel("", card)
        tools.addWidget(self.page_label)
        self.page_buttons: dict[str, PushButton] = {}
        for key, text in (("first", "|◀"), ("prev", "◀"), ("next", "▶"), ("last", "▶|")):
            button = PushButton(text, card)
            button.setFixedWidth(38)
            button.clicked.connect(lambda _c=False, name=key: self._on_page(name))
            tools.addWidget(button)
            self.page_buttons[key] = button
        layout.addLayout(tools)

        self.grid = ThumbnailGrid(card)
        self.grid.set_thumb_size(124)
        self.grid.setMinimumHeight(220)
        self.grid.imageActivated.connect(self._on_grid_activated)
        layout.addWidget(self.grid, 1)
        return card

    def _build_detail_column(self) -> QWidget:
        detail_card, layout = self._card("图像详情")

        self.detail_name = BodyLabel("—", detail_card)
        self.detail_name.setWordWrap(True)
        layout.addWidget(self.detail_name)

        form = QFormLayout()
        form.setSpacing(8)
        self.true_combo = ComboBox(detail_card)
        self.true_combo.currentIndexChanged.connect(self._on_true_label_changed)
        form.addRow("真实类别", self.true_combo)
        self.subset_value = BodyLabel("—", detail_card)
        form.addRow("图像集", self.subset_value)
        self.state_value = BodyLabel("—", detail_card)
        form.addRow("预测状态", self.state_value)
        self.pred_value = BodyLabel("—", detail_card)
        form.addRow("预测类别", self.pred_value)
        self.conf_value = BodyLabel("—", detail_card)
        form.addRow("置信度", self.conf_value)
        self.time_value = BodyLabel("—", detail_card)
        form.addRow("推断时间", self.time_value)
        layout.addLayout(form)

        self.preview = QLabel(detail_card)
        self.preview.setMinimumHeight(150)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setStyleSheet("background:#1B1B1B; border-radius:4px;")
        layout.addWidget(self.preview)

        toggle = QHBoxLayout()
        self.preview_buttons: dict[str, PushButton] = {}
        for key, text in (
            ("raw", "原图"), ("pred", "预测图"),
            ("overlay", "GT 叠加"), ("preprocess", "预处理"), ("heat", "热图"),
            ("gradcam", "Grad-CAM"),
        ):
            button = PushButton(text, detail_card)
            button.clicked.connect(lambda _c=False, name=key: self._set_preview(name))
            toggle.addWidget(button)
            self.preview_buttons[key] = button
        toggle.addStretch(1)
        layout.addLayout(toggle)
        # Grad-CAM 需要分类模型，默认禁用（选中分类结果后按需启用）
        if "gradcam" in self.preview_buttons:
            self.preview_buttons["gradcam"].setEnabled(False)

        self.prob_chart = BarChart(detail_card)
        self.prob_chart.setFixedHeight(96)
        layout.addWidget(self.prob_chart)

        metrics_card, metrics_layout = self._card("类别指标")
        self.class_table = TableWidget(metrics_card)
        self.class_table.setColumnCount(5)
        self.class_table.setHorizontalHeaderLabels(
            ["类别", "样本", "精确率", "召回率", "F1"]
        )
        self.class_table.verticalHeader().setVisible(False)
        self.class_table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.class_table.setMinimumHeight(180)
        metrics_layout.addWidget(self.class_table)

        holder = QWidget(self)
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(10)
        column.addWidget(detail_card)
        column.addWidget(metrics_card, 1)
        holder.setFixedWidth(300)
        return holder

    # --------------------------------------------------- 推理分页（原检测能力）
    def _build_demo_pane(self) -> QWidget:
        area = ScrollArea(self)
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(10)
        layout.addWidget(self._build_input_card())
        layout.addWidget(self._build_result_card())
        layout.addStretch(1)
        area.setWidget(body)
        return area

    def _build_input_card(self) -> CardWidget:
        card, layout = self._card("检测输入")
        form = QFormLayout()

        weights_row = QHBoxLayout()
        self.weights_edit2 = LineEdit(card)
        self.weights_edit2.setPlaceholderText("模型权重 (.pt / .ckpt)")
        weights_row.addWidget(self.weights_edit2, 1)
        weights_btn = PushButton("浏览", card)
        weights_btn.clicked.connect(self._on_browse_weights2)
        weights_row.addWidget(weights_btn)
        form.addRow("模型权重", weights_row)

        self.source_combo2 = ComboBox(card)
        for key, text in (("image", "图片"), ("video", "视频"), ("camera", "相机")):
            self.source_combo2.addItem(text, userData=key)
        self.source_combo2.currentIndexChanged.connect(self._on_source_changed)
        form.addRow("输入源", self.source_combo2)

        path_row = QHBoxLayout()
        self.path_edit = LineEdit(card)
        self.path_edit.setPlaceholderText("图片 / 视频文件路径")
        path_row.addWidget(self.path_edit, 1)
        self.path_btn = PushButton("浏览", card)
        self.path_btn.clicked.connect(self._on_browse_source)
        path_row.addWidget(self.path_btn)
        form.addRow("输入路径", path_row)
        layout.addLayout(form)

        row = QHBoxLayout()
        self.detect_btn = PrimaryPushButton("开始检测", card)
        self.detect_stop_btn = PushButton("停止", card)
        self.detect_export_btn = PushButton("导出报告", card)
        row.addWidget(self.detect_btn)
        row.addWidget(self.detect_stop_btn)
        row.addWidget(self.detect_export_btn)
        row.addStretch(1)
        self.detect_progress = ProgressBar(card)
        self.detect_progress.setRange(0, 100)
        self.detect_progress.setFixedWidth(150)
        row.addWidget(self.detect_progress)
        layout.addLayout(row)
        return card

    def _build_result_card(self) -> CardWidget:
        card, layout = self._card("检测结果")
        self.result_image = QLabel(card)
        self.result_image.setMinimumHeight(240)
        self.result_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.result_image)
        self.result_label = CaptionLabel("", card)
        layout.addWidget(self.result_label)
        self.table = TableWidget(card)
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(
            ["类别", "置信度", "x1", "y1", "x2", "y2"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(200)
        layout.addWidget(self.table)
        return card

    # -----------------------------------------------------------
    # 绑定
    # -----------------------------------------------------------
    def _bind(self) -> None:
        self.run_btn.clicked.connect(self._on_run)
        self.stop_btn.clicked.connect(self._vm.stop)
        self.export_btn.clicked.connect(self._on_export_eval)
        self.weights_edit.editingFinished.connect(self._on_weights_changed)
        self.device_combo.currentIndexChanged.connect(self._on_params_changed)
        self.limit_spin.valueChanged.connect(self._on_params_changed)

        self.detect_btn.clicked.connect(self._on_detect)
        self.detect_stop_btn.clicked.connect(self._vm.stop)
        self.detect_export_btn.clicked.connect(self._on_export_demo)

        self._vm.evaluationReady.connect(self._on_evaluation)
        self._vm.rowSelected.connect(self._on_row_selected)
        self._vm.resultReady.connect(self._on_result)
        self._vm.cameraFrame.connect(self._on_camera_frame)
        self._vm.configChanged.connect(self._on_config)
        self._vm.taskStarted.connect(self._on_task_started)
        self._vm.taskProgress.connect(self._on_task_progress)
        self._vm.taskFinished.connect(self._on_task_finished)
        self._vm.taskFailed.connect(self._on_task_failed)

    # -----------------------------------------------------------
    # 配置同步
    # -----------------------------------------------------------
    def _on_config(self, config) -> None:
        self._syncing = True
        try:
            if config.weights_path and self.weights_edit.text() != config.weights_path:
                self.weights_edit.setText(config.weights_path)
            if self.weights_edit2.text() != config.weights_path:
                self.weights_edit2.setText(config.weights_path)
            index = self.device_combo.findText(config.device)
            if index >= 0:
                self.device_combo.setCurrentIndex(index)
            self.limit_spin.setValue(int(config.max_images or 200))
            self.folder_edit.setText(config.eval_folder or "")
            self._reload_sources(config)
            subsets = list(config.eval_subsets) or [config.eval_subset or "val"]
            for key, box in self.subset_checks.items():
                box.blockSignals(True)
                box.setChecked(key in subsets)
                box.blockSignals(False)
            self.random_check.blockSignals(True)
            self.random_check.setChecked(bool(config.eval_random))
            self.random_check.blockSignals(False)
            self.seed_spin.blockSignals(True)
            self.seed_spin.setValue(int(config.eval_seed or 0))
            self.seed_spin.setEnabled(bool(config.eval_random))
            self.seed_spin.blockSignals(False)
            self.sort_combo.blockSignals(True)
            self.sort_combo.setCurrentIndex(max(
                0, [key for key, _text in _SORTS].index(config.eval_sort)
                if config.eval_sort in [key for key, _text in _SORTS] else 0
            ))
            self.sort_combo.blockSignals(False)
            self._reload_history(config.weights_path)
        finally:
            self._syncing = False

    def _reload_sources(self, config) -> None:
        """「数据拆分」下拉：项目里已生成的拆分（子集由多选框决定）。"""
        sources = self._vm.eval_splits() or self._vm.eval_sources()
        self.source_combo.clear()
        if not sources:
            self.source_combo.addItem("暂无已生成的拆分", userData=None)
        for item in sources:
            data = {
                "split_id": int(item.get("split_id") or 0),
                "subset": str(item.get("subset") or ""),
            }
            self.source_combo.addItem(str(item.get("label") or ""), userData=data)
        index = next(
            (
                row for row, item in enumerate(sources)
                if int(item.get("split_id") or 0) == int(config.eval_split_id)
            ),
            0,
        )
        if sources:
            self.source_combo.setCurrentIndex(index)

    def _reload_history(self, weights: str) -> None:
        """「最近训练」下拉：项目里训练产出的 best.pt。"""
        project = self._vm.project
        records = list(getattr(project.training, "history", []) or []) if project else []
        records = [item for item in records if item.get("best_weights")]
        self.history_combo.clear()
        for record in reversed(records[-8:]):
            label = (
                f"{record.get('name', '')} · "
                f"{record.get('best_label') or '指标'} "
                f"{float(record.get('best_value') or 0):.3f}"
            )
            self.history_combo.addItem(label, userData=record.get("best_weights"))
        self.history_combo.setVisible(bool(records))
        if weights:
            for index in range(self.history_combo.count()):
                if str(self.history_combo.itemData(index)) == weights:
                    self.history_combo.setCurrentIndex(index)
                    break

    # -----------------------------------------------------------
    # 交互
    # -----------------------------------------------------------
    def _on_browse_weights(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择模型权重", "", "模型权重 (*.pt *.ckpt)"
        )
        if path:
            self.weights_edit.setText(path)
            self._on_weights_changed()

    def _on_browse_weights2(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择模型权重", "", "模型权重 (*.pt *.ckpt)"
        )
        if path:
            self.weights_edit2.setText(path)
            self._vm.set_weights(path)

    def _on_browse_folder(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择评估目录")
        if directory:
            self.folder_edit.setText(directory)
            self._vm.set_eval_folder(directory)

    def _on_pick_history(self, index: int) -> None:
        if self._syncing or index < 0:
            return
        path = str(self.history_combo.itemData(index) or "")
        if path:
            self.weights_edit.setText(path)
            self._on_weights_changed()

    def _on_weights_changed(self) -> None:
        if self._syncing:
            return
        self._vm.set_weights(self.weights_edit.text().strip())

    def _on_eval_source_picked(self, _index: int) -> None:
        if self._syncing:
            return
        item = self.source_combo.currentData()
        if not isinstance(item, dict):
            return
        self.folder_edit.clear()
        split_id = int(item.get("split_id") or 0)
        if "counts" in item or not item.get("subset"):
            self._vm.set_eval_split(split_id)
            self._vm.set_eval_subsets(self._checked_subsets() or ["val"])
        else:
            self._vm.set_eval_source(split_id, str(item["subset"]))

    def _checked_subsets(self) -> list[str]:
        return [key for key, box in self.subset_checks.items() if box.isChecked()]

    def _on_subsets_changed(self, *_args) -> None:
        if self._syncing:
            return
        self._vm.set_eval_subsets(self._checked_subsets() or ["val"])

    def _on_random_changed(self, checked: bool) -> None:
        if self._syncing:
            return
        self.seed_spin.setEnabled(bool(checked))
        self._vm.set_eval_random(bool(checked))

    def _on_seed_changed(self, value: int) -> None:
        if self._syncing:
            return
        self._vm.set_eval_seed(int(value))

    def _on_params_changed(self, *_args) -> None:
        if self._syncing:
            return
        self._vm.set_device(self.device_combo.currentText())
        self._vm.set_max_images(self.limit_spin.value())

    def _on_run(self) -> None:
        self._vm.set_weights(self.weights_edit.text().strip())
        self._vm.set_device(self.device_combo.currentText())
        self._vm.set_max_images(self.limit_spin.value())
        self._vm.set_eval_folder(self.folder_edit.text().strip())
        self._vm.set_thresholds(
            self.conf_slider.value() / 100.0, self.iou_slider.value() / 100.0
        )
        self._vm.set_anomaly_model(self.anomaly_combo.currentData())
        self._vm.run_evaluation()

    def _on_ood(self) -> None:
        """打开 OOD 检测弹窗（沿用当前模型权重）。"""
        from src.views.dialogs import OodDialog

        OodDialog(self, self._vm.config.weights_path).exec()

    def _on_export_eval(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出评估报告", "evaluation.csv",
            "CSV (*.csv);;Excel (*.xlsx)",
        )
        if path:
            self._vm.export_eval_report(path)

    # --------------------------------------------------- 推理页交互
    def _on_browse_source(self) -> None:
        kind = self.source_combo2.currentData()
        if kind == "video":
            path, _ = QFileDialog.getOpenFileName(
                self, "选择视频", "",
                "视频 (*.mp4 *.avi *.mov *.mkv);;所有文件 (*)",
            )
        else:
            path, _ = QFileDialog.getOpenFileName(
                self, "选择图片", "",
                "图片 (*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp)",
            )
        if path:
            self.path_edit.setText(path)
            self._vm.set_source_path(path)

    def _on_source_changed(self) -> None:
        kind = self.source_combo2.currentData()
        self._vm.set_source(kind)
        is_camera = kind == "camera"
        self.path_edit.setEnabled(not is_camera)
        self.path_btn.setEnabled(not is_camera)
        if is_camera:
            self.path_edit.setText("0")
            self.path_edit.setPlaceholderText("相机设备序号")
            self._vm.set_source_path("0")
        else:
            self.path_edit.setPlaceholderText("图片 / 视频文件路径")

    def _on_detect(self) -> None:
        self._vm.set_weights(self.weights_edit2.text().strip())
        self._vm.set_source(self.source_combo2.currentData())
        self._vm.set_source_path(self.path_edit.text().strip())
        self._vm.set_device(self.device_combo.currentText())
        self._vm.set_anomaly_model(self.anomaly_combo.currentData())
        self._vm.set_thresholds(
            self.conf_slider.value() / 100.0, self.iou_slider.value() / 100.0
        )
        self._vm.run_detection()

    def _on_export_demo(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出检测报告", "detections.csv",
            "CSV (*.csv);;Excel (*.xlsx)",
        )
        if path:
            self._vm.export_report(path)

    # -----------------------------------------------------------
    # 评估结果
    # -----------------------------------------------------------
    def _on_evaluation(self, result: dict) -> None:
        self._rows = list(result.get("rows") or [])
        names = [str(name) for name in (result.get("class_names") or [])]
        metrics = result.get("metrics") or {}
        self._cell_filter = None
        self.matrix_view.clear_selection()

        if not self._rows:
            self._clear_evaluation()
            return

        colors = self._vm.class_colors(names)
        self._instances = []
        self._reload_true_combo(names)
        self._reload_class_filter(names)
        self._fill_tiles(metrics)
        self._fill_fp_summary(metrics)
        self._fill_overview(metrics)
        self.matrix_view.set_data(result.get("matrix") or [], names, colors)
        self._fill_class_table(metrics.get("per_class") or [], names, colors)
        self._fill_matrix_hint(result, names, colors)
        self._fill_anomaly(result)
        # 重新下发结果时（例如调阈值、改真实标签）保持当前选中行不变
        keep = self._vm.eval_index()
        self._apply_filter(select_first=False)
        if self._view_indexes:
            self._select_view(
                self._view_indexes.index(keep) if keep in self._view_indexes else 0
            )

    def _clear_evaluation(self) -> None:
        for label in self._tiles.values():
            label.setText("—")
        self.fp_row.setVisible(False)
        self.anomaly_card.setVisible(False)
        self.hist_view.set_data([])
        self._instances = []
        self.pie.set_data([], "")
        self.legend.set_data([])
        self.matrix_view.set_data([], [])
        self.matrix_hint.setText("")
        self.class_table.setRowCount(0)
        self.grid.set_images([])
        self.page_label.setText("")
        self.prob_chart.set_data([])
        self.preview.clear()

    def _fill_tiles(self, metrics: dict) -> None:
        self._tiles["wrong"].setText(str(int(metrics.get("wrong") or 0)))
        self._tiles["accuracy"].setText(self._percent(metrics.get("accuracy")))
        self._tiles["top1"].setText(self._percent(metrics.get("top1_error")))
        self._tiles["precision"].setText(self._percent(metrics.get("precision")))
        self._tiles["recall"].setText(self._percent(metrics.get("recall")))
        self._tiles["f1"].setText(self._percent(metrics.get("f1")))
        self._tiles["avg_ms"].setText(f"{float(metrics.get('avg_ms') or 0):.3f} ms")
        self._tiles["elapsed"].setText(f"{float(metrics.get('elapsed') or 0):.2f} s")

    @staticmethod
    def _percent(value) -> str:
        return f"{float(value or 0) * 100:.2f}%"

    def _fill_fp_summary(self, metrics: dict) -> None:
        """误检细分汇总（仅检测族显示：整图/实例两种粒度都用得上）。"""
        summary = metrics.get("fp_summary") or {}
        show = bool(summary) and bool(self._vm.eval_result.get("with_background"))
        self.fp_row.setVisible(show)
        if not show:
            return
        for key, _label in FP_REASONS:
            self._fp_tiles[key].setText(str(int(summary.get(key) or 0)))
        self._fp_tiles["total"].setText(str(int(summary.get("total") or 0)))

    def _fill_anomaly(self, result: dict) -> None:
        """异常检测：分数直方图 + 阈值 / 容忍度回填（仅异常任务显示该卡片）。"""
        show = str(result.get("task") or "") == "anomaly" and bool(result.get("rows"))
        self.anomaly_card.setVisible(show)
        if not show:
            return
        threshold = self._vm.anomaly_threshold()
        tolerance = self._vm.anomaly_tolerance()
        self._syncing = True
        try:
            self.anomaly_slider.setValue(int(round(threshold * 100)))
            self.tolerance_slider.setValue(int(round(tolerance * 100)))
            self.min_size_spin.setValue(int(self._vm.anomaly_min_size()))
        finally:
            self._syncing = False
        self.anomaly_value.setText(f"{threshold:.3f}")
        self.tolerance_value.setText(f"{int(round(tolerance * 100))}%")
        self.hist_view.set_data(
            self._vm.anomaly_scores(), threshold,
            self._vm.anomaly_median(), self._vm.anomaly_best_threshold(),
        )
        metrics = result.get("metrics") or {}
        rows = result.get("rows") or []
        flagged = sum(1 for row in rows if int(row.get("pred_id") or 0) == 1)
        truth = sum(1 for row in rows if int(row.get("true_id") or 0) == 1)
        self.anomaly_hint.setText(
            f"判定异常 {flagged} 张 · 实际异常 {truth} 张 · "
            f"准确率 {self._percent(metrics.get('accuracy'))}"
        )

    def _on_anomaly_threshold(self, raw: int) -> None:
        if self._syncing:
            return
        self._vm.set_anomaly_threshold(int(raw) / 100.0)

    def _on_hist_threshold(self, value: float) -> None:
        """拖动 / 点击直方图上的阈值线。"""
        if self._syncing:
            return
        self._vm.set_anomaly_threshold(float(value))

    def _on_anomaly_tolerance(self, raw: int) -> None:
        if self._syncing:
            return
        self._vm.set_anomaly_tolerance(int(raw) / 100.0)

    def _on_min_size(self, value: int) -> None:
        if self._syncing:
            return
        self._vm.set_anomaly_min_size(int(value))

    def _fill_overview(self, metrics: dict) -> None:
        correct = int(metrics.get("correct") or 0)
        wrong = int(metrics.get("wrong") or 0)
        total = int(metrics.get("total") or 0)
        self.pie.set_data(
            [("正确", correct, _EVAL_COLORS_OK), ("错误", wrong, _EVAL_COLORS_BAD)],
            str(total),
        )
        self.legend.set_data([
            (_EVAL_COLORS_OK, "正确", str(correct)),
            (_EVAL_COLORS_BAD, "错误", str(wrong)),
        ])

    def _fill_class_table(self, per_class: list, names: list, colors: list) -> None:
        self.class_table.setRowCount(len(per_class))
        for row, item in enumerate(per_class):
            name = str(item.get("name", ""))
            color = colors[row] if row < len(colors) else PLOT_COLORS[0]
            label = QTableWidgetItem(name)
            label.setForeground(Qt.GlobalColor.white)
            label.setToolTip(color)
            values = [
                label,
                QTableWidgetItem(str(int(item.get("support") or 0))),
                QTableWidgetItem(self._percent(item.get("precision"))),
                QTableWidgetItem(self._percent(item.get("recall"))),
                QTableWidgetItem(self._percent(item.get("f1"))),
            ]
            for column, cell in enumerate(values):
                self.class_table.setItem(row, column, cell)

    def _fill_matrix_hint(self, result: dict, names: list, colors: list) -> None:
        """矩阵下方：每类 TP / FP / FN 与漏检、误检计数。"""
        per_class = (result.get("metrics") or {}).get("per_class") or []
        parts = []
        for index, item in enumerate(per_class):
            parts.append(
                f"{names[index]} TP{int(item.get('tp') or 0)} "
                f"FP{int(item.get('fp') or 0)} FN{int(item.get('fn') or 0)}"
            )
        if result.get("with_background") and result.get("matrix"):
            matrix = result["matrix"]
            fp_total = sum(matrix[-1]) if len(matrix) > len(names) else 0
            fn_total = sum(row[-1] for row in matrix) if len(matrix[0]) > len(names) else 0
            parts.append(f"{BACKGROUND} {fp_total} · {MISSED} {fn_total}")
        self.matrix_hint.setText("　".join(parts))

    def _reload_true_combo(self, names: list) -> None:
        self._syncing = True
        try:
            self.true_combo.clear()
            for name in names:
                self.true_combo.addItem(name, userData=name)
        finally:
            self._syncing = False

    def _reload_class_filter(self, names: list) -> None:
        self._syncing = True
        try:
            self.class_combo.clear()
            self.class_combo.addItem("全部类别", userData=None)
            for name in names:
                self.class_combo.addItem(name, userData=name)
        finally:
            self._syncing = False

    # --------------------------------------------------- 筛选与导航
    def _on_filter_changed(self, *_args) -> None:
        if self._syncing:
            return
        self._cell_filter = None
        self.matrix_view.clear_selection()
        self._apply_filter(select_first=True)

    def _on_sort_changed(self, _index: int) -> None:
        """结果排序（置信度 / IoU / 错误优先）。"""
        if self._syncing:
            return
        self._vm.set_eval_sort(str(self.sort_combo.currentData() or "order"))
        self._rows = list(self._vm.eval_result.get("rows") or [])
        self._instance_mode = False
        self.view_combo.blockSignals(True)
        self.view_combo.setCurrentIndex(0)
        self.view_combo.blockSignals(False)
        self._apply_filter(select_first=True)

    def _on_view_changed(self, _index: int) -> None:
        """展示粒度：整图 / 实例。"""
        if self._syncing:
            return
        self._instance_mode = (
            str(self.view_combo.currentData() or "image") == "instance"
        )
        self._apply_filter(select_first=True)

    def _on_cell_clicked(self, row: int, col: int) -> None:
        """点击混淆矩阵单元格：筛选出该「真实 → 预测」组合的图片。"""
        self._cell_filter = (int(row), int(col))
        self._syncing = True
        try:
            self.filter_combo.setCurrentIndex(0)
            self.class_combo.setCurrentIndex(0)
        finally:
            self._syncing = False
        self._apply_filter(select_first=True)

    def _row_matches(self, row: dict, names: list) -> bool:
        mode = self.filter_combo.currentData()
        if mode == "wrong" and row.get("correct"):
            return False
        if mode == "right" and not row.get("correct"):
            return False
        wanted = self.class_combo.currentData()
        if wanted and row.get("label") != wanted:
            return False
        if self._cell_filter is not None:
            true_id, pred_id = self._cell_filter
            return self._row_hits_cell(row, true_id, pred_id, len(names))
        return True

    @staticmethod
    def _row_hits_cell(row: dict, true_id: int, pred_id: int, class_count: int) -> bool:
        """某行记录是否命中混淆矩阵的某个单元格。"""
        pairs = row.get("pairs")
        back = class_count
        if pairs is not None:
            if true_id < back and pred_id < back:
                return (true_id, pred_id) in pairs
            if true_id == back:
                return pred_id in (row.get("fp_classes") or [])
            if pred_id == back:
                return true_id in (row.get("fn_classes") or [])
            return False
        return int(row.get("true_id", -1)) == true_id and int(row.get("pred_id", -1)) == pred_id

    def _apply_filter(self, select_first: bool = False) -> None:
        result = self._vm.eval_result
        names = [str(name) for name in (result.get("class_names") or [])]
        self._view_indexes = [
            index for index, row in enumerate(self._rows)
            if self._row_matches(row, names)
        ]
        if self._instance_mode:
            self._fill_instance_grid()
        else:
            self._instances = []
            self.grid.set_images(
                [self._path_of(row) for row in self._view_rows()],
                evals=[
                    {
                        "conf": float(row.get("confidence") or 0.0),
                        "correct": bool(row.get("correct")),
                        "label": str(row.get("pred_label") or ""),
                    }
                    for row in self._view_rows()
                ],
            )
        self._update_page_label()
        if select_first and (self._view_indexes or self._instances):
            self._select_view(0)
        elif not self._view_indexes:
            self.grid.set_images([])
            self._update_page_label()

    def _fill_instance_grid(self) -> None:
        """实例级视图：当前筛选范围内每个真实框 / 预测框单独成块。"""
        wanted = set(self._view_indexes)
        self._instances = [
            item for item in self._vm.instance_items()
            if int(item.get("row", -1)) in wanted and str(item.get("path") or "")
        ]
        self.grid.set_images(
            [str(item.get("path") or "") for item in self._instances],
            evals=[
                {
                    "conf": float(item.get("confidence") or 0.0),
                    "correct": bool(item.get("correct")),
                    "label": self._instance_label(item),
                }
                for item in self._instances
            ],
        )

    @staticmethod
    def _instance_label(item: dict) -> str:
        """实例块的角标文字：TP / 误检·原因 / 漏检。"""
        kind = str(item.get("kind") or "")
        if kind == "tp":
            return "TP"
        if kind == "fp":
            reason = FP_REASON_LABELS.get(str(item.get("reason") or ""), "误检")
            return f"误检·{reason}"
        return "漏检"

    def _view_rows(self) -> list[dict]:
        return [self._rows[index] for index in self._view_indexes]

    @staticmethod
    def _path_of(row: dict) -> str:
        """缩略图展示原图（预测可视化放在右侧「预测图」里看）。"""
        return str(row.get("path") or "")

    def _update_page_label(self) -> None:
        if self._instance_mode:
            self.page_label.setText(f"{len(self._instances)} 个实例")
            return
        total = len(self._view_indexes)
        current = self._view_position() + 1 if total else 0
        self.page_label.setText(f"{current} / {total}")

    def _view_position(self) -> int:
        """当前查看记录在筛选结果里的位置。"""
        index = self._vm.eval_index()
        try:
            return self._view_indexes.index(index)
        except ValueError:
            return 0

    def _select_view(self, position: int) -> None:
        if not self._view_indexes:
            return
        position = max(0, min(len(self._view_indexes) - 1, int(position)))
        index = self._view_indexes[position]
        self.grid.setCurrentRow(position)
        self._vm.select_row(index)

    def _on_page(self, name: str) -> None:
        if not self._view_indexes:
            return
        last = len(self._view_indexes) - 1
        position = self._view_position()
        if name == "first":
            position = 0
        elif name == "last":
            position = last
        elif name == "prev":
            position -= 1
        else:
            position += 1
        self._select_view(position)

    def _on_grid_activated(self, row: int) -> None:
        if self._instance_mode:
            if 0 <= row < len(self._instances):
                self._vm.select_row(int(self._instances[row].get("row") or 0))
            return
        if 0 <= row < len(self._view_indexes):
            self._vm.select_row(self._view_indexes[row])

    # --------------------------------------------------- 图像详情
    def _on_row_selected(self, row: dict) -> None:
        if not row:
            return
        self.detail_name.setText(str(row.get("name") or "—"))
        self._syncing = True
        try:
            index = self.true_combo.findData(str(row.get("label") or ""))
            if index >= 0:
                self.true_combo.setCurrentIndex(index)
        finally:
            self._syncing = False

        subset = str(row.get("subset") or "")
        self.subset_value.setText(
            {"train": "训练集", "val": "验证集", "test": "测试集"}.get(subset) or "—"
        )
        correct = bool(row.get("correct"))
        self.state_value.setText("正确" if correct else "错误")
        self.state_value.setStyleSheet(
            f"color:{_EVAL_COLORS_OK if correct else _EVAL_COLORS_BAD};"
        )
        self.pred_value.setText(str(row.get("pred_label") or "—"))
        self.conf_value.setText(f"{float(row.get('confidence') or 0):.4f}")
        self.time_value.setText(f"{float(row.get('ms') or 0):.2f} ms")
        # GT 叠加只有检测族才有（漏检 / 误检 / 定位不准一眼可见）
        overlay = self.preview_buttons.get("overlay")
        if overlay is not None:
            overlay.setEnabled(bool(row.get("overlay")))
        heat = self.preview_buttons.get("heat")
        if heat is not None:
            heat.setEnabled(bool(row.get("heat_map")))
        gradcam = self.preview_buttons.get("gradcam")
        if gradcam is not None:
            gradcam.setEnabled(self._vm.gradcam_available())
        self._fill_probs(row)
        self._set_preview(self._preview_mode, row)
        self._update_page_label()

    def _fill_probs(self, row: dict) -> None:
        probs = row.get("probs") or {}
        if not probs:
            self.prob_chart.set_data([])
            return
        colors = self._vm.class_colors(list(probs.keys()))
        reference = max(float(value) for value in probs.values()) or 1.0
        self.prob_chart.set_data([
            (str(name), float(value), reference, colors[index % len(colors)])
            for index, (name, value) in enumerate(probs.items())
        ])

    def _set_preview(self, mode: str, row: dict | None = None) -> None:
        self._preview_mode = mode
        if row is None:
            row = self._vm.current_row()
        if not row:
            self.preview.clear()
            return
        if mode == "overlay":
            path = str(row.get("overlay") or "")
        elif mode == "preprocess":
            path = self._vm.build_preprocess_preview(row)
        elif mode == "heat":
            path = self._vm.build_heatmap_preview(row)
        elif mode == "gradcam":
            path = self._vm.build_gradcam_preview(row)
        elif mode == "pred":
            path = str(row.get("annotated") or "")
        else:
            path = ""
        if not path:
            path = str(row.get("path") or "")
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.preview.clear()
            return
        self.preview.setPixmap(pixmap.scaled(
            self.preview.width() or 260, 220,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))

    def _on_true_label_changed(self, index: int) -> None:
        if self._syncing or index < 0:
            return
        label = str(self.true_combo.itemData(index) or "")
        if label:
            self._vm.update_true_label(self._vm.eval_index(), label)

    # -----------------------------------------------------------
    # 推理结果（原检测能力）
    # -----------------------------------------------------------
    def _on_result(self, summary: dict) -> None:
        self._show_image(summary.get("annotated"))
        records = summary.get("records") or []
        abnormal = summary.get("abnormal")
        if abnormal is not None:
            self.result_label.setText(
                f"异常 {abnormal} / {summary.get('count', 0)} 张"
            )
            self._fill_table(records)
            return
        frames = summary.get("frames")
        elapsed = summary.get("elapsed") or 0.0
        text = (
            f"目标数量：{summary.get('count', 0)}"
            f" · 耗时：{elapsed * 1000:.1f} ms"
        )
        if frames:
            text = f"帧数：{frames} · " + text
        self.result_label.setText(text)
        self._fill_table(records)

    def _on_camera_frame(self, annotated, records) -> None:
        self._show_image(annotated)
        self.result_label.setText(f"目标数量：{len(records)}（实时）")

    def _show_image(self, array) -> None:
        if array is None:
            self.result_image.clear()
            return
        self.result_image.setPixmap(self._bgr_to_pixmap(array))

    def _fill_table(self, records: list, limit: int = 200) -> None:
        rows = records[:limit]
        self.table.setRowCount(len(rows))
        for row, record in enumerate(rows):
            values = [
                str(record.get("class_name", "")),
                f"{float(record.get('confidence', 0)):.3f}",
                f"{float(record.get('x1', 0)):.0f}",
                f"{float(record.get('y1', 0)):.0f}",
                f"{float(record.get('x2', 0)):.0f}",
                f"{float(record.get('y2', 0)):.0f}",
            ]
            for column, text in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(text))

    @staticmethod
    def _bgr_to_pixmap(array, max_width: int = 760) -> QPixmap:
        import numpy as np

        if array.ndim == 3 and array.shape[2] == 3:
            array = array[:, :, ::-1]
        array = np.ascontiguousarray(array)
        rows, cols = array.shape[:2]
        if array.ndim == 2:
            image = QImage(array.data, cols, rows, cols, QImage.Format.Format_Grayscale8)
        else:
            image = QImage(array.data, cols, rows, 3 * cols, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(image.copy())
        if pixmap.width() > max_width:
            pixmap = pixmap.scaledToWidth(
                max_width, Qt.TransformationMode.SmoothTransformation
            )
        return pixmap

    # -----------------------------------------------------------
    # 任务状态
    # -----------------------------------------------------------
    def _on_task_started(self, name: str) -> None:
        self.progress_bar.setValue(0)
        self.task_label.setText(f"{name}…")
        self.detect_progress.setValue(0)
        self.state_label.setText(f"{name}中")
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.detect_btn.setEnabled(False)

    def _on_task_progress(self, percent: int, text: str) -> None:
        self.progress_bar.setValue(percent)
        self.detect_progress.setValue(percent)
        if text:
            self.task_label.setText(text)

    def _on_task_finished(self, text: str) -> None:
        self.progress_bar.setValue(100)
        self.task_label.setText(text)
        self.state_label.setText("")
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.detect_btn.setEnabled(True)

    def _on_task_failed(self, text: str) -> None:
        self.task_label.setText(text)
        self.state_label.setText("")
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.detect_btn.setEnabled(True)
