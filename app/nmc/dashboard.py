"""The cost controllers' dashboard: budget against actual hours, drawn as plain SVG charts."""
import datetime
from nmc.core import date_input, esc, fmt, hidden, layout, no_access, permission_of, sel
from nmc.reports import ALL_REPORTS, period_filters


def nice_max(v):
    """A round top for a chart axis, and a step that divides it into four."""
    if v <= 0:
        return 4, 1
    mag = 10 ** (len(str(int(v))) - 1)
    for m in (1, 2, 2.5, 4, 5, 8, 10):
        if m * mag >= v:
            return m * mag, m * mag / 4
    return 10 * mag, 2.5 * mag


DASH_CSS = """<style>
.tiles{display:flex;flex-wrap:wrap;gap:.8rem;margin:0 0 1rem}
.tile{flex:1 1 125px;background:var(--white);border:1px solid var(--line);border-top:3px solid var(--orange);padding:.7rem 1rem}
.tile .v{font-size:1.7rem;font-weight:500;letter-spacing:-.03em;line-height:1.1}
.tile .l{font-size:.8rem;color:var(--grey-dark)}
.tile.warn{border-top-color:#b3261e}.tile.warn .v{color:#b3261e}
.chart{width:100%;height:auto;display:block}
.chart text{font-family:inherit;fill:var(--grey-dark);font-size:12px}
.chart .val{fill:var(--black)}
.chart .grid{stroke:var(--line);stroke-width:1}
.chart rect.hit:hover{fill:rgba(254,103,0,.08)}
.chart a{cursor:pointer;text-decoration:none}.chart a:hover text{fill:var(--orange)}
.key{display:flex;flex-wrap:wrap;gap:1.2rem;font-size:.8rem;color:var(--grey-dark);margin:.4rem 0 .8rem}
.key i{display:inline-block;width:12px;height:12px;margin-right:.35rem;vertical-align:-1px}
</style>"""


def dash_budget_chart(rows, u):
    """Horizontal bars, one per budgeted CTR: the budget as a track with an end tick, approved hours filled, pending hours hatched.
    A CTR over its budget is dark red and says so in words."""
    w, left, right, row_h, top = 900, 170, 210, 30, 8
    scale_max = max(max(r["budget_hours"], r["actual_hours"] + r["pending_hours"]) for r in rows)
    top_v, step = nice_max(scale_max)
    plot = w - left - right
    x = lambda v: left + plot * v / top_v
    h = top + row_h * len(rows) + 26
    o = ['<svg class="chart" viewBox="0 0 %d %d" role="img" aria-label="Budget hours against approved hours for each CTR">' % (w, h),
         '<defs><pattern id="pend" patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)">'
         '<rect width="2.5" height="6" fill="#fe6700"/></pattern></defs>']
    for k in range(5):
        gx = x(step * k)
        o.append('<line class="grid" x1="%.1f" x2="%.1f" y1="%d" y2="%d"/><text x="%.1f" y="%d" text-anchor="middle">%s</text>' % (gx, gx, top, h - 22, gx, h - 6, fmt(step * k)))
    for i, r in enumerate(rows):
        y = top + i * row_h
        bud, act, pend = r["budget_hours"], r["actual_hours"], r["pending_hours"]
        over = bud > 0 and act > bud
        pct = ("%.0f%%" % (100 * act / bud)) if bud else "no budget"
        tip = "%s: budget %s hrs, approved %s hrs, pending %s hrs, %s hrs %s" % (
            r["phase"], fmt(bud), fmt(act), fmt(pend), fmt(abs(bud - act)), "over budget" if over else "remaining")
        fill = "#b3261e" if over else "#fe6700"
        o.append('<a href="/ctr?u=%d&amp;phase=%d"><g><title>%s (click to open the CTR)</title><rect class="hit" x="0" y="%d" width="%d" height="%d" fill="transparent"/>' % (u, r["phase_id"], esc(tip), y, w, row_h))
        o.append('<text x="%d" y="%d" text-anchor="end" class="val">%s</text>' % (left - 10, y + 19, esc(r["phase"])))
        o.append('<rect x="%.1f" y="%d" width="%.1f" height="14" fill="#e5e5e6"/>' % (left, y + 7, x(bud) - left))                 # the budget
        o.append('<rect x="%.1f" y="%d" width="%.1f" height="14" fill="%s"/>' % (left, y + 7, max(x(act) - left, 0), fill))      # approved
        if pend:
            o.append('<rect x="%.1f" y="%d" width="%.1f" height="14" fill="url(#pend)"/>' % (x(act), y + 7, x(act + pend) - x(act)))
        o.append('<rect x="%.1f" y="%d" width="2" height="22" fill="#0f0f12"/>' % (x(bud) - 1, y + 3))                          # the budget line
        o.append('<text x="%d" y="%d" class="val">%s / %s hrs &#183; %s%s</text></g>' % (
            w - right + 12, y + 19, fmt(act), fmt(bud), pct, " &#9650; over" if over else ""))
        o[-1] = o[-1] + "</a>"
    o.append("</svg>")
    return "".join(o)


