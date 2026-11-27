"""The cost team's regular reports (the four they run in CMAP), each filterable by client: Booked v CTR (hours by week), Budget and actual hours per CTR,
the Weekly cost report (money) and the Invoice back up (a month's hours by person and day). Cost controllers only: they show rates and values.

Hours are submitted plus approved (drafts left out), the same basis as the manhour report. A CTR is shown as "CTR No - title" and its stage is the CTR's discipline. Budgets are the latest ones loaded for each CTR (Admin, Budgets)."""
import datetime
from urllib.parse import urlencode

from nmc.core import date_input, dmy, esc, fmt, hidden, monday, sel, sub_row

COST_REPORTS = (("ctr_week", "Booked v CTR"), ("budget_hours", "Budget and actual hours"), ("cost_week", "Weekly cost report"), ("invoice", "Invoice back up"))
COST_HELP = {
    "ctr_week": "Hours booked to each CTR week by week against its budget hours, for one client",
    "budget_hours": "Budget hours, actual hours and hours remaining for each CTR, for one client",
    "cost_week": "Budget value, booked value and remaining value for each CTR that has hours booked, for one client",
    "invoice": "Each person's hours on each CTR day by day for the period, with rate and value, to back up an invoice"}
MAX_WEEKS, MAX_DAYS = 78, 62
GROUPS = (("scope", "Scope"), ("project", "Project"), ("discipline", "Discipline"))

CTR_LABEL = "(s.scope_code || '-' || coalesce(ph.ctr_code, upper(ph.name)) || ' - ' || coalesce(ph.description, s.description))"
STAGE = "coalesce(dp.name, ph.name)"                                    # the CTR's discipline (CMAP's stage), or the CTR's name when it has none
BUDGET = "(select b.hours from ctr_budget b where b.phase_id = ph.phase_id order by b.as_of desc limit 1)"


def clients(con):
    return [(r[0], r[1]) for r in con.execute("select client_id, name from client where is_internal=0 order by name")]


def projects(con, client):
    return [(r[0], r[1]) for r in con.execute("select project_id, project_code || ' - ' || name from project where client_id=? order by project_code", (client,))]


def parse(con, q, kind):
    """The settings from the query: client, optional project, and the dates (defaults depend on the report)."""
    options = clients(con)
    try:
        client = int(q.get("client", [""])[0])
    except ValueError:
        client = 0
    if client not in [c[0] for c in options]:
        client = options[0][0] if options else 0
    try:
        project = int(q.get("project", [""])[0])
    except ValueError:
        project = None
    if project not in [p[0] for p in projects(con, client)]:
        project = None

    def day(key):
        try:
            return datetime.date.fromisoformat(q.get(key, [""])[0])
        except ValueError:
            return None
    first, last = day("from"), day("to")
    today = datetime.date.today()
    if kind == "invoice":
        if not first or not last:
            last = today.replace(day=1) - datetime.timedelta(days=1)
            first = last.replace(day=1)
    else:
        if not first:
            first = day_of(con.execute("select min(t.work_date) from time_entry t join scope s on s.scope_id=t.scope_id where s.client_id=? and t.status<>'Draft'", (client,)).fetchone()[0])                 or datetime.date(today.year, 1, 1)
        last = last or today
    if last < first:
        first, last = last, first
    group = q.get("group", [""])[0]
    return {"clients": options, "client": client, "client_name": dict(options).get(client, ""), "project": project, "from": first, "to": last,
            "group": group if group in dict(GROUPS) else ""}


def day_of(text):
    try:
        return datetime.date.fromisoformat(text) if text else None
    except ValueError:
        return None


def where(p):
    """SQL for the chosen client and project (on scopes s and phases ph), with its arguments."""
    return " and s.client_id=?" + (" and s.project_id=?" if p["project"] else ""), (p["client"],) + ((p["project"],) if p["project"] else ())


