"""图像导入弹窗（参照 Halcon DLT 的「打开图像」）。

导入时即可确定：
    * 图像来源（文件夹 / 多选图片文件）
    * 读取顺序（文件名 / 修改时间）与**反向顺序**
    * 新图片插入到已有图库的**右侧（最后）/ 左侧（最前）**
    * **子文件夹 → 标签**：树形展开所选文件夹的目录结构，勾选要导入的部分；
      每个含图的文件夹自动对应一个标签（默认取文件夹名，可双击改名），
      **标签留空的文件夹下图片导入后即「无标签」**

用法：
    dialog = ImportImagesDialog(parent, mode="folder", initial=[folder])
    if dialog.exec():
        data = dialog.result_data()      # 见 result_data() 的返回结构
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CaptionLabel,
    CheckBox,
    ComboBox,
    LineEdit,
    MessageBoxBase,
    PushButton,
    RadioButton,
    StrongBodyLabel,
    SubtitleLabel,
    TreeWidget,
)

from src.services.dataset_service import DatasetService
from src.utils.constants import IMAGE_EXTS

_IMAGE_FILTER = "图片 (*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp)"
_SORT_ITEMS = (("按文件名", "name"), ("按修改时间", "time"))
_UNLABELED_TEXT = "无标签"

# 树列
_COL_NAME, _COL_COUNT, _COL_LABEL = 0, 1, 2
_FOLDER_ROLE = Qt.ItemDataRole.UserRole
_TREE_HEIGHT = 196


class ImportImagesDialog(MessageBoxBase):
    """导入图像选项弹窗。

    Args:
        mode: "folder"（导入文件夹）/ "files"（导入选中的图片文件）。
        initial: 初始选择（文件夹路径或图片文件路径列表）。
        has_dataset: 图库中是否已有图片（决定「插入位置」是否可用）。
        classes: 保留参数（当前不再用于标注候选）。
        existing_count: 已有图片数量（保留参数）。
    """

    def __init__(
        self,
        parent=None,
        mode: str = "folder",
        initial=None,
        has_dataset: bool = False,
        classes=(),
        existing_count: int = 0,
    ):
        super().__init__(parent)
        self._mode = "files" if str(mode) == "files" else "folder"
        self._selection: list[Path] = [Path(p) for p in (initial or [])]
        self._images: list[Path] = []
        # 直接含图的文件夹 → 图片列表
        self._folder_images: dict[str, list[Path]] = {}
        # 文件夹路径 → 树节点
        self._nodes: dict[str, QTreeWidgetItem] = {}
        # 手工改过的标签（文件夹路径 → 文本），换目录后仍保留
        self._labels: dict[str, str] = {}
        self._has_dataset = bool(has_dataset)

        self.titleLabel = SubtitleLabel(
            "导入图像文件" if self._mode == "files" else "导入图像文件夹", self
        )
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self._build_body())

        # 只用于校验提示（没有说明文字）
        self.hint = CaptionLabel("", self)
        self.hint.setWordWrap(True)
        self.viewLayout.addWidget(self.hint)

        self.widget.setMinimumWidth(660)
        self.yesButton.setText("导入")
        self.cancelButton.setText("取消")
        # 接管确认按钮：先校验再关闭
        try:
            self.yesButton.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.yesButton.clicked.connect(self._on_confirm)

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
        layout.setSpacing(8)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        # 图像来源
        self.path_edit = LineEdit(holder)
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText("尚未选择图像来源")
        self.browse_btn = PushButton("浏览…", holder)
        self.browse_btn.clicked.connect(self._browse)
        source_row = QWidget(holder)
        source_layout = QHBoxLayout(source_row)
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_layout.setSpacing(8)
        source_layout.addWidget(self.path_edit, 1)
        source_layout.addWidget(self.browse_btn)
        form.addRow(CaptionLabel("图像来源", holder), source_row)

        # 读取顺序
        self.sort_box = ComboBox(holder)
        for text, key in _SORT_ITEMS:
            self.sort_box.addItem(text, userData=key)
        self.sort_box.setCurrentIndex(0)
        self.reverse_check = CheckBox("反向顺序", holder)
        self.reverse_check.setToolTip("勾选后把排序结果整体倒过来")
        order_row = QWidget(holder)
        order_layout = QHBoxLayout(order_row)
        order_layout.setContentsMargins(0, 0, 0, 0)
        order_layout.setSpacing(12)
        order_layout.addWidget(self.sort_box, 1)
        order_layout.addWidget(self.reverse_check)
        order_layout.addStretch(1)
        form.addRow(CaptionLabel("读取顺序", holder), order_row)

        # 插入位置
        self.right_radio = RadioButton("右侧（最后）", holder)
        self.left_radio = RadioButton("左侧（最前）", holder)
        self.right_radio.setChecked(True)
        position_row = QWidget(holder)
        position_layout = QHBoxLayout(position_row)
        position_layout.setContentsMargins(0, 0, 0, 0)
        position_layout.setSpacing(16)
        position_layout.addWidget(self.right_radio)
        position_layout.addWidget(self.left_radio)
        position_layout.addStretch(1)
        position_row.setEnabled(self._has_dataset)
        self.position_row = position_row
        form.addRow(CaptionLabel("插入位置", holder), position_row)

        layout.addLayout(form)

        self.dedupe_check = CheckBox("跳过重复图像", holder)
        self.dedupe_check.setToolTip("按内容 SHA256 去重")
        self.dedupe_check.setChecked(True)
        layout.addWidget(self.dedupe_check)

        self.tree = None
        if self._mode == "folder":
            layout.addWidget(StrongBodyLabel("子文件夹与标签", holder))
            self.tree = self._build_tree(holder)
            layout.addWidget(self.tree)

        self.preview = CaptionLabel("", holder)
        self.preview.setWordWrap(True)
        layout.addWidget(self.preview)

        # 选项变化即时刷新预览
        self.sort_box.currentIndexChanged.connect(lambda _i: self._update_preview())
        self.reverse_check.stateChanged.connect(lambda _s: self._update_preview())
        self.right_radio.toggled.connect(lambda _c: self._update_preview())
        self.dedupe_check.stateChanged.connect(lambda _s: self._update_preview())
        return holder

    def _build_tree(self, parent) -> TreeWidget:
        tree = TreeWidget(parent)
        tree.setColumnCount(3)
        tree.setHeaderLabels(["子文件夹", "图片", "标签"])
        tree.setMinimumHeight(_TREE_HEIGHT)
        tree.setUniformRowHeights(True)
        tree.setColumnWidth(_COL_NAME, 300)
        tree.setColumnWidth(_COL_COUNT, 64)
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
        """按当前来源重建「子文件夹 → 标签」树。"""
        self._folder_images = {}
        self._nodes = {}
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
        for key in sorted(counts, key=lambda k: (len(Path(k).parts), k.lower())):
            path = Path(key)
            parent = self._nodes.get(str(path.parent))
            item = QTreeWidgetItem(parent if parent is not None else self.tree)
            is_root = path == root
            item.setText(
                _COL_NAME,
                f"{root.name or str(root)}（所选文件夹）" if is_root else path.name,
            )
            item.setToolTip(_COL_NAME, key)
            item.setText(_COL_COUNT, str(counts[key]))
            item.setData(_COL_NAME, _FOLDER_ROLE, key)
            item.setFlags(
                item.flags()
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
            )
            item.setCheckState(_COL_NAME, Qt.CheckState.Checked)
            if key in self._folder_images:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                item.setText(_COL_LABEL, self._label_of(key))
                item.setToolTip(_COL_LABEL, "双击改名，留空即无标签")
            self._nodes[key] = item
        self.tree.expandAll()
        self.tree.blockSignals(False)

    # -----------------------------------------------------------
    # 标签
    # -----------------------------------------------------------
    def _label_of(self, folder: str) -> str:
        """文件夹当前标签（用户改过就用改过的，否则按默认规则推导）。

        默认规则：
            * 子文件夹 → 取文件夹名
            * 所选文件夹自身 → 它本身就是类别目录（其下没有含图的子目录）时取
              文件夹名；否则它只是个容器，自身图片默认「无标签」
            * 用户把标签清空 → 该文件夹下图片即「无标签」
        """
        if folder in self._labels:
            return self._labels[folder]
        root = self._root_path()
        if root is not None and Path(folder) == root:
            has_child_images = any(
                Path(key) != root for key in self._folder_images
            )
            return "" if has_child_images else (root.name or "")
        return Path(folder).name

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
            if node is not None and node.checkState(_COL_NAME) != Qt.CheckState.Unchecked:
                images.extend(items)
        return images

    def _selected_folders(self) -> list[str]:
        return [
            folder for folder in self._folder_images
            if (node := self._nodes.get(folder)) is not None
            and node.checkState(_COL_NAME) != Qt.CheckState.Unchecked
        ]

    def _update_preview(self) -> None:
        selected = self._selected_images()
        if not selected:
            self.preview.setText("尚未选择图像")
            self.yesButton.setEnabled(False)
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
                parts.append(f"标签：{detail}")
        if self._has_dataset:
            where = "左侧" if self.left_radio.isChecked() else "右侧"
            parts.append(f"插入{where}")
        if self.dedupe_check.isChecked():
            parts.append("跳过重复")

        parts.append(f"首张 {selected[0].name}")
        self.preview.setText("；".join(parts))
        self.yesButton.setEnabled(True)

    # -----------------------------------------------------------
    # 勾选联动
    # -----------------------------------------------------------
    def _on_item_changed(self, item, column: int) -> None:
        if column == _COL_NAME and self.tree is not None:
            state = item.checkState(_COL_NAME)
            self.tree.blockSignals(True)
            self._set_descendants(item, state)
            self._refresh_ancestors(item)
            self.tree.blockSignals(False)
        elif column == _COL_LABEL:
            folder = str(item.data(_COL_NAME, _FOLDER_ROLE) or "")
            if folder:
                self._labels[folder] = item.text(_COL_LABEL)
        self._update_preview()

    @staticmethod
    def _set_descendants(item, state) -> None:
        for index in range(item.childCount()):
            child = item.child(index)
            child.setCheckState(_COL_NAME, state)
            ImportImagesDialog._set_descendants(child, state)

    @staticmethod
    def _refresh_ancestors(item) -> None:
        parent = item.parent()
        while parent is not None:
            states = [
                parent.child(index).checkState(_COL_NAME)
                for index in range(parent.childCount())
            ]
            if all(state == Qt.CheckState.Checked for state in states):
                parent.setCheckState(_COL_NAME, Qt.CheckState.Checked)
            elif all(state == Qt.CheckState.Unchecked for state in states):
                parent.setCheckState(_COL_NAME, Qt.CheckState.Unchecked)
            else:
                parent.setCheckState(_COL_NAME, Qt.CheckState.PartiallyChecked)
            parent = parent.parent()

    def _on_item_double_clicked(self, item, column: int) -> None:
        """只有含图文件夹的「标签」列可以编辑。"""
        folder = str(item.data(_COL_NAME, _FOLDER_ROLE) or "")
        if column == _COL_LABEL and folder in self._folder_images:
            self.tree.editItem(item, _COL_LABEL)

    # -----------------------------------------------------------
    # 结果
    # -----------------------------------------------------------
    def _on_confirm(self) -> None:
        if not self._selected_images():
            self.hint.setText("请先选择图片")
            return
        self.accept()

    def result_data(self) -> dict:
        """返回导入选项；文件夹模式附带图片清单与「文件夹 → 标签」映射。"""
        data = {
            "mode": self._mode,
            "paths": [str(path) for path in self._selection],
            "sort": str(self.sort_box.currentData() or "name"),
            "reverse": self.reverse_check.isChecked(),
            "position": "left" if self.left_radio.isChecked() else "right",
            "dedupe": self.dedupe_check.isChecked(),
        }
        if self._mode == "folder":
            selected = set(self._selected_folders())
            data["files"] = [str(path) for path in self._selected_images()]
            data["label_map"] = {
                folder: self._labels.get(folder, self._label_of(folder))
                for folder in self._folder_images
                if folder in selected
            }
        return data
