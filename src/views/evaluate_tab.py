"""模型评估导航页：图片/相机输入、实时检测、结果展示与导出。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QVBoxLayout,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    Slider,
    StrongBodyLabel,
)

from src.viewmodels.evaluate_vm import EvaluateViewModel
from src.views.base_page import BasePage


class EvaluateTab(BasePage):
    """模型评估页：加载模型、选择输入源、实时检测、结果展示与导出。"""

    def __init__(self, vm: EvaluateViewModel, parent=None):
        super().__init__(parent)
        self._vm = vm
        self._build_ui()
        self._bind()

    def _build_ui(self) -> None:
        self._build_input_card()
        self._build_result_card()
        self.add_spacer()

    def _build_input_card(self) -> None:
        card, layout = self.add_card("检测输入")

        form = QFormLayout()

        # 权重
        weights_row = QHBoxLayout()
        self.weights_edit = LineEdit(card)
        self.weights_edit.setPlaceholderText("选择训练好的模型权重 (.pt)")
        weights_row.addWidget(self.weights_edit, 1)
        weights_btn = PushButton("浏览…", card)
        weights_btn.clicked.connect(self._on_browse_weights)
        weights_row.addWidget(weights_btn)
        form.addRow("模型权重：", weights_row)

        # 输入源
        self.source_combo = ComboBox(card)
        self.source_combo.addItems(["图片", "视频", "相机"])
        form.addRow("输入源：", self.source_combo)

        # 阈值
        def make_threshold(label, default):
            row = QVBoxLayout()
            row.setSpacing(2)
            top = QHBoxLayout()
            lbl = BodyLabel(label, card)
            val = BodyLabel(f"{default:.2f}", card)
            val.setAlignment(Qt.AlignmentFlag.AlignRight)
            top.addWidget(lbl)
            top.addWidget(val, 1)
            slider = Slider(Qt.Orientation.Horizontal, card)
            slider.setRange(0, 100)
            slider.setValue(int(default * 100))
            top2 = QHBoxLayout()
            top2.addWidget(slider)
            row.addLayout(top)
            row.addLayout(top2)
            layout.addLayout(row)
            return slider, val

        self.conf_slider, self.conf_val = make_threshold("置信度阈值", 0.25)
        self.iou_slider, self.iou_val = make_threshold("IOU 阈值", 0.45)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        self.detect_btn = PrimaryPushButton("开始检测", card)
        self.export_btn = PushButton("导出报告", card)
        btn_row.addWidget(self.detect_btn)
        btn_row.addWidget(self.export_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

    def _build_result_card(self) -> None:
        card, layout = self.add_card("检测结果")
        self.result_label = CaptionLabel("检测画面与统计信息将在此展示", card)
        layout.addWidget(self.result_label)

    # -----------------------------------------------------------
    # 交互
    # -----------------------------------------------------------
    def _on_browse_weights(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择权重文件", "", "PyTorch Weight (*.pt)",
        )
        if path:
            self.weights_edit.setText(path)
            self._vm.set_weights(path)

    def _on_detect(self) -> None:
        self._vm.set_weights(self.weights_edit.text().strip())
        self._vm.set_source(["image", "video", "camera"][self.source_combo.currentIndex()])
        self._vm.set_thresholds(self.conf_slider.value() / 100.0, self.iou_slider.value() / 100.0)
        self._vm.run_detection()

    def _bind(self) -> None:
        self.detect_btn.clicked.connect(self._on_detect)
        self.export_btn.clicked.connect(self._vm.export_report)
        self.conf_slider.valueChanged.connect(
            lambda v: self.conf_val.setText(f"{v / 100.0:.2f}")
        )
        self.iou_slider.valueChanged.connect(
            lambda v: self.iou_val.setText(f"{v / 100.0:.2f}")
        )
        self._vm.detectionDone.connect(self._on_result)

    def _on_result(self, result: dict) -> None:
        self.result_label.setText(
            f"检测到 {result['count']} 个目标，耗时 {result['elapsed'] * 1000:.1f} ms"
        )