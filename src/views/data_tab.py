"""数据管理导航页：子页容器（导入 / 类别 / 质检 / 划分）。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QStackedWidget, QVBoxLayout, QWidget
from qfluentwidgets import SegmentedWidget

from src.viewmodels.category_vm import CategoryViewModel
from src.viewmodels.dataset_vm import DatasetViewModel
from src.views.data_pages import (
    DataAugmentPage,
    DataCategoryPage,
    DataImportPage,
    DataQualityPage,
    DataSplitPage,
)


class DataTab(QWidget):
    """数据管理页：以分段控件组织多个子页。"""

    def __init__(
        self,
        vm: DatasetViewModel,
        category_vm: CategoryViewModel | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._vm = vm

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(0)

        self.segmented = SegmentedWidget(self)
        self.stack = QStackedWidget(self)

        self.import_page = DataImportPage(vm, self)
        self.category_page = DataCategoryPage(category_vm, vm, self)
        self.quality_page = DataQualityPage(vm, self)
        self.split_page = DataSplitPage(vm, self)
        self.augment_page = DataAugmentPage(vm, self)

        self._pages = {
            "import": self.import_page,
            "category": self.category_page,
            "quality": self.quality_page,
            "split": self.split_page,
            "augment": self.augment_page,
        }
        for key, text in (
            ("import", "导入"),
            ("category", "类别"),
            ("quality", "质检"),
            ("split", "划分"),
            ("augment", "增强"),
        ):
            self.segmented.addItem(key, text, onClick=lambda k=key: self._on_switch(k))
            self.stack.addWidget(self._pages[key])

        layout.addWidget(self.segmented, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.stack, 1)

        self.segmented.setCurrentItem("import")
        self.stack.setCurrentWidget(self.import_page)

    def _on_switch(self, key: str) -> None:
        page = self._pages.get(key)
        if page is not None:
            self.stack.setCurrentWidget(page)
