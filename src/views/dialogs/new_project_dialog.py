"""创建新项目对话框（参照 Halcon DLT 的「创建新项目」）。

结构：
    顶部  从现有数据集创建（可选）
    中部  深度学习方法网格 + 右侧说明面板
    底部  项目名称 / 项目文件路径 / 项目说明 / 保存图像路径 / 取消·创建
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    FluentIcon,
    LineEdit,
    MessageBoxBase,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    SubtitleLabel,
)

from src.utils.constants import PROJECT_FILE_FILTER, PROJECT_TYPES, project_type

_COLUMNS = 3
_UNSUPPORTED_COLOR = "#6A6A6A"


class MethodCard(QFrame):
    """深度学习方法的可选卡片。"""

    clicked = Signal(str)

    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("MethodCard")
        self.key = str(item["key"])
        self.supported = bool(item.get("supported", True))
        self.setFixedHeight(76)
        self.setCursor(
            Qt.CursorShape.PointingHandCursor if self.supported
            else Qt.CursorShape.ForbiddenCursor
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)
        title = StrongBodyLabel(item["label"], self)
        caption = CaptionLabel(item["short"], self)
        caption.setWordWrap(True)
        if not self.supported:
            title.setStyleSheet(f"color: {_UNSUPPORTED_COLOR};")
            caption.setText(f"{item['short']}（规划中）")
        layout.addWidget(title)
        layout.addWidget(caption)
        self._selected = False
        self._restyle()

    def _restyle(self) -> None:
        if self._selected:
            border, background = "#0F6CBD", "rgba(15, 108, 189, 0.20)"
        elif self.supported:
            border, background = "#3A3A3A", "rgba(255, 255, 255, 0.05)"
        else:
            border, background = "#2A2A2A", "rgba(255, 255, 255, 0.02)"
        self.setStyleSheet(
            f"#MethodCard {{ border: 1px solid {border};"
            f" border-radius: 6px; background: {background}; }}"
        )

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        self._restyle()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if self.supported:
            self.clicked.emit(self.key)
        else:
            self.clicked.emit("")


class NewProjectDialog(MessageBoxBase):
    """创建新项目对话框。

    用法：
        dialog = NewProjectDialog(parent)
        if dialog.exec():
            data = dialog.result_data()
    """

    def __init__(self, parent=None, default_dir: str = ""):
        super().__init__(parent)
        self._cards: dict[str, MethodCard] = {}
        self._current = "detect"
        self._dataset_dir = ""

        self.titleLabel = SubtitleLabel("创建新项目", self)
        self.viewLayout.addWidget(self.titleLabel)

        self.viewLayout.addWidget(self._build_dataset_row())
        self.viewLayout.addWidget(self._build_methods())
        self.viewLayout.addWidget(self._build_form(default_dir))

        self.yesButton.setText("创建项目")
        self.cancelButton.setText("取消")
        # 接管确认按钮：先做校验，通过后再真正关闭对话框
        try:
            self.yesButton.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.yesButton.clicked.connect(self._on_accept)
        self.widget.setMinimumSize(940, 660)

        self._select("detect")

    # -----------------------------------------------------------
    # 从现有数据集创建
    # -----------------------------------------------------------
    def _build_dataset_row(self) -> QWidget:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(CaptionLabel("从现有数据集创建项目（可选）：", container))

        row = QHBoxLayout()
        row.setSpacing(10)
        self.dataset_btn = PushButton("选择数据集目录", container)
        self.dataset_btn.setIcon(FluentIcon.FOLDER)
        self.dataset_btn.clicked.connect(self._on_pick_dataset)
        row.addWidget(self.dataset_btn)
        self.dataset_label = CaptionLabel(
            "不导入：项目创建后在「图库」页导入图片或拖入文件夹即可。", container
        )
        self.dataset_label.setWordWrap(True)
        row.addWidget(self.dataset_label, 1)
        layout.addLayout(row)
        return container

    def _on_pick_dataset(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择数据集目录")
        if not directory:
            return
        self._dataset_dir = directory
        counts = self._scan_summary(directory)
        self.dataset_label.setText(f"{directory}　（{counts}）")

    @staticmethod
    def _scan_summary(directory: str) -> str:
        from src.services.dataset_service import DatasetService

        images = DatasetService.scan_images(directory)
        folders = DatasetService.child_class_dirs(directory)
        if folders:
            return f"{len(images)} 张图片 · {len(folders)} 个类别目录"
        return f"{len(images)} 张图片"

    # -----------------------------------------------------------
    # 方法网格 + 说明
    # -----------------------------------------------------------
    def _build_methods(self) -> QWidget:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(CaptionLabel("深度学习方法：", container))

        row = QHBoxLayout()
        row.setSpacing(12)

        grid_holder = QWidget(container)
        grid = QGridLayout(grid_holder)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)
        for index, item in enumerate(PROJECT_TYPES):
            card = MethodCard(item, grid_holder)
            card.clicked.connect(self._on_card_clicked)
            grid.addWidget(card, index // _COLUMNS, index % _COLUMNS)
            self._cards[item["key"]] = card
        row.addWidget(grid_holder, 1)

        panel = QFrame(container)
        panel.setObjectName("MethodPanel")
        panel.setFixedWidth(300)
        panel.setStyleSheet(
            "#MethodPanel { border: 1px solid #333333; border-radius: 6px;"
            " background: rgba(255, 255, 255, 0.03); }"
        )
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(16, 14, 16, 14)
        panel_layout.setSpacing(8)
        self.detail_title = StrongBodyLabel("对象检测", panel)
        self.detail_text = BodyLabel("", panel)
        self.detail_text.setWordWrap(True)
        self.detail_note = CaptionLabel("", panel)
        self.detail_note.setWordWrap(True)
        panel_layout.addWidget(self.detail_title)
        panel_layout.addWidget(self.detail_text)
        panel_layout.addStretch(1)
        panel_layout.addWidget(self.detail_note)
        row.addWidget(panel)

        layout.addLayout(row)
        return container

    def _on_card_clicked(self, key: str) -> None:
        self._select(key or "")

    def _select(self, key: str) -> None:
        """选中某个方法；不支持的类型只展示说明，不改变当前选择。"""
        item = project_type(key) if key else None
        if item is None or not item.get("supported", True):
            if item is not None:
                self._show_detail(item, selectable=False)
            else:
                self.detail_title.setText("暂不支持")
                self.detail_text.setText("")
                self.detail_note.setText("该深度学习方法是规划中的能力，当前暂不可用。")
            return
        self._current = key
        for card_key, card in self._cards.items():
            card.set_selected(card_key == key)
        self._show_detail(item, selectable=True)

    def _show_detail(self, item: dict, selectable: bool) -> None:
        self.detail_title.setText(item["label"])
        self.detail_text.setText(item["detail"])
        if selectable:
            self.detail_note.setText(
                f"标注方式：{_annotation_text(item['annotation'])}\n"
                f"训练模型：YOLO11{item['model_suffix'] or ''}（可换更大变体）\n\n"
                f"{item['note']}"
            )
        else:
            self.detail_note.setText(f"{item['note']}\n\n该类型当前不可选。")

    # -----------------------------------------------------------
    # 表单
    # -----------------------------------------------------------
    def _build_form(self, default_dir: str) -> QWidget:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(CaptionLabel("项目名称", container))
        self.name_edit = LineEdit(container)
        self.name_edit.setText("新项目")
        self.name_edit.textChanged.connect(self._on_name_changed)
        row.addWidget(self.name_edit, 1)

        row.addWidget(CaptionLabel("项目文件路径", container))
        self.path_edit = LineEdit(container)
        if default_dir:
            self.path_edit.setText(str(Path(default_dir) / "新项目.mprj"))
        row.addWidget(self.path_edit, 2)
        browse = PushButton("浏览", container)
        browse.clicked.connect(self._on_browse_path)
        row.addWidget(browse)
        layout.addLayout(row)

        layout.addWidget(CaptionLabel("项目说明", container))
        self.desc_edit = QPlainTextEdit(container)
        self.desc_edit.setPlaceholderText("输入对项目的说明")
        self.desc_edit.setFixedHeight(72)
        layout.addWidget(self.desc_edit)

        bottom = QHBoxLayout()
        self.save_source_check = CheckBox("保存对应于项目的图像库路径", container)
        self.save_source_check.setChecked(True)
        bottom.addWidget(self.save_source_check)
        bottom.addStretch(1)

        self.hint = CaptionLabel("", container)
        self.hint.setWordWrap(True)
        bottom.addWidget(self.hint, 2)
        layout.addLayout(bottom)
        return container

    def _on_name_changed(self, text: str) -> None:
        path = self.path_edit.text().strip()
        if not path:
            return
        self.path_edit.setText(str(Path(path).with_name(f"{text.strip() or '新项目'}.mprj")))

    def _on_browse_path(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "选择项目文件位置",
            self.path_edit.text().strip() or "新项目", PROJECT_FILE_FILTER,
        )
        if path:
            self.path_edit.setText(path)

    # -----------------------------------------------------------
    # 提交
    # -----------------------------------------------------------
    def _on_accept(self) -> None:
        name = self.name_edit.text().strip() or "新项目"
        path = self.path_edit.text().strip()
        if not path:
            self.hint.setText("请填写项目文件路径")
            return
        if Path(path).suffix.lower() != ".mprj":
            path = f"{path}.mprj"
            self.path_edit.setText(path)
        if Path(path).exists():
            self.hint.setText("该路径已存在项目文件，请换一个路径")
            return
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.hint.setText(f"目录不可用：{exc}")
            return
        if self._dataset_dir and not self.save_source_check.isChecked():
            self._dataset_dir = ""
        self.accept()

    def result_data(self) -> dict:
        """返回创建项目所需的数据。"""
        return {
            "name": self.name_edit.text().strip() or "新项目",
            "path": self.path_edit.text().strip(),
            "model_type": self._current,
            "description": self.desc_edit.toPlainText().strip(),
            "dataset_dir": self._dataset_dir,
        }


def _annotation_text(mode: str) -> str:
    return {
        "none": "无需框选（分类 / 异常检测按整图判定）",
        "box": "轴对齐矩形框",
        "obb": "矩形框 + 角度微调",
        "polygon": "多边形轮廓",
    }.get(mode, mode)
