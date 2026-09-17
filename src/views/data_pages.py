"""数据管理子页：导入 / 类别 / 质检 / 划分。

每个子页均为 `BasePage`（可滚动卡片布局），由 `DataTab` 通过分段控件组织。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QSpinBox,
    QTableWidgetItem,
    QVBoxLayout,
)

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    ColorPickerButton,
    LineEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    Slider,
    TableWidget,
    TextEdit,
)

from src.services.augment_service import AugmentConfig
from src.services.quality_service import QualityService
from src.viewmodels.category_vm import CategoryViewModel
from src.viewmodels.dataset_vm import DatasetViewModel
from src.views.base_page import BasePage


# ===============================================================
# 导入
# ===============================================================
class DataImportPage(BasePage):
    """数据集导入与统计。"""

    def __init__(self, vm: DatasetViewModel, parent=None):
        super().__init__(parent)
        self._vm = vm
        self._build_ui()
        self._bind()
        self.setAcceptDrops(True)

    def _build_ui(self) -> None:
        card, layout = self.add_card("数据导入")
        row = QHBoxLayout()
        self.source_label = CaptionLabel("未导入数据集", card)
        row.addWidget(self.source_label, 1)
        folder_btn = PrimaryPushButton("导入文件夹", card)
        folder_btn.clicked.connect(self._on_import_folder)
        row.addWidget(folder_btn)
        files_btn = PushButton("导入文件…", card)
        files_btn.clicked.connect(self._on_import_files)
        row.addWidget(files_btn)
        layout.addLayout(row)

        stats_card, stats_layout = self.add_card("统计信息")
        form = QFormLayout()
        self.img_count = BodyLabel("—", stats_card)
        self.label_count = BodyLabel("—", stats_card)
        self.class_count = BodyLabel("—", stats_card)
        self.duplicate_count = BodyLabel("—", stats_card)
        self.class_list = BodyLabel("—", stats_card)
        form.addRow("图片数量：", self.img_count)
        form.addRow("标签数量：", self.label_count)
        form.addRow("类别数量：", self.class_count)
        form.addRow("重复图片：", self.duplicate_count)
        form.addRow("类别列表：", self.class_list)
        stats_layout.addLayout(form)
        self.add_spacer()

    # -----------------------------------------------------------
    # 交互
    # -----------------------------------------------------------
    def _on_import_folder(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择图片文件夹")
        if directory:
            self._vm.import_images(directory)

    def _on_import_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择图片文件", "",
            "图片 (*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp)",
        )
        if paths:
            self._vm.import_files(paths)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        dirs, files = [], []
        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            path = url.toLocalFile()
            (dirs if Path(path).is_dir() else files).append(path)
        if files:
            self._vm.import_files(files)
        elif dirs:
            self._vm.import_images(dirs[0])

    # -----------------------------------------------------------
    # 绑定
    # -----------------------------------------------------------
    def _bind(self) -> None:
        self._vm.datasetChanged.connect(self._on_dataset)

    def _on_dataset(self, dataset) -> None:
        if dataset is None:
            self.source_label.setText("未导入数据集")
            self.img_count.setText("—")
            self.label_count.setText("—")
            self.class_count.setText("—")
            self.duplicate_count.setText("—")
            self.class_list.setText("—")
            return
        self.source_label.setText(f"来源：{dataset.source_path or '（多个目录）'}")
        self.img_count.setText(str(dataset.image_count))
        self.label_count.setText(str(dataset.label_count))
        self.class_count.setText(str(dataset.class_count))
        self.duplicate_count.setText(str(dataset.duplicate_count))
        self.class_list.setText("、".join(dataset.class_names) or "—")


# ===============================================================
# 类别
# ===============================================================
class DataCategoryPage(BasePage):
    """缺陷类别管理。"""

    def __init__(
        self,
        vm: CategoryViewModel,
        dataset_vm: DatasetViewModel | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._vm = vm
        self._dataset_vm = dataset_vm
        self._rows: list = []
        self._loading = False
        self._build_ui()
        self._bind()
        self._reload()

    def _build_ui(self) -> None:
        card, layout = self.add_card("缺陷类别")
        self.table = TableWidget(card)
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["编号", "类别名", "颜色"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.table.setMinimumHeight(220)
        layout.addWidget(self.table)

        add_row = QHBoxLayout()
        self.name_edit = LineEdit(card)
        self.name_edit.setPlaceholderText("新类别名称")
        add_row.addWidget(self.name_edit, 1)
        add_btn = PrimaryPushButton("新增", card)
        add_btn.clicked.connect(self._on_add)
        add_row.addWidget(add_btn)
        layout.addLayout(add_row)

        ops = QHBoxLayout()
        self.move_up_btn = PushButton("上移", card)
        self.move_down_btn = PushButton("下移", card)
        self.remove_btn = PushButton("删除", card)
        self.color_picker = ColorPickerButton(QColor("#005FB8"), "颜色", card)
        ops.addWidget(self.move_up_btn)
        ops.addWidget(self.move_down_btn)
        ops.addWidget(self.remove_btn)
        ops.addWidget(self.color_picker)
        layout.addLayout(ops)

        file_ops = QHBoxLayout()
        import_btn = PushButton("导入…", card)
        export_btn = PushButton("导出…", card)
        sync_btn = PushButton("从数据集生成", card)
        import_btn.clicked.connect(self._on_import)
        export_btn.clicked.connect(self._on_export)
        sync_btn.clicked.connect(self._on_sync)
        file_ops.addWidget(import_btn)
        file_ops.addWidget(export_btn)
        file_ops.addWidget(sync_btn)
        file_ops.addStretch(1)
        layout.addLayout(file_ops)
        self.add_spacer()

    # -----------------------------------------------------------
    # 渲染
    # -----------------------------------------------------------
    def _reload(self, _classes=None) -> None:
        self._loading = True
        self._rows = list(self._vm.classes)
        self.table.setRowCount(len(self._rows))
        readonly = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        for row, cls in enumerate(self._rows):
            id_item = QTableWidgetItem(str(cls.cls_id))
            id_item.setFlags(readonly)

            name_item = QTableWidgetItem(cls.name)
            name_item.setFlags(readonly | Qt.ItemFlag.ItemIsEditable)

            color_item = QTableWidgetItem(cls.color)
            color_item.setFlags(readonly)
            color_item.setForeground(QColor(cls.color))

            self.table.setItem(row, 0, id_item)
            self.table.setItem(row, 1, name_item)
            self.table.setItem(row, 2, color_item)
        self._loading = False
        self._update_ops_enabled()

    def _selected_cls_id(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._rows):
            return None
        return self._rows[row].cls_id

    def _update_ops_enabled(self) -> None:
        has_selection = self._selected_cls_id() is not None
        for widget in (
            self.move_up_btn, self.move_down_btn, self.remove_btn, self.color_picker,
        ):
            widget.setEnabled(has_selection)

    # -----------------------------------------------------------
    # 交互
    # -----------------------------------------------------------
    def _on_add(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            self._vm.message.emit("warning", "请输入类别名称")
            return
        self._vm.add_class(name)
        self.name_edit.clear()

    def _on_item_changed(self, item) -> None:
        if self._loading or item.column() != 1:
            return
        row = item.row()
        if row >= len(self._rows):
            return
        cls = self._rows[row]
        new_name = item.text().strip()
        if not new_name or new_name == cls.name:
            self._reload()
            return
        self._vm.rename_class(cls.cls_id, new_name)

    def _on_move(self, delta: int) -> None:
        cls_id = self._selected_cls_id()
        if cls_id is not None:
            self._vm.move_class(cls_id, delta)

    def _on_remove(self) -> None:
        cls_id = self._selected_cls_id()
        if cls_id is not None:
            self._vm.remove_class(cls_id)

    def _on_color_picked(self, color) -> None:
        cls_id = self._selected_cls_id()
        if cls_id is None:
            return
        name = color.name() if hasattr(color, "name") else str(color)
        self._vm.set_color(cls_id, name)

    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "导入类别", "", "类别文件 (*.txt *.yaml *.yml)",
        )
        if path:
            self._vm.import_from_file(path)

    def _on_export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出类别", "classes.txt", "文本文件 (*.txt)",
        )
        if path:
            self._vm.export_to_file(path)

    def _on_sync(self) -> None:
        dataset = self._dataset_vm.dataset if self._dataset_vm else None
        self._vm.sync_from_dataset(dataset)

    # -----------------------------------------------------------
    # 绑定
    # -----------------------------------------------------------
    def _bind(self) -> None:
        self._vm.classesChanged.connect(self._reload)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemSelectionChanged.connect(self._update_ops_enabled)
        self.move_up_btn.clicked.connect(lambda: self._on_move(-1))
        self.move_down_btn.clicked.connect(lambda: self._on_move(1))
        self.remove_btn.clicked.connect(self._on_remove)
        self.color_picker.colorChanged.connect(self._on_color_picked)


# ===============================================================
# 质检
# ===============================================================
class DataQualityPage(BasePage):
    """数据质检：重复 / 模糊 / 曝光 / 标签校验。"""

    def __init__(self, vm: DatasetViewModel, parent=None):
        super().__init__(parent)
        self._vm = vm
        self._build_ui()
        self._vm.datasetChanged.connect(self._on_dataset)

    def _build_ui(self) -> None:
        card, layout = self.add_card("检查项")
        row = QHBoxLayout()
        for text, handler in (
            ("重复图片", self._check_duplicates),
            ("模糊图片", self._check_blurry),
            ("曝光异常", self._check_exposure),
            ("标签校验", self._check_labels),
            ("全部检查", self._check_all),
        ):
            btn = PushButton(text, card)
            btn.clicked.connect(handler)
            row.addWidget(btn)
        row.addStretch(1)
        layout.addLayout(row)

        result_card, result_layout = self.add_card("检查结果")
        self.report = TextEdit(result_card)
        self.report.setReadOnly(True)
        self.report.setFixedHeight(340)
        result_layout.addWidget(self.report)
        self.add_spacer()

    # -----------------------------------------------------------
    # 检查
    # -----------------------------------------------------------
    def _images(self) -> list:
        images = self._vm.source_images()
        if not images:
            self.report.setPlainText("请先导入数据集")
        return images

    def _on_dataset(self, _dataset) -> None:
        self.report.clear()

    def _check_duplicates(self) -> None:
        images = self._images()
        if not images:
            return
        groups = QualityService.find_duplicates(images)
        lines = [f"重复 / 近重复图片组：{len(groups)}"]
        for index, group in enumerate(groups, 1):
            lines.append(f"  组 {index}（{len(group)} 张）")
            lines.extend(f"    {path}" for path in group)
        self.report.setPlainText("\n".join(lines))

    def _check_blurry(self) -> None:
        images = self._images()
        if not images:
            return
        found = QualityService.find_blurry(images)
        lines = [f"疑似模糊图片：{len(found)}"]
        lines.extend(f"    清晰度 {value:.1f}  {path}" for path, value in found)
        self.report.setPlainText("\n".join(lines))

    def _check_exposure(self) -> None:
        images = self._images()
        if not images:
            return
        found = QualityService.find_exposure(images)
        lines = [f"曝光异常图片：{len(found)}"]
        lines.extend(f"    亮度 {value:.1f}  {path}" for path, value in found)
        self.report.setPlainText("\n".join(lines))

    def _check_labels(self) -> None:
        images = self._images()
        if not images:
            return
        result = QualityService.check_labels(images, self._vm.source_label_index())
        lines = [
            f"缺失标签：{len(result['missing_label'])}",
            f"空标签：{len(result['empty'])}",
            f"字段/坐标异常：{len(result['out_of_range'])}",
            f"孤立标签：{len(result['orphan_label'])}",
        ]
        for key in ("missing_label", "empty", "out_of_range", "orphan_label"):
            lines.extend(f"    {item}" for item in result[key])
        self.report.setPlainText("\n".join(lines))

    def _check_all(self) -> None:
        images = self._images()
        if not images:
            return
        groups = QualityService.find_duplicates(images)
        blurry = QualityService.find_blurry(images)
        exposure = QualityService.find_exposure(images)
        labels = QualityService.check_labels(images, self._vm.source_label_index())

        lines = [
            f"图片总数：{len(images)}",
            f"重复 / 近重复组：{len(groups)}",
            f"疑似模糊：{len(blurry)}",
            f"曝光异常：{len(exposure)}",
            f"缺失标签：{len(labels['missing_label'])}",
            f"空标签：{len(labels['empty'])}",
            f"字段/坐标异常：{len(labels['out_of_range'])}",
            f"孤立标签：{len(labels['orphan_label'])}",
        ]
        self.report.setPlainText("\n".join(lines))


# ===============================================================
# 划分
# ===============================================================
class DataSplitPage(BasePage):
    """数据集划分与产物展示。"""

    def __init__(self, vm: DatasetViewModel, parent=None):
        super().__init__(parent)
        self._vm = vm
        self._build_ui()
        self._bind()

    def _build_ui(self) -> None:
        card, layout = self.add_card("数据集划分")

        def make_slider(label, default):
            row = QVBoxLayout()
            row.setSpacing(2)
            top = QHBoxLayout()
            name = BodyLabel(label, card)
            val = BodyLabel(f"{default * 100:.0f}%", card)
            val.setAlignment(Qt.AlignmentFlag.AlignRight)
            top.addWidget(name)
            top.addWidget(val, 1)
            slider = Slider(Qt.Orientation.Horizontal, card)
            slider.setRange(0, 100)
            slider.setValue(int(default * 100))
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

        self.stratified_check = CheckBox("分层划分", card)
        self.stratified_check.setChecked(True)
        self.stratified_check.stateChanged.connect(
            lambda _state: self._vm.set_stratified(self.stratified_check.isChecked())
        )
        layout.addWidget(self.stratified_check)

        apply_btn = PushButton("应用划分", card)
        apply_btn.clicked.connect(self._vm.apply_split)
        layout.addWidget(apply_btn)

        result_card, result_layout = self.add_card("划分产物")
        form = QFormLayout()
        self.output_label = BodyLabel("—", result_card)
        self.output_label.setWordWrap(True)
        self.yaml_label = BodyLabel("—", result_card)
        self.yaml_label.setWordWrap(True)
        form.addRow("输出目录：", self.output_label)
        form.addRow("数据集配置：", self.yaml_label)
        result_layout.addLayout(form)
        self.add_spacer()

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

    def _bind(self) -> None:
        self._vm.datasetChanged.connect(self._on_dataset)

    def _on_dataset(self, dataset) -> None:
        if dataset is None:
            self.output_label.setText("—")
            self.yaml_label.setText("—")
            return
        self.output_label.setText(dataset.output_path or "—")
        self.yaml_label.setText(dataset.data_yaml or "—")


# ===============================================================
# 增强
# ===============================================================
class DataAugmentPage(BasePage):
    """数据增强：参数配置、效果预览与离线增强。"""

    def __init__(self, vm: DatasetViewModel, parent=None):
        super().__init__(parent)
        self._vm = vm
        self._build_ui()
        self._bind()

    def _build_ui(self) -> None:
        card, layout = self.add_card("增强参数")
        form = QFormLayout()

        self.hflip_spin = self._make_prob(0.5)
        self.vflip_spin = self._make_prob(0.0)
        self.bright_spin = self._make_prob(0.2)
        self.noise_spin = self._make_prob(0.0)
        self.blur_spin = self._make_prob(0.0)

        self.rotate_spin = QDoubleSpinBox(card)
        self.rotate_spin.setRange(0.0, 180.0)
        self.rotate_spin.setSingleStep(5.0)
        self.rotate_spin.setValue(15.0)

        self.scale_spin = QDoubleSpinBox(card)
        self.scale_spin.setRange(0.0, 0.5)
        self.scale_spin.setSingleStep(0.05)
        self.scale_spin.setDecimals(2)
        self.scale_spin.setValue(0.10)

        self.copies_spin = QSpinBox(card)
        self.copies_spin.setRange(1, 20)
        self.copies_spin.setValue(3)

        form.addRow("水平翻转：", self.hflip_spin)
        form.addRow("垂直翻转：", self.vflip_spin)
        form.addRow("旋转角度：", self.rotate_spin)
        form.addRow("缩放幅度：", self.scale_spin)
        form.addRow("亮度对比度：", self.bright_spin)
        form.addRow("高斯噪声：", self.noise_spin)
        form.addRow("高斯模糊：", self.blur_spin)
        form.addRow("增强份数：", self.copies_spin)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        preview_btn = PushButton("预览", card)
        preview_btn.clicked.connect(self._on_preview)
        apply_btn = PrimaryPushButton("生成增强数据", card)
        apply_btn.clicked.connect(self._on_apply)
        btn_row.addWidget(preview_btn)
        btn_row.addWidget(apply_btn)
        btn_row.addStretch(1)

        self.progress_bar = ProgressBar(card)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setFixedWidth(180)
        btn_row.addWidget(self.progress_bar)
        self.task_label = CaptionLabel("", card)
        btn_row.addWidget(self.task_label)
        layout.addLayout(btn_row)

        preview_card, preview_layout = self.add_card("效果预览")
        self.preview_label = QLabel(preview_card)
        self.preview_label.setMinimumHeight(200)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_layout.addWidget(self.preview_label)
        self.add_spacer()

    def _make_prob(self, default: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox(self)
        spin.setRange(0.0, 1.0)
        spin.setSingleStep(0.05)
        spin.setDecimals(2)
        spin.setValue(default)
        return spin

    # -----------------------------------------------------------
    # 交互
    # -----------------------------------------------------------
    def _config(self) -> AugmentConfig:
        return AugmentConfig(
            hflip=self.hflip_spin.value(),
            vflip=self.vflip_spin.value(),
            rotate=self.rotate_spin.value(),
            scale=self.scale_spin.value(),
            brightness=self.bright_spin.value(),
            contrast=self.bright_spin.value(),
            noise=self.noise_spin.value(),
            blur=self.blur_spin.value(),
            copies=self.copies_spin.value(),
        )

    def _on_preview(self) -> None:
        arrays = self._vm.augment_preview(self._config(), count=6)
        if not arrays:
            self.preview_label.clear()
            return
        self.preview_label.setPixmap(self._compose(arrays))

    def _on_apply(self) -> None:
        self._vm.augment_apply(self._config())

    # -----------------------------------------------------------
    # 预览拼图
    # -----------------------------------------------------------
    @staticmethod
    def _to_pixmap(array, height: int = 180) -> QPixmap:
        import numpy as np

        array = np.ascontiguousarray(array)
        rows, cols = array.shape[:2]
        image = QImage(array.data, cols, rows, 3 * cols, QImage.Format.Format_RGB888)
        return QPixmap.fromImage(image.copy()).scaledToHeight(
            height, Qt.TransformationMode.SmoothTransformation
        )

    @classmethod
    def _compose(cls, arrays, columns: int = 3) -> QPixmap:
        thumbs = [cls._to_pixmap(array) for array in arrays]
        rows = (len(thumbs) + columns - 1) // columns
        cell_w = max(thumb.width() for thumb in thumbs)
        cell_h = max(thumb.height() for thumb in thumbs)
        canvas = QPixmap(cell_w * min(columns, len(thumbs)), cell_h * rows)
        canvas.fill(QColor("#1E1E1E"))
        painter = QPainter(canvas)
        for index, thumb in enumerate(thumbs):
            row, col = divmod(index, columns)
            painter.drawPixmap(col * cell_w, row * cell_h, thumb)
        painter.end()
        return canvas

    # -----------------------------------------------------------
    # 绑定
    # -----------------------------------------------------------
    def _bind(self) -> None:
        self._vm.taskStarted.connect(self._on_task_started)
        self._vm.taskProgress.connect(self._on_task_progress)
        self._vm.taskFinished.connect(self._on_task_finished)
        self._vm.taskFailed.connect(self._on_task_failed)

    def _on_task_started(self, name: str) -> None:
        self.progress_bar.setValue(0)
        self.task_label.setText(f"{name}…")

    def _on_task_progress(self, percent: int, text: str) -> None:
        self.progress_bar.setValue(percent)
        if text:
            self.task_label.setText(text)

    def _on_task_finished(self, text: str) -> None:
        self.progress_bar.setValue(100)
        self.task_label.setText(text)

    def _on_task_failed(self, text: str) -> None:
        self.task_label.setText(text)
