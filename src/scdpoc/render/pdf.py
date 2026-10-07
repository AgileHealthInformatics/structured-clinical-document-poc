"""Deterministic PDF page rendition of the patient summary.

Uses ReportLab in invariant mode with embedded Bitstream Vera fonts (shipped
with ReportLab under a permissive licence), so the same view model and
renderer version produce byte-identical pages (AT-03). No standard-14 fonts
are used because PDF/A requires every font to be embedded.
"""
from __future__ import annotations

import io
from pathlib import Path
from xml.sax.saxutils import escape

import reportlab
from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ..ips.view import SummaryView
from .html import RENDERER_VERSION

_FONTS_REGISTERED = False


def _register_fonts() -> None:
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return
    font_dir = Path(reportlab.__file__).parent / "fonts"
    for name, file in [("Vera", "Vera.ttf"), ("Vera-Bold", "VeraBd.ttf"), ("Vera-Italic", "VeraIt.ttf"),
                       ("Vera-BoldItalic", "VeraBI.ttf")]:
        pdfmetrics.registerFont(TTFont(name, str(font_dir / file)))
    pdfmetrics.registerFontFamily("Vera", normal="Vera", bold="Vera-Bold", italic="Vera-Italic",
                                  boldItalic="Vera-BoldItalic")
    _FONTS_REGISTERED = True


INK = colors.HexColor("#1c1c1c")
MUTED = colors.HexColor("#555555")
RULE = colors.HexColor("#999999")
BANNER = colors.HexColor("#7a1f1f")
HEAD_BG = colors.HexColor("#f1efe9")
# Tables default to Helvetica for cell text; force the embedded font everywhere.
COLUMN_FRACTIONS = {
    "11450-4": [0.46, 0.16, 0.16, 0.22],
    "48765-2": [0.36, 0.16, 0.18, 0.30],
    "10160-0": [0.28, 0.12, 0.42, 0.18],
    "11369-6": [0.50, 0.20, 0.30],
    "30954-2": [0.44, 0.14, 0.20, 0.22],
}
_TABLE_FONT = ("FONT", (0, 0), (-1, -1), "Vera", 8.5)


def _styles() -> dict[str, ParagraphStyle]:
    base = ParagraphStyle("base", fontName="Vera", fontSize=9, leading=12, textColor=INK, alignment=TA_LEFT)
    return {
        "base": base,
        "title": ParagraphStyle("title", parent=base, fontName="Vera-Bold", fontSize=17, leading=21),
        "h2": ParagraphStyle("h2", parent=base, fontName="Vera-Bold", fontSize=11.5, leading=15, spaceBefore=10),
        "cell": ParagraphStyle("cell", parent=base, fontSize=8.5, leading=11),
        "head": ParagraphStyle("head", parent=base, fontName="Vera-Bold", fontSize=8.5, leading=11),
        "empty": ParagraphStyle("empty", parent=base, fontName="Vera-Italic", textColor=MUTED),
        "banner": ParagraphStyle("banner", parent=base, fontName="Vera-Bold", fontSize=8, leading=10,
                                 textColor=colors.white),
    }


def render_pdf(view: SummaryView, attachment_name: str) -> bytes:
    _register_fonts()
    rl_config.invariant = 1                    # fixed internal dates/ids -> reproducible bytes
    st = _styles()
    buf = io.BytesIO()

    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFont("Vera", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 10 * mm, f"Document {view.document_id}")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
        canvas.drawString(18 * mm, 6.5 * mm,
                          f"Rendered by {RENDERER_VERSION} from the embedded FHIR IPS Associated File "
                          f"(AFRelationship=Source)")
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=18 * mm, title=view.title, author=view.custodian,
                            subject=f"Patient summary for {view.patient_name}", creator=RENDERER_VERSION,
                            invariant=1)
    width = A4[0] - 36 * mm - 12   # frame has 6pt padding each side
    story = []
    banner = Table([[Paragraph("SYNTHETIC DATA ONLY - DEMONSTRATOR, NOT FOR CLINICAL USE", st["banner"])]],
                   colWidths=[width], hAlign="LEFT")
    banner.setStyle(TableStyle([_TABLE_FONT, ("BACKGROUND", (0, 0), (-1, -1), BANNER),
                                ("LEFTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 3),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story += [banner, Spacer(1, 6 * mm), Paragraph(escape(view.title), st["title"]), Spacer(1, 3 * mm)]

    meta = [("Patient", view.patient_name), ("Identifier", view.patient_id), ("Date of birth", view.birth_date),
            ("Sex (administrative)", view.gender), ("Issued", view.issued), ("Author", view.author),
            ("Custodian", view.custodian), ("Document", view.document_id)]
    if view.replaces:
        meta.append(("Replaces", view.replaces))
    meta.append(("Structured source", f"{attachment_name} (embedded, AFRelationship=Source)"))
    mt = Table([[Paragraph(escape(k), st["head"]), Paragraph(escape(v), st["cell"])] for k, v in meta],
               colWidths=[38 * mm, width - 38 * mm], hAlign="LEFT")
    mt.setStyle(TableStyle([_TABLE_FONT, ("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                            ("TOPPADDING", (0, 0), (-1, -1), 1), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [mt, Spacer(1, 2 * mm)]

    for s in view.sections:
        block = [Paragraph(escape(s.title), st["h2"]), Spacer(1, 1.5 * mm)]
        if s.empty_text:
            block.append(Paragraph(escape(s.empty_text), st["empty"]))
        else:
            widths = [width * f for f in COLUMN_FRACTIONS.get(s.code, [1 / len(s.columns)] * len(s.columns))]
            data = [[Paragraph(escape(c), st["head"]) for c in s.columns]]
            data += [[Paragraph(escape(v), st["cell"]) for v in r] for r in s.rows]
            t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
            t.setStyle(TableStyle([_TABLE_FONT,
                ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG), ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
            block.append(t)
        story.append(KeepTogether(block))

    def canvasmaker(*args, **kwargs):
        # Initial font must be an embedded font, otherwise ReportLab writes a
        # Helvetica (standard-14, non-embedded) reference into every page.
        kwargs["initialFontName"] = "Vera"
        return Canvas(*args, **kwargs)

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page, canvasmaker=canvasmaker)
    return buf.getvalue()
