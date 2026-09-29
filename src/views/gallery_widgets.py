"""图库页专用面板：浏览筛选栏、数据集拆分映射、图像标记。

参照 Halcon DLT 的图库视图：
    FilterBar     图像窗口上方的「标签 / 数据集拆分 / 标记 + 文本筛选」下拉菜单
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
    CheckBox,
    ComboBox,
    DropDownPushButton,
    FluentIcon,
    Flyout,
    FlyoutViewBase,
    PillPushButton,
    PushButton,
    RoundMenu,
    SearchLineEdit,
    StrongBodyLabel,
    TransparentToolButton,
)

from src.utils.constants import SPLIT_COLORS
from src.views.ui import FlowContainer, ToolGroup
from src.views.ui import tokens as T

# 标记 / 类别色块边长（装饰性尺寸，组件内部常量）
_SWATCH_SIZE = 14

_COLOR_EMPTY = "#1B1B1B"

# 「全部」项的键（多选面板里选它表示不筛选）
_ALL_KEY = "all"
# 标签菜单的档位：标注状态 + 无标签 + 各标签类别
_ANNOTATED_STEPS = (("annotated", "已标注"), ("unannotated", "未标注"))
_UNLABELED_KEY = "unlabeled"
_CLASS_PREFIX = "class:"
# 标记菜单的档位
_MARK_STEPS = (("tagged", "带标记"), ("untagged", "无标记"))
# 数据集拆分档位（来自左侧「数据集拆分映射」）
_SPLIT_STEPS = (
    ("train", "训练"), ("val", "验证"), ("test", "测试"), ("none", "未划分"),
)



class _FilterPanel(FlyoutViewBase):
    """筛选下拉面板：一组可多选的勾选项（点「全部」即清空其它选择）。"""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._boxes: dict[str, CheckBox] = {}
        self._order: list[str] = []
        self.vBoxLayout = QVBoxLayout(self)
        self.vBoxLayout.setContentsMargins(
            T.CARD_PAD_H, T.SPACE_ML, T.CARD_PAD_H, T.CARD_PAD_V
        )
        self.vBoxLayout.setSpacing(T.SPACE_XXS)
        self.setMinimumWidth(T.PANEL_MIN_W)

    def set_items(self, items: list, selected=()) -> None:
        """items: [(key, text), ...]；selected 为已勾选的键（空 = 全部）。"""
        selected = set(selected) or {_ALL_KEY}
        for key, text in items:
            box = CheckBox(text, self)
            box.setChecked(key in selected)
            box.toggled.connect(lambda checked, k=key: self._on_toggled(k, checked))
            box.setMinimumHeight(28)
            self.vBoxLayout.addWidget(box)
            self._boxes[key] = box
            self._order.append(key)

    def _on_toggled(self, key: str, checked: bool) -> None:
        if checked:
            if key == _ALL_KEY:
                # 勾「全部」→ 清掉其它选择
                for other in self._order:
                    if other != _ALL_KEY:
                        self._set_checked(other, False)
            else:
                # 勾具体项 → 取消「全部」
                self._set_checked(_ALL_KEY, False)
        if not self.selected():
            # 一个都不剩 → 回到「全部」
            self._set_checked(_ALL_KEY, True)
        self.changed.emit()

    def _set_checked(self, key: str, checked: bool) -> None:
        box = self._boxes.get(key)
        if box is None:
            return
        box.blockSignals(True)
        box.setChecked(checked)
        box.blockSignals(False)

    def selected(self) -> list[str]:
        """已勾选的键（不含「全部」；空列表表示全部）。"""
        return [
            key for key in self._order
            if key != _ALL_KEY and self._boxes[key].isChecked()
        ]
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
    """图像窗口上方的筛选栏：三个多选下拉菜单 + 文本筛选。

    每个菜单都是「下拉 + 勾选项」，**可多选**；选项自动取自左侧各面板：
        标签       已标注 / 未标注 / 无标签 / 各标签类别（左侧「标签类别」）
        数据集拆分  训练 / 验证 / 测试 / 未划分（左侧「数据集拆分映射」）
        标记       带标记 / 无标记 / 各图像标记（左侧「图像标记」）
    勾选「全部」或一个都不选 = 不按该维度筛选。
    """

    filterChanged = Signal()
    textChanged = Signal(str)
    rulesRequested = Signal()         # 打开「自定义筛选规则」弹窗
    rulesCleared = Signal()           # 清除自定义规则
    statsRequested = Signal()         # 打开「标签统计」弹窗

    def __init__(self, parent=None):
        super().__init__(parent)
        self._annotated: list[str] = []   # 已标注 / 未标注（空 = 全部）
        self._classes: list[str] = []     # 类别勾选（"" = 无标签；空 = 全部）
        self._splits: list[str] = []      # train / val / test / none
        self._marks: list[str] = []       # tagged / untagged / 标记名
        self._class_options: list[str] = []
        self._tag_names: list[str] = []
        self._panel = None                # 当前展开的筛选面板（同时只留一个）

        # 容器化 + 可换行：宽度不足时整组折到下一行，而不是把按钮压窄、
        # 把「标签: 全部」这类文字截断成「签: 全」（商业软件不应出现该现象）。
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        container = FlowContainer(
            self, spacing=T.SPACE_MD,
            margins=(T.SPACE_LG, T.SPACE_MD, T.SPACE_LG, T.SPACE_MD),
        )
        outer.addWidget(container)
        flow = container.flow()

        self.label_btn = self._menu_button("标签", FluentIcon.TAG, "label")
        self.split_btn = self._menu_button("数据集拆分", FluentIcon.LIBRARY, "split")
        self.mark_btn = self._menu_button("标记", FluentIcon.FLAG, "mark")
        flow.addWidget(ToolGroup(
            self.label_btn, self.split_btn, self.mark_btn, parent=self,
        ))

        # 自定义筛选规则 + 标签统计（参照 DLT 的筛选规则 / 统计入口）
        self.rules_btn = PushButton("规则", self)
        self.rules_btn.setCheckable(True)
        self.rules_btn.setToolTip("按条件组合筛选（名称 / 尺寸 / 标注 / 标记…）")
        self.rules_btn.clicked.connect(lambda: self.rulesRequested.emit())

        self.rules_clear_btn = TransparentToolButton(self)
        self.rules_clear_btn.setIcon(FluentIcon.DELETE)
        self.rules_clear_btn.setToolTip("清除自定义筛选规则")
        self.rules_clear_btn.setFixedSize(T.ICON_BTN_SM, T.ICON_BTN_SM)
        self.rules_clear_btn.setVisible(False)
        self.rules_clear_btn.clicked.connect(lambda: self.rulesCleared.emit())

        self.stats_btn = PushButton("统计", self)
        self.stats_btn.setToolTip("按标签类别 / 数据集拆分 / 图像标记统计数量与占比")
        self.stats_btn.clicked.connect(lambda: self.statsRequested.emit())
        flow.addWidget(ToolGroup(
            self.rules_btn, self.rules_clear_btn, self.stats_btn, parent=self,
        ))

        self.search = SearchLineEdit(self)
        self.search.setPlaceholderText("输入筛选文本")
        self.search.setFixedWidth(T.CTRL_W_XL)
        self.search.textChanged.connect(lambda _t: self.textChanged.emit(self.text()))
        self.search.searchSignal.connect(lambda _t: self.textChanged.emit(self.text()))
        flow.addWidget(ToolGroup(self.search, parent=self))

        self.summary = CaptionLabel("", self)
        self.summary.setToolTip("当前显示 / 全部张数（含选中数）")
        flow.addWidget(ToolGroup(self.summary, parent=self))

        self._refresh_buttons()

    # -----------------------------------------------------------
    # 下拉菜单（多选）
    # -----------------------------------------------------------
    def _menu_button(self, title: str, icon, which: str) -> DropDownPushButton:
        button = DropDownPushButton(f"{title}：全部", self)
        button.setIcon(icon)
        button.setCheckable(True)          # 选中态表示「该筛选已生效」
        button.setMinimumWidth(120)
        button.setToolTip("点击展开筛选选项（可多选）")
        button.clicked.connect(lambda: self._open_menu(which))
        return button

    def _button(self, which: str) -> DropDownPushButton:
        return {
            "label": self.label_btn, "split": self.split_btn, "mark": self.mark_btn,
        }[which]

    def menu_items(self, which: str) -> list:
        """菜单项 [(key, text), ...]（key 为 "all" 的项表示不筛选）。"""
        if which == "label":
            items = [(_ALL_KEY, "全部")]
            items.extend(_ANNOTATED_STEPS)
            items.append((_UNLABELED_KEY, "无标签"))
            items.extend(
                (f"{_CLASS_PREFIX}{name}", name) for name in self._class_options
            )
            return items
        if which == "split":
            return [(_ALL_KEY, "全部")] + list(_SPLIT_STEPS)
        return [(_ALL_KEY, "全部")] + list(_MARK_STEPS) + [
            (name, name) for name in self._tag_names
        ]

    def selected_keys(self, which: str) -> list[str]:
        """当前勾选的键（不含「全部」）。"""
        if which == "label":
            keys = list(self._annotated)
            if "" in self._classes:
                keys.append(_UNLABELED_KEY)
            keys.extend(
                f"{_CLASS_PREFIX}{name}" for name in self._classes if name
            )
            return keys
        if which == "split":
            return list(self._splits)
        return list(self._marks)

    def _open_menu(self, which: str) -> None:
        panel = _FilterPanel(self)
        panel.set_items(self.menu_items(which), self.selected_keys(which))
        panel.changed.connect(lambda w=which, p=panel: self._apply(w, p.selected()))
        self._panel = panel
        button = self._button(which)
        Flyout.make(panel, target=button, parent=self)

    def _apply(self, which: str, keys: list) -> None:
        if which == "label":
            annotated, classes = [], []
            for key in keys:
                if key in dict(_ANNOTATED_STEPS):
                    annotated.append(key)
                elif key == _UNLABELED_KEY:
                    classes.append("")
                elif key.startswith(_CLASS_PREFIX):
                    classes.append(key[len(_CLASS_PREFIX):])
            self._annotated, self._classes = annotated, classes
        elif which == "split":
            self._splits = list(keys)
        else:
            self._marks = list(keys)
        self._refresh_buttons()
        self.filterChanged.emit()

    def _refresh_buttons(self) -> None:
        label_names = [dict(_ANNOTATED_STEPS)[key] for key in self._annotated]
        label_names += [
            "无标签" if name == "" else name for name in self._classes
        ]
        self.label_btn.setText(self._summary("标签", label_names))
        self.label_btn.setChecked(bool(label_names))

        split_names = [dict(_SPLIT_STEPS)[key] for key in self._splits]
        self.split_btn.setText(self._summary("数据集拆分", split_names))
        self.split_btn.setChecked(bool(split_names))

        mark_names = [dict(_MARK_STEPS).get(key, key) for key in self._marks]
        self.mark_btn.setText(self._summary("标记", mark_names))
        self.mark_btn.setChecked(bool(mark_names))

    @staticmethod
    def _summary(title: str, names: list) -> str:
        if not names:
            return f"{title}：全部"
        if len(names) == 1:
            return f"{title}：{names[0]}"
        return f"{title}：已选 {len(names)} 项"

    # -----------------------------------------------------------
    # 候选值（由左侧面板提供）
    # -----------------------------------------------------------
    def set_classes(self, names: list) -> None:
        """把左侧「标签类别」的类别并入标签菜单（失效的选择自动摘除）。

        页面刷新时调用；摘除结果会在本次刷新的过滤中立即生效，因此不再广播。
        """
        self._class_options = [str(name) for name in names]
        self._classes = [
            c for c in self._classes if c == "" or c in self._class_options
        ]
        self._refresh_buttons()

    def set_tag_names(self, names: list) -> None:
        """把左侧「图像标记」的标记并入标记菜单（失效的选择自动摘除）。"""
        self._tag_names = [str(name) for name in names]
        self._marks = [
            m for m in self._marks
            if m in dict(_MARK_STEPS) or m in self._tag_names
        ]
        self._refresh_buttons()

    # -----------------------------------------------------------
    # 取值（供页面过滤；返回列表，"all" 表示不筛选）
    # -----------------------------------------------------------
    def label_value(self):
        return list(self._annotated) or "all"

    def class_value(self):
        """类别筛选：列表（元素 "" 表示无标签）。"""
        return list(self._classes) or "all"

    def mark_value(self):
        return list(self._marks) or "all"

    def split_value(self):
        return list(self._splits) or "all"

    def text(self) -> str:
        return self.search.text().strip()

    def set_summary(self, shown: int, total: int, selected: int = 0) -> None:
        """更新计数：显示张数 / 总数（有选中时附选中数）。"""
        text = f"显示 {shown} / {total} 张"
        if selected:
            text += f" · 已选 {selected}"
        self.summary.setText(text)

    def set_rule_summary(self, count: int, description: str = "") -> None:
        """更新规则按钮状态（条数 + 悬停显示规则内容）。"""
        self.rules_btn.setText(f"规则 {count}" if count else "规则")
        self.rules_btn.setChecked(bool(count))
        self.rules_clear_btn.setVisible(bool(count))
        self.rules_btn.setToolTip(
            f"当前规则：{description}" if description else
            "按条件组合筛选（名称 / 尺寸 / 标注 / 标记…）"
        )
        self.rules_clear_btn.setToolTip(f"清除规则：{description}" if description else "清除自定义筛选规则")

    def reset(self) -> None:
        """复位到「全部」。"""
        self._annotated = []
        self._classes = []
        self._splits = []
        self._marks = []
        self.search.blockSignals(True)
        self.search.setText("")
        self.search.blockSignals(False)
        self._refresh_buttons()
        self.filterChanged.emit()


class SplitRow(QWidget):
    """拆分映射的一行：色块 + 名称 + 数量。"""

    clicked = Signal(str)

    def __init__(self, key: str, text: str, color: str, parent=None):
        super().__init__(parent)
        self.setObjectName("SplitRow")
        self.key = key
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(T.SPACE_MD, T.SPACE_XS, T.SPACE_MD, T.SPACE_XS)
        layout.setSpacing(T.SPACE_MD)

        swatch = QLabel(self)
        swatch.setFixedSize(_SWATCH_SIZE, _SWATCH_SIZE)
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
        layout.setContentsMargins(T.CARD_PAD_H, T.CARD_PAD_V, T.CARD_PAD_H, T.CARD_PAD_V)
        layout.setSpacing(T.SPACE_SM)
        layout.addWidget(StrongBodyLabel("数据集拆分映射", self))

        self.name_box = ComboBox(self)
        self.name_box.setToolTip("当前使用的拆分名称")
        layout.addWidget(self.name_box)

        # 锁定提示只用图标 + tooltip（界面不写说明文字，见 UI 规则 §10）
        lock = QWidget(self)
        lock_layout = QHBoxLayout(lock)
        lock_layout.setContentsMargins(0, 0, 0, 0)
        lock_layout.setSpacing(T.SPACE_SM)
        lock_icon = TransparentToolButton(lock)
        lock_icon.setIcon(FluentIcon.CONSTRACT)
        lock_icon.setFixedSize(T.ICON_BTN_SM, T.ICON_BTN_SM)
        lock_icon.setEnabled(False)
        lock_icon.setToolTip("该拆分已用于训练，划分不可更改")
        lock_layout.addWidget(lock_icon)
        lock_layout.addStretch(1)
        layout.addWidget(lock)
        self.lock_row = lock

        self.rows: dict[str, SplitRow] = {}
        for key, text, color in _SPLIT_ROWS:
            row = SplitRow(key, text, color, self)
            row.clicked.connect(self.splitRequested)
            layout.addWidget(row)
            self.rows[key] = row

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
        layout.setContentsMargins(T.CARD_PAD_H, T.CARD_PAD_V, T.CARD_PAD_H, T.CARD_PAD_V)
        layout.setSpacing(T.SPACE_SM)

        header = QHBoxLayout()
        header.setSpacing(T.SPACE_SM)
        header.addWidget(StrongBodyLabel("图像标记", self))
        header.addStretch(1)
        self.add_btn = TransparentToolButton(self)
        self.add_btn.setIcon(FluentIcon.ADD)
        self.add_btn.setToolTip("新增图像标记")
        self.add_btn.setFixedSize(T.ICON_BTN_MD, T.ICON_BTN_MD)
        self.add_btn.clicked.connect(self.addRequested)
        header.addWidget(self.add_btn)
        layout.addLayout(header)

        self.hint = CaptionLabel("", self)
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        holder = QWidget(self)
        self.grid = QGridLayout(holder)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(T.SPACE_XS)
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
            self.hint.setText("暂无标记")
            return
        self.hint.setText("")
        for index, name in enumerate(self._names):
            chip = PillPushButton(self)
            chip.setCheckable(False)
            chip.setText(f"{name}  {counts.get(name, 0)}")
            chip.setIcon(color_icon(self._colors.get(name, "")))
            chip.setToolTip(
                f"已挂 {counts.get(name, 0)} 张 · 点击追加/删除 · 右键编辑"
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



