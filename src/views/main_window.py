"""主窗口：直接继承 QFluentWidgets 的 FluentWindow（无边框，自带标题栏与左侧导航），
并在其内部重构根布局，加入顶部菜单栏与底部状态栏。

布局结构（纵向）：
    Fluent 标题栏（无边框窗口自带：应用标题 + 窗口控制按钮 + 拖动区）
    菜单栏（QMenuBar）
    内容区（横向：左侧导航栏 + 右侧导航页窗口）
    状态栏（底部）
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices
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
from qfluentwidgets.components.navigation import NavigationTreeWidget

from src.utils.constants import (
    APP_NAME,
    APP_VERSION,
    BRAND_COLOR,
    NAV_MIN_WIDTH,
    NAV_WIDTH_MARGIN,
    PROJECT_FILE_FILTER,
)
from src.utils.config import ConfigManager
from src.utils.history import stack
from src.utils.logger import get_logger
from src.viewmodels import (
    AnnotateViewModel,
    CategoryViewModel,
    DatasetViewModel,
    EvaluateViewModel,
    ExportViewModel,
    ModelViewModel,
    ProjectViewModel,
    TrainViewModel,
)
from src.views import (
    AnnotateTab,
    EvaluateTab,
    ExportTab,
    GalleryTab,
    ProjectTab,
    ReviewTab,
    SplitTab,
    TrainTab,
)
from src.views.dialogs.preferences_dialog import PreferencesDialog
from src.views.dialogs.shortcut_dialog import ShortcutDialog
from src.views.shortcuts import ShortcutManager

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

        # 配置（偏好设置持久化）
        self.config = ConfigManager(self)

        # ViewModel 层（业务逻辑）
        self.project_vm = ProjectViewModel(self)
        self.dataset_vm = DatasetViewModel(self.project_vm, self)
        self.category_vm = CategoryViewModel(self.project_vm, self)
        self.annotate_vm = AnnotateViewModel(self.dataset_vm, self.project_vm, self)
        self.model_vm = ModelViewModel(self.project_vm, self)
        self.train_vm = TrainViewModel(self.project_vm, self)
        self.evaluate_vm = EvaluateViewModel(self.project_vm, self)
        # 导出页要读评估结果来生成模型报告，因此把评估 ViewModel 传入
        self.export_vm = ExportViewModel(self.project_vm, self.evaluate_vm, self)

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
        # 按导航页标题长度收窄导航栏宽度
        self._fit_navigation_width()

        # 快捷键体系（F1 查看总览）与撤销 / 重做
        self._init_shortcuts()
        self.setAcceptDrops(True)
        # 应用已保存的偏好设置（十字准线、区域不透明度、滚轮方向等）
        self._apply_preferences()
        self.project_vm.recoverableChanged.emit(
            self.project_vm.recoverable_projects()
        )

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
    # 导航栏宽度自适应
    # -----------------------------------------------------------
    def _fit_navigation_width(self) -> None:
        """按导航页标题长度收窄导航栏展开宽度。

        QFluentWidgets 把导航面板的展开宽度固定为 322px，而条目宽度为
        「面板宽度 - 10」，默认值下条目内有近 200px 是纯空白。这里取各导航
        项 ``suitableWidth()``（左缩进 + 图标 + 文字 + 右缩进）的最大值，
        加上面板内边距与条目右侧呼吸空间，得到刚好容纳最长标题的宽度。

        注意：``setExpandWidth`` 会同时改写 ``NavigationWidget.EXPAND_WIDTH``，
        因此必须在导航项创建之后、窗口首次展开之前调用。
        """
        panel = self.navigationInterface.panel
        widest = 0
        for item in panel.items.values():
            widget = item.widget
            if isinstance(widget, NavigationTreeWidget):
                widest = max(widest, widget.suitableWidth())

        if widest <= 0:
            return

        width = max(NAV_MIN_WIDTH, widest + NAV_WIDTH_MARGIN)
        self.navigationInterface.setExpandWidth(width)
        logger.info("导航栏展开宽度：%dpx（最长标题所需 %dpx）", width, widest)

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
        self.file_menu = file_menu          # 持有引用，便于后续扩展与自动化检查
        save_action = QAction("保存项目", self)
        save_action.setIcon(FluentIcon.SAVE.icon())
        save_as_action = QAction("项目另存为", self)
        save_as_action.setIcon(FluentIcon.SAVE_AS.icon())
        close_action = QAction("关闭项目", self)
        close_action.setIcon(FluentIcon.CLOSE.icon())
        exit_action = QAction("退出", self)
        exit_action.setIcon(FluentIcon.POWER_BUTTON.icon())

        file_menu.addAction(save_action)
        file_menu.addAction(save_as_action)
        file_menu.addSeparator()
        file_menu.addAction(close_action)
        file_menu.addSeparator()
        file_menu.addAction(exit_action)

        # 菜单栏创建早于导航页，这里用转发方法在触发时再取页面
        save_action.triggered.connect(self._save_project)
        save_as_action.triggered.connect(self._save_project_as)
        close_action.triggered.connect(self._close_project)
        exit_action.triggered.connect(self.close)

        edit_menu = menu_bar.addMenu("编辑(&E)")
        undo_action = QAction("撤销", self)
        redo_action = QAction("重做", self)
        undo_action.setShortcut("Ctrl+Z")
        redo_action.setShortcut("Ctrl+Y")
        edit_menu.addAction(undo_action)
        edit_menu.addAction(redo_action)
        undo_action.triggered.connect(self._undo)
        redo_action.triggered.connect(self._redo)

        # 文件 / 帮助菜单里的快捷键（与上面的 QAction 一一对应）
        save_action.setShortcut("Ctrl+S")
        save_as_action.setShortcut("Ctrl+Shift+S")
        close_action.setShortcut("Ctrl+W")

        view_menu = menu_bar.addMenu("视图(&V)")
        preferences_action = QAction("偏好设置…", self)
        preferences_action.setShortcut("Ctrl+,")
        view_menu.addAction(preferences_action)
        preferences_action.triggered.connect(self._on_preferences)
        view_menu.addSeparator()
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
        shortcut_action = QAction("快捷键", self)
        shortcut_action.setShortcut("F1")
        log_action = QAction("打开日志目录", self)
        about_action = QAction("关于", self)
        help_menu.addAction(shortcut_action)
        help_menu.addAction(log_action)
        help_menu.addAction(about_action)
        shortcut_action.triggered.connect(self._show_shortcuts)
        log_action.triggered.connect(self._open_log_dir)
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

    def _on_dataset_ready(self, yaml_path: str) -> None:
        """数据集划分完成后，把 data.yaml 交给训练页。"""
        self.train_vm.update_config(data_yaml=yaml_path)

    def _open_in_annotator(self, path: str) -> None:
        """从图库跳转到标注页，并定位到指定图片。"""
        index = self.annotate_vm.set_image_by_path(path)
        if index < 0:
            self._show_message("warning", "未在当前数据集中找到该图片")
            return
        self.switchTo(self.annotate_tab)

    def _on_project_loaded(self, project) -> None:
        """打开 / 新建项目后，把项目数据回填到各 ViewModel。

        各页面只监听自己 ViewModel 的信号，因此必须在这里统一分发；
        否则打开已有项目会出现「界面全空」。
        （类别页与标注页分别自行监听 projectChanged / datasetChanged。）
        """
        for vm in (self.dataset_vm, self.model_vm, self.train_vm,
                   self.evaluate_vm, self.export_vm):
            loader = getattr(vm, "load_from_project", None)
            if loader is None:
                continue
            try:
                loader(project)
            except Exception as exc:  # noqa: BLE001 - 单页恢复失败不应阻断整体
                logger.warning("%s 恢复项目状态失败: %s", type(vm).__name__, exc)
        # 项目类型决定标注方式与训练任务
        try:
            self.annotate_tab.apply_project_type()
            self.train_tab.sync_from_config()
        except Exception as exc:  # noqa: BLE001 - 界面同步失败不应阻断打开流程
            logger.warning("按项目类型同步界面失败: %s", exc)

    # -----------------------------------------------------------
    # 导航页注册
    # -----------------------------------------------------------
    def _init_navigation(self) -> None:
        self.project_tab = ProjectTab(self.project_vm, self.dataset_vm, self)
        self.project_tab.setObjectName("projectTab")
        self.addSubInterface(
            self.project_tab, FluentIcon.FOLDER, "项目管理",
            position=NavigationItemPosition.TOP,
        )

        self.gallery_tab = GalleryTab(self.dataset_vm, self.category_vm, self)
        self.gallery_tab.setObjectName("galleryTab")
        self.addSubInterface(
            self.gallery_tab, FluentIcon.PHOTO, "图库导入",
            position=NavigationItemPosition.TOP,
        )

        self.annotate_tab = AnnotateTab(self.annotate_vm, self.category_vm, self)
        self.annotate_tab.setObjectName("annotateTab")
        self.addSubInterface(
            self.annotate_tab, FluentIcon.BRUSH, "图像标注",
            position=NavigationItemPosition.TOP,
        )

        self.review_tab = ReviewTab(self.dataset_vm, self.category_vm, self)
        self.review_tab.setObjectName("reviewTab")
        self.addSubInterface(
            self.review_tab, FluentIcon.CHECKBOX, "标注检查",
            position=NavigationItemPosition.TOP,
        )

        self.split_tab = SplitTab(self.dataset_vm, self.category_vm, self)
        self.split_tab.setObjectName("splitTab")
        self.addSubInterface(
            self.split_tab, FluentIcon.LIBRARY, "数据拆分",
            position=NavigationItemPosition.TOP,
        )

        # 训练页同时承载「模型与权重」（原「模型管理」页已并入此页）
        self.train_tab = TrainTab(
            self.train_vm, self.model_vm, self.dataset_vm, self
        )
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
    # 快捷键 / 撤销重做
    # -----------------------------------------------------------
    def _init_shortcuts(self) -> None:
        """注册全局与页面级快捷键，并登记到 F1 总览。"""
        self.shortcuts = ShortcutManager(self)
        manager = self.shortcuts
        pages = [
            ("项目管理", self.project_tab), ("图库导入", self.gallery_tab),
            ("图像标注", self.annotate_tab), ("标注检查", self.review_tab),
            ("数据拆分", self.split_tab), ("模型训练", self.train_tab),
            ("模型评估", self.evaluate_tab), ("模型导出", self.export_tab),
        ]
        for index, (name, page) in enumerate(pages, start=1):
            manager.register(
                self, f"Alt+{index}", lambda target=page: self.switchTo(target),
                f"打开{name}页", group="通用", global_scope=True,
            )

        # 以下快捷键由菜单 QAction 提供（此处只登记到总览，避免重复注册）
        for group, name, sequence in (
            ("通用", "撤销", "Ctrl+Z"),
            ("通用", "重做", "Ctrl+Y"),
            ("通用", "重做", "Ctrl+Shift+Z"),
            ("项目", "保存项目", "Ctrl+S"),
            ("项目", "项目另存为", "Ctrl+Shift+S"),
            ("项目", "关闭项目", "Ctrl+W"),
            ("通用", "快捷键总览", "F1"),
        ):
            manager.record(group, name, sequence)
        manager.register(self, "F11", self._toggle_fullscreen, "全屏切换",
                         group="显示", global_scope=True)

        # 图库 / 检查页
        manager.register(self.gallery_tab, "Ctrl+A", self.gallery_tab.select_all,
                         "全选图像", group="图库 / 检查")
        manager.register(self.gallery_tab, "Delete", self.gallery_tab.remove_selected,
                         "移除选中图像", group="图库 / 检查")
        manager.register(self.review_tab, "Ctrl+A", self.review_tab.select_all,
                         "全选图像", group="图库 / 检查")

        # 标注页（画布内的键位在 AnnotationCanvas 里处理，这里只登记到总览）
        manager.register(self.annotate_tab, "Ctrl+S", self.annotate_vm.save,
                         "保存标注", group="标注")
        for name, sequence in (
            ("选择类别 1-9", "1 - 9"),
            ("上 / 下 / 左 / 右移动标注", "↑ ↓ ← →"),
            ("细调标注（更小步长）", "Alt + ↑ ↓ ← →"),
            ("大步移动标注", "Shift + ↑ ↓ ← →"),
            ("删除选中标注", "Del"),
            ("全选标注", "Ctrl + A"),
            ("多选标注（加选 / 减选）", "Ctrl + 点击"),
            ("框选标注", "Shift + 拖动"),
            ("下一张 / 上一张图片", "PageDown / PageUp"),
            ("首张 / 末张图片", "Ctrl + PageDown / PageUp"),
            ("适应窗口", "Ctrl + 0"),
            ("放大 / 缩小", "Ctrl + + / Ctrl + -"),
            ("闭合多边形", "右键 / 双击"),
            ("T / V / E 拆分角标", "点击角标"),
        ):
            manager.record("标注", name, sequence)

    def _undo(self) -> None:
        label = stack().undo()
        if label:
            self._set_status(f"已撤销：{label}（{stack().status_text()}）")
            logger.info("撤销：%s", label)
        else:
            self._set_status("没有可撤销的操作")

    def _redo(self) -> None:
        label = stack().redo()
        if label:
            self._set_status(f"已重做：{label}")
            logger.info("重做：%s", label)
        else:
            self._set_status("没有可重做的操作")

    def _show_shortcuts(self) -> None:
        dialog = ShortcutDialog(self.shortcuts.items(), self)
        dialog.exec()

    # -----------------------------------------------------------
    # 偏好设置
    # -----------------------------------------------------------
    def _on_preferences(self) -> None:
        dialog = PreferencesDialog(self.config, self)
        if dialog.exec():
            dialog.apply()
            self._apply_preferences()
            self._set_status("偏好设置已更新")

    def _apply_preferences(self) -> None:
        """把偏好设置应用到各页面。"""
        config = self.config
        canvas = self.annotate_tab.canvas
        canvas.set_crosshair(config.crosshair, config.crosshair_opacity)
        canvas.set_region_opacity(config.region_opacity)
        canvas.set_wheel_inverted(config.wheel_inverted)
        canvas.set_show_pixel(config.show_pixel_value)
        self.annotate_tab.brightness_slider.setValue(config.default_brightness)
        self.annotate_tab.contrast_slider.setValue(config.default_contrast)
        # 自动保存间隔随偏好变化重设
        self.project_vm.load_preferences()
        # CPU 线程数：未手动改过时跟随偏好（默认 4）
        if int(getattr(self.train_vm.config, "workers", 4) or 4) == 4:
            self.train_vm.update_config(workers=config.cpu_threads)

    def _open_log_dir(self) -> None:
        directory = Path(ConfigManager.log_dir())
        if directory.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))
        else:
            self._set_status("日志目录尚未创建")

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    # -----------------------------------------------------------
    # 拖放：项目文件 / 图片 / 文件夹
    # -----------------------------------------------------------
    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """拖入 `.mprj` 打开项目，拖入图片 / 文件夹则导入图库。"""
        urls = [
            url for url in event.mimeData().urls() if url.isLocalFile()
        ]
        if not urls:
            return
        paths = [Path(url.toLocalFile()) for url in urls]
        projects = [p for p in paths if p.suffix.lower() == ".mprj"]
        if len(projects) == 1:
            self.project_vm.open_project(str(projects[0]))
            self.switchTo(self.project_tab)
            event.acceptProposedAction()
            return
        images = [
            p for p in paths
            if p.is_file() and p.suffix.lower() in (
                ".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp",
            )
        ]
        folders = [p for p in paths if p.is_dir()]
        if images or folders:
            self.switchTo(self.gallery_tab)
            self.gallery_tab.import_paths([str(p) for p in images + folders])
            event.acceptProposedAction()

    # -----------------------------------------------------------
    # 消息提示
    # -----------------------------------------------------------
    def _wire_messages(self) -> None:
        """将各 ViewModel 的 message 信号统一转为 InfoBar 提示。"""
        vms = [
            self.project_vm, self.dataset_vm, self.category_vm, self.annotate_vm,
            self.model_vm, self.train_vm, self.evaluate_vm, self.export_vm,
        ]
        for vm in vms:
            vm.message.connect(self._show_message)
        # 当前项目变化时：更新状态栏，并把项目数据回填到各页面
        self.project_vm.projectChanged.connect(self._on_project_changed_status)
        self.project_vm.projectChanged.connect(self._on_project_loaded)
        # 数据集划分完成后，把 data.yaml 交给训练页
        self.dataset_vm.datasetReady.connect(self._on_dataset_ready)
        # 图库双击图片 → 切到标注页并定位到该图片
        self.gallery_tab.requestAnnotate.connect(self._open_in_annotator)
        # 新建项目时若带数据集目录，直接导入图库
        self.project_tab.requestImportDataset.connect(self.dataset_vm.import_images)
        # 项目信息变化（如完成划分、标注）后刷新项目页
        self.dataset_vm.datasetChanged.connect(lambda _d: self.project_tab.refresh_current())
        self.annotate_vm.statusChanged.connect(
            lambda _done, _total: self.project_tab.refresh_current()
        )

    def _show_message(self, level: str, text: str) -> None:
        """在窗口右上角弹出 InfoBar。level: success / error / warning / info。

        info 级（蓝色图标）只写入后台日志，不弹窗：这类消息多是「已保存 /
        当前档位 / 已刷新」之类的过程反馈，弹窗太吵；页面内的提示文字、
        状态栏与日志仍然保留，反馈不会丢失。
        """
        level = level if level in ("success", "error", "warning", "info") else "info"
        logger.info("[%s] %s", level, text)
        if level == "info":
            return
        getattr(InfoBar, level)(
            title=text,
            content="",
            parent=self,
            position=InfoBarPosition.TOP_RIGHT,
            duration=5000,       # 通知停留时长
        )

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

    # -----------------------------------------------------------
    # 文件菜单转发（项目管理页持有名称 / 说明编辑状态）
    # -----------------------------------------------------------
    def _save_project(self) -> None:
        """菜单：保存当前项目（同时写回项目页上的名称 / 说明编辑）。"""
        self.project_tab.save_project()

    def _save_project_as(self) -> None:
        """菜单：项目另存为。"""
        self.project_tab.save_project_as()

    def _close_project(self) -> None:
        """菜单：关闭当前项目。"""
        self.project_tab.close_project()

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

    def closeEvent(self, e) -> None:  # noqa: N802 - Qt 命名
        logger.info("应用退出")
        super().closeEvent(e)