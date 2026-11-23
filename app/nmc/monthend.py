"""Month end (cost controllers): who has entered and submitted their time for a calendar month, and the month's hours by CTR.

A month is the calendar month (1 Feb to 28 Feb), whatever day the last one falls on, so a week that runs over the month end counts
on each side of it by date. People are chased from here (by hand for now); nothing is closed or locked from this page."""
import datetime

from nmc.core import dmy, esc, fmt, layout, no_access, permission_of, table


def month_bounds(q):
    """(first day, last day) of the month in q['m'] (YYYY-MM); the last month by default."""
    try:
        first = datetime.date.fromisoformat((q.get("m", [""])[0] or "x") + "-01")
    except ValueError:
        first = datetime.date.today().replace(day=1) - datetime.timedelta(days=1)
        first = first.replace(day=1)
    last = (first.replace(day=28) + datetime.timedelta(days=4)).replace(day=1) - datetime.timedelta(days=1)
    return first, last


def month_people(con, first, last):
    """Everyone active in the month, with their hours by status and the last day they have hours on."""
    rows = con.execute(
        "select p.user_id, p.nmc_id, p.name, r.name as title, "
        "coalesce(sum(t.hours),0) as total, coalesce(sum(case when t.status='Draft' then t.hours end),0) as draft, "
        "coalesce(sum(case when t.status='Submitted' then t.hours end),0) as submitted, "
        "coalesce(sum(case when t.status in ('Approved','Edited') then t.hours end),0) as approved, max(t.work_date) as last_day, "
        "(select count(*) from v_offshore_night n where n.user_id=p.user_id and n.work_date between ? and ?) as nights "
        "from app_user p join role r on r.role_id=p.role_id "
        "left join time_entry t on t.user_id=p.user_id and t.work_date between ? and ? "
        "where p.permission<>'admin' and p.books_time=1 and p.start_date<=? and (p.end_date is null or p.end_date>=?) "
        "group by p.user_id, r.role_id order by p.name", (first.isoformat(), last.isoformat(), first.isoformat(), last.isoformat(), last.isoformat(), first.isoformat())).fetchall()
    out = []
    for r in rows:
        state = ("No hours entered" if not r["total"] else "Not submitted (%s h in draft)" % fmt(r["draft"]) if r["draft"] else "Submitted")
        out.append((r, state))
    return out


def month_ctrs(con, first, last):
    """The month's hours by CTR and skill code, by status, with the value at the client's rate (client work only)."""
    return con.execute(
        "select c.name as client, s.scope_code, (s.scope_code || '-' || coalesce(ph.ctr_code, upper(ph.name))) as ctr, ro.skill_code, "
        "sum(t.hours) as hours, sum(case when t.status='Approved' or t.status='Edited' then t.hours else 0 end) as approved, "
        "sum(case when t.status='Submitted' then t.hours else 0 end) as submitted, sum(case when t.status='Draft' then t.hours else 0 end) as draft, "
        "sum(t.hours * coalesce(rt.sales_rate, 0)) as value "
        "from time_entry t join phase ph on ph.phase_id=t.phase_id join scope s on s.scope_id=ph.scope_id join client c on c.client_id=s.client_id "
        "join role ro on ro.role_id=t.worked_role_id left join rate rt on rt.rate_id=t.rate_id "
        "where t.work_date between ? and ? group by ph.phase_id, t.worked_role_id, c.client_id, s.scope_id, ro.role_id order by c.name, s.scope_code, ph.name, ro.skill_code",
        (first.isoformat(), last.isoformat())).fetchall()


def view_monthend(con, u, q):
    if permission_of(con, u) != "controller":
        return no_access(con, u, "Month end")
    return layout(con, u, "Month end", monthend_body(con, u, q, "/monthend"))