def role_budget_value(con, phase_id, client_id, fallback):
    """The value of a CTR's budget when it is split by role: each role's hours at the client's current rate for that role (the client's average rate for a role
    with none). None when the latest budget has no roles."""
    v = con.execute(
        "select sum(br.hours * coalesce((select rt.sales_rate from rate rt where rt.client_id=? and rt.role_id=br.role_id and rt.effective_from <= ? "
        "order by rt.effective_from desc limit 1), ?)) as v from ctr_budget_role br "
        "where br.budget_id = (select budget_id from ctr_budget where phase_id=? order by as_of desc limit 1)",
        (client_id, datetime.date.today().isoformat(), fallback, phase_id)).fetchone()[0]
    return None if v is None else round(float(v), 2)


def report(con, kind, q):
    """One report: {"title", "subtitle", "header", "rows": [(kind, cells)], "left": columns of text, "note", "params"}; kind of a row is 'row', 'sub' or 'grand'."""
    p = parse(con, q, kind)
    w, wargs = where(p)
    rng = (p["from"].isoformat(), p["to"].isoformat())
    base = p["client_name"] + ((" | " + dict(projects(con, p["client"]))[p["project"]]) if p["project"] else "")
    sub = "%s | %s to %s" % (base, dmy(rng[0]), dmy(rng[1]))
    out = {"params": p, "rows": [], "note": ""}
    out["meta"] = ([] if kind == "budget_hours" else ["Date = %s - %s" % (dmy(rng[0]), dmy(rng[1]))]) \
        + ["Project = " + (dict(projects(con, p["client"]))[p["project"]] if p["project"] else "All projects"), "Name = " + p["client_name"]]
    if kind == "ctr_week":
        weeks, d = [], monday(p["from"])
        while d <= p["to"] and len(weeks) < MAX_WEEKS:
            weeks.append(d.isoformat())
            d += datetime.timedelta(days=7)
        found = con.execute(
            "select ph.phase_id, " + CTR_LABEL + " as ctr, " + STAGE + " as stage, " + BUDGET + " as budget, monday_of(t.work_date) as wk, sum(t.hours) as hours "
            "from phase ph join scope s on s.scope_id=ph.scope_id left join discipline dp on dp.discipline_id=ph.discipline_id left join time_entry t on t.phase_id=ph.phase_id and t.status<>'Draft' and t.work_date between ? and ? "
            "where true" + w + " group by ph.phase_id, s.scope_id, dp.discipline_id, wk order by ctr", rng + wargs).fetchall()
        ctrs = {}
        for r in found:
            c = ctrs.setdefault(r["phase_id"], {"ctr": r["ctr"], "stage": r["stage"], "budget": r["budget"], "w": {}})
            if r["wk"]:
                c["w"][r["wk"]] = r["hours"]
        out["header"] = ["Project: Project No - Title", "Stage Name", "Project: Budget Hours"] + [dmy(x) for x in weeks] + ["Total"]
        tot = {x: 0.0 for x in weeks}
        for c in ctrs.values():
            if c["budget"] is None and not c["w"]:
                continue                                                           # nothing budgeted and nothing booked
            total = sum(c["w"].values())
            out["rows"].append(("row", [c["ctr"], c["stage"], c["budget"]] + [c["w"].get(x) for x in weeks] + [total]))
            for x in weeks:
                tot[x] += c["w"].get(x, 0)
        if out["rows"]:
            out["rows"].append(("grand", ["Total", "", sum(r[1][2] or 0 for r in out["rows"])] + [tot[x] or None for x in weeks] + [sum(tot.values())]))
        out.update(title="Booked v CTR", subtitle=sub + " | hours per week commencing", left=2)
    elif kind == "budget_hours":
        found = con.execute(
            "select " + CTR_LABEL + " as ctr, " + BUDGET + " as budget, coalesce((select sum(t.hours) from time_entry t where t.phase_id=ph.phase_id and t.status<>'Draft'), 0) as actual "
            "from phase ph join scope s on s.scope_id=ph.scope_id where true" + w + " order by ctr", wargs).fetchall()
        out["header"] = ["Project No - Title", "Budget Hours", "Actual Hours", "Remaining Hours"]
        for r in found:
            if r["budget"] is None and not r["actual"]:
                continue
            out["rows"].append(("row", [r["ctr"], r["budget"], r["actual"], (r["budget"] - r["actual"]) if r["budget"] is not None else None]))
        if out["rows"]:
            b, a = sum(r[1][1] or 0 for r in out["rows"]), sum(r[1][2] or 0 for r in out["rows"])
            out["rows"].append(("grand", ["Total", b, a, b - sum(r[1][2] or 0 for r in out["rows"] if r[1][1] is not None)]))
        out.update(title="Budget & Actual hours per CTR", subtitle=base + " | actual hours to date, all weeks", left=1,
                   note="Remaining is budget less actual for CTRs that have a budget.")
    elif kind == "cost_week":
        found = con.execute(
            "select ph.phase_id, " + CTR_LABEL + " as ctr, ph.payment_terms, ph.lump_sum_value, " + BUDGET + " as budget, "
            "coalesce((select sum(t2.hours) from time_entry t2 where t2.phase_id=ph.phase_id and t2.status<>'Draft' and t2.rate_id is not null), 0) as all_hours, "
            "coalesce((select sum(t2.hours * r2.sales_rate) from time_entry t2 join rate r2 on r2.rate_id=t2.rate_id where t2.phase_id=ph.phase_id and t2.status<>'Draft'), 0) as all_value, "
            "sum(t.hours * rt.sales_rate) as booked, sum(t.hours) as hours "
            "from phase ph join scope s on s.scope_id=ph.scope_id join time_entry t on t.phase_id=ph.phase_id and t.status<>'Draft' and t.work_date between ? and ? "
            "join rate rt on rt.rate_id=t.rate_id where true" + w + " group by ph.phase_id, s.scope_id order by ctr", rng + wargs).fetchall()
        fallback = con.execute("select coalesce(avg(sales_rate), 0) from rate where client_id=?", (p["client"],)).fetchone()[0]
        out["header"] = ["Project: Project No - Title", "Budget Value", "Booked Value", "Remaining Value"]
        for r in found:
            if r["payment_terms"] == "Lump Sum" and r["lump_sum_value"] is not None:
                budget = r["lump_sum_value"]                                        # a lump sum CTR is budgeted at the price agreed
            elif r["budget"] is not None and role_budget_value(con, r["phase_id"], p["client"], fallback) is not None:
                budget = role_budget_value(con, r["phase_id"], p["client"], fallback)       # a budget split by role is valued role by role at the client's rates
            elif r["budget"] is not None:                                           # budget hours at the average rate booked to the CTR so far (the client's average rate if none yet)
                budget = round(r["budget"] * ((r["all_value"] / r["all_hours"]) if r["all_hours"] else fallback), 2)
            else:
                budget = None
            booked = round(r["booked"], 2)
            out["rows"].append(("row", [r["ctr"], budget, booked, round(budget - booked, 2) if budget is not None else None]))
        if out["rows"]:
            out["rows"].append(("grand", ["Total", round(sum(r[1][1] or 0 for r in out["rows"]), 2), round(sum(r[1][2] for r in out["rows"]), 2),
                                          round(sum(r[1][3] or 0 for r in out["rows"]), 2)]))
        out.update(title="Weekly Cost Report", subtitle=sub + " | only CTRs with hours booked", subtitle_note="Only shows CTRs with hours booked", left=1,
                   note="Booked value is hours x the sales rate on each entry. Budget value is the lump sum for a lump sum CTR, otherwise the budget hours of each role at the client's rate for that role "
                        "(a budget with no role breakdown is valued at the average rate booked to the CTR so far, an estimate), and is left empty where there is no budget.")
    else:   # invoice
        ndays = (p["to"] - p["from"]).days + 1
        days = [p["from"] + datetime.timedelta(days=i) for i in range(min(ndays, MAX_DAYS))]
        if ndays > MAX_DAYS:
            out["note"] = "Showing the first %d days; pick a shorter period." % MAX_DAYS
        found = con.execute(
            "select ph.phase_id, " + CTR_LABEL + " as ctr, " + STAGE + " as stage, a.name as asset, a.po_number, us.name as person, ro.name as role, ro.role_id, t.work_date, sum(t.hours) as hours, "
            "sum(t.hours * rt.sales_rate) as value from time_entry t join phase ph on ph.phase_id=t.phase_id join scope s on s.scope_id=ph.scope_id "
            "left join discipline dp on dp.discipline_id=ph.discipline_id left join asset a on a.asset_id=s.asset_id join app_user us on us.user_id=t.user_id join role ro on ro.role_id=t.worked_role_id join rate rt on rt.rate_id=t.rate_id "
            "where t.status<>'Draft' and t.work_date between ? and ?" + w + " group by ph.phase_id, s.scope_id, dp.discipline_id, a.asset_id, us.user_id, ro.role_id, t.work_date order by ctr, person, ro.role_id, t.work_date",
            (days[0].isoformat(), days[-1].isoformat()) + wargs).fetchall() if days else []
        lines = {}
        for r in found:
            k = (r["ctr"], r["person"], r["role_id"])
            line = lines.setdefault(k, {"ctr": r["ctr"], "stage": r["stage"], "asset": r["asset"] or "", "po": r["po_number"] or "", "person": r["person"], "role": r["role"], "d": {}, "value": 0.0})
            line["d"][r["work_date"]] = r["hours"]
            line["value"] += r["value"]
        out["header"] = ["Project: Project No - Title", "Person: Full Name (Firstname Lastname)", "Role", "Stage Name", "Asset", "PO Number"] + [dmy(d.isoformat()) for d in days] + ["Total", "Value"]
        by_ctr = {}
        for line in lines.values():
            by_ctr.setdefault(line["ctr"], []).append(line)
        grand_d, grand_v = {d.isoformat(): 0.0 for d in days}, 0.0
        for ctr, people in by_ctr.items():
            sub_d, sub_v = {d.isoformat(): 0.0 for d in days}, 0.0
            for i, line in enumerate(people):
                out["rows"].append(("row", [ctr, line["person"], line["role"], line["stage"], line["asset"], line["po"]] + [line["d"].get(d.isoformat()) for d in days]
                                    + [sum(line["d"].values()), round(line["value"], 2)]))
                for d in sub_d:
                    sub_d[d] += line["d"].get(d, 0)
                sub_v += line["value"]
            out["rows"].append(("sub", ["Total " + ctr.split(" - ")[0], "", "", "", "", ""] + [sub_d[d.isoformat()] or None for d in days] + [sum(sub_d.values()), round(sub_v, 2)]))
            for d in grand_d:
                grand_d[d] += sub_d[d]
            grand_v += sub_v
        if out["rows"]:
            out["rows"].append(("grand", ["Total", "", "", "", "", ""] + [grand_d[d.isoformat()] or None for d in days] + [sum(grand_d.values()), round(grand_v, 2)]))
        out.update(title="Invoice back up", subtitle=sub + " | hours per day", left=6,
                   note=out["note"] or "Hours and value (hours x the sales rate on each entry) for each person and role on each CTR, with a total for each CTR.")
    if p["group"] and out["rows"]:
        out["rows"] = regroup(con, out["rows"], out["left"], p, w, wargs)
        out["subtitle"] += " | grouped by " + dict(GROUPS)[p["group"]].lower()
        out["meta"].append("Grouped by = " + dict(GROUPS)[p["group"]])
    return out


