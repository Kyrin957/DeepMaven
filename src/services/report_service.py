"""报告服务：检测记录导出为 CSV / Excel，模型报告导出为 HTML / Markdown。

模型报告（`ModelReport`）把项目、模型、数据拆分、训练结果与评估指标汇总成
**自包含**的单文件报告：HTML 版内联样式与 SVG 图表，用浏览器打开即可直接
打印成 PDF；Markdown 版便于贴进文档或邮件。
"""

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

# 混淆矩阵里「无真实标签的预测」一列的表头（与 evaluation_service.BACKGROUND 一致）
MODEL_REPORT_BACKGROUND = "误检"


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
        """导出检测记录 CSV（utf-8-sig，便于 Excel 直接打开）。"""
        headers, rows = ReportService.to_table(records)
        return ReportService.write_table(path, headers, rows)

    @staticmethod
    def write_excel(
        path: str | Path, records: list, sheet: str = "detections"
    ) -> Path:
        """导出检测记录 Excel（openpyxl）。"""
        headers, rows = ReportService.to_table(records)
        return ReportService.write_table(path, headers, rows, sheet=sheet)

    @staticmethod
    def write_table(
        path: str | Path, headers: list, rows: list, sheet: str = "sheet1"
    ) -> Path:
        """导出任意表格（按扩展名选择 CSV / Excel）。"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix.lower() in (".xlsx", ".xls"):
            ReportService._write_excel_table(path, headers, rows, sheet)
        else:
            ReportService._write_csv_table(path, headers, rows)
        logger.info("导出表格：%s（%s 条）", path, len(rows))
        return path

    @staticmethod
    def _write_csv_table(path: Path, headers: list, rows: list) -> None:
        import csv

        with open(path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(headers)
            writer.writerows(rows)

    @staticmethod
    def _write_excel_table(path: Path, headers: list, rows: list, sheet: str) -> None:
        from openpyxl import Workbook

        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = sheet
        worksheet.append(list(headers))
        for row in rows:
            worksheet.append(list(row))
        for index, header in enumerate(headers, 1):
            worksheet.column_dimensions[
                worksheet.cell(row=1, column=index).column_letter
            ].width = max(10, len(str(header)) + 6)
        workbook.save(path)


class ModelReport:
    """模型报告：项目 / 模型 / 拆分 / 训练 / 评估信息的单文件报告。

    报告上下文（由调用方组装，`ModelReport` 只负责排版）：

    ```python
    {
        "generated_at": "2026-09-20 12:00:00",
        "project": {"name", "task", "model_type", "classes": [], "created_at",
                    "updated_at", "app_version"},
        "model": {"name", "weights", "source", "variant", "imgsz", "device",
                  "epochs", "best_label", "best_value", "best_epoch", "size_mb",
                  "split_name"},
        "split": {"name", "counts": {"train": 20, "val": 4, "test": 3},
                  "total": 27, "ratios": {"train": 0.74}},
        "evaluation": {"available": bool, "subset", "split_name", "metrics": {...},
                       "per_class": [...], "class_names": [...], "matrix": [[...]],
                       "with_background": bool},
        "export": {"format", "path", "size_mb", "time", "for_inference", "for_api"},
    }
    ```
    """

    _COLORS = {"train": "#0F6CBD", "val": "#0F7B0F", "test": "#C42B1C"}
    _SPLIT_LABELS = {"train": "训练", "val": "验证", "test": "测试"}

    # -----------------------------------------------------------
    # 对外
    # -----------------------------------------------------------
    @staticmethod
    def write(path: str | Path, context: dict) -> Path:
        """按扩展名写出报告（`.md` 为 Markdown，其余为 HTML）。"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix.lower() in (".md", ".markdown", ".txt"):
            path.write_text(ModelReport.build_markdown(context), encoding="utf-8")
        else:
            path.write_text(ModelReport.build_html(context), encoding="utf-8")
        logger.info("生成模型报告：%s", path)
        return path

    # -----------------------------------------------------------
    # 公共数据整理
    # -----------------------------------------------------------
    @staticmethod
    def _fact_sections(context: dict) -> list[tuple[str, list[tuple[str, str]]]]:
        """各段落的「项 → 值」清单（HTML 与 Markdown 共用）。"""
        project = context.get("project") or {}
        model = context.get("model") or {}
        export = context.get("export") or {}

        model_rows = [
            ("模型名称", str(model.get("name") or "—")),
            ("模型来源", str(model.get("source") or "—")),
            ("模型变体", str(model.get("variant") or "—")),
            ("权重文件", str(model.get("weights") or "—")),
            ("模型大小", f"{float(model.get('size_mb') or 0):.2f} MB"),
            ("图像尺寸", f"{model.get('imgsz', '—')}×{model.get('imgsz', '—')}"),
            ("训练方式", str(model.get("device") or "—")),
            ("已训练 Epoch", str(model.get("epochs", "—"))),
            ("最佳指标", (
                f"{model.get('best_label') or '—'} "
                f"{float(model.get('best_value') or 0):.4f}"
                f"（Epoch {model.get('best_epoch', '—')}）"
            )),
            ("使用拆分", str(model.get("split_name") or model.get("split", "—"))),
        ]
        project_rows = [
            ("项目名称", str(project.get("name") or "—")),
            ("任务类型", str(project.get("task") or "—")),
            ("类别", "、".join(project.get("classes") or []) or "—"),
            ("创建时间", str(project.get("created_at") or "—")),
            ("更新时间", str(project.get("updated_at") or "—")),
            ("程序版本", str(project.get("app_version") or "—")),
        ]
        export_rows = [
            ("导出格式", str(export.get("format") or "—")),
            ("导出文件", str(export.get("path") or "—")),
            ("文件大小", f"{float(export.get('size_mb') or 0):.2f} MB"),
            ("导出时间", str(export.get("time") or "—")),
            ("优化方向", (
                "针对 API 接口" if export.get("for_api")
                else ("针对推断" if export.get("for_inference") else "无")
            )),
        ]
        return [
            ("项目信息", project_rows),
            ("模型信息", model_rows),
            ("导出信息", export_rows),
        ]

    @staticmethod
    def _split_rows(context: dict) -> list[tuple[str, str, str]]:
        """拆分：[(子集, 数量, 占比)]。"""
        split = context.get("split") or {}
        counts = split.get("counts") or {}
        total = int(split.get("total") or sum(int(value or 0) for value in counts.values()))
        rows = []
        for key in ("train", "val", "test"):
            count = int(counts.get(key) or 0)
            share = (count / total * 100) if total else 0.0
            rows.append((ModelReport._SPLIT_LABELS[key], str(count), f"{share:.1f}%"))
        rows.append(("合计", str(total), "100%" if total else "0%"))
        return rows

    @staticmethod
    def _eval_rows(context: dict) -> list[tuple[str, str]]:
        evaluation = context.get("evaluation") or {}
        if not evaluation.get("available"):
            return []
        metrics = evaluation.get("metrics") or {}
        return [
            ("评估集", str(evaluation.get("label") or "—")),
            ("图像总数", str(int(metrics.get("total") or 0))),
            ("正确预测", str(int(metrics.get("correct") or 0))),
            ("错误预测", str(int(metrics.get("wrong") or 0))),
            ("准确率", f"{float(metrics.get('accuracy') or 0) * 100:.2f}%"),
            ("Top-1 错误率", f"{float(metrics.get('top1_error') or 0) * 100:.2f}%"),
            ("平均精确率", f"{float(metrics.get('precision') or 0) * 100:.2f}%"),
            ("平均召回率", f"{float(metrics.get('recall') or 0) * 100:.2f}%"),
            ("F1 分数", f"{float(metrics.get('f1') or 0) * 100:.2f}%"),
            ("平均推断时间", f"{float(metrics.get('avg_ms') or 0):.3f} ms"),
        ]

    # 报告里的预测样本分组（评估页会各取若干张）
    _SAMPLE_KINDS = (
        ("correct", "正确预测"),
        ("fp", "误检 / 误判"),
        ("fn", "漏检"),
    )

    @staticmethod
    def _data_uri(path, width: int = 280, quality: int = 82) -> str:
        """把图片缩放后内联成 data URI（报告单文件即可分享、可直接打印）。"""
        try:
            import base64
            import io

            from PIL import Image as PILImage

            with PILImage.open(path) as handle:
                image = handle.convert("RGB")
            ratio = min(1.0, width / max(1, image.width))
            if ratio < 1.0:
                image = image.resize((
                    max(1, int(image.width * ratio)),
                    max(1, int(image.height * ratio)),
                ))
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=quality)
            return (
                "data:image/jpeg;base64,"
                + base64.b64encode(buffer.getvalue()).decode("ascii")
            )
        except Exception as exc:  # noqa: BLE001 - 单张内联失败不影响报告
            logger.debug("内联样本图失败 %s: %s", path, exc)
            return ""

    @staticmethod
    def _sample_groups(context: dict) -> list[tuple[str, list[dict]]]:
        """按「正确 / 误检 / 漏检」分组样本图。"""
        samples = ((context.get("evaluation") or {}).get("samples")) or []
        groups: list[tuple[str, list[dict]]] = []
        for kind, label in ModelReport._SAMPLE_KINDS:
            items = [
                dict(item) for item in samples
                if str(item.get("kind") or "") == kind and item.get("path")
            ]
            if items:
                groups.append((label, items))
        return groups

    @staticmethod
    def _per_class_rows(context: dict) -> list[list[str]]:
        evaluation = context.get("evaluation") or {}
        rows = []
        for item in evaluation.get("per_class") or []:
            rows.append([
                str(item.get("name", "")),
                str(int(item.get("support") or 0)),
                str(int(item.get("tp") or 0)),
                str(int(item.get("fp") or 0)),
                str(int(item.get("fn") or 0)),
                f"{float(item.get('precision') or 0) * 100:.2f}%",
                f"{float(item.get('recall') or 0) * 100:.2f}%",
                f"{float(item.get('f1') or 0) * 100:.2f}%",
            ])
        return rows

    @staticmethod
    def _matrix_rows(context: dict) -> tuple[list[str], list[list[str]]]:
        """混淆矩阵：表头 + 行（行 = 真实、列 = 预测）。"""
        evaluation = context.get("evaluation") or {}
        matrix = evaluation.get("matrix") or []
        names = [str(name) for name in (evaluation.get("class_names") or [])]
        if not matrix:
            return [], []
        names = list(names) + ([MODEL_REPORT_BACKGROUND]
                               if evaluation.get("with_background") else [])
        header = ["真实 \\ 预测"] + names
        rows = []
        for index, row in enumerate(matrix):
            label = names[index] if index < len(names) else str(index)
            rows.append([label] + [str(int(value)) for value in row])
        return header, rows

    # -----------------------------------------------------------
    # Markdown
    # -----------------------------------------------------------
    @staticmethod
    def build_markdown(context: dict) -> str:
        project = context.get("project") or {}
        lines = [
            f"# {project.get('name') or '项目'} · 模型报告",
            "",
            f"生成时间：{context.get('generated_at') or ''}",
            "",
        ]
        for title, rows in ModelReport._fact_sections(context):
            lines += [f"## {title}", "", "| 项 | 值 |", "| --- | --- |"]
            lines += [f"| {key} | {value} |" for key, value in rows]
            lines.append("")

        lines += ["## 数据拆分", "", "| 子集 | 数量 | 占比 |", "| --- | --- | --- |"]
        lines += [f"| {name} | {count} | {share} |"
                  for name, count, share in ModelReport._split_rows(context)]
        lines.append("")

        if (context.get("evaluation") or {}).get("available"):
            lines += ["## 评估结果", "", "| 指标 | 值 |", "| --- | --- |"]
            lines += [f"| {key} | {value} |"
                      for key, value in ModelReport._eval_rows(context)]
            lines.append("")
            per_class = ModelReport._per_class_rows(context)
            if per_class:
                lines += ["### 逐类指标", "",
                          "| 类别 | 样本 | TP | FP | FN | 精确率 | 召回率 | F1 |",
                          "| --- | --- | --- | --- | --- | --- | --- | --- |"]
                lines += ["| " + " | ".join(row) + " |" for row in per_class]
                lines.append("")
            header, rows = ModelReport._matrix_rows(context)
            if rows:
                lines += ["### 混淆矩阵", "",
                          "| " + " | ".join(header) + " |",
                          "| " + " | ".join("---" for _ in header) + " |"]
                lines += ["| " + " | ".join(row) + " |" for row in rows]
                lines.append("")
            groups = ModelReport._sample_groups(context)
            if groups:
                lines += ["### 预测样本", ""]
                for label, items in groups:
                    lines.append(f"- **{label}**：")
                    lines += [
                        f"  - {item.get('caption') or ''}（`{item.get('path')}`）"
                        for item in items
                    ]
                lines.append("")
        else:
            lines += ["## 评估结果", "", "尚未评估。", ""]
        return "\n".join(lines).rstrip() + "\n"

    # -----------------------------------------------------------
    # HTML
    # -----------------------------------------------------------
    @staticmethod
    def build_html(context: dict) -> str:
        project = context.get("project") or {}
        title = f"{project.get('name') or '项目'} · 模型报告"
        blocks = []

        for name, rows in ModelReport._fact_sections(context):
            body = "".join(
                f"<tr><th>{key}</th><td>{value}</td></tr>" for key, value in rows
            )
            blocks.append(
                f"<section><h2>{name}</h2><table class=\"facts\">{body}</table></section>"
            )

        # 数据拆分（环形图 + 表格）
        split = context.get("split") or {}
        counts = split.get("counts") or {}
        segments = [
            (ModelReport._SPLIT_LABELS[key], int(counts.get(key) or 0),
             ModelReport._COLORS[key])
            for key in ("train", "val", "test")
        ]
        total = int(split.get("total") or sum(value for _, value, _ in segments))
        split_table = "".join(
            f"<tr><td>{name}</td><td>{count}</td><td>{share}</td></tr>"
            for name, count, share in ModelReport._split_rows(context)
        )
        blocks.append(
            "<section><h2>数据拆分</h2>"
            f"<p class=\"caption\">拆分名称：{split.get('name') or '—'}</p>"
            "<div class=\"charts\">"
            f"{ModelReport._svg_donut(segments, str(total))}"
            "<table><thead><tr><th>子集</th><th>数量</th><th>占比</th></tr></thead>"
            f"<tbody>{split_table}</tbody></table>"
            "</div></section>"
        )

        # 评估结果
        evaluation = context.get("evaluation") or {}
        if evaluation.get("available"):
            metrics = evaluation.get("metrics") or {}
            pie = ModelReport._svg_donut(
                [("正确", int(metrics.get("correct") or 0), "#0F7B0F"),
                 ("错误", int(metrics.get("wrong") or 0), "#C42B1C")],
                str(int(metrics.get("total") or 0)),
            )
            facts = "".join(
                f"<tr><th>{key}</th><td>{value}</td></tr>"
                for key, value in ModelReport._eval_rows(context)
            )
            html = [
                "<section><h2>评估结果</h2><div class=\"charts\">",
                pie, f"<table class=\"facts\">{facts}</table>", "</div>",
            ]
            per_class = ModelReport._per_class_rows(context)
            if per_class:
                head = ("<tr><th>类别</th><th>样本</th><th>TP</th><th>FP</th>"
                        "<th>FN</th><th>精确率</th><th>召回率</th><th>F1</th></tr>")
                body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row)
                               + "</tr>" for row in per_class)
                html.append(f"<h3>逐类指标</h3><table><thead>{head}</thead>"
                            f"<tbody>{body}</tbody></table>")
            header, rows = ModelReport._matrix_rows(context)
            if rows:
                head = "<tr>" + "".join(f"<th>{cell}</th>" for cell in header) + "</tr>"
                body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row)
                               + "</tr>" for row in rows)
                html.append(f"<h3>混淆矩阵</h3><table class=\"matrix\">"
                            f"<thead>{head}</thead><tbody>{body}</tbody></table>")
            html.append("</section>")
            blocks.append("".join(html))

            # 预测样本（正确 / 误检 / 漏检 各若干张，内联在报告里）
            groups = ModelReport._sample_groups(context)
            if groups:
                sample_html = ["<section><h2>预测样本</h2>"]
                for label, items in groups:
                    sample_html.append(
                        f"<h3>{label}（{len(items)}）</h3><div class=\"samples\">"
                    )
                    for item in items:
                        caption = str(item.get("caption") or "")
                        uri = ModelReport._data_uri(str(item.get("path") or ""))
                        if uri:
                            sample_html.append(
                                f"<figure><img src=\"{uri}\" alt=\"{caption}\">"
                                f"<figcaption>{caption}</figcaption></figure>"
                            )
                        else:
                            sample_html.append(
                                f"<figure class=\"missing\"><figcaption>{caption}"
                                f"（{item.get('path')}）</figcaption></figure>"
                            )
                    sample_html.append("</div>")
                sample_html.append("</section>")
                blocks.append("".join(sample_html))
        else:
            blocks.append("<section><h2>评估结果</h2><p>尚未评估。</p></section>")

        style = """
:root { color-scheme: light; }
body { font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", sans-serif;
       margin: 0 auto; padding: 32px 40px; max-width: 900px; color: #1F1F1F; }
h1 { font-size: 24px; margin: 0 0 6px; }
h2 { font-size: 17px; margin: 26px 0 10px; padding-bottom: 6px;
     border-bottom: 2px solid #0F6CBD; }
h3 { font-size: 14px; margin: 18px 0 6px; color: #444; }
.meta { color: #6A6A6A; font-size: 12px; margin: 0 0 18px; }
.caption { color: #6A6A6A; font-size: 12px; margin: 0 0 10px; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
table.facts th { text-align: left; width: 132px; color: #444; font-weight: 600; }
th, td { border: 1px solid #DCDCDC; padding: 6px 10px; text-align: left; }
thead th { background: #F2F6FA; }
.facts tr:nth-child(even) td, .facts tr:nth-child(even) th { background: #FAFAFA; }
.charts { display: flex; gap: 24px; align-items: center; flex-wrap: wrap; }
.charts table { flex: 1 1 260px; }
.matrix td:first-child { background: #F2F6FA; font-weight: 600; }
.samples { display: flex; flex-wrap: wrap; gap: 12px; }
.samples figure { margin: 0; width: 190px; }
.samples img { width: 100%; border: 1px solid #DCDCDC; border-radius: 4px;
               background: #FAFAFA; }
.samples figcaption { font-size: 12px; color: #666; margin-top: 4px;
                      line-height: 1.4; word-break: break-all; }
.samples figure.missing figcaption { color: #C42B1C; }
footer { margin-top: 30px; color: #8A8A8A; font-size: 12px; }
@media print { body { padding: 0; max-width: none; } @page { margin: 16mm; } }
"""

        return (
            "<!doctype html>\n<html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            f"<title>{title}</title><style>{style}</style></head><body>"
            f"<h1>{title}</h1>"
            f"<p class=\"meta\">生成时间：{context.get('generated_at') or ''}"
            f" · DeepMaven v{project.get('app_version') or ''}</p>"
            + "".join(blocks)
            + "<footer>本报告由 DeepMaven 自动生成，可用浏览器直接打印为 PDF。</footer>"
            "</body></html>\n"
        )

    # -----------------------------------------------------------
    # 图表
    # -----------------------------------------------------------
    @staticmethod
    def _svg_donut(segments: list, center_text: str = "", size: float = 170.0,
                   thickness: float = 26.0) -> str:
        """内联 SVG 环形图（纯文本，HTML 与打印都好用）。"""
        import math

        values = [(str(name), max(0.0, float(value)), str(color))
                  for name, value, color in segments]
        total = sum(value for _, value, _ in values)
        center = size / 2
        radius = (size - thickness) / 2
        circumference = 2 * math.pi * radius
        if total <= 0:
            return (f"<svg width=\"{size}\" height=\"{size}\" "
                    f"viewBox=\"0 0 {size} {size}\"></svg>")
        circles = []
        offset = 0.0
        for _name, value, color in values:
            length = circumference * value / total
            gap = circumference - length
            circles.append(
                f"<circle cx=\"{center}\" cy=\"{center}\" r=\"{radius:.2f}\" "
                f"fill=\"none\" stroke=\"{color}\" stroke-width=\"{thickness:.1f}\" "
                f"stroke-dasharray=\"{length:.2f} {gap:.2f}\" "
                f"stroke-dashoffset=\"{-offset:.2f}\"/>"
            )
            offset += length
        return (
            f"<svg width=\"{size}\" height=\"{size}\" viewBox=\"0 0 {size} {size}\">"
            f"<g transform=\"rotate(-90 {center} {center})\">{''.join(circles)}</g>"
            f"<text x=\"{center}\" y=\"{center}\" text-anchor=\"middle\" "
            f"dominant-baseline=\"central\" font-size=\"22\" fill=\"#1F1F1F\">"
            f"{center_text}</text></svg>"
        )