def monthend_body(con, u, q, base, r=None):
    """The month-end report as html. base is the page it sits on (its month links go there), r the report to reopen on the Reports page."""
    rp = ("&amp;r=%s" % r) if r else ""
    first, last = month_bounds(q)
    prev = (first - datetime.timedelta(days=1)).replace(day=1)
    nxt = last + datetime.timedelta(days=1)
    people = month_people(con, first, last)
    this = datetime.date.today().strftime("%Y-%m")
    b = ['<div class="mnav"><span><a href="%s?u=%d&amp;m=%s%s">&lt;&lt; %s</a></span> <span class="cur-month">%s <small>(%s to %s)</small></span> '
         '<span><a href="%s?u=%d&amp;m=%s%s">%s &gt;&gt;</a></span> <span><a href="%s?u=%d&amp;m=%s%s">This month</a></span>'
         '<form method="get" action="%s"><input type="hidden" name="u" value="%d">%s Month <input type="month" name="m" value="%s"> <input type="submit" value="Go"></form></div>'
         '<p class="xl"><a href="/monthend.xlsx?u=%d&amp;m=%s">Download the month as Excel</a></p>' % (
             base, u, prev.strftime("%Y-%m"), rp, prev.strftime("%b %Y"), first.strftime("%B %Y"), first.strftime("%d/%m"), last.strftime("%d/%m"),
             base, u, nxt.strftime("%Y-%m"), rp, nxt.strftime("%b %Y"), base, u, this, rp,
             base, u, ('<input type="hidden" name="r" value="%s">' % r) if r else "", first.strftime("%Y-%m"), u, first.strftime("%Y-%m"))]
    b.append("<p><small>The calendar month, by the date each hour was worked: a week that runs over the month end counts on each side of it. "
             "Nothing is closed from here, so nobody is stopped from entering time they are owed pay for; chase anyone below who has not entered or submitted.</small></p>")
    todo = [(r, s) for r, s in people if s != "Submitted"]
    b.append('<p><input type="button" class="mbtn" id="mb-chase" value="Still to chase (%d)" onclick="showMonthPart(&quot;chase&quot;)"> '
             '<input type="button" class="mbtn" id="mb-people" value="Everyone, by status" onclick="showMonthPart(&quot;people&quot;)"> '
             '<input type="button" class="mbtn" id="mb-ctr" value="Hours by CTR" onclick="showMonthPart(&quot;ctr&quot;)"></p>' % len(todo))
    b.append('<div class="mpart" id="mp-chase">')
    b.append("<h4>Still to chase (%d of %d people)</h4>" % (len(todo), len(people)))
    if todo:
        b.append(table(["ID", "Name", "Job title", "Hours entered", "Last day entered", "Status"],
                       [[r["nmc_id"], r["name"], r["title"], fmt(r["total"]), dmy(r["last_day"]), s] for r, s in todo]))
    else:
        b.append("<p>Everyone active in %s has submitted their hours.</p>" % esc(first.strftime("%B %Y")))
    b.append('</div><div class="mpart" id="mp-people" style="display:none">')
    b.append("<h4>Everyone, by status</h4>")
    b.append(table(["ID", "Name", "Hours entered", "Draft", "Submitted", "Approved", "Offshore nights", "Status"],
                   [[r["nmc_id"], r["name"], fmt(r["total"]), fmt(r["draft"]), fmt(r["submitted"]), fmt(r["approved"]), r["nights"] or "", s] for r, s in people]))
    b.append("<p><small>Offshore nights are for staff only, worked out from the offshore hours (a night is earned when offshore that day and the next), for the allowance payroll.</small></p>")
    ctrs = month_ctrs(con, first, last)
    b.append('</div><div class="mpart" id="mp-ctr" style="display:none">')
    b.append("<h4>Hours by CTR</h4>")
    if ctrs:
        rows = [[r["client"], r["ctr"], r["skill_code"], fmt(r["hours"]), fmt(r["approved"]), fmt(r["submitted"]), fmt(r["draft"]), "%.2f" % r["value"] if r["value"] else ""] for r in ctrs]
        rows.append(["Total", "", "", fmt(sum(r["hours"] for r in ctrs)), fmt(sum(r["approved"] for r in ctrs)), fmt(sum(r["submitted"] for r in ctrs)),
                     fmt(sum(r["draft"] for r in ctrs)), "%.2f" % sum(r["value"] for r in ctrs)])
        b.append(table(["Client", "CTR", "Skill code", "Hours", "Approved", "Submitted", "Draft", "Value (&pound;)"], rows, raw_heads=True))
    else:
        b.append("<p>No hours were booked in this month.</p>")
    b.append("</div>")
    b.append("<script>function showMonthPart(k){document.querySelectorAll('.mpart').forEach(function(e){e.style.display=e.id==='mp-'+k?'block':'none'});"
             "document.querySelectorAll('.mbtn').forEach(function(e){e.style.fontWeight=e.id==='mb-'+k?'bold':'normal'})}showMonthPart('chase')</script>")
    return "".join(b)


def month_sheet(con, q):
    """The month's hours by CTR as rows for an Excel file."""
    first, last = month_bounds(q)
    rows = [["Client", "Scope", "CTR", "Skill code", "Hours", "Approved", "Submitted", "Draft", "Value"]]
    for r in month_ctrs(con, first, last):
        rows.append([r["client"], r["scope_code"], r["ctr"], r["skill_code"], r["hours"], r["approved"], r["submitted"], r["draft"], round(r["value"], 2)])
    return rows, "Month %s" % first.strftime("%Y-%m")
