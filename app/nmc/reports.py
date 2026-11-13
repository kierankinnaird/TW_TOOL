"""Reports: the CTR page and tree, period reports, timesheet grid, manhour report and the Excel downloads."""
import datetime
import io
from urllib.parse import quote, urlencode
from nmc.core import date_input, esc, fmt, hidden, layout, monday, no_access, permission_of, sel, sub_row


REPORT_JS = """<script>
function layout2(){
  var t=document.getElementById('ctr'), rows=t.rows, idx={};
  for(var i=1;i<rows.length;i++){var id=rows[i].getAttribute('data-id'); if(id){idx[id]=i;}}
  var vis={};
  for(var j=1;j<rows.length;j++){
    var r=rows[j], p=r.getAttribute('data-parent'), id=r.getAttribute('data-id'), show;
    if(!p){show=true;} else {show=vis[p]&&rows[idx[p]].getAttribute('data-open')==='1';}
    if(id){vis[id]=show;}
    r.style.display=show?'':'none';
    var tg=r.getElementsByClassName('tg')[0];
    if(tg&&r.getAttribute('data-kids')==='1'){tg.innerHTML=r.getAttribute('data-open')==='1'?'[-]':'[+]';}
  }
}
function tog(el){var r=el; while(r&&r.tagName!=='TR'){r=r.parentNode;} r.setAttribute('data-open',r.getAttribute('data-open')==='1'?'0':'1'); layout2();}
function openTo(n){var rows=document.getElementById('ctr').rows;
  for(var i=1;i<rows.length;i++){var l=parseInt(rows[i].getAttribute('data-level'),10);
    if(rows[i].getAttribute('data-kids')==='1'){rows[i].setAttribute('data-open',l<n?'1':'0');}}
  layout2();}
window.onload=function(){openTo(3);};
</script>"""


def report_view(con, u):
    """What this person may see in the reports. Cost controllers see everything, with £ values and rates. Everyone else sees
    hours only, limited to their own hours plus, if they approve any scopes, the hours booked to those scopes."""
    ctl = permission_of(con, u) == "controller"
    return {"money": ctl, "all": ctl}


ALL_REPORTS = {"money": True, "all": True}


# scopes a person can see hours for: the ones they approve and the ones they have booked to (arguments: user, user)
VISIBLE_SCOPES = ("(select scope_id from v_scope_approver where user_id=? union select scope_id from time_entry where user_id=?)")


def visible_to(rv, u):
    """SQL limiting time entries (t) on scopes (s) to what this person may see, and its arguments."""
    if rv["all"]:
        return "", ()
    return " and (t.user_id=? or exists (select 1 from v_scope_approver va where va.scope_id=s.scope_id and va.user_id=?))", (u, u)


def ctr_tree_rows(con, u=None, rv=ALL_REPORTS):
    """Client > Project > Scope > Phase > Skill code > Person > Entry. Hours/Total are approved only; Pending = Submitted + Edited.
    Sales rates and totals are only put in the page for people who may see £ values."""
    money = rv["money"]
    vis, vargs = visible_to(rv, u)
    ents = con.execute(
        "select c.name as client, pr.project_code, pr.name as project, s.scope_code, s.description, ph.phase_id, (s.scope_code || '-' || coalesce(ph.ctr_code, upper(ph.name))) as phase, "
        "ro.skill_code, ro.name as role, rt.rate_id, rt.sales_rate, u.name as person, t.entry_id, t.work_date, t.hours, t.status "
        "from time_entry t join phase ph on ph.phase_id=t.phase_id join scope s on s.scope_id=ph.scope_id "
        "join project pr on pr.project_id=s.project_id join client c on c.client_id=s.client_id "
        "join rate rt on rt.rate_id=t.rate_id join role ro on ro.role_id=rt.role_id join app_user u on u.user_id=t.user_id "
        "where t.status<>'Draft'" + vis + " order by c.name, pr.name, s.scope_code, ph.name, ro.skill_code, u.name, t.work_date", vargs).fetchall()
    # also list phases that have no booked hours yet, so the structure is visible: all of them for cost controllers,
    # only the scopes they approve for everyone else
    empty = con.execute(
        "select c.name as client, pr.name as project, s.scope_code, s.description, ph.phase_id, (s.scope_code || '-' || coalesce(ph.ctr_code, upper(ph.name))) as phase "
        "from phase ph join scope s on s.scope_id=ph.scope_id join project pr on pr.project_id=s.project_id "
        "join client c on c.client_id=s.client_id where s.status='Open' and c.is_internal=0 and ph.phase_id not in "
        "(select phase_id from time_entry where status<>'Draft')" + ("" if rv["all"] else " and exists (select 1 from v_scope_approver va where va.scope_id=s.scope_id and va.user_id=?)"),
        () if rv["all"] else (u,)).fetchall()

    def mk(label, kind):
        return {"label": label, "kind": kind, "kids": {}, "h": 0.0, "t": 0.0, "p": 0.0, "skill": "", "rate": None, "status": ""}

    root = {}

    def node(parent, key, label, kind):
        if key not in parent:
            parent[key] = mk(label, kind)
        return parent[key]

    def add(n, ent):
        if ent["status"] == "Approved":
            n["h"] += ent["hours"]; n["t"] += ent["hours"] * ent["sales_rate"]
        else:
            n["p"] += ent["hours"]

    for e in ents:
        path = [node(root, e["client"], e["client"], "Client")]
        path.append(node(path[-1]["kids"], e["project"], e["project"], "Project"))
        path.append(node(path[-1]["kids"], e["scope_code"], "%s %s" % (e["scope_code"], e["description"]), "Scope"))
        ph = node(path[-1]["kids"], e["phase_id"], e["phase"], "CTR")
        ph["phase_id"] = e["phase_id"]
        path.append(ph)
        # one line per rate for cost controllers; without rates on show, one line per skill code
        sk = node(path[-1]["kids"], e["rate_id"] if money else e["skill_code"], "%s %s" % (e["skill_code"], e["role"]), "Skill code")
        sk["skill"], sk["rate"] = e["skill_code"], e["sales_rate"]
        path.append(sk)
        pe = node(sk["kids"], e["person"], e["person"], "Person")
        pe["skill"], pe["rate"] = e["skill_code"], e["sales_rate"]
        path.append(pe)
        en = mk(e["work_date"], "Entry")
        en["skill"], en["rate"], en["status"] = e["skill_code"], e["sales_rate"], e["status"]
        pe["kids"]["%s-%d" % (e["work_date"], e["entry_id"])] = en
        path.append(en)
        for n in path:
            add(n, e)
    for e in empty:
        c = node(root, e["client"], e["client"], "Client")
        p = node(c["kids"], e["project"], e["project"], "Project")
        s = node(p["kids"], e["scope_code"], "%s %s" % (e["scope_code"], e["description"]), "Scope")
        node(s["kids"], e["phase_id"], e["phase"], "CTR")["phase_id"] = e["phase_id"]

    def label_html(n):
        if n.get("phase_id") and u is not None:
            return '%s &nbsp;<a href="/ctr?u=%d&amp;phase=%d">Open CTR</a>' % (esc(n["label"]), u, n["phase_id"])
        return esc(n["label"])

    out, counter = [], [0]

    def walk(n, level, parent):
        counter[0] += 1
        rid = "r%d" % counter[0]
        kids = list(n["kids"].values())
        tg = '<span class="tg" style="cursor:pointer;font-family:monospace" onclick="tog(this)">[-]</span> ' if kids else "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; "
        weight = "b" if level < 5 else "span"
        pad = 4 + level * 16
        row = ('<tr data-id="%s" data-parent="%s" data-level="%d" data-kids="%d" data-open="1">'
               '<td>%s</td><td style="padding-left:%dpx"><%s>%s%s</%s></td><td>%s</td>' % (
                   rid, parent, level, 1 if kids else 0, n["kind"], pad, weight, tg, label_html(n), weight, esc(n["skill"])))
        if money:
            row += '<td align="right">%s</td>' % (("%.2f" % n["rate"]) if n["rate"] is not None else "")
        row += '<td align="right"><%s>%s</%s></td>' % (weight, fmt(n["h"]), weight)
        if money:
            row += '<td align="right"><%s>%.2f</%s></td>' % (weight, n["t"], weight)
        row += '<td align="right"><%s>%s</%s></td><td>%s</td></tr>' % (weight, fmt(n["p"]), weight, esc(n["status"]))
        out.append(row)
        for k in kids:
            walk(k, level + 1, rid)

    for c in root.values():
        walk(c, 0, "")
    g_h = sum(c["h"] for c in root.values())
    g_t = sum(c["t"] for c in root.values())
    g_p = sum(c["p"] for c in root.values())
    return "".join(out), g_h, g_t, g_p


