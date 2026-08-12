"""模型管理导航页：预训练模型选择、权重导入、模型信息。"""

from __future__ import annotations

from PySide6.QtWidgets import QFileDialog, QFormLayout, QHBoxLayout
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
)

from src.viewmodels.model_vm import ModelViewModel
from src.views.base_page import BasePage


class ModelTab(BasePage):
    """模型管理页：选择 YOLO 变体、导入预训练权重、展示模型信息。"""

    def __init__(self, vm: ModelViewModel, parent=None):
        super().__init__(parent)
        self._vm = vm
        self._build_ui()
        self._bind()

    def _build_ui(self) -> None:
        self._build_select_card()
        self._build_weights_card()
        self._build_info_card()
        self.add_spacer()

    def _build_select_card(self) -> None:
        card, layout = self.add_card("预训练模型选择")

        form = QFormLayout()
        self.variant_combo = ComboBox(card)
        for variant in self._vm.variants:
            self.variant_combo.addItem(
                f"{variant['label']}  —  {variant['desc']}", variant["key"],
            )
        form.addRow("模型变体：", self.variant_combo)
        layout.addLayout(form)
        self.variant_desc = CaptionLabel(
            f"默认推理尺寸 {self._vm.selected['imgsz']}×{self._vm.selected['imgsz']}", card
        )
        layout.addWidget(self.variant_desc)

    def _build_weights_card(self) -> None:
        card, layout = self.add_card("权重导入")
        row = QHBoxLayout()
        self.weights_edit = LineEdit(card)
        self.weights_edit.setPlaceholderText("选择 .pt 自定义权重，留空则使用官方预训练权重")
        row.addWidget(self.weights_edit, 1)
        browse_btn = PushButton("浏览…", card)
        browse_btn.clicked.connect(self._on_browse)
        row.addWidget(browse_btn)
        layout.addLayout(row)
        import_btn = PrimaryPushButton("导入权重", card)
        import_btn.clicked.connect(self._on_import_weights)
        layout.addWidget(import_btn)

    def _build_info_card(self) -> None:
        card, layout = self.add_card("模型信息")
        self.info_label = CaptionLabel("导入权重后在此显示模型基础信息", card)
        layout.addWidget(self.info_label)

    # -----------------------------------------------------------
    # 交互
    # -----------------------------------------------------------
    def _on_browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择权重文件", "", "PyTorch Weight (*.pt)",
        )
        if path:
            self.weights_edit.setText(path)

    def _on_import_weights(self) -> None:
        path = self.weights_edit.text().strip()
        if path:
            self._vm.import_weights(path)
        else:
            self._vm.select_variant(self.variant_combo.currentData())

    def _on_selected(self, variant: dict) -> None:
        self.variant_desc.setText(
            f"默认推理尺寸 {variant['imgsz']}×{variant['imgsz']}  ·  {variant['desc']}"
        )

    def _on_model_info(self, info: dict) -> None:
        if not info:
            self.info_label.setText("前权重为空，将使用官方预训练权重")
            return
        self.info_label.setText(
            f"权重路径：{info.get('weights', '—')}\n"
            f"任务类型：{info.get('task', '—')}\n"
            f"类别：{list(info.get('names', {}).values()) or '—'}"
        )

    def _bind(self) -> None:
        self.variant_combo.currentIndexChanged.connect(
            lambda _i: self._vm.select_variant(self.variant_combo.currentData())
        )
        self._vm.modelSelected.connect(self._on_selected)
        self._vm.modelInfo.connect(self._on_model_info)