"""数据管理导航页：数据集导入、统计、划分、预览。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    PrimaryPushButton,
    PushButton,
    Slider,
    StrongBodyLabel,
    SubtitleLabel,
)

from src.viewmodels.dataset_vm import DatasetViewModel
from src.views.base_page import BasePage


class DataTab(BasePage):
    """数据管理页：展示数据集统计、支持划分与预览。"""

    def __init__(self, vm: DatasetViewModel, parent=None):
        super().__init__(
            "数据管理",
            "导入图片数据集、查看统计信息、划分训练/验证/测试集",
            parent,
        )
        self._vm = vm
        self._build_ui()
        self._bind()

    def _build_ui(self) -> None:
        self._build_import_card()
        self._build_stats_card()
        self._build_split_card()
        self._build_preview_card()
        self.add_spacer()

    def _build_import_card(self) -> None:
        card, layout = self.add_card("数据集导入")
        import_row = QHBoxLayout()
        self.source_label = CaptionLabel("未导入数据集", card)
        import_row.addWidget(self.source_label, 1)
        import_btn = PrimaryPushButton("导入图片文件夹", card)
        import_btn.clicked.connect(self._on_import)
        import_row.addWidget(import_btn)
        layout.addLayout(import_row)

    def _build_stats_card(self) -> None:
        card, layout = self.add_card("统计信息")
        form = QFormLayout()
        self.img_count = BodyLabel("—", card)
        self.label_count = BodyLabel("—", card)
        self.class_count = BodyLabel("—", card)
        self.class_list = BodyLabel("—", card)
        form.addRow("图片数量：", self.img_count)
        form.addRow("标签数量：", self.label_count)
        form.addRow("类别数量：", self.class_count)
        form.addRow("类别列表：", self.class_list)
        layout.addLayout(form)

    def _build_split_card(self) -> None:
        card, layout = self.add_card("数据集划分")

        def make_slider(label, default):
            row = QVBoxLayout()
            row.setSpacing(2)
            top = QHBoxLayout()
            lbl = BodyLabel(label, card)
            val = BodyLabel(f"{default * 100:.0f}%", card)
            val.setAlignment(Qt.AlignmentFlag.AlignRight)
            top.addWidget(lbl)
            top.addWidget(val, 1)
            slider = Slider(Qt.Orientation.Horizontal, card)
            slider.setRange(0, 100)
            slider.setValue(default * 100)
            row.addLayout(top)
            row.addWidget(slider)
            layout.addLayout(row)
            return slider, val

        self.train_slider, self.train_val = make_slider("训练集", 0.7)
        self.val_slider, self.val_val = make_slider("验证集", 0.2)
        self.test_slider, self.test_val = make_slider("测试集", 0.1)

        self.train_slider.valueChanged.connect(self._on_split_changed)
        self.val_slider.valueChanged.connect(self._on_split_changed)
        self.test_slider.valueChanged.connect(self._on_split_changed)

        apply_btn = PushButton("应用划分", card)
        apply_btn.clicked.connect(self._vm.apply_split)
        layout.addWidget(apply_btn)

    def _build_preview_card(self) -> None:
        card, layout = self.add_card("图片预览")
        layout.addWidget(CaptionLabel("缩略图网格预览将在后续迭代中实现", card))

    # -----------------------------------------------------------
    # 交互
    # -----------------------------------------------------------
    def _on_import(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择图片文件夹")
        if directory:
            self._vm.import_images(directory)

    def _on_split_changed(self, _value=None) -> None:
        train = self.train_slider.value() / 100.0
        val = self.val_slider.value() / 100.0
        test = self.test_slider.value() / 100.0
        total = train + val + test
        if total == 0:
            return
        self.train_val.setText(f"{train / total * 100:.0f}%")
        self.val_val.setText(f"{val / total * 100:.0f}%")
        self.test_val.setText(f"{test / total * 100:.0f}%")
        self._vm.set_split(train / total, val / total, test / total)

    # -----------------------------------------------------------
    # 绑定
    # -----------------------------------------------------------
    def _bind(self) -> None:
        self._vm.datasetImported.connect(self._on_dataset)

    def _on_dataset(self, dataset) -> None:
        self.source_label.setText(f"来源：{dataset.source_path}")
        self.img_count.setText(str(dataset.image_count))
        self.label_count.setText(str(dataset.label_count))
        self.class_count.setText(str(dataset.class_count))
        self.class_list.setText("、".join(dataset.class_names) or "—")