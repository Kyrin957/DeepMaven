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
    QAbstractItemView,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPlainTextEdit,
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
    ScrollArea,
    SegmentedWidget,
    Slider,
    StrongBodyLabel,
)

from src.services.autolabel_service import DEFAULT_DETECT_WEIGHTS, DEFAULT_SAM_WEIGHTS
from src.viewmodels.annotate_vm import AnnotateViewModel
from src.viewmodels.category_vm import CategoryViewModel
from src.views.data_widgets import side_column
from src.views.ui import (
    FlowContainer,
    SafeDoubleSpinBox,
    SafeSpinBox,
    ToolGroup,
)
from src.views.ui import tokens as T
from src.views.widgets import (
    MODE_BOX,
    MODE_BROWSE,
    MODE_MASK,
    MODE_POLYGON,
    MODE_TEXT,
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
        self._edit_before: list | None = None     # 几何编辑前的标注快照（撤销用）
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
        root.setContentsMargins(T.SPACE_XL, T.SPACE_LG, T.SPACE_XL, T.SPACE_LG)
        root.setSpacing(T.SPACE_LG)

        root.addWidget(self._build_left_column())
        root.addLayout(self._build_center(), 1)
        root.addWidget(self._build_right_column())

    # --------------------------------------------------- 左栏
    def _build_left_column(self) -> QWidget:
        current_card = CardWidget(self)
        layout = QVBoxLayout(current_card)
        layout.setContentsMargins(T.CARD_PAD_H, T.CARD_PAD_V, T.CARD_PAD_H, T.CARD_PAD_V)
        layout.setSpacing(T.SPACE_SM)
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
        nav_row.setSpacing(T.SPACE_SM)
        self.prev_btn = PushButton("上一张", current_card)
        self.next_btn = PushButton("下一张", current_card)
        nav_row.addWidget(self.prev_btn)
        nav_row.addWidget(self.next_btn)
        layout.addLayout(nav_row)

        jump_row = QHBoxLayout()
        jump_row.setSpacing(T.SPACE_SM)
        self.jump_spin = SafeSpinBox(current_card)
        self.jump_spin.setRange(1, 1)
        self.jump_spin.setFixedWidth(
            T.field_width(self.jump_spin, T.STEPPER_CHARS_NARROW)
        )
        jump_row.addWidget(self.jump_spin)
        self.jump_btn = PushButton("跳转", current_card)
        jump_row.addWidget(self.jump_btn)
        layout.addLayout(jump_row)

        list_card = CardWidget(self)
        list_layout = QVBoxLayout(list_card)
        list_layout.setContentsMargins(T.CARD_PAD_H, T.CARD_PAD_V, T.CARD_PAD_H, T.CARD_PAD_V)
        list_layout.setSpacing(T.SPACE_SM)
        list_layout.addWidget(StrongBodyLabel("图像列表", list_card))
        self.grid = ThumbnailGrid(list_card)
        self.grid.set_thumb_size(THUMB_SMALL)
        self.grid.set_wheel_zoom(True)      # Ctrl + 滚轮缩放缩略图
        list_layout.addWidget(self.grid, 1)

        return side_column(current_card, list_card, width=T.SIDE_W_NARROW)

    # --------------------------------------------------- 中间
    def _build_center(self) -> QVBoxLayout:
        """中间列：工具条 + 预标注栏 + 画布。

        整体放进滚动区：窗口较矮（或高分屏缩放把可用高度压小）时，
        两行工具条加上画布的最小高度可能超出可用高度；此时由本区滚动兜底，
        避免竖向空间不足把工具条挤到互相重叠（见开发文档 §4.4）。
        空间充足时内容撑满视口，画布照常占满剩余区域。
        """
        area = ScrollArea(self)
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        column = QVBoxLayout(body)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(T.SPACE_ML)
        column.addWidget(self._build_toolbar())
        column.addWidget(self._build_preannotate_bar())
        column.addWidget(self._build_canvas_card(), 1)
        area.setWidget(body)

        wrapper = QVBoxLayout()
        wrapper.setContentsMargins(0, 0, 0, 0)
        wrapper.setSpacing(0)
        wrapper.addWidget(area)
        return wrapper

    def _build_toolbar(self) -> CardWidget:
        """工具条：按逻辑分组容器化排布，宽度不足时**整组换行**。

        旧实现把 15+ 个控件塞进单行 ``QHBoxLayout``；一旦可用宽度不够
        （窄窗口、或高分屏下字体变宽），Qt 无法换行，只能让控件互相覆盖，
        于是出现「浏览｜矩形｜多边形｜存标」文字与指示条重叠。

        现在改用 ``FlowLayout`` + ``ToolGroup``：每个分组是独立容器，
        分组整体折到下一行，因此任意窗口宽度 / 缩放比例下都不会重叠。
        """
        card = CardWidget(self)
        # FlowContainer 只通过 sizeHint / minimumSizeHint 表达折行高度，
        # 不写显式最小尺寸，因此不会击穿页面的 Ignored 策略（见 §4.4）
        outer = QVBoxLayout(card)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        container = FlowContainer(
            card, spacing=T.SPACE_MD,
            margins=(T.SPACE_XL, T.SPACE_MD, T.SPACE_XL, T.SPACE_MD),
        )
        outer.addWidget(container)
        flow = container.flow()

        # ① 绘制工具（浏览 / 矩形 / 多边形 / 掩码；OCR 项目为 浏览 + 文本）
        self.mode_seg = SegmentedWidget(card)
        self._reload_tools("box")
        self.mode_seg.setCurrentItem(MODE_BOX)
        flow.addWidget(ToolGroup(self.mode_seg, parent=card))

        # ② 类别选择
        self.class_combo = ComboBox(card)
        self.class_combo.setMinimumWidth(140)
        flow.addWidget(
            ToolGroup(BodyLabel("类别", card), self.class_combo, parent=card)
        )

        # ③ 旋转角微调（仅「对象检测·旋转框」，整组显隐）
        self.rotate_spin = SafeDoubleSpinBox(card)
        self.rotate_spin.setRange(-180.0, 180.0)
        self.rotate_spin.setDecimals(1)
        self.rotate_spin.setSingleStep(5.0)
        self.rotate_spin.setValue(0.0)
        self.rotate_spin.setSuffix(" °")
        self.rotate_spin.setFixedWidth(
            T.field_width(self.rotate_spin, T.STEPPER_CHARS_NARROW)
        )
        self.rotate_btn = PushButton("旋转选中", card)
        self.rotate_group = ToolGroup(
            BodyLabel("角度", card), self.rotate_spin, self.rotate_btn, parent=card
        )
        self.rotate_group.setVisible(False)
        flow.addWidget(self.rotate_group)

        # ④ 掩码工具（画笔 / 橡皮 / 生成轮廓，整组显隐）
        self.brush_spin = SafeSpinBox(card)
        self.brush_spin.setRange(2, 300)
        self.brush_spin.setValue(24)
        self.brush_spin.setSuffix(" px")
        self.brush_spin.setFixedWidth(
            T.field_width(self.brush_spin, T.STEPPER_CHARS_NARROW)
        )
        self.erase_check = CheckBox("橡皮", card)
        self.brush_label = BodyLabel("笔刷", card)
        self.outline_btn = PushButton("生成轮廓", card)
        self.mask_clear_btn = PushButton("清除掩码", card)
        self.mask_group = ToolGroup(
            self.brush_label, self.brush_spin, self.erase_check,
            self.outline_btn, self.mask_clear_btn, parent=card,
        )
        self.mask_group.setVisible(False)
        flow.addWidget(self.mask_group)

        # ⑤ 孔洞（多边形模式下的子工具，整组显隐）
        self.hole_check = CheckBox("孔洞", card)
        self.hole_check.setToolTip("画出的多边形从选中实例中挖掉")
        self.hole_group = ToolGroup(self.hole_check, parent=card)
        self.hole_group.setVisible(False)
        flow.addWidget(self.hole_group)

        # ⑥ 标注存取（保存 / 清空 / 导入）
        self.save_btn = PrimaryPushButton("保存标注", card)
        self.clear_btn = PushButton("清空", card)
        self.import_btn = PushButton("导入标注", card)
        flow.addWidget(ToolGroup(
            self.save_btn, self.clear_btn, self.import_btn, parent=card,
        ))

        # ⑦ 导出标注（格式 + 按钮）。与 ⑥ 分开成两组：五件套合起来近 500px，
        # 最窄窗口下比工具条还宽，整组折行也放不下（会横向溢出被裁）。
        self.export_combo = ComboBox(card)
        for key, text in (
            ("coco", "COCO json"),
            ("voc", "VOC xml"),
            ("yolo", "YOLO txt"),
        ):
            self.export_combo.addItem(text, userData=key)
        self.export_btn = PushButton("导出标注", card)
        flow.addWidget(ToolGroup(
            self.export_combo, self.export_btn, parent=card,
        ))
        return card

    def _build_preannotate_bar(self) -> CardWidget:
        """预标注栏：同样容器化 + 可换行，避免窄窗口下控件互相重叠。"""
        card = CardWidget(self)
        outer = QVBoxLayout(card)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        container = FlowContainer(
            card, spacing=T.SPACE_MD,
            margins=(T.SPACE_XL, T.SPACE_MD, T.SPACE_XL, T.SPACE_MD),
        )
        outer.addWidget(container)
        flow = container.flow()

        self.detect_edit = LineEdit(card)
        self.detect_edit.setText(DEFAULT_DETECT_WEIGHTS)
        self.detect_edit.setMinimumWidth(180)
        browse_btn = PushButton("浏览", card)
        browse_btn.clicked.connect(self._on_browse_detect)
        flow.addWidget(ToolGroup(
            BodyLabel("检测权重", card), self.detect_edit, browse_btn, parent=card,
        ))

        self.conf_spin = SafeDoubleSpinBox(card)
        self.conf_spin.setDecimals(2)
        self.conf_spin.setSingleStep(0.05)
        self.conf_spin.setRange(0.05, 0.95)
        self.conf_spin.setValue(0.25)
        self.conf_spin.setFixedWidth(
            T.field_width(self.conf_spin, T.STEPPER_CHARS_NARROW)
        )
        flow.addWidget(
            ToolGroup(BodyLabel("置信度", card), self.conf_spin, parent=card)
        )

        self.detect_btn = PrimaryPushButton("预标注未标注图片", card)
        self.sam_btn = PushButton("SAM 细化选中", card)
        flow.addWidget(ToolGroup(self.detect_btn, self.sam_btn, parent=card))

        self.progress_bar = ProgressBar(card)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setFixedWidth(T.CTRL_W_LG)
        self.task_label = CaptionLabel("", card)
        flow.addWidget(ToolGroup(self.progress_bar, self.task_label, parent=card))
        return card

    def _build_canvas_card(self) -> CardWidget:
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(T.SPACE_LG, T.SPACE_ML, T.SPACE_LG, T.SPACE_ML)
        layout.setSpacing(T.SPACE_MD)

        strip = QHBoxLayout()
        strip.setSpacing(T.SPACE_ML)
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
        layout.setContentsMargins(T.CARD_PAD_H, T.CARD_PAD_V, T.CARD_PAD_H, T.CARD_PAD_V)
        layout.setSpacing(T.SPACE_SM)
        layout.addWidget(StrongBodyLabel("导航器", navigator_card))
        self.navigator = Navigator(navigator_card)
        layout.addWidget(self.navigator)
        self.fit_btn = PushButton("适应窗口", navigator_card)
        layout.addWidget(self.fit_btn)

        display_card = CardWidget(self)
        display_layout = QVBoxLayout(display_card)
        display_layout.setContentsMargins(T.CARD_PAD_H, T.CARD_PAD_V, T.CARD_PAD_H, T.CARD_PAD_V)
        display_layout.setSpacing(T.SPACE_SM)
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
        item_layout.setContentsMargins(T.CARD_PAD_H, T.CARD_PAD_V, T.CARD_PAD_H, T.CARD_PAD_V)
        item_layout.setSpacing(T.SPACE_SM)
        item_layout.addWidget(StrongBodyLabel("标注对象", item_card))
        self.item_list = QListWidget(item_card)
        self.item_list.setMinimumHeight(110)
        # 支持多选（Ctrl 点选 / Shift 连选），与画布选中同步
        self.item_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        item_layout.addWidget(self.item_list)

        action_row = QHBoxLayout()
        self.delete_btn = PushButton("删除选中", item_card)
        self.select_all_btn = PushButton("全选", item_card)
        action_row.addWidget(self.delete_btn)
        action_row.addWidget(self.select_all_btn)
        item_layout.addLayout(action_row)

        # 数值编辑（参照 DLT 的「编辑选中标注」表）
        self.edit_card = CardWidget(self)
        edit_layout = QVBoxLayout(self.edit_card)
        edit_layout.setContentsMargins(T.CARD_PAD_H, T.CARD_PAD_V, T.CARD_PAD_H, T.CARD_PAD_V)
        edit_layout.setSpacing(T.SPACE_SM)
        edit_layout.addWidget(StrongBodyLabel("编辑选中", self.edit_card))
        form = QFormLayout()
        form.setSpacing(T.SPACE_SM)
        self.item_class_combo = ComboBox(self.edit_card)
        self.item_class_combo.currentIndexChanged.connect(
            lambda _i: self._on_item_class_changed()
        )
        form.addRow("类别", self.item_class_combo)

        self.geo_spins: dict[str, SafeDoubleSpinBox] = {}
        for key, text in (("x", "X"), ("y", "Y"), ("w", "宽"), ("h", "高")):
            spin = SafeDoubleSpinBox(self.edit_card)
            spin.setRange(0.0, 100000.0)
            spin.setDecimals(1)
            spin.setFixedWidth(T.field_width(spin, T.STEPPER_CHARS))
            form.addRow(text, spin)
            self.geo_spins[key] = spin

        self.angle_spin = SafeDoubleSpinBox(self.edit_card)
        self.angle_spin.setRange(-360.0, 360.0)
        self.angle_spin.setDecimals(1)
        self.angle_spin.setSuffix(" °")
        self.angle_spin.setFixedWidth(
            T.field_width(self.angle_spin, T.STEPPER_CHARS_NARROW)
        )
        form.addRow("旋转", self.angle_spin)

        # 文本框转写（OCR 项目选中文本框时出现）
        self.text_edit = LineEdit(self.edit_card)
        self.text_edit.setPlaceholderText("文本内容")
        self.text_edit.textEdited.connect(self._on_text_edited)
        form.addRow("文本", self.text_edit)
        self._edit_form = form
        form.setRowVisible(self.text_edit, False)
        edit_layout.addLayout(form)

        edit_row = QHBoxLayout()
        self.apply_geo_btn = PushButton("应用", self.edit_card)
        self.apply_angle_btn = PushButton("旋转", self.edit_card)
        edit_row.addWidget(self.apply_geo_btn)
        edit_row.addWidget(self.apply_angle_btn)
        edit_layout.addLayout(edit_row)

        note_card = CardWidget(self)
        note_layout = QVBoxLayout(note_card)
        note_layout.setContentsMargins(T.CARD_PAD_H, T.CARD_PAD_V, T.CARD_PAD_H, T.CARD_PAD_V)
        note_layout.setSpacing(T.SPACE_SM)
        note_layout.addWidget(StrongBodyLabel("备注", note_card))
        self.note_edit = QPlainTextEdit(note_card)
        self.note_edit.setPlaceholderText("为该图像填写备注…")
        self.note_edit.setFixedHeight(96)
        note_layout.addWidget(self.note_edit)
        self.note_btn = PushButton("保存备注", note_card)
        note_layout.addWidget(self.note_btn)

        return side_column(
            navigator_card, display_card, item_card, self.edit_card, note_card,
            width=T.SIDE_W,
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
        self.canvas.selectionListChanged.connect(self._on_selection_list)
        self.canvas.classRequested.connect(self._on_class_key)
        self.canvas.imageStepRequested.connect(vm.step_image)
        # 画布内的几何编辑（拖动 / 微调 / 孔洞 / 旋转）先快照、后登记撤销
        self.canvas.editStarted.connect(self._on_edit_started)
        self.canvas.editFinished.connect(self._on_edit_finished)
        self.navigator.attach(self.canvas)

        self.grid.imageActivated.connect(self._on_grid_activated)
        self.item_list.currentRowChanged.connect(self._on_item_row)
        self.item_list.itemSelectionChanged.connect(self._on_list_selection)

        # 掩码 / 孔洞 / 数值编辑
        self.brush_spin.valueChanged.connect(
            lambda value: self.canvas.set_brush_size(float(value))
        )
        self.erase_check.stateChanged.connect(
            lambda _s: self.canvas.set_mask_erase(self.erase_check.isChecked())
        )
        self.outline_btn.clicked.connect(self._on_generate_outline)
        self.mask_clear_btn.clicked.connect(self._on_clear_mask)
        self.hole_check.stateChanged.connect(
            lambda _s: self.canvas.set_hole_mode(self.hole_check.isChecked())
        )
        self.apply_geo_btn.clicked.connect(self._on_apply_geometry)
        self.apply_angle_btn.clicked.connect(self._on_apply_angle)
        self.select_all_btn.clicked.connect(self.canvas.select_all)

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
        for combo in (self.class_combo, self.item_class_combo):
            combo.blockSignals(True)
            combo.clear()
            for cls in classes:
                combo.addItem(cls.name, userData=cls.cls_id)
            combo.blockSignals(False)
        self.canvas.set_classes(classes)
        if classes:
            self.canvas.set_pending_class(self.class_combo.currentData())
        self._sync_property_fields()

    def _on_classes_changed(self, _classes) -> None:
        self._refresh_classes()

    def _on_class_changed(self, _index: int) -> None:
        value = self.class_combo.currentData()
        if value is not None:
            self.canvas.set_pending_class(int(value))

    # -----------------------------------------------------------
    # 交互
    # -----------------------------------------------------------
    def _reload_tools(self, mode: str) -> None:
        """按标注方式重建工具条：OCR 项目为「浏览 + 文本」，其余为原四件套。"""
        tools = (
            [(MODE_BROWSE, "浏览"), (MODE_TEXT, "文本")]
            if mode == "text"
            else [
                (MODE_BROWSE, "浏览"), (MODE_BOX, "矩形"),
                (MODE_POLYGON, "多边形"), (MODE_MASK, "掩码"),
            ]
        )
        signature = "|".join(key for key, _text in tools)
        if getattr(self, "_tool_signature", "") == signature:
            return
        self.mode_seg.clear()
        for key, text in tools:
            self.mode_seg.addItem(key, text, onClick=lambda k=key: self._on_mode(k))
        self._tool_signature = signature

    def _on_mode(self, key: str) -> None:
        self.canvas.set_mode(key)
        # 整组显隐（配合 FlowLayout：隐藏的分组不占位、不重排错位）
        self.mask_group.setVisible(key == MODE_MASK)
        self.hole_group.setVisible(key == MODE_POLYGON)
        self.hole_check.setChecked(False)

    # -----------------------------------------------------------
    # 掩码 / 孔洞 / 数值编辑
    # -----------------------------------------------------------
    def _on_generate_outline(self) -> None:
        """掩码 → 多边形实例（孔洞自动并入外轮廓）。"""
        count = self.canvas.generate_from_mask()
        if count:
            self.status_message(f"已生成 {count} 个实例")
        else:
            self.status_message("掩码为空")

    def _on_clear_mask(self) -> None:
        self.canvas.clear_mask()
        self.status_message("掩码已清空")

    def _on_class_key(self, position: int) -> None:
        """数字键 1-9 选类别。"""
        if 0 <= position < self.class_combo.count():
            self.class_combo.setCurrentIndex(position)

    def _on_edit_started(self) -> None:
        """画布开始几何编辑：先快照，供撤销回退。"""
        self._edit_before = self._vm.snapshot_items()

    def _on_edit_finished(self, label: str) -> None:
        if self._edit_before is None:
            return
        self._vm.push_undo(
            label or "修改标注", self._edit_before, key=f"edit:{self._vm.index}"
        )
        self._edit_before = None
        self._sync_property_fields()

    def _on_selection_list(self, indexes: list) -> None:
        """画布多选 → 同步列表与属性面板。"""
        self._syncing = True
        try:
            self.item_list.blockSignals(True)
            self.item_list.clearSelection()
            for index in indexes:
                if 0 <= index < self.item_list.count():
                    self.item_list.item(index).setSelected(True)
            if 0 <= self._canvas_selected < self.item_list.count():
                self.item_list.setCurrentRow(self._canvas_selected)
            self.item_list.blockSignals(False)
        finally:
            self._syncing = False
        self._sync_property_fields()

    def _on_list_selection(self) -> None:
        """列表多选 → 同步画布。"""
        if self._syncing:
            return
        indexes = sorted(
            self.item_list.row(item) for item in self.item_list.selectedItems()
        )
        if not indexes:
            return
        current = self.item_list.currentRow()
        primary = current if current in indexes else indexes[-1]
        self.canvas.select_many(indexes, primary=primary)
        self._canvas_selected = primary
        self._sync_property_fields()

    def _sync_property_fields(self) -> None:
        """把主选中标注的位置 / 尺寸填进数值框。"""
        current = self._vm.current
        primary = self._canvas_selected
        valid = (
            current is not None
            and 0 <= primary < len(current.items)
        )
        for widget in (
            *self.geo_spins.values(), self.angle_spin,
            self.apply_geo_btn, self.apply_angle_btn, self.item_class_combo,
        ):
            widget.setEnabled(valid)
        if not valid:
            self._edit_form.setRowVisible(self.text_edit, False)
            return
        item = current.items[primary]
        width = max(1, int(current.width or 1))
        height = max(1, int(current.height or 1))
        x1, y1, x2, y2 = item.bounds()
        self._syncing = True
        try:
            self.geo_spins["x"].setValue(x1 * width)
            self.geo_spins["y"].setValue(y1 * height)
            self.geo_spins["w"].setValue(max(0.0, (x2 - x1) * width))
            self.geo_spins["h"].setValue(max(0.0, (y2 - y1) * height))
            position = self.item_class_combo.findData(item.cls_id)
            self.item_class_combo.setCurrentIndex(max(0, position))
            # 转写：仅文本框（OCR）显示该行
            self.text_edit.setText(str(item.text or ""))
            self.text_edit.setEnabled(item.is_text)
            self._edit_form.setRowVisible(self.text_edit, item.is_text)
        finally:
            self._syncing = False

    def _on_text_edited(self, text: str) -> None:
        """录入 / 修改文本框转写。"""
        if self._syncing or self._canvas_selected < 0:
            return
        if self._vm.set_text(self._canvas_selected, text):
            self.status_message("已更新文本")
            self._rebuild_item_list(self._vm.current)

    def _on_apply_geometry(self) -> None:
        """按数值框设置位置与尺寸（像素）。"""
        if self._canvas_selected < 0:
            self.status_message("请先选择标注对象")
            return
        applied = self._vm.set_geometry(
            self._canvas_selected,
            self.geo_spins["x"].value(), self.geo_spins["y"].value(),
            self.geo_spins["w"].value(), self.geo_spins["h"].value(),
        )
        if applied:
            self.status_message("已应用位置与尺寸")

    def _on_apply_angle(self) -> None:
        delta = self.angle_spin.value()
        if not delta:
            self.status_message("请先设置旋转角度")
            return
        if self.canvas.rotate_selected(delta):
            self.angle_spin.setValue(0.0)
            self.status_message(f"已旋转 {delta:+.1f}°")
        else:
            self.status_message("请先选择标注对象")

    def _on_item_class_changed(self) -> None:
        """编辑卡的类别下拉：批量修改选中标注的类别。"""
        if self._syncing:
            return
        cls_id = self.item_class_combo.currentData()
        indexes = self.canvas.selection()
        if cls_id is None or not indexes:
            return
        changed = self._vm.set_class_for(indexes, int(cls_id))
        if changed:
            self.status_message(f"已修改 {changed} 个标注的类别")

    def apply_project_type(self) -> None:
        """按项目类型限定标注方式（项目类型决定标注工具，参照 DLT）。"""
        mode = self._vm.annotation_mode()
        target = {
            "box": MODE_BOX, "obb": MODE_BOX, "polygon": MODE_POLYGON,
            "text": MODE_TEXT,
        }.get(mode, MODE_BROWSE)
        self._reload_tools(mode)
        self.mode_seg.setCurrentItem(target)
        self._on_mode(target)

        is_obb = mode == "obb"
        self.rotate_group.setVisible(is_obb)
        if mode == "none":
            self.status_message("按整图判定，无需框选")
        elif is_obb:
            self.status_message("旋转框：拖框后用「旋转选中」调角度")

    def _on_rotate(self) -> None:
        delta = self.rotate_spin.value()
        if not delta:
            self.status_message("请先设置旋转角度（度）")
            return
        if self.canvas.rotate_selected(delta):
            self.rotate_spin.setValue(0.0)
            self.status_message(f"已旋转 {delta:+.1f}°")
        else:
            self.status_message("请先选中一个标注")

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
        self._sync_property_fields()

    def _on_item_row(self, row: int) -> None:
        if row >= 0:
            self.canvas.select(row)

    def _on_delete(self) -> None:
        """删除选中的标注（支持多选）。"""
        indexes = self.canvas.selection()
        if not indexes and self._canvas_selected >= 0:
            indexes = [self._canvas_selected]
        if not indexes:
            self.status_message("请先选择标注对象")
            return
        removed = self._vm.remove_many(indexes)
        if removed:
            self.status_message(f"已删除 {removed} 个标注")

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
                kind = {"box": "矩形", "polygon": "多边形", "mask": "掩码",
                        "text": "文本"}.get(item.kind, item.kind)
                name = names.get(item.cls_id, item.cls_id)
                label = f"{index + 1}. {name} · {kind}"
                if item.is_text:
                    label += f"：{item.text or '（空）'}"
                self.item_list.addItem(label)
        self.item_list.blockSignals(False)
        self._sync_property_fields()

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
