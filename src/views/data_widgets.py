"""数据模块共用面板：数据导入、数据集概览、标签类别、浏览筛选。

图库 / 图像标注 / 标注检查 / 数据拆分 四页复用这些面板，
保证左侧信息栏的布局与交互一致（参照 Halcon DLT 的侧栏结构）。
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    FluentIcon,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    SegmentedWidget,
    Slider,
    StrongBodyLabel,
    TransparentToolButton,
)

from src.views.dialogs.class_edit_dialog import PRESET_COLORS, ClassEditDialog

_COLOR_FALLBACK = "#66CCFF"
_UNLABELED_ROW_LABEL = "无标签"


class StatsCard(CardWidget):
    """数据集概览：图片 / 标签 / 类别 / 重复 + 来源目录。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(5)
        layout.addWidget(StrongBodyLabel("数据集概览", self))

        self._values: dict[str, BodyLabel] = {}
        for key, text in (
            ("images", "图片数"), ("labels", "标签数"),
            ("classes", "类别数"), ("duplicates", "重复图片"),
        ):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(CaptionLabel(text, self))
            value = BodyLabel("—", self)
            value.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            row.addWidget(value, 1)
            layout.addLayout(row)
            self._values[key] = value

        self.source = CaptionLabel("来源：未导入数据集", self)
        self.source.setWordWrap(True)
        layout.addWidget(self.source)

    def set_dataset(self, dataset) -> None:
        if dataset is None:
            for label in self._values.values():
                label.setText("—")
            self.source.setText("来源：未导入数据集")
            return
        self._values["images"].setText(str(dataset.image_count))
        self._values["labels"].setText(str(dataset.label_count))
        self._values["classes"].setText(str(dataset.class_count))
        self._values["duplicates"].setText(str(dataset.duplicate_count))
        self.source.setText(f"来源：{dataset.source_path or '—'}")


class ImportCard(CardWidget):
    """数据导入：从文件夹或图片文件导入（图库页左上角）。"""

    folderRequested = Signal()
    filesRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        layout.addWidget(StrongBodyLabel("数据导入", self))

        self.folder_btn = PrimaryPushButton(self)
        self.folder_btn.setText("导入文件夹")
        self.folder_btn.setIcon(FluentIcon.FOLDER_ADD)
        self.folder_btn.clicked.connect(self.folderRequested)
        layout.addWidget(self.folder_btn)

        self.files_btn = PushButton(self)
        self.files_btn.setText("导入图片")
        self.files_btn.setIcon(FluentIcon.PHOTO)
        self.files_btn.clicked.connect(self.filesRequested)
        layout.addWidget(self.files_btn)

        self.hint = CaptionLabel("也可以把文件夹或图片直接拖入窗口。", self)
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)


class ClassRow(QWidget):
    """类别列表的一行：色块 + 名称 + 样本数 + 行尾「编辑 / 删除」图标按钮。

    「全部类别」行不可编辑（editable=False），只用于清除类别筛选。
    """

    clicked = Signal(str)              # 类别名（"" 表示「全部类别」）
    editRequested = Signal(str)
    deleteRequested = Signal(str)

    def __init__(
        self,
        name: str,
        color: str,
        count=None,
        editable: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self.category = name
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 4, 2)
        layout.setSpacing(6)

        chip = QLabel(self)
        chip.setFixedSize(12, 12)
        if color:
            chip.setStyleSheet(f"background: {color}; border-radius: 2px;")
        else:
            # 无色（「无标签」行）：空心方框
            chip.setStyleSheet(
                "background: transparent; border: 1px solid #6A6A6A;"
                " border-radius: 2px;"
            )
        layout.addWidget(chip)

        layout.addWidget(BodyLabel(name or _UNLABELED_ROW_LABEL, self), 1)
        if count is not None:
            layout.addWidget(CaptionLabel(str(count), self))
        if editable:
            layout.addWidget(
                self._tool(FluentIcon.EDIT, "编辑名称与颜色", self.editRequested)
            )
            layout.addWidget(
                self._tool(FluentIcon.DELETE, "删除该类别", self.deleteRequested)
            )

    def _tool(self, icon, tip: str, signal) -> TransparentToolButton:
        button = TransparentToolButton(self)
        button.setIcon(icon)
        button.setToolTip(tip)
        button.setFixedSize(24, 24)
        button.clicked.connect(lambda: signal.emit(self.category))
        return button

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.category)
        super().mousePressEvent(event)


