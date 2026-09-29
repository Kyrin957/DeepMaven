"""图像标注页：单张图片标注工作台。

界面结构参照 Halcon DLT 的标注视图：
    中间：绘制工具条 + 预标注工具条 + 标注画布（画布条内置翻页图标）
    右侧：导航器、标注对象列表、编辑、备注
    窗口最右：显示调试栏（亮度 / 对比度）
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
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
    FluentIcon,
    LineEdit,
    MessageBox,
    PrimaryPushButton,
    PrimaryToolButton,
    ProgressBar,
    PushButton,
    ScrollArea,
    SegmentedWidget,
    StrongBodyLabel,
)

from src.services.autolabel_service import DEFAULT_DETECT_WEIGHTS, DEFAULT_SAM_WEIGHTS
from src.viewmodels.annotate_vm import AnnotateViewModel
from src.viewmodels.category_vm import CategoryViewModel
from src.views.data_widgets import ClassCard, side_column
from src.views.dialogs.tag_edit_dialog import DEFAULT_TAG_COLOR, TagEditDialog
from src.views.gallery_widgets import SplitMapCard, TagCard
from src.views.ui import (
    BusyPopup,
    DisplayDock,
    FlowContainer,
    SafeCompactSpinBox,
    SafeDoubleSpinBox,
    SafeSpinBox,
    ToolGroup,
    bind_icon,
    icon,
    icon_button,
)
from src.views.ui import tokens as T
from src.views.widgets import (
    MODE_BOX,
    MODE_BROWSE,
    MODE_MASK,
    MODE_POLYGON,
    MODE_TEXT,
    AnnotationCanvas,
    Navigator,
)


# 标注对象列表左侧的类别色块（与「标签类别」行同风格）
_CHIP_PX = 10
_FALLBACK_CHIP = "#66CCFF"
# 「另存图像为…」的文件过滤器
_IMAGE_FILTER = "图片 (*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp)"
# 标注方式 → 可用绘制工具（标注框类型互不混用，参照 DLT）
_TOOLS_BY_ANNOTATION: dict[str, list[tuple[str, str]]] = {
    "none": [(MODE_BROWSE, "浏览")],
    "box": [(MODE_BROWSE, "浏览"), (MODE_BOX, "矩形")],
    "obb": [(MODE_BROWSE, "浏览"), (MODE_BOX, "矩形")],
    "polygon": [
        (MODE_BROWSE, "浏览"), (MODE_POLYGON, "多边形"), (MODE_MASK, "掩码"),
    ],
    "text": [(MODE_BROWSE, "浏览"), (MODE_TEXT, "文本")],
}


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
        # 数据集 ViewModel：侧栏「标签类别 / 拆分映射 / 图像标记」卡片共用
        self._dataset_vm = vm.dataset_vm
        self.class_card: ClassCard | None = None
        self._canvas_selected = -1
        self._done = 0
        self._total = 0
        self._syncing = False
        self._edit_before: list | None = None     # 几何编辑前的标注快照（撤销用）
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

        # 左侧：图像 / 标签类别 / 拆分映射 / 图像标记（与图像导入页同构）
        root.addWidget(self._build_left_column())
        root.addLayout(self._build_center(), 1)
        root.addWidget(self._build_right_column())

        # 显示调试栏：窗口右侧竖直图标（亮度 / 对比度）
        self.display_dock = DisplayDock(self)
        root.addWidget(self.display_dock)

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
        self.preannotate_card = self._build_preannotate_bar()
        column.addWidget(self.preannotate_card)
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

        # ① 绘制工具（由项目标注方式决定：整图 / 矩形 / 旋转矩形 / 多边形 / 文本）
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
        self.rotate_btn = icon_button(FluentIcon.ROTATE, "按设定角度旋转选中", card)
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
        self.outline_btn = icon_button(FluentIcon.PENCIL_INK, "由掩码生成轮廓", card)
        self.mask_clear_btn = icon_button(FluentIcon.ERASE_TOOL, "清除掩码", card)
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

        # ⑥ 标注存取（保存 / 清空 / 导入）——整图任务没有标注文件，整组隐藏
        self.save_btn = icon_button(FluentIcon.SAVE, "保存标注", card, primary=True)
        self.clear_btn = icon_button(FluentIcon.BROOM, "清空当前图标注", card)
        self.import_btn = icon_button(FluentIcon.DOWNLOAD, "导入标注文件", card)
        self.transfer_group = ToolGroup(
            self.save_btn, self.clear_btn, self.import_btn, parent=card,
        )
        flow.addWidget(self.transfer_group)

        # ⑦ 导出标注（格式 + 按钮）。与 ⑥ 分开成两组：五件套合起来近 500px，
        # 最窄窗口下比工具条还宽，整组折行也放不下（会横向溢出被裁）。
        self.export_combo = ComboBox(card)
        for key, text in (
            ("coco", "COCO json"),
            ("voc", "VOC xml"),
            ("yolo", "YOLO txt"),
        ):
            self.export_combo.addItem(text, userData=key)
        self.export_btn = icon_button(FluentIcon.IMAGE_EXPORT, "导出标注", card)
        self.export_group = ToolGroup(
            self.export_combo, self.export_btn, parent=card,
        )
        flow.addWidget(self.export_group)
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
        browse_btn = icon_button(FluentIcon.FOLDER, "选择检测权重文件", card)
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

    # --------------------------------------------------- 左侧
    def _build_left_column(self) -> QWidget:
        """左侧栏：图像 / 标签类别 / 拆分映射 / 图像标记（与图像导入页同构）。

        这些卡片对**当前图片**生效（标注页按张工作）；显示调试仍在窗口
        最右的 DisplayDock（RULE §11）。
        """
        self.image_card = self._build_image_card()
        cards: list[QWidget] = [self.image_card]
        if self._category_vm is not None:
            self.class_card = ClassCard(self._category_vm, self)
            cards.append(self.class_card)
        self.split_map_card = SplitMapCard(self)
        self.tag_card = TagCard(self)
        cards.extend([self.split_map_card, self.tag_card])
        return side_column(*cards, width=T.SIDE_W)

    def _build_image_card(self) -> CardWidget:
        """图像卡片：当前图名称 / 状态 + 前后切换（自画布条迁入）。

        翻页区用**固定两行**（见《开发文档.md》§4.28）：上行四个翻页按钮、
        下行页码框 + 总页数——单行时「4 按钮 + 页码框 + 总数」在 268px 侧栏里
        几乎顶满，字体稍大即被裁；页码框宽度交给控件自身 ``sizeHint()``
        （按最大页码文字计算），不写定宽。
        """
        card = CardWidget(self)
        self.image_card = card
        layout = QVBoxLayout(card)
        layout.setContentsMargins(T.CARD_PAD_H, T.CARD_PAD_V, T.CARD_PAD_H, T.CARD_PAD_V)
        layout.setSpacing(T.SPACE_SM)
        layout.addWidget(StrongBodyLabel("图像", card))

        self.strip_name = CaptionLabel("未加载图片", card)
        self.strip_name.setWordWrap(True)
        self.strip_state = CaptionLabel("未标注", card)
        layout.addWidget(self.strip_name)
        layout.addWidget(self.strip_state)

        # 第一行：翻页按钮。图标统一走 qtawesome（Material Design Icons，
        # 见 views.ui.icons）；两行布局让 4 个按钮与页码框在窄侧栏（268px）
        # 与高分屏大字体下都放得下（单行时 4 按钮 + 页码 + 总数会顶满被裁）
        self.first_btn = icon_button(icon("first"), "首张图片", card)
        self.prev_btn = icon_button(icon("prev"), "上一张图片", card)
        self.next_btn = icon_button(icon("next"), "下一张图片", card)
        self.last_btn = icon_button(icon("last"), "末张图片", card)
        for button, name in (
            (self.first_btn, "first"), (self.prev_btn, "prev"),
            (self.next_btn, "next"), (self.last_btn, "last"),
        ):
            bind_icon(button, name)      # 明暗主题切换时自动重绘
        nav_row = QHBoxLayout()
        nav_row.setContentsMargins(0, 0, 0, 0)
        nav_row.setSpacing(T.SPACE_XS)
        nav_row.addStretch(1)
        for button in (self.first_btn, self.prev_btn,
                       self.next_btn, self.last_btn):
            nav_row.addWidget(button)
        nav_row.addStretch(1)
        layout.addLayout(nav_row)

        # 第二行：页码框 + 总页数。
        # 页码框**不写定宽**：qfluentwidgets 的 sizeHint 本就按 maximum 的
        # 文字宽度 + 步进器开销计算（max=1→60px、272→80px、12345→98px），
        # 即「长度由最大页码决定」，且随字体 / DPI 自动适配；两端 stretch
        # 保证布局只给它 sizeHint 宽度、不被拉伸。
        self.jump_spin = SafeCompactSpinBox(card)
        self.jump_spin.setRange(1, 1)
        self.jump_spin.setToolTip("输入序号后回车跳转")
        self.page_total = CaptionLabel("/ 0", card)
        page_row = QHBoxLayout()
        page_row.setContentsMargins(0, 0, 0, 0)
        page_row.setSpacing(T.SPACE_XS)
        page_row.addStretch(1)
        page_row.addWidget(self.jump_spin)
        page_row.addWidget(self.page_total)
        page_row.addStretch(1)
        layout.addLayout(page_row)
        return card

    # --------------------------------------------------- 中间
    def _build_canvas_card(self) -> CardWidget:
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(T.SPACE_LG, T.SPACE_ML, T.SPACE_LG, T.SPACE_ML)
        layout.setSpacing(T.SPACE_MD)

        # 翻页 / 名称已迁到左侧「图像」卡片，画布条只留画布本体
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

        item_card = CardWidget(self)
        item_layout = QVBoxLayout(item_card)
        item_layout.setContentsMargins(T.CARD_PAD_H, T.CARD_PAD_V, T.CARD_PAD_H, T.CARD_PAD_V)
        item_layout.setSpacing(T.SPACE_SM)
        item_layout.addWidget(StrongBodyLabel("标注对象", item_card))
        self.item_list = QListWidget(item_card)
        self.item_list.setMinimumHeight(110)
        # 行首为类别色块图标（与「标签类别」行同色）
        self.item_list.setIconSize(QSize(_CHIP_PX + 2, _CHIP_PX + 2))
        # 支持多选（Ctrl 点选 / Shift 连选），与画布选中同步
        self.item_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        item_layout.addWidget(self.item_list)

        action_row = QHBoxLayout()
        self.delete_btn = icon_button(FluentIcon.DELETE, "删除选中标注", item_card)
        self.select_all_btn = icon_button(icon("select-all"), "全选标注", item_card)
        bind_icon(self.select_all_btn, "select-all")
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
        self.apply_geo_btn = icon_button(
            FluentIcon.ACCEPT, "应用位置与尺寸", self.edit_card
        )
        self.apply_angle_btn = icon_button(
            FluentIcon.ROTATE, "按角度旋转选中", self.edit_card
        )
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
        self.note_btn = icon_button(FluentIcon.SAVE, "保存备注", note_card)
        note_layout.addWidget(self.note_btn)

        return side_column(
            navigator_card, item_card, self.edit_card, note_card,
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

        self.first_btn.clicked.connect(self._on_first)
        self.prev_btn.clicked.connect(vm.prev_image)
        self.next_btn.clicked.connect(vm.next_image)
        self.last_btn.clicked.connect(self._on_last)
        self.jump_spin.editingFinished.connect(self._on_jump)
        self.save_btn.clicked.connect(vm.save)
        self.clear_btn.clicked.connect(vm.clear_annotations)
        self.import_btn.clicked.connect(self._on_import)
        self.export_btn.clicked.connect(self._on_export)
        self.delete_btn.clicked.connect(self._on_delete)
        self.class_combo.currentIndexChanged.connect(self._on_class_changed)
        self.detect_btn.clicked.connect(self._on_preannotate)
        self.sam_btn.clicked.connect(self._on_sam)

        self.display_dock.changed.connect(self._apply_display)
        self.note_btn.clicked.connect(self._on_save_note)
        # 画布右键菜单：另存图像 / 打开所在位置
        self.canvas.saveImageRequested.connect(self._on_save_image)
        self.canvas.openImageFolderRequested.connect(self._on_open_image_folder)

        # 左侧栏：标签类别 / 拆分映射 / 图像标记（对当前图片生效）
        if self.class_card is not None:
            self.class_card.classClicked.connect(self._on_class_clicked)
            self.class_card.changed.connect(lambda: self._refresh_side())
        self.split_map_card.splitRequested.connect(self._on_split_requested)
        self.tag_card.tagToggled.connect(self._on_tag_toggled)
        self.tag_card.addRequested.connect(self._on_add_tag)
        self.tag_card.editRequested.connect(self._on_edit_tag)
        self.tag_card.deleteRequested.connect(self._on_delete_tag)
        if self._dataset_vm is not None:
            self._dataset_vm.datasetChanged.connect(
                lambda _value: self._refresh_side()
            )

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
        self._refresh_side()

    def _on_class_changed(self, _index: int) -> None:
        value = self.class_combo.currentData()
        if value is not None:
            self.canvas.set_pending_class(int(value))

    # -----------------------------------------------------------
    # 交互
    # -----------------------------------------------------------
    def _reload_tools(self, annotation: str) -> None:
        """按标注方式重建工具条。

        整图任务（分类 / 异常检测）只保留「浏览」；水平框与旋转框只保留
        「矩形」；实例 / 语义分割为「多边形 + 掩码」；OCR 为「文本」。
        """
        tools = _TOOLS_BY_ANNOTATION.get(annotation, _TOOLS_BY_ANNOTATION["box"])
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
        """按项目类型限定标注方式与工具（整图 / 矩形 / 旋转矩形 / 多边形 / 文本）。

        任务差异全部用「工具与栏位的可用性」表达，不写说明文字：
            * 整图任务（分类 / 异常检测）无标注文件 —— 隐藏存取与导出组，
              预标注栏也隐藏（整图判定在左栏「标签类别」完成）；
            * 旋转框（obb）—— 只给矩形工具 + 角度组（拖框后调角度）；
            * 实例 / 语义分割 —— 多边形 + 掩码，可用 SAM 细化；
            * OCR —— 文本框。
        """
        annotation = self._vm.annotation_mode()
        target = {
            "box": MODE_BOX, "obb": MODE_BOX, "polygon": MODE_POLYGON,
            "text": MODE_TEXT,
        }.get(annotation, MODE_BROWSE)
        self._reload_tools(annotation)
        self.mode_seg.setCurrentItem(target)
        self._on_mode(target)

        self.rotate_group.setVisible(annotation == "obb")
        self.preannotate_card.setVisible(annotation in ("box", "obb", "polygon"))
        self.sam_btn.setVisible(annotation == "polygon")
        has_files = annotation != "none"
        self.transfer_group.setVisible(has_files)
        self.export_group.setVisible(has_files)

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

    def _on_first(self) -> None:
        """首张（复用 step_image 的跳首语义）。"""
        self._vm.step_image(-9999)

    def _on_last(self) -> None:
        """末张。"""
        self._vm.step_image(9999)

    def _on_jump(self) -> None:
        if self._syncing:
            return
        self._vm.set_current(self.jump_spin.value() - 1)

    # -----------------------------------------------------------
    # 图像文件操作（画布右键菜单）
    # -----------------------------------------------------------
    def _on_save_image(self) -> None:
        """另存当前图像到指定路径。"""
        source = self._vm.current_path()
        if source is None:
            return
        target, _ = QFileDialog.getSaveFileName(
            self, "另存图像为", str(source), _IMAGE_FILTER
        )
        if target:
            self._dataset_vm.save_image_as(source, target)

    def _on_open_image_folder(self) -> None:
        """打开当前图像所在目录。"""
        source = self._vm.current_path()
        if source is None:
            return
        directory = Path(source).parent
        if not directory.is_dir():
            self.status_message("图像所在目录不存在")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def _apply_display(self) -> None:
        """把显示调试栏的亮度 / 对比度下发给画布（只影响显示）。"""
        values = self.display_dock.values()
        with BusyPopup.scope(self.window(), "正在应用显示效果") as busy:
            busy.step()          # 先让滑杆 / 读数重绘，再做画布重算
            self.canvas.set_brightness(round(values["brightness"] * 100))
            self.canvas.set_contrast(round(values["contrast"] * 100))

    # -----------------------------------------------------------
    # 左侧栏：图像信息 / 标签类别 / 拆分映射 / 图像标记
    # -----------------------------------------------------------
    def _current_paths(self) -> list[str]:
        """当前图片路径（左侧栏的类别 / 拆分 / 标记都针对它设置）。"""
        path = self._vm.current_path()
        return [str(path)] if path is not None else []

    def _class_counts(self) -> dict:
        """各类别的实际图片数；检测任务额外统计未标注张数（与图库页一致）。"""
        vm = self._dataset_vm
        if vm is None:
            return {}
        counts = vm.class_distribution()
        if vm.split_layout() != "classify":
            flags = vm.annotated_flags()
            counts["未标注"] = sum(1 for flag in flags if not flag)
        return counts

    def _refresh_side(self) -> None:
        """刷新左侧「标签类别 / 拆分映射 / 图像标记」卡片状态。"""
        vm = self._dataset_vm
        if vm is None or vm.dataset is None:
            return
        if self.class_card is not None and self._category_vm is not None:
            self.class_card.set_classes(
                list(self._category_vm.classes), self._class_counts()
            )
        current = ""
        paths = self._current_paths()
        labels = vm.subset_labels()
        position = {str(path): index for index, path in enumerate(vm.images())}
        if paths:
            index = position.get(paths[0])
            if index is not None and index < len(labels):
                current = labels[index]
        self.split_map_card.set_state(
            vm.split_name_label(), vm.split_summary(), current, vm.split_locked()
        )
        counts: dict[str, int] = {}
        for values in vm.tag_map().values():
            for name in values:
                counts[name] = counts.get(name, 0) + 1
        self.tag_card.set_tags(
            vm.all_tag_names(),
            {item["name"]: item["color"] for item in vm.image_tag_defs()},
            counts,
        )

    def _on_class_clicked(self, name: str) -> None:
        """点类别行 → 当前图片改为该类别（"" = 无标签）。"""
        paths = self._current_paths()
        if paths and self._dataset_vm is not None:
            self._dataset_vm.set_class_for(paths, name)
            self._refresh_side()

    def _on_split_requested(self, split: str) -> None:
        """点拆分映射行 → 当前图片改划到该子集。"""
        vm = self._dataset_vm
        if vm is None:
            return
        if vm.split_locked():
            self._vm.message.emit("warning", "该拆分已用于训练，无法更改")
            return
        paths = self._current_paths()
        if paths:
            vm.set_split_for(paths, split)
            self._refresh_side()

    def _on_tag_toggled(self, name: str) -> None:
        """点图像标记 → 当前图片没有则追加、已有则删除。"""
        paths = self._current_paths()
        if paths and self._dataset_vm is not None:
            self._dataset_vm.toggle_image_tag(paths, name)
            self._refresh_side()

    def _on_add_tag(self) -> None:
        paths = self._current_paths()
        dialog = TagEditDialog(
            self.window(), color=DEFAULT_TAG_COLOR, has_selection=bool(paths)
        )
        dialog.applied.connect(self._apply_new_tag)
        if not dialog.exec():
            return
        self._apply_new_tag(dialog.result_data())

    def _apply_new_tag(self, data: dict) -> None:
        """新增标记定义；勾了「为当前图像分配标记」时同时挂到当前图片。"""
        vm = self._dataset_vm
        if vm is None or not vm.add_image_tag(data["text"], data["color"]):
            return
        if data.get("assign") and self._current_paths():
            vm.toggle_image_tag(self._current_paths(), data["text"])
        self._refresh_side()

    def _on_edit_tag(self, name: str) -> None:
        vm = self._dataset_vm
        target = None
        if vm is not None:
            target = next(
                (item for item in vm.image_tag_defs() if item["name"] == name),
                None,
            )
        if target is None:
            return
        paths = self._current_paths()
        dialog = TagEditDialog(
            self.window(),
            text=target["name"],
            color=target["color"] or DEFAULT_TAG_COLOR,
            editing=True,
            has_selection=bool(paths),
        )
        dialog.applied.connect(lambda data: self._apply_edit_tag(name, data))
        if not dialog.exec():
            return
        self._apply_edit_tag(name, dialog.result_data())

    def _apply_edit_tag(self, old_name: str, data: dict) -> None:
        vm = self._dataset_vm
        if vm is None or not vm.update_image_tag(
            old_name, data["text"], data["color"]
        ):
            return
        if data.get("assign") and self._current_paths():
            vm.toggle_image_tag(self._current_paths(), data["text"])
        self._refresh_side()

    def _on_delete_tag(self, name: str) -> None:
        box = MessageBox(
            "删除图像标记",
            f"确定要删除标记「{name}」吗？\n\n该标记会从所有图像上一并摘除。",
            self.window(),
        )
        box.yesButton.setText("删除")
        box.cancelButton.setText("取消")
        if box.exec() and self._dataset_vm is not None:
            self._dataset_vm.remove_image_tag(name)
            self._refresh_side()

    # -----------------------------------------------------------
    # 未保存改动（关闭 / 切换项目前的检查与冲刷）
    # -----------------------------------------------------------
    def has_unsaved(self) -> bool:
        """当前图片是否有未保存的标注改动。"""
        return self._vm.has_unsaved()

    def flush_pending(self) -> None:
        """立即保存当前图片的未保存标注。"""
        self._vm.flush_pending()

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
        """图片清单变化：更新页码范围与统计（缩略图列表已移除）。"""
        self.jump_spin.setRange(1, max(1, len(paths)))
        self._update_totals()
        self._refresh_side()

    def _on_image_changed(self, index: int) -> None:
        path = self._vm.current_path()
        if path is not None:
            self.canvas.set_image(path)
            self.navigator.set_image(path)
            self.display_dock.reset()       # 换图恢复默认显示（亮度 / 对比度归零）
        self._syncing = True
        self.jump_spin.setValue(index + 1)
        self._syncing = False
        self._update_strip()
        self._refresh_side()

    def _on_annotation_loaded(self, annotation) -> None:
        self.canvas.set_annotations(annotation.items if annotation else [])
        self._rebuild_item_list(annotation)
        self._update_strip()

    def _on_note_loaded(self, text: str) -> None:
        self.note_edit.setPlainText(text or "")

    def _on_status(self, done: int, total: int) -> None:
        self._done, self._total = done, total
        self._update_strip()

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
        """重建右侧「标注对象」列表：类别色块 + 序号 + 名称 + 类型。"""
        classes = {cls.cls_id: cls for cls in self._vm.class_items()}
        self.item_list.blockSignals(True)
        self.item_list.clear()
        if annotation is not None:
            for index, item in enumerate(annotation.items):
                cls = classes.get(item.cls_id)
                name = cls.name if cls is not None else str(item.cls_id)
                color = cls.color if cls is not None else _FALLBACK_CHIP
                kind = {"box": "矩形", "polygon": "多边形", "mask": "掩码",
                        "text": "文本"}.get(item.kind, item.kind)
                label = f"{index + 1}. {name} · {kind}"
                if item.is_text:
                    label += f"：{item.text or '（空）'}"
                row = QListWidgetItem(self._chip_icon(color), label)
                row.setToolTip(self._item_tooltip(annotation, item, name, kind))
                self.item_list.addItem(row)
        self.item_list.blockSignals(False)
        self._sync_list_selection()
        self._sync_property_fields()

    def _sync_list_selection(self) -> None:
        """把画布选中状态回填到对象列表。

        画完自动选中时，选择信号先于列表重建发出（此刻列表还是旧内容，
        同步不到），因此列表重建后必须按画布状态再回填一次。
        """
        indexes = self.canvas.selection()
        primary = self.canvas.selected_index()
        self._canvas_selected = primary
        self._syncing = True
        try:
            self.item_list.clearSelection()
            for index in indexes:
                row = self.item_list.item(index)
                if row is not None:
                    row.setSelected(True)
            if 0 <= primary < self.item_list.count():
                self.item_list.setCurrentRow(primary)
        finally:
            self._syncing = False

    def _chip_icon(self, color: str) -> QIcon:
        """类别色块图标（与「标签类别」行的色块同色，便于逐对象核对）。"""
        pixmap = QPixmap(_CHIP_PX, _CHIP_PX)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color or _FALLBACK_CHIP))
        painter.drawRoundedRect(0, 0, _CHIP_PX, _CHIP_PX, 2, 2)
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _item_tooltip(annotation, item, name: str, kind: str) -> str:
        """对象列表行提示：类别 · 类型 · 像素尺寸（OCR 附转写）。"""
        width = max(1, int(getattr(annotation, "width", 0) or 1))
        height = max(1, int(getattr(annotation, "height", 0) or 1))
        x1, y1, x2, y2 = item.bounds()
        px_w = int(round((x2 - x1) * width))
        px_h = int(round((y2 - y1) * height))
        text = f"{name} · {kind} · {px_w} × {px_h} px"
        if item.is_text and item.text:
            text += f" · {item.text}"
        return text

    def _update_totals(self) -> None:
        self._done = self._vm.annotated_count()
        self._total = len(self._vm.images)
        self._update_strip()

    def _update_strip(self) -> None:
        total = len(self._vm.images)
        path = self._vm.current_path()
        percent = 0 if not total else round(self._done * 100 / total)
        self.footer_status.setText(
            f"已标注 {self._done} / {total}（{percent}%）"
        )
        self.page_total.setText(f"/ {total}")
        self.jump_spin.setRange(1, max(1, total))
        if total == 0 or path is None:
            self.strip_name.setText("未加载图片")
            self.strip_state.setText("未标注")
            return
        self.strip_name.setText(str(path.name))
        label_dir = self._vm.label_dir()
        annotated = False
        if label_dir is not None:
            from src.services.annotation_service import AnnotationService

            annotated = AnnotationService.label_path_for(path, label_dir).is_file()
        self.strip_state.setText("已标注" if annotated else "未标注")
