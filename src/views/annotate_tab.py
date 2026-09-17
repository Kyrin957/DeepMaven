"""图像标注导航页：缩略图导航 + 标注画布 + 标注列表。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QListWidget,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    SegmentedWidget,
)

from src.services.autolabel_service import DEFAULT_DETECT_WEIGHTS, DEFAULT_SAM_WEIGHTS
from src.viewmodels.annotate_vm import AnnotateViewModel
from src.viewmodels.category_vm import CategoryViewModel
from src.views.widgets import (
    MODE_BOX,
    MODE_BROWSE,
    MODE_POLYGON,
    AnnotationCanvas,
    ThumbnailGrid,
)


class AnnotateTab(QWidget):
    """图像标注页。"""

    def __init__(
        self,
        vm: AnnotateViewModel,
        category_vm: CategoryViewModel | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._vm = vm
        self._category_vm = category_vm
        self._canvas_selected = -1
        self._done = 0
        self._total = 0
        self._build_ui()
        self._bind()
        self._refresh_classes()
        self._vm.refresh()

    # -----------------------------------------------------------
    # UI
    # -----------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        root.addWidget(self._build_toolbar())
        root.addWidget(self._build_preannotate_bar())

        body = QHBoxLayout()
        body.setSpacing(10)

        self.grid = ThumbnailGrid(self)
        self.grid.setFixedWidth(186)
        body.addWidget(self.grid)

        self.canvas = AnnotationCanvas(self)
        self.canvas.setMinimumHeight(420)
        body.addWidget(self.canvas, 1)

        side = QVBoxLayout()
        side.setSpacing(6)
        side.addWidget(BodyLabel("当前标注", self))
        self.item_list = QListWidget(self)
        self.item_list.setFixedWidth(200)
        side.addWidget(self.item_list, 1)
        self.delete_btn = PushButton("删除选中", self)
        side.addWidget(self.delete_btn)
        body.addLayout(side)

        root.addLayout(body, 1)

        self.status_label = CaptionLabel("未加载数据集", self)
        root.addWidget(self.status_label)

    def _build_toolbar(self) -> CardWidget:
        toolbar = CardWidget(self)
        layout = QHBoxLayout(toolbar)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(10)

        self.mode_seg = SegmentedWidget(toolbar)
        for key, text in (
            (MODE_BROWSE, "浏览"),
            (MODE_BOX, "矩形"),
            (MODE_POLYGON, "多边形"),
        ):
            self.mode_seg.addItem(key, text, onClick=lambda k=key: self._on_mode(k))
        layout.addWidget(self.mode_seg)

        layout.addWidget(BodyLabel("类别", toolbar))
        self.class_combo = ComboBox(toolbar)
        self.class_combo.setMinimumWidth(140)
        layout.addWidget(self.class_combo)
        layout.addStretch(1)

        self.prev_btn = PushButton("上一张", toolbar)
        self.next_btn = PushButton("下一张", toolbar)
        self.save_btn = PrimaryPushButton("保存", toolbar)
        self.clear_btn = PushButton("清空", toolbar)
        self.import_btn = PushButton("导入…", toolbar)
        self.export_combo = ComboBox(toolbar)
        for key, text in (
            ("coco", "COCO json"),
            ("voc", "VOC xml"),
            ("yolo", "YOLO txt"),
        ):
            self.export_combo.addItem(text, userData=key)
        self.export_btn = PushButton("导出…", toolbar)

        for widget in (
            self.prev_btn, self.next_btn, self.save_btn, self.clear_btn,
            self.import_btn, self.export_combo, self.export_btn,
        ):
            layout.addWidget(widget)
        return toolbar

    def _build_preannotate_bar(self) -> CardWidget:
        """半自动预标注工具条。"""
        card = CardWidget(self)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(10)

        layout.addWidget(BodyLabel("检测权重", card))
        self.detect_edit = LineEdit(card)
        self.detect_edit.setText(DEFAULT_DETECT_WEIGHTS)
        self.detect_edit.setMinimumWidth(170)
        layout.addWidget(self.detect_edit)
        browse_btn = PushButton("浏览…", card)
        browse_btn.clicked.connect(self._on_browse_detect)
        layout.addWidget(browse_btn)

        layout.addWidget(BodyLabel("置信度", card))
        self.conf_spin = QDoubleSpinBox(card)
        self.conf_spin.setDecimals(2)
        self.conf_spin.setSingleStep(0.05)
        self.conf_spin.setRange(0.05, 0.95)
        self.conf_spin.setValue(0.25)
        layout.addWidget(self.conf_spin)

        self.detect_btn = PrimaryPushButton("预标注未标注图片", card)
        layout.addWidget(self.detect_btn)
        self.sam_btn = PushButton("SAM 细化选中", card)
        layout.addWidget(self.sam_btn)
        layout.addStretch(1)

        self.progress_bar = ProgressBar(card)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setFixedWidth(170)
        layout.addWidget(self.progress_bar)
        self.task_label = CaptionLabel("", card)
        layout.addWidget(self.task_label)
        return card

    # -----------------------------------------------------------
    # 类别
    # -----------------------------------------------------------
    def _refresh_classes(self) -> None:
        classes = self._vm.class_items()
        self.class_combo.blockSignals(True)
        self.class_combo.clear()
        for cls in classes:
            self.class_combo.addItem(cls.name, userData=cls.cls_id)
        self.class_combo.blockSignals(False)
        self.canvas.set_classes(classes)
        if classes:
            self.canvas.set_pending_class(self.class_combo.currentData())

    def _on_classes_changed(self, _classes) -> None:
        self._refresh_classes()

    def _on_class_changed(self, _index: int) -> None:
        value = self.class_combo.currentData()
        if value is not None:
            self.canvas.set_pending_class(int(value))

    # -----------------------------------------------------------
    # 交互
    # -----------------------------------------------------------
    def _on_mode(self, key: str) -> None:
        self.canvas.set_mode(key)

    def _on_images(self, paths: list) -> None:
        self.grid.set_images(paths)
        self.grid.set_current_index(0)
        self._refresh_marks()
        self._update_status()

    def _on_image_changed(self, index: int) -> None:
        path = self._vm.current_path()
        if path is not None:
            self.canvas.set_image(path)
        self.grid.set_current_index(index)
        self._update_status()

    def _on_annotation_loaded(self, annotation) -> None:
        self.canvas.set_annotations(annotation.items if annotation else [])
        self._rebuild_item_list(annotation)
        self._update_status()

    def _on_status(self, done: int, total: int) -> None:
        self._done, self._total = done, total
        self._refresh_marks()
        self._update_status()

    def _on_grid_activated(self, index: int) -> None:
        self._vm.set_current(index)

    def _on_canvas_selection(self, index: int) -> None:
        self._canvas_selected = index
        if 0 <= index < self.item_list.count():
            self.item_list.blockSignals(True)
            self.item_list.setCurrentRow(index)
            self.item_list.blockSignals(False)

    def _on_item_row(self, row: int) -> None:
        if row >= 0:
            self.canvas.select(row)

    def _on_delete(self) -> None:
        if self._canvas_selected < 0:
            InfoBar.warning(
                title="请先选择标注", content="", parent=self,
                position=InfoBarPosition.TOP_RIGHT, duration=2500,
            )
            return
        self._vm.remove_annotation(self._canvas_selected)

    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "导入标注", "",
            "标注文件 (*.json *.xml *.txt);;COCO (*.json);;VOC (*.xml);;YOLO (*.txt)",
        )
        if not path:
            return
        # 选择 YOLO 标签文件时，导入其所在目录的全部标签
        target = str(Path(path).parent) if path.lower().endswith(".txt") else path
        self._vm.import_annotations(target)

    def _on_export(self) -> None:
        fmt = self.export_combo.currentData()
        if fmt == "coco":
            path, _ = QFileDialog.getSaveFileName(
                self, "导出 COCO", "annotations.json", "COCO json (*.json)",
            )
            if path:
                self._vm.export_annotations(path, "coco")
            return
        directory = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if directory:
            self._vm.export_annotations(directory, fmt)

    # -----------------------------------------------------------
    # 半自动预标注
    # -----------------------------------------------------------
    def _on_browse_detect(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择检测权重", "", "PyTorch Weight (*.pt)",
        )
        if path:
            self.detect_edit.setText(path)

    def _on_preannotate(self) -> None:
        self._vm.preannotate(
            weights=self.detect_edit.text().strip() or DEFAULT_DETECT_WEIGHTS,
            conf=self.conf_spin.value(),
        )

    def _on_sam(self) -> None:
        if self._canvas_selected < 0:
            InfoBar.warning(
                title="请先选择一个标注", content="", parent=self,
                position=InfoBarPosition.TOP_RIGHT, duration=2500,
            )
            return
        self._vm.refine_with_sam(self._canvas_selected, DEFAULT_SAM_WEIGHTS)

    def _on_task_started(self, name: str) -> None:
        self.progress_bar.setValue(0)
        self.task_label.setText(f"{name}…")
        self._set_busy(True)

    def _on_task_progress(self, percent: int, text: str) -> None:
        self.progress_bar.setValue(percent)
        if text:
            self.task_label.setText(text)

    def _on_task_finished(self, text: str) -> None:
        self.progress_bar.setValue(100)
        self.task_label.setText(text)
        self._set_busy(False)

    def _on_task_failed(self, text: str) -> None:
        self.task_label.setText(text)
        self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        self.detect_btn.setEnabled(not busy)
        self.sam_btn.setEnabled(not busy)

    # -----------------------------------------------------------
    # 渲染
    # -----------------------------------------------------------
    def _rebuild_item_list(self, annotation) -> None:
        names = {cls.cls_id: cls.name for cls in self._vm.class_items()}
        self._canvas_selected = -1
        self.item_list.blockSignals(True)
        self.item_list.clear()
        if annotation is not None:
            for index, item in enumerate(annotation.items):
                kind = "矩形" if item.is_box else "多边形"
                name = names.get(item.cls_id, item.cls_id)
                self.item_list.addItem(f"{index + 1}. {name} · {kind}")
        self.item_list.blockSignals(False)

    def _refresh_marks(self) -> None:
        self.grid.set_annotated(self._vm.annotated_flags())

    def _update_status(self) -> None:
        total = len(self._vm.images)
        if total == 0:
            self.status_label.setText("未加载数据集")
            return
        annotation = self._vm.current
        current = annotation.count if annotation else 0
        self.status_label.setText(
            f"第 {self._vm.index + 1} / {total} 张 · 当前标注 {current} 个"
            f" · 已标注 {self._done} / {self._total}"
        )

    # -----------------------------------------------------------
    # 绑定
    # -----------------------------------------------------------
    def _bind(self) -> None:
        self._vm.imageListChanged.connect(self._on_images)
        self._vm.imageChanged.connect(self._on_image_changed)
        self._vm.annotationLoaded.connect(self._on_annotation_loaded)
        self._vm.statusChanged.connect(self._on_status)
        self._vm.taskStarted.connect(self._on_task_started)
        self._vm.taskProgress.connect(self._on_task_progress)
        self._vm.taskFinished.connect(self._on_task_finished)
        self._vm.taskFailed.connect(self._on_task_failed)

        self.canvas.annotationAdded.connect(self._vm.add_annotation)
        self.canvas.deleteRequested.connect(self._on_delete)
        self.canvas.selectionChanged.connect(self._on_canvas_selection)

        self.grid.imageActivated.connect(self._on_grid_activated)
        self.item_list.currentRowChanged.connect(self._on_item_row)

        self.prev_btn.clicked.connect(self._vm.prev_image)
        self.next_btn.clicked.connect(self._vm.next_image)
        self.save_btn.clicked.connect(self._vm.save)
        self.clear_btn.clicked.connect(self._vm.clear_annotations)
        self.import_btn.clicked.connect(self._on_import)
        self.export_btn.clicked.connect(self._on_export)
        self.delete_btn.clicked.connect(self._on_delete)
        self.class_combo.currentIndexChanged.connect(self._on_class_changed)

        if self._category_vm is not None:
            self._category_vm.classesChanged.connect(self._on_classes_changed)
