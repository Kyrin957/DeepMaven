"""模型训练导航页：训练参数、实时曲线与训练记录。

界面结构参照 Halcon DLT 的「训练」页：

    左侧：项目信息 + 模型/权重 + 开始训练 + 训练记录列表
    中央：设置（拆分 / 模型与权重 / 训练参数 / 数据增强）与结果（实时指标 + 曲线 + 日志）

训练过程中的 Loss 与评价指标以**实时折线图**呈现（纯 QPainter 自绘，见
`views/widgets/charts.py::LineChart`），不再是纯文本指标。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QBoxLayout,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStackedWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    Action,
    BodyLabel,
    CaptionLabel,
    setCustomStyleSheet,
    CardWidget,
    CheckBox,
    ComboBox,
    FluentIcon,
    LineEdit,
    MessageBox,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    RoundMenu,
    ScrollArea,
    SegmentedWidget,
    StrongBodyLabel,
    TableWidget,
    TextEdit,
    TransparentToolButton,
)

from src.services.backends import for_task as backend_for_task
from src.utils.constants import (
    OPTIMIZERS,
    SPLIT_COLORS,
    SPLIT_LABELS,
    YOLO_MODEL_VARIANTS,
    base_variant_key,
    uses_dfl,
)
from src.utils.device import device_help, device_label, device_options
from src.utils.tasks import is_detection_family, supports_class_counts
from src.viewmodels.dataset_vm import DatasetViewModel
from src.viewmodels.model_vm import ModelViewModel
from src.viewmodels.train_vm import TrainViewModel
from src.views.data_widgets import side_column
from src.views.dialogs import ImagePreviewDialog
from src.views.train_help import (
    AUGMENT_HELP,
    DEVICE_HELP,
    DISTRIBUTION_HELP,
    IMAGE_HELP,
    MODEL_HELP,
    MODELS_HELP,
    PARAM_HELP,
    SPLIT_HELP,
)
from src.views.ui import (
    FieldRow,
    ParamGroup,
    SafeCompactDoubleSpinBox,
    SafeCompactSpinBox,
    SafeDoubleSpinBox,
    SafeSpinBox,
    card_title_row,
    icon_button,
)
from src.views.ui import tokens as T
from src.views.ui.icons import icon
from src.views.widgets import BarChart, LineChart, PieChart

# ---------------------------------------------------------------
# 参数表：(键, 标签, 最小值, 最大值, 默认值, 步长, 后缀) / 浮点表末位是小数位
# 分三处摆放：常用（每次训练都会看）/ 高级（默认收起）/ 图像设置
# ---------------------------------------------------------------
_COMMON_INT_PARAMS = [
    ("epochs", "训练轮数", 1, 10000, 100, 1, ""),
    ("batch", "批次大小", 1, 512, 16, 1, ""),
    ("seed", "随机种子", 0, 99999, 0, 1, ""),
]
_COMMON_FLOAT_PARAMS = [
    ("lr", "学习率", 0.00001, 1.0, 0.01, 0.001, 5),
]
# 高级：Ultralytics 的默认值在多数数据集上都够用，默认收起
_ADV_INT_PARAMS = [
    ("patience", "早停耐心值", 0, 1000, 50, 1, " 轮"),
    ("close_mosaic", "末轮关增强", 0, 100, 10, 1, " 轮"),
    ("workers", "数据线程", 0, 32, 4, 1, ""),
]
_ADV_FLOAT_PARAMS = [
    ("weight_decay", "权重衰减", 0.0, 1.0, 0.0005, 0.0001, 5),
    ("momentum", "动量", 0.0, 1.0, 0.937, 0.01, 3),
    ("warmup_epochs", "学习率预热", 0.0, 50.0, 3.0, 0.5, 1),
    ("dropout", "Dropout", 0.0, 0.9, 0.0, 0.05, 2),
    ("dfl", "DFL 增益", 0.0, 20.0, 1.5, 0.5, 2),
]
# 图像设置：输入尺寸与通道（对应 Ultralytics 的 imgsz / channels）
_IMAGE_INT_PARAMS = [
    ("imgsz", "图像尺寸", 32, 2048, 640, 32, " px"),
    ("channels", "通道", 1, 4, 3, 1, ""),
]
_CHECK_PARAMS = [
    ("deterministic", "使用确定性算法"),
    ("cos_lr", "余弦学习率调度"),
    ("val", "每轮验证"),
    ("cache", "缓存图片"),
    ("rect", "矩形训练"),
    ("single_cls", "视为单一类别"),
    ("resume", "断点续训"),
]
_AUG_PARAMS = [
    ("hflip", "水平翻转", 0.0, 1.0, 0.5, 0.05),
    ("vflip", "垂直翻转", 0.0, 1.0, 0.0, 0.05),
    ("degrees", "旋转角度", 0.0, 180.0, 0.0, 5.0),
    ("scale", "缩放幅度", 0.0, 1.0, 0.5, 0.05),
    ("translate", "平移幅度", 0.0, 1.0, 0.1, 0.05),
    ("hsv_h", "色调变化", 0.0, 1.0, 0.015, 0.005),
    ("hsv_s", "饱和度变化", 0.0, 1.0, 0.7, 0.05),
    ("hsv_v", "亮度变化", 0.0, 1.0, 0.4, 0.05),
    ("mosaic", "马赛克", 0.0, 1.0, 1.0, 0.1),
    ("mixup", "混合", 0.0, 1.0, 0.0, 0.1),
]

_LOSS_COLORS = {"训练损失": "#0F6CBD", "验证损失": "#0F7B0F"}
_METRIC_COLORS = ("#0F6CBD", "#0F7B0F", "#C42B1C", "#B16CEA", "#E8A33D")

# 预训练模型下拉里「导入的权重文件」项的标记：userData = (_CUSTOM_TAG, 路径)，
# 与官方变体项（userData = 变体 key，字符串）区分开——一个下拉两个来源。
_CUSTOM_TAG = "custom"
# qf 下拉的箭头与左右内边距（按控件实现预留，量测文本宽度时扣除）
_COMBO_TEXT_CHROME = 34


def _custom_path(data) -> str:
    """下拉项的 userData 是否为导入权重项；是则返回路径，否则空串。"""
    if isinstance(data, tuple) and len(data) == 2 and data[0] == _CUSTOM_TAG:
        return str(data[1])
    return ""


def _equal_width(widgets) -> None:
    """把一组控件设为**等宽**（取各自"已声明宽度"的最大值）。

    同一个栅格里「下拉 + 步进器」长短不一，右边缘参差会显得散乱；统一宽度后
    成列对齐。取齐基准是 `setFixedWidth` / `setMinimumWidth` **声明的宽度**
    （即 `field_width()` 算出来的字符宽度），**不看 `sizeHint()`**——下拉的
    sizeHint 会随当前项文字变化，拿它当基准会把整组一起撑宽。
    """
    declared = [
        widget.minimumWidth() for widget in widgets if widget is not None
    ]
    declared = [value for value in declared if value > 0]
    if not declared:
        return
    target = max(declared)
    for widget in widgets:
        if widget is not None:
            widget.setFixedWidth(target)


def _duration(seconds: float) -> str:
    """秒 → HH:MM:SS。"""
    total = max(0, int(seconds))
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


class _ControlButton(TransparentToolButton):
    """模型卡片控制按钮（复位 / 开始 / 停止）：纯图标 + **禁用态明显淡化**。

    qf 的 ``ToolButton.paintEvent`` 用 ``painter.setOpacity(0.43)`` 表达禁用态，
    但 ``ui.icons`` 的图标来自 qtawesome（字体引擎）——实测该不透明度对它无效，
    禁用与可用**逐像素相同**（《开发文档.md》§4.61 自检：墨量 47134 → 47134）。
    卡片按钮不写文字（§10），禁用提示只能靠图标本身，故在启用状态变化时挂 /
    摘 ``QGraphicsOpacityEffect``（40 %）：未训练的「复位 / 停止」一眼可辨。
    """

    _DISABLED_OPACITY = 0.4

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802 - Qt 命名
        super().setEnabled(enabled)
        has_effect = self.graphicsEffect() is not None
        if enabled:
            if has_effect:
                # 置 None 即销毁 effect，恢复按可用态绘制
                self.setGraphicsEffect(None)  # type: ignore[arg-type]
            return
        if not has_effect:
            effect = QGraphicsOpacityEffect(self)
            effect.setOpacity(self._DISABLED_OPACITY)
            self.setGraphicsEffect(effect)


class _ModelCard(QFrame):
    """训练模型卡片：名称（行内编辑）/ 规格 / 状态指标 + 训练控制 + 菜单。

    参照 Halcon DLT 的模型卡：点击选中；名称直接在卡片上改（回车 / 失焦
    提交，不再弹窗）；「⋯」菜单含 重命名 / 复制 / 删除 / 在文件资源管理器
    中打开训练；底部三个等分图标按钮为 复位 / 开始（暂停 → 继续）/ 停止
    （不写文字，含义走 tooltip；不可用时整枚淡化，见 `_ControlButton`）。
    """

    selected = Signal(int)
    toggleRequested = Signal(int)     # 开始 / 暂停 / 继续（按状态切换）
    stopRequested = Signal(int)
    resetRequested = Signal(int)
    renamed = Signal(int, str)        # 名称行内编辑提交（model_id, 新名称）
    duplicateRequested = Signal(int)
    deleteRequested = Signal(int)
    openDirRequested = Signal(int)

    def __init__(
        self,
        model: dict,
        variant_label: str,
        active: bool,
        running: bool,
        paused: bool,
        parent=None,
    ):
        super().__init__(parent)
        self.model_id = int(model.get("model_id", 0))
        self._original_name = str(model.get("name") or "模型")
        self._active = bool(active)
        self.setObjectName("ModelCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(T.SPACE_LG, T.SPACE_MD, T.SPACE_LG, T.SPACE_MD)
        layout.setSpacing(T.SPACE_XS)

        head = QHBoxLayout()
        head.setSpacing(T.SPACE_SM)
        # 名称行内编辑：点击位置直接改，回车 / 失焦提交（不再弹输入框）
        self.name_edit = LineEdit(self)
        self.name_edit.setText(self._original_name)
        self.name_edit.setToolTip("点击修改模型名称，回车确认")
        # 追加（不替换）透明标题式样式，保留 qf 输入的焦点 / 悬停行为
        setCustomStyleSheet(
            self.name_edit,
            "LineEdit { border: none; background: transparent;"
            " padding: 1px 2px; }",
            "LineEdit { border: none; background: transparent;"
            " padding: 1px 2px; }",
        )
        self.name_edit.editingFinished.connect(self._on_name_edited)
        head.addWidget(self.name_edit, 1)
        self.menu_btn = TransparentToolButton(self)
        self.menu_btn.setIcon(FluentIcon.MORE)
        self.menu_btn.setFixedSize(T.ICON_BTN_MD, T.ICON_BTN_MD)
        self.menu_btn.setToolTip("模型操作")
        self.menu_btn.clicked.connect(self._show_menu)
        head.addWidget(self.menu_btn)
        layout.addLayout(head)

        layout.addWidget(CaptionLabel(variant_label or "—", self))
        state = self._state_text(model, running, paused)
        self.state_label = BodyLabel(state, self)
        self.state_label.setToolTip(state)
        layout.addWidget(self.state_label)

        # 控制按钮：只画图标、等分拉伸占满卡片宽度——图标居中、点击面积大；
        # 含义一律走 tooltip，不写文字（卡片窄，图标 + 文字会互相挤压，§10）。
        # 复位仅在**训练过**（暂停 / 停止 / 完成 / 失败）且非「运行中」时可用；
        # 未训练的模型不可复位（历史：未训练也能点，语义不明）。
        status = str(model.get("status") or "")
        attempted = bool(
            status in ("finished", "error", "stopped", "paused")
            or str(model.get("metric_label") or "").strip()
            or str(model.get("run_dir") or "").strip()
        )
        row = QHBoxLayout()
        row.setSpacing(T.SPACE_SM)
        self.reset_btn = self._tool(
            "reset", "复位训练状态", self.resetRequested,
            enabled=attempted and not (running and not paused),
        )
        row.addWidget(self.reset_btn, 1)
        if running and not paused:
            self.toggle_btn = self._tool(
                "pause", "暂停训练（本轮结束后生效）", self.toggleRequested
            )
        else:
            self.toggle_btn = self._tool(
                "play",
                "继续训练" if paused else "开始训练",
                self.toggleRequested,
            )
        row.addWidget(self.toggle_btn, 1)
        self.stop_btn = self._tool(
            "stop", "停止训练", self.stopRequested, enabled=running
        )
        row.addWidget(self.stop_btn, 1)
        layout.addLayout(row)

        if not self._active:
            for widget in (self.reset_btn, self.toggle_btn, self.stop_btn):
                widget.setEnabled(False)
        self._restyle()

    def _on_name_edited(self) -> None:
        """名称行内编辑提交（回车 / 失焦）：有变化才发信号。"""
        text = self.name_edit.text().strip()
        if text and text != self._original_name:
            self.renamed.emit(self.model_id, text)

    @staticmethod
    def _state_text(model: dict, running: bool, paused: bool) -> str:
        """卡片状态行：训练态 / 指标（最佳主指标 + 轮次）/ 未训练。"""
        if running:
            return "训练中（已暂停，可继续）" if paused else "训练中"
        if str(model.get("status") or "") == "error":
            return "上次训练失败"
        label = str(model.get("metric_label") or "")
        if label:
            epoch = int(model.get("metric_epoch") or 0)
            suffix = f"（第 {epoch} 轮）" if epoch else ""
            return (
                f"{label} {float(model.get('metric_value') or 0):.4f}{suffix}"
            )
        return "未训练"

    def _tool(
        self, name: str, tip: str, signal, enabled: bool = True
    ) -> TransparentToolButton:
        """卡片内的控制按钮（图标态；等分拉伸、禁用态明显）。

        用 `_ControlButton`（`TransparentToolButton` 子类）：图标由 qf 居中
        绘制、禁用时整体降到 40% 不透明度；图标按钮的最小宽只有 24px，三枚
        并排不会撑宽卡片（UI 规则 §24）。含义一律走 tooltip，不写文字（§10）。
        """
        button = _ControlButton(self)
        button.setIcon(icon(name))
        button.setIconSize(QSize(T.ICON_GLYPH_LG, T.ICON_GLYPH_LG))
        button.setToolTip(tip)
        button.setFixedHeight(32)
        # QToolButton 默认不参与拉伸，显式声明横向 Expanding：行内三枚
        # `addWidget(..., 1)` 等分占满卡片宽度（图标仍居中）
        button.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        button.setEnabled(enabled)
        button.clicked.connect(lambda: signal.emit(self.model_id))
        return button

    def _restyle(self) -> None:
        border = "#0F6CBD" if self._active else T.GROUP_BORDER
        background = "rgba(15, 108, 189, 0.10)" if self._active else "transparent"
        self.setStyleSheet(
            "#ModelCard { border: 1px solid " + border + "; border-radius: 6px;"
            " background: " + background + "; }"
        )

    def _show_menu(self) -> None:
        menu = RoundMenu(parent=self)
        menu.addAction(Action(
            "重命名",
            triggered=lambda: (
                self.name_edit.setFocus(),
                self.name_edit.selectAll(),
            ),
        ))
        menu.addAction(Action(
            "复制", triggered=lambda: self.duplicateRequested.emit(self.model_id)
        ))
        menu.addAction(Action(
            "删除", triggered=lambda: self.deleteRequested.emit(self.model_id)
        ))
        menu.addSeparator()
        menu.addAction(Action(
            "在文件资源管理器中打开训练",
            triggered=lambda: self.openDirRequested.emit(self.model_id),
        ))
        position = self.menu_btn.mapToGlobal(
            QPoint(0, self.menu_btn.height())
        )
        menu.exec(position)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self.model_id)
        super().mousePressEvent(event)


class TrainTab(QWidget):
    """模型训练页：参数设置、训练执行、实时曲线与结果。

    与拆分 / 检查等两栏页面一致，直接继承 `QWidget` 并自建根布局；
    `BasePage` 自带滚动区布局，两者叠加会导致根布局安装失败（界面空白）。
    """

    def __init__(
        self,
        vm: TrainViewModel,
        model_vm: ModelViewModel | None = None,
        dataset_vm: DatasetViewModel | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._vm = vm
        self._model_vm = model_vm
        self._dataset_vm = dataset_vm
        self._syncing = False
        self._int_spins: dict[str, SafeSpinBox] = {}
        self._float_spins: dict[str, SafeDoubleSpinBox] = {}
        self._checks: dict[str, CheckBox] = {}
        self._aug_spins: dict[str, SafeCompactDoubleSpinBox] = {}
        self._aug_checks: dict[str, CheckBox] = {}
        # 参数键 → 字段行（按后端能力整行显隐，见 _apply_param_visibility）
        self._param_rows: dict[str, QWidget] = {}
        self._tile_values: dict[str, BodyLabel] = {}
        self._weight_inputs_rendered: tuple = ()
        # 最近一次选定的自定义权重（切回「自定义权重」时免再弹一次对话框）
        self._custom_weights = ""
        self._build_ui()
        self._bind()
        self._on_status(self._vm.config.status)
        self.sync_from_config()

    # -----------------------------------------------------------
    # 界面搭建
    # -----------------------------------------------------------
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(T.SPACE_XL, T.SPACE_LG, T.SPACE_XL, T.SPACE_LG)
        root.setSpacing(T.SPACE_LG)
        root.addWidget(self._build_side(), 0)
        root.addLayout(self._build_main(), 1)

    def _card(
        self, title: str, help_entries=None
    ) -> tuple[CardWidget, QVBoxLayout]:
        """建卡片：标题行 = `标题 + [?]`（给了说明条目才出现帮助按钮）。"""
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(T.SPACE_2XL, T.SPACE_XL, T.SPACE_2XL, T.SPACE_XL)
        layout.setSpacing(T.SPACE_ML)
        if title:
            layout.addWidget(card_title_row(card, title, help_entries))
        return card, layout

    # --------------------------------------------------- 左侧（训练模型管理）
    def _build_side(self) -> QWidget:
        models_card, models_layout = self._card("训练模型", MODELS_HELP)
        self.new_model_btn = PushButton("新建模型", models_card)
        self.new_model_btn.clicked.connect(self._on_model_create)
        models_layout.addWidget(self.new_model_btn)
        self.models_hint = CaptionLabel("", models_card)
        self.models_hint.setWordWrap(True)
        models_layout.addWidget(self.models_hint)
        self.models_holder = QVBoxLayout()
        self.models_holder.setContentsMargins(0, 0, 0, 0)
        self.models_holder.setSpacing(T.SPACE_SM)
        models_layout.addLayout(self.models_holder)
        models_layout.addStretch(1)

        return side_column(models_card, width=T.SIDE_W_WIDE)

    def _rebuild_models(self) -> None:
        """重建左侧模型卡片（活动高亮 / 训练状态 / 指标）。"""
        while self.models_holder.count():
            item = self.models_holder.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        models = self._vm.models()
        active_id = self._vm.active_model_id()
        status = str(self._vm.config.status)
        alive = status in ("running", "paused")
        paused = status == "paused"
        for model in models:
            model_id = int(model.get("model_id", 0))
            is_active = model_id == active_id
            card = _ModelCard(
                model,
                # 活动模型用**活配置**：改完变体 / 权重来源，卡片规格行立刻反映
                variant_label=self._variant_label_for(
                    model, self._vm.config if is_active else None
                ),
                active=is_active,
                running=alive and is_active,
                paused=paused and is_active,
                parent=self,
            )
            card.selected.connect(self._on_model_selected)
            card.toggleRequested.connect(self._on_model_toggle)
            card.stopRequested.connect(lambda _id: self._vm.stop())
            card.resetRequested.connect(lambda _id: self._vm.reset())
            card.renamed.connect(self._on_model_renamed)
            card.duplicateRequested.connect(self._on_model_duplicate)
            card.deleteRequested.connect(self._on_model_delete)
            card.openDirRequested.connect(self._on_model_open_dir)
            self.models_holder.addWidget(card)
        self.models_hint.setText(
            "" if models else "暂无训练模型：点击「新建模型」创建"
        )

    @staticmethod
    def _variant_label_for(model: dict, live=None) -> str:
        """模型卡片规格行：**实际用于训练的权重来源**。

        预训练变体：显示变体**全名**（含任务后缀）+ 输入尺寸——分类 / 分割 /
        旋转框的权重名带任务后缀（如 ``yolo11n-cls``），必须显示全名以免与
        检测变体混淆。自定义权重：显示「自定义权重 · 文件名」（此时变体不参与
        训练，显示变体名会误导）。

        ``live``：活动模型的活配置（快照要等「切换模型 / 开始训练」才回写，
        用活配置才能当场反映变体与权重来源的改动）。
        """
        config = live if live is not None else (model.get("config") or {})
        read = (
            (lambda name: config.get(name))
            if isinstance(config, dict)
            else (lambda name: getattr(config, name, ""))
        )
        weights = str(read("weights_path") or "")
        if weights:
            return f"自定义权重 · {Path(weights).name}"
        key = str(read("model_key") or "")
        base = base_variant_key(key)
        variant = next(
            (v for v in YOLO_MODEL_VARIANTS if v["key"] == base), None
        )
        if variant is None:
            return key or "—"
        suffix = key[len(base):] if key.startswith(base) else ""
        size = int(variant.get("imgsz") or 0)
        name = f"{variant['label']}{suffix}"
        return f"{name} ({size}x{size})" if size else name

    # --------------------------------------------------- 模型管理交互
    def _on_model_selected(self, model_id: int) -> None:
        if int(model_id) == self._vm.active_model_id():
            return
        self._vm.select_model(int(model_id))

    def _on_model_toggle(self, model_id: int) -> None:
        """卡片主按钮：开始训练 / 暂停 / 继续（按当前状态切换）。"""
        if int(model_id) != self._vm.active_model_id():
            self._vm.select_model(int(model_id))
            self._sync_widgets(self._vm.config)
            self._refresh_split()
        status = str(self._vm.config.status)
        if status == "running":
            self._vm.pause()
        elif status == "paused":
            self._vm.resume()
        else:
            self._on_start()

    def _on_model_create(self) -> None:
        self._vm.create_model()
        self._sync_widgets(self._vm.config)
        self._refresh_split()

    def _on_model_renamed(self, model_id: int, name: str) -> None:
        """名称行内编辑提交：改名失败（同名 / 空）时重建卡片回滚显示。"""
        if not self._vm.rename_model(int(model_id), name):
            self._rebuild_models()

    def _on_model_duplicate(self, model_id: int) -> None:
        self._vm.duplicate_model(int(model_id))

    def _on_model_delete(self, model_id: int) -> None:
        model = next(
            (item for item in self._vm.models()
             if int(item.get("model_id", 0)) == int(model_id)),
            None,
        )
        if model is None:
            return
        box = MessageBox(
            "删除训练模型",
            f"确定删除模型「{model.get('name')}」吗？\n\n"
            "其训练产物目录将一并清理（训练记录保留）。",
            self,
        )
        box.yesButton.setText("删除")
        box.cancelButton.setText("取消")
        if box.exec():
            self._vm.remove_model(int(model_id))

    def _on_model_open_dir(self, model_id: int) -> None:
        folder = self._vm.open_model_dir(int(model_id))
        if folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    # --------------------------------------------------- 中央
    def _build_main(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(T.SPACE_ML)

        header = CardWidget(self)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(T.SPACE_XL, T.SPACE_ML, T.SPACE_XL, T.SPACE_ML)
        header_layout.setSpacing(T.SPACE_LG)
        self.tab_seg = SegmentedWidget(header)
        self.tab_seg.addItem("settings", "设置", onClick=lambda: self._switch(0))
        self.tab_seg.addItem("results", "结果", onClick=lambda: self._switch(1))
        self.tab_seg.addItem("compare", "对比", onClick=lambda: self._switch(2))
        self.tab_seg.setCurrentItem("settings")
        # 三个 tab 等分撑满右栏宽度：`SegmentedWidget`（Pivot）自身是横向
        # Minimum 策略、按文字宽度出尺寸，看着很小；改为 Expanding + 拉伸
        # 系数 1，条目侧 `insertWidget(..., stretch=1)` 已就位，随之等分。
        self.tab_seg.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        header_layout.addWidget(self.tab_seg, 1)
        self.state_label = CaptionLabel("", header)
        header_layout.addWidget(self.state_label)
        column.addWidget(header)

        self.stack = QStackedWidget(self)
        self.stack.addWidget(self._build_settings_pane())
        self.stack.addWidget(self._build_results_pane())
        self.stack.addWidget(self._build_compare_pane())
        column.addWidget(self.stack, 1)
        return column

    def _switch(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        if index == 1:
            # 切到结果页时按最新数据重绘一次曲线
            self._on_curves(self._vm.curves())
        elif index == 2:
            # 切到对比页时按勾选的训练记录重建曲线
            self._refresh_compare()

    # --------------------------------------------------- 设置页
    def _build_settings_pane(self) -> QWidget:
        area = ScrollArea(self)
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        # 无训练模型时整体禁用（参数属于所选模型，见 _refresh）
        self.settings_body = body
        # 两列紧凑排布（参照 Halcon DLT）：
        #   左列 数据集拆分（环形图 ｜ 汇总表）→ 模型与权重 → 图像设置 → 设备配置
        #   右列 类别分布（条形图，需整列宽）→ 训练参数（常用 / 高级 / 训练选项）
        #        → 数据增强
        # 卡片按内容自适应高度、控件按参数字符宽度收紧，不再一律纵向占满整行
        columns = QHBoxLayout(body)
        columns.setContentsMargins(T.SPACE_XXS, T.SPACE_XXS, T.SPACE_XXS, T.SPACE_XXS)
        columns.setSpacing(T.SPACE_ML)
        left = QVBoxLayout()
        left.setSpacing(T.SPACE_ML)
        right = QVBoxLayout()
        right.setSpacing(T.SPACE_ML)

        # 左列：数据与输入侧（拆分卡内部「环形图 ｜ 汇总表」左右排列）
        left.addWidget(self._build_split_card())
        left.addWidget(self._build_model_card())
        left.addWidget(self._build_image_card())
        left.addWidget(self._build_device_card())
        left.addStretch(1)
        # 右列：先看数据体检（类别分布图），再调训练参数与增强——图表要整列宽
        # 才舒展（并排时只剩 ~120px，类别名与「数量（占比）」都会被省略号吃掉）
        right.addWidget(self._build_distribution_card())
        right.addWidget(self._build_param_card())
        right.addWidget(self._build_aug_card())
        right.addStretch(1)
        columns.addLayout(left, 1)
        columns.addLayout(right, 1)
        self._pane_columns = columns
        self._pane_left = left
        self._pane_right = right
        self._reflow_settings()
        area.setWidget(body)
        return area

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self._reflow_settings()

    def _reflow_settings(self) -> None:
        """放不下两列时设置页**改单列**（避免右栏被横向裁掉，§8）。

        判据用**布局自身的最小宽**（随字体 / DPI 自动变化）与可用宽比较，
        而不是写死窗口宽：`左列 + 间距 + 右列 ≤ 可用宽` 才用两列。
        单列时卡片自上而下堆叠、由滚动区承接高度。
        """
        columns = getattr(self, "_pane_columns", None)
        if columns is None:
            return
        need = (self._pane_left.minimumSize().width() + columns.spacing()
                + self._pane_right.minimumSize().width())
        available = self.width() - T.SIDE_W_WIDE - 4 * T.SPACE_XL
        direction = (
            QBoxLayout.Direction.LeftToRight if available >= need
            else QBoxLayout.Direction.TopToBottom
        )
        if columns.direction() != direction:
            columns.setDirection(direction)

    def _build_split_card(self) -> CardWidget:
        """数据集拆分：下拉选择 + 概览（环形图 | 汇总表 **左右排列**）。

        概览元素与「数据拆分页 → 拆分概览」**逐元素一致**（环形图 + 汇总表：
        色块 / 名称 / 图像数 / 百分比 + 分隔线 + 合计行），但本页只占半栏宽，
        因此内部改为**左右两列**（图在左、表在右），比竖排更省高度、也更容易
        一眼比对张数与占比；环形图取 `T.CTRL_W_LG`（拆分页全宽用 `XL`）。
        刷新由数据集 / 拆分信号自动驱动，不设「刷新」按钮。
        """
        card, layout = self._card("数据集拆分", SPLIT_HELP)
        # 一个项目可以有多套拆分：训练前选择用哪一套
        pick_row = QHBoxLayout()
        pick_row.addWidget(CaptionLabel("拆分", card))
        self.split_combo = ComboBox(card)
        self.split_combo.currentIndexChanged.connect(self._on_split_picked)
        pick_row.addWidget(self.split_combo, 1)
        layout.addLayout(pick_row)

        body = QHBoxLayout()
        body.setSpacing(T.SPACE_LG)
        self.pie = PieChart(card)
        # 与汇总表左右并排（整列宽，用大号环形图）
        self.pie.setFixedWidth(T.CTRL_W_LG)
        body.addWidget(self.pie, 0, Qt.AlignmentFlag.AlignVCenter)
        self.pie.setToolTip("训练 / 验证 / 测试的占比")

        table = QGridLayout()
        table.setHorizontalSpacing(T.SPACE_MD)
        table.setVerticalSpacing(T.SPACE_SM)
        table.setColumnMinimumWidth(1, T.LABEL_MIN_W)

        dataset_head = CaptionLabel("数据集", card)
        table.addWidget(dataset_head, 0, 0, 1, 2)
        count_head = CaptionLabel("图像", card)
        count_head.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        table.addWidget(count_head, 0, 2)
        percent_head = CaptionLabel("百分比", card)
        percent_head.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        table.addWidget(percent_head, 0, 3)

        self.summary_rows: dict[str, dict[str, QLabel]] = {}
        for row_index, key in enumerate(("train", "val", "test"), start=1):
            chip = QLabel(card)
            chip.setFixedSize(12, 12)
            chip.setStyleSheet(
                f"background: {SPLIT_COLORS[key]}; border-radius: 2px;"
            )
            name = BodyLabel(SPLIT_LABELS[key], card)
            count = CaptionLabel("0", card)
            count.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            percent = CaptionLabel("0 %", card)
            percent.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            table.addWidget(chip, row_index, 0)
            table.addWidget(name, row_index, 1)
            table.addWidget(count, row_index, 2)
            table.addWidget(percent, row_index, 3)
            self.summary_rows[key] = {"count": count, "percent": percent}

        divider = QWidget(card)
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background: {T.GROUP_BORDER};")
        table.addWidget(divider, 4, 0, 1, 4)

        total_name = StrongBodyLabel("合计", card)
        total_name.setToolTip("拆分中的图像（训练 + 验证 + 测试）")
        self.summary_total = CaptionLabel("0", card)
        self.summary_total.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.summary_percent = CaptionLabel("0 %", card)
        self.summary_percent.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        table.addWidget(total_name, 5, 0, 1, 2)
        table.addWidget(self.summary_total, 5, 2)
        table.addWidget(self.summary_percent, 5, 3)

        table_holder = QWidget(card)
        table_holder.setLayout(table)
        # 表在右列、按内容宽度放置：拉满会把「图像 / 百分比」两列推到卡片两端
        body.addWidget(table_holder, 1, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(body)
        return card

    def _build_model_card(self) -> CardWidget:
        """模型与权重：**一个下拉**里二选一（官方预训练变体 / 导入的权重文件）。

        训练时两者等价：`--weights = weights_path or f"{model_key}.pt"`——导入
        的权重覆盖变体，变体此时只作为展示与兜底名。参照 Halcon DLT：预训练模型
        下拉右侧一个导入按钮，导入的文件作为下拉里的**一项**（显示文件名）加入，
        选中即生效。一个控件一个来源，不会有「下拉也选了、权重也导了，用哪个」
        的歧义，也不需要额外的分段开关。
        其它任务（U-Net / 异常检测 / OCR）训练不用自定义权重，只显示变体、
        不显示导入按钮。
        """
        card, layout = self._card("模型与权重", MODEL_HELP)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(T.SPACE_SM)
        row.addWidget(CaptionLabel("预训练模型", card))
        self.variant_combo = ComboBox(card)
        # 显示名带任务后缀（分类的 yolo11n-cls → YOLO11n-cls），避免与
        # 检测变体混淆；下拉宽度按内容收紧，不占满整行
        task = str(self._vm.config.task_type or "detect")
        for candidate in backend_for_task(task).candidates(task):
            self.variant_combo.addItem(candidate["label"], userData=candidate["key"])
        self.variant_combo.setMinimumWidth(170)
        row.addWidget(self.variant_combo, 1)
        self.variant_combo.installEventFilter(self)
        # 导入自定义权重：作为下拉里的一项加入（Halcon DLT 的预训练模型行）
        self.import_btn = icon_button(icon("import"), "导入权重文件", card)
        self.import_btn.clicked.connect(self._on_import_weights)
        row.addWidget(self.import_btn)
        layout.addLayout(row)

        self.variant_desc = CaptionLabel("", card)
        layout.addWidget(self.variant_desc)
        # 权重信息 / 任务状态（导入探测结果）
        self.model_info = CaptionLabel("", card)
        self.model_info.setWordWrap(True)
        layout.addWidget(self.model_info)
        self._update_source_ui("")
        return card

    # -----------------------------------------------------------
    # 权重来源（下拉内二选一：官方变体 ↔ 导入的权重文件）
    # -----------------------------------------------------------
    def _supports_custom_weights(self, task: str = "") -> bool:
        """训练是否用得上自定义权重：YOLO 系 `weights_path` 覆盖预训练权重。

        U-Net / 异常检测 / OCR 的训练参数里没有自定义权重（那是评估 / 导出
        的输入），对它们不提供这个入口，避免出现「导了权重却不生效」。
        """
        task = task or str(self._vm.config.task_type or "detect")
        return backend_for_task(task).key == "yolo"

    def _current_weights(self) -> str:
        """当前生效的自定义权重路径（下拉选中导入项 / 不支持的任务为空串）。"""
        if not self._supports_custom_weights():
            return ""
        return self._selected_custom()

    def _current_model_key(self) -> str:
        """下拉选中的官方变体 key；选中导入的权重时沿用配置里的兜底名。"""
        if self._selected_custom():
            return str(self._vm.config.model_key or "")
        return str(self.variant_combo.currentData() or "")

    def _selected_custom(self) -> str:
        """下拉当前项是否为导入的权重（是则返回路径，否则空串）。"""
        return _custom_path(self.variant_combo.currentData())

    def _custom_index(self, path: str) -> int:
        """下拉里导入权重项的下标（按路径匹配，官方变体项跳过）。"""
        if not path:
            return -1
        for i in range(self.variant_combo.count()):
            if _custom_path(self.variant_combo.itemData(i)) == path:
                return i
        return -1

    def _ensure_custom_item(self, path: str) -> int:
        """把导入的权重作为下拉里的一项加入（同名更新，不重复添加）。"""
        name = Path(path).name or path
        index = self._custom_index(path)
        if index < 0:
            self.variant_combo.addItem(name, userData=(_CUSTOM_TAG, path))
            index = self.variant_combo.count() - 1
        self._elide_weights_items()
        return index

    def _drop_stale_custom_items(self, keep: str) -> None:
        """移除与配置不符的导入权重项（同一次会话里换项目 / 换模型时不留残余）。

        清理后残留项不会「另一项目的权重还能在下拉里选到」，也不会被误当
        当前来源（下拉当前项若不是官方变体，``_selected_custom`` 才会命中）。
        """
        for i in reversed(range(self.variant_combo.count())):
            path = _custom_path(self.variant_combo.itemData(i))
            if path and path != keep:
                self.variant_combo.removeItem(i)

    def _elide_weights_items(self) -> None:
        """导入项文字按**实际可见宽度**中间省略（超长文件名不被裁切）。

        qf 的 ``ComboBox`` 继承 ``QPushButton``，``sizeHint`` 随当前项文字增长
        ——权重文件名很长时会把整行挤窄、文字被裁掉（§8.5 文字不得被截断）。
        与缩略图角标同一口径：**装得下就原样显示**，省略后完整文件名走下拉
        tooltip（``_update_source_ui``）。
        """
        combo = self.variant_combo
        budget = combo.width() - _COMBO_TEXT_CHROME
        if budget <= 0 or getattr(self, "_eliding", False):
            return
        self._eliding = True
        try:
            metrics = combo.fontMetrics()
            for i in range(combo.count()):
                path = _custom_path(combo.itemData(i))
                if not path:
                    continue
                name = Path(path).name or path
                combo.setItemText(i, metrics.elidedText(
                    name, Qt.TextElideMode.ElideMiddle, budget
                ))
        finally:
            self._eliding = False

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt 命名
        # 下拉宽度变化后重算导入项的省略文字（超长文件名不随窗口变窄被裁切）
        if obj is self.variant_combo and event.type() == QEvent.Resize:
            self._elide_weights_items()
        return super().eventFilter(obj, event)

    def _pick_weights_file(self) -> str:
        """弹窗选择权重文件（取消返回空串）。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "导入权重文件", "", "PyTorch Weight (*.pt)"
        )
        return str(path or "")

    def _apply_weights(self, path: str) -> None:
        """把导入的权重写入配置并探测模型信息（下拉里选中对应项）。"""
        self._custom_weights = str(path)
        index = self._ensure_custom_item(self._custom_weights)
        if self.variant_combo.currentIndex() != index:
            # 尚未选中：由变体切换路径统一处理，避免重复导入探测
            self.variant_combo.setCurrentIndex(index)
            return
        self._apply({"weights_path": self._custom_weights})
        if self._model_vm is not None:
            self._model_vm.import_weights(self._custom_weights)
        self._update_source_ui(self._custom_weights)

    def _update_source_ui(self, weights: str) -> None:
        """按当前下拉选择刷新说明文字与导入按钮显隐（不回写配置）。"""
        support = self._supports_custom_weights()
        custom = bool(weights) and support
        self.import_btn.setVisible(support)
        # 选中导入项时下拉项文字就是文件名，变体描述不再适用
        self.variant_desc.setVisible(not custom)
        self.variant_combo.setToolTip(str(weights) if custom else "")
        if not custom:
            self.model_info.setText("")

    def _make_int_spins(
        self, parent, table, chars: int, compact: bool = False
    ) -> list[tuple[str, QWidget]]:
        """按参数表建整型步进器；**同一组用同一字符宽度**（输入框等宽成列）。

        `compact`：用紧凑步进器（省 54px/个）——两列栅格要放进半栏宽卡片时用它。
        """
        fields: list[tuple[str, QWidget]] = []
        for key, label, minimum, maximum, default, step, suffix in table:
            spin = SafeCompactSpinBox(parent) if compact else SafeSpinBox(parent)
            spin.setRange(minimum, maximum)
            spin.setSingleStep(step)
            spin.setValue(default)
            if suffix:
                spin.setSuffix(suffix)
            spin.setFixedWidth(T.field_width(spin, chars))
            spin.valueChanged.connect(self._on_param_changed)
            self._int_spins[key] = spin
            fields.append((label, spin))
        return fields

    def _make_float_spins(
        self, parent, table, chars: int, compact: bool = False
    ) -> list[tuple[str, QWidget]]:
        """按参数表建浮点步进器（同样按组统一字符宽度）。"""
        fields: list[tuple[str, QWidget]] = []
        for key, label, minimum, maximum, default, step, decimals in table:
            spin = (
                SafeCompactDoubleSpinBox(parent) if compact
                else SafeDoubleSpinBox(parent)
            )
            spin.setRange(minimum, maximum)
            spin.setSingleStep(step)
            spin.setDecimals(decimals)
            spin.setValue(default)
            spin.setFixedWidth(T.field_width(spin, chars))
            spin.valueChanged.connect(self._on_param_changed)
            self._float_spins[key] = spin
            fields.append((label, spin))
        return fields

    def _build_param_card(self) -> CardWidget:
        """训练参数：分组（常用 / 高级 / 训练选项）**全部直接显示**。

        分组的依据是「多久调一次」：常用 = 求解器与轮数 / 批次 / 学习率；高级 =
        早停、末轮关增强、数据线程、权重衰减、动量、预热、Dropout、DFL 与训练
        选项——这些默认值在多数数据集上就够用，但**不隐藏**（折叠会让"设了什么"
        看不全，也挡住按任务 / 系列整行显隐）。组内标签列宽与输入框宽度**统一**
        （`T.label_width` + 同字符数），两列输入框成列对齐；每个参数的含义与调试
        方法在标题旁的「?」里（`train_help.PARAM_HELP`）。
        """
        card, layout = self._card("训练参数", PARAM_HELP)

        # —— 常用 ——
        common = ParamGroup("常用", card)
        self.opt_combo = ComboBox(common)
        self.opt_combo.addItems(OPTIMIZERS)
        self.opt_combo.currentIndexChanged.connect(self._on_param_changed)
        common_fields: list[tuple[str, QWidget]] = [("求解器", self.opt_combo)]
        common_fields += self._make_int_spins(
            common, _COMMON_INT_PARAMS, T.STEPPER_CHARS_NARROW, compact=True
        )
        # 学习率小数位多（0.01000 = 7 字符）：按 8 字符定宽，`_equal_width`
        # 会把同组其余控件一起取齐
        common_fields += self._make_float_spins(
            common, _COMMON_FLOAT_PARAMS, T.STEPPER_CHARS, compact=True
        )
        # 同组控件等宽：下拉与步进器长短不一会在两列栅格里显得散乱
        _equal_width([control for _label, control in common_fields])
        common_rows = common.add_grid(common_fields, columns=2)
        self._param_rows.update(dict(zip(
            ["optimizer"]
            + [key for key, *_rest in _COMMON_INT_PARAMS]
            + [key for key, *_rest in _COMMON_FLOAT_PARAMS],
            common_rows,
        )))
        layout.addWidget(common)

        # 异常检测专属参数（仅异常任务显示，放在常用之后，不必展开高级）
        self.anomaly_row = QWidget(card)
        anomaly_layout = QHBoxLayout(self.anomaly_row)
        anomaly_layout.setContentsMargins(0, 0, 0, 0)
        anomaly_layout.setSpacing(T.SPACE_SM)
        self.anomaly_edit = LineEdit(self.anomaly_row)
        self.anomaly_edit.setPlaceholderText("含 normal/ 与 abnormal/ 的目录")
        anomaly_layout.addWidget(self.anomaly_edit, 1)
        anomaly_btn = icon_button(
            FluentIcon.FOLDER, "选择异常检测数据目录", self.anomaly_row
        )
        anomaly_btn.clicked.connect(self._on_browse_anomaly)
        anomaly_layout.addWidget(anomaly_btn)
        self.anomaly_check = CheckBox("预训练权重", self.anomaly_row)
        self.anomaly_check.setChecked(True)
        anomaly_layout.addWidget(self.anomaly_check)
        layout.addWidget(self.anomaly_row)

        # —— 高级：**直接显示**（不折叠）——
        # 折叠会让"设了什么"看不全，也挡住按系列 / 任务的整行显隐；改为与常用
        # 同列排布，靠分组标题区分层级。
        advanced = ParamGroup("高级", card)
        # 权重衰减要显示 0.00050（7 字符）→ 整组按 8 字符定宽（同组等宽）
        advanced_fields: list[tuple[str, QWidget]] = []
        advanced_fields += self._make_int_spins(
            advanced, _ADV_INT_PARAMS, T.STEPPER_CHARS, compact=True
        )
        advanced_fields += self._make_float_spins(
            advanced, _ADV_FLOAT_PARAMS, T.STEPPER_CHARS, compact=True
        )
        adv_keys = [key for key, *_rest in _ADV_INT_PARAMS + _ADV_FLOAT_PARAMS]
        # DFL 留在栅格外单独一行：方便按系列（YOLO11 / 26）整行显隐
        dfl_index = adv_keys.index("dfl")
        dfl_label, dfl_control = advanced_fields.pop(dfl_index)
        adv_keys.pop(dfl_index)
        advanced_rows = advanced.add_grid(advanced_fields, columns=2)
        self._param_rows.update(dict(zip(adv_keys, advanced_rows)))
        advanced.align_labels(
            [label for label, _ in advanced_fields] + [dfl_label]
        )
        self.dfl_spin = self._float_spins["dfl"]
        self.dfl_row = advanced.add_field(dfl_label, dfl_control)
        self._param_rows["dfl"] = self.dfl_row
        self.advanced_group = advanced
        layout.addWidget(advanced)

        # 训练选项：两列栅格（成列对齐，比流式折行整齐）
        options = ParamGroup("训练选项", card)
        check_grid = QGridLayout()
        check_grid.setContentsMargins(0, 0, 0, 0)
        check_grid.setHorizontalSpacing(T.SPACE_LG)
        check_grid.setVerticalSpacing(T.SPACE_SM)
        for index, (key, label) in enumerate(_CHECK_PARAMS):
            box = CheckBox(label, options)
            box.stateChanged.connect(self._on_param_changed)
            check_grid.addWidget(box, index // 2, index % 2)
            self._checks[key] = box
        for column in range(2):
            check_grid.setColumnStretch(column, 1)
        options.add_layout(check_grid)
        self.options_group = options
        layout.addWidget(options)
        return card

    def _build_image_card(self) -> CardWidget:
        """图像设置：输入尺寸、通道与「图像预览」（letterbox + 增强示意）。

        尺寸与通道是图像侧的输入规格（Ultralytics 的 `imgsz` / `channels`），
        每次训练前通常要看一眼，因此独立成卡、与数据区放在一起。
        """
        card, layout = self._card("图像设置", IMAGE_HELP)
        self.image_card = card
        fields = self._make_int_spins(
            card, _IMAGE_INT_PARAMS, T.STEPPER_CHARS, compact=True
        )
        holder = QWidget(card)
        grid = QGridLayout(holder)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(T.SPACE_LG)
        grid.setVerticalSpacing(T.SPACE_MD)
        label_w = T.label_width(card, [label for label, _ in fields])
        rows = []
        for index, (label, control) in enumerate(fields):
            row = FieldRow(label, control, holder, label_width=label_w)
            grid.addWidget(row, index // 2, index % 2)
            rows.append(row)
        for column in range(2):
            grid.setColumnStretch(column, 1)
        self._param_rows.update(dict(zip(
            [key for key, *_rest in _IMAGE_INT_PARAMS], rows
        )))
        layout.addWidget(holder)

        preview_row = QHBoxLayout()
        preview_row.setSpacing(T.SPACE_SM)
        self.preview_btn = icon_button(
            FluentIcon.VIEW, "图像预览（letterbox + 增强示意）", card
        )
        self.preview_btn.clicked.connect(self._on_preview_params)
        preview_row.addWidget(self.preview_btn)
        self.preview_hint = CaptionLabel("", card)
        self.preview_hint.setWordWrap(True)
        preview_row.addWidget(self.preview_hint, 1)
        layout.addLayout(preview_row)
        return card

    def _build_device_card(self) -> CardWidget:
        """设备配置：训练设备（auto / cpu / cuda:n）。

        提示文字单独一行：显卡名可能很长，与下拉挤在一行会把下拉压窄
        （§1 一行最多两个默认控件）。
        """
        card, layout = self._card("设备配置", DEVICE_HELP)
        row = QHBoxLayout()
        row.setSpacing(T.SPACE_MD)
        row.addWidget(CaptionLabel("设备", card))
        self.device_combo = ComboBox(card)
        for option in device_options():
            self.device_combo.addItem(option, userData=option.split(" (")[0])
        self.device_combo.currentIndexChanged.connect(self._on_device_changed)
        row.addWidget(self.device_combo)
        row.addStretch(1)
        layout.addLayout(row)
        self.device_hint = CaptionLabel("", card)
        self.device_hint.setWordWrap(True)
        layout.addWidget(self.device_hint)
        return card

    def _build_distribution_card(self) -> CardWidget:
        """类别分布（**只读**，条形图）+ 类别失衡权重强度（`cls_pw`）。

        用**条形图**表达每个类别的样本量（条长 = 相对最多类，右侧标注「数量
        （占比）」）：文字栅格在窄卡片里会参差不齐，图更直观也更像成品软件。
        统计口径：检测 / 旋转框 / 实例分割按标签**框数**、分类按类别目录图片数。

        真正生效的开关是 `cls_pw`（0 = 关闭，1 = 完全逆频次）：权重由
        Ultralytics 在训练开始时按训练集频次自行生成，**只有检测族**
        （检测 / 实例分割 / 旋转框）的损失消费它，其它任务整组隐藏（§23）。
        """
        card, layout = self._card("类别分布", DISTRIBUTION_HELP)
        self.dist_card = card
        self.dist_chart = BarChart(card)
        layout.addWidget(self.dist_chart)
        self.dist_hint = CaptionLabel("", card)
        self.dist_hint.setWordWrap(True)
        layout.addWidget(self.dist_hint)

        # 失衡权重强度：Ultralytics `cls_pw`（训练开始时按训练集频次自动生成
        # 逆频次权重），仅检测族显示——见 `_apply_param_visibility`
        self.balance_row = QWidget(card)
        balance_box = QHBoxLayout(self.balance_row)
        balance_box.setContentsMargins(0, 0, 0, 0)
        balance_box.setSpacing(T.SPACE_MD)
        balance_box.addWidget(CaptionLabel("失衡权重", self.balance_row))
        # 紧凑步进器：半栏宽卡片里与图表并排，省 54px
        self.cls_pw_spin = SafeCompactDoubleSpinBox(self.balance_row)
        self.cls_pw_spin.setRange(0.0, 1.0)
        self.cls_pw_spin.setSingleStep(0.05)
        self.cls_pw_spin.setDecimals(2)
        self.cls_pw_spin.setValue(0.0)
        self.cls_pw_spin.setFixedWidth(
            T.field_width(self.cls_pw_spin, T.STEPPER_CHARS_NARROW)
        )
        self.cls_pw_spin.setToolTip(
            "类别失衡权重强度：训练开始时按训练集类别频次生成逆频次权重，"
            "1 = 完全逆频次，0 = 关闭"
        )
        self.cls_pw_spin.valueChanged.connect(self._on_param_changed)
        self._float_spins["cls_pw"] = self.cls_pw_spin
        balance_box.addWidget(self.cls_pw_spin)
        balance_box.addStretch(1)
        layout.addWidget(self.balance_row)
        return card

    def _fill_distribution(self) -> None:
        """刷新类别分布卡（条形图 + 一行状态）。

        统计要扫训练集标签，而刷新链路会被参数改动频繁触发——用输入签名
        挡掉无变化的重算重绘。**切换拆分 / 重新分配拆分必须跟着刷新**
        （历史缺陷：只有「未生成」分支才刷新，重新分配后卡片仍显示上一套
        拆分的数字）。
        """
        signature = self._weight_inputs()
        if signature == self._weight_inputs_rendered:
            return
        self._weight_inputs_rendered = signature
        rows = self._vm.class_distribution()
        if not rows:
            self.dist_chart.set_data([])
            self.dist_hint.setText("暂无可统计的训练集")
            return
        # 条长相对**最多类**（类别之间可比）；颜色取项目里定义的类别颜色，
        # 与标注 / 图库同色，右侧同时给出数量与占比
        counts = [row["count"] for row in rows]
        peak = float(max(counts) or 1)
        colors = self._vm.class_colors([row["name"] for row in rows])
        self.dist_chart.set_data([
            (
                str(row["name"]), float(row["count"]), peak, colors[index],
                f"{row['count']}（{row['share']:.0f}%）",
            )
            for index, row in enumerate(rows)
        ])
        fewest = min(counts)
        self.dist_hint.setText(
            "各类别数量一致" if fewest <= 0 or max(counts) == fewest
            else f"最悬殊 1:{max(counts) / fewest:.0f}"
        )

    def _weight_inputs(self) -> tuple:
        """类别分布卡的输入签名：训练集统计只依赖这些（不含磁盘内容）。"""
        config = self._vm.config
        split = (
            self._dataset_vm.active_split()
            if self._dataset_vm is not None else None
        )
        return (
            int(getattr(config, "split_id", 0) or 0),
            str(getattr(config, "task_type", "") or ""),
            0 if split is None else int(split.split_id),
            0 if split is None else int(split.total()),
        )

    def _on_preview_params(self) -> None:
        """生成并查看训练参数预览拼图。"""
        path = self._vm.preview_params()
        if not path:
            return
        config = self._vm.config
        self.preview_hint.setText(f"预览已生成：{Path(path).name}")
        ImagePreviewDialog(
            self, path, "训练参数预览",
            f"imgsz {int(config.imgsz)} · 增强 {'开启' if config.augment else '关闭'}"
            " · 每格为 letterbox 后的训练输入示意（mosaic / mixup 未体现）",
        ).exec()

    def _apply_param_visibility(self) -> None:
        """按**后端能力** + 任务 + 模型系列显隐参数：设了不生效就不显示（§23）。

        三条口径：
        1. **后端消费范围**（`BackendAdapter.train_param_keys`）：自研 U-Net
           只吃轮数 / 批次 / 尺寸 / 学习率 / 种子，Anomalib 只吃轮数 / 批次 /
           种子——名单外的字段整行隐藏，整组都不支持时连组与「高级」入口一起
           隐藏（`build_args` 同样只传这些）；
        2. **检测族**（`is_detection_family`）：「失衡权重」`cls_pw` 与
           「DFL 增益」只在这类任务的损失里生效（分类是无权重 CrossEntropy）；
        3. **模型系列**：YOLO26 是 `reg_max=1` 的 L1 回归，`dfl` 被忽略。
        """
        task = str(self._vm.config.task_type or "detect")
        backend = backend_for_task(task)
        detection = is_detection_family(task)

        for key, row in self._param_rows.items():
            row.setVisible(backend.supports_train_param(key))
        self.options_group.setVisible(backend.supports_train_param("deterministic"))
        self.aug_card.setVisible(backend.supports_train_param("augment"))
        self.image_card.setVisible(
            backend.supports_train_param("imgsz")
            or backend.supports_train_param("channels")
        )
        # 高级组整组都无适用项时隐藏（U-Net / Anomalib 只保留常用与设备）
        advanced_any = any(
            backend.supports_train_param(key)
            for key in ("patience", "close_mosaic", "workers", "weight_decay",
                        "momentum", "warmup_epochs", "dropout", "dfl")
        )
        self.advanced_group.setVisible(advanced_any)

        self.balance_row.setVisible(detection)
        self.dist_card.setVisible(supports_class_counts(task))
        self.dfl_row.setVisible(
            detection and uses_dfl(self._current_model_key())
            and backend.supports_train_param("dfl")
        )

    def _build_aug_card(self) -> CardWidget:
        """数据增强：全局开关 + **逐项启用**开关 + 数值（两列栅格）。

        每项 = 「勾选（是否启用）+ 数值（概率 / 幅度）」，两列排布：勾选列与
        数值列各自按最长内容**取齐**，一眼能顺着列往下看（比流式折行整齐，也
        不会把勾选文字压没，§1/§2）。取消勾选时该项按 0 下发（Ultralytics 里
        0 = 关闭），**数值本身保留在配置里**，重新勾选即恢复——不会因为试一次
        就丢掉调好的概率。

        参数含义 / 调参顺序（一次只改一项、先看预览再跑）在标题旁的「?」里
        （`train_help.AUGMENT_HELP`）。
        """
        card, layout = self._card("数据增强", AUGMENT_HELP)
        self.aug_card = card
        self.augment_check = CheckBox("启用图像增强", card)
        self.augment_check.setChecked(True)
        self.augment_check.stateChanged.connect(self._on_augment_toggled)
        layout.addWidget(self.augment_check)

        self.aug_holder = QWidget(card)
        grid = QGridLayout(self.aug_holder)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(T.SPACE_LG)
        grid.setVerticalSpacing(T.SPACE_SM)
        boxes: list[CheckBox] = []
        spins: list[SafeCompactDoubleSpinBox] = []
        for key, label, minimum, maximum, default, step in _AUG_PARAMS:
            spin = SafeCompactDoubleSpinBox(self.aug_holder)
            spin.setRange(minimum, maximum)
            spin.setSingleStep(step)
            spin.setDecimals(3 if step < 0.01 else 2)
            spin.setValue(default)
            spin.setFixedWidth(T.field_width(spin, T.STEPPER_CHARS_NARROW))
            spin.valueChanged.connect(self._on_param_changed)
            self._aug_spins[key] = spin
            spins.append(spin)

            box_check = CheckBox(label, self.aug_holder)
            box_check.setToolTip(f"启用「{label}」")
            box_check.toggled.connect(self._on_augment_item_toggled)
            self._aug_checks[key] = box_check
            boxes.append(box_check)
        # 勾选列按最长标签取齐（数值框本身已等宽），两列成列对齐
        label_width = T.label_width(self.aug_holder, [box.text() for box in boxes]) + 24
        for index, (box_check, spin) in enumerate(zip(boxes, spins)):
            box_check.setMinimumWidth(label_width)
            column = (index % 2) * 2
            grid.addWidget(box_check, index // 2, column)
            grid.addWidget(spin, index // 2, column + 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        layout.addWidget(self.aug_holder)
        return card

    def _on_augment_item_toggled(self, _checked: bool) -> None:
        """某一项增强的启用开关：联动数值框可用性与配置中的启用标记。"""
        if self._syncing:
            return
        sender = self.sender()
        for key, box in self._aug_checks.items():
            if box is sender:
                self._aug_spins[key].setEnabled(box.isChecked())
                break
        self._on_param_changed()

    # --------------------------------------------------- 结果页
    def _build_results_pane(self) -> QWidget:
        area = ScrollArea(self)
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(T.SPACE_XXS, T.SPACE_XXS, T.SPACE_XXS, T.SPACE_XXS)
        layout.setSpacing(T.SPACE_ML)
        layout.addWidget(self._build_progress_card())
        layout.addWidget(self._build_curve_card())
        layout.addWidget(self._build_compare_card())
        layout.addWidget(self._build_history_card())
        layout.addWidget(self._build_log_card())
        layout.addStretch(1)
        area.setWidget(body)
        return area

    def _build_history_card(self) -> CardWidget:
        """训练记录（历次运行的完成时间与最佳指标，点击回看曲线）。"""
        card, layout = self._card("训练记录")
        self.history_list = TableWidget(card)
        self.history_list.setColumnCount(2)
        self.history_list.setHorizontalHeaderLabels(["时间", "最佳"])
        self.history_list.verticalHeader().setVisible(False)
        self.history_list.setFixedHeight(180)
        self.history_list.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.history_list.setSelectionBehavior(
            TableWidget.SelectionBehavior.SelectRows
        )
        layout.addWidget(self.history_list)
        return card

    def _build_progress_card(self) -> CardWidget:
        card, layout = self._card("训练进度")
        self.progress_bar = ProgressBar(card)
        self.progress_bar.setRange(0, 100)
        layout.addWidget(self.progress_bar)

        tiles = [
            ("轮次", "epoch"), ("迭代", "iteration"), ("学习率", "lr"),
            ("训练损失", "loss"), ("验证损失", "val_loss"),
            ("已用时", "elapsed"), ("预计剩余", "eta"), ("最佳指标", "best"),
        ]
        grid = QGridLayout()
        grid.setSpacing(T.SPACE_ML)
        for index, (title, key) in enumerate(tiles):
            box = QWidget(card)
            column = QVBoxLayout(box)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(T.SPACE_XXS)
            column.addWidget(CaptionLabel(title, box))
            value = BodyLabel("—", box)
            column.addWidget(value)
            grid.addWidget(box, index // 4, index % 4)
            self._tile_values[key] = value
        layout.addLayout(grid)
        return card

    def _build_curve_card(self) -> CardWidget:
        card, layout = self._card("训练曲线")
        self.loss_chart = LineChart(card, y_label="Loss")
        layout.addWidget(self.loss_chart)
        self.metric_chart = LineChart(card)
        layout.addWidget(self.metric_chart)
        return card

    def _build_compare_card(self) -> CardWidget:
        card, layout = self._card("指标对比")
        self.compare_table = TableWidget(card)
        self.compare_table.setColumnCount(3)
        self.compare_table.setHorizontalHeaderLabels(["指标", "上一轮", "最新"])
        self.compare_table.verticalHeader().setVisible(False)
        self.compare_table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.compare_table.setFixedHeight(170)
        self.compare_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.compare_table)
        return card

    def _build_log_card(self) -> CardWidget:
        card, layout = self._card("训练日志")
        self.log_edit = TextEdit(card)
        self.log_edit.setReadOnly(True)
        self.log_edit.setFixedHeight(150)
        layout.addWidget(self.log_edit)
        return card

    # --------------------------------------------------- 对比页
    def _build_compare_pane(self) -> QWidget:
        area = ScrollArea(self)
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(T.SPACE_XXS, T.SPACE_XXS, T.SPACE_XXS, T.SPACE_XXS)
        layout.setSpacing(T.SPACE_ML)
        layout.addWidget(self._build_model_table_card())
        layout.addWidget(self._build_compare_curve_card())
        layout.addWidget(self._build_compare_metric_card())
        layout.addStretch(1)
        area.setWidget(body)
        return area

    def _build_model_table_card(self) -> CardWidget:
        card, layout = self._card("训练模型")
        self.model_table = TableWidget(card)
        self.model_table.setColumnCount(5)
        self.model_table.setHorizontalHeaderLabels(
            ["选择", "时间", "拆分", "训练模型", "最佳"]
        )
        self.model_table.verticalHeader().setVisible(False)
        self.model_table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.model_table.setFixedHeight(150)
        self.model_table.itemChanged.connect(self._on_model_check_changed)
        layout.addWidget(self.model_table)
        return card

    def _build_compare_curve_card(self) -> CardWidget:
        card, layout = self._card("曲线对比")
        self.compare_loss = LineChart(card, y_label="Loss")
        layout.addWidget(self.compare_loss)
        self.compare_metric = LineChart(card)
        layout.addWidget(self.compare_metric)
        return card

    def _build_compare_metric_card(self) -> CardWidget:
        card, layout = self._card("指标对比")
        self.compare_models = TableWidget(card)
        self.compare_models.setColumnCount(7)
        self.compare_models.setHorizontalHeaderLabels(
            ["时间", "拆分", "训练模型", "变体", "最佳指标", "最佳轮次", "最终 Loss"]
        )
        self.compare_models.verticalHeader().setVisible(False)
        self.compare_models.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.compare_models.setFixedHeight(170)
        self.compare_models.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.compare_models)
        return card

    # -----------------------------------------------------------
    # 交互
    # -----------------------------------------------------------
    def _bind(self) -> None:
        self.variant_combo.currentIndexChanged.connect(self._on_variant_changed)

        self._vm.statusChanged.connect(self._on_status)
        self._vm.modelsChanged.connect(lambda _models: self._rebuild_models())
        self._vm.progressChanged.connect(self._on_progress)
        self._vm.metricsChanged.connect(self._on_metrics)
        self._vm.curvesChanged.connect(self._on_curves)
        self._vm.logAppended.connect(self._on_log)
        self._vm.configChanged.connect(self._on_config)
        self._vm.historyChanged.connect(self._on_history)
        self.history_list.cellClicked.connect(self._on_history_clicked)

        if self._model_vm is not None:
            self._model_vm.modelInfo.connect(self._on_model_info)
            self._model_vm.taskStarted.connect(self._on_task_started)
            self._model_vm.taskFailed.connect(self._on_task_failed)
        if self._dataset_vm is not None:
            self._dataset_vm.datasetChanged.connect(lambda _d: self._refresh())
            self._dataset_vm.datasetReady.connect(lambda _p: self._refresh())
            self._dataset_vm.splitsChanged.connect(
                lambda _rows: self._refresh_split()
            )

    def _on_import_weights(self) -> None:
        """导入按钮：选权重文件并作为下拉里的一项加入（取消则不变）。"""
        path = self._pick_weights_file()
        if path:
            self._apply_weights(path)

    def _on_browse_anomaly(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择异常检测数据目录")
        if directory:
            self.anomaly_edit.setText(directory)
            self._apply({"anomaly_root": directory})

    def _on_variant_changed(self, _index: int) -> None:
        if self._syncing:
            return
        custom = self._selected_custom()
        if custom:
            # 选中导入的权重：变体不参与训练（只作为兜底名），只写权重路径
            self._apply_weights(custom)
            return
        key = str(self.variant_combo.currentData() or "")
        if not key:
            return
        if self._model_vm is not None:
            self._model_vm.select_variant(key)
        self._update_variant_desc(key)
        # 选中官方变体 → 导入的权重退出（配置清空，变体生效）
        self._apply({"model_key": key, "weights_path": ""})
        self._apply_param_visibility()      # 换系列后 DFL 等项显隐跟着变

    def _update_variant_desc(self, key: str) -> None:
        """显示变体描述（key 可能带任务后缀，如分类的 yolo11n-cls）。

        下拉选中导入的权重时，下拉项文字就是文件名，不显示变体描述。
        """
        if self._selected_custom():
            self.variant_desc.setText("")
            return
        base = base_variant_key(key)
        variant = next((v for v in YOLO_MODEL_VARIANTS if v["key"] == base), None)
        self.variant_desc.setText(
            f"{variant['desc']} · {variant['imgsz']}px" if variant is not None else ""
        )

    def _variant_index(self, key: str) -> int:
        """下拉里模型 key 的下标：先精确匹配，再按基础名（去任务后缀）兜底。"""
        text = str(key or "")
        index = self.variant_combo.findData(text)
        if index >= 0 or not text:
            return index
        base = base_variant_key(text)
        for i in range(self.variant_combo.count()):
            if _custom_path(self.variant_combo.itemData(i)):
                continue                    # 导入权重项不参与变体匹配
            if base_variant_key(str(self.variant_combo.itemData(i) or "")) == base:
                return i
        return -1

    def _on_device_changed(self, _index: int = 0) -> None:
        self._update_device_hint()
        self._on_param_changed()

    def _update_device_hint(self) -> None:
        """显示当前实际会用的设备（一眼确认有没有走 GPU）。

        用不到 GPU 时文字里带**原因**（CPU 版 PyTorch / 无可用 CUDA），
        下一步操作（装 CUDA 版 PyTorch 的命令）放在 tooltip（§10）。
        """
        device = str(self.device_combo.currentData() or "auto")
        self.device_hint.setText(device_label(device))
        help_text = device_help()
        self.device_hint.setToolTip(help_text)
        self.device_combo.setToolTip(help_text)

    def _on_augment_toggled(self, _state: int) -> None:
        enabled = self.augment_check.isChecked()
        self.aug_holder.setEnabled(enabled)
        self._on_param_changed()

    def _on_param_changed(self, *_args) -> None:
        if self._syncing:
            return
        self._apply(self._collect())

    def _apply(self, changes: dict) -> None:
        """写回训练配置（带同步标志，避免与界面回填互相触发）。"""
        self._syncing = True
        try:
            self._vm.update_config(**changes)
        finally:
            self._syncing = False

    def _collect(self) -> dict:
        """把界面上的全部参数收集为配置字典。"""
        values: dict = {
            "model_key": self._current_model_key(),
            "weights_path": self._current_weights(),
            "optimizer": self.opt_combo.currentText(),
            "device": str(self.device_combo.currentData() or "auto"),
            "anomaly_root": self.anomaly_edit.text().strip(),
            "anomaly_pretrained": self.anomaly_check.isChecked(),
            "augment": self.augment_check.isChecked(),
            # 逐项启用标记：取消勾选的项按 0 下发（见 TrainingConfig.augment_value），
            # 数值本身仍原样保存，重新勾选即恢复
            "augment_enabled": {
                key: box.isChecked() for key, box in self._aug_checks.items()
            },
        }
        for key, spin in self._int_spins.items():
            values[key] = spin.value()
        for key, spin in self._float_spins.items():
            values[key] = spin.value()
        for key, box in self._checks.items():
            values[key] = box.isChecked()
        for key, spin in self._aug_spins.items():
            values[key] = spin.value()
        return values

    def _on_start(self) -> None:
        self._apply(self._collect())
        self._vm.start()

    # -----------------------------------------------------------
    # 配置 / 状态同步
    # -----------------------------------------------------------
    def _on_config(self, config) -> None:
        self._sync_widgets(config)
        self._refresh()

    def sync_from_config(self) -> None:
        """项目类型 / 配置变化后刷新界面（main_window 打开项目时调用）。

        这里按配置**清掉与项目不符的导入权重项**：同一次会话里换项目时，上一
        项目导入的文件不应残留在下拉里（会被误当当前来源）。同一项目内的参数
        改动不走这里，导入过的项保留在列表中（改回官方变体后仍可选回）。
        """
        self._sync_widgets(self._vm.config, drop_stale=True)
        self._refresh()

    def _sync_widgets(self, config, drop_stale: bool = False) -> None:
        self._syncing = True
        try:
            for key, spin in self._int_spins.items():
                spin.setValue(int(getattr(config, key, spin.value()) or 0))
            for key, spin in self._float_spins.items():
                spin.setValue(float(getattr(config, key, spin.value()) or 0.0))
            for key, box in self._checks.items():
                box.setChecked(bool(getattr(config, key, False)))
            for key, spin in self._aug_spins.items():
                spin.setValue(float(getattr(config, key, spin.value()) or 0.0))
            self.augment_check.setChecked(bool(getattr(config, "augment", True)))
            self.aug_holder.setEnabled(self.augment_check.isChecked())
            # 逐项启用：旧项目没这个标记 → 按「值 > 0」推断
            for key, box in self._aug_checks.items():
                enabled = (
                    config.augment_enabled_for(key)
                    if hasattr(config, "augment_enabled_for")
                    else float(getattr(config, key, 0.0) or 0.0) > 0.0
                )
                box.setChecked(bool(enabled))
                self._aug_spins[key].setEnabled(bool(enabled))

            index = self.opt_combo.findText(str(getattr(config, "optimizer", "auto")))
            self.opt_combo.setCurrentIndex(max(0, index))
            device = str(getattr(config, "device", "auto"))
            device_index = self.device_combo.findData(device)
            if device_index < 0:
                self.device_combo.addItem(device, userData=device)
                device_index = self.device_combo.count() - 1
            self.device_combo.setCurrentIndex(device_index)
            self._update_device_hint()

            # 任务类型由项目类型决定：只切模型列表，不让用户改
            task = str(getattr(config, "task_type", "detect"))
            self._reload_variants(task)
            # 权重来源：配置里带导入权重 → 下拉选中该文件项，否则选中官方变体
            weights = str(getattr(config, "weights_path", ""))
            support = self._supports_custom_weights(task)
            keep = weights if support else ""
            if drop_stale:
                self._drop_stale_custom_items(keep)
            if keep:
                self._custom_weights = keep
                index = self._ensure_custom_item(keep)
            else:
                index = self._variant_index(str(getattr(config, "model_key", "")))
            if index >= 0:
                self.variant_combo.setCurrentIndex(index)
            self._update_variant_desc(self._current_model_key())
            self._update_source_ui(weights if support else "")
            self.anomaly_edit.setText(str(getattr(config, "anomaly_root", "")))
            self.anomaly_check.setChecked(
                bool(getattr(config, "anomaly_pretrained", True))
            )
            self.anomaly_row.setVisible(config.task_type == "anomaly")
            self._apply_param_visibility()
        finally:
            self._syncing = False

    def _reload_variants(self, task: str) -> None:
        """按任务类型重建模型下拉（候选由任务对应的后端提供，避免重复添加）。"""
        signature = str(task)
        if getattr(self, "_variant_task", "") == signature:
            return
        previous = self._current_model_key()
        self.variant_combo.clear()
        for candidate in backend_for_task(task).candidates(task):
            self.variant_combo.addItem(candidate["label"], userData=candidate["key"])
        self._variant_task = signature
        index = self.variant_combo.findData(previous)
        self.variant_combo.setCurrentIndex(max(0, index))

    def _refresh(self) -> None:
        """刷新变体描述、数据集拆分图、模型卡片与训练记录。"""
        config = self._vm.config
        if not self.variant_desc.text() and not self._selected_custom():
            base = base_variant_key(str(config.model_key or ""))
            variant = next(
                (v for v in YOLO_MODEL_VARIANTS if v["key"] == base), None
            )
            if variant is not None:
                self.variant_desc.setText(
                    f"{variant['desc']} · {variant['imgsz']}px"
                )
        # 参数属于所选模型：无模型时设置页整体禁用（提示见状态栏）
        self.settings_body.setEnabled(self._vm.has_model())
        self._refresh_split()
        self._rebuild_models()
        self._on_history(self._vm.history())

    def _refresh_split(self) -> None:
        """拆分下拉 + 占比图（一个项目可有多套拆分，训练前先选一套）。"""
        # 类别权重按当前拆分的训练集统计：拆分切换 / 重新分配后一并刷新
        self._fill_distribution()
        config = self._vm.config
        splits = (
            self._dataset_vm.splits_ready() if self._dataset_vm is not None else []
        )
        self._syncing = True
        try:
            self.split_combo.clear()
            for row in splits:
                self.split_combo.addItem(str(row["label"]), userData=int(row["id"]))
            index = self.split_combo.findData(int(config.split_id))
            if index < 0 and splits:
                index = 0
            if index >= 0:
                self.split_combo.setCurrentIndex(index)
        finally:
            self._syncing = False

        row = next(
            (item for item in splits if int(item["id"]) == int(config.split_id)), None
        )
        if row is None and splits:
            row = splits[0]
        if row is not None and int(row["id"]) != int(config.split_id):
            self._syncing = True
            try:
                self._sync_split_config(row)
            finally:
                self._syncing = False

        if config.task_type == "anomaly":
            # 数据目录跟随当前拆分（拆分切换 / 重新划分后同步显示）
            self.anomaly_edit.setText(str(config.anomaly_root or ""))
            self._set_split_summary({}, 0)
            return

        counts = (row or {}).get("counts") or {}
        total = int((row or {}).get("total") or 0)
        if total:
            self._set_split_summary(counts, total)
            return

        # 未生成：用当前拆分参数的预览值（“拆分中的图像” = 三个子集之和，
        # 与拆分页概览同口径——改划到「未划分」的图片不计入）
        preview = (
            self._dataset_vm.preview_split() if self._dataset_vm is not None else {}
        ) or {}
        subsets = preview.get("subsets") or {}
        preview_counts = {
            name: int((subsets.get(name) or {}).get("count", 0))
            for name in ("train", "val", "test")
        }
        self._set_split_summary(preview_counts, sum(preview_counts.values()))

    def _set_split_summary(self, counts: dict, total: int) -> None:
        """更新拆分概览（环形图 + 汇总表；与数据拆分页概览卡同一结构）。"""
        segments = []
        for key in ("train", "val", "test"):
            count = int(counts.get(key, 0) or 0)
            segments.append((SPLIT_LABELS[key], count, SPLIT_COLORS[key]))
            self.summary_rows[key]["count"].setText(str(count))
            self.summary_rows[key]["percent"].setText(
                f"{count * 100 / total:.0f} %" if total else "0 %"
            )
        self.pie.set_data(segments if total else [], str(total) if total else "")
        self.summary_total.setText(str(total))
        self.summary_percent.setText("100 %" if total else "0 %")

    def _on_split_picked(self, _index: int = 0) -> None:
        """在训练页切换拆分：同步项目当前拆分与训练配置。"""
        if self._syncing or self._dataset_vm is None:
            return
        split_id = self.split_combo.currentData()
        if split_id is None:
            return
        row = next(
            (item for item in self._dataset_vm.splits_ready()
             if int(item["id"]) == int(split_id)),
            None,
        )
        if row is None:
            return
        self._dataset_vm.select_split(int(split_id))
        self._syncing = True
        try:
            self._sync_split_config(row)
        finally:
            self._syncing = False
        self._refresh_split()

    def _sync_split_config(self, row: dict) -> None:
        """把选中的拆分写入训练配置（data.yaml 随拆分走）。"""
        split = (
            self._dataset_vm.split_by_id(int(row["id"]))
            if self._dataset_vm is not None else None
        )
        self._vm.update_config(
            split_id=int(row["id"]),
            split_name=str(row["name"]),
            data_yaml=str(getattr(split, "data_yaml", "") or ""),
        )

    def _on_history(self, records: list) -> None:
        """刷新左侧训练记录与「对比」页的模型列表。"""
        # 只有多个训练模型时才在记录里标注归属，单模型下不增加噪音
        multi_setup = len(self._vm.models()) > 1
        self.history_list.setRowCount(len(records))
        for row, record in enumerate(records):
            name = str(record.get("name", ""))
            owner = str(
                record.get("model_name") or record.get("setup_name") or ""
            )
            if multi_setup and owner:
                name = f"{name} · {owner}"
            best = record.get("best_value") or 0.0
            label = str(record.get("best_label") or "")
            self.history_list.setItem(row, 0, QTableWidgetItem(name))
            self.history_list.setItem(
                row, 1,
                QTableWidgetItem(f"{label} {float(best):.4f}".strip()),
            )
        if not records:
            self.history_list.setRowCount(1)
            self.history_list.setItem(0, 0, QTableWidgetItem("暂无记录"))
            self.history_list.setItem(0, 1, QTableWidgetItem(""))

        self._syncing = True
        try:
            self.model_table.setRowCount(len(records))
            for row, record in enumerate(records):
                check = QTableWidgetItem()
                check.setFlags(
                    Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
                )
                # 默认勾选最近两次，便于直接横向对比
                check.setCheckState(
                    Qt.CheckState.Checked if row < 2 else Qt.CheckState.Unchecked
                )
                self.model_table.setItem(row, 0, check)
                self.model_table.setItem(
                    row, 1, QTableWidgetItem(str(record.get("name", "")))
                )
                self.model_table.setItem(
                    row, 2,
                    QTableWidgetItem(str(record.get("split_name") or "—")),
                )
                self.model_table.setItem(
                    row, 3,
                    QTableWidgetItem(str(
                        record.get("model_name")
                        or record.get("setup_name") or "—"
                    )),
                )
                self.model_table.setItem(row, 4, QTableWidgetItem(
                    f"{record.get('best_label') or ''} "
                    f"{float(record.get('best_value') or 0):.4f}".strip()
                ))
            if not records:
                self.model_table.setRowCount(1)
                self.model_table.setItem(0, 0, QTableWidgetItem("暂无记录"))
        finally:
            self._syncing = False
        self._refresh_compare()

    # -----------------------------------------------------------
    # 多模型横向对比
    # -----------------------------------------------------------
    def _model_checked(self, row: int) -> bool:
        item = self.model_table.item(row, 0)
        return bool(
            item is not None
            and item.flags() & Qt.ItemFlag.ItemIsUserCheckable
            and item.checkState() == Qt.CheckState.Checked
        )

    def _on_model_check_changed(self, _item) -> None:
        if not self._syncing:
            self._refresh_compare()

    def _refresh_compare(self) -> None:
        """按勾选的训练记录重建对比曲线与指标表。"""
        records = self._vm.history()
        loss_series, metric_series, rows = [], [], []
        multi_setup = len(self._vm.models()) > 1
        for index, record in enumerate(records):
            if not self._model_checked(index):
                continue
            curves = self._vm.run_curves(index)
            color = _METRIC_COLORS[len(rows) % len(_METRIC_COLORS)]
            label = str(record.get("name") or f"#{index + 1}")
            owner = str(
                record.get("model_name") or record.get("setup_name") or ""
            )
            if multi_setup and owner:
                label = f"{label} · {owner}"
            loss = curves.get("loss")
            if loss:
                loss_series.append((label, color, loss[1]))
            metric = curves.get("metric")
            if metric:
                metric_series.append((f"{label} · {metric[0]}", color, metric[1]))
            rows.append(record)

        self.compare_loss.set_data(loss_series)
        self.compare_metric.set_data(metric_series)
        self._fill_compare_table(rows)

    def _fill_compare_table(self, rows: list) -> None:
        self.compare_models.setRowCount(len(rows))
        for index, record in enumerate(rows):
            best_label = str(record.get("best_label") or "")
            best_value = float(record.get("best_value") or 0.0)
            values = [
                str(record.get("name", "")),
                str(record.get("split_name") or "—"),
                str(
                    record.get("model_name")
                    or record.get("setup_name") or "—"
                ),
                str(record.get("model") or "—"),
                f"{best_label} {best_value:.4f}".strip(),
                str(record.get("best_epoch") or "—"),
                f"{float(record.get('loss') or 0):.4f}",
            ]
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                if str(record.get("results") or ""):
                    item.setToolTip(str(record.get("results")))
                self.compare_models.setItem(index, column, item)

    def _on_history_clicked(self, row: int, _column: int) -> None:
        self._vm.load_run(row)
        self._switch(1)

    def _on_model_info(self, info: dict) -> None:
        """权重探测信息（导入后显示类别数 / 参数量 / 层数）。"""
        if not info:
            self.model_info.setText("")
            return
        names = info.get("names") or {}
        labels = list(names.values()) if isinstance(names, dict) else list(names)
        self.model_info.setText(
            f"类别 {len(labels)} · 参数量 {int(info.get('params', 0)):,} · "
            f"层数 {int(info.get('layers', 0))}"
        )

    def _on_task_started(self, name: str) -> None:
        self.model_info.setText(name)

    def _on_task_failed(self, text: str) -> None:
        self.model_info.setText(text)

    def _on_status(self, status: str) -> None:
        """状态变化：更新状态标签并刷新模型卡片按钮（开始 / 暂停 / 停止）。"""
        text = {
            "idle": "", "running": "训练中", "paused": "已暂停（轮边界）",
            "finished": "已完成", "error": "失败",
        }.get(status, status)
        self.state_label.setText(text)
        self._rebuild_models()

    def _on_progress(self, value: float) -> None:
        self.progress_bar.setValue(int(round(value * 100)))

    def _on_metrics(self, row: dict) -> None:
        """更新指标卡片与「上一轮 / 最新」对比表。"""
        if not row:
            for label in self._tile_values.values():
                label.setText("—")
            self.compare_table.setRowCount(0)
            return
        config = self._vm.config
        total = int(row.get("total") or config.epochs or 0)
        epochs = int(row.get("epoch") or 0)
        iterations = int(row.get("iterations") or 0)
        iteration = int(row.get("iteration") or 0)
        self._tile_values["epoch"].setText(f"{epochs} / {total}")
        self._tile_values["iteration"].setText(
            f"{iteration} / {iterations}" if iterations else str(iteration)
        )
        self._tile_values["lr"].setText(f"{float(row.get('lr') or 0):.6g}")
        self._tile_values["loss"].setText(f"{float(row.get('loss') or 0):.4f}")
        val_loss = float(row.get("val_loss") or 0.0)
        self._tile_values["val_loss"].setText(
            f"{val_loss:.4f}" if val_loss else "—"
        )
        self._tile_values["elapsed"].setText(
            _duration(float(row.get("elapsed") or 0.0))
        )
        eta = float(row.get("eta") or 0.0)
        self._tile_values["eta"].setText(_duration(eta) if eta else "—")
        label = str(row.get("main_label") or self._vm.main_label())
        best_epoch = int(row.get("best_epoch") or 0)
        best_value = float(row.get("best_value") or 0.0)
        self._tile_values["best"].setText(
            f"{label} {best_value:.4f}（Epoch {best_epoch}）" if best_epoch
            else "—"
        )
        self.progress_bar.setValue(int(round(float(row.get("progress") or 0) * 100)))
        self._update_compare(row)

    def _update_compare(self, row: dict) -> None:
        if row.get("partial"):
            return
        previous = row.get("previous") or {}
        entries = [
            ("训练损失", previous.get("loss"), row.get("loss")),
            ("验证损失", previous.get("val_loss"), row.get("val_loss")),
            (str(row.get("main_label") or "指标"),
             previous.get("main_value"), row.get("main_value")),
        ]
        label = str(row.get("main_label") or "")
        best_epoch = int(row.get("best_epoch") or 0)
        entries.append((
            f"最佳 {label}".strip(),
            previous.get("best_value"),
            f"{float(row.get('best_value') or 0):.4f}（Epoch {best_epoch}）"
            if best_epoch else row.get("best_value"),
        ))
        self.compare_table.setRowCount(len(entries))
        for index, (name, old, new) in enumerate(entries):
            self.compare_table.setItem(index, 0, QTableWidgetItem(str(name)))
            self.compare_table.setItem(
                index, 1, QTableWidgetItem(_cell(old))
            )
            self.compare_table.setItem(index, 2, QTableWidgetItem(_cell(new)))

    def _on_curves(self, payload: dict) -> None:
        """把曲线数据交给折线图（Loss + 主评价指标）。"""
        if not payload:
            self.loss_chart.clear()
            self.metric_chart.clear()
            return
        loss_series = [
            (name, _LOSS_COLORS.get(name, _METRIC_COLORS[0]), points)
            for name, points in payload.get("loss", [])
        ]
        metric_series = [
            (name, _METRIC_COLORS[index % len(_METRIC_COLORS)], points)
            for index, (name, points) in enumerate(payload.get("metric", []))
        ]
        self.loss_chart.set_data(loss_series)
        self.metric_chart.set_data(metric_series)

    def _on_log(self, line: str) -> None:
        self.log_edit.append(line)


def _cell(value) -> str:
    """对比表格里的数值格式化。"""
    if value is None or value == "":
        return "—"
    if isinstance(value, str):
        return value
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return str(value)