def dash_week_chart(weeks, clients, colours):
    """Columns of approved hours by week, stacked by client, with a two pixel gap between the segments."""
    w, h, left, bottom, top = 900, 280, 50, 34, 10
    totals = [sum(wk[1].values()) for wk in weeks]
    top_v, step = nice_max(max(totals))
    plot_h, band = h - bottom - top, (w - left - 10) / len(weeks)
    bar = min(band * 0.6, 56)
    y = lambda v: top + plot_h * (1 - v / top_v)
    o = ['<svg class="chart" viewBox="0 0 %d %d" role="img" aria-label="Approved hours by week and client">' % (w, h)]
    for k in range(5):
        gy = y(step * k)
        o.append('<line class="grid" x1="%d" x2="%d" y1="%.1f" y2="%.1f"/><text x="%d" y="%.1f" text-anchor="end">%s</text>' % (left, w - 10, gy, gy, left - 8, gy + 4, fmt(step * k)))
    for i, (wk, by) in enumerate(weeks):
        cx = left + band * i + band / 2
        base = 0.0
        tip = "Week commencing %s/%s/%s: %s hrs approved" % (wk[8:], wk[5:7], wk[:4], fmt(sum(by.values())))
        o.append("<g><title>%s</title>" % esc(tip + "".join("; %s %s" % (c, fmt(by[c])) for c in clients if by.get(c))))
        o.append('<rect class="hit" x="%.1f" y="%d" width="%.1f" height="%d" fill="transparent"/>' % (cx - band / 2, top, band, plot_h))
        for c in clients:
            v = by.get(c, 0)
            if not v:
                continue
            y0, y1 = y(base), y(base + v)
            o.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s" stroke="#fff" stroke-width="1"/>' % (cx - bar / 2, y1, bar, max(y0 - y1, 0), colours[c]))
            base += v
        o.append('<text x="%.1f" y="%.1f" text-anchor="middle" class="val">%s</text>' % (cx, y(base) - 5, fmt(base)))
        o.append('<text x="%.1f" y="%d" text-anchor="middle">%s/%s</text></g>' % (cx, h - 14, wk[8:], wk[5:7]))
    o.append('<text x="%d" y="%d" text-anchor="middle">Week commencing (dd/mm)</text></svg>' % (left + (w - left) / 2, h - 1))
    return "".join(o)


