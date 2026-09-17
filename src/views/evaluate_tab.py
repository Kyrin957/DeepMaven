"""模型评估导航页：图片 / 视频 / 相机检测、结果展示与报告导出。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QTableWidgetItem,
    QVBoxLayout,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    LineEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    Slider,
    TableWidget,
)

from src.services.anomalib_service import AnomalibService
from src.viewmodels.evaluate_vm import EvaluateViewModel
from src.views.base_page import BasePage


class EvaluateTab(BasePage):
    """模型评估页：加载模型、选择输入源、检测、结果展示与导出。"""

    def __init__(self, vm: EvaluateViewModel, parent=None):
        super().__init__(parent)
        self._vm = vm
        self._build_ui()
        self._bind()
        self._on_source_changed()

    # -----------------------------------------------------------
    # UI
    # -----------------------------------------------------------
    def _build_ui(self) -> None:
        self._build_input_card()
        self._build_result_card()
        self.add_spacer()

    def _build_input_card(self) -> None:
        card, layout = self.add_card("检测输入")
        form = QFormLayout()

        weights_row = QHBoxLayout()
        self.weights_edit = LineEdit(card)
        self.weights_edit.setPlaceholderText("训练好的模型权重 (.pt)")
        weights_row.addWidget(self.weights_edit, 1)
        weights_btn = PushButton("浏览…", card)
        weights_btn.clicked.connect(self._on_browse_weights)
        weights_row.addWidget(weights_btn)
        form.addRow("模型权重：", weights_row)

        self.source_combo = ComboBox(card)
        for key, text in (("image", "图片"), ("video", "视频"), ("camera", "相机")):
            self.source_combo.addItem(text, userData=key)
        form.addRow("输入源：", self.source_combo)

        self.path_row = QHBoxLayout()
        self.path_edit = LineEdit(card)
        self.path_edit.setPlaceholderText("图片 / 视频文件路径")
        self.path_row.addWidget(self.path_edit, 1)
        self.path_btn = PushButton("浏览…", card)
        self.path_btn.clicked.connect(self._on_browse_source)
        self.path_row.addWidget(self.path_btn)
        form.addRow("输入路径：", self.path_row)

        self.device_combo = ComboBox(card)
        self.device_combo.addItems(["auto", "cpu", "cuda:0"])
        form.addRow("设备：", self.device_combo)

        self._form = form
        self._row_anomaly = form.rowCount()
        self.anomaly_combo = ComboBox(card)
        for name in AnomalibService.available_models():
            self.anomaly_combo.addItem(name, userData=name)
        form.addRow("异常模型：", self.anomaly_combo)
        layout.addLayout(form)

        self.conf_slider, self.conf_val = self._make_threshold(
            "置信度阈值", 0.25, layout, card
        )
        self.iou_slider, self.iou_val = self._make_threshold(
            "IOU 阈值", 0.45, layout, card
        )

        btn_row = QHBoxLayout()
        self.detect_btn = PrimaryPushButton("开始检测", card)
        self.stop_btn = PushButton("停止", card)
        self.export_btn = PushButton("导出报告", card)
        btn_row.addWidget(self.detect_btn)
        btn_row.addWidget(self.stop_btn)
        btn_row.addWidget(self.export_btn)
        btn_row.addStretch(1)

        self.progress_bar = ProgressBar(card)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setFixedWidth(170)
        btn_row.addWidget(self.progress_bar)
        self.task_label = CaptionLabel("", card)
        btn_row.addWidget(self.task_label)
        layout.addLayout(btn_row)

    def _build_result_card(self) -> None:
        card, layout = self.add_card("检测结果")

        self.result_image = QLabel(card)
        self.result_image.setMinimumHeight(240)
        self.result_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.result_image)

        self.result_label = CaptionLabel("尚未执行检测", card)
        layout.addWidget(self.result_label)

        self.table = TableWidget(card)
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(
            ["类别", "置信度", "x1", "y1", "x2", "y2"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(200)
        layout.addWidget(self.table)

    def _make_threshold(self, label, default, layout, parent):
        row = QVBoxLayout()
        row.setSpacing(2)
        top = QHBoxLayout()
        top.addWidget(BodyLabel(label, parent))
        value = BodyLabel(f"{default:.2f}", parent)
        value.setAlignment(Qt.AlignmentFlag.AlignRight)
        top.addWidget(value, 1)
        slider = Slider(Qt.Orientation.Horizontal, parent)
        slider.setRange(0, 100)
        slider.setValue(int(default * 100))
        row.addLayout(top)
        row.addWidget(slider)
        layout.addLayout(row)
        return slider, value

    # -----------------------------------------------------------
    # 交互
    # -----------------------------------------------------------
    def _on_browse_weights(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择模型权重", "", "模型权重 (*.pt *.ckpt)",
        )
        if path:
            self.weights_edit.setText(path)
            self._vm.set_weights(path)

    def _on_browse_source(self) -> None:
        kind = self.source_combo.currentData()
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
        kind = self.source_combo.currentData()
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

    def _update_anomaly_row(self) -> None:
        """权重为 .ckpt 时显示异常检测模型选择。"""
        is_ckpt = self.weights_edit.text().strip().lower().endswith(".ckpt")
        self._form.setRowVisible(self._row_anomaly, is_ckpt)

    def _on_detect(self) -> None:
        self._vm.set_weights(self.weights_edit.text().strip())
        self._vm.set_source(self.source_combo.currentData())
        self._vm.set_source_path(self.path_edit.text().strip())
        self._vm.set_device(self.device_combo.currentText())
        self._vm.set_anomaly_model(self.anomaly_combo.currentData())
        self._vm.set_thresholds(
            self.conf_slider.value() / 100.0, self.iou_slider.value() / 100.0
        )
        self._vm.run_detection()

    def _on_export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出检测报告", "detections.csv",
            "CSV (*.csv);;Excel (*.xlsx)",
        )
        if path:
            self._vm.export_report(path)

    # -----------------------------------------------------------
    # 结果
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
    # 绑定
    # -----------------------------------------------------------
    def _bind(self) -> None:
        self.weights_edit.textChanged.connect(lambda _text: self._update_anomaly_row())
        self._update_anomaly_row()
        self.detect_btn.clicked.connect(self._on_detect)
        self.stop_btn.clicked.connect(self._vm.stop)
        self.export_btn.clicked.connect(self._on_export)
        self.source_combo.currentIndexChanged.connect(
            lambda _i: self._on_source_changed()
        )
        self.conf_slider.valueChanged.connect(
            lambda value: self.conf_val.setText(f"{value / 100.0:.2f}")
        )
        self.iou_slider.valueChanged.connect(
            lambda value: self.iou_val.setText(f"{value / 100.0:.2f}")
        )

        self._vm.resultReady.connect(self._on_result)
        self._vm.cameraFrame.connect(self._on_camera_frame)
        self._vm.configChanged.connect(self._on_config)
        self._vm.taskStarted.connect(self._on_task_started)
        self._vm.taskProgress.connect(self._on_task_progress)
        self._vm.taskFinished.connect(self._on_task_finished)
        self._vm.taskFailed.connect(self._on_task_failed)

    def _on_config(self, config) -> None:
        if config.weights_path and self.weights_edit.text() != config.weights_path:
            self.weights_edit.setText(config.weights_path)

    def _on_task_started(self, name: str) -> None:
        self.progress_bar.setValue(0)
        self.task_label.setText(f"{name}…")
        self.detect_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

    def _on_task_progress(self, percent: int, text: str) -> None:
        self.progress_bar.setValue(percent)
        if text:
            self.task_label.setText(text)

    def _on_task_finished(self, text: str) -> None:
        self.progress_bar.setValue(100)
        self.task_label.setText(text)
        self.detect_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def _on_task_failed(self, text: str) -> None:
        self.task_label.setText(text)
        self.detect_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
