"""
Convert PROJECT_JOURNAL.md to PROJECT_JOURNAL.pdf using reportlab.
Run:  python make_pdf.py
"""
import pathlib, re
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, Preformatted, KeepTogether
)
from reportlab.platypus.flowables import HRFlowable

HERE     = pathlib.Path(__file__).parent
MD_PATH  = HERE / "PROJECT_JOURNAL.md"
PDF_PATH = HERE / "PROJECT_JOURNAL.pdf"

# ── Colour palette ────────────────────────────────────────────────────────────
C_NAVY   = colors.HexColor("#0d1b4b")
C_BLUE   = colors.HexColor("#1c3d7a")
C_INDIGO = colors.HexColor("#4c6ef5")
C_LTBLUE = colors.HexColor("#dde9ff")
C_LTGREY = colors.HexColor("#f4f6fb")
C_GREY   = colors.HexColor("#8892aa")
C_BORDER = colors.HexColor("#c5d3f0")
C_WHITE  = colors.white
C_CODE   = colors.HexColor("#2b3a8a")
C_CODEBG = colors.HexColor("#f0f4ff")
C_WARN   = colors.HexColor("#5c4a00")
C_WARNBG = colors.HexColor("#fffbea")

# ── Styles ────────────────────────────────────────────────────────────────────
styles = getSampleStyleSheet()

def sty(name, **kw):
    return ParagraphStyle(name, **kw)

H1 = sty("H1", fontName="Helvetica-Bold", fontSize=22, textColor=C_NAVY,
          spaceAfter=6, spaceBefore=0, leading=28)
H2 = sty("H2", fontName="Helvetica-Bold", fontSize=14, textColor=C_BLUE,
          spaceAfter=4, spaceBefore=18, leading=18)
H3 = sty("H3", fontName="Helvetica-Bold", fontSize=11.5, textColor=C_BLUE,
          spaceAfter=3, spaceBefore=12, leading=15)
H4 = sty("H4", fontName="Helvetica-Bold", fontSize=10.5, textColor=C_BLUE,
          spaceAfter=2, spaceBefore=8, leading=14)
BODY = sty("BODY", fontName="Helvetica", fontSize=10, textColor=C_NAVY,
           spaceAfter=5, leading=15)
BULLET = sty("BULLET", fontName="Helvetica", fontSize=10, textColor=C_NAVY,
             spaceAfter=3, leading=14, leftIndent=14, bulletIndent=0)
CODE_INLINE = sty("CODE_INLINE", fontName="Courier", fontSize=9,
                  textColor=C_CODE, backColor=C_CODEBG)
NOTE = sty("NOTE", fontName="Helvetica-Oblique", fontSize=9.5,
           textColor=C_WARN, backColor=C_WARNBG, spaceAfter=5,
           leftIndent=10, rightIndent=10, leading=13)

MONO = ParagraphStyle("MONO", fontName="Courier", fontSize=8.5,
                      textColor=C_CODE, leading=12, leftIndent=0)

TH = sty("TH", fontName="Helvetica-Bold", fontSize=9, textColor=C_WHITE,
         alignment=TA_LEFT, leading=12)
TD = sty("TD", fontName="Helvetica", fontSize=9, textColor=C_NAVY,
         alignment=TA_LEFT, leading=12)

# ── Helpers ───────────────────────────────────────────────────────────────────
def escape_xml(s):
    """Escape characters that are special in reportlab's XML parser."""
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;"))