def period_filters(con, q, u=None, rv=ALL_REPORTS):
    """Client > Project > Scope > CTR filter for the period report. Each dropdown only lists what fits the choices above it,
    and a choice that no longer fits (e.g. a project of another client) is dropped. Returns (chosen ids, option lists).
    People who may not see everything are only offered the scopes they approve or have booked to."""
    def pick(name, options):
        try:
            v = int(q.get(name, [""])[0])
        except ValueError:
            return None
        return v if v in [o[0] for o in options] else None

    allowed = rv["all"]
    vargs = () if allowed else (u, u)
    in_scopes = "" if allowed else " and s.scope_id in " + VISIBLE_SCOPES
    # Client work only: internal hours have no sales value, so they are not part of the period report (see the Internal hours section).
    clients = [tuple(r) for r in con.execute(
        "select client_id, name from client where is_internal=0"
        + ("" if allowed else " and client_id in (select client_id from scope where scope_id in " + VISIBLE_SCOPES + ")")
        + " order by name", vargs)]
    client = pick("client", clients)
    projects = [(r["project_id"], r["name"] if client else "%s - %s" % (r["client"], r["name"])) for r in con.execute(
        "select p.project_id, p.name, c.name as client from project p join client c on c.client_id=p.client_id "
        "where c.is_internal=0 and (? is null or p.client_id=?)"
        + ("" if allowed else " and p.project_id in (select project_id from scope where scope_id in " + VISIBLE_SCOPES + ")")
        + " order by c.name, p.name", (client, client) + vargs)]
    project = pick("project", projects)
    scopes = [(r["scope_id"], "%s %s" % (r["scope_code"], r["description"])) for r in con.execute(
        "select s.scope_id, s.scope_code, s.description from scope s join client c on c.client_id=s.client_id "
        "where c.is_internal=0 and (? is null or s.client_id=?) and (? is null or s.project_id=?)" + in_scopes
        + " order by s.scope_code", (client, client, project, project) + vargs)]
    scope = pick("scope", scopes)
    phases = [(r["phase_id"], r["ctr_no"]) for r in con.execute(
        "select ph.phase_id, (s.scope_code || '-' || coalesce(ph.ctr_code, upper(ph.name))) as ctr_no, s.scope_code from phase ph join scope s on s.scope_id=ph.scope_id "
        "join client c on c.client_id=s.client_id "
        "where c.is_internal=0 and (? is null or s.client_id=?) and (? is null or s.project_id=?) and (? is null or s.scope_id=?)" + in_scopes
        + " order by s.scope_code, ph.name", (client, client, project, project, scope, scope) + vargs)]
    phase = pick("phase", phases)
    return (client, project, scope, phase), (clients, projects, scopes, phases)


def period_data(con, u, q, rv):
    """The weekly period report: (rows, from, to, chosen filters, filter option lists)."""
    money = rv["money"]
    this_year = datetime.date.today().year
    d_from = q.get("from", [""])[0] or "%d-01-01" % this_year
    d_to = q.get("to", [""])[0] or "%d-12-31" % this_year
    filters = period_filters(con, q, u, rv)
    (client, project, scope, phase) = filters[0]
    chosen = (client, client, project, project, scope, scope, phase, phase)
    if money:
        per = con.execute("select week_start, client, project, scope_code, phase, skill_code, role, hours, total from v_period_report "
                          "where week_start between ? and ? and (scope_code, phase) in "
                          "(select s.scope_code, (s.scope_code || '-' || coalesce(ph.ctr_code, upper(ph.name))) from phase ph join scope s on s.scope_id=ph.scope_id "
                          "where (? is null or s.client_id=?) and (? is null or s.project_id=?) and (? is null or s.scope_id=?) "
                          "and (? is null or ph.phase_id=?)) "
                          "order by week_start, client, project, scope_code, phase, skill_code",
                          (d_from, d_to) + chosen).fetchall()
    else:
        # the view above adds everyone's hours together, so for everyone else the same figures are built from their own entries only
        per = con.execute(
            "select date(t.work_date,'weekday 0','-6 days') as week_start, c.name as client, p.name as project, s.scope_code, "
            "(s.scope_code || '-' || coalesce(ph.ctr_code, upper(ph.name))) as phase, r.skill_code, r.name as role, sum(t.hours) as hours, 0 as total "
            "from time_entry t join phase ph on ph.phase_id=t.phase_id join scope s on s.scope_id=ph.scope_id "
            "join project p on p.project_id=s.project_id join client c on c.client_id=s.client_id "
            "join rate rt on rt.rate_id=t.rate_id join role r on r.role_id=rt.role_id "
            "where t.status='Approved' and date(t.work_date,'weekday 0','-6 days') between ? and ? "
            "and (t.user_id=? or exists (select 1 from v_scope_approver va where va.scope_id=s.scope_id and va.user_id=?)) "
            "and (? is null or s.client_id=?) and (? is null or s.project_id=?) and (? is null or s.scope_id=?) and (? is null or ph.phase_id=?) "
            "group by week_start, ph.phase_id, rt.rate_id order by week_start, client, project, s.scope_code, phase, r.skill_code",
            (d_from, d_to, u, u) + chosen).fetchall()
    return per, d_from, d_to, filters


