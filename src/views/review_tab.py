"""标注检查页：以缩略图排布复核标注质量与数据质量。

布局参照 Halcon DLT 的检查视图：
    左侧：已选择图像详情 / 对象实例 / 标签类别 / 质检结果
    右侧：大卡片缩略图网格（已标注项带彩色角标与描边）——
          顶部不放筛选容器，筛选完全由左侧「标签类别」点击驱动
          （「无标签」行 = 未标注图片，再点当前行取消筛选）

任务差异：
    * 整图任务（分类 / 异常检测）没有对象实例，按整图类别核对；
    * 框 / 多边形 / 文本框任务（检测 / 旋转框 / 分割 / OCR）在「对象实例」
      列表里逐对象展示：裁剪预览 + 类别 + 类型，双击可回标注页修正。
      注意裁剪只是**核对视图**：本项目 YOLO 系按整图 + 标签文件训练，
      不把对象裁出来做训练数据（OCR 识别集除外）。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QIcon,
    QImage,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    PrimaryPushButton,
    StrongBodyLabel,
)

from src.services.annotation_service import AnnotationService
from src.viewmodels.category_vm import CategoryViewModel
from src.viewmodels.dataset_vm import DatasetViewModel
from src.views.data_widgets import ClassCard, side_column
from src.views.ui import DisplayDock
from src.views.ui import tokens as T
from src.views.widgets import THUMB_MEDIUM, THUMB_STEPS, ThumbnailGrid
# 整图任务的数据布局（无对象实例）
_WHOLE_IMAGE_LAYOUTS = ("classify", "anomaly_folder")
# 对象实例的裁剪预览边长（装饰性尺寸）
_ITEM_ICON = 44
# 对象类型显示名
_KIND_TEXT = {"box": "矩形", "polygon": "多边形", "mask": "掩码", "text": "文本"}
_FALLBACK_COLOR = "#66CCFF"


class ReviewTab(QWidget):
    """标注检查页。"""

    requestAnnotate = Signal(str, int)     # 跳转标注页（图路径, 实例序号；-1 = 不定位对象）

    def __init__(
        self,
        dataset_vm: DatasetViewModel,
        category_vm: CategoryViewModel | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._vm = dataset_vm
        self._category_vm = category_vm
        # 当前筛选的类别行：None = 全部；"" = 「无标签」（未标注）；其余 = 类别名
        self._class_filter: str | None = None
        # 页面不可见时只记录待渲染的图片，切回本页再画
        self._pending_pairs: list = []
        self._stale = True
        # 「对象实例」列表当前对应的图片路径（双击回标注页用）
        self._item_path: str | None = None
        self._build_ui()
        self._bind()
        self.refresh()

    # -----------------------------------------------------------
    # UI
    # -----------------------------------------------------------
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(T.SPACE_XL, T.SPACE_LG, T.SPACE_XL, T.SPACE_LG)
        root.setSpacing(T.SPACE_LG)

        # 显示调试栏：窗口右侧竖直图标（亮度 / 对比度 / 缩略图尺寸；与图库页一致）
        self.display_dock = DisplayDock(self, with_size=True)
        self.display_dock.set_thumb_steps(THUMB_STEPS)

        root.addWidget(self._build_side())
        root.addLayout(self._build_main(), 1)
        root.addWidget(self.display_dock)

    def _build_side(self) -> QWidget:
        self.detail_card = CardWidget(self)
        layout = QVBoxLayout(self.detail_card)
        layout.setContentsMargins(T.CARD_PAD_H, T.CARD_PAD_V, T.CARD_PAD_H, T.CARD_PAD_V)
        layout.setSpacing(T.SPACE_SM)
        layout.addWidget(StrongBodyLabel("已选择图像", self.detail_card))
        self.detail_name = BodyLabel("—", self.detail_card)
        self.detail_name.setWordWrap(True)
        layout.addWidget(self.detail_name)
        self.detail_path = CaptionLabel("路径：—", self.detail_card)
        self.detail_path.setWordWrap(True)
        layout.addWidget(self.detail_path)
        self.detail_class = CaptionLabel("类别：—", self.detail_card)
        layout.addWidget(self.detail_class)
        self.detail_state = CaptionLabel("状态：—", self.detail_card)
        layout.addWidget(self.detail_state)
        self.detail_count = CaptionLabel("标注对象：—", self.detail_card)
        layout.addWidget(self.detail_count)

        # 对象实例：逐对象裁剪预览（整图任务隐藏）
        self.items_card = CardWidget(self)
        items_layout = QVBoxLayout(self.items_card)
        items_layout.setContentsMargins(
            T.CARD_PAD_H, T.CARD_PAD_V, T.CARD_PAD_H, T.CARD_PAD_V
        )
        items_layout.setSpacing(T.SPACE_SM)
        items_layout.addWidget(StrongBodyLabel("对象实例", self.items_card))
        self.items_list = QListWidget(self.items_card)
        self.items_list.setIconSize(QSize(_ITEM_ICON, _ITEM_ICON))
        self.items_list.setMinimumHeight(150)
        self.items_list.itemDoubleClicked.connect(self._on_item_activated)
        items_layout.addWidget(self.items_list)

        self.class_card = ClassCard(self._category_vm, self)
        self.class_card.setMaximumHeight(260)

        self.quality_card = CardWidget(self)
        quality_layout = QVBoxLayout(self.quality_card)
        quality_layout.setContentsMargins(T.CARD_PAD_H, T.CARD_PAD_V, T.CARD_PAD_H, T.CARD_PAD_V)
        quality_layout.setSpacing(T.SPACE_SM)
        quality_layout.addWidget(StrongBodyLabel("质检", self.quality_card))
        self.quality_btn = PrimaryPushButton("一键质检", self.quality_card)
        quality_layout.addWidget(self.quality_btn)
        self.quality_list = QListWidget(self.quality_card)
        self.quality_list.setMinimumHeight(150)
        quality_layout.addWidget(self.quality_list)
        self.quality_hint = CaptionLabel("", self.quality_card)
        self.quality_hint.setWordWrap(True)
        quality_layout.addWidget(self.quality_hint)

        return side_column(
            self.detail_card, self.items_card, self.class_card, self.quality_card,
            width=292,
        )

    def _build_main(self) -> QVBoxLayout:
        """缩略图窗口：顶部不再放容器，筛选完全由左侧「标签类别」点击驱动。"""
        column = QVBoxLayout()
        column.setSpacing(T.SPACE_ML)

        self.grid = ThumbnailGrid(self)
        self.grid.set_thumb_steps(THUMB_STEPS)   # 档位与图库页 / 调试栏滑杆一一对应
        self.grid.set_thumb_size(THUMB_MEDIUM)   # 默认档与图库页保持一致
        self.grid.set_wheel_zoom(True)      # Ctrl + 滚轮缩放缩略图
        column.addWidget(self.grid, 1)
        # 档位初始值对齐网格当前尺寸（否则面板打开时读数与实际缩略图不符）
        self.display_dock.set_thumb_size(self.grid.thumb_size())
        self._apply_display()
        return column

    # -----------------------------------------------------------
    # 绑定
    # -----------------------------------------------------------
    def _bind(self) -> None:
        self.display_dock.thumbSizeChanged.connect(self._on_thumb_size)
        self.display_dock.changed.connect(self._apply_display)
        self.grid.thumbSizeChanged.connect(self._on_grid_thumb_size)
        self.grid.imageActivated.connect(self._on_selected)
        # 双击缩略图 → 在标注页打开该图（与图库页一致，不定位对象）
        self.grid.imageDoubleClicked.connect(self._on_grid_double_click)
        self.class_card.classClicked.connect(self._on_class_clicked)
        self.class_card.changed.connect(self.refresh)
        self.quality_btn.clicked.connect(self._vm.run_quality_check)
        self._vm.qualityReady.connect(self._on_quality)

        # 按需自动刷新（不再提供手动刷新按钮）
        self._vm.datasetChanged.connect(lambda _d: self.refresh())
        self._vm.taskFinished.connect(lambda _t: self.refresh())
        if self._category_vm is not None:
            self._category_vm.classesChanged.connect(lambda _c: self.refresh())

    # -----------------------------------------------------------
    # 刷新
    # -----------------------------------------------------------
    def refresh(self) -> None:
        counts = self._vm.class_distribution()
        flags = self._vm.annotated_flags()
        # 「无标签」行数量 = 未标注张数（分类 / 检测统一口径，与图库 / 标注页一致）
        counts[""] = sum(1 for flag in flags if not flag)
        if self._category_vm is not None:
            self.class_card.set_counts(counts)
            self.class_card.set_classes(list(self._category_vm.classes), counts)

        pairs = self._filtered_pairs()
        self._pending_pairs = pairs
        # 本页不可见时不渲染缩略图，切回本页再画（避免打开项目就重建几百张图）
        if self.isVisible():
            self._render_grid()
        else:
            self._stale = True
        if self.isVisible():
            self._on_selected(self.grid.current_index())

    def _filtered_pairs(self) -> list:
        """按左侧类别行的当前选择取图片。

        ``None`` = 全部；``""``（「无标签」行）= 未标注图片；其余 = 该类别。
        """
        if self._class_filter is None:
            return self._vm.filtered_images("all", "")
        if self._class_filter == "":
            return self._vm.filtered_images("unannotated", "")
        return self._vm.filtered_images("all", self._class_filter)

    def _apply_display(self) -> None:
        """把显示增强参数下发给缩略图网格（只影响显示）。"""
        self.grid.set_display(**self.display_dock.values())
        self.grid.prewarm_visible()

    def _render_grid(self) -> None:
        """真正绘制缩略图（仅在页面可见时调用）。"""
        pairs = self._pending_pairs or []
        paths = [path for path, _ in pairs]
        colors, subsets = self._vm.decorations_for(paths)
        self.grid.set_images(
            paths,
            [flag for _, flag in pairs],
            colors,
            subsets,
            classnames=self._vm.class_names_for(paths),
        )
        # 没有选中项时默认选中第一张，保证详情面板不是空的
        if pairs and self.grid.current_index() < 0:
            self.grid.set_current_index(0)
        self._on_selected(self.grid.current_index())
        self._stale = False

    def refresh_marks(self) -> None:
        """只刷新「已标注」角标（切回本页时调用）。"""
        pairs = self._filtered_pairs()
        if self.grid.count() != len(pairs):
            self._pending_pairs = pairs
            self._render_grid()
            return
        colors, _subsets = self._vm.decorations_for([path for path, _ in pairs])
        self.grid.set_annotated(
            [flag for _, flag in pairs], colors,
            classnames=self._vm.class_names_for([path for path, _ in pairs]),
        )

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        if self._stale:
            self._render_grid()
        else:
            self.refresh_marks()

    def _on_class_clicked(self, name: str) -> None:
        """点击类别行筛选：「无标签」= 未标注图片；再点当前行取消筛选。

        页面上没有独立「全部」入口——顶部筛选容器已删除，筛选完全由
        本列表驱动（再次点击当前行即显示全部）。
        """
        self._class_filter = None if self._class_filter == name else name
        self.refresh()

    def _on_thumb_size(self, step: int) -> None:
        index = max(0, min(len(THUMB_STEPS) - 1, int(step)))
        self.grid.set_thumb_size(THUMB_STEPS[index])

    def _on_grid_double_click(self, index: int) -> None:
        """双击缩略图 → 在标注页打开该图（与图库页一致，不定位对象）。"""
        path = self.grid.path_at(index)
        if path:
            self.requestAnnotate.emit(path, -1)

    def select_all(self) -> None:
        """全选当前筛选出的图像（Ctrl + A）。"""
        self.grid.selectAll()

    def _on_grid_thumb_size(self, size: int) -> None:
        """Ctrl + 滚轮缩放后，把尺寸档位同步到调试栏。"""
        self.display_dock.set_thumb_size(int(size))

    def _on_selected(self, index: int) -> None:
        # 整图任务（分类 / 异常检测）按整图类别核对，没有对象实例
        whole = self._vm.split_layout() in _WHOLE_IMAGE_LAYOUTS
        self.items_card.setVisible(not whole)

        path = self.grid.path_at(index)
        if not path:
            self.detail_name.setText("—")
            self.detail_path.setText("路径：—")
            self.detail_class.setText("类别：—")
            self.detail_state.setText("状态：—")
            self.detail_count.setText("标注对象：—")
            self._reload_items(None)
            return

        image = Path(path)
        images = self._vm.images()
        flags = self._vm.annotated_flags()
        annotated = False
        if image in images:
            position = images.index(image)
            if position < len(flags):
                annotated = flags[position]

        self.detail_name.setText(image.name)
        self.detail_path.setText(f"路径：{image.parent}")
        self.detail_class.setText(f"类别：{self._vm.image_class(image)}")
        self.detail_state.setText("状态：已标注" if annotated else "状态：未标注")

        if whole:
            self._reload_items(None)
            self.detail_count.setText("标注对象：—")
            return
        count = self._reload_items(image)
        self.detail_count.setText(f"标注对象：{count} 个")

    # -----------------------------------------------------------
    # 对象实例（裁剪核对视图）
    # -----------------------------------------------------------
    def _reload_items(self, image: Path | None) -> int:
        """重建「对象实例」列表，返回对象数（无图时为 0）。

        每个对象展示裁剪预览 + 类别色 + 类型：检测 / 分割核对框与轮廓、
        OCR 附转写文本。裁剪仅为人工核对，不代表训练口径（整图训练）。
        """
        self.items_list.clear()
        self._item_path = str(image) if image is not None else None
        if image is None:
            return 0
        label_dir = self._vm.label_dir()
        if label_dir is None:
            return 0
        label = AnnotationService.label_path_for(image, label_dir)
        if not label.is_file():
            return 0
        source = QImage(str(image))
        if source.isNull():
            return 0
        annotation = AnnotationService.load_yolo(
            label, source.width(), source.height()
        )
        classes = {}
        if self._category_vm is not None:
            classes = {cls.cls_id: cls for cls in self._category_vm.classes}
        for index, item in enumerate(annotation.items):
            cls = classes.get(item.cls_id)
            name = cls.name if cls is not None else str(item.cls_id)
            color = cls.color if cls is not None else _FALLBACK_COLOR
            kind = _KIND_TEXT.get(item.kind, item.kind)
            row = QListWidgetItem(
                self._item_icon(source, item, color),
                self._item_label(index, name, kind, item),
            )
            row.setToolTip(self._item_tip(annotation, item, name, kind))
            self.items_list.addItem(row)
        return len(annotation.items)

    @staticmethod
    def _item_label(index: int, name: str, kind: str, item) -> str:
        label = f"{index + 1}. {name} · {kind}"
        if item.is_text:
            label += f"：{item.text or '—'}"
        return label

    @staticmethod
    def _item_tip(annotation, item, name: str, kind: str) -> str:
        """实例行提示：类别 · 类型 · 像素尺寸（OCR 附转写）。"""
        width = max(1, int(annotation.width or 1))
        height = max(1, int(annotation.height or 1))
        x1, y1, x2, y2 = item.bounds()
        px_w = int(round((x2 - x1) * width))
        px_h = int(round((y2 - y1) * height))
        text = f"{name} · {kind} · {px_w} × {px_h} px"
        if item.is_text and item.text:
            text += f" · {item.text}"
        return text

    def _item_icon(self, source: QImage, item, color: str) -> QIcon:
        """对象区域裁剪预览；多边形 / 掩码叠加轮廓与浅填充。"""
        width, height = source.width(), source.height()
        x1, y1, x2, y2 = item.bounds()
        pad = 0.01
        px1 = max(0, int((x1 - pad) * width))
        py1 = max(0, int((y1 - pad) * height))
        px2 = min(width, int((x2 + pad) * width) + 1)
        py2 = min(height, int((y2 + pad) * height) + 1)
        if px2 <= px1 or py2 <= py1:
            return QIcon()
        preview = source.copy(px1, py1, px2 - px1, py2 - py1).scaled(
            _ITEM_ICON, _ITEM_ICON,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        if not item.is_rect and len(item.points) >= 3:
            painter = QPainter(preview)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            sx = preview.width() / (px2 - px1)
            sy = preview.height() / (py2 - py1)
            polygon = QPolygonF([
                QPointF(
                    (point[0] * width - px1) * sx,
                    (point[1] * height - py1) * sy,
                )
                for point in item.points
            ])
            stroke = QColor(color or _FALLBACK_COLOR)
            painter.setPen(QPen(stroke, 1.5))
            fill = QColor(stroke)
            fill.setAlpha(60)
            painter.setBrush(fill)
            painter.drawPolygon(polygon)
            painter.end()
        return QIcon(QPixmap.fromImage(preview))

    def _on_item_activated(self, item) -> None:
        """双击对象实例 → 在标注页打开该图并选中对应对象。"""
        if self._item_path is None:
            return
        self.requestAnnotate.emit(
            self._item_path, self.items_list.row(item)
        )

    # -----------------------------------------------------------
    # 质检
    # -----------------------------------------------------------
    def _on_quality(self, result: dict) -> None:
        self.quality_list.clear()
        if not result:
            return
        duplicates = result.get("duplicates") or []
        blurry = result.get("blurry") or []
        exposure = result.get("exposure") or []
        labels = result.get("labels") or {}

        rows = [
            f"重复图片：{len(duplicates)} 组",
            f"模糊图片：{len(blurry)} 张",
            f"曝光异常：{len(exposure)} 张",
        ]
        if labels.get("skipped"):
            # 分类 / 异常检测：类别由目录 / 用户标记决定，没有每图标签文件
            rows.append("标签校验：该任务无 YOLO 标签（已跳过）")
        else:
            rows += [
                f"空标签：{len(labels.get('empty', []) or [])}",
                f"越界/非法：{len(labels.get('out_of_range', []) or [])}",
                f"缺失标签：{len(labels.get('missing_label', []) or [])}",
                f"孤立标签：{len(labels.get('orphan_label', []) or [])}",
            ]
        for text in rows:
            self.quality_list.addItem(QListWidgetItem(text))

        for group in duplicates[:3]:
            names = "、".join(Path(p).name for p in group[:3])
            self.quality_list.addItem(QListWidgetItem(f"  重复组：{names}"))
        for path, score in blurry[:3]:
            self.quality_list.addItem(
                QListWidgetItem(f"  模糊：{Path(path).name}（清晰度 {score:.0f}）")
            )
        for path, value in exposure[:3]:
            self.quality_list.addItem(
                QListWidgetItem(f"  曝光：{Path(path).name}（均值 {value:.0f}）")
            )
        self.quality_hint.setText("质检完成。")
