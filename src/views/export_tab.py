"""模型导出导航页：格式选择、导出路径、导出进度。"""

from __future__ import annotations

from PySide6.QtWidgets import QFileDialog, QFormLayout, QHBoxLayout
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    LineEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    StrongBodyLabel,
)

from src.utils.constants import EXPORT_FORMATS
from src.viewmodels.export_vm import ExportViewModel
from src.views.base_page import BasePage


class ExportTab(BasePage):
    """模型导出页：选择导出格式与路径，执行导出并显示进度。"""

    def __init__(self, vm: ExportViewModel, parent=None):
        super().__init__(
            "模型导出",
            "将训练完成的模型导出为 PT / ONNX / TorchScript 等部署格式",
            parent,
        )
        self._vm = vm
        self._build_ui()
        self._bind()

    def _build_ui(self) -> None:
        self._build_config_card()
        self._build_progress_card()
        self.add_spacer()

    def _build_config_card(self) -> None:
        card, layout = self.add_card("导出配置")

        form = QFormLayout()

        # 源模型
        weights_row = QHBoxLayout()
        self.weights_edit = LineEdit(card)
        self.weights_edit.setPlaceholderText("选择待导出的模型权重 (.pt)")
        weights_row.addWidget(self.weights_edit, 1)
        weights_btn = PushButton("浏览…", card)
        weights_btn.clicked.connect(self._on_browse_weights)
        weights_row.addWidget(weights_btn)
        form.addRow("源模型：", weights_row)

        # 导出格式
        self.format_combo = ComboBox(card)
        for fmt in EXPORT_FORMATS:
            self.format_combo.addItem(f"{fmt['label']}  —  {fmt['desc']}", fmt["key"])
        form.addRow("导出格式：", self.format_combo)

        # 导出路径
        out_row = QHBoxLayout()
        self.out_edit = LineEdit(card)
        self.out_edit.setPlaceholderText("选择导出保存目录")
        self.out_edit.setText(self._vm.config.output_dir)
        out_row.addWidget(self.out_edit, 1)
        out_btn = PushButton("浏览…", card)
        out_btn.clicked.connect(self._on_browse_out)
        out_row.addWidget(out_btn)
        form.addRow("导出目录：", out_row)

        layout.addLayout(form)

        self.export_btn = PrimaryPushButton("开始导出", card)
        layout.addWidget(self.export_btn)

    def _build_progress_card(self) -> None:
        card, layout = self.add_card("导出进度")
        self.progress_bar = ProgressBar(card)
        self.progress_bar.setRange(0, 100)
        layout.addWidget(self.progress_bar)
        self.result_label = CaptionLabel("尚未开始导出", card)
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

    def _on_browse_out(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if directory:
            self.out_edit.setText(directory)

    def _on_export(self) -> None:
        self._vm.set_weights(self.weights_edit.text().strip())
        self._vm.set_format(self.format_combo.currentData())
        self._vm.set_output_dir(self.out_edit.text().strip())
        self._vm.export()

    def _bind(self) -> None:
        self.export_btn.clicked.connect(self._on_export)
        self._vm.progressChanged.connect(self._on_progress)
        self._vm.exportFinished.connect(self._on_finished)

    def _on_progress(self, value: float) -> None:
        self.progress_bar.setValue(int(value * 100))

    def _on_finished(self, path: str) -> None:
        self.result_label.setText(f"导出完成：{path}")