class ClassCard(CardWidget):
    """标签类别：类别列表与增删改排序。

    - 列表上方：「新增」「上移」「下移」（全部为图标按钮）
    - 列表首行：「无标签」（无色），表示不给图像指定任何类别
    - 每行尾部：「编辑」「删除」，编辑打开统一的类别编辑弹窗
    - 点击类别行只广播，不直接筛选：图库页用于给选中图像赋值
    """

    selected = Signal(int)             # 选中类别的 cls_id
    classClicked = Signal(str)         # 点击类别行（"" = 无标签）
    changed = Signal()                 # 类别增删改之后（供页面刷新统计）

    def __init__(self, category_vm, parent=None):
        super().__init__(parent)
        self._vm = category_vm
        self._counts: dict = {}
        self._classes: list = []
        self._items: dict[str, QListWidgetItem] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        layout.addWidget(StrongBodyLabel("标签类别", self))

        # 列表上方：新增 + 顺序排列
        tools = QHBoxLayout()
        tools.setSpacing(4)
        self.add_btn = self._tool(FluentIcon.ADD, "新增类别", self._on_add)
        self.up_btn = self._tool(
            FluentIcon.UP, "上移选中类别", lambda: self._on_move(-1)
        )
        self.down_btn = self._tool(
            FluentIcon.DOWN, "下移选中类别", lambda: self._on_move(1)
        )
        for button in (self.add_btn, self.up_btn, self.down_btn):
            tools.addWidget(button)
        tools.addStretch(1)
        layout.addLayout(tools)

        self.list = QListWidget(self)
        self.list.setMinimumHeight(150)
        self.list.itemSelectionChanged.connect(self._on_selection)
        layout.addWidget(self.list)

        self.hint = CaptionLabel(
            "点击类别行可给它赋值；行尾按钮可编辑名称 / 颜色，或删除类别。", self
        )
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        if self._vm is not None:
            self._vm.classesChanged.connect(self._on_classes_changed)
        self._update_tools()

    def _tool(self, icon, tip: str, slot) -> TransparentToolButton:
        button = TransparentToolButton(self)
        button.setIcon(icon)
        button.setToolTip(tip)
        button.setFixedSize(28, 28)
        button.clicked.connect(slot)
        return button

    def _update_tools(self) -> None:
        """没有任何类别时禁用排序按钮，避免"点了没反应"。"""
        has_selection = self.current_class_id() is not None
        self.add_btn.setEnabled(self._vm is not None)
        self.up_btn.setEnabled(has_selection)
        self.down_btn.setEnabled(has_selection)

    # -----------------------------------------------------------
    # 列表渲染
    # -----------------------------------------------------------
    def set_classes(self, classes: list, counts: dict | None = None) -> None:
        self._classes = list(classes or [])
        if counts is not None:
            self._counts = dict(counts)
        selected = self.current_class_id()

        self.list.blockSignals(True)
        self.list.clear()
        self._items = {}
        # 首行「无标签」（无色）：表示不给图像指定任何类别
        self._add_row("", "", None, editable=False)
        for cls in self._classes:
            self._add_row(
                cls.name, cls.color, self._counts.get(cls.name), cls_id=cls.cls_id
            )
        self.list.blockSignals(False)

        if selected is not None:
            for name, item in self._items.items():
                if item.data(Qt.ItemDataRole.UserRole) == selected:
                    self.list.setCurrentItem(item)
                    break
        self._update_tools()

    def _add_row(
        self, name: str, color: str, count, editable: bool = True, cls_id: int = -1
    ) -> None:
        row = ClassRow(name, color, count, editable=editable, parent=self.list)
        row.clicked.connect(self._on_row_clicked)
        row.editRequested.connect(self._on_edit)
        row.deleteRequested.connect(self._on_delete)

        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, int(cls_id))
        item.setData(Qt.ItemDataRole.UserRole + 1, name)
        item.setSizeHint(QSize(0, 30))
        item.setToolTip(
            f"id={cls_id}  颜色 {color}" if editable
            else "不给图像指定任何类别"
        )
        self.list.addItem(item)
        self.list.setItemWidget(item, row)
        self._items[name] = item

    def set_counts(self, counts: dict) -> None:
        self._counts = dict(counts or {})

    def current_class_id(self) -> int | None:
        item = self.list.currentItem()
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return int(value) if isinstance(value, int) and value >= 0 else None

    def current_class_name(self) -> str:
        item = self.list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole + 1)) if item is not None else ""

    def _find(self, name: str):
        return next((cls for cls in self._classes if cls.name == name), None)

    def _next_color(self) -> str:
        used = {str(cls.color).upper() for cls in self._classes}
        for value in PRESET_COLORS:
            if value.upper() not in used:
                return value
        return PRESET_COLORS[len(self._classes) % len(PRESET_COLORS)]

    # -----------------------------------------------------------
    # 交互
    # -----------------------------------------------------------
    def _on_classes_changed(self, classes) -> None:
        self.set_classes(list(classes))

    def _on_selection(self) -> None:
        self._update_tools()
        cls_id = self.current_class_id()
        if cls_id is not None:
            self.selected.emit(int(cls_id))

    def _on_row_clicked(self, name: str) -> None:
        """点击类别行：选中该行并广播（是否筛选 / 赋值由页面决定）。"""
        item = self._items.get(name)
        if item is not None:
            self.list.setCurrentItem(item)
        self.classClicked.emit(name)
        self.hint.setText("已选择「无标签」" if not name else f"已选择类别「{name}」")

    def _on_add(self) -> None:
        if self._vm is None:
            return
        dialog = ClassEditDialog(
            self.window(),
            color=self._next_color(),
            taken=[cls.name for cls in self._classes],
        )
        if not dialog.exec():
            return
        data = dialog.result_data()
        self._vm.add_class(data["name"], data["color"])
        self.changed.emit()
        self.hint.setText(f"已新增类别「{data['name']}」")

    def _on_edit(self, name: str) -> None:
        if self._vm is None:
            return
        target = self._find(name)
        if target is None:
            return
        dialog = ClassEditDialog(
            self.window(),
            name=target.name,
            color=target.color,
            cls_id=target.cls_id,
            taken=[cls.name for cls in self._classes if cls.name != name],
        )
        if not dialog.exec():
            return
        data = dialog.result_data()
        self._vm.rename_class(target.cls_id, data["name"])
        self._vm.set_color(target.cls_id, data["color"])
        self.changed.emit()
        self.hint.setText(f"类别已更新为「{data['name']}」")

    def _on_delete(self, name: str) -> None:
        if self._vm is None:
            return
        target = self._find(name)
        if target is None:
            return
        box = MessageBox(
            "删除类别",
            f"确定要删除类别「{name}」吗？\n\n"
            "删除后类别 id 会重排，已有标注需要重新核查。",
            self.window(),
        )
        box.yesButton.setText("删除")
        box.cancelButton.setText("取消")
        if not box.exec():
            return
        self._vm.remove_class(target.cls_id)
        self.changed.emit()

    def _on_move(self, delta: int) -> None:
        if self._vm is None:
            return
        cls_id = self.current_class_id()
        if cls_id is not None:
            self._vm.move_class(int(cls_id), delta)
            self.changed.emit()