def view_reports(con, u, q):
    if permission_of(con, u) == "admin":  # Admin (IT) has no reports
        return no_access(con, u, "Reports")
    rv = report_view(con, u)
    money = rv["money"]
    per, d_from, d_to, ((client, project, scope, phase), (clients, projects, scopes, phases)) = period_data(con, u, q, rv)
    auto = ' onchange="this.form.submit()"'
    filtered = any(x is not None for x in (client, project, scope, phase))
    tree_rows, g_h, g_t, g_p = ctr_tree_rows(con, u, rv)
    if money:
        head = ("<tr><th>Level</th><th>Name</th><th>Skill code</th><th>Sales rate</th><th>Actual booked hrs</th>"
                "<th>Total</th><th>Pending hrs</th><th>Status</th></tr>")
        foot = sub_row(["", "Grand total", "", "", fmt(g_h), "%.2f" % g_t, fmt(g_p), ""], right_from=4)
    else:
        head = ("<tr><th>Level</th><th>Name</th><th>Skill code</th><th>Actual booked hrs</th>"
                "<th>Pending hrs</th><th>Status</th></tr>")
        foot = sub_row(["", "Grand total", "", fmt(g_h), fmt(g_p), ""], right_from=3)

    t2 = ["<tr><th>Week commencing</th><th>Client</th><th>Project</th><th>Scope</th><th>CTR</th><th>Skill code</th>"
          "<th>Role</th><th>Hours</th>%s</tr>" % ("<th>Total</th>" if money else "")]
    r_h = r_t = 0.0
    for wk in dict.fromkeys(r["week_start"] for r in per):
        lines = [r for r in per if r["week_start"] == wk]
        wh = wt = 0.0
        for r in lines:
            t2.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td align=\"right\">%s</td>%s</tr>" % (
                esc(r["week_start"]), esc(r["client"]), esc(r["project"]), esc(r["scope_code"]), esc(r["phase"]),
                esc(r["skill_code"]), esc(r["role"]), fmt(r["hours"]), ('<td align="right">%.2f</td>' % r["total"]) if money else ""))
            wh += r["hours"]; wt += r["total"]
        t2.append(sub_row(["", "Subtotal week commencing %s" % wk, "", "", "", "", "", fmt(wh)] + (["%.2f" % wt] if money else []), right_from=7))
        r_h += wh; r_t += wt
    t2.append(sub_row(["", "Total for range", "", "", "", "", "", fmt(r_h)] + (["%.2f" % r_t] if money else []), right_from=7))

    if money:
        tree_note = ("Actual booked hrs are approved hours only. Pending hrs are submitted or edited hours still waiting for approval. "
                     "Click [+] / [-] to open a row down to the person and their timesheet entries. Hours and Total roll up at every level. "
                     "Each CTR is priced from the client rate card by skill code. All weeks to date.")
        period_note = ("These are the same approved hours as the CTR, split by week. Each line here is one week of a CTR line above, "
                       "so it is smaller than the all-weeks figure. When the date range covers every week, the total for range matches the CTR grand total.")
        filtered_note = "Filtered to the client, project, scope or CTR chosen above, so the total for range is only part of the CTR grand total."
    else:
        tree_note = ("Actual booked hrs are approved hours only. Pending hrs are submitted or edited hours still waiting for approval. "
                     "Click [+] / [-] to open a row down to the person and their timesheet entries. You see your own hours and, for any scope you approve, "
                     "everyone's hours on it. All weeks to date.")
        period_note = ("These are the same approved hours as the CTR, split by week. Each line here is one week of a CTR line above, "
                       "so it is smaller than the all-weeks figure.")
        filtered_note = "Filtered to the client, project, scope or CTR chosen above, so the total for range is only part of your hours."

    period_q = urlencode([("u", u), ("from", d_from), ("to", d_to)] + [(k, v) for k, v in (("client", client), ("project", project), ("scope", scope), ("phase", phase)) if v is not None])
    b = ['<p><a href="/reports/grid?u=%d">Timesheet grid: hours by person, code and day</a> (ready-made layouts you can tailor) &nbsp; '
         '<a href="/reports/period.xlsx?%s">Download the period report as Excel</a>%s</p>' % (
             u, esc(period_q), (' &nbsp; <a href="/reports/manhours?u=%d">Manhour report by client (people and roles, Excel)</a>' % u) if manhour_clients(con, u) else ""),
         "<h4>CTR (Cost Time Resource) - by client, project, scope and CTR</h4>",
         "<p><small>%s</small></p>" % tree_note,
         '<p><input type="button" value="Expand all" onclick="openTo(9)"> <input type="button" value="Collapse all" onclick="openTo(0)"> '
         '<input type="button" value="Show to CTR" onclick="openTo(3)"> <input type="button" value="Show to skill code" onclick="openTo(4)"> '
         '<input type="button" value="Show to person" onclick="openTo(5)"> <input type="button" value="Show to entries" onclick="openTo(6)"></p>',
         '<table id="ctr" border="1" cellpadding="3" cellspacing="0">%s%s%s</table>' % (head, tree_rows, foot),
         REPORT_JS,
         "<h4>Period reports (weekly) - approved hours</h4>",
         "<p><small>%s</small></p>" % period_note,
         '<form method="get" action="/reports">%s<p>Client %s Project %s Scope %s CTR %s</p>'
         '<p>From %s To %s '
         '<input type="submit" value="Show"> <input type="button" value="Reset" onclick="location.href=\'/reports?u=%d\'"></p></form>' % (
             hidden(u=u),
             sel("client", clients, client, True, "All clients", auto), sel("project", projects, project, True, "All projects", auto),
             sel("scope", scopes, scope, True, "All scopes", auto), sel("phase", phases, phase, True, "All CTRs", auto),
             date_input("from", d_from), date_input("to", d_to), u),
         "<p><small>%s</small></p>" % filtered_note if filtered else "",
         '<table border="1" cellpadding="3" cellspacing="0">%s</table>' % "".join(t2)]

    # Internal hours: paid by NMC, not billed, so no rate and no total. Same date range, not affected by the client filters above.
    ivis, iargs = visible_to(rv, u)
    internal = con.execute(
        "select date(t.work_date,'weekday 0','-6 days') as week_start, s.scope_code, s.description, u.name as person, ro.skill_code, "
        "sum(case when t.status='Approved' then t.hours else 0 end) as approved, "
        "sum(case when t.status in ('Submitted','Edited') then t.hours else 0 end) as pending "
        "from time_entry t join scope s on s.scope_id=t.scope_id join client c on c.client_id=s.client_id "
        "join app_user u on u.user_id=t.user_id join role ro on ro.role_id=t.worked_role_id "
        "where c.is_internal=1 and t.status<>'Draft' and date(t.work_date,'weekday 0','-6 days') between ? and ?" + ivis +
        " group by week_start, s.scope_id, u.user_id, ro.role_id order by week_start, s.scope_code, u.name", (d_from, d_to) + iargs).fetchall()
    b.append("<h4>Internal hours (paid by NMC, not billed to a client)</h4>")
    b.append("<p><small>Hours booked to Internal codes, for the date range above. They have no sales rate, so there is no total value, "
             "and they are not part of the CTR or the period report. Approved hrs are approved; pending hrs are submitted or edited and waiting for approval.</small></p>")
    if internal:
        t3 = ["<tr><th>Week commencing</th><th>Internal code</th><th>Person</th><th>Skill code</th><th>Approved hrs</th><th>Pending hrs</th></tr>"]
        for r in internal:
            t3.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td align="right">%s</td><td align="right">%s</td></tr>' % (
                esc(r["week_start"]), esc("%s %s" % (r["scope_code"], r["description"])), esc(r["person"]), esc(r["skill_code"]),
                fmt(r["approved"]), fmt(r["pending"])))
        t3.append(sub_row(["", "Total for range", "", "", fmt(sum(r["approved"] for r in internal)), fmt(sum(r["pending"] for r in internal))], right_from=4))
        b.append('<table border="1" cellpadding="3" cellspacing="0">%s</table>' % "".join(t3))
    else:
        b.append("<p>No internal hours in this date range.</p>")

    # Allowances claimed in the date range: days per person and allowance, approved and waiting
    avis, aargs = ("", ()) if rv["all"] else (" and (a.user_id=? or exists (select 1 from v_scope_approver va where va.scope_id=s.scope_id and va.user_id=?))", (u, u))
    allow_rows = con.execute(
        "select bu.name as person, at.name as allowance, sum(a.status='Approved') as approved, sum(a.status='Submitted') as waiting "
        "from allowance_entry a join scope s on s.scope_id=a.scope_id join app_user bu on bu.user_id=a.user_id join allowance_type at on at.allowance_id=a.allowance_id "
        "where a.status<>'Draft' and date(a.work_date,'weekday 0','-6 days') between ? and ?" + avis +
        " group by a.user_id, a.allowance_id order by bu.name, a.allowance_id", (d_from, d_to) + aargs).fetchall()
    b.append("<h4>Allowances (offshore allowance)</h4>")
    b.append("<p><small>Days claimed in the date range above, by person and allowance. Approved days are signed off; waiting days are submitted and not yet approved. "
             "No amounts are held yet.</small></p>")
    if allow_rows:
        b.append('<table border="1" cellpadding="3" cellspacing="0"><tr><th>Person</th><th>Allowance</th><th>Approved days</th><th>Waiting days</th></tr>%s%s</table>' % (
            "".join('<tr><td>%s</td><td>%s</td><td align="right">%d</td><td align="right">%d</td></tr>' % (esc(r["person"]), esc(r["allowance"]), r["approved"], r["waiting"]) for r in allow_rows),
            sub_row(["Total", "", str(sum(r["approved"] for r in allow_rows)), str(sum(r["waiting"] for r in allow_rows))], right_from=2)))
    else:
        b.append("<p>No allowances in this date range.</p>")

    # Hours by where they were worked and whether they were overtime (hours only). Client and internal hours, approved, same date range.
    lvis, largs = visible_to(rv, u)
    by_loc = con.execute(
        "select lo.name as location, sum(case when t.overtime=0 then t.hours else 0 end) as normal, "
        "sum(case when t.overtime=1 then t.hours else 0 end) as ot "
        "from time_entry t join scope s on s.scope_id=t.scope_id join location lo on lo.location_id=t.location_id "
        "where t.status='Approved' and date(t.work_date,'weekday 0','-6 days') between ? and ?" + lvis +
        " group by lo.location_id order by lo.location_id", (d_from, d_to) + largs).fetchall()
    b.append("<h4>Hours by location and overtime (approved)</h4>")
    b.append("<p><small>All approved hours in the date range above, client and internal, by where they were worked and whether they were overtime.%s</small></p>" % (
        " Overtime has no separate rate yet, so overtime hours are valued at the normal rate in the totals earlier on this page." if money else ""))
    if by_loc:
        t4 = ["<tr><th>Location</th><th>Normal hrs</th><th>Overtime hrs</th><th>Total hrs</th></tr>"]
        for r in by_loc:
            t4.append('<tr><td>%s</td><td align="right">%s</td><td align="right">%s</td><td align="right">%s</td></tr>' % (
                esc(r["location"]), fmt(r["normal"]), fmt(r["ot"]), fmt(r["normal"] + r["ot"])))
        n_all, o_all = sum(r["normal"] for r in by_loc), sum(r["ot"] for r in by_loc)
        t4.append(sub_row(["Total", fmt(n_all), fmt(o_all), fmt(n_all + o_all)], right_from=1))
        b.append('<table border="1" cellpadding="3" cellspacing="0">%s</table>' % "".join(t4))
    else:
        b.append("<p>No approved hours in this date range.</p>")
    if permission_of(con, u) == "controller":
        brows = con.execute("select * from v_ctr_budget where budget_hours is not null or actual_hours>0 or pending_hours>0 order by client, phase").fetchall()
        b.append("<h4>Budget against actual hours (from P6)</h4>")
        b.append("<p><small>Budget hours are the latest load from P6 (Admin, Budgets). Actual is approved hours to date, all weeks; pending is submitted hours waiting for approval. "
                 "Remaining is budget less actual; used is actual as a percentage of budget. A CTR with hours but no budget is shown as not budgeted.</small></p>")
        if brows:
            t4 = ["<tr><th>Client</th><th>CTR No</th><th>Budget hrs</th><th>As of</th><th>Actual hrs</th><th>Pending hrs</th><th>Remaining hrs</th><th>Used</th></tr>"]
            for r in brows:
                bh = r["budget_hours"]
                t4.append('<tr><td>%s</td><td>%s</td><td align="right">%s</td><td>%s</td><td align="right">%s</td><td align="right">%s</td><td align="right">%s</td><td align="right">%s</td></tr>' % (
                    esc(r["client"]), esc(r["phase"]), fmt(bh) if bh is not None else "not budgeted", esc(r["as_of"] or ""), fmt(r["actual_hours"]), fmt(r["pending_hours"]),
                    fmt(bh - r["actual_hours"]) if bh is not None else "", ("%.0f%%" % (100 * r["actual_hours"] / bh)) if bh else ""))
            tb = sum(r["budget_hours"] or 0 for r in brows)
            ta = sum(r["actual_hours"] for r in brows if r["budget_hours"] is not None)
            t4.append(sub_row(["", "Total (budgeted CTRs)", fmt(tb), "", fmt(ta), "", fmt(tb - ta), ("%.0f%%" % (100 * ta / tb)) if tb else ""], right_from=2))
            b.append('<table border="1" cellpadding="3" cellspacing="0">%s</table>' % "".join(t4))
        else:
            b.append("<p>No budgets loaded and no client hours yet.</p>")

    return layout(con, u, "Reports", "".join(b))