def sums(rows, left):
    """Column totals for some rows' cells: numbers from column `left` on are added up, anything else is left blank."""
    return ["" for _ in range(left)] + [round(sum(r[i] for r in rows if isinstance(r[i], (int, float))), 2) if any(isinstance(r[i], (int, float)) for r in rows) else None
                                        for i in range(left, len(rows[0]))]


def regroup(con, rows, left, p, w, wargs):
    """Put the report's CTRs in groups (scope, project or discipline) with a total row for each group, groups in name order. The report's own rows are unchanged:
    every CTR keeps its figures and the grand total still comes last; the groups only reorder them and add their totals."""
    mode = p["group"]
    meta = {}
    for r in con.execute("select " + CTR_LABEL + " as ctr, s.scope_code, s.description, pr.name as project, coalesce(dp.name, 'No discipline') as discipline "
                         "from phase ph join scope s on s.scope_id=ph.scope_id join project pr on pr.project_id=s.project_id left join discipline dp on dp.discipline_id=ph.discipline_id "
                         "where true" + w, wargs):
        label = {"scope": "%s - %s" % (r["scope_code"], r["description"]), "project": r["project"], "discipline": r["discipline"]}[mode]
        meta[r["ctr"]] = ((r["description"], r["scope_code"]) if mode == "scope" else (label,), label)
    grand = [r for r in rows if r[0] == "grand"]
    blocks, block = [], []                                         # a block is one CTR: its rows, and for the invoice its total row
    for kind, cells in rows:
        if kind == "grand":
            continue
        block.append((kind, cells))
        if kind != "row" or (kind == "row" and not any(k == "sub" for k, _ in rows)):
            blocks.append(block)
            block = []
    if block:
        blocks.append(block)
    groups = {}
    for b in blocks:
        ctr = next((c[0] for k, c in b if k == "row" and c[0]), None) or b[0][1][0]
        key = meta.get(ctr, (("~",), "Other"))
        groups.setdefault(key[0], (key[1], []))[1].append(b)
    out = []
    for key in sorted(groups):
        label, bs = groups[key]
        for b in bs:
            out += b
        counted = [c for b in bs for k, c in b if (k == "sub" if any(kk == "sub" for kk, _ in b) else k == "row")]
        total = sums(counted, left)
        total[0] = "Total " + label
        out.append(("group", total))
    return out + grand


