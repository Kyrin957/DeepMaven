"""模型导出导航页：选择训练产物 → 配置导出选项 → 导出模型 / 生成报告。

布局参照 Halcon DLT 的导出视图：

    左栏：导出配置（模型来源 / 格式 / 目录 / 尺寸与 Opset）+ 导出进度
    中央：模型概览（变体 / 尺寸 / 设备 / Epoch / 最佳指标…）、数据拆分（环形图 + 明细）、
          评估结果（指标 + 环形图）
    右栏：导出模型（针对推断 / 针对 API 优化 + 导出按钮）、生成报告、导出记录
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    LineEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    StrongBodyLabel,
    TableWidget,
)

from src.utils.constants import EXPORT_FORMATS, SPLIT_COLORS
from src.viewmodels.export_vm import ExportViewModel
from src.views.data_widgets import side_column
from src.views.ui import SafeSpinBox
from src.views.ui import tokens as T
from src.views.widgets import LegendList, PieChart

_REPORT_FORMATS = [
    ("html", "网页报告 (.html)"),
    ("md", "Markdown (.md)"),
]

# 导出后回归结论的显示文案（状态 → 表格文字）
_REGRESSION_LABELS = {"pass": "通过", "fail": "未通过", "skip": "跳过"}


def _regression_label(item: dict) -> str:
    return _REGRESSION_LABELS.get(str(item.get("regression") or ""), "—")


class ExportTab(QWidget):
    """模型导出页。"""

    def __init__(self, vm: ExportViewModel, parent=None):
        super().__init__(parent)
        self._vm = vm
        self._syncing = False
        self._build_ui()
        self._bind()
        self._on_config(self._vm.config)

    # -----------------------------------------------------------
    # 界面
    # -----------------------------------------------------------
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(T.SPACE_XL, T.SPACE_LG, T.SPACE_XL, T.SPACE_LG)
        root.setSpacing(T.SPACE_LG)
        root.addWidget(self._build_side(), 0)
        root.addLayout(self._build_center(), 1)
        root.addWidget(self._build_actions(), 0)

    def _card(self, title: str) -> tuple[CardWidget, QVBoxLayout]:
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(T.SPACE_2XL, T.SPACE_XL, T.SPACE_2XL, T.SPACE_XL)
        layout.setSpacing(T.SPACE_ML)
        if title:
            layout.addWidget(StrongBodyLabel(title, card))
        return card, layout

    def _tile(self, parent, layout: QGridLayout, row: int, column: int,
              title: str) -> BodyLabel:
        """「标题 + 值」小块，返回值的标签。"""
        box = QWidget(parent)
        inner = QVBoxLayout(box)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(T.SPACE_XXS)
        inner.addWidget(CaptionLabel(title, box))
        value = BodyLabel("—", box)
        value.setWordWrap(True)
        inner.addWidget(value)
        layout.addWidget(box, row, column)
        return value

    # --------------------------------------------------- 左栏
    def _build_side(self) -> QWidget:
        card, layout = self._card("导出配置")

        self.record_combo = ComboBox(card)
        self.record_combo.currentIndexChanged.connect(self._on_record_picked)
        layout.addWidget(self.record_combo)

        weights_row = QHBoxLayout()
        self.weights_edit = LineEdit(card)
        self.weights_edit.setPlaceholderText("模型权重 (.pt)")
        weights_row.addWidget(self.weights_edit, 1)
        weights_btn = PushButton("浏览", card)
        weights_btn.clicked.connect(self._on_browse_weights)
        weights_row.addWidget(weights_btn)
        layout.addLayout(weights_row)

        form = QFormLayout()
        form.setSpacing(T.SPACE_MD)
        self.format_combo = ComboBox(card)
        for fmt in EXPORT_FORMATS:
            self.format_combo.addItem(fmt["label"], userData=fmt["key"])
        self.format_combo.currentIndexChanged.connect(self._on_format_changed)
        self.format_combo.setToolTip("ONNX 跨平台 / TorchScript 便于 C++")
        form.addRow("导出格式", self.format_combo)

        out_row = QHBoxLayout()
        self.out_edit = LineEdit(card)
        self.out_edit.setPlaceholderText("导出目录")
        out_row.addWidget(self.out_edit, 1)
        out_btn = PushButton("浏览", card)
        out_btn.clicked.connect(self._on_browse_out)
        out_row.addWidget(out_btn)
        form.addRow("导出目录", out_row)

        size_row = QHBoxLayout()
        size_row.setSpacing(T.SPACE_MD)
        self.imgsz_spin = SafeSpinBox(card)
        self.imgsz_spin.setRange(64, 4096)
        self.imgsz_spin.setSingleStep(32)
        self.imgsz_spin.setFixedWidth(
            T.field_width(self.imgsz_spin, T.STEPPER_CHARS_WIDE)
        )
        self.imgsz_spin.valueChanged.connect(self._on_params_changed)
        size_row.addWidget(self.imgsz_spin)
        self.opset_spin = SafeSpinBox(card)
        self.opset_spin.setRange(9, 20)
        self.opset_spin.setFixedWidth(
            T.field_width(self.opset_spin, T.STEPPER_CHARS_NARROW)
        )
        self.opset_spin.valueChanged.connect(self._on_params_changed)
        size_row.addWidget(self.opset_spin)
        size_row.addStretch(1)
        form.addRow("尺寸 / Opset", size_row)
        layout.addLayout(form)

        progress_card, progress_layout = self._card("导出进度")
        self.progress_bar = ProgressBar(progress_card)
        self.progress_bar.setRange(0, 100)
        progress_layout.addWidget(self.progress_bar)
        self.result_label = CaptionLabel("", progress_card)
        self.result_label.setWordWrap(True)
        progress_layout.addWidget(self.result_label)
        self.open_btn = PushButton("打开目录", progress_card)
        self.open_btn.clicked.connect(self._on_open_dir)
        progress_layout.addWidget(self.open_btn)

        return side_column(card, progress_card, width=310)

    # --------------------------------------------------- 中央
    def _build_center(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(T.SPACE_ML)
        column.addWidget(self._build_model_card(), 0)
        column.addWidget(self._build_split_card(), 1)
        column.addWidget(self._build_eval_card(), 1)
        return column

    def _build_model_card(self) -> CardWidget:
        card, layout = self._card("模型概览")
        self.model_name = BodyLabel("—", card)
        layout.addWidget(self.model_name)
        self.model_path = CaptionLabel("", card)
        self.model_path.setWordWrap(True)
        layout.addWidget(self.model_path)

        grid = QGridLayout()
        grid.setSpacing(T.SPACE_ML)
        self.model_tiles = {
            "variant": self._tile(card, grid, 0, 0, "模型变体"),
            "source": self._tile(card, grid, 0, 1, "来源"),
            "imgsz": self._tile(card, grid, 1, 0, "图像尺寸"),
            "device": self._tile(card, grid, 1, 1, "训练方式"),
            "epochs": self._tile(card, grid, 2, 0, "已训练 Epoch"),
            "best": self._tile(card, grid, 2, 1, "最佳指标"),
            "split": self._tile(card, grid, 3, 0, "使用拆分"),
            "size": self._tile(card, grid, 3, 1, "模型大小"),
        }
        layout.addLayout(grid)
        return card

    def _build_split_card(self) -> CardWidget:
        card, layout = self._card("数据拆分")
        self.split_hint = CaptionLabel("", card)
        layout.addWidget(self.split_hint)
        row = QHBoxLayout()
        self.split_pie = PieChart(card)
        self.split_pie.setMinimumHeight(150)
        row.addWidget(self.split_pie, 1)
        self.split_legend = LegendList(card)
        row.addWidget(self.split_legend, 1)
        layout.addLayout(row)
        return card

    def _build_eval_card(self) -> CardWidget:
        card, layout = self._card("评估结果")
        row = QHBoxLayout()
        grid = QGridLayout()
        grid.setSpacing(T.SPACE_ML)
        self.eval_tiles = {
            "wrong": self._tile(card, grid, 0, 0, "错误预测"),
            "correct": self._tile(card, grid, 0, 1, "正确预测"),
            "accuracy": self._tile(card, grid, 1, 0, "准确率"),
            "f1": self._tile(card, grid, 1, 1, "F1 分数"),
            "avg_ms": self._tile(card, grid, 2, 0, "平均推断时间"),
            "total": self._tile(card, grid, 2, 1, "图像总数"),
        }
        holder = QWidget(card)
        holder.setLayout(grid)
        row.addWidget(holder, 1)
        self.eval_pie = PieChart(card)
        self.eval_pie.setMinimumHeight(150)
        row.addWidget(self.eval_pie, 1)
        layout.addLayout(row)
        return card

    # --------------------------------------------------- 右栏
    def _build_actions(self) -> QWidget:
        export_card, export_layout = self._card("导出模型")
        self.inference_check = CheckBox("针对推断进行优化", export_card)
        self.inference_check.setToolTip("固定尺寸 + 图精简")
        self.inference_check.stateChanged.connect(self._on_options_changed)
        export_layout.addWidget(self.inference_check)

        self.api_check = CheckBox("针对 API 接口进行优化", export_card)
        self.api_check.setToolTip("动态尺寸，便于服务端变尺寸输入")
        self.api_check.stateChanged.connect(self._on_options_changed)
        export_layout.addWidget(self.api_check)

        self.half_check = CheckBox("半精度（FP16）", export_card)
        self.half_check.setToolTip(
            "ONNX / TorchScript 可导出 FP16：体积更小、推理更快；"
            "需要 GPU 运行时，CPU 环境会被忽略"
        )
        self.half_check.stateChanged.connect(self._on_options_changed)
        export_layout.addWidget(self.half_check)

        self.export_btn = PrimaryPushButton("导出模型", export_card)
        self.export_btn.setMinimumHeight(36)
        export_layout.addWidget(self.export_btn)
        export_layout.addStretch(1)

        report_card, report_layout = self._card("生成报告")
        self.report_combo = ComboBox(report_card)
        for key, text in _REPORT_FORMATS:
            self.report_combo.addItem(text, userData=key)
        report_layout.addWidget(self.report_combo)
        self.report_btn = PushButton("生成报告", report_card)
        self.report_btn.setMinimumHeight(36)
        report_layout.addWidget(self.report_btn)
        report_layout.addStretch(1)

        history_card, history_layout = self._card("导出记录")
        self.history_table = TableWidget(history_card)
        self.history_table.setColumnCount(4)
        self.history_table.setHorizontalHeaderLabels(["时间", "格式", "大小", "回归"])
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.history_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.history_table.setMinimumHeight(150)
        history_layout.addWidget(self.history_table)

        holder = QWidget(self)
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(T.SPACE_ML)
        column.addWidget(export_card, 0)
        column.addWidget(report_card, 0)
        column.addWidget(history_card, 1)
        holder.setFixedWidth(T.PANEL_W)
        return holder

    # -----------------------------------------------------------
    # 绑定
    # -----------------------------------------------------------
    def _bind(self) -> None:
        self.export_btn.clicked.connect(self._on_export)
        self.report_btn.clicked.connect(self._on_report)
        self.weights_edit.editingFinished.connect(
            lambda: self._vm.set_weights(self.weights_edit.text().strip())
        )
        self.out_edit.editingFinished.connect(
            lambda: self._vm.set_output_dir(self.out_edit.text().strip())
        )
        self._vm.configChanged.connect(self._on_config)
        self._vm.selectionChanged.connect(self._on_selection)
        self._vm.historyChanged.connect(self._on_history)
        self._vm.taskStarted.connect(self._on_task_started)
        self._vm.taskProgress.connect(self._on_task_progress)
        self._vm.taskFinished.connect(self._on_task_finished)
        self._vm.taskFailed.connect(self._on_task_failed)
        self._vm.exportFinished.connect(self._on_export_finished)

    # -----------------------------------------------------------
    # 配置同步
    # -----------------------------------------------------------
    def _on_config(self, config) -> None:
        self._syncing = True
        try:
            if self.weights_edit.text() != config.weights_path:
                self.weights_edit.setText(config.weights_path or "")
            if self.out_edit.text() != config.output_dir:
                self.out_edit.setText(config.output_dir or "")
            self._reload_formats()
            index = self.format_combo.findData(config.format)
            if index >= 0:
                self.format_combo.setCurrentIndex(index)
            self.imgsz_spin.setValue(int(config.imgsz or 640))
            self.opset_spin.setValue(int(config.opset or 12))
            self.inference_check.setChecked(bool(config.for_inference))
            self.api_check.setChecked(bool(config.for_api))
            self.half_check.setChecked(bool(config.half))
            self._reload_records(config.weights_path)
        finally:
            self._syncing = False
        self._on_selection(self._vm.selection())
        self._refresh_overview()
        self._on_history(self._vm.export_history())

    def _reload_records(self, weights: str) -> None:
        """训练记录下拉：项目里每次训练产出的 best.pt。"""
        records = self._vm.records()
        self.record_combo.clear()
        if not records:
            self.record_combo.addItem("暂无训练记录", userData=None)
            self.record_combo.setEnabled(False)
            return
        self.record_combo.setEnabled(True)
        for item in records:
            self.record_combo.addItem(item["label"], userData=item["weights"])
        index = self.record_combo.findData(weights)
        self.record_combo.setCurrentIndex(index if index >= 0 else 0)

    def refresh_splits(self) -> None:
        """拆分列表变化后重取拆分概览（主窗口在 splitsChanged 时调用）。"""
        self._refresh_overview()

    def _refresh_overview(self) -> None:
        """拆分与评估概览。"""
        split = self._vm.split_summary()
        counts = (split or {}).get("counts") or {}
        total = int((split or {}).get("total") or 0)
        self.split_hint.setText(
            f"{split.get('name', '—')} · 共 {total} 张" if split else "尚未拆分"
        )
        self.split_pie.set_data(
            [(name, counts.get(key, 0), SPLIT_COLORS[key])
             for key, name in (("train", "训练"), ("val", "验证"), ("test", "测试"))],
            str(total),
        )
        self.split_legend.set_data([
            (SPLIT_COLORS[key], name,
             f"{counts.get(key, 0)}　{(counts.get(key, 0) / total * 100 if total else 0):.0f}%")
            for key, name in (("train", "训练"), ("val", "验证"), ("test", "测试"))
        ])

        evaluation = self._vm.evaluation()
        if not evaluation.get("available"):
            for label in self.eval_tiles.values():
                label.setText("—")
            self.eval_pie.set_data([], "")
            return
        metrics = evaluation.get("metrics") or {}
        self.eval_tiles["wrong"].setText(str(int(metrics.get("wrong") or 0)))
        self.eval_tiles["correct"].setText(str(int(metrics.get("correct") or 0)))
        self.eval_tiles["accuracy"].setText(
            f"{float(metrics.get('accuracy') or 0) * 100:.2f}%"
        )
        self.eval_tiles["f1"].setText(f"{float(metrics.get('f1') or 0) * 100:.2f}%")
        self.eval_tiles["avg_ms"].setText(f"{float(metrics.get('avg_ms') or 0):.3f} ms")
        self.eval_tiles["total"].setText(str(int(metrics.get("total") or 0)))
        self.eval_pie.set_data(
            [("正确", int(metrics.get("correct") or 0), "#0F7B0F"),
             ("错误", int(metrics.get("wrong") or 0), "#C42B1C")],
            str(int(metrics.get("total") or 0)),
        )

    def _reload_formats(self) -> None:
        """按选中模型的后端重建格式下拉（异常检测只有模型包）。"""
        options = self._vm.export_options()
        signature = "|".join(str(item["key"]) for item in options)
        if not options or getattr(self, "_format_signature", "") == signature:
            return
        previous = str(self.format_combo.currentData() or "")
        # 保留外层的同步标记（本方法可能在外层同步过程中被调用）
        outer_syncing = self._syncing
        self._syncing = True
        try:
            self.format_combo.clear()
            for item in options:
                self.format_combo.addItem(
                    str(item["label"]), userData=str(item["key"])
                )
            index = self.format_combo.findData(previous)
            self.format_combo.setCurrentIndex(max(0, index))
        finally:
            self._syncing = outer_syncing
        self._format_signature = signature

    def _on_selection(self, info: dict) -> None:
        """模型概览随选中模型变化。"""
        if not info:
            return
        self._reload_formats()
        self.model_name.setText(str(info.get("name") or "—"))
        weights = str(info.get("weights") or "")
        self.model_path.setText(weights if weights else "尚未选择模型")
        self.model_tiles["variant"].setText(str(info.get("variant") or "—"))
        self.model_tiles["source"].setText(str(info.get("source") or "—"))
        imgsz = int(info.get("imgsz") or 0)
        self.model_tiles["imgsz"].setText(f"{imgsz}×{imgsz}" if imgsz else "—")
        self.model_tiles["device"].setText(str(info.get("device") or "—"))
        epochs = info.get("epochs") or 0
        self.model_tiles["epochs"].setText(str(epochs) if epochs else "—")
        label = str(info.get("best_label") or "")
        value = float(info.get("best_value") or 0)
        self.model_tiles["best"].setText(
            f"{label} {value:.3f}（Epoch {info.get('best_epoch') or 0}）"
            if label else "—"
        )
        self.model_tiles["split"].setText(str(info.get("split_name") or "—"))
        size = float(info.get("size_mb") or 0)
        self.model_tiles["size"].setText(f"{size:.2f} MB" if size else "—")

    def _on_history(self, history: list) -> None:
        rows = list(history or [])[-6:]
        self.history_table.setRowCount(len(rows))
        for row, item in enumerate(reversed(rows)):
            values = [
                str(item.get("time", ""))[-8:],
                str(item.get("format", "")),
                f"{float(item.get('size') or 0):.2f} MB",
                _regression_label(item),
            ]
            for column, text in enumerate(values):
                self.history_table.setItem(row, column, QTableWidgetItem(text))
            self.history_table.item(row, 0).setToolTip(str(item.get("path", "")))
            self.history_table.item(row, 3).setToolTip(
                str(item.get("regression_detail", ""))
            )

    # -----------------------------------------------------------
    # 交互
    # -----------------------------------------------------------
    def _on_format_changed(self, _index: int = 0) -> None:
        if self._syncing:
            return
        self._vm.set_format(str(self.format_combo.currentData()))

    def _on_params_changed(self, *_args) -> None:
        """尺寸 / Opset 变化（回填配置时同步中则不回写，避免把项目标记为已修改）。"""
        if self._syncing:
            return
        self._vm.set_imgsz(self.imgsz_spin.value())
        self._vm.set_opset(self.opset_spin.value())

    def _on_options_changed(self, *_args) -> None:
        """导出选项变化；「推断优化」与「API 优化」互斥（与 DLT 一致）。"""
        if self._syncing:
            return
        if self.inference_check.isChecked() and self.api_check.isChecked():
            sender = self.sender()
            target = (
                self.inference_check if sender is self.api_check else self.api_check
            )
            target.setChecked(False)
            return
        self._vm.set_for_inference(self.inference_check.isChecked())
        self._vm.set_for_api(self.api_check.isChecked())
        self._vm.set_half(self.half_check.isChecked())

    def _on_record_picked(self, index: int) -> None:
        if self._syncing or index < 0:
            return
        if not self.record_combo.isEnabled():
            return
        if self._vm.select_record(index):
            self.weights_edit.setText(self._vm.config.weights_path)
            self._refresh_overview()

    def _on_browse_weights(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择模型权重", "", "PyTorch Weight (*.pt)"
        )
        if path:
            self.weights_edit.setText(path)
            self._vm.set_weights(path)
            self._refresh_overview()

    def _on_browse_out(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if directory:
            self.out_edit.setText(directory)
            self._vm.set_output_dir(directory)

    def _on_export(self) -> None:
        self._vm.set_weights(self.weights_edit.text().strip())
        self._vm.set_format(str(self.format_combo.currentData()))
        self._vm.set_output_dir(self.out_edit.text().strip())
        self._vm.export()

    def _on_report(self) -> None:
        fmt = str(self.report_combo.currentData() or "html")
        suffix = "md" if fmt == "md" else "html"
        project = self._vm.project
        name = f"{getattr(project, 'name', '') or 'model'}报告.{suffix}"
        filters = "Markdown (*.md)" if fmt == "md" else "网页报告 (*.html)"
        path, _ = QFileDialog.getSaveFileName(self, "生成报告", name, filters)
        if path:
            self._vm.build_report(path)

    def _on_open_dir(self) -> None:
        """打开最近一次导出所在的目录。"""
        latest = self._vm.latest_export()
        target = str(latest.get("path") or "")
        directory = Path(target).parent if target else Path(
            self._vm.config.output_dir or "."
        )
        if directory.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    # -----------------------------------------------------------
    # 任务状态
    # -----------------------------------------------------------
    def _on_task_started(self, name: str) -> None:
        self.progress_bar.setValue(0)
        self.result_label.setText(f"{name}…")
        self.export_btn.setEnabled(False)

    def _on_task_progress(self, percent: int, text: str) -> None:
        self.progress_bar.setValue(percent)
        if text:
            self.result_label.setText(text)

    def _on_task_finished(self, text: str) -> None:
        self.progress_bar.setValue(100)
        self.export_btn.setEnabled(True)

    def _on_task_failed(self, text: str) -> None:
        self.result_label.setText(text)
        self.export_btn.setEnabled(True)

    def _on_export_finished(self, path: str) -> None:
        self.result_label.setText(f"已导出：{Path(path).name}")
        self._refresh_overview()