# ---------- Timesheet grid report, layouts and Excel downloads (R27, R29, R30) ----------
GRID_COLS = [("person", "Person", "u.name"), ("client", "Client", "c.name"), ("project", "Project", "pr.name"),
             ("scope", "Scope", "s.scope_code || ' ' || s.description"), ("phase", "CTR", "(s.scope_code || '-' || coalesce(ph.ctr_code, upper(ph.name)))"),
             ("skill", "Skill code", "ro.skill_code"), ("loc", "Location", "lo.name"),
             ("type", "Type", "case when t.overtime=1 then 'Overtime' else 'Normal' end")]


# ready-made layouts: name, the columns that group the hours, and how the time is split across the page (day, week or one total)
GRID_LAYOUTS = [("pcd", "Person by code by day", ["person", "client", "project", "scope", "phase", "skill", "loc", "type"], "day"),
                ("pd", "Person by day", ["person"], "day"),
                ("pw", "Person by week", ["person"], "week"),
                ("cpt", "Code by person (total)", ["client", "project", "scope", "phase", "person"], "none"),
                ("cw", "Code by week", ["client", "project", "scope", "phase"], "week"),
                ("ltw", "Location and type by week", ["loc", "type"], "week")]


GRID_BY = [("day", "each day"), ("week", "each week"), ("none", "one total")]


GRID_MAX_DAYS = 42


def grid_data(con, u, q, rv):
    """The timesheet grid: hours grouped by the chosen columns (a ready-made layout, or ticked columns) with days, weeks or a
    single total across the page. Returns a dict with the effective settings, the header, the rows and the total row.
    Only what this person may see is included, and values only for cost controllers."""
    money = rv["money"]
    vis, vargs = visible_to(rv, u)
    layouts = {k: (name, cols, by) for k, name, cols, by in GRID_LAYOUTS}
    layout = q.get("layout", ["pcd"])[0]
    if layout not in layouts:
        layout = "pcd"
    tailored = q.get("tailor", [""])[0] == "1" and q.get("lay_prev", [""])[0] == layout  # a changed layout starts again from its preset
    keys = [c[0] for c in GRID_COLS]
    cols = [k for k in keys if k in q.get("col", [])] if tailored else list(layouts[layout][1])
    by = q.get("by", [""])[0] if tailored and q.get("by", [""])[0] in dict(GRID_BY) else layouts[layout][2]
    status = "all" if q.get("st", [""])[0] == "all" else "approved"

    def pick_date(name, default):
        try:
            return datetime.date.fromisoformat(q.get(name, [""])[0])
        except ValueError:
            return default

    latest = con.execute("select max(t.work_date) from time_entry t join scope s on s.scope_id=t.scope_id where t.status<>'Draft'" + vis, vargs).fetchone()[0]
    first = monday(datetime.date.fromisoformat(latest)) if latest else monday(datetime.date.today())
    d_from, d_to = pick_date("from", first), pick_date("to", first + datetime.timedelta(days=6))
    if d_to < d_from:
        d_from, d_to = d_to, d_from
    notes = []
    if by == "day" and (d_to - d_from).days + 1 > GRID_MAX_DAYS:
        by = "week"
        notes.append("That is more than %d days, so the hours are shown for each week instead of each day." % GRID_MAX_DAYS)

    base = ("from time_entry t join scope s on s.scope_id=t.scope_id join phase ph on ph.phase_id=t.phase_id "
            "join project pr on pr.project_id=s.project_id join client c on c.client_id=s.client_id "
            "join app_user u on u.user_id=t.user_id join role ro on ro.role_id=t.worked_role_id "
            "join location lo on lo.location_id=t.location_id left join rate rt on rt.rate_id=t.rate_id where t.status<>'Draft'" + vis)
    options = {
        "client": [tuple(r) for r in con.execute("select distinct c.client_id, c.name " + base + " order by c.name", vargs)],
        "scope": [(r[0], "%s %s" % (r[1], r[2])) for r in con.execute("select distinct s.scope_id, s.scope_code, s.description " + base + " order by s.scope_code", vargs)],
        "person": [tuple(r) for r in con.execute("select distinct u.user_id, u.name " + base + " order by u.name", vargs)]}
    chosen = {}
    for k, col in (("client", "c.client_id"), ("scope", "s.scope_id"), ("person", "u.user_id")):
        try:
            v = int(q.get(k, [""])[0])
        except ValueError:
            v = None
        chosen[k] = v if v in [o[0] for o in options[k]] else None
    extra, eargs = "", []
    for k, col in (("client", "c.client_id"), ("scope", "s.scope_id"), ("person", "u.user_id")):
        if chosen[k] is not None:
            extra += " and %s=?" % col
            eargs.append(chosen[k])
    sql_cols = "".join(", %s as k_%s" % (e, k) for k, _, e in GRID_COLS if k in cols)
    statuses = "('Approved')" if status == "approved" else "('Approved','Submitted','Edited')"
    ents = con.execute("select t.work_date, t.hours, coalesce(rt.sales_rate, 0) as rate%s %s and t.status in %s and t.work_date between ? and ?%s "
                       "order by t.work_date" % (sql_cols, base, statuses, extra),
                       list(vargs) + [d_from.isoformat(), d_to.isoformat()] + eargs).fetchall()

    if by == "day":
        buckets = [d_from + datetime.timedelta(days=i) for i in range((d_to - d_from).days + 1)]
        labels = [d.strftime("%a %d/%m") for d in buckets]
        key_of = lambda iso: iso
        bucket_keys = [d.isoformat() for d in buckets]
    elif by == "week":
        w = monday(d_from)
        bucket_keys = []
        while w <= d_to:
            bucket_keys.append(w.isoformat())
            w += datetime.timedelta(days=7)
        labels = ["w/c %s" % datetime.date.fromisoformat(k).strftime("%d/%m") for k in bucket_keys]
        key_of = lambda iso: monday(datetime.date.fromisoformat(iso)).isoformat()
    else:
        bucket_keys, labels, key_of = [], [], (lambda iso: "")
    groups = {}
    for e in ents:
        g = groups.setdefault(tuple(e["k_" + k] for k in cols) or ("All hours",), {"b": {}, "h": 0.0, "v": 0.0})
        g["b"][key_of(e["work_date"])] = g["b"].get(key_of(e["work_date"]), 0.0) + e["hours"]
        g["h"] += e["hours"]
        g["v"] += e["hours"] * e["rate"]
    names = {k: l for k, l, _ in GRID_COLS}
    nc = max(len(cols), 1)
    header = [names[k] for k in cols] or ["Hours"]
    header += labels + ["Total hours"] + (["Value"] if money else [])
    rows = [list(key) + [groups[key]["b"].get(k) or None for k in bucket_keys] + [groups[key]["h"]] + ([round(groups[key]["v"], 2)] if money else [])
            for key in sorted(groups)]
    total_row = (["Total"] + [None] * (nc - 1) + [sum(g["b"].get(k, 0.0) for g in groups.values()) or None for k in bucket_keys]
                 + [sum(g["h"] for g in groups.values())] + ([round(sum(g["v"] for g in groups.values()), 2)] if money else []))
    return {"layout": layout, "cols": cols, "by": by, "status": status, "from": d_from, "to": d_to, "chosen": chosen, "options": options,
            "header": header, "rows": rows, "total": total_row, "notes": notes, "money": money, "ncols": nc}


