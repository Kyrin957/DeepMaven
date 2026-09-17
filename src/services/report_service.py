"""检测报告导出服务：CSV / Excel。"""

from __future__ import annotations

from pathlib import Path

from src.utils.logger import get_logger

logger = get_logger("report")

# (记录字段, 表头)
COLUMNS = [
    ("frame", "帧"),
    ("class_name", "类别"),
    ("confidence", "置信度"),
    ("x1", "x1"),
    ("y1", "y1"),
    ("x2", "x2"),
    ("y2", "y2"),
    ("width", "宽"),
    ("height", "高"),
    ("class_id", "类别ID"),
]


class ReportService:
    """把检测记录导出为表格文件。"""

    @staticmethod
    def to_table(records: list) -> tuple[list[str], list[list]]:
        """转换为 (表头, 行) 结构。"""
        headers = [label for _key, label in COLUMNS]
        rows = [
            [record.get(key, "") for key, _label in COLUMNS]
            for record in records
        ]
        return headers, rows

    @staticmethod
    def write_csv(path: str | Path, records: list) -> Path:
        """导出 CSV（utf-8-sig，便于 Excel 直接打开）。"""
        import csv

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        headers, rows = ReportService.to_table(records)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(rows)
        logger.info("导出 CSV 报告：%s（%s 条）", path, len(rows))
        return path

    @staticmethod
    def write_excel(
        path: str | Path, records: list, sheet: str = "detections"
    ) -> Path:
        """导出 Excel（openpyxl）。"""
        from openpyxl import Workbook

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        headers, rows = ReportService.to_table(records)
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = sheet
        worksheet.append(headers)
        for row in rows:
            worksheet.append(row)
        for index, header in enumerate(headers, 1):
            worksheet.column_dimensions[
                worksheet.cell(row=1, column=index).column_letter
            ].width = max(10, len(str(header)) + 6)
        workbook.save(path)
        logger.info("导出 Excel 报告：%s（%s 条）", path, len(rows))
        return path
