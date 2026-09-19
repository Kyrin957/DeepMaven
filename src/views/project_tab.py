"""项目管理导航页（参照 Halcon DLT 的项目管理）。

布局：
    左侧：新建 / 打开 / 保存 / 另存为，当前项目信息（可编辑），关闭项目
    右侧：最近的项目（封面卡片；双击打开，右键管理，按需自动刷新）
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    Action,
    BodyLabel,
    CaptionLabel,
    CardWidget,
    FluentIcon,
    LineEdit,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    RoundMenu,
    ScrollArea,
    StrongBodyLabel,
)

from src.services.project_service import ProjectService
from src.utils.constants import (
    APP_NAME,
    APP_VERSION,
    PROJECT_FILE_FILTER,
    project_type,
)
from src.viewmodels.project_vm import ProjectViewModel
from src.views.dialogs import NewProjectDialog

_COVER_W, _COVER_H = 184, 116
_COLUMNS = 4


class ProjectCard(QFrame):
    """最近项目卡片：封面缩略图 + 项目名。"""

    selected = Signal(str)
    activated = Signal(str)                    # 双击
    contextRequested = Signal(str, object)     # 右键：path, 全局坐标

    def __init__(
        self,
        path: str,
        name: str,
        cover: bytes = b"",
        missing: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("ProjectCard")
        self.path = path
        self._missing = missing
        self._selected = False
        self.setFixedSize(_COVER_W + 20, _COVER_H + 46)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 8)
        layout.setSpacing(5)

        self.cover = QLabel(self)
        self.cover.setFixedSize(_COVER_W, _COVER_H)
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover.setStyleSheet("background:#1B1B1B; border-radius:4px;")
        pixmap = QPixmap()
        if cover and pixmap.loadFromData(cover):
            self.cover.setPixmap(pixmap.scaled(
                _COVER_W, _COVER_H,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        else:
            self.cover.setText("无封面")
            self.cover.setStyleSheet(
                "background:#1B1B1B; border-radius:4px; color:#7A7A7A;"
            )
        layout.addWidget(self.cover)

        title = CaptionLabel(name, self)
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        title.setText(
            title.fontMetrics().elidedText(
                name, Qt.TextElideMode.ElideMiddle, _COVER_W
            )
        )
        layout.addWidget(title)

        tip = f"{name}\n{path}"
        self.setToolTip(tip + ("\n（项目文件不存在）" if missing else ""))
        self._restyle()

    def _restyle(self) -> None:
        if self._selected:
            border, background = "#0F6CBD", "rgba(15, 108, 189, 0.18)"
        elif self._missing:
            border, background = "#C42B1C", "rgba(196, 43, 28, 0.12)"
        else:
            border, background = "#333333", "rgba(255, 255, 255, 0.04)"
        self.setStyleSheet(
            f"#ProjectCard {{ border: 1px solid {border}; border-radius: 6px;"
            f" background: {background}; }}"
        )

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        self._restyle()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.MouseButton.RightButton:
            self.contextRequested.emit(
                self.path, event.globalPosition().toPoint()
            )
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self.path)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.path)


class ProjectTab(QWidget):
    """项目管理页。"""

    requestImportDataset = Signal(str)      # 新建项目时带了数据集目录

    def __init__(
        self,
        project_vm: ProjectViewModel,
        dataset_vm=None,
        parent=None,
    ):
        super().__init__(parent)
        self._vm = project_vm
        self._dataset_vm = dataset_vm
        self._cards: list[ProjectCard] = []
        self._shown_path = ""       # 信息面板当前展示的项目文件路径
        self._build_ui()
        self._bind()
        self._reload_recent()
        self._show_project(self._vm.project, current=True)
        self._update_actions()

    # -----------------------------------------------------------
    # UI
    # -----------------------------------------------------------
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(12)
        root.addWidget(self._build_side())
        root.addLayout(self._build_recent_area(), 1)

    def _build_side(self) -> QWidget:
        container = QWidget(self)
        container.setFixedWidth(320)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        action_card = CardWidget(container)
        action_layout = QVBoxLayout(action_card)
        action_layout.setContentsMargins(16, 14, 16, 14)
        action_layout.setSpacing(8)
        self.new_btn = PrimaryPushButton(action_card)
        self.new_btn.setText("新建项目")
        self.new_btn.setIcon(FluentIcon.ADD)
        self.open_btn = PushButton(action_card)
        self.open_btn.setText("打开项目")
        self.open_btn.setIcon(FluentIcon.FOLDER)
        self.save_btn = PushButton(action_card)
        self.save_btn.setText("保存项目")
        self.save_btn.setIcon(FluentIcon.SAVE)
        self.save_as_btn = PushButton(action_card)
        self.save_as_btn.setText("项目另存为")
        self.save_as_btn.setIcon(FluentIcon.SAVE_AS)
        for button in (self.new_btn, self.open_btn,
                       self.save_btn, self.save_as_btn):
            action_layout.addWidget(button)
        layout.addWidget(action_card)

        info_card = CardWidget(container)
        info_layout = QVBoxLayout(info_card)
        info_layout.setContentsMargins(16, 14, 16, 14)
        info_layout.setSpacing(6)
        self.info_title = StrongBodyLabel("项目信息", info_card)
        info_layout.addWidget(self.info_title)

        info_layout.addWidget(CaptionLabel("项目名称", info_card))
        self.name_edit = LineEdit(info_card)
        info_layout.addWidget(self.name_edit)

        info_layout.addWidget(CaptionLabel("项目说明", info_card))
        self.desc_edit = QPlainTextEdit(info_card)
        self.desc_edit.setFixedHeight(64)
        self.desc_edit.setPlaceholderText("输入对项目的说明")
        info_layout.addWidget(self.desc_edit)

        self._values: dict[str, BodyLabel] = {}
        for key, label in (
            ("type", "项目类型"),
            ("file", "项目文件"),
            ("source", "图像库路径"),
            ("classes", "标签类别"),
            ("marked", "已标记图像"),
            ("created", "创建 / 修改"),
            ("version", "程序 / 文件版本"),
            ("log", "日志路径"),
        ):
            info_layout.addWidget(CaptionLabel(label, info_card))
            value = BodyLabel("—", info_card)
            value.setWordWrap(True)
            info_layout.addWidget(value)
            self._values[key] = value

        scroll = ScrollArea(container)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(info_card)
        layout.addWidget(scroll, 1)

        self.close_btn = PushButton(container)
        self.close_btn.setText("关闭项目")
        self.close_btn.setIcon(FluentIcon.CLOSE)
        layout.addWidget(self.close_btn)
        return container

    def _build_recent_area(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(10)

        header = CardWidget(self)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 10, 16, 10)
        header_layout.setSpacing(10)
        header_layout.addWidget(StrongBodyLabel("最近的项目", header))
        header_layout.addStretch(1)
        self.hint = CaptionLabel("", header)
        header_layout.addWidget(self.hint)
        column.addWidget(header)

        self.scroll = ScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.grid = QGridLayout(self.body)
        self.grid.setContentsMargins(4, 4, 4, 4)
        self.grid.setSpacing(10)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.scroll.setWidget(self.body)
        column.addWidget(self.scroll, 1)
        return column

    # -----------------------------------------------------------
    # 绑定
    # -----------------------------------------------------------
    def _bind(self) -> None:
        self.new_btn.clicked.connect(self._on_new)
        self.open_btn.clicked.connect(self._on_open)
        self.save_btn.clicked.connect(self.save_project)
        self.save_as_btn.clicked.connect(self.save_project_as)
        self.close_btn.clicked.connect(self.close_project)
        self._vm.projectChanged.connect(self._on_project_changed)
        # 最近项目按需自动刷新（打开程序、新建、打开、保存、另存为、删除等）
        self._vm.recentUpdated.connect(lambda _items: self._reload_recent())

    # -----------------------------------------------------------
    # 最近项目
    # -----------------------------------------------------------
    def _reload_recent(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._cards = []

        # 项目文件已被移动 / 删除的记录不再展示
        recent = [
            entry for entry in self._vm.recent_projects()
            if Path(str(entry.get("path", ""))).is_file()
        ][:16]
        if not recent:
            self.hint.setText("暂无最近项目记录")
            return

        current = self._vm.current_path
        for index, entry in enumerate(recent):
            path = str(entry.get("path", ""))
            name = str(entry.get("name") or Path(path).stem)
            project, cover = ProjectService.peek_project(path)
            if project is not None and project.name:
                name = project.name
            card = ProjectCard(path, name, cover, parent=self.body)
            card.selected.connect(self._on_card_selected)
            card.activated.connect(self._on_card_activated)
            card.contextRequested.connect(self._on_card_menu)
            if path == current:
                card.set_selected(True)
            self.grid.addWidget(card, index // _COLUMNS, index % _COLUMNS)
            self._cards.append(card)
        self.hint.setText("")

    def _on_card_selected(self, path: str) -> None:
        for card in self._cards:
            card.set_selected(card.path == path)
        project, _cover = ProjectService.peek_project(path)
        if project is None:
            self._show_project(None, current=False)
            return
        self._show_project(project, current=(path == self._vm.current_path))

    def _on_card_activated(self, path: str) -> None:
        if not Path(path).is_file():
            self._vm.message.emit("error", f"项目文件不存在：{path}")
            self._reload_recent()
            return
        self._vm.open_project(path)

    def _on_card_menu(self, path: str, position) -> None:
        menu = RoundMenu(parent=self)
        menu.addAction(Action("打开项目", triggered=lambda: self._on_card_activated(path)))
        menu.addAction(Action("打开项目所在文件夹", triggered=lambda: self._open_folder(path)))
        menu.addSeparator()
        menu.addAction(Action("从最近项目记录中移除", triggered=lambda: self._on_remove_recent(path)))
        menu.addAction(Action("删除项目文件", triggered=lambda: self._on_delete_project(path)))
        menu.exec(position)

    def _open_folder(self, path: str) -> None:
        directory = Path(path).parent
        if not directory.is_dir():
            self._vm.message.emit("warning", "项目所在目录不存在")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def _on_remove_recent(self, path: str) -> None:
        self._vm.delete_recent(path)
        self._reload_recent()

    def _on_delete_project(self, path: str) -> None:
        name = Path(path).stem
        box = MessageBox(
            "删除项目文件",
            f"确定要删除项目「{name}」的文件吗？\n{path}\n\n"
            "不可撤销；数据集与模型文件不会被删除。",
            self,
        )
        box.yesButton.setText("删除")
        box.cancelButton.setText("取消")
        if not box.exec():
            return
        self._vm.delete_project(path)
        self._reload_recent()

    # -----------------------------------------------------------
    # 项目操作
    # -----------------------------------------------------------
    def _on_new(self) -> None:
        default_dir = self._vm.project_dir() or str(Path.home() / "Documents")
        dialog = NewProjectDialog(self.window(), default_dir=default_dir)
        if not dialog.exec():
            return
        data = dialog.result_data()
        project = self._vm.create_project(
            path=data["path"],
            name=data["name"],
            model_type=data["model_type"],
            description=data["description"],
        )
        if project is None:
            return
        dataset_dir = data.get("dataset_dir") or ""
        if dataset_dir:
            self.requestImportDataset.emit(dataset_dir)
        self._reload_recent()
        self._show_project(project, current=True)

    def _on_open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "打开项目文件", self._vm.project_dir(), PROJECT_FILE_FILTER
        )
        if path:
            project = self._vm.open_project(path)
            if project is not None:
                self._reload_recent()

    def save_project(self) -> None:
        """保存当前项目（同时把名称 / 说明输入框的内容写回项目）。"""
        if not self._vm.has_project():
            self._vm.message.emit("warning", "当前没有打开的项目")
            return
        self._apply_edits()
        self._vm.save_project()

    def save_project_as(self) -> None:
        """项目另存为：写到新的 .mprj 并把当前项目切换过去。"""
        if not self._vm.has_project():
            self._vm.message.emit("warning", "当前没有打开的项目")
            return
        self._apply_edits()
        path, _ = QFileDialog.getSaveFileName(
            self, "项目另存为", self._vm.current_path or "新项目.mprj",
            PROJECT_FILE_FILTER,
        )
        if not path:
            return
        if Path(path).resolve() == Path(self._vm.current_path).resolve():
            self._vm.message.emit("warning", "目标与原项目相同，请换一个文件名")
            return
        self._vm.save_project_as(path)

    def close_project(self) -> None:
        """关闭当前项目（项目文件保留在磁盘上）。"""
        if not self._vm.has_project():
            self._vm.message.emit("warning", "当前没有打开的项目")
            return
        project = self._vm.project
        box = MessageBox(
            "关闭项目",
            f"确定要关闭项目「{project.name}」吗？\n\n"
            "项目文件不会被删除。",
            self,
        )
        box.yesButton.setText("关闭")
        box.cancelButton.setText("取消")
        if box.exec():
            self._vm.close_project()

    def _apply_edits(self) -> None:
        """把名称 / 说明输入框的内容写回项目对象（仅在展示当前项目时）。"""
        project = self._vm.project
        if project is None or self._shown_path != self._vm.current_path:
            return
        name = self.name_edit.text().strip()
        description = self.desc_edit.toPlainText()
        changed = False
        if name and name != project.name:
            project.name = name
            changed = True
        if description != (project.description or ""):
            project.description = description
            changed = True
        if changed:
            project.touch()

    def _update_actions(self) -> None:
        """按「是否已打开项目」控制按钮的启用状态。"""
        opened = self._vm.has_project()
        for button in (self.save_btn, self.save_as_btn, self.close_btn):
            button.setEnabled(opened)

    # -----------------------------------------------------------
    # 信息展示
    # -----------------------------------------------------------
    def _on_project_changed(self, project) -> None:
        self._show_project(project, current=True)
        self._update_selection()
        self._update_actions()

    def _update_selection(self) -> None:
        """同步最近项目卡片的选中态。"""
        current = self._vm.current_path
        for card in self._cards:
            card.set_selected(card.path == current)

    def _show_project(self, project, current: bool) -> None:
        self._shown_path = (
            str(project.params.get("path", "") or "") if project is not None else ""
        )
        if project is None:
            self.info_title.setText("项目信息")
            self._values["type"].setText("—")
            self._values["file"].setText("—")
            self._values["source"].setText("—")
            self._values["classes"].setText("—")
            self._values["marked"].setText("—")
            self._values["created"].setText("—")
            self._values["version"].setText("—")
            self._values["log"].setText("—")
            self.name_edit.setText("")
            self.desc_edit.setPlainText("")
            return

        item = project_type(project.model_type)
        self.info_title.setText("当前项目" if current else "选中的项目（未打开）")
        self.name_edit.setText(project.name)
        self.desc_edit.setPlainText(project.description or "")
        self._values["type"].setText(
            f"{item['label']}（{item['short']}）"
            + ("" if item["supported"] else " · 规划中")
        )
        self._values["file"].setText(str(project.params.get("path", "") or "—"))
        self._values["source"].setText(project.dataset.source_label or "—")

        names = project.class_names
        distributions = project.dataset.class_counts or {}
        if names:
            text = "、".join(
                f"{name}" + (f"（{distributions[name]}）" if name in distributions else "")
                for name in names
            )
        elif distributions:
            text = "、".join(f"{k}（{v}）" for k, v in distributions.items())
        else:
            text = "—"
        self._values["classes"].setText(text)

        total = project.dataset.image_count
        if current and self._dataset_vm is not None and self._dataset_vm.dataset is not None:
            total = len(self._dataset_vm.images())
            marked = self._dataset_vm.annotated_count()
        else:
            marked = total
        self._values["marked"].setText(f"{marked} / {total}" if total else "—")
        self._values["created"].setText(
            f"{project.created_at} / {project.updated_at}"
        )
        self._values["version"].setText(
            f"{APP_NAME} {APP_VERSION} / 项目格式 v{project.app_version}"
        )
        from src.utils.constants import DATA_DIR

        self._values["log"].setText(str(DATA_DIR / "logs" / "deepmaven.log"))

    def refresh_current(self) -> None:
        """外部数据变化后刷新当前项目信息（如完成划分、标注）。"""
        if self._vm.project is not None:
            self._show_project(self._vm.project, current=True)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        self.refresh_current()
        self._reload_recent()       # 回到本页时按需刷新（替代手动刷新按钮）
        self._update_actions()