def grid_query(g, u):
    """The settings of a grid as a query string, so the Excel link gives exactly what is on screen."""
    pairs = [("u", u), ("layout", g["layout"]), ("lay_prev", g["layout"]), ("tailor", "1"), ("by", g["by"]), ("st", g["status"]),
             ("from", g["from"].isoformat()), ("to", g["to"].isoformat())]
    pairs += [("col", c) for c in g["cols"]]
    pairs += [(k, v) for k, v in g["chosen"].items() if v is not None]
    return urlencode(pairs)


def manhour_clients(con, u):
    """The clients whose manhour report this person can run: every client for a cost controller; for anyone else the clients where they are the main
    approver (the project manager) or a secondary approver on a scope. Internal is never offered: it has no client to report to."""
    if permission_of(con, u) == "controller":
        return [(r[0], r[1]) for r in con.execute("select client_id, name from client where is_internal=0 order by name")]
    if permission_of(con, u) == "admin":
        return []
    return [(r[0], r[1]) for r in con.execute(
        "select distinct c.client_id, c.name from client c join scope s on s.client_id=c.client_id join v_scope_approver va on va.scope_id=s.scope_id "
        "where c.is_internal=0 and va.user_id=? order by c.name", (u,))]


def manhour_data(con, u, q):
    """The manhour report for one client: approved hours by job family, role and person, one column per week, with subtotals. Hours only, never money.
    Returns None when this person has no client to report on."""
    clients = manhour_clients(con, u)
    if not clients:
        return None
    try:
        cid = int(q.get("client", [""])[0])
    except ValueError:
        cid = 0
    if cid not in [c[0] for c in clients]:
        cid = clients[0][0]
    this_year = datetime.date.today().year
    d_from = q.get("from", [""])[0] or "%d-01-01" % this_year
    d_to = q.get("to", [""])[0] or "%d-12-31" % this_year
    vis, vargs = visible_to(report_view(con, u), u)
    found = con.execute(
        "select date(t.work_date,'weekday 0','-6 days') as wk, ro.family, ro.skill_code, ro.name as role, us.name as person, "
        "sum(case when t.status='Approved' then t.hours else 0 end) as approved, "
        "sum(case when t.status in ('Submitted','Edited') then t.hours else 0 end) as pending "
        "from time_entry t join scope s on s.scope_id=t.scope_id join role ro on ro.role_id=t.worked_role_id join app_user us on us.user_id=t.user_id "
        "where s.client_id=? and t.status<>'Draft' and date(t.work_date,'weekday 0','-6 days') between ? and ?" + vis +
        " group by wk, ro.role_id, us.user_id order by ro.family, ro.skill_code, us.name, wk", (cid, d_from, d_to) + vargs).fetchall()
    weeks = sorted({r["wk"] for r in found})
    tree = {}  # family -> (skill code, role) -> person -> {"w": {week: approved}, "pending": hours}
    for r in found:
        person = tree.setdefault(r["family"], {}).setdefault((r["skill_code"], r["role"]), {}).setdefault(r["person"], {"w": {}, "pending": 0.0})
        person["w"][r["wk"]] = person["w"].get(r["wk"], 0.0) + r["approved"]
        person["pending"] += r["pending"]
    return {"clients": clients, "client_id": cid, "client": dict(clients)[cid], "from": d_from, "to": d_to, "weeks": weeks, "tree": tree}


def manhour_lines(data):
    """The report as rows of cells, each flagged: 'head', 'row', 'role' (subtotal), 'family' (subtotal) or 'grand'."""
    weeks = data["weeks"]
    dmy = lambda iso: "%s/%s/%s" % (iso[8:], iso[5:7], iso[:4])
    lines = [("head", ["Job family", "Skill code", "Role", "Person"] + [dmy(w) for w in weeks] + ["Approved hrs", "Pending hrs"])]
    grand = {w: 0.0 for w in weeks}
    grand_p = 0.0

    def total_cells(per_week, pending):
        return [per_week[w] or None for w in weeks] + [sum(per_week.values()), pending]   # a week with no hours is left blank
    for family, roles in data["tree"].items():
        fam = {w: 0.0 for w in weeks}
        fam_p = 0.0
        for (code, role), people in roles.items():
            rol = {w: 0.0 for w in weeks}
            rol_p = 0.0
            for person, d in people.items():
                per = {w: d["w"].get(w, 0.0) for w in weeks}
                lines.append(("row", [family, code, role, person] + total_cells(per, d["pending"])))
                for w in weeks:
                    rol[w] += per[w]
                rol_p += d["pending"]
            if len(people) > 1:  # a role with one person needs no subtotal under that person's line
                lines.append(("role", ["", code, "Total " + role, ""] + total_cells(rol, rol_p)))
            for w in weeks:
                fam[w] += rol[w]
            fam_p += rol_p
        lines.append(("family", ["Total " + family, "", "", ""] + total_cells(fam, fam_p)))
        for w in weeks:
            grand[w] += fam[w]
        grand_p += fam_p
    if data["tree"]:
        lines.append(("grand", ["Total for the client", "", "", ""] + total_cells(grand, grand_p)))
    return lines


def view_manhours(con, u, q):
    """The manhour report for a client, for the client's project manager (its main approver) and the cost controllers."""
    data = manhour_data(con, u, q)
    if data is None:
        return layout(con, u, "Reports", "<p>The manhour report is for a client's project manager (its main approver) and for cost controllers.</p>")
    lines = manhour_lines(data)
    auto = ' onchange="this.form.submit()"'
    qs = urlencode([("u", u), ("client", data["client_id"]), ("from", data["from"]), ("to", data["to"])])
    b = ["<p><a href=\"/reports?u=%d\">&lt;&lt; Reports</a></p>" % u,
         "<p><small>Approved hours for one client, broken down by job family, role and the people who worked in it, one column per week (week commencing). "
         "Hours only. Pending hrs are submitted hours still waiting for approval and are not in the approved figures.</small></p>",
         '<form method="get" action="/reports/manhours">%s<p>Client %s From %s To %s <input type="submit" value="Show"> '
         '<a href="/reports/manhours.xlsx?%s">Download as Excel</a></p></form>' % (
             hidden(u=u), sel("client", data["clients"], data["client_id"], False, "", auto), date_input("from", data["from"]), date_input("to", data["to"]), esc(qs))]
    if len(lines) > 1:
        out = []
        for kind, cells in lines:
            tag = "th" if kind == "head" else "td"
            bold = kind in ("role", "family", "grand")
            tds = []
            for i, c in enumerate(cells):
                txt = fmt(c) if isinstance(c, float) else esc(c)
                if bold and txt:
                    txt = "<b>%s</b>" % txt
                tds.append("<%s%s>%s</%s>" % (tag, ' align="right"' if i >= 4 and kind != "head" else "", txt, tag))
            out.append('<tr%s>%s</tr>' % (' style="background:#f1f1f2"' if kind in ("role", "family") else ("" if kind != "grand" else ' style="border-top:2px solid #0f0f12"'), "".join(tds)))
        b.append('<table border="1" cellpadding="3" cellspacing="0">%s</table>' % "".join(out))
    else:
        b.append("<p>No hours for %s in this date range.</p>" % esc(data["client"]))
    return layout(con, u, "Reports", "".join(b))


def manhour_sheet(data):
    lines = manhour_lines(data)
    rows = [[None if (isinstance(c, str) and c == "" and i >= 4) else c for i, c in enumerate(cells)] for kind, cells in lines]
    bold = tuple(i + 1 for i, (kind, _) in enumerate(lines) if kind != "row")
    return rows, bold, "Manhours %s" % data["client"][:20]


