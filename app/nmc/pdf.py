"""PDF reports. The man-hour report for a client as a PDF to read and sign: a clear table of approved hours by job family, role and person, and approval boxes
(name, signature, date) kept together at the end so the document can go straight into DocuSign."""
import datetime
import io
import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import BaseDocTemplate, Frame, Image, KeepTogether, PageTemplate, Paragraph, Spacer, Table, TableStyle

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOGO = os.path.join(ROOT, "app", "static", "nmc-logo-black.png")

BLACK, ORANGE = colors.HexColor("#0f0f12"), colors.HexColor("#fe6700")
GREY, LIGHT, LINE = colors.HexColor("#4b4b50"), colors.HexColor("#f1f1f2"), colors.HexColor("#c8c8ca")

WEEK_LIMIT = 13          # up to a quarter of weeks has a column each; a longer period is shown by month so the table still fits the page
MARGIN = 12 * mm
PAGE = landscape(A4)

TEXT = ParagraphStyle("t", fontName="Helvetica", fontSize=8.5, leading=11, textColor=BLACK)
SMALL = ParagraphStyle("s", parent=TEXT, fontSize=7.5, leading=9.5, textColor=GREY)
H1 = ParagraphStyle("h1", parent=TEXT, fontName="Helvetica-Bold", fontSize=18, leading=22)
H2 = ParagraphStyle("h2", parent=TEXT, fontName="Helvetica-Bold", fontSize=11, leading=14, spaceBefore=2, spaceAfter=4)


def num(v):
    """Hours as a figure: blank for nothing, whole numbers without a decimal point."""
    return "" if not v else ("%g" % round(v, 2))


def dmy(iso):
    return "%s/%s/%s" % (iso[8:10], iso[5:7], iso[0:4])