def sheet_rows(rep):
    """The report as rows for an Excel sheet: the header first, then every row, with the rows that are totals (bold and shaded)."""
    rows = [rep["header"]] + [cells for _, cells in rep["rows"]]
    bold = tuple(i + 2 for i, (kind, _) in enumerate(rep["rows"]) if kind != "row")
    return rows, bold


def col_name(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def workbook(rep, kind):
    """The report as an Excel file laid out like the cost team's CMAP exports: title, 'Exported' line and the filters in small type, a bold header, plain rows,
    totals in bold between heavy lines, and on the Invoice back up each CTR written once and merged down its people."""
    import io
    import zipfile
    from xml.sax.saxutils import escape

    def clean(v):
        return escape("".join(ch for ch in str(v) if ch in "\t\n\r" or ord(ch) >= 32))

    header, left = rep["header"], rep["left"]
    ncol = len(header)
    centred = set()
    if kind == "ctr_week":
        centred = set(range(3, ncol - 1))
    elif kind == "invoice":
        centred = set(range(6, ncol - 2))
    meta = ["Exported from NMC Timewriting: " + datetime.date.today().strftime("%d/%m/%Y")]
    lines, merges = [], []

    def put(r, c, v, s):
        ref = "%s%d" % (col_name(c), r)
        if v is None or v == "":
            return '<c r="%s" s="%d"/>' % (ref, s)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return '<c r="%s" s="%d"><v>%s</v></c>' % (ref, s, repr(round(float(v), 6)))
        return '<c r="%s" s="%d" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (ref, s, clean(v))

    def row(r, cells):
        lines.append('<row r="%d">%s</row>' % (r, "".join(cells)))

    row(2, [put(2, 0, "%s - %s from NMC Timewriting" % (rep["title"], rep["params"]["client_name"]), 4)])
    if rep.get("subtitle_note"):
        row(3, [put(3, 0, rep["subtitle_note"], 5)])
    row(4, [put(4, 0, meta[0], 5)])
    r = 6
    for m in rep["meta"]:
        row(r, [put(r, 0, m, 5)])
        r += 1
    head = r + 1
    row(head, [put(head, c, h, 7 if c < left else 6) for c, h in enumerate(header)])
    r = head + 1
    widths = [max(len(str(h)) + 2, 12) for h in header]
    group_start, last_ctr = None, None
    for i, (k, cells) in enumerate(rep["rows"]):
        out = []
        if k == "row":
            for c, v in enumerate(cells):
                if kind == "invoice" and c == 0:
                    if v == last_ctr:
                        v = None                                              # the CTR is written once, on its first row, and merged down
                    else:
                        group_start, last_ctr = r, v
                    out.append(put(r, c, v, 10))
                elif c < left:
                    out.append(put(r, c, v, 2))
                else:
                    out.append(put(r, c, v, 1 if c in centred else 3))
                if v not in (None, ""):
                    widths[c] = max(widths[c], len("{:,.2f}".format(v)) + 2 if isinstance(v, (int, float)) else len(str(v)) + 2)
            if kind == "invoice" and (i + 1 == len(rep["rows"]) or rep["rows"][i + 1][0] != "row"):
                if group_start is not None and group_start < r:
                    merges.append("A%d:A%d" % (group_start, r))
                last_ctr = None
        else:
            out = [put(r, c, (v if c >= left else None), 9 if k == "grand" else 8) for c, v in enumerate(cells)]
        row(r, out)
        r += 1
    cap = lambda c, w: min(w, 80 if c == 0 else 40)
    cols = "".join('<col min="%d" max="%d" width="%d" customWidth="1"/>' % (c + 1, c + 1, 11 if c in centred else cap(c, w)) for c, w in enumerate(widths))
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<sheetViews><sheetView tabSelected="1" workbookViewId="0"><pane xSplit="1" ySplit="%d" topLeftCell="B%d" activePane="bottomRight" state="frozen"/></sheetView></sheetViews>'
             '<sheetFormatPr defaultRowHeight="15"/><cols>%s</cols><sheetData>%s</sheetData>%s</worksheet>' % (
                 head, head + 1, cols, "".join(lines), ('<mergeCells count="%d">%s</mergeCells>' % (len(merges), "".join('<mergeCell ref="%s"/>' % m for m in merges))) if merges else ""))
    fmt_ = 'numFmtId="164"'
    xfs = [
        '<xf %s fontId="0" fillId="0" borderId="0" xfId="0"/>',
        '<xf %s fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment horizontal="center"/></xf>',
        '<xf %s fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment horizontal="left"/></xf>',
        '<xf %s fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment horizontal="right"/></xf>',
        '<xf %s fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment horizontal="left"/></xf>',
        '<xf %s fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment horizontal="left"/></xf>',
        '<xf %s fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment horizontal="center"/></xf>',
        '<xf %s fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment horizontal="left"/></xf>',
        '<xf %s fontId="3" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1"><alignment horizontal="right"/></xf>',
        '<xf %s fontId="3" fillId="0" borderId="2" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1"><alignment horizontal="right"/></xf>',
        '<xf %s fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment horizontal="left" vertical="top"/></xf>']
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<numFmts count="1"><numFmt numFmtId="164" formatCode="###,###,###.00;\\-###,###,###.00;0;@"/></numFmts>'
              '<fonts count="4"><font><sz val="11"/><name val="Calibri"/></font><font><sz val="9"/><name val="Calibri"/></font><font><b/><sz val="16"/><name val="Calibri"/></font>'
              '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
              '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
              '<borders count="3"><border><left/><right/><top/><bottom/><diagonal/></border>'
              '<border><left/><right/><top style="thin"><color auto="1"/></top><bottom style="thin"><color auto="1"/></bottom><diagonal/></border>'
              '<border><left/><right/><top style="thick"><color auto="1"/></top><bottom style="thick"><color auto="1"/></bottom><diagonal/></border></borders>'
              '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              '<cellXfs count="%d">%s</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>' % (len(xfs), "".join(x % fmt_ for x in xfs)))
    parts = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Export" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
        "xl/styles.xml": styles, "xl/worksheets/sheet1.xml": sheet}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in parts.items():
            z.writestr(name, data)
    return buf.getvalue()