def view_grid(con, u, q):
    """Hours by person, code and day (or week, or total) with ready-made layouts that can be tailored, and an Excel download."""
    if permission_of(con, u) == "admin":  # Admin (IT) has no reports
        return no_access(con, u, "Reports")
    g = grid_data(con, u, q, report_view(con, u))
    auto = ' onchange="this.form.submit()"'
    form = ('<form method="get" action="/reports/grid">%s<input type="hidden" name="lay_prev" value="%s"><input type="hidden" name="tailor" value="1">'
            "<p>Layout %s &nbsp; Time across the page %s &nbsp; Show %s</p>"
            "<p>Columns %s</p>"
            "<p>From %s To %s &nbsp; Client %s Scope %s Person %s</p>"
            '<p><input type="submit" value="Show"> <input type="button" value="Reset" onclick="location.href=\'/reports/grid?u=%d\'"> '
            '<a href="/reports/grid.xlsx?%s">Download as Excel</a></p></form>' % (
                hidden(u=u), g["layout"],
                sel("layout", [(k, n) for k, n, _, _ in GRID_LAYOUTS], g["layout"], False, "-", auto),
                sel("by", GRID_BY, g["by"]),
                sel("st", [("approved", "approved hours only"), ("all", "approved and waiting for approval")], g["status"]),
                " ".join('<label><input type="checkbox" name="col" value="%s"%s> %s</label>' % (k, " checked" if k in g["cols"] else "", l)
                         for k, l, _ in GRID_COLS),
                date_input("from", g["from"].isoformat()), date_input("to", g["to"].isoformat()),
                sel("client", g["options"]["client"], g["chosen"]["client"], True, "All clients"),
                sel("scope", g["options"]["scope"], g["chosen"]["scope"], True, "All scopes"),
                sel("person", g["options"]["person"], g["chosen"]["person"], True, "Everyone"), u, esc(grid_query(g, u))))
    head = "".join("<th>%s</th>" % esc(h) for h in g["header"])
    nc = g["ncols"]

    def line(r, bold=False):
        out = []
        for i, v in enumerate(r):
            if i < nc:
                text, attr = esc(v if v is not None else ""), ' style="white-space:nowrap"'
            else:
                text = "" if v is None else ("%.2f" % v if g["money"] and i == len(r) - 1 else fmt(v))
                attr = ' align="right"'
            out.append("<td%s>%s</td>" % (attr, "<b>%s</b>" % text if bold else text))
        return "<tr>%s</tr>" % "".join(out)

    body_rows = [line(r) for r in g["rows"]]
    total_html = line(g["total"], True)
    b = ['<p><a href="/reports?u=%d">&lt;&lt; Back to Reports</a></p>' % u, "<h4>Timesheet grid: hours by person, code and day</h4>",
         "<p><small>Pick a ready-made layout, then tailor it: tick the columns that group the hours, choose whether time runs across the page by day, "
         "week or as one total, narrow it by client, scope or person, and download exactly what you see as an Excel file. "
         "Changing the layout resets the ticked columns to that layout. %s</small></p>" % (
             "Value is hours x the client sales rate (internal hours have no rate)." if g["money"] else "You see your own hours and, for scopes you approve, everyone's on them.")]
    b += ["<p><small>%s</small></p>" % esc(n) for n in g["notes"]]
    b.append(form)
    if g["rows"]:
        b.append('<table border="1" cellpadding="3" cellspacing="0"><tr>%s</tr>%s%s</table>' % (head, "".join(body_rows), total_html))
    else:
        b.append("<p>No hours match these choices.</p>")
    return layout(con, u, "Reports - timesheet grid", "".join(b))


def grid_sheet(g):
    """The grid as rows for a spreadsheet: header, lines, total."""
    return [g["header"]] + g["rows"] + ([g["total"]] if g["rows"] else [])


def col_letter(i):
    out, i = "", i + 1
    while i:
        i, r = divmod(i - 1, 26)
        out = chr(65 + r) + out
    return out


def xlsx_bytes(sheet_name, rows, bold_rows=(1,)):
    """A one-sheet Excel file (.xlsx) from rows of text and numbers, built with the standard library only. Rows in bold_rows (counting from 1)
    are bold. Text is stored as text, so nothing typed into the system can be read as a formula."""
    import io
    import zipfile
    from xml.sax.saxutils import escape

    def clean(v):
        return escape("".join(ch for ch in str(v) if ch in "\t\n\r" or ord(ch) >= 32))

    lines, widths = [], []
    for ri, row in enumerate(rows, 1):
        cells = []
        for ci, v in enumerate(row):
            if v is None or v == "":
                continue
            ref, style = "%s%d" % (col_letter(ci), ri), ' s="1"' if ri in bold_rows else ""
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                cells.append('<c r="%s"%s><v>%s</v></c>' % (ref, style, repr(round(float(v), 6))))
                text = fmt(v)
            else:
                cells.append('<c r="%s"%s t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (ref, style, clean(v)))
                text = str(v)
            while len(widths) <= ci:
                widths.append(8)
            widths[ci] = max(widths[ci], min(len(text) + 2, 50))
        lines.append('<row r="%d">%s</row>' % (ri, "".join(cells)))
    name = "".join(ch for ch in sheet_name if ch not in "[]:*?/\\")[:31] or "Report"
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
             '<cols>%s</cols><sheetData>%s</sheetData></worksheet>' % (
                 "".join('<col min="%d" max="%d" width="%d" customWidth="1"/>' % (i + 1, i + 1, w) for i, w in enumerate(widths)), "".join(lines)))
    parts = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="%s" sheetId="1" r:id="rId1"/></sheets></workbook>' % clean(name),
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
        "xl/styles.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>',
        "xl/worksheets/sheet1.xml": sheet}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for part, text in parts.items():
            z.writestr(part, text.encode("utf-8"))
    return buf.getvalue()


def period_sheet(con, u, q, rv):
    per, d_from, d_to, _ = period_data(con, u, q, rv)
    money = rv["money"]
    rows = [["Week commencing", "Client", "Project", "Scope", "CTR", "Skill code", "Role", "Hours"] + (["Total"] if money else [])]
    for r in per:
        rows.append([r["week_start"], r["client"], r["project"], r["scope_code"], r["phase"], r["skill_code"], r["role"], r["hours"]]
                    + ([round(r["total"], 2)] if money else []))
    if per:
        rows.append(["Total for range"] + [None] * 6 + [sum(r["hours"] for r in per)] + ([round(sum(r["total"] for r in per), 2)] if money else []))
    return rows, ("Period %s to %s" % (d_from, d_to))