def inline(text):
    """Replace inline code `x` with styled spans, and **bold**."""
    # Handle code spans first — escape XML inside them, then wrap
    def code_sub(m):
        inner = escape_xml(m.group(1))
        return f'<font face="Courier" color="#2b3a8a" backColor="#f0f4ff">{inner}</font>'
    text = re.sub(r'`([^`]+)`', code_sub, text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\*(.+?)\*',   r'<i>\1</i>', text)
    # strip bare markdown links [text](url) → text
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # escape any remaining bare < > & that aren't part of our tags
    # (only outside of tags we already inserted)
    parts = re.split(r'(<[^>]+>)', text)
    out = []
    for part in parts:
        if part.startswith('<') and part.endswith('>'):
            out.append(part)   # already a tag — leave alone
        else:
            # escape stray & < > that aren't entity refs
            part = re.sub(r'&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)', '&amp;', part)
            part = re.sub(r'<', '&lt;', part)
            part = re.sub(r'>', '&gt;', part)
            out.append(part)
    return "".join(out)

def hr():
    return HRFlowable(width="100%", thickness=1, color=C_BORDER,
                      spaceAfter=6, spaceBefore=4)

# ── Markdown parser ───────────────────────────────────────────────────────────
def parse_md(text):
    """
    Hand-rolled line-by-line MD→Platypus converter.
    Handles: H1-H4, paragraphs, bullet/numbered lists, code fences,
    inline tables, blockquotes, horizontal rules.
    """
    story = []
    lines = text.splitlines()
    i = 0

    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()

        # ── Blank line ────────────────────────────────────────────────────────
        if not stripped:
            i += 1
            continue

        # ── Headings ──────────────────────────────────────────────────────────
        if stripped.startswith("#### "):
            story.append(Paragraph(inline(stripped[5:]), H4))
            i += 1; continue
        if stripped.startswith("### "):
            story.append(Paragraph(inline(stripped[4:]), H3))
            i += 1; continue
        if stripped.startswith("## "):
            story.append(hr())
            story.append(Paragraph(inline(stripped[3:]), H2))
            i += 1; continue
        if stripped.startswith("# "):
            story.append(Paragraph(inline(stripped[2:]), H1))
            story.append(Spacer(1, 4))
            i += 1; continue

        # ── Horizontal rule ───────────────────────────────────────────────────
        if re.match(r'^-{3,}$', stripped) or re.match(r'^\*{3,}$', stripped):
            story.append(hr())
            i += 1; continue

        # ── Fenced code block ─────────────────────────────────────────────────
        if stripped.startswith("```"):
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # consume closing ```
            code_text = "\n".join(code_lines)
            table = Table(
                [[Preformatted(code_text, MONO)]],
                colWidths=[16.6*cm]
            )
            table.setStyle(TableStyle([
                ("BACKGROUND", (0,0), (-1,-1), C_CODEBG),
                ("BOX",        (0,0), (-1,-1), 0.8, C_INDIGO),
                ("LEFTPADDING",(0,0), (-1,-1), 8),
                ("RIGHTPADDING",(0,0),(-1,-1), 8),
                ("TOPPADDING", (0,0), (-1,-1), 6),
                ("BOTTOMPADDING",(0,0),(-1,-1), 6),
            ]))
            story.append(Spacer(1, 3))
            story.append(table)
            story.append(Spacer(1, 4))
            continue

        # ── Blockquote ────────────────────────────────────────────────────────
        if stripped.startswith("> "):
            story.append(Paragraph(inline(stripped[2:]), NOTE))
            i += 1; continue

        # ── Pipe table ────────────────────────────────────────────────────────
        if stripped.startswith("|"):
            tbl_lines = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                tbl_lines.append(lines[i].strip())
                i += 1
            # filter separator rows (---|---)
            data_rows = [r for r in tbl_lines
                         if not re.match(r'^\|[-| :]+\|$', r)]
            if not data_rows:
                continue
            table_data = []
            for ri, row in enumerate(data_rows):
                cells = [c.strip() for c in row.strip("|").split("|")]
                sty_use = TH if ri == 0 else TD
                table_data.append([Paragraph(inline(c), sty_use) for c in cells])
            col_count = max(len(r) for r in table_data)
            col_w = 16.6 * cm / col_count
            tbl = Table(table_data, colWidths=[col_w] * col_count,
                        repeatRows=1)
            tbl.setStyle(TableStyle([
                ("BACKGROUND",    (0,0), (-1,0),  C_INDIGO),
                ("TEXTCOLOR",     (0,0), (-1,0),  C_WHITE),
                ("BACKGROUND",    (0,1), (-1,-1), C_WHITE),
                ("ROWBACKGROUNDS",(0,1), (-1,-1), [C_WHITE, C_LTGREY]),
                ("GRID",          (0,0), (-1,-1), 0.4, C_BORDER),
                ("VALIGN",        (0,0), (-1,-1), "TOP"),
                ("TOPPADDING",    (0,0), (-1,-1), 4),
                ("BOTTOMPADDING", (0,0), (-1,-1), 4),
                ("LEFTPADDING",   (0,0), (-1,-1), 6),
                ("RIGHTPADDING",  (0,0), (-1,-1), 6),
            ]))
            story.append(Spacer(1, 4))
            story.append(tbl)
            story.append(Spacer(1, 6))
            continue

        # ── Bullet list ───────────────────────────────────────────────────────
        if re.match(r'^[-*+] ', stripped):
            story.append(Paragraph("• " + inline(stripped[2:]), BULLET))
            i += 1; continue

        # ── Numbered list ─────────────────────────────────────────────────────
        nm = re.match(r'^(\d+)\. (.+)', stripped)
        if nm:
            story.append(Paragraph(f"{nm.group(1)}. " + inline(nm.group(2)), BULLET))
            i += 1; continue

        # ── Regular paragraph ─────────────────────────────────────────────────
        story.append(Paragraph(inline(stripped), BODY))
        i += 1

    return story

# ── Build PDF ─────────────────────────────────────────────────────────────────
def build():
    doc = SimpleDocTemplate(
        str(PDF_PATH),
        pagesize=A4,
        leftMargin=2.2*cm, rightMargin=2.2*cm,
        topMargin=2*cm,    bottomMargin=2.2*cm,
        title="FinSight — Full Project Journal",
        author="FinSight Team",
    )

    md_text = MD_PATH.read_text(encoding="utf-8")
    story = parse_md(md_text)

    doc.build(story)
    size_kb = PDF_PATH.stat().st_size // 1024
    print(f"PDF written to: {PDF_PATH}  ({size_kb} KB)")

if __name__ == "__main__":
    build()