class FilterCard(CardWidget):
    """浏览筛选：全部 / 已标注 / 未标注 + 缩略图尺寸。"""

    filterChanged = Signal(str)      # all / annotated / unannotated
    thumbSizeChanged = Signal(int)

    def __init__(self, with_size: bool = True, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        layout.addWidget(StrongBodyLabel("浏览筛选", self))

        self.segment = SegmentedWidget(self)
        self._key = "all"
        for key, text in (("all", "全部"), ("annotated", "已标注"),
                          ("unannotated", "未标注")):
            self.segment.addItem(key, text, onClick=lambda k=key: self._on_filter(k))
        layout.addWidget(self.segment)
        self.segment.setCurrentItem("all")

        self.summary = CaptionLabel("共 0 张", self)
        layout.addWidget(self.summary)

        if with_size:
            size_row = QHBoxLayout()
            size_row.addWidget(CaptionLabel("缩略图", self))
            self.size_slider = Slider(Qt.Orientation.Horizontal, self)
            self.size_slider.setRange(0, 2)
            self.size_slider.setValue(1)
            self.size_slider.valueChanged.connect(
                lambda value: self.thumbSizeChanged.emit(int(value))
            )
            size_row.addWidget(self.size_slider, 1)
            layout.addLayout(size_row)

    def _on_filter(self, key: str) -> None:
        self._key = key
        self.filterChanged.emit(key)

    def set_summary(self, total: int, annotated: int) -> None:
        self.summary.setText(f"共 {total} 张 · 已标注 {annotated} 张")

    def set_filter(self, key: str) -> None:
        self._key = key
        self.segment.setCurrentItem(key)

    def filter_key(self) -> str:
        return self._key


class SectionLabel(CaptionLabel):
    """小节标题（用于侧栏分隔）。"""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        font = self.font()
        font.setBold(True)
        self.setFont(font)


def side_column(*widgets, width: int = 268) -> QWidget:
    """把若干面板竖排为一个固定宽度的侧栏。"""
    container = QWidget()
    container.setFixedWidth(width)
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
    for widget in widgets:
        layout.addWidget(widget)
    layout.addStretch(1)
    return container