class NumberedCanvas(canvas.Canvas):
    """A canvas that knows how many pages there are, for "Page 2 of 3" on each."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._saved = []

    def showPage(self):
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            self.setFont("Helvetica", 7.5)
            self.setFillColor(GREY)
            self.drawRightString(PAGE[0] - MARGIN, 7 * mm, "Page %d of %d" % (self._pageNumber, total))
            super().showPage()
        super().save()


def columns(weeks):
    """The columns of the table: each week (as dd/mm), or each month when the period is long. Returns (keys, labels, key of a week)."""
    if len(weeks) <= WEEK_LIMIT:
        return list(weeks), ["%s/%s" % (w[8:10], w[5:7]) for w in weeks], lambda w: w
    months = sorted({w[:7] for w in weeks})
    names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return months, ["%s %s" % (names[int(m[5:7]) - 1], m[2:4]) for m in months], lambda w: w[:7]


def table_rows(data, keys, key_of):
    """The report as table rows (cells) with a kind each: 'family', 'role', 'person', 'total' or 'grand'."""
    out = []
    grand = {k: 0.0 for k in keys}
    for family, roles in data["tree"].items():
        fam = {k: 0.0 for k in keys}
        out.append(("family", [family] + [""] * (len(keys) + 1)))
        for (code, role), people in roles.items():
            per_person = []
            role_tot = {k: 0.0 for k in keys}
            for person, d in people.items():
                cells = {k: 0.0 for k in keys}
                for w, h in d["w"].items():
                    cells[key_of(w)] += h
                per_person.append((person, cells))
                for k in keys:
                    role_tot[k] += cells[k]
            if len(per_person) == 1:                                             # one person in the role: a single line carries both
                person, cells = per_person[0]
                out.append(("person", ["%s  (%s %s)" % (person, code, role)] + [num(cells[k]) for k in keys] + [num(sum(cells.values()))]))
            else:
                out.append(("role", ["%s %s" % (code, role)] + [num(role_tot[k]) for k in keys] + [num(sum(role_tot.values()))]))
                for person, cells in per_person:
                    out.append(("person", ["      " + person] + [num(cells[k]) for k in keys] + [num(sum(cells.values()))]))
            for k in keys:
                fam[k] += role_tot[k]
        out.append(("total", ["Total " + family] + [num(fam[k]) for k in keys] + [num(sum(fam.values()))]))
        for k in keys:
            grand[k] += fam[k]
    out.append(("grand", ["Total approved hours for the client"] + [num(grand[k]) for k in keys] + [num(sum(grand.values()))]))
    return out, sum(grand.values())


def signature_boxes(client, prepared_by, produced):
    """Three boxes side by side: who prepared it, NMC's approval and the client's. Name, a tall signature space and a date in each."""
    def box(title, name="", date=""):
        return [Paragraph("<b>%s</b>" % title, TEXT), Paragraph("Name: %s" % name, TEXT), Paragraph("Signature:", TEXT), "", Paragraph("Date: %s" % date, TEXT)]
    cols = [box("Prepared by (NMC)", prepared_by, produced), box("Approved by (NMC)"), box("Approved by (%s)" % client)]
    t = Table([[c[i] for c in cols] for i in range(5)], colWidths=[(PAGE[0] - 2 * MARGIN) / 3.0] * 3, rowHeights=[8 * mm, 9 * mm, 6 * mm, 18 * mm, 9 * mm])
    t.setStyle(TableStyle([("BOX", (0, 0), (0, -1), 0.8, BLACK), ("BOX", (1, 0), (1, -1), 0.8, BLACK), ("BOX", (2, 0), (2, -1), 0.8, BLACK),
                           ("BACKGROUND", (0, 0), (-1, 0), LIGHT), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("LINEBELOW", (0, 1), (-1, 1), 0.4, LINE), ("LINEBELOW", (0, 3), (-1, 3), 0.4, LINE),
                           ("LEFTPADDING", (0, 0), (-1, -1), 6), ("VALIGN", (0, 3), (-1, 3), "TOP")]))
    return t


def manhour_pdf(data, prepared_by="", compress=True):
    """The man-hour report (the dict from reports.manhour_data) as PDF bytes."""
    produced = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
    keys, labels, key_of = columns(data["weeks"])
    rows, total = table_rows(data, keys, key_of)
    client = data["client"]
    period = "%s to %s" % (dmy(data["from"]), dmy(data["to"]))

    width = PAGE[0] - 2 * MARGIN
    first, last = 80 * mm, 20 * mm
    each = min(18 * mm, (width - first - last) / max(1, len(keys)))
    col_w = [width - last - each * len(keys)] + [each] * len(keys) + [last]
    head = ["Person / role"] + labels + ["Total"]
    styles = [("FONTNAME", (0, 0), (-1, -1), "Helvetica"), ("FONTSIZE", (0, 0), (-1, -1), 8), ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
              ("BACKGROUND", (0, 0), (-1, 0), BLACK), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
              ("LINEBELOW", (0, 1), (-1, -1), 0.25, LINE), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
              ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]
    for i, (kind, _) in enumerate(rows, start=1):
        if kind == "family":
            styles += [("BACKGROUND", (0, i), (-1, i), LIGHT), ("FONTNAME", (0, i), (-1, i), "Helvetica-Bold"), ("LINEABOVE", (0, i), (-1, i), 0.8, ORANGE)]
        elif kind == "role":
            styles += [("FONTNAME", (0, i), (-1, i), "Helvetica-Bold")]
        elif kind == "total":
            styles += [("FONTNAME", (0, i), (-1, i), "Helvetica-Bold"), ("LINEABOVE", (0, i), (-1, i), 0.6, BLACK)]
        elif kind == "grand":
            styles += [("FONTNAME", (0, i), (-1, i), "Helvetica-Bold"), ("BACKGROUND", (0, i), (-1, i), BLACK), ("TEXTCOLOR", (0, i), (-1, i), colors.white)]
    table = Table([head] + [cells for _, cells in rows], colWidths=col_w, repeatRows=1)
    table.setStyle(TableStyle(styles))

    story = [Paragraph("Manhours report", H1), Spacer(1, 2 * mm),
             Paragraph("<b>Client:</b> %s &nbsp;&nbsp; <b>Period:</b> %s &nbsp;&nbsp; <b>Produced:</b> %s" % (client, period, produced), TEXT),
             Paragraph("Approved hours only, by job family, role and person. %s" % (
                 "Each column is a week (week commencing, Monday)." if len(keys) == len(data["weeks"]) else
                 "Each column is a month, by the Monday of each week worked."), SMALL),
             Spacer(1, 4 * mm)]
    if len(rows) > 1:
        story.append(table)
    else:
        story.append(Paragraph("No approved hours for %s in this period." % client, TEXT))
    story += [Spacer(1, 8 * mm),
              KeepTogether([Paragraph("Approval", H2),
                            Paragraph("The approved hours above (%s in total) are confirmed as correct for %s, for the period %s." % (num(total) or "0 hours", client, period), TEXT),
                            Spacer(1, 3 * mm), signature_boxes(client, prepared_by, produced)])]

    buf = io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=PAGE, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN + 14 * mm, bottomMargin=MARGIN + 4 * mm,
                          title="Manhours %s %s" % (client, period), author="NMC Energy", pageCompression=1 if compress else 0)

    def decorate(c, d):
        c.saveState()
        if os.path.exists(LOGO):
            c.drawImage(LOGO, MARGIN, PAGE[1] - MARGIN - 9 * mm, width=26 * mm, height=9 * mm, preserveAspectRatio=True, mask="auto")
        c.setFont("Helvetica", 8)
        c.setFillColor(GREY)
        c.drawRightString(PAGE[0] - MARGIN, PAGE[1] - MARGIN - 4 * mm, "Manhours report: %s, %s" % (client, period))
        c.setStrokeColor(ORANGE)
        c.setLineWidth(1.2)
        c.line(MARGIN, PAGE[1] - MARGIN - 11 * mm, PAGE[0] - MARGIN, PAGE[1] - MARGIN - 11 * mm)
        c.restoreState()

    doc.addPageTemplates([PageTemplate(id="p", frames=[Frame(MARGIN, MARGIN + 4 * mm, width, PAGE[1] - 2 * MARGIN - 18 * mm, id="f", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)],
                                       onPage=decorate)])
    doc.build(story, canvasmaker=NumberedCanvas)
    return buf.getvalue()
