"""Plain-text resume -> one ATS-friendly PDF: real text, one column, no tables for content.

Layout rules match the master resume's plain-text format: name, headline, contact line, then ALL-CAPS section
headings, "Company | Location" and "Title | Dates" lines, and "- " bullets.
"""

import re
from html import escape
from pathlib import Path

from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

HEADING = re.compile(r"^[A-Z][A-Z &/]+$")
DATED = re.compile(r"(January|February|March|April|May|June|July|August|September|October|November|December"
                   r"|Present|\b(19|20)\d\d\b)")

_s = lambda name, **kw: ParagraphStyle(name, fontName="Helvetica", fontSize=10, leading=13, **kw)
NAME = _s("name", alignment=TA_CENTER).clone("name", fontName="Helvetica-Bold", fontSize=20, leading=24)
CENTER = _s("center", alignment=TA_CENTER)
SECTION = _s("section", spaceBefore=8, spaceAfter=2).clone("section", fontName="Helvetica-Bold", fontSize=11.5)
COMPANY = _s("company", spaceBefore=5).clone("company", fontName="Helvetica-Bold")
ROLE = _s("role", spaceAfter=2).clone("role", fontName="Helvetica-Oblique")
BULLET = _s("bullet", leftIndent=14, bulletIndent=4, spaceAfter=2)
BODY = _s("body", spaceAfter=1)


def _left_right(left: str, right: str) -> str:
    return f"{escape(left)}<font name='Helvetica' size=9.5>&nbsp;&nbsp;|&nbsp;&nbsp;{escape(right)}</font>"


def generate_pdf(resume_text: str, out_path: Path) -> Path:
    lines = [l.rstrip() for l in resume_text.strip().splitlines()]
    story = [Paragraph(escape(lines[0]), NAME), Paragraph(escape(lines[1]), CENTER),
             Paragraph(escape(lines[2]), CENTER), Spacer(1, 4)]
    for i, line in enumerate(lines[3:], start=3):
        s = line.strip()
        if not s:
            continue
        if HEADING.match(s) and len(s) > 3:
            story += [Paragraph(escape(s), SECTION), HRFlowable(width="100%", thickness=0.6, spaceAfter=3)]
        elif s.startswith("- "):
            story.append(Paragraph(escape(s[2:]), BULLET, bulletText="•"))
        elif "|" in s:
            left, right = (p.strip() for p in s.split("|", 1))
            # "Company | Location" is followed by a dated "Title | Dates" line.
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            style = ROLE if DATED.search(right) and not ("|" in nxt and DATED.search(nxt)) else COMPANY
            story.append(Paragraph(_left_right(left, right), style))
        else:
            story.append(Paragraph(escape(s), BODY))
    doc = SimpleDocTemplate(str(out_path), pagesize=letter, topMargin=0.5 * inch, bottomMargin=0.5 * inch,
                            leftMargin=0.75 * inch, rightMargin=0.75 * inch)
    doc.build(story)
    return out_path
