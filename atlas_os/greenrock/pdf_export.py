"""GreenRock PDF export helpers."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from atlas_os.greenrock.assets import atlas_logo_path, greenrock_logo_path


BUNDLED_PYTHON = Path(
    "/Users/marklalich/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
)


def render_markdown_report_to_pdf(markdown_path: Path, pdf_path: Path) -> Path:
    try:
        _render_with_reportlab(markdown_path, pdf_path)
    except ModuleNotFoundError as exc:
        if exc.name != "reportlab" or not BUNDLED_PYTHON.exists():
            raise
        subprocess.run(
            [
                str(BUNDLED_PYTHON),
                "-m",
                "atlas_os.greenrock.pdf_export",
                "--render",
                str(markdown_path),
                str(pdf_path),
            ],
            check=True,
        )
    return pdf_path


def _render_with_reportlab(markdown_path: Path, pdf_path: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="GreenRockTitle",
            parent=styles["Title"],
            alignment=TA_CENTER,
            fontName="Helvetica-Bold",
            fontSize=20,
            leading=24,
            textColor=colors.HexColor("#174C3C"),
            spaceAfter=8,
        )
    )
    styles.add(
        ParagraphStyle(
            name="GreenRockSubtitle",
            parent=styles["Heading2"],
            alignment=TA_CENTER,
            fontName="Helvetica",
            fontSize=13,
            leading=16,
            textColor=colors.HexColor("#557064"),
            spaceAfter=16,
        )
    )
    styles.add(
        ParagraphStyle(
            name="GreenRockHeading",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=16,
            textColor=colors.HexColor("#174C3C"),
            spaceBefore=10,
            spaceAfter=6,
        )
    )
    styles.add(
        ParagraphStyle(
            name="GreenRockSmall",
            parent=styles["BodyText"],
            fontSize=8,
            leading=10,
        )
    )
    styles.add(
        ParagraphStyle(
            name="GreenRockBody",
            parent=styles["BodyText"],
            alignment=TA_LEFT,
            fontSize=9.5,
            leading=13,
            spaceAfter=6,
        )
    )

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=letter,
        rightMargin=0.55 * inch,
        leftMargin=0.55 * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.55 * inch,
        title="GreenRock Analysts Monthly Opportunity Report",
        author="Atlas OS",
        pageCompression=0,
    )
    markdown = markdown_path.read_text(encoding="utf-8")
    doc.greenrock_data_mode = _markdown_data_mode(markdown)
    story = _cover_story(markdown, styles, colors) + _markdown_to_story(markdown, styles, colors)
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)


def _cover_story(markdown: str, styles, colors) -> list:
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.units import inch
    from reportlab.platypus import Image, PageBreak, Paragraph, Spacer, Table, TableStyle

    title = _markdown_title(markdown)
    data_mode = _markdown_data_mode(markdown)
    report_date = _markdown_field(markdown, "Report Date") or _markdown_field(markdown, "Date") or "Local draft"
    candidate_source = _markdown_field(markdown, "Candidate Source") or _markdown_field(markdown, "Staged Candidate Source") or "Local GreenRock workflow"
    cover_note = (
        "Approval/status disclaimer: this local report export is gated by human approval. "
        "It is not a recommendation, not publication, not email distribution, and not a trading action."
    )
    brand_style = styles["GreenRockSubtitle"].clone("CoverBrand")
    brand_style.alignment = TA_CENTER
    brand_style.textColor = colors.HexColor("#174C3C")
    brand_style.fontName = "Helvetica-Bold"
    story: list = [Spacer(1, 0.28 * inch)]
    logos = []
    for logo_path in (atlas_logo_path(), greenrock_logo_path()):
        if not logo_path:
            continue
        try:
            logos.append(Image(str(logo_path), width=1.0 * inch, height=1.0 * inch))
        except Exception:
            pass
    if logos:
        logo_table = Table([logos], hAlign="CENTER")
        logo_table.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
        story.extend([logo_table, Spacer(1, 0.24 * inch)])
    else:
        story.extend([Paragraph("Atlas OS | GreenRock Analysts", brand_style), Spacer(1, 0.24 * inch)])
    story.extend(
        [
            Paragraph("Atlas OS Command Center", brand_style),
            Paragraph(_clean_inline(title), styles["GreenRockTitle"]),
            Paragraph("GreenRock Analysts", styles["GreenRockSubtitle"]),
            Spacer(1, 0.22 * inch),
            _cover_meta_table(
                (
                    ("Date", report_date),
                    ("Data Mode", data_mode.upper()),
                    ("Candidate Source", candidate_source),
                    ("Status", "Human-approved local PDF export"),
                ),
                styles,
                colors,
            ),
            Spacer(1, 0.28 * inch),
            Paragraph(_clean_inline(cover_note), styles["GreenRockBody"]),
            Spacer(1, 0.5 * inch),
            Paragraph("Professional research draft. Approval gates remain mandatory for any downstream use.", styles["GreenRockSmall"]),
            PageBreak(),
        ]
    )
    return story


def _cover_meta_table(rows: tuple[tuple[str, str], ...], styles, colors):
    from reportlab.platypus import Paragraph, Table, TableStyle

    table = Table(
        [[Paragraph(f"<b>{_clean_inline(label)}</b>", styles["GreenRockSmall"]), Paragraph(_clean_inline(value), styles["GreenRockSmall"])] for label, value in rows],
        colWidths=[120, 330],
        hAlign="CENTER",
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#174C3C")),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#F3C969")),
                ("BACKGROUND", (1, 0), (1, -1), colors.HexColor("#F4F7F5")),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#C8D2CC")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return table


def _markdown_to_story(markdown: str, styles, colors) -> list:
    from reportlab.lib.units import inch
    from reportlab.platypus import Image, PageBreak, Paragraph, Spacer, Table, TableStyle

    story: list = []
    logo_path = greenrock_logo_path()
    if logo_path:
        try:
            story.extend([Image(str(logo_path), width=1.1 * inch, height=1.1 * inch), Spacer(1, 0.08 * inch)])
        except Exception:
            pass
    lines = markdown.splitlines()
    index = 0
    h2_count = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        if line.startswith("!["):
            index += 1
            continue

        if line.startswith("|"):
            table_lines: list[str] = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_lines.append(lines[index].strip())
                index += 1
            table_data = _parse_table(table_lines, styles)
            if table_data:
                column_count = len(table_data[0])
                table = Table(table_data, repeatRows=1, hAlign="LEFT")
                table.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#174C3C")),
                            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#F3C969")),
                            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                            ("FONTSIZE", (0, 0), (-1, -1), 7 if column_count >= 7 else 8),
                            ("LEADING", (0, 0), (-1, -1), 8),
                            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#C8D2CC")),
                            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F7F5")]),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ]
                    )
                )
                story.extend([table, Spacer(1, 0.12 * inch)])
            continue

        if line.startswith("# "):
            story.append(Paragraph(_clean_inline(line[2:]), styles["GreenRockTitle"]))
        elif line.startswith("## "):
            h2_count += 1
            if h2_count in {4, 6, 8, 10}:
                story.append(PageBreak())
            story.append(Paragraph(_clean_inline(line[3:]), styles["GreenRockHeading"]))
        elif line.startswith("### "):
            story.append(Paragraph(_clean_inline(line[4:]), styles["Heading3"]))
        elif line.startswith("- "):
            story.append(Paragraph(f"• {_clean_inline(line[2:])}", styles["GreenRockBody"]))
        elif line.startswith("> "):
            story.append(Paragraph(f"<i>{_clean_inline(line[2:])}</i>", styles["GreenRockBody"]))
        else:
            style_name = "GreenRockSubtitle" if line == "## Technical Dislocation Screen" else "GreenRockBody"
            story.append(Paragraph(_clean_inline(line), styles[style_name]))
        index += 1
    return story


def _parse_table(table_lines: list[str], styles) -> list[list]:
    from reportlab.platypus import Paragraph

    rows = []
    for raw in table_lines:
        cells = [cell.strip() for cell in raw.strip("|").split("|")]
        if all(set(cell) <= {"-", ":"} for cell in cells):
            continue
        rows.append([Paragraph(_clean_inline(cell), styles["GreenRockSmall"]) for cell in cells])
    return rows


def _clean_inline(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    return text


def _markdown_data_mode(markdown: str) -> str:
    return "Real" if re.search(r"\*\*Data Mode:\*\*\s*REAL|Data Mode:\s*REAL", markdown) else "Mock"


def _markdown_title(markdown: str) -> str:
    for line in markdown.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return "GreenRock Analysts Monthly Opportunity Report"


def _markdown_field(markdown: str, label: str) -> str | None:
    pattern = rf"(?:\*\*{re.escape(label)}:\*\*|{re.escape(label)}:)\s*(.+)"
    match = re.search(pattern, markdown)
    return match.group(1).strip() if match else None


def _footer(canvas, doc) -> None:
    inch = 72
    data_mode = getattr(doc, "greenrock_data_mode", "Mock")
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColorRGB(0.35, 0.42, 0.38)
    canvas.drawString(doc.leftMargin, 0.32 * inch, f"GreenRock Analysts - {data_mode} data draft/export")
    canvas.drawRightString(7.95 * inch, 0.32 * inch, f"Page {doc.page}")
    canvas.restoreState()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--render", action="store_true")
    parser.add_argument("markdown_path", type=Path)
    parser.add_argument("pdf_path", type=Path)
    args = parser.parse_args(argv)
    if args.render:
        _render_with_reportlab(args.markdown_path, args.pdf_path)
        return 0
    parser.error("Expected --render")


if __name__ == "__main__":
    raise SystemExit(main())
