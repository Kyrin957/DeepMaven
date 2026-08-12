"""主窗口：直接继承 QFluentWidgets 的 FluentWindow（无边框，自带标题栏与左侧导航），
并在其内部重构根布局，加入顶部菜单栏与底部状态栏。

布局结构（纵向）：
    Fluent 标题栏（无边框窗口自带：应用标题 + 窗口控制按钮 + 拖动区）
    菜单栏（QMenuBar）
    内容区（横向：左侧导航栏 + 右侧导航页窗口）
    状态栏（底部）
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenuBar,
    QVBoxLayout,
)
from qfluentwidgets import (
    FluentIcon,
    FluentWindow,
    InfoBar,
    InfoBarPosition,
    NavigationItemPosition,
    setTheme,
    setThemeColor,
    Theme,
)

from src.utils.constants import APP_NAME, APP_VERSION, BRAND_COLOR, PROJECT_FILE_FILTER
from src.utils.logger import get_logger
from src.viewmodels import (
    DatasetViewModel,
    EvaluateViewModel,
    ExportViewModel,
    ModelViewModel,
    ProjectViewModel,
    TrainViewModel,
)
from src.views import (
    DataTab,
    EvaluateTab,
    ExportTab,
    ModelTab,
    ProjectTab,
    TrainTab,
)

logger = get_logger("main_window")


class MainWindow(FluentWindow):
    """DeepMaven 主窗口（无边框 Fluent 窗口）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} — 深度学习缺陷检测系统")
        self.resize(1280, 800)
        self.setMinimumSize(1080, 680)
        # 应用品牌色
        setThemeColor(BRAND_COLOR)

        # ViewModel 层（业务逻辑）
        self.project_vm = ProjectViewModel(self)
        self.dataset_vm = DatasetViewModel(self)
        self.model_vm = ModelViewModel(self)
        self.train_vm = TrainViewModel(self)
        self.evaluate_vm = EvaluateViewModel(self)
        self.export_vm = ExportViewModel(self)

        # 隐藏导航栏顶部的「返回」后退按钮
        self.navigationInterface.setReturnButtonVisible(False)
        # 把导航栏的「展开/收起」按钮放进导航栏内（避开顶部标题栏区域）
        self._tuck_expand_button_into_nav()

        # 顶部菜单栏 + 底部状态栏
        self.menu_bar = self._create_menu_bar()
        self.status_bar_area = self._create_status_bar()

        # 重构根布局：把 FluentWindow 默认的横向 hBoxLayout
        # （左侧导航 + 右侧页面）作为中间内容，外包纵向布局。
        self._rebuild_layout()

        # 注册导航页
        self._init_navigation()

        # 统一消息提示
        self._wire_messages()

        # 状态栏就绪提示
        self._set_status(f"就绪  ·  {APP_NAME} v{APP_VERSION}")

    # -----------------------------------------------------------
    # 导航栏「展开/收起」按钮定位
    # -----------------------------------------------------------
    def _tuck_expand_button_into_nav(self) -> None:
        """把导航面板顶部的展开按钮（menuButton）下移到标题栏下方。

        默认情况下该按钮位于导航面板顶部、与 Fluent 标题栏重叠；
        这里增大导航面板 vBoxLayout 的顶部边距（避开约 48px 的标题栏），
        使展开按钮落在导航栏内部。
        """
        panel = self.navigationInterface.panel
        margins = panel.vBoxLayout.contentsMargins()
        panel.vBoxLayout.setContentsMargins(
            margins.left(), 48, margins.right(), margins.bottom(),
        )

    # -----------------------------------------------------------
    # 布局重构：在右侧 widgetLayout 内构建纵向 [菜单栏, 页面, 状态栏]
    # 说明：FluentWindow 的根布局 hBoxLayout 为横向 [导航栏, widgetLayout]，
    #       且为无边框窗口所持有、不可整体替换。因此把页面从 widgetLayout
    #       移入新建的纵向布局 vLayout，菜单栏与其上下排布。
    #       widgetLayout 原有的 48px 上边距用于避开浮动标题栏，予以保留，
    #       使菜单栏恰好位于标题栏下方。
    # -----------------------------------------------------------
    def _rebuild_layout(self) -> None:
        self.vLayout = QVBoxLayout()
        self.vLayout.setContentsMargins(0, 0, 0, 0)
        self.vLayout.setSpacing(0)

        # 从 widgetLayout 取出页面，重构为纵向排布
        self.widgetLayout.removeWidget(self.stackedWidget)
        self.vLayout.addWidget(self.menu_bar)
        self.vLayout.addWidget(self.stackedWidget, 1)
        self.vLayout.addWidget(self.status_bar_area)

        self.widgetLayout.addLayout(self.vLayout)
        self.widgetLayout.setStretchFactor(self.vLayout, 1)

    # -----------------------------------------------------------
    # 顶部菜单栏
    # -----------------------------------------------------------
    def _create_menu_bar(self) -> QMenuBar:
        menu_bar = QMenuBar(self)
        menu_bar.setNativeMenuBar(False)

        file_menu = menu_bar.addMenu("文件(&F)")
        new_action = QAction("新建项目", self)
        open_action = QAction("打开项目", self)
        save_action = QAction("保存", self)
        exit_action = QAction("退出", self)
        file_menu.addAction(new_action)
        file_menu.addAction(open_action)
        file_menu.addSeparator()
        file_menu.addAction(save_action)
        file_menu.addSeparator()
        file_menu.addAction(exit_action)
        new_action.triggered.connect(self._nav_to_project)
        open_action.triggered.connect(self._open_project_dialog)
        save_action.triggered.connect(lambda: self.project_vm.save_project())
        exit_action.triggered.connect(self.close)

        view_menu = menu_bar.addMenu("视图(&V)")
        theme_submenu = view_menu.addMenu("切换主题")
        light_action = QAction("亮色", self)
        dark_action = QAction("暗色", self)
        auto_action = QAction("跟随系统", self)
        theme_submenu.addAction(light_action)
        theme_submenu.addAction(dark_action)
        theme_submenu.addAction(auto_action)
        light_action.triggered.connect(lambda: self._apply_theme("light"))
        dark_action.triggered.connect(lambda: self._apply_theme("dark"))
        auto_action.triggered.connect(lambda: self._apply_theme("auto"))

        help_menu = menu_bar.addMenu("帮助(&H)")
        about_action = QAction("关于", self)
        help_menu.addAction(about_action)
        about_action.triggered.connect(self._show_about)

        return menu_bar

    # -----------------------------------------------------------
    # 底部状态栏
    # -----------------------------------------------------------
    def _create_status_bar(self) -> QFrame:
        bar = QFrame(self)
        bar.setObjectName("statusBar")
        bar.setFixedHeight(30)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 0, 16, 0)
        layout.setSpacing(8)

        self.status_label = QLabel("", bar)
        self.status_label.setObjectName("statusText")
        layout.addWidget(self.status_label, 1)

        self.theme_indicator = QLabel("主题：跟随系统", bar)
        self.theme_indicator.setObjectName("statusText")
        layout.addWidget(self.theme_indicator)
        return bar

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _on_project_changed_status(self, project) -> None:
        """状态栏显示当前项目。"""
        if project is None:
            self._set_status(f"就绪  ·  {APP_NAME} v{APP_VERSION}")
        else:
            self._set_status(f"当前项目：{project.name}  ·  {project.params.get('path', '')}")

    # -----------------------------------------------------------
    # 导航页注册
    # -----------------------------------------------------------
    def _init_navigation(self) -> None:
        self.project_tab = ProjectTab(self.project_vm, self)
        self.project_tab.setObjectName("projectTab")
        self.addSubInterface(
            self.project_tab, FluentIcon.FOLDER, "项目管理",
            position=NavigationItemPosition.TOP,
        )

        self.data_tab = DataTab(self.dataset_vm, self)
        self.data_tab.setObjectName("dataTab")
        self.addSubInterface(
            self.data_tab, FluentIcon.LIBRARY, "数据管理",
            position=NavigationItemPosition.TOP,
        )

        self.model_tab = ModelTab(self.model_vm, self)
        self.model_tab.setObjectName("modelTab")
        self.addSubInterface(
            self.model_tab, FluentIcon.ROBOT, "模型管理",
            position=NavigationItemPosition.TOP,
        )

        self.train_tab = TrainTab(self.train_vm, self)
        self.train_tab.setObjectName("trainTab")
        self.addSubInterface(
            self.train_tab, FluentIcon.TRAIN, "模型训练",
            position=NavigationItemPosition.TOP,
        )

        self.evaluate_tab = EvaluateTab(self.evaluate_vm, self)
        self.evaluate_tab.setObjectName("evaluateTab")
        self.addSubInterface(
            self.evaluate_tab, FluentIcon.VIEW, "模型评估",
            position=NavigationItemPosition.TOP,
        )

        self.export_tab = ExportTab(self.export_vm, self)
        self.export_tab.setObjectName("exportTab")
        self.addSubInterface(
            self.export_tab, FluentIcon.DOWNLOAD, "模型导出",
            position=NavigationItemPosition.TOP,
        )

    # -----------------------------------------------------------
    # 消息提示
    # -----------------------------------------------------------
    def _wire_messages(self) -> None:
        """将各 ViewModel 的 message 信号统一转为 InfoBar 提示。"""
        vms = [
            self.project_vm, self.dataset_vm, self.model_vm,
            self.train_vm, self.evaluate_vm, self.export_vm,
        ]
        for vm in vms:
            vm.message.connect(self._show_message)
        # 当前项目变化时更新状态栏
        self.project_vm.projectChanged.connect(self._on_project_changed_status)

    def _show_message(self, level: str, text: str) -> None:
        """在窗口右上角弹出 InfoBar。level: success / error / warning / info。"""
        level = level if level in ("success", "error", "warning", "info") else "info"
        getattr(InfoBar, level)(
            title=text,
            content="",
            parent=self,
            position=InfoBarPosition.TOP_RIGHT,
            duration=3000,
        )
        logger.info("[%s] %s", level, text)

    # -----------------------------------------------------------
    # 其它
    # -----------------------------------------------------------
    def _apply_theme(self, theme: str) -> None:
        if theme == "light":
            setTheme(Theme.LIGHT)
        elif theme == "dark":
            setTheme(Theme.DARK)
        else:
            setTheme(Theme.AUTO)
        self.theme_indicator.setText(f"主题：{theme}")
        self._set_status(f"已切换主题：{theme}")

    def _nav_to_project(self) -> None:
        self.navigationInterface.setCurrentItem(self.project_tab.objectName())

    def _open_project_dialog(self) -> None:
        """弹出 .mprj 文件选择对话框并打开项目。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "打开项目文件", "", PROJECT_FILE_FILTER
        )
        if path:
            self.project_vm.open_project(path)

    def _show_about(self) -> None:
        InfoBar.info(
            title=f"{APP_NAME} v{APP_VERSION}",
            content="基于 PySide6 与 QFluentWidgets 的深度学习缺陷检测系统。\n"
                    "采用 MVVM 架构，覆盖项目管理、数据管理、模型训练、评估与导出。",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=-1,
        )

    # -----------------------------------------------------------
    # 事件
    # -----------------------------------------------------------
    def resizeEvent(self, e) -> None:
        """覆盖默认标题栏定位：让其靠左（x=0）并占满全宽。

        FluentWindow 默认把标题栏 move(46, 0)，为顶部展开按钮预留 46px；
        展开按钮已移入导航栏，故校正标题栏从窗口最左侧开始。
        """
        super().resizeEvent(e)
        self.titleBar.move(0, 0)
        self.titleBar.resize(self.width(), self.titleBar.height())

    def closeEvent(self, event) -> None:
        logger.info("应用退出")
        super().closeEvent(event)