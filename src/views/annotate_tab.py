"""图像标注页：单张图片标注工作台。

界面结构参照 Halcon DLT 的标注视图：
    左侧：当前图像信息 + 图像列表（缩略图导航）
    中间：绘制工具条 + 预标注工具条 + 标注画布
    右侧：导航器、显示（亮度/对比度）、标注对象列表、备注
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    LineEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    SegmentedWidget,
    Slider,
    StrongBodyLabel,
)

from src.services.autolabel_service import DEFAULT_DETECT_WEIGHTS, DEFAULT_SAM_WEIGHTS
from src.viewmodels.annotate_vm import AnnotateViewModel
from src.viewmodels.category_vm import CategoryViewModel
from src.views.data_widgets import side_column
from src.views.widgets import (
    MODE_BOX,
    MODE_BROWSE,
    MODE_POLYGON,
    THUMB_SMALL,
    AnnotationCanvas,
    Navigator,
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
        self._syncing = False
        # 本页不可见时只记录待渲染的图片，切回本页再画缩略图
        self._pending_paths: list[str] = []
        self._stale = True
        self._build_ui()
        self._bind()
        self._refresh_classes()
        self._vm.refresh()

    # -----------------------------------------------------------
    # UI
    # -----------------------------------------------------------
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(12)

        root.addWidget(self._build_left_column())
        root.addLayout(self._build_center(), 1)
        root.addWidget(self._build_right_column())

    # --------------------------------------------------- 左栏
    def _build_left_column(self) -> QWidget:
        current_card = CardWidget(self)
        layout = QVBoxLayout(current_card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        layout.addWidget(StrongBodyLabel("当前图像", current_card))

        self.preview = QLabel(current_card)
        self.preview.setFixedHeight(150)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setStyleSheet("background:#1B1B1B; border-radius:4px;")
        layout.addWidget(self.preview)

        self.image_name = BodyLabel("—", current_card)
        self.image_name.setWordWrap(True)
        layout.addWidget(self.image_name)

        self.image_meta = CaptionLabel("尺寸：—  状态：—", current_card)
        self.image_meta.setWordWrap(True)
        layout.addWidget(self.image_meta)

        nav_row = QHBoxLayout()
        nav_row.setSpacing(6)
        self.prev_btn = PushButton("上一张", current_card)
        self.next_btn = PushButton("下一张", current_card)
        nav_row.addWidget(self.prev_btn)
        nav_row.addWidget(self.next_btn)
        layout.addLayout(nav_row)

        jump_row = QHBoxLayout()
        jump_row.setSpacing(6)
        self.jump_spin = QSpinBox(current_card)
        self.jump_spin.setRange(1, 1)
        jump_row.addWidget(self.jump_spin, 1)
        self.jump_btn = PushButton("跳转", current_card)
        jump_row.addWidget(self.jump_btn)
        layout.addLayout(jump_row)

        list_card = CardWidget(self)
        list_layout = QVBoxLayout(list_card)
        list_layout.setContentsMargins(14, 12, 14, 12)
        list_layout.setSpacing(6)
        list_layout.addWidget(StrongBodyLabel("图像列表", list_card))
        self.grid = ThumbnailGrid(list_card)
        self.grid.set_thumb_size(THUMB_SMALL)
        list_layout.addWidget(self.grid, 1)

        return side_column(current_card, list_card, width=240)

    # --------------------------------------------------- 中间
    def _build_center(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(10)
        column.addWidget(self._build_toolbar())
        column.addWidget(self._build_preannotate_bar())
        column.addWidget(self._build_canvas_card(), 1)
        return column

    def _build_toolbar(self) -> CardWidget:
        card = CardWidget(self)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(10)

        self.mode_seg = SegmentedWidget(card)
        for key, text in (
            (MODE_BROWSE, "浏览"),
            (MODE_BOX, "矩形"),
            (MODE_POLYGON, "多边形"),
        ):
            self.mode_seg.addItem(key, text, onClick=lambda k=key: self._on_mode(k))
        layout.addWidget(self.mode_seg)
        self.mode_seg.setCurrentItem(MODE_BOX)

        layout.addWidget(BodyLabel("类别", card))
        self.class_combo = ComboBox(card)
        self.class_combo.setMinimumWidth(150)
        layout.addWidget(self.class_combo)

        # 旋转框任务的角度微调（仅「对象检测·旋转框」类型显示）
        self.rotate_spin = QDoubleSpinBox(card)
        self.rotate_spin.setRange(-180.0, 180.0)
        self.rotate_spin.setDecimals(1)
        self.rotate_spin.setSingleStep(5.0)
        self.rotate_spin.setValue(0.0)
        self.rotate_spin.setSuffix(" °")
        self.rotate_spin.setFixedWidth(96)
        layout.addWidget(self.rotate_spin)
        self.rotate_btn = PushButton("旋转选中", card)
        layout.addWidget(self.rotate_btn)
        self.rotate_spin.setVisible(False)
        self.rotate_btn.setVisible(False)

        layout.addStretch(1)

        self.save_btn = PrimaryPushButton("保存标注", card)
        self.clear_btn = PushButton("清空", card)
        self.import_btn = PushButton("导入标注", card)
        self.export_combo = ComboBox(card)
        for key, text in (
            ("coco", "COCO json"),
            ("voc", "VOC xml"),
            ("yolo", "YOLO txt"),
        ):
            self.export_combo.addItem(text, userData=key)
        self.export_btn = PushButton("导出标注", card)
        for widget in (self.save_btn, self.clear_btn, self.import_btn,
                       self.export_combo, self.export_btn):
            layout.addWidget(widget)
        return card

    def _build_preannotate_bar(self) -> CardWidget:
        card = CardWidget(self)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(10)

        layout.addWidget(BodyLabel("检测权重", card))
        self.detect_edit = LineEdit(card)
        self.detect_edit.setText(DEFAULT_DETECT_WEIGHTS)
        self.detect_edit.setMinimumWidth(160)
        layout.addWidget(self.detect_edit)
        browse_btn = PushButton("浏览", card)
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
        self.sam_btn = PushButton("SAM 细化选中", card)
        layout.addWidget(self.detect_btn)
        layout.addWidget(self.sam_btn)
        layout.addStretch(1)

        self.progress_bar = ProgressBar(card)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setFixedWidth(150)
        layout.addWidget(self.progress_bar)
        self.task_label = CaptionLabel("", card)
        layout.addWidget(self.task_label)
        return card

    def _build_canvas_card(self) -> CardWidget:
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        strip = QHBoxLayout()
        strip.setSpacing(10)
        self.strip_position = StrongBodyLabel("—", card)
        strip.addWidget(self.strip_position)
        strip.addWidget(CaptionLabel("·", card))
        self.strip_name = CaptionLabel("未加载图片", card)
        strip.addWidget(self.strip_name)
        strip.addStretch(1)
        self.strip_state = CaptionLabel("未标注", card)
        strip.addWidget(self.strip_state)
        layout.addLayout(strip)

        self.canvas = AnnotationCanvas(card)
        self.canvas.setMinimumHeight(360)
        layout.addWidget(self.canvas, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.footer_status = CaptionLabel("已标注 0 / 0", card)
        footer.addWidget(self.footer_status)
        layout.addLayout(footer)
        return card

    # --------------------------------------------------- 右栏
    def _build_right_column(self) -> QWidget:
        navigator_card = CardWidget(self)
        layout = QVBoxLayout(navigator_card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        layout.addWidget(StrongBodyLabel("导航器", navigator_card))
        self.navigator = Navigator(navigator_card)
        layout.addWidget(self.navigator)
        self.fit_btn = PushButton("适应窗口", navigator_card)
        layout.addWidget(self.fit_btn)

        display_card = CardWidget(self)
        display_layout = QVBoxLayout(display_card)
        display_layout.setContentsMargins(14, 12, 14, 12)
        display_layout.setSpacing(6)
        display_layout.addWidget(StrongBodyLabel("显示", display_card))

        display_layout.addWidget(CaptionLabel("亮度", display_card))
        self.brightness_slider = Slider(Qt.Orientation.Horizontal, display_card)
        self.brightness_slider.setRange(-100, 100)
        display_layout.addWidget(self.brightness_slider)

        display_layout.addWidget(CaptionLabel("对比度", display_card))
        self.contrast_slider = Slider(Qt.Orientation.Horizontal, display_card)
        self.contrast_slider.setRange(-100, 100)
        display_layout.addWidget(self.contrast_slider)

        self.reset_view_btn = PushButton("重置显示", display_card)
        display_layout.addWidget(self.reset_view_btn)

        item_card = CardWidget(self)
        item_layout = QVBoxLayout(item_card)
        item_layout.setContentsMargins(14, 12, 14, 12)
        item_layout.setSpacing(6)
        item_layout.addWidget(StrongBodyLabel("标注对象", item_card))
        self.item_list = QListWidget(item_card)
        self.item_list.setMinimumHeight(120)
        item_layout.addWidget(self.item_list)
        self.delete_btn = PushButton("删除选中", item_card)
        item_layout.addWidget(self.delete_btn)

        note_card = CardWidget(self)
        note_layout = QVBoxLayout(note_card)
        note_layout.setContentsMargins(14, 12, 14, 12)
        note_layout.setSpacing(6)
        note_layout.addWidget(StrongBodyLabel("备注", note_card))
        self.note_edit = QPlainTextEdit(note_card)
        self.note_edit.setPlaceholderText("为该图像填写备注…")
        self.note_edit.setFixedHeight(96)
        note_layout.addWidget(self.note_edit)
        self.note_btn = PushButton("保存备注", note_card)
        note_layout.addWidget(self.note_btn)

        return side_column(
            navigator_card, display_card, item_card, note_card, width=252
        )

    # -----------------------------------------------------------
    # 绑定
    # -----------------------------------------------------------
    def _bind(self) -> None:
        vm = self._vm
        vm.imageListChanged.connect(self._on_images)
        vm.imageChanged.connect(self._on_image_changed)
        vm.annotationLoaded.connect(self._on_annotation_loaded)
        vm.noteLoaded.connect(self._on_note_loaded)
        vm.statusChanged.connect(self._on_status)
        vm.taskStarted.connect(self._on_task_started)
        vm.taskProgress.connect(self._on_task_progress)
        vm.taskFinished.connect(self._on_task_finished)
        vm.taskFailed.connect(self._on_task_failed)

        self.canvas.annotationAdded.connect(vm.add_annotation)
        self.canvas.annotationChanged.connect(vm.mark_dirty)
        self.rotate_btn.clicked.connect(self._on_rotate)
        self.canvas.deleteRequested.connect(self._on_delete)
        self.canvas.selectionChanged.connect(self._on_canvas_selection)
        self.navigator.attach(self.canvas)

        self.grid.imageActivated.connect(self._on_grid_activated)
        self.item_list.currentRowChanged.connect(self._on_item_row)

        self.prev_btn.clicked.connect(vm.prev_image)
        self.next_btn.clicked.connect(vm.next_image)
        self.jump_btn.clicked.connect(self._on_jump)
        self.save_btn.clicked.connect(vm.save)
        self.clear_btn.clicked.connect(vm.clear_annotations)
        self.import_btn.clicked.connect(self._on_import)
        self.export_btn.clicked.connect(self._on_export)
        self.delete_btn.clicked.connect(self._on_delete)
        self.class_combo.currentIndexChanged.connect(self._on_class_changed)
        self.detect_btn.clicked.connect(self._on_preannotate)
        self.sam_btn.clicked.connect(self._on_sam)

        self.fit_btn.clicked.connect(self._on_fit_view)
        self.brightness_slider.valueChanged.connect(self.canvas.set_brightness)
        self.contrast_slider.valueChanged.connect(self.canvas.set_contrast)
        self.reset_view_btn.clicked.connect(self._on_reset_display)
        self.note_btn.clicked.connect(self._on_save_note)

        if self._category_vm is not None:
            self._category_vm.classesChanged.connect(self._on_classes_changed)

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

    def apply_project_type(self) -> None:
        """按项目类型限定标注方式（项目类型决定标注工具，参照 DLT）。"""
        mode = self._vm.annotation_mode()
        target = {
            "box": MODE_BOX, "obb": MODE_BOX, "polygon": MODE_POLYGON,
        }.get(mode, MODE_BROWSE)
        self.mode_seg.setCurrentItem(target)
        self.canvas.set_mode(target)

        is_obb = mode == "obb"
        self.rotate_spin.setVisible(is_obb)
        self.rotate_btn.setVisible(is_obb)
        if mode == "none":
            self.status_message(
                "该任务类型按整图判定，无需框选标注；类别可在「图库」页管理"
            )
        elif is_obb:
            self.status_message(
                "旋转框：拖出矩形后，用「旋转选中」微调角度（自动转四点框）"
            )

    def _on_rotate(self) -> None:
        delta = self.rotate_spin.value()
        if not delta:
            self.status_message("请先设置旋转角度（度）")
            return
        if self.canvas.rotate_selected(delta):
            self.rotate_spin.setValue(0.0)
            self.status_message(f"已旋转 {delta:+.1f}°，记得点击「保存标注」")
        else:
            self.status_message("请先在画布或「标注对象」列表中选中一个标注")

    def _on_jump(self) -> None:
        self._vm.set_current(self.jump_spin.value() - 1)

    def _on_fit_view(self) -> None:
        self.canvas.fit_to_view()
        self.status_message("已恢复为适应窗口")

    def _on_reset_display(self) -> None:
        self.brightness_slider.setValue(0)
        self.contrast_slider.setValue(0)
        self.canvas.reset_adjust()
        self.status_message("已重置亮度与对比度")

    def _on_save_note(self) -> None:
        path = self._vm.current_path()
        if path is None:
            self._vm.notify("请先选择一张图片", "warning")
            return
        self._vm.save_note(path, self.note_edit.toPlainText())
        self.status_message("备注已保存")

    def status_message(self, text: str) -> None:
        """页面级反馈：同时写入工具条提示与全局通知，避免「点了没反应」。"""
        self.task_label.setText(text)
        self._vm.notify(text)

    # -----------------------------------------------------------
    # 刷新
    # -----------------------------------------------------------
    def _on_images(self, paths: list) -> None:
        """图片清单变化：先记账，缩略图等本页可见时再画。"""
        self._pending_paths = [str(p) for p in paths]
        self.jump_spin.setRange(1, max(1, len(paths)))
        self._update_totals()
        if self.isVisible():
            self._render_grid()
        else:
            self._stale = True

    def _render_grid(self) -> None:
        """真正绘制图片列表（仅在页面可见时调用）。"""
        paths = self._pending_paths or []
        self.grid.set_images(paths, self._vm.annotated_flags())
        if paths:
            index = getattr(self._vm, "index", 0)
            self.grid.set_current_index(index if 0 <= index < len(paths) else 0)
        self._stale = False

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        if self._stale:
            self._render_grid()

    def _on_image_changed(self, index: int) -> None:
        path = self._vm.current_path()
        if path is not None:
            self.canvas.set_image(path)
            self.navigator.set_image(path)
            self.brightness_slider.setValue(0)
            self.contrast_slider.setValue(0)
            self.preview.setPixmap(
                QPixmap(str(path)).scaled(
                    self.preview.width() or 200, self.preview.height() or 150,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            self.image_name.setText(path.name)
            try:
                size = self._vm.current.width, self._vm.current.height
                self.image_meta.setText(f"尺寸：{size[0]} × {size[1]}")
            except AttributeError:
                self.image_meta.setText("尺寸：—")
        self.grid.set_current_index(index)
        self._syncing = True
        self.jump_spin.setValue(index + 1)
        self._syncing = False
        self._update_strip()

    def _on_annotation_loaded(self, annotation) -> None:
        self.canvas.set_annotations(annotation.items if annotation else [])
        self._rebuild_item_list(annotation)
        self._update_strip()

    def _on_note_loaded(self, text: str) -> None:
        self.note_edit.setPlainText(text or "")

    def _on_status(self, done: int, total: int) -> None:
        self._done, self._total = done, total
        self._refresh_marks()
        self._update_strip()

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
            self.status_message("请先选择标注对象")
            return
        self._vm.remove_annotation(self._canvas_selected)

    # -----------------------------------------------------------
    # 导入 / 导出
    # -----------------------------------------------------------
    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "导入标注", "",
            "标注文件 (*.json *.xml *.txt);;COCO (*.json);;VOC (*.xml);;YOLO (*.txt)",
        )
        if not path:
            return
        target = str(Path(path).parent) if path.lower().endswith(".txt") else path
        self._vm.import_annotations(target)

    def _on_export(self) -> None:
        fmt = self.export_combo.currentData()
        if fmt == "coco":
            path, _ = QFileDialog.getSaveFileName(
                self, "导出 COCO", "annotations.json", "COCO json (*.json)"
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
            self, "选择检测权重", "", "PyTorch Weight (*.pt)"
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
            self.status_message("请先选择一个标注对象")
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

    def _update_totals(self) -> None:
        self._done = self._vm.annotated_count()
        self._total = len(self._vm.images)
        self._update_strip()

    def _update_strip(self) -> None:
        total = len(self._vm.images)
        index = self._vm.index
        path = self._vm.current_path()
        percent = 0 if not total else round(self._done * 100 / total)
        self.footer_status.setText(
            f"已标注 {self._done} / {total}（{percent}%）"
        )
        if total == 0 or path is None:
            self.strip_position.setText("—")
            self.strip_name.setText("未加载图片")
            self.strip_state.setText("未标注")
            return
        annotation = self._vm.current
        count = annotation.count if annotation else 0
        self.strip_position.setText(f"第 {index + 1} / {total} 张")
        self.strip_name.setText(
            f"{path.name} · 当前标注 {count} 个 · 已标注 {self._done} / {self._total}"
        )
        label_dir = self._vm.label_dir()
        annotated = False
        if label_dir is not None:
            from src.services.annotation_service import AnnotationService

            annotated = AnnotationService.label_path_for(path, label_dir).is_file()
        self.strip_state.setText("已标注" if annotated else "未标注")