def num(c, decimals=2):
    """A number as the report shows it: thousands separators and two decimals, blank when there is nothing."""
    return "" if c is None else ("{:,.%df}" % decimals).format(c)


def render_table(header, rows, left, sticky=0):
    """A report table in the house style: a dark header that stays put, numbers on the right to two decimals, light grey subtotal rows and a dark total row.
    rows is [(kind, cells)] with kind 'row', 'sub' or 'grand'; the first `left` columns are text; the first `sticky` columns stay in view when the table is scrolled across."""
    heads = "".join('<th class="%s">%s</th>' % (("k " if i < sticky else "") + ("t" if i < left else "n"), esc(h)) for i, h in enumerate(header))
    trs = []
    for kind, cells in rows:
        tds = []
        for i, c in enumerate(cells):
            cls = ("k " if i < sticky else "") + ("t" if i < left else "n")
            if isinstance(c, (int, float)) and not isinstance(c, bool):
                tds.append('<td class="%s">%s</td>' % (cls, num(c)))
            else:
                tds.append('<td class="%s">%s</td>' % (cls, esc(c if c is not None else "")))
        trs.append('<tr class="%s">%s</tr>' % (kind, "".join(tds)))
    return '<div class="crwrap"><table class="cr"><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>' % (heads, "".join(trs))


