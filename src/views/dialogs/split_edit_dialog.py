"""拆分创建 / 编辑弹窗（参照 Halcon DLT 的创建拆分 / 编辑拆分）。

    SplitEditDialog  名称 + 环形图 +「图像数 / 百分比」联动表 + 自定义种子
    + 分层抽样；创建 / 编辑共用，按钮组按模式切换（左 → 右）：

    创建：取消 / 创建拆分并执行分配
    编辑：取消 / 保存拆分并重新分配

**异常检测项目走单独的分配表**（`anomaly=` 给出时）：训练只用良好图像，
所以按类别分成两组——「良好图像 (n)」与「异常图像 (m)」，每组的训练 / 验证 /
测试各自「数量 ↔ 百分比」联动；异常图的训练格固定 0 并禁用（约束用控件可用性
表达，说明走 tooltip）。环形图按**合并口径**预览（训练 / 验证 / 测试）。

联动规则与拆分页一致：**改动项精确定值**，同一类别内其余项按各自当前值比例
吸收剩余（数量模式合计 = 该类总数、百分比模式合计 = 100）；输出比例以
数量口径为准（数量 / 总数）——「设 100 张」划分即 100 张。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
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
    StrongBodyLabel,
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

# 异常检测的分组标题（类型键 → 显示名）
_KIND_LABELS = {"normal": "良好图像", "abnormal": "异常图像"}


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
        anomaly: dict | None = None,
    ):
        """`anomaly` 给出时走**异常检测的按类别分配表**。

        Args:
            anomaly: ``{"totals": {"normal": n, "abnormal": m},
                "plan": {kind: {子集: 数量}}}``——totals 决定每组的分母，
                plan 为初值（缺省时按 `ratios` 建议）。
        """
        super().__init__(parent)
        self._mode = mode
        self._total = max(0, int(total))
        self._syncing = False
        self._choice = CANCEL
        self._anomaly = anomaly or None
        self._anomaly_totals: dict[str, int] = dict(
            (self._anomaly or {}).get("totals") or {}
        )
        self._anomaly_plan: dict[str, dict[str, int]] = {
            kind: {sub: max(0, int(value)) for sub, value in (rows or {}).items()}
            for kind, rows in ((self._anomaly or {}).get("plan") or {}).items()
        }

        self.widget.setMinimumWidth(
            T.CTRL_W_XL * (4 if self._anomaly else 3)
        )
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
        body.addLayout(
            self._build_anomaly_table() if self._anomaly else self._build_table(), 1
        )
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

        # 分层抽样只对「按比例 + 分层」的常规拆分有意义：异常检测按类别分配
        # （服务端 `_allocate_by_kind` 不看 stratified），故不显示该勾选
        self.stratified_check = CheckBox("按类别分层抽样（推荐）", self)
        self.stratified_check.setChecked(bool(stratified))
        self.stratified_check.setVisible(not self._anomaly)
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

        if self._anomaly:
            # 已保存的分配优先（编辑既有拆分）；没有才按比例给建议值
            self._apply_anomaly_values(
                self._anomaly_plan or self._suggest_plan(tuple(ratios))
            )
        else:
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
    # 异常检测：良好 / 异常两组分配表
    # -----------------------------------------------------------
    def _build_anomaly_table(self) -> QGridLayout:
        """两组列：「良好图像 (n)」与「异常图像 (m)」各带数量 / 百分比。

        训练只用良好图像（异常图的训练格固定 0 并禁用，约束用控件可用性表达）；
        两组之间一条竖分隔线，行的名称列与常规弹窗一致（色块 + 训练 / 验证 /
        测试）。
        """
        table = QGridLayout()
        table.setHorizontalSpacing(T.SPACE_LG)
        table.setVerticalSpacing(T.SPACE_SM)

        labeled = int(self._anomaly_totals.get("labeled", 0))
        known = int(self._anomaly_totals.get("all", 0))
        hint = CaptionLabel(f"已标注 / 全部：{labeled} / {known}", self)
        hint.setToolTip(
            "只有标注了「良好 / 异常」的图片参与分配：在导入弹窗里按文件夹语义"
            "选择，或在标注页修改类别类型"
        )
        table.addWidget(hint, 0, 0, 1, 6)

        self.anomaly_count_spins: dict[tuple, SafeCompactSpinBox] = {}
        self.anomaly_percent_spins: dict[tuple, SafeCompactSpinBox] = {}
        for index, kind in enumerate(("normal", "abnormal")):
            column = 1 + index * 3            # 数量列（百分比列 = column + 1）
            title = StrongBodyLabel(
                f"{_KIND_LABELS[kind]}（{int(self._anomaly_totals.get(kind, 0))}）",
                self,
            )
            table.addWidget(
                title, 1, column, 1, 2, Qt.AlignmentFlag.AlignHCenter
            )
            for offset, text in ((0, "数量"), (1, "百分比")):
                head = CaptionLabel(text, self)
                head.setAlignment(
                    Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
                )
                if offset == 1:
                    head.setToolTip("按该类图片数折算，改一项其余项按比例吸收")
                table.addWidget(head, 2, column + offset)

        # 两组之间的竖分隔线（颜色走令牌，明暗主题都可见）
        line = QFrame(self)
        line.setFrameShape(QFrame.Shape.VLine)
        line.setStyleSheet(f"color: {T.GROUP_BORDER};")
        table.addWidget(line, 1, 4, 4, 1)

        for row, sub in enumerate(("train", "val", "test"), start=3):
            holder = QWidget(self)
            name_row = QHBoxLayout(holder)
            name_row.setContentsMargins(0, 0, 0, 0)
            name_row.setSpacing(T.SPACE_MD)
            chip = QLabel(holder)
            chip.setFixedSize(12, 12)
            chip.setStyleSheet(
                f"background: {SPLIT_COLORS[sub]}; border-radius: 2px;"
            )
            name_row.addWidget(chip)
            name_row.addWidget(BodyLabel(SPLIT_LABELS[sub], holder))
            name_row.addStretch(1)
            table.addWidget(holder, row, 0)

            for index, kind in enumerate(("normal", "abnormal")):
                column = 1 + index * 3
                count = SafeCompactSpinBox(self)
                count.setRange(0, 999999)
                count.setFixedWidth(T.field_width(count, T.STEPPER_CHARS_NARROW))
                count.valueChanged.connect(
                    lambda _v, k=kind, s=sub: self._on_anomaly_edited(k, s, "count")
                )
                table.addWidget(count, row, column, Qt.AlignmentFlag.AlignHCenter)

                percent = SafeCompactSpinBox(self)
                percent.setRange(0, 100)
                percent.setSuffix(" %")
                percent.setFixedWidth(
                    T.field_width(percent, T.STEPPER_CHARS_NARROW)
                )
                percent.valueChanged.connect(
                    lambda _v, k=kind, s=sub: self._on_anomaly_edited(
                        k, s, "percent"
                    )
                )
                table.addWidget(
                    percent, row, column + 1, Qt.AlignmentFlag.AlignHCenter
                )
                if kind == "abnormal" and sub == "train":
                    count.setToolTip("异常图像不参与训练：只进验证 / 测试")
                    percent.setToolTip("异常图像不参与训练：只进验证 / 测试")
                self.anomaly_count_spins[(kind, sub)] = count
                self.anomaly_percent_spins[(kind, sub)] = percent
        return table

    def _suggest_plan(self, ratios: tuple) -> dict[str, dict[str, int]]:
        """按比例给一份建议分配（异常图训练恒 0，按剩余比例归一化）。

        权重取**千分整数**（`_proportional_sizes` 内部 `int()` 取整，浮点比例
        会被归零——历史故障）。
        """
        plan: dict[str, dict[str, int]] = {}
        for kind in ("normal", "abnormal"):
            total = int(self._anomaly_totals.get(kind, 0))
            weights = {
                sub: max(0, int(round(float(ratios[index]) * 1000)))
                for index, sub in enumerate(("train", "val", "test"))
            }
            if kind == "abnormal":
                weights["train"] = 0
            sizes = DatasetService._proportional_sizes(total, weights)
            plan[kind] = {sub: int(sizes.get(sub, 0)) for sub in weights}
        return plan

    def _apply_anomaly_values(self, plan: dict) -> None:
        """把分配写回两组输入框（屏蔽信号）并刷新环形图。"""
        self._syncing = True
        try:
            for kind in ("normal", "abnormal"):
                rows = plan.get(kind) or {}
                total = int(self._anomaly_totals.get(kind, 0))
                for sub in ("train", "val", "test"):
                    value = max(0, int(rows.get(sub, 0)))
                    self.anomaly_count_spins[(kind, sub)].setValue(value)
                    self.anomaly_percent_spins[(kind, sub)].setValue(
                        int(round(value * 100 / total)) if total else 0
                    )
                    self._set_anomaly_enabled(kind, sub, total)
        finally:
            self._syncing = False
        self._anomaly_plan = {
            kind: {
                sub: int(self.anomaly_count_spins[(kind, sub)].value())
                for sub in ("train", "val", "test")
            }
            for kind in ("normal", "abnormal")
        }
        self._refresh_anomaly_pie()

    def _set_anomaly_enabled(self, kind: str, sub: str, total: int) -> None:
        """可编辑性：异常图的训练格恒禁用；该类没有图片时整组禁用。"""
        editable = total > 0 and not (kind == "abnormal" and sub == "train")
        self.anomaly_count_spins[(kind, sub)].setEnabled(editable)
        self.anomaly_percent_spins[(kind, sub)].setEnabled(editable)

    def _refresh_anomaly_pie(self) -> None:
        """环形图按**合并口径**预览（训练 / 验证 / 测试）。"""
        combined = {
            sub: sum(
                int(self._anomaly_plan.get(kind, {}).get(sub, 0))
                for kind in ("normal", "abnormal")
            )
            for sub in ("train", "val", "test")
        }
        segments = [
            (SPLIT_LABELS[sub], combined[sub], SPLIT_COLORS[sub])
            for sub in ("train", "val", "test")
        ]
        total = sum(combined.values()) or self._total
        self.pie.set_data(segments, str(total))

    def _rebalance_kind(self, kind: str, changed: str, value: int) -> dict:
        """改一格后重排**同类别**其余行，使行和恒等于该类图片数。"""
        total = int(self._anomaly_totals.get(kind, 0))
        if kind == "abnormal" and changed == "train":
            value = 0
        value = max(0, min(total, int(value)))
        others = [sub for sub in ("train", "val", "test") if sub != changed]
        if kind == "abnormal":
            others = [sub for sub in others if sub != "train"]
        current = self._anomaly_plan.get(kind) or {}
        weights = {sub: max(0, int(current.get(sub, 0))) for sub in others}
        if not any(weights.values()):
            weights = {sub: 1 for sub in others}
        sizes = DatasetService._proportional_sizes(total - value, weights)
        rows = {sub: 0 for sub in ("train", "val", "test")}
        rows[changed] = value
        rows.update({sub: int(sizes.get(sub, 0)) for sub in others})
        return rows

    def _on_anomaly_edited(self, kind: str, sub: str, mode: str) -> None:
        """改数量或百分比：同类别其余行按比例吸收差额后写回。"""
        if self._syncing:
            return
        total = int(self._anomaly_totals.get(kind, 0))
        if mode == "count":
            value = int(self.anomaly_count_spins[(kind, sub)].value())
        else:
            percent = int(self.anomaly_percent_spins[(kind, sub)].value())
            value = int(round(total * percent / 100))
        plan = {k: dict(rows) for k, rows in self._anomaly_plan.items()}
        plan[kind] = self._rebalance_kind(kind, sub, value)
        self._apply_anomaly_values(plan)

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
        """弹窗内容：名称 / 比例（数量口径）/ 数量 / 种子（None = 不改）/ 分层。

        异常检测模式额外给出 ``anomaly_counts``（按类别的分配）；比例按**合并**
        口径折算（真正落库时 `DatasetViewModel.set_anomaly_allocation` 会再按
        分配结果重算比例）。
        """
        data = {
            "name": self.name_edit.text().strip(),
            "seed": (
                int(self.seed_spin.value()) if self.seed_check.isChecked()
                else None
            ),
            "stratified": bool(self.stratified_check.isChecked()),
        }
        if self._anomaly:
            plan = {
                kind: {
                    sub: int(self.anomaly_count_spins[(kind, sub)].value())
                    for sub in ("train", "val", "test")
                }
                for kind in ("normal", "abnormal")
            }
            combined = {
                sub: sum(plan[kind][sub] for kind in ("normal", "abnormal"))
                for sub in ("train", "val", "test")
            }
            total = sum(combined.values()) or self._total
            data.update({
                "anomaly_counts": plan,
                "counts": combined,
                "percents": {
                    sub: int(round(combined[sub] * 100 / total)) if total else 0
                    for sub in ("train", "val", "test")
                },
                "ratios": tuple(
                    combined[sub] / total if total else 0.0
                    for sub in ("train", "val", "test")
                ),
            })
            return data
        keys = ("train", "val", "test")
        counts = {key: int(self.count_spins[key].value()) for key in keys}
        percents = {key: int(self.ratio_spins[key].value()) for key in keys}
        total = self._total
        if total > 0:
            ratios = tuple(counts[key] / total for key in keys)
        else:
            ratios = tuple(percents[key] / 100 for key in keys)
        data.update({
            "ratios": ratios, "counts": counts, "percents": percents,
        })
        return data

    def _on_primary(self) -> None:
        # accept 由 MessageBoxBase 处理，这里只记录选择
        self._choice = (
            CREATE_APPLY if self._mode == MODE_CREATE else SAVE_APPLY
        )