def view_dashboard(con, u, q):
    """The cost controllers' dashboard: budget hours (from P6) against the approved hours booked here, in hours only, and the weekly flow of hours."""
    if permission_of(con, u) != "controller":
        return no_access(con, u, "Dashboard")
    (f_client, f_project, f_scope, f_phase), (f_clients, f_projects, f_scopes, f_phases) = period_filters(con, q, u, ALL_REPORTS)
    chosen = (f_client, f_client, f_project, f_project, f_scope, f_scope, f_phase, f_phase)
    show = q.get("show", [""])[0]
    show = show if show in ("over", "near", "under") else ""

    def a_date(name):
        try:
            return datetime.date.fromisoformat(q.get(name, [""])[0]).isoformat()
        except ValueError:
            return ""
    d_from, d_to = a_date("from"), a_date("to")
    rows = con.execute("select b.* from v_ctr_budget b join phase ph on ph.phase_id=b.phase_id join scope s on s.scope_id=ph.scope_id "
                       "where b.budget_hours is not null and (? is null or s.client_id=?) and (? is null or s.project_id=?) and (? is null or s.scope_id=?) "
                       "and (? is null or ph.phase_id=?) order by b.actual_hours * 1.0 / nullif(b.budget_hours, 0) desc, b.phase", chosen).fetchall()
    n_budgeted = len(rows)
    if show == "over":
        rows = [r for r in rows if r["actual_hours"] > r["budget_hours"]]
    elif show == "near":
        rows = [r for r in rows if r["actual_hours"] >= 0.9 * r["budget_hours"]]
    elif show == "under":
        rows = [r for r in rows if r["actual_hours"] < 0.9 * r["budget_hours"]]
    auto = ' onchange="this.form.submit()"'
    b = [DASH_CSS, "<p><small>Hours only, as P6 holds hours only. Actual is approved hours; pending is submitted hours waiting for approval. Client work only: "
         "internal hours are not budgeted. Click a CTR to open it. The tables are on the <a href=\"/reports?u=%d\">Reports</a> page.</small></p>" % u,
         '<form method="get" action="/dashboard">%s<p>Client %s Project %s Scope %s CTR %s Show %s</p>'
         '<p>Weeks from %s to %s <small>(the weekly chart only: budgets are always compared with all approved hours to date)</small> '
         '<input type="submit" value="Filter"> <input type="button" value="Reset" onclick="location.href=\'/dashboard?u=%d\'"></p></form>' % (
             hidden(u=u),
             sel("client", f_clients, f_client, True, "All clients", auto), sel("project", f_projects, f_project, True, "All projects", auto),
             sel("scope", f_scopes, f_scope, True, "All scopes", auto), sel("phase", f_phases, f_phase, True, "All CTRs", auto),
             sel("show", [("over", "Over budget"), ("near", "At 90% or more"), ("under", "Under 90%")], show, True, "All budgeted CTRs", auto),
             date_input("from", d_from), date_input("to", d_to), u)]
    if rows:
        tb = sum(r["budget_hours"] for r in rows)
        ta = sum(r["actual_hours"] for r in rows)
        tp = sum(r["pending_hours"] for r in rows)
        over = [r for r in rows if r["budget_hours"] and r["actual_hours"] > r["budget_hours"]]
        near = [r for r in rows if r["budget_hours"] and 0.9 * r["budget_hours"] <= r["actual_hours"] <= r["budget_hours"]]
        tile = lambda v, label, cls="": '<div class="tile %s"><div class="v">%s</div><div class="l">%s</div></div>' % (cls, v, esc(label))
        b.append('<div class="tiles">%s</div>' % "".join([
            tile(fmt(tb), "Budget hours (%d CTRs)" % len(rows)), tile(fmt(ta), "Approved hours"), tile(fmt(tp), "Pending approval"),
            tile(fmt(tb - ta), "Remaining hours"), tile(("%.0f%%" % (100 * ta / tb)) if tb else "", "Of budget used"),
            tile(str(len(over)), "Over budget" + (": " + ", ".join(r["phase"] for r in over) if over else ""), "warn" if over else ""),
            tile(str(len(near)), "At 90% or more of budget")]))
        b.append("<h4>Budget against approved hours, by CTR</h4>")
        b.append('<div class="key"><span><i style="background:#e5e5e6"></i>Budget</span><span><i style="background:#fe6700"></i>Approved</span>'
                 '<span><i style="background:repeating-linear-gradient(135deg,#fe6700 0 2px,#fff 2px 5px)"></i>Pending</span>'
                 '<span><i style="background:#b3261e"></i>Over budget</span><span><i style="background:#0f0f12;width:2px;height:12px"></i>Budget line</span></div>')
        b.append(dash_budget_chart(rows, u))
    elif n_budgeted:
        b.append("<p>No CTRs match these filters (%d budgeted CTR%s before the Show filter).</p>" % (n_budgeted, "" if n_budgeted == 1 else "s"))
    elif f_client or f_project or f_scope or f_phase:
        b.append("<p>No budgets are held for the CTRs these filters select. Load them from P6 on Admin, Budgets.</p>")
    else:
        b.append("<p>No budgets have been loaded yet. Load them from P6 on Admin, Budgets and the charts will fill in.</p>")
    # approved hours by week and client
    per = con.execute("select date(t.work_date,'weekday 0','-6 days') as wk, c.name as client, sum(t.hours) as hrs "
                      "from time_entry t join scope s on s.scope_id=t.scope_id join client c on c.client_id=s.client_id "
                      "where t.status='Approved' and c.is_internal=0 and (? is null or s.client_id=?) and (? is null or s.project_id=?) and (? is null or s.scope_id=?) "
                      "and (? is null or t.phase_id=?) and date(t.work_date,'weekday 0','-6 days') between ? and ? group by wk, c.client_id order by wk",
                      chosen + (d_from or "0000-01-01", d_to or "9999-12-31")).fetchall()
    b.append("<h4>Approved hours by week and client</h4>")
    if per:
        clients = list(dict.fromkeys(r["client"] for r in sorted(per, key=lambda r: r["client"])))
        palette = ["#fe6700", "#4b4b50", "#8f8f92", "#0f0f12"]
        colours = {c: palette[i % len(palette)] for i, c in enumerate(clients)}
        weeks = []
        for wk in dict.fromkeys(r["wk"] for r in per):
            weeks.append((wk, {r["client"]: r["hrs"] for r in per if r["wk"] == wk}))
        weeks = weeks[-26:] if (d_from or d_to) else weeks[-12:]   # the last 12 weeks, or up to 26 of the range asked for
        b.append('<div class="key">%s</div>' % "".join('<span><i style="background:%s"></i>%s</span>' % (colours[c], esc(c)) for c in clients))
        b.append(dash_week_chart(weeks, clients, colours))
    else:
        b.append("<p>No approved client hours for these filters.</p>")
    return layout(con, u, "Dashboard", "".join(b))
