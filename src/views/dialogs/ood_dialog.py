"""OOD 检测弹窗：用「已知 / 正常」样本拟合分布，再判定待测图片。

参照 Halcon DLT 的分布外检测思路——先拿训练集（或正常样本目录）拟合特征分布，
再对可疑图片给出距离，超过阈值的判为分布外，用于拦截模型没见过的图。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QListWidget,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CaptionLabel,
    LineEdit,
    MessageBoxBase,
    PushButton,
    SubtitleLabel,
)

from src.services.ood_service import DEFAULT_PERCENTILE, OodService, OodStats
from src.views.ui import SafeSpinBox
from src.views.ui import tokens as T

_IMAGE_FILTER = "图片 (*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp)"


class OodDialog(MessageBoxBase):
    """OOD 检测弹窗。

    用法：
        OodDialog(parent, weights="best.pt").exec()
    """

    def __init__(self, parent=None, weights: str = ""):
        super().__init__(parent)
        self._service: OodService | None = None
        self._stats: OodStats | None = None

        self.titleLabel = SubtitleLabel("OOD 检测（分布外样本）", self)
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self._build_body(weights))

        self.hint = CaptionLabel(
            "先用「已知样本」拟合分布，再对可疑图片判定；距离超过阈值即判为分布外",
            self,
        )
        self.hint.setWordWrap(True)
        self.viewLayout.addWidget(self.hint)

        self.result_list = QListWidget(self)
        self.result_list.setMinimumHeight(160)
        self.viewLayout.addWidget(self.result_list)

        self.widget.setMinimumWidth(580)
        self.yesButton.setVisible(False)
        self.cancelButton.setText("关闭")

    # -----------------------------------------------------------
    def _build_body(self, weights: str) -> QWidget:
        from PySide6.QtWidgets import QFileDialog

        body = QWidget(self)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(T.SPACE_MD)
        form = QFormLayout()
        form.setSpacing(T.SPACE_MD)

        self.weights_edit = LineEdit(body)
        self.weights_edit.setPlaceholderText("分类模型权重 (.pt)")
        self.weights_edit.setText(str(weights or ""))
        row = QHBoxLayout()
        row.addWidget(self.weights_edit, 1)
        browse = PushButton("浏览", body)
        browse.clicked.connect(lambda: self._pick_file(self.weights_edit))
        row.addWidget(browse)
        form.addRow("模型权重", row)

        self.known_edit = LineEdit(body)
        self.known_edit.setPlaceholderText("已知 / 正常样本所在目录")
        row = QHBoxLayout()
        row.addWidget(self.known_edit, 1)
        browse = PushButton("浏览", body)
        browse.clicked.connect(lambda: self._pick_dir(self.known_edit))
        row.addWidget(browse)
        form.addRow("已知样本", row)

        self.target_edit = LineEdit(body)
        self.target_edit.setPlaceholderText("待测图片路径，或包含图片的目录")
        row = QHBoxLayout()
        row.addWidget(self.target_edit, 1)
        file_btn = PushButton("图片", body)
        file_btn.clicked.connect(lambda: self._pick_file(self.target_edit, False))
        row.addWidget(file_btn)
        dir_btn = PushButton("目录", body)
        dir_btn.clicked.connect(lambda: self._pick_dir(self.target_edit))
        row.addWidget(dir_btn)
        form.addRow("待测", row)

        self.percentile_spin = SafeSpinBox(body)
        self.percentile_spin.setRange(50, 100)
        self.percentile_spin.setSuffix(" 分位")
        self.percentile_spin.setValue(int(DEFAULT_PERCENTILE))
        self.percentile_spin.setToolTip("阈值取「已知样本距离」的分位数，越大越宽松")
        self.percentile_spin.setFixedWidth(
            T.field_width(self.percentile_spin, T.STEPPER_CHARS_NARROW)
        )
        form.addRow("阈值", self.percentile_spin)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        self.fit_btn = PushButton("拟合分布", body)
        self.fit_btn.clicked.connect(self._on_fit)
        buttons.addWidget(self.fit_btn)
        self.check_btn = PushButton("判定", body)
        self.check_btn.clicked.connect(self._on_check)
        buttons.addWidget(self.check_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return body

    # -----------------------------------------------------------
    def _pick_file(self, target, weight: bool = True) -> None:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self, "选择文件", "",
            "模型权重 (*.pt)" if weight else _IMAGE_FILTER,
        )
        if path:
            target.setText(path)

    def _pick_dir(self, target) -> None:
        from PySide6.QtWidgets import QFileDialog

        directory = QFileDialog.getExistingDirectory(self, "选择目录")
        if directory:
            target.setText(directory)

    def _service_for(self) -> OodService | None:
        weights = self.weights_edit.text().strip()
        if not weights:
            self.hint.setText("请先选择分类模型权重")
            return None
        if not OodService.is_available():
            self.hint.setText("未安装 Ultralytics，无法使用 OOD 检测")
            return None
        if not OodService.supported(weights):
            self.hint.setText(
                "OOD 检测目前只支持分类模型（检测 / 分割没有可直接取用的分类头）"
            )
            return None
        if self._service is None or self._service.weights != weights:
            self._service = OodService(weights=weights)
            self._stats = None
        return self._service

    def _on_fit(self) -> None:
        service = self._service_for()
        if service is None:
            return
        directory = self.known_edit.text().strip()
        if not directory:
            self.hint.setText("请选择「已知样本」目录")
            return
        self.fit_btn.setEnabled(False)
        try:
            self._stats = service.fit(
                directory, percentile=self.percentile_spin.value()
            )
        except ValueError as exc:
            self.hint.setText(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - 模型侧异常统一提示
            self.hint.setText(f"拟合失败：{exc}")
            return
        finally:
            self.fit_btn.setEnabled(True)
        self.hint.setText(
            f"已用 {self._stats.samples} 张样本拟合（{self._stats.dim} 维特征，"
            f"阈值 {self._stats.threshold:.3f}）"
        )

    def _on_check(self) -> None:
        service = self._service_for()
        if service is None:
            return
        if self._stats is None:
            self.hint.setText("请先拟合分布")
            return
        raw = self.target_edit.text().strip()
        if not raw:
            self.hint.setText("请选择待测图片或目录")
            return
        target = Path(raw)
        if target.is_dir():
            paths = [
                path for path in sorted(target.glob("*"))
                if path.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp",
                                           ".tif", ".tiff", ".webp")
            ]
        else:
            paths = [target]
        if not paths:
            self.hint.setText("待测路径里没有图片")
            return
        self.result_list.clear()
        try:
            results = service.predict([str(path) for path in paths], self._stats)
        except Exception as exc:  # noqa: BLE001
            self.hint.setText(f"判定失败：{exc}")
            return
        flagged = 0
        for item in results:
            if item.get("error"):
                text = f"{item.get('name')} · 失败：{item.get('error')}"
            else:
                verdict = "分布外 (OOD)" if item.get("ood") else "分布内"
                text = f"{item.get('name')} · 距离 {item.get('distance'):.3f} · {verdict}"
                flagged += 1 if item.get("ood") else 0
            entry = self.result_list.addItem(text)
        self.hint.setText(
            f"共判定 {len(results)} 张（阈值 {self._stats.threshold:.3f}），"
            f"其中 {flagged} 张判为分布外"
        )
