"""图库页专用面板：浏览筛选栏、数据集拆分映射、图像标记。

参照 Halcon DLT 的图库视图：
    FilterBar     图像窗口上方的「按标签 / 按标记 / 按拆分 + 文本筛选」
    SplitMapCard  把选中图像划入 训练 / 验证 / 测试，或移出全部拆分集
    TagCard       图像标记（类似备注），点击标记即对选中图像追加 / 删除
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    Action,
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    FluentIcon,
    PillPushButton,
    RoundMenu,
    SearchLineEdit,
    Slider,
    StrongBodyLabel,
    TransparentToolButton,
)

from src.utils.constants import SPLIT_COLORS

_COLOR_EMPTY = "#1B1B1B"

# 筛选档位（按顺序循环点击）
_LABEL_STEPS = (("all", "全部"), ("annotated", "已标注"), ("unannotated", "未标注"))
_MARK_BASE_STEPS = (("all", "全部"), ("tagged", "带标记"), ("untagged", "无标记"))
_SPLIT_STEPS = (
    ("all", "全部"), ("train", "训练"), ("val", "验证"),
    ("test", "测试"), ("none", "未划分"),
)
# 拆分映射的行：键为空串表示「不在任何一个拆分集里」
_SPLIT_ROWS = (
    ("", "不在任何一个拆分集里", _COLOR_EMPTY),
    ("train", "训练", SPLIT_COLORS["train"]),
    ("val", "验证", SPLIT_COLORS["val"]),
    ("test", "测试", SPLIT_COLORS["test"]),
)
# 行键 ↔ 子集标记（数据层用 T / V / E，界面用 train / val / test）
_SPLIT_TO_MARK = {"train": "T", "val": "V", "test": "E", "": ""}
_MARK_TO_SPLIT = {"T": "train", "V": "val", "E": "test", "": ""}
_TAG_COLUMNS = 3


def color_icon(color: str, size: int = 11) -> QIcon:
    """小色块图标（颜色为空时返回空图标 = 无色）。"""
    if not color:
        return QIcon()
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(color))
    return QIcon(pixmap)


class FilterBar(CardWidget):
    """图像窗口上方的筛选栏。

    三个按钮各自在候选档位之间循环切换，右侧是文本筛选框（文件名 / 类别 / 标记）。
    """

    filterChanged = Signal()
    textChanged = Signal(str)
    thumbSizeChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._label = "all"
        self._mark = "all"
        self._split = "all"
        self._tag_names: list[str] = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        self.label_pill = self._pill("按标签")
        self.label_pill.clicked.connect(lambda: self._cycle("label"))
        layout.addWidget(self.label_pill)

        self.mark_pill = self._pill("按标记")
        self.mark_pill.clicked.connect(lambda: self._cycle("mark"))
        layout.addWidget(self.mark_pill)

        self.split_pill = self._pill("按拆分")
        self.split_pill.clicked.connect(lambda: self._cycle("split"))
        layout.addWidget(self.split_pill)

        self.search = SearchLineEdit(self)
        self.search.setPlaceholderText("输入筛选文本")
        self.search.setFixedWidth(240)
        self.search.textChanged.connect(lambda _t: self.textChanged.emit(self.text()))
        self.search.searchSignal.connect(lambda _t: self.textChanged.emit(self.text()))
        layout.addWidget(self.search)
        layout.addStretch(1)

        self.size_slider = Slider(Qt.Orientation.Horizontal, self)
        self.size_slider.setRange(0, 2)
        self.size_slider.setValue(1)
        self.size_slider.setFixedWidth(90)
        self.size_slider.setToolTip("缩略图尺寸")
        self.size_slider.valueChanged.connect(
            lambda value: self.thumbSizeChanged.emit(int(value))
        )
        layout.addWidget(self.size_slider)

        self.summary = CaptionLabel("", self)
        layout.addWidget(self.summary)

        self._refresh_pills()

    # -----------------------------------------------------------
    def _pill(self, title: str) -> PillPushButton:
        button = PillPushButton(self)
        button.setCheckable(True)          # 自带选中态，用来看「当前档位是否生效」
        button.setIcon(FluentIcon.ACCEPT)
        button.setText(f"{title}: 全部")
        button.setToolTip("点击在各档位之间循环切换")
        return button

    def _mark_steps(self) -> tuple:
        return _MARK_BASE_STEPS + tuple((name, name) for name in self._tag_names)

    def _steps(self, which: str) -> tuple:
        return {
            "label": _LABEL_STEPS,
            "mark": self._mark_steps(),
            "split": _SPLIT_STEPS,
        }[which]

    def _value(self, which: str) -> str:
        return {"label": self._label, "mark": self._mark, "split": self._split}[which]

    def _cycle(self, which: str) -> None:
        keys = [key for key, _ in self._steps(which)]
        current = self._value(which)
        index = (keys.index(current) + 1) % len(keys) if current in keys else 0
        setattr(self, f"_{which}", keys[index])
        self._refresh_pills()
        self.filterChanged.emit()

    def _refresh_pills(self) -> None:
        for which, pill, title in (
            ("label", self.label_pill, "按标签"),
            ("mark", self.mark_pill, "按标记"),
            ("split", self.split_pill, "按拆分"),
        ):
            steps = dict(self._steps(which))
            current = self._value(which)
            pill.setText(f"{title}: {steps.get(current, current)}")
            pill.setChecked(current != "all")

    # -----------------------------------------------------------
    def set_tag_names(self, names: list) -> None:
        """把现有标记并入「按标记」的候选档位。"""
        self._tag_names = [str(name) for name in names]
        if self._mark not in dict(self._mark_steps()):
            self._mark = "all"
        self._refresh_pills()

    def label_value(self) -> str:
        return self._label

    def mark_value(self) -> str:
        return self._mark

    def split_value(self) -> str:
        return self._split

    def text(self) -> str:
        return self.search.text().strip()

    def set_summary(self, shown: int, total: int) -> None:
        self.summary.setText(f"显示 {shown} / {total} 张")

    def reset(self) -> None:
        """复位到「全部」。"""
        self._label = self._mark = self._split = "all"
        self.search.blockSignals(True)
        self.search.setText("")
        self.search.blockSignals(False)
        self._refresh_pills()


class SplitRow(QWidget):
    """拆分映射的一行：色块 + 名称 + 数量。"""

    clicked = Signal(str)

    def __init__(self, key: str, text: str, color: str, parent=None):
        super().__init__(parent)
        self.setObjectName("SplitRow")
        self.key = key
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 8, 3)
        layout.setSpacing(8)

        swatch = QLabel(self)
        swatch.setFixedSize(14, 14)
        swatch.setStyleSheet(
            f"background: {color}; border: 1px solid #5A5A5A; border-radius: 2px;"
        )
        layout.addWidget(swatch)
        layout.addWidget(BodyLabel(text, self), 1)

        self.count = CaptionLabel("", self)
        layout.addWidget(self.count)

    def set_count(self, value) -> None:
        self.count.setText("" if value is None else str(value))

    def set_active(self, active: bool) -> None:
        self.setStyleSheet(
            "#SplitRow { background: rgba(0, 163, 140, 0.85); border-radius: 4px; }"
            if active else ""
        )

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.key)
        super().mousePressEvent(event)


class SplitMapCard(CardWidget):
    """数据集拆分映射：点击某一行即可把选中图像划入对应子集。"""

    splitRequested = Signal(str)     # "" / train / val / test

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        layout.addWidget(StrongBodyLabel("数据集拆分映射", self))

        self.name_box = ComboBox(self)
        self.name_box.setToolTip("当前使用的拆分名称")
        layout.addWidget(self.name_box)

        lock = QWidget(self)
        lock_layout = QHBoxLayout(lock)
        lock_layout.setContentsMargins(0, 0, 0, 0)
        lock_layout.setSpacing(6)
        lock_icon = TransparentToolButton(lock)
        lock_icon.setIcon(FluentIcon.CONSTRACT)
        lock_icon.setFixedSize(22, 22)
        lock_icon.setEnabled(False)
        lock_layout.addWidget(lock_icon)
        self.lock_label = CaptionLabel("该拆分由训练使用，无法更改。", lock)
        self.lock_label.setWordWrap(True)
        lock_layout.addWidget(self.lock_label, 1)
        layout.addWidget(lock)
        self.lock_row = lock

        self.rows: dict[str, SplitRow] = {}
        for key, text, color in _SPLIT_ROWS:
            row = SplitRow(key, text, color, self)
            row.clicked.connect(self.splitRequested)
            layout.addWidget(row)
            self.rows[key] = row

        self.hint = CaptionLabel("选中图像后点击某一行即可改划。", self)
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

    def set_state(
        self, name: str, counts: dict, current: str = "", locked: bool = False
    ) -> None:
        """刷新拆分名称、各档位数量、选中图像所在档位与锁定状态。

        Args:
            counts: 以子集标记（T / V / E / 空串）为键的图片数。
            current: 当前选中图像所在的子集标记（T / V / E / 空串）。
        """
        self.name_box.blockSignals(True)
        if self.name_box.count() == 0:
            self.name_box.addItem(name or "dataset")
        else:
            self.name_box.setItemText(0, name or "dataset")
        self.name_box.setCurrentIndex(0)
        self.name_box.blockSignals(False)
        self.name_box.setEnabled(not locked)

        active = _MARK_TO_SPLIT.get(str(current or ""), "")
        self.lock_row.setVisible(locked)
        for key, row in self.rows.items():
            row.set_count(counts.get(_SPLIT_TO_MARK.get(key, ""), 0))
            row.set_active(key == active)
        self.hint.setText(
            "该拆分已生成并用于训练，映射只读。" if locked
            else "选中图像后点击某一行即可改划；「不在任何一个拆分集里」表示未划分。"
        )


class TagCard(CardWidget):
    """图像标记：点击某个标记即对选中图像「追加 / 删除」（类似备注）。"""

    tagToggled = Signal(str)         # 点击标记 → 追加或删除
    addRequested = Signal()
    editRequested = Signal(str)
    deleteRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._names: list[str] = []
        self._colors: dict[str, str] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(6)
        header.addWidget(StrongBodyLabel("图像标记", self))
        header.addStretch(1)
        self.add_btn = TransparentToolButton(self)
        self.add_btn.setIcon(FluentIcon.ADD)
        self.add_btn.setToolTip("新增图像标记")
        self.add_btn.setFixedSize(28, 28)
        self.add_btn.clicked.connect(self.addRequested)
        header.addWidget(self.add_btn)
        layout.addLayout(header)

        self.hint = CaptionLabel("为图像设置图像标记：", self)
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        holder = QWidget(self)
        self.grid = QGridLayout(holder)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(4)
        layout.addWidget(holder)

    def set_tags(self, names: list, colors: dict, counts: dict) -> None:
        """重建标记色块（含每个标记已挂的图片数）。"""
        self._names = [str(name) for name in names]
        self._colors = dict(colors or {})
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if not self._names:
            self.hint.setText("暂无图像标记，点击右上角 + 新增。")
            return
        self.hint.setText("为图像设置图像标记（点击即追加，再点删除）：")
        for index, name in enumerate(self._names):
            chip = PillPushButton(self)
            chip.setCheckable(False)
            chip.setText(f"{name}  {counts.get(name, 0)}")
            chip.setIcon(color_icon(self._colors.get(name, "")))
            chip.setToolTip(
                f"标记「{name}」已挂 {counts.get(name, 0)} 张\n"
                "点击：选中图像若没有则追加、有则删除；右键可编辑 / 删除"
            )
            chip.clicked.connect(lambda _=False, n=name: self.tagToggled.emit(n))
            chip.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            chip.customContextMenuRequested.connect(
                lambda position, n=name, c=chip: self._menu(n, c, position)
            )
            self.grid.addWidget(chip, index // _TAG_COLUMNS, index % _TAG_COLUMNS)

    def _menu(self, name: str, chip, position) -> None:
        menu = RoundMenu(parent=self)
        menu.addAction(
            Action("编辑标记", triggered=lambda: self.editRequested.emit(name))
        )
        menu.addAction(
            Action("删除标记", triggered=lambda: self.deleteRequested.emit(name))
        )
        menu.exec(chip.mapToGlobal(position))
