"""模型训练导航页：参数配置、启动/停止、进度、指标、日志。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QVBoxLayout
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    LineEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    SpinBox,
    StrongBodyLabel,
    TextEdit,
)

from src.utils.constants import OPTIMIZERS, TASK_TYPES, YOLO_MODEL_VARIANTS
from src.viewmodels.train_vm import TrainViewModel
from src.views.base_page import BasePage


class TrainTab(BasePage):
    """模型训练页：训练参数配置与训练监控。"""

    def __init__(self, vm: TrainViewModel, parent=None):
        super().__init__(parent)
        self._vm = vm
        self._build_ui()
        self._bind()

    def _build_ui(self) -> None:
        self._build_param_card()
        self._build_monitor_card()
        self._build_log_card()
        self.add_spacer()

    def _build_param_card(self) -> None:
        card, layout = self.add_card("训练参数配置")

        form = QFormLayout()
        self.task_combo = ComboBox(card)
        for item in TASK_TYPES:
            self.task_combo.addItem(item["label"], item["key"])
        form.addRow("任务类型：", self.task_combo)

        self.model_combo = ComboBox(card)
        for variant in YOLO_MODEL_VARIANTS:
            self.model_combo.addItem(variant["label"], variant["key"])
        form.addRow("模型：", self.model_combo)

        self.epochs_spin = SpinBox(card)
        self.epochs_spin.setRange(1, 10000)
        self.epochs_spin.setValue(100)
        form.addRow("训练轮数 (epochs)：", self.epochs_spin)

        self.batch_spin = SpinBox(card)
        self.batch_spin.setRange(1, 512)
        self.batch_spin.setValue(16)
        form.addRow("批次大小 (batch)：", self.batch_spin)

        from PySide6.QtWidgets import QDoubleSpinBox
        self.lr_double = QDoubleSpinBox(card)
        self.lr_double.setDecimals(4)
        self.lr_double.setRange(0.0001, 1.0)
        self.lr_double.setSingleStep(0.001)
        self.lr_double.setValue(0.01)
        form.addRow("学习率 (lr)：", self.lr_double)

        self.opt_combo = ComboBox(card)
        self.opt_combo.addItems(OPTIMIZERS)
        form.addRow("优化器：", self.opt_combo)

        self.device_combo = ComboBox(card)
        self.device_combo.addItems(["auto", "cpu", "cuda:0"])
        form.addRow("设备：", self.device_combo)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        self.start_btn = PrimaryPushButton("启动训练", card)
        self.stop_btn = PushButton("停止", card)
        self.reset_btn = PushButton("重置状态", card)
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.stop_btn)
        btn_row.addWidget(self.reset_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

    def _build_monitor_card(self) -> None:
        card, layout = self.add_card("训练监控")

        # 进度条
        progress_row = QVBoxLayout()
        progress_row.setSpacing(4)
        self.progress_label = CaptionLabel("进度：0%", card)
        progress_row.addWidget(self.progress_label)
        self.progress_bar = ProgressBar(card)
        self.progress_bar.setRange(0, 100)
        progress_row.addWidget(self.progress_bar)
        layout.addLayout(progress_row)

        # 指标
        metric_row = QHBoxLayout()
        self.loss_label = BodyLabel("Loss：—", card)
        self.map_label = BodyLabel("mAP50：—", card)
        self.prec_label = BodyLabel("Precision：—", card)
        self.recall_label = BodyLabel("Recall：—", card)
        for lbl in (self.loss_label, self.map_label, self.prec_label, self.recall_label):
            metric_row.addWidget(lbl)
        metric_row.addStretch(1)
        layout.addLayout(metric_row)

        layout.addWidget(CaptionLabel("损失曲线与 mAP 曲线将使用 Matplotlib 绘制", card))

    def _build_log_card(self) -> None:
        card, layout = self.add_card("训练日志")
        self.log_edit = TextEdit(card)
        self.log_edit.setReadOnly(True)
        self.log_edit.setFixedHeight(160)
        layout.addWidget(self.log_edit)

    # -----------------------------------------------------------
    # 交互
    # -----------------------------------------------------------
    def _on_start(self) -> None:
        self._vm.update_config(
            task_type=self.task_combo.currentData(),
            model_key=self.model_combo.currentData(),
            epochs=self.epochs_spin.value(),
            batch=self.batch_spin.value(),
            lr=self.lr_double.value(),
            optimizer=self.opt_combo.currentText(),
            device=self.device_combo.currentText(),
        )
        self._vm.start()

    def _bind(self) -> None:
        self.start_btn.clicked.connect(self._on_start)
        self.stop_btn.clicked.connect(self._vm.stop)
        self.reset_btn.clicked.connect(self._vm.reset)
        self._vm.statusChanged.connect(self._on_status)
        self._vm.progressChanged.connect(self._on_progress)
        self._vm.metricsChanged.connect(self._on_metrics)
        self._vm.logAppended.connect(self._on_log)

    def _on_status(self, status: str) -> None:
        self.start_btn.setEnabled(status != "running")

    def _on_progress(self, value: float) -> None:
        self.progress_bar.setValue(int(value * 100))
        self.progress_label.setText(f"进度：{value * 100:.0f}%")

    def _on_metrics(self, metrics: dict) -> None:
        self.loss_label.setText(f"Loss：{metrics.get('loss', '—')}")
        self.map_label.setText(f"mAP50：{metrics.get('mAP50', '—')}")
        self.prec_label.setText(f"Precision：{metrics.get('precision', '—')}")
        self.recall_label.setText(f"Recall：{metrics.get('recall', '—')}")

    def _on_log(self, line: str) -> None:
        self.log_edit.append(line)