"""模型训练导航页：参数配置、启动/停止、进度、指标、日志。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
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
    SpinBox,
    StrongBodyLabel,
    TextEdit,
)

from src.services.anomalib_service import AnomalibService
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
        self._form = form
        self.task_combo = ComboBox(card)
        for item in TASK_TYPES:
            self.task_combo.addItem(item["label"], userData=item["key"])
        form.addRow("任务类型：", self.task_combo)

        self.model_combo = ComboBox(card)
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

        self._row_dataset = form.rowCount()
        self.dataset_label = BodyLabel("—", card)
        self.dataset_label.setWordWrap(True)
        form.addRow("数据集配置：", self.dataset_label)

        self._row_anomaly = form.rowCount()
        anomaly_row = QWidget(card)
        anomaly_layout = QHBoxLayout(anomaly_row)
        anomaly_layout.setContentsMargins(0, 0, 0, 0)
        self.anomaly_edit = LineEdit(anomaly_row)
        self.anomaly_edit.setPlaceholderText("含 normal/ 与 abnormal/ 的目录")
        anomaly_layout.addWidget(self.anomaly_edit, 1)
        anomaly_btn = PushButton("浏览…", anomaly_row)
        anomaly_btn.clicked.connect(self._on_browse_anomaly)
        anomaly_layout.addWidget(anomaly_btn)
        self.anomaly_pretrained_check = CheckBox("预训练权重", anomaly_row)
        self.anomaly_pretrained_check.setChecked(True)
        anomaly_layout.addWidget(self.anomaly_pretrained_check)
        form.addRow("异常数据目录：", anomaly_row)

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
        self.epoch_label = BodyLabel("Epoch：—", card)
        self.loss_label = BodyLabel("Loss：—", card)
        self.map_label = BodyLabel("mAP50：—", card)
        self.prec_label = BodyLabel("Precision：—", card)
        self.recall_label = BodyLabel("Recall：—", card)
        self.auroc_label = BodyLabel("AUROC：—", card)
        for lbl in (self.epoch_label, self.loss_label, self.map_label,
                    self.prec_label, self.recall_label, self.auroc_label):
            metric_row.addWidget(lbl)
        metric_row.addStretch(1)
        layout.addLayout(metric_row)

    def _build_log_card(self) -> None:
        card, layout = self.add_card("训练日志")
        self.log_edit = TextEdit(card)
        self.log_edit.setReadOnly(True)
        self.log_edit.setFixedHeight(160)
        layout.addWidget(self.log_edit)

    # -----------------------------------------------------------
    # 交互
    # -----------------------------------------------------------
    def _on_task_changed(self, _index=None) -> None:
        """任务类型切换时刷新模型列表与专属参数行。"""
        task = self.task_combo.currentData()
        self.model_combo.clear()
        if task == "anomaly":
            for name in AnomalibService.available_models():
                self.model_combo.addItem(name, userData=name)
        else:
            for variant in YOLO_MODEL_VARIANTS:
                self.model_combo.addItem(variant["label"], userData=variant["key"])
        is_anomaly = task == "anomaly"
        self._form.setRowVisible(self._row_anomaly, is_anomaly)
        self._form.setRowVisible(self._row_dataset, not is_anomaly)

    def _on_browse_anomaly(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择异常检测数据目录")
        if directory:
            self.anomaly_edit.setText(directory)

    def _on_start(self) -> None:
        self._vm.update_config(
            task_type=self.task_combo.currentData(),
            model_key=self.model_combo.currentData() or "",
            epochs=self.epochs_spin.value(),
            batch=self.batch_spin.value(),
            lr=self.lr_double.value(),
            optimizer=self.opt_combo.currentText(),
            device=self.device_combo.currentText(),
            anomaly_root=self.anomaly_edit.text().strip(),
            anomaly_pretrained=self.anomaly_pretrained_check.isChecked(),
        )
        self._vm.start()

    def _bind(self) -> None:
        self.task_combo.currentIndexChanged.connect(self._on_task_changed)
        self.start_btn.clicked.connect(self._on_start)
        self.stop_btn.clicked.connect(self._vm.stop)
        self.reset_btn.clicked.connect(self._vm.reset)
        self._vm.statusChanged.connect(self._on_status)
        self._vm.progressChanged.connect(self._on_progress)
        self._vm.metricsChanged.connect(self._on_metrics)
        self._vm.logAppended.connect(self._on_log)
        self._vm.configChanged.connect(self._on_config)
        self._on_status(self._vm.config.status)
        self._on_task_changed()

    def _on_config(self, config) -> None:
        self.dataset_label.setText(config.data_yaml or "—")

    def _on_status(self, status: str) -> None:
        running = status == "running"
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)

    def _on_progress(self, value: float) -> None:
        self.progress_bar.setValue(int(value * 100))
        self.progress_label.setText(f"进度：{value * 100:.0f}%")

    def _on_metrics(self, metrics: dict) -> None:
        epoch = metrics.get("epoch")
        if epoch:
            self.epoch_label.setText(f"Epoch：{epoch} / {metrics.get('total', '—')}")
        self.loss_label.setText(f"Loss：{metrics.get('loss', '—')}")
        self.map_label.setText(f"mAP50：{metrics.get('mAP50', '—')}")
        self.prec_label.setText(f"Precision：{metrics.get('precision', '—')}")
        self.recall_label.setText(f"Recall：{metrics.get('recall', '—')}")
        if metrics.get("auroc") is not None:
            self.auroc_label.setText(f"AUROC：{metrics['auroc']}")

    def _on_log(self, line: str) -> None:
        self.log_edit.append(line)