def filter_bar(u, kind, fields, link):
    """The filters of a report on one line, with labels above the boxes, a Show button and the Excel download."""
    labels = "".join("<label>%s%s</label>" % (esc(name), box) for name, box in fields)
    return '<form class="crform" method="get" action="/reports">%s%s<input type="submit" value="Show"> <a class="dl" href="%s">Download as Excel</a></form>' % (hidden(u=u, r=kind), labels, esc(link))


def panel(con, u, q, kind):
    """One cost report on the Reports page: the filters, a line saying what it covers, the table and a note on how the figures are made."""
    rep = report(con, kind, q)
    p = rep["params"]
    dated = kind != "budget_hours"
    fields = [("Client", sel("client", p["clients"], p["client"])), ("Project", sel("project", projects(con, p["client"]), p["project"], True, "All projects"))]
    fields.append(("Group by", sel("group", GROUPS, p["group"], True, "No grouping")))
    if dated:
        fields += [("From", date_input("from", p["from"].isoformat())), ("To", date_input("to", p["to"].isoformat()))]
    keep = [("u", u), ("k", kind), ("client", p["client"])] + ([("project", p["project"])] if p["project"] else []) + ([("group", p["group"])] if p["group"] else []) + ([("from", p["from"].isoformat()), ("to", p["to"].isoformat())] if dated else [])
    out = ["<h4>%s</h4>" % esc(rep["title"]), '<p class="crmeta">%s &nbsp;|&nbsp; Exported %s</p>' % (esc(rep["subtitle"]), datetime.datetime.now().strftime("%d/%m/%Y %H:%M")),
           filter_bar(u, kind, fields, "/reports/cost.xlsx?" + urlencode(keep))]
    if rep["rows"]:
        out.append(render_table(rep["header"], rep["rows"], rep["left"], sticky=1))
        out.append('<p class="crmeta">%s %s</p>' % (esc(COST_HELP[kind] + "."), esc(rep["note"])))
    else:
        out.append("<p>Nothing to show for %s%s.</p>" % (esc(p["client_name"]), " in this period" if dated else ""))
    return "".join(out)
