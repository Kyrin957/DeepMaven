"""拆分创建 / 编辑弹窗（参照 Halcon DLT 的创建拆分 / 编辑拆分）。

    SplitEditDialog  名称 + 环形图 +「图像数 / 百分比」联动表 + 自定义种子
    + 分层抽样；创建 / 编辑共用，按钮组按模式切换（左 → 右）：

    创建：取消 / 创建拆分并执行分配
    编辑：取消 / 保存拆分并重新分配

联动规则与拆分页一致：**改动项精确定值**，其余两项按各自当前值比例
吸收剩余（数量模式合计 = 总数、百分比模式合计 = 100）；输出比例以
数量口径为准（数量 / 总数）——「设 100 张」划分即 100 张。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    LineEdit,
    MessageBoxBase,
    SubtitleLabel,
)

from src.services.dataset_service import DatasetService
from src.utils.constants import SPLIT_COLORS, SPLIT_LABELS
from src.views.ui import SafeCompactSpinBox
from src.views.ui import tokens as T
from src.views.widgets import PieChart

# 弹窗模式
MODE_CREATE = "create"
MODE_EDIT = "edit"

# 返回值（"" = 取消）
CANCEL = ""
CREATE_APPLY = "create_apply"
SAVE_APPLY = "save_apply"


class SplitEditDialog(MessageBoxBase):
    """拆分创建 / 编辑弹窗。"""

    def __init__(
        self,
        parent=None,
        *,
        mode: str = MODE_CREATE,
        name: str = "",
        total: int = 0,
        ratios: tuple = (0.7, 0.2, 0.1),
        seed: int = 0,
        stratified: bool = True,
    ):
        super().__init__(parent)
        self._mode = mode
        self._total = max(0, int(total))
        self._syncing = False
        self._choice = CANCEL

        self.widget.setMinimumWidth(T.CTRL_W_XL * 3)
        self.titleLabel = SubtitleLabel(
            "创建拆分" if mode == MODE_CREATE else "编辑拆分", self
        )
        self.viewLayout.addWidget(self.titleLabel)

        self.name_edit = LineEdit(self)
        self.name_edit.setText(str(name))
        self.name_edit.setPlaceholderText("拆分名称")
        self.viewLayout.addWidget(self.name_edit)

        # 环形图（随联动实时预览）+ 三行「图像数 / 百分比」
        body = QHBoxLayout()
        body.setSpacing(T.SPACE_XL)
        self.pie = PieChart(self)
        self.pie.setFixedWidth(T.CTRL_W_XL)
        body.addWidget(self.pie, 0, Qt.AlignmentFlag.AlignTop)
        body.addLayout(self._build_table(), 1)
        self.viewLayout.addLayout(body)

        # 自定义随机种子（勾选后才生效；不勾选 = 保持现有 / 默认 0）
        self.seed_check = CheckBox("设置一个用户自定义的随机种子", self)
        self.seed_spin = SafeCompactSpinBox(self)
        self.seed_spin.setRange(0, 99999)
        self.seed_spin.setValue(int(seed))
        self.seed_spin.setFixedWidth(
            T.field_width(self.seed_spin, T.STEPPER_CHARS_NARROW)
        )
        self.seed_spin.setEnabled(False)
        self.seed_check.stateChanged.connect(
            lambda _state: self.seed_spin.setEnabled(self.seed_check.isChecked())
        )
        seed_row = QHBoxLayout()
        seed_row.setSpacing(T.SPACE_MD)
        seed_row.addWidget(self.seed_check)
        seed_row.addWidget(self.seed_spin)
        seed_row.addStretch(1)
        self.viewLayout.addLayout(seed_row)

        self.stratified_check = CheckBox("按类别分层抽样（推荐）", self)
        self.stratified_check.setChecked(bool(stratified))
        self.viewLayout.addWidget(self.stratified_check)

        # 按钮组仅两个（左 → 右）：取消 / 确认——确认即执行拆分
        self.yesButton.setText(
            "创建拆分并执行分配" if mode == MODE_CREATE else "保存拆分并重新分配"
        )
        self.cancelButton.setText("取消")
        self.yesButton.clicked.connect(self._on_primary)
        # MessageBoxBase 默认确认在左、取消在右——对调为常规的「取消在左、确认在右」；
        # 重新插入时保留 stretch=1 / 垂直居中（与另一个按钮等大，见 unsaved_dialog）
        self.buttonLayout.removeWidget(self.cancelButton)
        self.buttonLayout.insertWidget(
            0, self.cancelButton, 1, Qt.AlignmentFlag.AlignVCenter
        )

        self._apply_ratios(tuple(ratios))

    # -----------------------------------------------------------
    # 表格
    # -----------------------------------------------------------
    def _build_table(self) -> QGridLayout:
        """三行「色块 + 名称 + 图像数 + 百分比」；列头与输入框居中对齐。"""
        table = QGridLayout()
        table.setHorizontalSpacing(T.SPACE_XL)
        table.setVerticalSpacing(T.SPACE_SM)

        head_count = CaptionLabel("图像数", self)
        head_count.setToolTip("分层抽样会按类别微调实际张数")
        head_count.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
        )
        head_percent = CaptionLabel("百分比", self)
        head_percent.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
        )
        table.addWidget(head_count, 0, 1)
        table.addWidget(head_percent, 0, 2)

        self.ratio_spins: dict[str, SafeCompactSpinBox] = {}
        self.count_spins: dict[str, SafeCompactSpinBox] = {}
        for row, key in enumerate(("train", "val", "test"), start=1):
            holder = QWidget(self)
            name_row = QHBoxLayout(holder)
            name_row.setContentsMargins(0, 0, 0, 0)
            name_row.setSpacing(T.SPACE_MD)
            chip = QLabel(holder)
            chip.setFixedSize(12, 12)
            chip.setStyleSheet(
                f"background: {SPLIT_COLORS[key]}; border-radius: 2px;"
            )
            name_row.addWidget(chip)
            name_row.addWidget(BodyLabel(SPLIT_LABELS[key], holder))
            name_row.addStretch(1)
            table.addWidget(holder, row, 0)

            count = SafeCompactSpinBox(self)
            count.setRange(0, 999999)
            count.setFixedWidth(
                T.field_width(count, T.STEPPER_CHARS_NARROW)
            )
            count.valueChanged.connect(
                lambda _v, k=key: self._on_ratio_edited(k, "count")
            )
            table.addWidget(count, row, 1, Qt.AlignmentFlag.AlignHCenter)

            percent = SafeCompactSpinBox(self)
            percent.setRange(0, 100)
            percent.setSuffix(" %")
            percent.setFixedWidth(
                T.field_width(percent, T.STEPPER_CHARS_NARROW)
            )
            percent.valueChanged.connect(
                lambda _v, k=key: self._on_ratio_edited(k, "percent")
            )
            table.addWidget(percent, row, 2, Qt.AlignmentFlag.AlignHCenter)

            self.ratio_spins[key] = percent
            self.count_spins[key] = count
        return table

    # -----------------------------------------------------------
    # 联动
    # -----------------------------------------------------------
    def _on_ratio_edited(self, changed: str, mode: str) -> None:
        """图像数 / 百分比联动：改动项精确定值，其余两项吸收剩余。"""
        if self._syncing:
            return
        keys = ("train", "val", "test")
        total = self._total
        others = [key for key in keys if key != changed]
        if mode == "count" and total > 0:
            count = max(0, min(total, int(self.count_spins[changed].value())))
            counts = {changed: count}
            weights = {
                key: max(0, int(self.count_spins[key].value())) for key in others
            }
            if not any(weights.values()):
                weights = {key: 1 for key in others}
            sizes = DatasetService._proportional_sizes(total - count, weights)
            counts.update({key: int(sizes.get(key, 0)) for key in others})
            percents = {
                key: int(round(counts[key] * 100 / total)) for key in keys
            }
        else:
            percent = max(0, min(100, int(self.ratio_spins[changed].value())))
            percents = {changed: percent}
            weights = {
                key: max(0, int(self.ratio_spins[key].value()))
                for key in others
            }
            if not any(weights.values()):
                weights = {key: 1 for key in others}
            sizes = DatasetService._proportional_sizes(100 - percent, weights)
            percents.update({key: int(sizes.get(key, 0)) for key in others})
            counts = {
                key: (int(round(percents[key] * total / 100)) if total else 0)
                for key in keys
            }
            if total > 0:
                # 数量合计对齐总数：余数补到改动项
                counts[changed] = max(
                    0, counts[changed] + total - sum(counts.values())
                )
        self._apply_values(percents, counts)

    def _apply_ratios(self, ratios: tuple) -> None:
        """按比例初值回写两组输入框（数量合计对齐总数）。"""
        keys = ("train", "val", "test")
        percents = {
            key: int(round(float(ratios[index]) * 100))
            for index, key in enumerate(keys)
        }
        total = self._total
        counts = {
            key: (int(round(percents[key] * total / 100)) if total else 0)
            for key in keys
        }
        if total > 0:
            counts["train"] = max(
                0, counts["train"] + total - sum(counts.values())
            )
        self._apply_values(percents, counts)

    def _apply_values(self, percents: dict, counts: dict) -> None:
        """回写两组输入框并刷新环形图预览。"""
        self._syncing = True
        try:
            for key in ("train", "val", "test"):
                self.ratio_spins[key].setValue(int(percents.get(key, 0)))
                self.count_spins[key].setValue(int(counts.get(key, 0)))
        finally:
            self._syncing = False
        segments = [
            (SPLIT_LABELS[key], int(counts.get(key, 0)), SPLIT_COLORS[key])
            for key in ("train", "val", "test")
        ]
        total = self._total or sum(value for _name, value, _color in segments)
        self.pie.set_data(segments, str(total))

    # -----------------------------------------------------------
    # 结果
    # -----------------------------------------------------------
    def exec_choice(self) -> str:
        """弹出并返回用户选择（``""`` = 取消）。"""
        self._choice = CANCEL
        if self.exec():
            return self._choice
        return CANCEL

    def values(self) -> dict:
        """弹窗内容：名称 / 比例（数量口径）/ 数量 / 种子（None = 不改）/ 分层。"""
        keys = ("train", "val", "test")
        counts = {key: int(self.count_spins[key].value()) for key in keys}
        percents = {key: int(self.ratio_spins[key].value()) for key in keys}
        total = self._total
        if total > 0:
            ratios = tuple(counts[key] / total for key in keys)
        else:
            ratios = tuple(percents[key] / 100 for key in keys)
        return {
            "name": self.name_edit.text().strip(),
            "ratios": ratios,
            "counts": counts,
            "percents": percents,
            "seed": (
                int(self.seed_spin.value()) if self.seed_check.isChecked()
                else None
            ),
            "stratified": bool(self.stratified_check.isChecked()),
        }

    def _on_primary(self) -> None:
        # accept 由 MessageBoxBase 处理，这里只记录选择
        self._choice = (
            CREATE_APPLY if self._mode == MODE_CREATE else SAVE_APPLY
        )
