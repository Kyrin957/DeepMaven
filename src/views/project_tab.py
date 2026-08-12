"""项目管理导航页。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    HyperlinkButton,
    LineEdit,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
)

from src.utils.constants import PROJECT_FILE_FILTER, TASK_TYPES
from src.viewmodels.project_vm import ProjectViewModel
from src.views.base_page import BasePage


class ProjectTab(BasePage):
    """项目管理页：新建/打开 .mprj 项目、项目信息、最近项目。"""

    def __init__(self, vm: ProjectViewModel, parent=None):
        super().__init__(parent)
        self._vm = vm
        self._build_ui()
        self._bind()

    # -----------------------------------------------------------
    # UI
    # -----------------------------------------------------------
    def _build_ui(self) -> None:
        self._build_new_project_card()
        self._build_info_card()
        self._build_recent_card()
        self.add_spacer()

    def _build_new_project_card(self) -> None:
        card, layout = self.add_card("新建项目")

        # 项目文件（.mprj）——文件名即项目名称
        layout.addWidget(BodyLabel("项目文件 (.mprj)，文件名即项目名称"))
        dir_row = QHBoxLayout()
        self.path_edit = LineEdit(card)
        self.path_edit.setPlaceholderText("例如：D:/projects/玻璃划痕检测.mprj")
        dir_row.addWidget(self.path_edit, 1)
        path_btn = PushButton("浏览…", card)
        path_btn.clicked.connect(self._browse_path)
        dir_row.addWidget(path_btn)
        layout.addLayout(dir_row)

        # 模型类型
        layout.addWidget(BodyLabel("模型类型"))
        self.model_type_combo = ComboBox(card)
        for item in TASK_TYPES:
            self.model_type_combo.addItem(item["label"], item["key"])
        layout.addWidget(self.model_type_combo)

        self.create_btn = PrimaryPushButton("创建项目", card)
        layout.addWidget(self.create_btn)

    def _build_info_card(self) -> None:
        card, layout = self.add_card("当前项目")
        self.info_label = CaptionLabel("尚未打开任何项目", card)
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)
        self.open_btn = PushButton("打开项目文件…", card)
        layout.addWidget(self.open_btn)

    def _build_recent_card(self) -> None:
        card, layout = self.add_card("最近项目")
        # 容器：动态填充可点击的最近项目链接
        self.recent_box = QVBoxLayout()
        self.recent_box.setSpacing(8)
        layout.addLayout(self.recent_box)
        self.recent_label = CaptionLabel("暂无最近项目", card)
        self.recent_label.setWordWrap(True)
        self.recent_box.addWidget(self.recent_label)

    # -----------------------------------------------------------
    # 交互
    # -----------------------------------------------------------
    def _browse_path(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "选择项目文件", "未命名项目", PROJECT_FILE_FILTER
        )
        if path:
            self.path_edit.setText(path)

    def _on_create(self) -> None:
        path = self.path_edit.text().strip()
        self._vm.create_project(
            path=path,
            name=Path(path).stem,
            model_type=self.model_type_combo.currentData(),
            description="",
        )

    def _on_open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "打开项目文件", "", PROJECT_FILE_FILTER
        )
        if path:
            self._vm.open_project(path)

    def _on_project_changed(self, project) -> None:
        if project is None:
            self.info_label.setText("尚未打开任何项目")
            return
        self.info_label.setText(
            f"名称：{project.name}\n"
            f"项目文件：{project.params.get('path', '')}\n"
            f"模型类型：{project.model_type}\n"
            f"创建时间：{project.created_at}\n"
            f"内嵌文件：{project.file_count} 个"
        )

    def _on_recent_updated(self, projects: list) -> None:
        # 清空容器，重新填充
        while self.recent_box.count():
            item = self.recent_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if not projects:
            self.recent_label = CaptionLabel("暂无最近项目", self)
            self.recent_label.setWordWrap(True)
            self.recent_box.addWidget(self.recent_label)
            return

        for p in projects[:10]:
            name = p.get("name") or Path(p.get("path", "")).stem
            path = p.get("path", "")
            btn = HyperlinkButton(self)
            btn.setText(name)
            btn.setToolTip(path)
            btn.clicked.connect(
                lambda checked=False, n_=name, p_=path: self._on_recent_clicked(n_, p_)
            )
            self.recent_box.addWidget(btn)

    def _on_recent_clicked(self, name: str, path: str) -> None:
        """点击最近项目：确认保存当前项目，再确认是否打开该项目。"""
        current = self._vm.project
        if current is not None and current.dirty:
            box = MessageBox(
                "保存当前项目",
                f"打开新项目前，是否保存当前项目「{current.name}」的未保存内容？",
                self,
            )
            if box.exec():
                self._vm.save_project()

        confirm = MessageBox(
            "打开项目",
            f"是否打开最近项目「{name}」？\n{path}",
            self,
        )
        if confirm.exec():
            self._vm.open_project(path)

    # -----------------------------------------------------------
    # 绑定
    # -----------------------------------------------------------
    def _bind(self) -> None:
        self.create_btn.clicked.connect(self._on_create)
        self.open_btn.clicked.connect(self._on_open_file)
        self._vm.projectChanged.connect(self._on_project_changed)
        self._vm.recentUpdated.connect(self._on_recent_updated)
        try:
            self._on_recent_updated(self._vm.recent_projects())
        except OSError:
            pass