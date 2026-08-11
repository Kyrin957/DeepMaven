"""项目管理导航页。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    SubtitleLabel,
    TextEdit,
)

from src.utils.constants import TASK_TYPES
from src.viewmodels.project_vm import ProjectViewModel
from src.views.base_page import BasePage


class ProjectTab(BasePage):
    """项目管理页：新建/打开项目、项目信息、最近项目。"""

    def __init__(self, vm: ProjectViewModel, parent=None):
        super().__init__(
            "项目管理",
            "创建、打开与管理深度学习项目，覆盖从数据到部署的完整工作流",
            parent,
        )
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

        # 项目名称
        layout.addWidget(BodyLabel("项目名称"))
        self.name_edit = LineEdit(card)
        self.name_edit.setPlaceholderText("例如：玻璃划痕检测")
        layout.addWidget(self.name_edit)

        # 工作目录
        layout.addWidget(BodyLabel("工作目录"))
        dir_row = QHBoxLayout()
        self.dir_edit = LineEdit(card)
        self.dir_edit.setPlaceholderText("选择项目保存位置")
        dir_row.addWidget(self.dir_edit, 1)
        dir_btn = PushButton("浏览", card)
        dir_btn.clicked.connect(self._browse_dir)
        dir_row.addWidget(dir_btn)
        layout.addLayout(dir_row)

        # 模型类型
        layout.addWidget(BodyLabel("模型类型"))
        self.model_type_combo = ComboBox(card)
        for item in TASK_TYPES:
            self.model_type_combo.addItem(item["label"], item["key"])
        layout.addWidget(self.model_type_combo)

        # 描述
        layout.addWidget(BodyLabel("项目描述"))
        self.desc_edit = TextEdit(card)
        self.desc_edit.setFixedHeight(80)
        layout.addWidget(self.desc_edit)

        self.create_btn = PrimaryPushButton("创建项目", card)
        layout.addWidget(self.create_btn)

    def _build_info_card(self) -> None:
        card, layout = self.add_card("当前项目")
        self.info_label = CaptionLabel("尚未打开任何项目", card)
        layout.addWidget(self.info_label)
        self.open_btn = PushButton("打开本地项目…", card)
        layout.addWidget(self.open_btn)

    def _build_recent_card(self) -> None:
        card, layout = self.add_card("最近项目")
        self.recent_label = CaptionLabel("暂无最近项目", card)
        layout.addWidget(self.recent_label)

    # -----------------------------------------------------------
    # 交互
    # -----------------------------------------------------------
    def _browse_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择工作目录")
        if directory:
            self.dir_edit.setText(directory)

    def _on_create(self) -> None:
        self._vm.create_project(
            name=self.name_edit.text().strip(),
            work_dir=self.dir_edit.text().strip(),
            model_type=self.model_type_combo.currentData(),
            description=self.desc_edit.toPlainText().strip(),
        )

    def _on_open_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "打开项目目录")
        if directory:
            self._vm.open_by_dir(directory)

    def _on_project_changed(self, project) -> None:
        if project is None:
            self.info_label.setText("尚未打开任何项目")
            return
        self.info_label.setText(
            f"名称：{project.name}\n工作目录：{project.work_dir}\n"
            f"模型类型：{project.model_type}\n创建时间：{project.created_at}"
        )

    def _on_recent_updated(self, projects: list) -> None:
        if not projects:
            self.recent_label.setText("暂无最近项目")
            return
        lines = [f"{p.name}  ·  目录：{p.work_dir}\n" for p in projects[:10]]
        self.recent_label.setText("".join(lines))

    # -----------------------------------------------------------
    # 绑定
    # -----------------------------------------------------------
    def _bind(self) -> None:
        self.create_btn.clicked.connect(self._on_create)
        self.open_btn.clicked.connect(self._on_open_dir)
        self._vm.projectChanged.connect(self._on_project_changed)
        self._vm.recentUpdated.connect(self._on_recent_updated)
        self._on_recent_updated(self._vm.recent_projects())