def view_ctr(con, u, q):
    """One CTR (a phase): a Summary tab plus one tab per job family, like the tabs in the CTR workbook.
    Sales rates and values are only shown to cost controllers; everyone else sees hours, limited to what they may see."""
    if permission_of(con, u) == "admin":  # Admin (IT) has no reports
        return no_access(con, u, "CTR")
    rv = report_view(con, u)
    money = rv["money"]
    vis, vargs = visible_to(rv, u)
    try:
        pid = int(q.get("phase", ["0"])[0])
    except ValueError:
        pid = 0
    ph = con.execute("select ph.*, (s.scope_code || '-' || coalesce(ph.ctr_code, upper(ph.name))) as phase, ph.name as phase_name, ph.status as phase_status, ph.description as phase_desc, s.scope_code, s.description as scope_desc, s.status as scope_status, c.framework_ref, "
                     "pr.project_code, pr.name as project, c.name as client, "
                     "(select group_concat(x.name, ', ') from (select au.name from v_scope_approver va join app_user au on au.user_id=va.user_id "
                     "where va.scope_id=s.scope_id order by au.name) x) as approver "
                     "from phase ph join scope s on s.scope_id=ph.scope_id join project pr on pr.project_id=s.project_id "
                     "join client c on c.client_id=s.client_id "
                     "where ph.phase_id=?", (pid,)).fetchone()
    if not ph:
        return layout(con, u, "CTR", "<p>CTR not found. Open one from the <a href=\"/reports?u=%d\">Reports</a> page.</p>" % u)
    ents = con.execute(
        "select t.entry_id, t.work_date, t.hours, t.status, t.approved_at, r.family, r.skill_code, r.name as title, rt.sales_rate, "
        "u.name as person, ap.name as approved_by, lo.name as location, t.overtime "
        "from time_entry t join scope s on s.scope_id=t.scope_id join rate rt on rt.rate_id=t.rate_id join role r on r.role_id=rt.role_id "
        "join location lo on lo.location_id=t.location_id "
        "join app_user u on u.user_id=t.user_id left join app_user ap on ap.user_id=t.approved_by "
        "where t.phase_id=? and t.status<>'Draft'" + vis + " order by r.family, r.skill_code, u.name, t.work_date", (pid,) + vargs).fetchall()
    if not rv["all"] and not ents:  # nothing here is theirs to see, so say nothing about the CTR itself
        return layout(con, u, "CTR", "<p>There are no hours you can see in this CTR. Open one from the <a href=\"/reports?u=%d\">Reports</a> page.</p>" % u)

    def tot(rows):
        h = sum(e["hours"] for e in rows if e["status"] == "Approved")
        t = sum(e["hours"] * e["sales_rate"] for e in rows if e["status"] == "Approved")
        p = sum(e["hours"] for e in rows if e["status"] != "Approved")
        return h, t, p

    def td(v, right=False):
        return '<td align="right">%s</td>' % v if right else "<td>%s</td>" % v

    families = sorted({e["family"] for e in ents})
    fam = q.get("family", ["Summary"])[0]
    if fam != "Summary" and fam != "Allowances" and fam not in families:
        fam = "Summary"
    base = "/ctr?u=%d&amp;phase=%d" % (u, pid)
    tabs = ['<b>[Summary]</b>' if fam == "Summary" else '<a href="%s&amp;family=Summary">Summary</a>' % base]
    for f in families:
        tabs.append("<b>[%s]</b>" % esc(f) if f == fam else '<a href="%s&amp;family=%s">%s</a>' % (base, quote(f), esc(f)))

    head = ('<p><a href="/reports?u=%d">&lt;&lt; Back to Reports</a></p>'
            "<table border=\"0\" cellpadding=\"2\"><tr><td>Client</td><td><b>%s</b></td></tr><tr><td>Project</td><td><b>%s %s</b></td></tr>"
            "<tr><td>Scope</td><td><b>%s %s</b> (%s)</td></tr><tr><td>CTR</td><td><b>%s</b> (%s)</td></tr>"
            "<tr><td>Approver</td><td>%s</td></tr>%s</table>" % (
                u, esc(ph["client"]), esc(ph["project_code"]), esc(ph["project"]), esc(ph["scope_code"]), esc(ph["scope_desc"]),
                esc(ph["scope_status"]), esc(ph["phase"]), esc(ph["phase_status"]), esc(ph["approver"]),
                ("<tr><td>Description</td><td>%s</td></tr>" % esc(ph["phase_desc"]) if ph["phase_desc"] else "")
                + ("<tr><td>WBS / CBS / ABS</td><td>%s</td></tr>" % esc(" / ".join(x or "-" for x in (ph["wbs"], ph["cbs"], ph["abs_code"])))
                   if (ph["wbs"] or ph["cbs"] or ph["abs_code"]) else "")))
    avis, aargs = ("", ()) if rv["all"] else (" and (a.user_id=? or exists (select 1 from v_scope_approver va where va.scope_id=s.scope_id and va.user_id=?))", (u, u))
    arows = con.execute(
        "select bu.name as person, at.name as allowance, a.status, count(*) as days, sum(a.amount) as value "
        "from v_allowance_value a join scope s on s.scope_id=a.scope_id join app_user bu on bu.user_id=a.user_id "
        "join allowance_type at on at.allowance_id=a.allowance_id "
        "where a.phase_id=? and a.status<>'Draft'" + avis + " group by a.user_id, a.allowance_id, a.status "
        "order by bu.name, a.allowance_id", (pid,) + aargs).fetchall()
    a_value = sum(r["value"] for r in arows if r["status"] == "Approved")
    tabs.append(("<b>[Allowances]</b>" if fam == "Allowances" else '<a href="%s&amp;family=Allowances">Allowances</a>' % base) if arows else "")
    tabs = [x for x in tabs if x]
    if fam == "Summary":  # the CTR sheet carries its own header, so only the way back is shown above it
        head = '<p><a href="/reports?u=%d">&lt;&lt; Back to Reports</a></p>' % u
    b = [head, "<p>CTR tabs: %s</p>" % " | ".join(tabs)]
    if not ents and not arows:
        b.append("<p>No hours have been submitted to this CTR yet.</p>")
        return layout(con, u, "CTR", "".join(b))
    allow_table = ""
    if arows:
        allow_table = ('<p><b>Allowances</b> <small>(offshore allowance: staff only; only Approved days count)%s</small></p>'
                       '<table border="1" cellpadding="3" cellspacing="0"><tr><th>Person</th><th>Allowance</th><th>Days</th>%s<th>Status</th></tr>%s%s</table>' % (
                           "" if rv["money"] else "",
                           "<th>Value</th>" if rv["money"] else "",
                           "".join("<tr>%s</tr>" % "".join((
                               "<td>%s</td>" % esc(r["person"]), "<td>%s</td>" % esc(r["allowance"]), '<td align="right">%d</td>' % r["days"],
                               ('<td align="right">%.2f</td>' % (r["value"] if r["status"] == "Approved" else 0)) if rv["money"] else "",
                               "<td>%s</td>" % esc(r["status"]))) for r in arows),
                           sub_row(["Total", "", str(sum(r["days"] for r in arows if r["status"] == "Approved"))] + (["%.2f" % a_value] if rv["money"] else []) + [""], right_from=4)))

    tcol = "<th>Total</th>" if money else ""
    if fam == "Allowances":
        b.append("<h4>Allowances - %s %s, %s</h4>" % (esc(ph["scope_code"]), esc(ph["scope_desc"]), esc(ph["phase"])) + allow_table)
        return layout(con, u, "CTR - %s %s - %s" % (ph["scope_code"], ph["scope_desc"], ph["phase"]), "".join(b))
    if fam == "Summary":
        def para(v):
            return esc(v).replace("\n", "<br>") if v else "&ndash;"

        def label(x):
            return "<td><b>%s</b></td>" % x
        ctr_no = ph["phase"]
        b.append("<h4>CTR %s</h4>" % esc(ctr_no))
        b.append('<table border="1" cellpadding="3" cellspacing="0">'
                 "<tr>%s<td colspan=\"3\">%s</td></tr><tr>%s<td colspan=\"3\">%s</td></tr><tr>%s<td colspan=\"3\">%s</td></tr>"
                 "<tr>%s<td colspan=\"3\">%s</td></tr><tr>%s<td colspan=\"3\">%s</td></tr>"
                 "<tr>%s<td>%s</td>%s<td>%s</td></tr>"
                 "<tr>%s<td>%s</td>%s<td>%s</td>%s<td>%s</td></tr></table>" % (
                     label("Project Name"), esc(ph["project"]), label("Project Title"), esc("%s - %s" % (ph["scope_desc"], ph["phase_name"])),
                     label("Client"), esc(ph["client"]), label("Date"), datetime.date.today().strftime("%d/%m/%Y"),
                     label("Framework Agreement Ref"), esc(ph["framework_ref"] or "-"),
                     label("CTR Title"), esc(ph["phase_desc"] or ph["phase_name"]), label("CTR No"), esc(ctr_no),
                     label("Prepared by"), esc(ph["prepared_by"] or "-"), label("Checked by"), esc(ph["checked_by"] or "-"),
                     label("Approved by"), esc(ph["approver"] or "-")))
        for title, key in (("Objectives", "objectives"), ("Input Requirements", "inputs"), ("Scope of Work", "scope_of_work"),
                           ("This CTR assumes the following", "assumptions"), ("This CTR excludes the following", "exclusions")):
            b.append('<table border="1" cellpadding="3" cellspacing="0" style="width:100%%;margin-top:.4rem"><tr><th align="left">%s</th></tr><tr><td>%s</td></tr></table>' % (
                title, para(ph[key])))
        b.append('<table border="1" cellpadding="3" cellspacing="0" style="margin-top:.4rem"><tr>%s<td>%s</td>%s<td>%s</td></tr><tr>%s<td>%s</td>%s<td>%s</td></tr></table>' % (
            label("Commencement Date"), esc(ph["book_from"] or "ref plan"), label("Duration"), "ref plan",
            label("Completion Date"), esc(ph["book_to"] or "ref plan"), label("Expenses"), esc(ph["expenses"])))
        if ph["wbs"] or ph["cbs"] or ph["abs_code"]:
            b.append('<table border="1" cellpadding="3" cellspacing="0" style="margin-top:.4rem"><tr>%s<td>%s</td></tr></table>' % (
                label("WBS / CBS / ABS"), esc(" / ".join(x or "-" for x in (ph["wbs"], ph["cbs"], ph["abs_code"])))))
        # resources: hours booked and approved, at the client rate card
        res = {}
        for e in ents:
            k = (e["skill_code"], e["title"], e["sales_rate"] if money else None)
            r = res.setdefault(k, [0.0, 0.0, 0.0])
            if e["status"] == "Approved":
                r[0] += e["hours"]
                r[1] += e["hours"] * e["sales_rate"]
            else:
                r[2] += e["hours"]
        res_rows = "".join("<tr><td>%s</td>%s<td align=\"right\">%s</td>%s<td align=\"right\">%s</td></tr>" % (
            esc("%s %s" % (k[0], k[1])), ('<td align="right">%.2f</td>' % k[2]) if money else "", fmt(v[0]),
            ('<td align="right">%.2f</td>' % v[1]) if money else "", fmt(v[2])) for k, v in sorted(res.items(), key=lambda kv: kv[0][0]))
        h_all, t_all, p_all = tot(ents)
        res_total = ("<tr><td><b>Sub-Totals</b></td>%s<td align=\"right\"><b>%s</b></td>%s<td align=\"right\"><b>%s</b></td></tr>" % (
            "<td></td>" if money else "", fmt(h_all), ('<td align="right"><b>%.2f</b></td>' % t_all) if money else "", fmt(p_all)))
        b.append('<table border="1" cellpadding="3" cellspacing="0" style="margin-top:.4rem"><tr><th>Resource</th>%s<th>Hours</th>%s<th>Pending hrs</th></tr>%s%s</table>' % (
            "<th>Cost &pound; / Hour</th>" if money else "", "<th>Total</th>" if money else "", res_rows, res_total))
        # additions: the allowances, at the allowance rate table
        adds = con.execute(
            "select at.name as allowance, a.amount, count(*) as units "
            "from v_allowance_value a join scope s on s.scope_id=a.scope_id join allowance_type at on at.allowance_id=a.allowance_id "
            "where a.phase_id=? and a.status='Approved'" + avis +
            " group by a.allowance_id, a.amount order by a.allowance_id, a.amount", (pid,) + aargs).fetchall()
        add_total = sum(r["amount"] * r["units"] for r in adds)
        add_rows = "".join("<tr><td>%s</td>%s<td align=\"right\">%d</td>%s</tr>" % (
            esc(r["allowance"]),
            ('<td align="right">%.2f</td>' % r["amount"]) if money else "", r["units"],
            ('<td align="right">%.2f</td>' % (r["amount"] * r["units"])) if money else "") for r in adds)
        b.append('<table border="1" cellpadding="3" cellspacing="0" style="margin-top:.4rem"><tr><th>Addition</th>%s<th>Unit (days)</th>%s</tr>%s</table>' % (
            "<th>Cost &pound; / Unit</th>" if money else "", "<th>Total</th>" if money else "",
            add_rows or "<tr><td colspan=\"%d\">No approved allowances.</td></tr>" % (4 if money else 2)))
        if money:
            b.append("<p><b>Total for this CTR: %.2f</b> <small>(resources %.2f + additions %.2f; approved hours and allowances only)</small></p>" % (
                t_all + add_total, t_all, add_total))
        terms = " &nbsp; ".join("%s [%s]" % (x, "X" if ph["payment_terms"] == x else "&nbsp;") for x in ("Reimbursable", "Budget", "Lump Sum"))
        b.append('<table border="1" cellpadding="3" cellspacing="0" style="margin-top:.4rem"><tr>%s<td>%s</td></tr></table>' % (label("Payment Terms"), terms))
        b.append('<table border="1" cellpadding="3" cellspacing="0" style="width:100%%;margin-top:.4rem"><tr><th align="left">Basis of Proposal</th></tr><tr><td>%s</td></tr></table>' % para(ph["basis"]))
        bud = con.execute("select * from v_ctr_budget where phase_id=?", (pid,)).fetchone() if money else None
        if bud and bud["budget_hours"] is not None:
            b.append("<p><b>Budget %s hrs</b> (P6, as of %s) &nbsp; actual %s hrs &nbsp; remaining %s hrs &nbsp; used %.0f%%</p>" % (
                fmt(bud["budget_hours"]), esc(bud["as_of"]), fmt(bud["actual_hours"]), fmt(bud["budget_hours"] - bud["actual_hours"]),
                100 * bud["actual_hours"] / bud["budget_hours"] if bud["budget_hours"] else 0))
        elif bud:
            b.append("<p><small>No budget has been loaded for this CTR (Admin, Budgets).</small></p>")
        b.append("<p><small>Hours are those booked and approved against this CTR, priced at the client's rate card for each skill code; additions are approved allowances "
                 "at the allowance rate table. Pending hrs are submitted and waiting for approval. The job family tabs above list the people and each timesheet entry.</small></p>")
    else:
        fe = [e for e in ents if e["family"] == fam]
        b.append("<h4>%s</h4>" % esc(fam))
        # resources: one line per job title (and per rate, for cost controllers)
        rows = []
        rate_of = (lambda e: e["sales_rate"]) if money else (lambda e: None)
        for key in dict.fromkeys((e["skill_code"], e["title"], rate_of(e)) for e in fe):
            part = [e for e in fe if (e["skill_code"], e["title"], rate_of(e)) == key]
            h, t, p = tot(part)
            rows.append("<tr>%s%s%s%s%s%s</tr>" % (td(esc(key[0])), td(esc(key[1])), td("%.2f" % key[2], True) if money else "",
                                                   td(fmt(h), True), td("%.2f" % t, True) if money else "", td(fmt(p), True)))
        h, t, p = tot(fe)
        if money:
            rows.append(sub_row(["", "Sub-total", "", fmt(h), "%.2f" % t, fmt(p)], right_from=3))
        else:
            rows.append(sub_row(["", "Sub-total", fmt(h), fmt(p)], right_from=2))
        b.append('<p><b>Resources</b></p><table border="1" cellpadding="3" cellspacing="0"><tr><th>Skill code</th><th>Job title</th>%s'
                 '<th>Actual booked hrs</th>%s<th>Pending hrs</th></tr>%s</table>' % ("<th>Sales rate</th>" if money else "", tcol, "".join(rows)))
        # people
        rows = []
        for key in dict.fromkeys((e["person"], e["skill_code"], e["title"], rate_of(e)) for e in fe):
            part = [e for e in fe if (e["person"], e["skill_code"], e["title"], rate_of(e)) == key]
            h, t, p = tot(part)
            rows.append("<tr>%s%s%s%s%s</tr>" % (td(esc(key[0])), td(esc("%s %s" % (key[1], key[2]))), td(fmt(h), True),
                                                 td("%.2f" % t, True) if money else "", td(fmt(p), True)))
        rows.append(sub_row(["Sub-total", "", fmt(tot(fe)[0])] + (["%.2f" % tot(fe)[1]] if money else []) + [fmt(tot(fe)[2])], right_from=2))
        b.append('<p><b>People</b></p><table border="1" cellpadding="3" cellspacing="0"><tr><th>Person</th><th>Booked as</th><th>Actual booked hrs</th>'
                 '%s<th>Pending hrs</th></tr>%s</table>' % (tcol, "".join(rows)))
        # entries
        rows = []
        for e in sorted(fe, key=lambda x: (x["work_date"], x["person"])):
            rows.append("<tr>%s%s%s%s%s%s%s%s%s</tr>" % (
                td(esc(e["work_date"])), td(esc(e["person"])), td(esc("%s %s" % (e["skill_code"], e["title"]))),
                td(esc(e["location"])), td("<b>Overtime</b>" if e["overtime"] else "Normal"), td(fmt(e["hours"]), True),
                td("%.2f" % (e["hours"] * e["sales_rate"]), True) if money else "", td(esc(e["status"])), td(esc(e["approved_by"] or ""))))
        b.append('<p><b>Timesheet entries</b> <small>(%sonly Approved entries count towards the CTR)</small></p>'
                 '<table border="1" cellpadding="3" cellspacing="0"><tr><th>Date</th><th>Person</th><th>Booked as</th><th>Location</th><th>Type</th><th>Hours</th>%s'
                 '<th>Status</th><th>Approved by</th></tr>%s</table>' % ("value = hours x sales rate; " if money else "", "<th>Value</th>" if money else "", "".join(rows)))
    return layout(con, u, "CTR - %s %s - %s" % (ph["scope_code"], ph["scope_desc"], ph["phase"]), "".join(b))
