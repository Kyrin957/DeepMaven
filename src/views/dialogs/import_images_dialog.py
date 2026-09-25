"""图像导入弹窗（参照 Halcon DLT 的「导入并标注图像」）。

导入时即可确定：
    * 图像来源（文件夹 / 多选图片文件）
    * **文件夹层级 → 类别名**：按「左侧（顶层）N 级 / 右侧（底层）N 级」组合
      所选文件夹的相对路径生成类别名，可设反向顺序与定界符；
      每个含图文件夹可双击改名，**留空即「无标签」**
    * **类别类型**（异常检测项目）：每个类别可设为「良好 / 异常」
    * 底部二选一：**导入并标注图像** / **导入图像时不进行标注**

用法：
    dialog = ImportImagesDialog(parent, mode="folder", initial=[folder])
    if dialog.exec():
        data = dialog.result_data()      # 见 result_data() 的返回结构
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QPushButton,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CaptionLabel,
    CheckBox,
    LineEdit,
    MessageBoxBase,
    PushButton,
    SubtitleLabel,
    TreeWidget,
)

from src.services.dataset_service import DatasetService
from src.views.ui import SafeSpinBox
from src.views.ui import tokens as T
from src.utils.constants import IMAGE_EXTS

_IMAGE_FILTER = "图片 (*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp)"
_UNLABELED_TEXT = "无标签"
_DEFAULT_SEPARATOR = "_"
_MAX_LEVEL = 8

# 树列
_COL_FOLDER, _COL_LABEL, _COL_KIND = 0, 1, 2
_FOLDER_ROLE = Qt.ItemDataRole.UserRole
_TREE_HEIGHT = 216
_COL_KIND_W = 184
# 「类别类型」行高：树项的文本区比行高上下各小 6px，所以行高必须给足
# 「按钮高 + 2×留白」＝ 36px。给 30px 时文本区只有 18px，24px 的切换按钮
# 会被挤到裁切，看起来"偏下"（按钮下沿被切、文字baseline 也随之偏）。
_KIND_BTN_H = 24
_ITEM_TEXT_PAD_V = 6
_ROW_HEIGHT = _KIND_BTN_H + _ITEM_TEXT_PAD_V * 2

# 类别类型（异常检测项目）
KIND_NORMAL, KIND_ABNORMAL = "normal", "abnormal"
_KIND_ITEMS = ((KIND_NORMAL, "良好"), (KIND_ABNORMAL, "异常"))


class _KindSelector(QWidget):
    """「良好 / 异常」二选一切换（异常检测项目的类别类型）。"""

    changed = Signal(str)

    _QSS = (
        "QPushButton { background: transparent; color: #9A9A9A;"
        " border: 1px solid #5A5A5A; border-radius: 3px; padding: 1px 12px; }"
        "QPushButton:hover { border: 1px solid #8A8A8A; }"
        "QPushButton:checked { background: #005FB8; color: #FFFFFF;"
        " border: 1px solid #005FB8; }"
    )

    def __init__(self, kind: str = KIND_NORMAL, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(T.SPACE_SM)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for key, text in _KIND_ITEMS:
            button = QPushButton(text, self)
            button.setCheckable(True)
            button.setFixedHeight(_KIND_BTN_H)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(self._QSS)
            button.clicked.connect(lambda _checked=False, k=key: self._on_click(k))
            self._group.addButton(button)
            layout.addWidget(button)
            self._buttons[key] = button

        self._kind = kind if kind in self._buttons else KIND_NORMAL
        self._buttons[self._kind].setChecked(True)
        layout.addStretch(1)

    def kind(self) -> str:
        return self._kind

    def set_kind(self, kind: str) -> None:
        """静默切换当前类型（不发 changed，用于跟随类别名变化）。"""
        key = kind if kind in self._buttons else KIND_NORMAL
        if key == self._kind:
            return
        self._kind = key
        self._buttons[key].setChecked(True)

    def _on_click(self, key: str) -> None:
        """点击即记录该类型（点当前项视为确认）。"""
        self._kind = key
        self._buttons[key].setChecked(True)
        self.changed.emit(key)


class ImportImagesDialog(MessageBoxBase):
    """导入图像弹窗。

    Args:
        mode: "folder"（导入文件夹）/ "files"（导入选中的图片文件）。
        initial: 初始选择（文件夹路径或图片文件路径列表）。
        is_anomaly: 项目是否为异常检测（决定是否显示「类别类型」列）。
        existing_kinds: 已有类别的类型 {类别名: "normal" / "abnormal"}，
            作为「类别类型」的默认值（用户不动就不改写）。
    """

    def __init__(
        self,
        parent=None,
        mode: str = "folder",
        initial=None,
        is_anomaly: bool = False,
        existing_kinds=None,
    ):
        super().__init__(parent)
        self._mode = "files" if str(mode) == "files" else "folder"
        self._is_anomaly = bool(is_anomaly)
        self._existing_kinds = {
            str(name): str(kind)
            for name, kind in (existing_kinds or {}).items()
            if str(kind) in (KIND_NORMAL, KIND_ABNORMAL)
        }
        self._selection: list[Path] = [Path(p) for p in (initial or [])]
        self._images: list[Path] = []
        # 直接含图的文件夹 → 图片列表
        self._folder_images: dict[str, list[Path]] = {}
        # 文件夹路径 → 树节点
        self._nodes: dict[str, QTreeWidgetItem] = {}
        # 手工改过的类别名（文件夹路径 → 文本）
        self._labels: dict[str, str] = {}
        # 类别类型（文件夹路径 → normal / abnormal）
        self._kinds: dict[str, str] = {}
        # 类别类型控件（文件夹路径 → 控件）
        self._selectors: dict[str, "_KindSelector"] = {}
        self._annotate = True
        self.tree: TreeWidget | None = None

        self.titleLabel = SubtitleLabel(
            "导入图像文件" if self._mode == "files" else "导入图像文件夹", self
        )
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self._build_body())

        # 只用于校验提示（没有说明文字）
        self.hint = CaptionLabel("", self)
        self.hint.setWordWrap(True)
        self.viewLayout.addWidget(self.hint)

        self.widget.setMinimumWidth(760)
        self.cancelButton.setText("取消")
        self.yesButton.setText("导入" if self._mode == "files" else "导入并标注图像")

        # 「不标注」按钮与确认按钮并排
        self.skip_btn = PushButton(self.buttonGroup)
        self.skip_btn.setText("导入图像时不进行标注")
        index = self.buttonLayout.indexOf(self.yesButton)
        if index >= 0:
            self.buttonLayout.insertWidget(index, self.skip_btn)
        else:
            self.buttonLayout.addWidget(self.skip_btn)
        self.skip_btn.clicked.connect(lambda: self._on_confirm(False))

        # 接管确认按钮：先校验再关闭
        try:
            self.yesButton.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.yesButton.clicked.connect(lambda: self._on_confirm(True))
        if self._mode == "files":
            self.skip_btn.hide()

        if self._selection:
            self._sync_path_field()
        self._reload_tree()

    # -----------------------------------------------------------
    # UI
    # -----------------------------------------------------------
    def _build_body(self) -> QWidget:
        holder = QWidget(self)
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(T.SPACE_MD)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(T.SPACE_LG)
        form.setVerticalSpacing(T.SPACE_MD)

        # 图像来源
        self.path_edit = LineEdit(holder)
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText("尚未选择图像来源")
        self.browse_btn = PushButton("浏览…", holder)
        self.browse_btn.clicked.connect(self._browse)
        source_row = QWidget(holder)
        source_layout = QHBoxLayout(source_row)
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_layout.setSpacing(T.SPACE_MD)
        source_layout.addWidget(self.path_edit, 1)
        source_layout.addWidget(self.browse_btn)
        form.addRow(CaptionLabel("图像来源", holder), source_row)
        layout.addLayout(form)

        if self._mode == "folder":
            layout.addLayout(self._build_levels(holder))
            self.tree = self._build_tree(holder)
            layout.addWidget(self.tree)

        self.dedupe_check = CheckBox("跳过重复图像", holder)
        self.dedupe_check.setToolTip("按内容 SHA256 去重")
        self.dedupe_check.setChecked(True)
        self.dedupe_check.stateChanged.connect(lambda _s: self._update_preview())
        layout.addWidget(self.dedupe_check)

        self.preview = CaptionLabel("", holder)
        self.preview.setWordWrap(True)
        layout.addWidget(self.preview)
        return holder

    def _build_levels(self, parent) -> QGridLayout:
        """顶部「文件夹层级 → 类别名」设置。"""
        grid = QGridLayout()
        grid.setHorizontalSpacing(T.SPACE_LG)
        grid.setVerticalSpacing(T.SPACE_MD)

        self.left_check = CheckBox("左侧文件夹级别", parent)
        self.left_spin = SafeSpinBox(parent)
        self.left_spin.setRange(1, _MAX_LEVEL)
        self.left_spin.setValue(1)
        self.left_spin.setFixedWidth(
            T.field_width(self.left_spin, T.STEPPER_CHARS_NARROW)
        )
        self.left_spin.setEnabled(False)
        self.left_check.toggled.connect(self._on_levels_changed)
        self.left_spin.valueChanged.connect(lambda _v: self._refresh_labels())

        self.right_check = CheckBox("右侧文件夹级别", parent)
        self.right_spin = SafeSpinBox(parent)
        self.right_spin.setRange(1, _MAX_LEVEL)
        self.right_spin.setValue(1)
        self.right_spin.setFixedWidth(
            T.field_width(self.right_spin, T.STEPPER_CHARS_NARROW)
        )
        self.right_check.setChecked(True)
        self.right_check.toggled.connect(self._on_levels_changed)
        self.right_spin.valueChanged.connect(lambda _v: self._refresh_labels())

        self.reverse_check = CheckBox("按相反顺序构建类别名称", parent)
        self.reverse_check.toggled.connect(lambda _c: self._refresh_labels())

        self.sep_edit = LineEdit(parent)
        self.sep_edit.setText(_DEFAULT_SEPARATOR)
        self.sep_edit.setMaxLength(3)
        self.sep_edit.setFixedWidth(T.CTRL_W_SM)
        self.sep_edit.textChanged.connect(lambda _t: self._refresh_labels())

        grid.addWidget(self.left_check, 0, 0)
        grid.addWidget(self.left_spin, 0, 1, Qt.AlignmentFlag.AlignLeft)
        grid.addWidget(self.right_check, 1, 0)
        grid.addWidget(self.right_spin, 1, 1, Qt.AlignmentFlag.AlignLeft)
        grid.addWidget(self.reverse_check, 2, 0)

        separator_row = QHBoxLayout()
        separator_row.setContentsMargins(0, 0, 0, 0)
        separator_row.setSpacing(T.SPACE_MD)
        separator_row.addWidget(CaptionLabel("类别名称定界符", parent))
        separator_row.addWidget(self.sep_edit)
        separator_row.addStretch(1)
        grid.addLayout(separator_row, 2, 1)
        grid.setColumnStretch(2, 1)
        return grid

    def _build_tree(self, parent) -> TreeWidget:
        tree = TreeWidget(parent)
        if self._is_anomaly:
            tree.setColumnCount(3)
            tree.setHeaderLabels(["文件夹结构", "类别名称", "类别类型"])
            tree.setColumnWidth(_COL_KIND, _COL_KIND_W)
        else:
            tree.setColumnCount(2)
            tree.setHeaderLabels(["文件夹结构", "类别名称"])
        tree.setMinimumHeight(_TREE_HEIGHT)
        tree.setColumnWidth(_COL_FOLDER, 288)
        tree.setColumnWidth(_COL_LABEL, 232)
        tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        tree.itemChanged.connect(self._on_item_changed)
        tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        return tree

    # -----------------------------------------------------------
    # 来源选择
    # -----------------------------------------------------------
    def _initial_dir(self) -> str:
        for path in self._selection:
            if path.is_dir():
                return str(path)
            if path.parent.is_dir():
                return str(path.parent)
        return ""

    def _browse(self) -> None:
        if self._mode == "folder":
            directory = QFileDialog.getExistingDirectory(
                self, "选择图片文件夹", self._initial_dir()
            )
            if directory:
                self._selection = [Path(directory)]
        else:
            paths, _ = QFileDialog.getOpenFileNames(
                self, "选择图片", self._initial_dir(), _IMAGE_FILTER
            )
            if paths:
                self._selection = [Path(p) for p in paths]
        self._sync_path_field()
        self._reload_tree()

    def _sync_path_field(self) -> None:
        """把当前选择显示到只读输入框（文件模式只展示摘要）。"""
        if not self._selection:
            self.path_edit.clear()
            return
        if len(self._selection) == 1:
            self.path_edit.setText(str(self._selection[0]))
            return
        self.path_edit.setText(
            f"{self._selection[0].parent}（共 {len(self._selection)} 个文件）"
        )

    # -----------------------------------------------------------
    # 文件夹树
    # -----------------------------------------------------------
    def _root_path(self) -> Path | None:
        if self._mode != "folder" or not self._selection:
            return None
        root = self._selection[0]
        return root if root.is_dir() else None

    def _reload_tree(self) -> None:
        """按当前来源重建「文件夹结构 → 类别名」树。"""
        self._folder_images = {}
        self._nodes = {}
        self._selectors = {}
        self._images = []
        root = self._root_path()
        if root is None:
            # 文件模式（或来源无效）：图片即所选文件
            self._images = [
                path for path in self._selection
                if path.is_file() and path.suffix.lower() in IMAGE_EXTS
            ]
        else:
            self._images = DatasetService.scan_images(root)
            for image in self._images:
                self._folder_images.setdefault(str(image.parent), []).append(image)
            if self.tree is not None:
                self._build_nodes(root, self._subtree_counts(root))
        self._update_preview()

    def _subtree_counts(self, root: Path) -> dict[str, int]:
        """每个目录（含子目录）的图片总数（含图目录才建节点）。"""
        counts: dict[str, int] = {}
        for image in self._images:
            folder = image.parent
            while True:
                key = str(folder)
                counts[key] = counts.get(key, 0) + 1
                if folder == root or folder.parent == folder:
                    break
                folder = folder.parent
        return counts

    def _build_nodes(self, root: Path, counts: dict) -> None:
        """按路径深度建树（只显示含图的目录，空目录不展示）。"""
        self.tree.blockSignals(True)
        self.tree.clear()
        self._nodes = {}
        for key in sorted(counts, key=lambda k: (len(Path(k).parts), k.lower())):
            path = Path(key)
            parent = self._nodes.get(str(path.parent))
            item = QTreeWidgetItem(parent if parent is not None else self.tree)
            item.setText(
                _COL_FOLDER, f"{path.name or str(path)} ({counts[key]})"
            )
            item.setToolTip(_COL_FOLDER, key)
            item.setData(_COL_FOLDER, _FOLDER_ROLE, key)
            item.setFlags(
                item.flags()
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
            )
            item.setCheckState(_COL_FOLDER, Qt.CheckState.Checked)
            if self._is_anomaly:
                # 所有行统一按「能放下切换按钮」的高度给尺寸提示，
                # 否则含子目录的行矮一截，一列按钮上下参差
                item.setSizeHint(_COL_KIND, QSize(_COL_KIND_W, _ROW_HEIGHT))
            if key in self._folder_images:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                item.setText(_COL_LABEL, self._label_of(key))
                item.setToolTip(_COL_LABEL, "双击改名，留空即无标签")
                if self._is_anomaly:
                    selector = _KindSelector(self._default_kind(key))
                    selector.changed.connect(
                        lambda kind, k=key: self._on_kind_changed(k, kind)
                    )
                    self._selectors[key] = selector
                    self.tree.setItemWidget(item, _COL_KIND, selector)
            self._nodes[key] = item
        self.tree.expandAll()
        self.tree.blockSignals(False)

    # -----------------------------------------------------------
    # 类别名（文件夹层级 → 名称）
    # -----------------------------------------------------------
    def _chain(self, folder: str) -> list[str]:
        """文件夹 → 命名链：所选文件夹名 + 其下的各级子目录名。"""
        root = self._root_path()
        if root is None:
            return []
        name = root.name or str(root)
        try:
            relative = Path(folder).relative_to(root)
        except ValueError:
            return [name]
        return [name] + [part for part in relative.parts if part not in ("", ".")]

    def _levels(self) -> tuple[int, int]:
        """当前生效的（左侧级别, 右侧级别）。"""
        left = int(self.left_spin.value()) if self.left_check.isChecked() else 0
        right = int(self.right_spin.value()) if self.right_check.isChecked() else 0
        return left, right

    def _computed_label(self, folder: str) -> str:
        """按层级设置生成类别名。

        左侧级别从命名链顶部取、右侧级别从底部取；两侧都没勾选时按最底层
        文件夹命名；勾选「按相反顺序构建」则把取到的部分整体倒过来。
        """
        chain = self._chain(folder)
        if not chain:
            return ""
        left, right = self._levels()
        if left + right <= 0:
            right = 1
        if left + right >= len(chain):
            parts = list(chain)
        else:
            parts = chain[:left] + chain[len(chain) - right:]
        if self.reverse_check.isChecked():
            parts.reverse()
        return self.sep_edit.text().join(parts)

    def _label_of(self, folder: str) -> str:
        """文件夹当前类别名（手工改名优先，否则按层级规则计算）。"""
        if folder in self._labels:
            return self._labels[folder]
        return self._computed_label(folder)

    def _on_levels_changed(self) -> None:
        self.left_spin.setEnabled(self.left_check.isChecked())
        self.right_spin.setEnabled(self.right_check.isChecked())
        self._refresh_labels()

    def _refresh_labels(self) -> None:
        """层级 / 定界符变化后重算未改名的类别名。"""
        if self.tree is None:
            self._update_preview()
            return
        self.tree.blockSignals(True)
        for folder, item in self._nodes.items():
            if folder in self._labels or folder not in self._folder_images:
                continue
            item.setText(_COL_LABEL, self._computed_label(folder))
            self._apply_default_kind(folder)
        self.tree.blockSignals(False)
        self._update_preview()

    def _default_kind(self, folder: str) -> str:
        """「类别类型」的初始值：本次改过 > 同名类别已有类型 > 良好。"""
        if folder in self._kinds:
            return self._kinds[folder]
        name = self._label_of(folder).strip()
        return self._existing_kinds.get(name, KIND_NORMAL)

    def _apply_default_kind(self, folder: str) -> None:
        """类别名变化后，未手工改过类型的行跟随新的默认类型。"""
        selector = self._selectors.get(folder)
        if selector is not None and folder not in self._kinds:
            selector.set_kind(self._default_kind(folder))

    def _on_kind_changed(self, folder: str, kind: str) -> None:
        self._kinds[folder] = kind

    # -----------------------------------------------------------
    # 选择与预览
    # -----------------------------------------------------------
    def _selected_images(self) -> list[Path]:
        """当前勾选范围内要导入的图片。"""
        if self._mode != "folder":
            return list(self._images)
        images: list[Path] = []
        for folder, items in self._folder_images.items():
            node = self._nodes.get(folder)
            if node is not None and node.checkState(_COL_FOLDER) != Qt.CheckState.Unchecked:
                images.extend(items)
        return images

    def _selected_folders(self) -> list[str]:
        return [
            folder for folder in self._folder_images
            if (node := self._nodes.get(folder)) is not None
            and node.checkState(_COL_FOLDER) != Qt.CheckState.Unchecked
        ]

    def _update_preview(self) -> None:
        selected = self._selected_images()
        enable = bool(selected)
        self.yesButton.setEnabled(enable)
        skip_btn = getattr(self, "skip_btn", None)
        if skip_btn is not None:
            skip_btn.setEnabled(enable)
        if not selected:
            self.preview.setText("尚未选择图像")
            return

        parts = [f"共 {len(selected)} 张图像"]
        if self._mode == "folder":
            dist: dict[str, int] = {}
            for image in selected:
                name = self._label_of(str(image.parent)).strip() or _UNLABELED_TEXT
                dist[name] = dist.get(name, 0) + 1
            if dist:
                detail = "、".join(
                    f"{name} {count}" for name, count in sorted(dist.items())
                )
                parts.append(f"类别：{detail}")
        if self.dedupe_check.isChecked():
            parts.append("跳过重复")
        self.preview.setText("；".join(parts))

    # -----------------------------------------------------------
    # 勾选联动
    # -----------------------------------------------------------
    def _on_item_changed(self, item, column: int) -> None:
        if column == _COL_FOLDER and self.tree is not None:
            state = item.checkState(_COL_FOLDER)
            self.tree.blockSignals(True)
            self._set_descendants(item, state)
            self._refresh_ancestors(item)
            self.tree.blockSignals(False)
        elif column == _COL_LABEL:
            folder = str(item.data(_COL_FOLDER, _FOLDER_ROLE) or "")
            if folder:
                self._labels[folder] = item.text(_COL_LABEL)
                self._apply_default_kind(folder)
        self._update_preview()

    @staticmethod
    def _set_descendants(item, state) -> None:
        for index in range(item.childCount()):
            child = item.child(index)
            child.setCheckState(_COL_FOLDER, state)
            ImportImagesDialog._set_descendants(child, state)

    @staticmethod
    def _refresh_ancestors(item) -> None:
        parent = item.parent()
        while parent is not None:
            states = [
                parent.child(index).checkState(_COL_FOLDER)
                for index in range(parent.childCount())
            ]
            if all(state == Qt.CheckState.Checked for state in states):
                parent.setCheckState(_COL_FOLDER, Qt.CheckState.Checked)
            elif all(state == Qt.CheckState.Unchecked for state in states):
                parent.setCheckState(_COL_FOLDER, Qt.CheckState.Unchecked)
            else:
                parent.setCheckState(_COL_FOLDER, Qt.CheckState.PartiallyChecked)
            parent = parent.parent()

    def _on_item_double_clicked(self, item, column: int) -> None:
        """只有含图文件夹的「类别名称」列可以编辑。"""
        folder = str(item.data(_COL_FOLDER, _FOLDER_ROLE) or "")
        if column == _COL_LABEL and folder in self._folder_images:
            self.tree.editItem(item, _COL_LABEL)

    # -----------------------------------------------------------
    # 结果
    # -----------------------------------------------------------
    def _on_confirm(self, annotate: bool) -> None:
        if not self._selected_images():
            self.hint.setText("请先选择图像")
            return
        self._annotate = bool(annotate)
        self.accept()

    def result_data(self) -> dict:
        """返回导入选项；文件夹模式附带图片清单、类别映射与类别类型。"""
        data = {
            "mode": self._mode,
            "paths": [str(path) for path in self._selection],
            "dedupe": self.dedupe_check.isChecked(),
            "annotate": bool(self._annotate),
        }
        if self._mode == "folder":
            selected = self._selected_folders()
            data["files"] = [str(path) for path in self._selected_images()]
            data["label_map"] = {
                folder: self._label_of(folder) for folder in selected
            }
            kinds: dict[str, str] = {}
            for folder in selected:
                name = self._label_of(folder).strip()
                if name and folder in self._kinds:
                    kinds[name] = self._kinds[folder]
            if kinds:
                data["class_kinds"] = kinds
        return data
