"""偏好设置对话框（跨会话持久化，参照 DLT 的 Preferences）。

改动在点「确定」后一次性写入 `ConfigManager`，并广播 `preferencesChanged`，
各页面据此应用（十字准线、滚轮方向、默认线程数、默认亮度 / 对比度等）。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    LineEdit,
    MessageBoxBase,
    PushButton,
    Slider,
    SpinBox,
    SubtitleLabel,
)

from src.utils.config import ConfigManager


class PreferencesDialog(MessageBoxBase):
    """应用偏好设置。"""

    def __init__(self, config: ConfigManager, parent=None):
        super().__init__(parent)
        self._config = config
        self.titleLabel = SubtitleLabel("偏好设置", self)
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self._build_body())

        self.hint = CaptionLabel("", self)
        self.viewLayout.addWidget(self.hint)
        self.yesButton.setText("确定")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(560)

    # -----------------------------------------------------------
    # 表单
    # -----------------------------------------------------------
    def _build_body(self) -> QWidget:
        body = QWidget(self)
        outer = QVBoxLayout(body)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        form = QFormLayout()
        form.setSpacing(8)

        # 默认项目目录
        row = QHBoxLayout()
        self.dir_edit = LineEdit(body)
        self.dir_edit.setText(self._config.default_project_dir)
        row.addWidget(self.dir_edit, 1)
        browse = PushButton("浏览", body)
        browse.clicked.connect(self._on_browse_dir)
        row.addWidget(browse)
        form.addRow("默认项目目录", row)

        self.recent_spin = SpinBox(body)
        self.recent_spin.setRange(0, 50)
        self.recent_spin.setValue(self._config.max_recent)
        form.addRow("最近项目数量", self.recent_spin)

        self.open_last_check = QCheckBox("启动时打开最近项目", body)
        self.open_last_check.setChecked(self._config.open_last_project)
        form.addRow("", self.open_last_check)

        self.threads_spin = SpinBox(body)
        self.threads_spin.setRange(1, 64)
        self.threads_spin.setValue(self._config.cpu_threads)
        form.addRow("训练 CPU 线程数", self.threads_spin)

        self.wheel_check = QCheckBox("滚轮反向缩放", body)
        self.wheel_check.setChecked(self._config.wheel_inverted)
        form.addRow("", self.wheel_check)

        self.crosshair_check = QCheckBox("显示标注十字准线", body)
        self.crosshair_check.setChecked(self._config.crosshair)
        form.addRow("", self.crosshair_check)

        self.crosshair_slider, self.crosshair_value = self._slider_row(
            body, form, "十字准线不透明度", 15, 100,
            self._config.crosshair_opacity, "%",
        )
        self.region_slider, self.region_value = self._slider_row(
            body, form, "标注区域不透明度", 0, 100,
            self._config.region_opacity, "%",
        )
        self.brightness_slider, self.brightness_value = self._slider_row(
            body, form, "图像默认亮度", -100, 100,
            self._config.default_brightness, "",
        )
        self.contrast_slider, self.contrast_value = self._slider_row(
            body, form, "图像默认对比度", -100, 100,
            self._config.default_contrast, "",
        )

        self.pixel_check = QCheckBox("按住 Shift 显示像素值", body)
        self.pixel_check.setChecked(self._config.show_pixel_value)
        form.addRow("", self.pixel_check)

        self.autosave_spin = QSpinBox(body)
        self.autosave_spin.setRange(0, 600)
        self.autosave_spin.setSuffix(" 秒")
        self.autosave_spin.setValue(self._config.autosave_seconds)
        self.autosave_spin.setToolTip("0 表示关闭自动保存")
        form.addRow("自动保存间隔", self.autosave_spin)

        # 日志：单文件上限与保留份数（重启后生效）
        self.log_count_spin = QSpinBox(body)
        self.log_count_spin.setRange(1, 50)
        self.log_count_spin.setSuffix(" 份")
        self.log_count_spin.setValue(self._config.log_backup_count)
        self.log_count_spin.setToolTip("滚动日志保留份数（重启后生效）")
        form.addRow("日志保留", self.log_count_spin)

        self.log_size_spin = QSpinBox(body)
        self.log_size_spin.setRange(1, 100)
        self.log_size_spin.setSuffix(" MB")
        self.log_size_spin.setValue(self._config.log_max_mb)
        self.log_size_spin.setToolTip("单个日志文件上限（重启后生效）")
        form.addRow("单文件上限", self.log_size_spin)
        outer.addLayout(form)

        log_row = QHBoxLayout()
        log_row.addWidget(BodyLabel("日志", body))
        log_btn = PushButton("打开日志目录", body)
        log_btn.clicked.connect(self._on_open_log_dir)
        log_row.addWidget(log_btn)
        log_row.addStretch(1)
        outer.addLayout(log_row)
        return body

    def _slider_row(self, body, form, label: str, minimum: int, maximum: int,
                    value: int, suffix: str):
        holder = QWidget(body)
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        top = QHBoxLayout()
        text = CaptionLabel(f"{value}{suffix}", holder)
        top.addWidget(text)
        top.addStretch(1)
        column.addLayout(top)
        slider = Slider(holder)
        slider.setRange(minimum, maximum)
        slider.setValue(value)
        slider.valueChanged.connect(
            lambda raw, target=text: target.setText(f"{raw}{suffix}")
        )
        column.addWidget(slider)
        form.addRow(label, holder)
        return slider, text

    # -----------------------------------------------------------
    # 交互
    # -----------------------------------------------------------
    def _on_browse_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择默认项目目录")
        if directory:
            self.dir_edit.setText(directory)

    def _on_open_log_dir(self) -> None:
        directory = Path(ConfigManager.log_dir())
        if directory.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))
        else:
            self.hint.setText("日志目录尚未创建")

    def values(self) -> dict:
        """当前表单值（与 `ConfigManager.PREFERENCES` 的键一一对应）。"""
        return {
            "pref/default_project_dir": self.dir_edit.text().strip(),
            "pref/max_recent": self.recent_spin.value(),
            "pref/open_last_project": self.open_last_check.isChecked(),
            "pref/cpu_threads": self.threads_spin.value(),
            "pref/wheel_inverted": self.wheel_check.isChecked(),
            "pref/crosshair": self.crosshair_check.isChecked(),
            "pref/crosshair_opacity": self.crosshair_slider.value(),
            "pref/region_opacity": self.region_slider.value(),
            "pref/default_brightness": self.brightness_slider.value(),
            "pref/default_contrast": self.contrast_slider.value(),
            "pref/show_pixel_value": self.pixel_check.isChecked(),
            "pref/autosave_seconds": self.autosave_spin.value(),
            "pref/log_backup_count": self.log_count_spin.value(),
            "pref/log_max_mb": self.log_size_spin.value(),
        }

    def apply(self) -> None:
        """把表单值写入配置并广播。"""
        for key, value in self.values().items():
            self._config.set_pref(key, value)
        self._config.notify_preferences_changed()
