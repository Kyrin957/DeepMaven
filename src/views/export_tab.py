"""模型导出导航页：格式选择、导出路径、参数与导出进度。"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QSpinBox,
)
from qfluentwidgets import (
    CaptionLabel,
    CheckBox,
    ComboBox,
    LineEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
)

from src.utils.constants import EXPORT_FORMATS
from src.viewmodels.export_vm import ExportViewModel
from src.views.base_page import BasePage


class ExportTab(BasePage):
    """模型导出页：选择导出格式与路径，执行导出并显示进度。"""

    def __init__(self, vm: ExportViewModel, parent=None):
        super().__init__(parent)
        self._vm = vm
        self._build_ui()
        self._bind()

    # -----------------------------------------------------------
    # UI
    # -----------------------------------------------------------
    def _build_ui(self) -> None:
        self._build_config_card()
        self._build_progress_card()
        self.add_spacer()

    def _build_config_card(self) -> None:
        card, layout = self.add_card("导出配置")
        form = QFormLayout()

        weights_row = QHBoxLayout()
        self.weights_edit = LineEdit(card)
        self.weights_edit.setPlaceholderText("待导出的模型权重 (.pt)")
        weights_row.addWidget(self.weights_edit, 1)
        weights_btn = PushButton("浏览…", card)
        weights_btn.clicked.connect(self._on_browse_weights)
        weights_row.addWidget(weights_btn)
        form.addRow("源模型：", weights_row)

        self.format_combo = ComboBox(card)
        for fmt in EXPORT_FORMATS:
            self.format_combo.addItem(
                f"{fmt['label']}  —  {fmt['desc']}", userData=fmt["key"]
            )
        self.format_combo.currentIndexChanged.connect(
            lambda _i: self._vm.set_format(self.format_combo.currentData())
        )
        form.addRow("导出格式：", self.format_combo)

        out_row = QHBoxLayout()
        self.out_edit = LineEdit(card)
        self.out_edit.setPlaceholderText("导出保存目录")
        self.out_edit.setText(self._vm.config.output_dir)
        self.out_edit.editingFinished.connect(
            lambda: self._vm.set_output_dir(self.out_edit.text().strip())
        )
        out_row.addWidget(self.out_edit, 1)
        out_btn = PushButton("浏览…", card)
        out_btn.clicked.connect(self._on_browse_out)
        out_row.addWidget(out_btn)
        form.addRow("导出目录：", out_row)

        self.imgsz_spin = QSpinBox(card)
        self.imgsz_spin.setRange(64, 4096)
        self.imgsz_spin.setSingleStep(32)
        self.imgsz_spin.setValue(self._vm.config.imgsz)
        self.imgsz_spin.valueChanged.connect(self._vm.set_imgsz)
        form.addRow("图像尺寸：", self.imgsz_spin)

        self.opset_spin = QSpinBox(card)
        self.opset_spin.setRange(9, 20)
        self.opset_spin.setValue(self._vm.config.opset)
        self.opset_spin.valueChanged.connect(self._vm.set_opset)
        form.addRow("ONNX Opset：", self.opset_spin)

        option_row = QHBoxLayout()
        self.dynamic_check = CheckBox("动态尺寸", card)
        self.dynamic_check.setChecked(self._vm.config.dynamic)
        self.dynamic_check.stateChanged.connect(
            lambda _s: self._vm.set_dynamic(self.dynamic_check.isChecked())
        )
        self.simplify_check = CheckBox("图精简", card)
        self.simplify_check.setChecked(self._vm.config.simplify)
        self.simplify_check.stateChanged.connect(
            lambda _s: self._vm.set_simplify(self.simplify_check.isChecked())
        )
        option_row.addWidget(self.dynamic_check)
        option_row.addWidget(self.simplify_check)
        option_row.addStretch(1)
        form.addRow("选项：", option_row)

        layout.addLayout(form)

        self.export_btn = PrimaryPushButton("开始导出", card)
        layout.addWidget(self.export_btn)

    def _build_progress_card(self) -> None:
        card, layout = self.add_card("导出进度")
        self.progress_bar = ProgressBar(card)
        self.progress_bar.setRange(0, 100)
        layout.addWidget(self.progress_bar)
        self.result_label = CaptionLabel("尚未开始导出", card)
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)

    # -----------------------------------------------------------
    # 交互
    # -----------------------------------------------------------
    def _on_browse_weights(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择模型权重", "", "PyTorch Weight (*.pt)",
        )
        if path:
            self.weights_edit.setText(path)
            self._vm.set_weights(path)

    def _on_browse_out(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if directory:
            self.out_edit.setText(directory)
            self._vm.set_output_dir(directory)

    def _on_export(self) -> None:
        self._vm.set_weights(self.weights_edit.text().strip())
        self._vm.set_format(self.format_combo.currentData())
        self._vm.set_output_dir(self.out_edit.text().strip())
        self._vm.export()

    # -----------------------------------------------------------
    # 绑定
    # -----------------------------------------------------------
    def _bind(self) -> None:
        self.export_btn.clicked.connect(self._on_export)
        self._vm.taskStarted.connect(self._on_task_started)
        self._vm.taskProgress.connect(self._on_task_progress)
        self._vm.taskFinished.connect(self._on_task_finished)
        self._vm.taskFailed.connect(self._on_task_failed)
        self._vm.exportFinished.connect(self._on_finished)

    def _on_task_started(self, name: str) -> None:
        self.progress_bar.setValue(0)
        self.result_label.setText(f"{name}…")
        self.export_btn.setEnabled(False)

    def _on_task_progress(self, percent: int, text: str) -> None:
        self.progress_bar.setValue(percent)
        if text:
            self.result_label.setText(text)

    def _on_task_finished(self, text: str) -> None:
        self.progress_bar.setValue(100)
        self.result_label.setText(text)
        self.export_btn.setEnabled(True)

    def _on_task_failed(self, text: str) -> None:
        self.result_label.setText(text)
        self.export_btn.setEnabled(True)

    def _on_finished(self, path: str) -> None:
        self.result_label.setText(f"导出完成：{path}")
