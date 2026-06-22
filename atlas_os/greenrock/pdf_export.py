"""GreenRock PDF export helpers."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


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
    )
    story = _markdown_to_story(markdown_path.read_text(encoding="utf-8"), styles, colors)
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)


def _markdown_to_story(markdown: str, styles, colors) -> list:
    from reportlab.lib.units import inch
    from reportlab.platypus import PageBreak, Paragraph, Spacer, Table, TableStyle

    story: list = []
    lines = markdown.splitlines()
    index = 0
    h2_count = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line:
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
                            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
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


def _footer(canvas, doc) -> None:
    from reportlab.lib.units import inch

    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColorRGB(0.35, 0.42, 0.38)
    canvas.drawString(doc.leftMargin, 0.32 * inch, "GreenRock Analysts - Mock data draft/export")
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
