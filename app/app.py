"""Front end for the v1 mock database. Plain HTML with one inline stylesheet, standard library only.

Run from the repo root:   python app/app.py        then open  http://localhost:8000
Uses build/timewriting.db (create it first with: python scripts/build.py).
There is no login: pick who you are from the "Signed in as" box (mock).
"""
import datetime
import html
import os
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("TW_DB", os.path.join(ROOT, "build", "timewriting.db"))
# Databases built before this version lack columns and rules the app relies on. Matches PRAGMA user_version in db/schema.sql.
REQUIRED_SCHEMA = 1
# Static files the app may serve. Only these exact URLs, never a path built from the request.
ASSETS = {"/static/TASAOrbiterVF.woff2": (os.path.join(ROOT, "app", "static", "TASAOrbiterVF.woff2"), "font/woff2")}


# ---------- helpers ----------
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def esc(v):
    return html.escape("" if v is None else str(v))


def stamp():
    # Stored with microseconds on purpose: the database rejects an edit whose last_edited_at is the same as the previous
    # edit by the same person (e.g. Save draft then Submit week in one click), so whole seconds are not unique enough.
    # Screens show it with fmt_time().
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")


def fmt_time(v):
    """A stored timestamp as yyyy-mm-dd HH:MM:SS (no fractions of a second)."""
    return "" if v is None else str(v)[:19]


def fmt(h):
    return "" if h is None else "%g" % h


def monday(d):
    return d - datetime.timedelta(days=d.weekday())


def ensure_current_period(con):
    """Open this week's period automatically: the first request of a new week creates it. A week that already
    exists, open or closed, is left alone, so a week an admin has closed stays closed."""
    con.execute("insert or ignore into period (week_start) values (?)", (monday(datetime.date.today()).isoformat(),))
    con.commit()


def table(headers, rows, raw_cols=()):
    out = ['<table border="1" cellpadding="3" cellspacing="0"><tr>']
    out += ["<th>%s</th>" % esc(h) for h in headers]
    out.append("</tr>")
    for r in rows:
        out.append("<tr>")
        for i, v in enumerate(r):
            out.append("<td>%s</td>" % (v if i in raw_cols else esc(v)))
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)


def me_row(con, u):
    return con.execute(
        "select u.*, r.name as role_name, r.family, r.skill_code from app_user u join role r on r.role_id=u.role_id "
        "where u.user_id=?", (u,)).fetchone()


def permission_of(con, u):
    me = me_row(con, u)
    return me["permission"] if me else None


def is_approver(con, u):
    """Approver is not a permission: it means being named as approver on at least one scope."""
    return con.execute("select 1 from scope where approver_user_id=? limit 1", (u,)).fetchone() is not None


def approval_queue(con, u):
    """What this person approves, as SQL on time entries (t) and scopes (s) with its arguments, or None if they approve nothing.
    A named approver approves hours booked to their scopes, but never their own. Hours booked by a scope's approver have nobody else
    who may approve them, so a cost controller approves those."""
    if permission_of(con, u) == "controller":
        return " and t.user_id = s.approver_user_id", ()
    if is_approver(con, u):
        return " and s.approver_user_id=? and t.user_id<>?", (u, u)
    return None


def may_action(con, u, eid):
    """Can this person approve or reject this entry? The scope's approver can (the database refuses their own hours);
    a cost controller can, but only for hours booked by the scope's approver."""
    row = con.execute("select s.approver_user_id, t.user_id from time_entry t join scope s on s.scope_id=t.scope_id where t.entry_id=?", (eid,)).fetchone()
    if not row:
        return False
    if permission_of(con, u) == "controller":
        return row[1] == row[0]
    return row[0] == u


def pending_for(con, u):
    queue = approval_queue(con, u)
    if queue is None:
        return 0
    return con.execute("select count(*) from time_entry t join scope s on s.scope_id=t.scope_id "
                       "where t.status in ('Submitted','Edited')" + queue[0], queue[1]).fetchone()[0]


STYLE = """
@font-face{font-family:"TASA Orbiter";font-style:normal;font-weight:400 600;font-display:swap;
  src:local("TASA Orbiter"),local("TASAOrbiter"),url(/static/TASAOrbiterVF.woff2) format("woff2")}
:root{--black:#0f0f12;--ink:#1e1e24;--grey-dark:#4b4b50;--grey-mid:#8f8f92;--grey-light:#bcbcbd;--line:#e5e5e6;
  --bg:#f5f5f6;--white:#fff;--orange:#fe6700;--tint:rgba(254,103,0,.07)}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{font-family:"TASA Orbiter","TASAOrbiter","Segoe UI",system-ui,-apple-system,Arial,sans-serif;font-size:15px;line-height:1.4;letter-spacing:-.02em;
  color:var(--black);background:var(--bg);-webkit-font-smoothing:antialiased}
a{color:var(--black);text-decoration:none}
a:hover{color:var(--orange)}
.top{background:var(--black);color:var(--white);display:flex;align-items:center;justify-content:space-between;gap:1rem;
  padding:.7rem 1.5rem;border-bottom:3px solid var(--orange)}
.brand{font-size:1.1rem;font-weight:500;letter-spacing:-.02em}
.brand .tag{font-size:.7rem;font-weight:400;color:var(--grey-light);border:1px solid rgba(255,255,255,.3);padding:.1rem .5rem;margin-left:.5rem}
.who{display:flex;align-items:center;gap:.6rem;font-size:.85rem;color:var(--grey-light);margin:0}
.top select{background:var(--ink);color:var(--white);border:1px solid rgba(255,255,255,.25)}
.top input[type=submit]{background:transparent;color:var(--white);border:1px solid rgba(255,255,255,.3)}
.top input[type=submit]:hover{background:var(--orange);border-color:var(--orange);color:var(--white)}
.shell{display:flex;align-items:flex-start;gap:1.5rem;padding:1.5rem}
.side{flex:0 0 200px;position:sticky;top:1rem}
.side a{display:block;position:relative;padding:.5rem .8rem .5rem 1rem;color:var(--grey-dark)}
.side a:hover{background:var(--white);color:var(--black)}
.side a.cur{background:var(--white);color:var(--black);font-weight:500}
.side a.cur::before{content:"";position:absolute;left:0;top:.45rem;bottom:.45rem;width:3px;background:var(--orange)}
.me{margin-top:1.2rem;padding:.8rem 1rem;background:var(--white);border:1px solid var(--line);font-size:.8rem;color:var(--grey-dark)}
.me b{display:block;color:var(--black);font-size:.9rem;font-weight:500}
main{flex:1;min-width:0}
h1{font-size:1.6rem;font-weight:450;letter-spacing:-.02em;margin:0 0 1rem}
.card{background:var(--white);border:1px solid var(--line);padding:1.2rem 1.4rem;overflow-x:auto}
h4{font-size:1.05rem;font-weight:500;letter-spacing:-.01em;margin:1.8rem 0 .4rem}
h4:first-child,.card>h4:first-child{margin-top:0}
p{margin:.55rem 0}
small{color:var(--grey-dark);font-size:.8rem}
.card a{text-decoration:underline;text-decoration-color:var(--grey-light);text-underline-offset:3px}
.card a:hover{text-decoration-color:var(--orange)}
table[border]{border:0;border-collapse:collapse;font-size:.85rem;margin:.4rem 0}
.card .tabs{display:flex;flex-wrap:wrap;gap:.4rem;margin:0 0 .6rem}
.card .tabs a{text-decoration:none;padding:.3rem .85rem;border:1px solid rgba(30,30,36,.3);font-size:.85rem}
.card .tabs a:hover{border-color:var(--black);color:var(--orange)}
.card .tabs a.cur{background:var(--black);color:var(--white);border-color:var(--black)}
table[border] th{background:var(--black);color:var(--white);font-weight:450;text-align:left;padding:.45rem .65rem;border:0}
table[border] td{padding:.35rem .65rem;border:0;border-bottom:1px solid var(--line)}
table[border] tr:hover td{background:var(--tint)}
table[border] td[align=right],table[border] td[align=center]{font-variant-numeric:tabular-nums}
table[border="0"] th,table[border="0"] td,table[border="0"] tr:hover td{background:none;color:inherit;border:0;padding:.15rem .6rem .15rem 0}
input[type=text],input[type=date],select{font:inherit;font-size:.9rem;height:2rem;padding:0 .6rem;color:var(--black);background:var(--white);
  border:1px solid var(--grey-light);}
input[type=text]:focus,input[type=date]:focus,select:focus{outline:2px solid var(--orange);outline-offset:1px;border-color:var(--orange)}
table[border] input[type=text]{height:1.8rem;text-align:center;padding:0 .3rem}
input[type=submit],input[type=button]{font:inherit;font-size:.85rem;font-weight:450;height:2.2rem;padding:0 1.1rem;cursor:pointer;
  transition:color .3s,border-color .3s,background-color .3s}
input[type=submit]{background:var(--black);color:var(--white);border:1px solid var(--black)}
input[type=submit]:hover{background:var(--orange);border-color:var(--orange)}
input[type=button]{background:transparent;color:var(--black);border:1px solid rgba(30,30,36,.3)}
input[type=button]:hover{border-color:var(--black);color:var(--orange)}
input[type=checkbox]{accent-color:var(--orange);width:1rem;height:1rem}
.msg{margin:0 0 1rem;padding:.7rem 1rem;background:var(--bg);border-left:4px solid var(--black)}
.msg.bad{border-left-color:var(--orange);background:var(--tint)}
.msg p{margin:0 0 .3rem}
.msg ul{margin:0;padding-left:1.2rem}
.tg{color:var(--orange)}
@media (max-width:820px){.shell{flex-direction:column}.side{position:static;flex:none;width:100%}.top{flex-wrap:wrap}}
"""

NAV_KEY = {"My": "timesheet", "Approvals": "approvals", "Reports": "reports", "CTR": "reports",
           "Admin": "admin", "Edit": "admin", "Audit": "audit"}


def layout(con, u, title, body):
    users = con.execute("select a.user_id, a.name, r.name as role_name, a.permission from app_user a "
                        "join role r on r.role_id=a.role_id order by a.name").fetchall()
    opts = "".join('<option value="%d"%s>%s - %s%s</option>' % (
        x["user_id"], " selected" if x["user_id"] == u else "", esc(x["name"]), esc(x["role_name"]),
        " (%s)" % x["permission"] if x["permission"] != "user" else "") for x in users)
    me = me_row(con, u)
    perm = me["permission"] if me else None
    items = [("timesheet", "My Timesheet", "/timesheet?u=%d" % u)]
    if approval_queue(con, u) is not None:
        items.append(("approvals", "Approvals (%d)" % pending_for(con, u), "/approvals?u=%d" % u))
    if perm != "admin":  # Admin (IT) manages users and job roles only: no reports
        items.append(("reports", "Reports", "/reports?u=%d" % u))
    if perm in ("controller", "admin"):
        items += [("admin", "Admin", "/admin?u=%d" % u), ("audit", "Audit trail", "/audit?u=%d" % u)]
    cur = NAV_KEY.get(title.split(" ")[0])
    menu = "".join('<a href="%s"%s>%s</a>' % (href, ' class="cur"' if key == cur else "", esc(label)) for key, label, href in items)
    who = "<b>%s</b>%s (%s)<br>%s, %s" % (esc(me["name"]), esc(me["role_name"]), esc(me["skill_code"]), esc(me["family"]), esc(me["permission"])) if me else ""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>%s - NMC Timewriting</title><style>%s</style></head><body>"
        '<header class="top"><div class="brand">NMC Timewriting<span class="tag">mock</span></div>'
        '<form class="who" method="get" action="/timesheet">Signed in as '
        '<select name="u">%s</select><input type="submit" value="Switch user"></form></header>'
        '<div class="shell"><nav class="side">%s<div class="me">%s</div></nav>'
        '<main><h1>%s</h1><div class="card">%s</div></main></div></body></html>'
    ) % (esc(title), STYLE, opts, menu, who, esc(title), body)


def no_access(con, u, title):
    return layout(con, u, title, "<p>This page is not available to your role.</p>")


def msgs_html(msgs, ok=False):
    if not msgs:
        return ""
    return '<div class="msg%s"><p><b>%s</b></p><ul>%s</ul></div>' % (
        "" if ok else " bad", "Done:" if ok else "Problems:", "".join("<li>%s</li>" % esc(m) for m in msgs))


# ---------- My Timesheet ----------
def parse_adds(vals):
    out = []
    for v in vals:
        try:
            s, r = v.split(":")
            out.append((int(s), int(r)))
        except ValueError:
            pass
    return out


TIMESHEET_JS = """<script>
// Keeps the row, day and week totals in step with what is typed, before anything is saved.
function fmtH(x){return String(parseFloat(x.toFixed(2)));}
function recalc(){
  var rows=document.querySelectorAll('tr.row'), dt={}, gt=0, i, j, k;
  for(i=0;i<rows.length;i++){
    var cells=rows[i].querySelectorAll('td.c'), rt=0;
    for(j=0;j<cells.length;j++){
      var inp=cells[j].querySelector('input[type=text]');
      var v=parseFloat(inp?inp.value:cells[j].getAttribute('data-h'));
      if(isNaN(v)){v=0;}
      rt+=v; var d=cells[j].getAttribute('data-day'); dt[d]=(dt[d]||0)+v;
    }
    rows[i].querySelector('td.rt').textContent=fmtH(rt); gt+=rt;
  }
  var ds=document.querySelectorAll('td.dt');
  for(k=0;k<ds.length;k++){ds[k].firstChild.textContent=fmtH(dt[ds[k].getAttribute('data-day')]||0);}
  var g=document.querySelector('td.gt'); if(g){g.firstChild.textContent=fmtH(gt);}
}
document.addEventListener('input',function(e){if(e.target.name&&e.target.name.indexOf('h_')===0){recalc();}});
</script>"""


def view_timesheet(con, u, w, msgs=None, adds=None, ok=False):
    adds = adds or []
    me = me_row(con, u)
    if not me:
        return layout(con, u, "My Timesheet", "<p>Unknown user.</p>")
    days = [w + datetime.timedelta(days=i) for i in range(7)]
    d0, d6 = days[0].isoformat(), days[6].isoformat()
    period = con.execute("select * from period where week_start=?", (d0,)).fetchone()
    open_week = bool(period and period["status"] == "Open")
    entries = con.execute(
        "select t.*, s.scope_code, s.description, c.name as client, pr.name as project, ph.name as phase, ro.skill_code "
        "from time_entry t join phase ph on ph.phase_id=t.phase_id join scope s on s.scope_id=ph.scope_id "
        "join project pr on pr.project_id=s.project_id join client c on c.client_id=s.client_id "
        "join role ro on ro.role_id=t.worked_role_id where t.user_id=? and t.work_date between ? and ? "
        "order by c.name, pr.name, s.scope_code, ph.name, ro.skill_code", (u, d0, d6)).fetchall()

    rows = {}
    for e in entries:
        k = (e["phase_id"], e["worked_role_id"])
        rows.setdefault(k, {"client": e["client"], "project": e["project"], "scope": "%s %s" % (e["scope_code"], e["description"]),
                            "phase": e["phase"], "skill": e["skill_code"], "cells": {}})["cells"][e["work_date"]] = e
    for (pid, rid) in adds:
        if (pid, rid) in rows:
            continue
        s = con.execute("select s.*, c.name as client, pr.name as project, ph.name as phase from phase ph "
                        "join scope s on s.scope_id=ph.scope_id join project pr on pr.project_id=s.project_id "
                        "join client c on c.client_id=s.client_id where ph.phase_id=?", (pid,)).fetchone()
        r = con.execute("select * from role where role_id=?", (rid,)).fetchone()
        if s and r:
            rows[(pid, rid)] = {"client": s["client"], "project": s["project"], "scope": "%s %s" % (s["scope_code"], s["description"]),
                                "phase": s["phase"], "skill": r["skill_code"], "cells": {}}

    b = []
    b.append(msgs_html(msgs, ok))
    prev_w, next_w = (w - datetime.timedelta(days=7)).isoformat(), (w + datetime.timedelta(days=7)).isoformat()
    b.append('<p><a href="/timesheet?u=%d&amp;w=%s">&lt;&lt; Previous week</a> &nbsp; '
             "<b>Week commencing %s</b> &nbsp; "
             '<a href="/timesheet?u=%d&amp;w=%s">Next week &gt;&gt;</a></p>' % (u, prev_w, w.strftime("%d/%m/%Y"), u, next_w))
    if not period:
        b.append("<p>This week has not been opened for timewriting. Ask an admin to open it.</p>")
    elif not open_week:
        b.append("<p><b>This week is closed.</b> New hours cannot be added. Approved hours can only be changed "
                 "with a reason (controlled edit).</p>")

    adds_hidden = "".join('<input type="hidden" name="add" value="%d:%d">' % k for k in rows if not rows[k]["cells"])
    b.append('<form method="post" action="/timesheet/save">'
             '<input type="hidden" name="u" value="%d"><input type="hidden" name="w" value="%s">%s' % (u, d0, adds_hidden))
    head = ["Client", "Project", "Scope", "Phase", "Skill code"] + [d.strftime("%a %d/%m") for d in days] + ["Total"]
    trs = []
    day_tot = {d.isoformat(): 0.0 for d in days}
    for (pid, rid), r in rows.items():
        tds = ['<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td style="white-space:nowrap">%s</td>' % (
            esc(r["client"]), esc(r["project"]), esc(r["scope"]), esc(r["phase"]), esc(r["skill"]))]
        rt = 0.0
        for d in days:
            iso = d.isoformat()
            e = r["cells"].get(iso)
            name = "h_%d_%d_%s" % (pid, rid, iso)
            fixed = ""  # hours the person cannot type over (shown as text), so the live totals can still count them
            if e is None:
                cell = '<input type="text" size="4" name="%s" value="">' % name if open_week else "&nbsp;"
            else:
                rt += e["hours"]
                day_tot[iso] += e["hours"]
                if e["status"] == "Submitted" or (e["status"] == "Draft" and not open_week):
                    cell = fmt(e["hours"])
                    fixed = ' data-h="%s"' % fmt(e["hours"])
                else:
                    cell = '<input type="text" size="4" name="%s" value="%s">' % (name, fmt(e["hours"]))
            tds.append('<td align="center" class="c" data-day="%s"%s>%s</td>' % (iso, fixed, cell))
        tds.append('<td align="right" class="rt">%s</td>' % fmt(rt))
        trs.append('<tr class="row">%s</tr>' % "".join(tds))
    if not trs:
        trs.append('<tr><td colspan="%d">No rows yet. Add a row below.</td></tr>' % len(head))
    foot = '<tr><td colspan="5" align="right"><b>Total</b></td>%s<td align="right" class="gt"><b>%s</b></td></tr>' % (
        "".join('<td align="center" class="dt" data-day="%s"><b>%s</b></td>' % (d.isoformat(), fmt(day_tot[d.isoformat()])) for d in days),
        fmt(sum(day_tot.values())))
    b.append('<table border="1" cellpadding="3" cellspacing="0"><tr>%s</tr>%s%s</table>' % (
        "".join("<th>%s</th>" % esc(h) for h in head), "".join(trs), foot))
    b.append(TIMESHEET_JS)
    by_status = {"Approved": 0.0, "Edited": 0.0, "Submitted": 0.0, "Draft": 0.0}
    for e in entries:
        by_status[e["status"]] += e["hours"]
    b.append("<p><b>Hours this week (all statuses): %s</b> &nbsp; Approved %s, Edited %s, Submitted %s, Draft %s</p>" % (
        fmt(sum(by_status.values())), fmt(by_status["Approved"]), fmt(by_status["Edited"]), fmt(by_status["Submitted"]), fmt(by_status["Draft"])))
    b.append("<p><small>To clear a draft entry, empty its box and click Save draft.</small></p>")
    b.append('<p>Reason for changing approved hours (needed if you change hours that have already been approved): '
             '<input type="text" name="reason" size="60"></p>')
    b.append('<p><input type="submit" name="action" value="Save draft"> '
             '<input type="submit" name="action" value="Submit week"></p></form>')

    # add a row
    scopes = con.execute(
        "select ph.phase_id, ph.name as phase, s.scope_code, s.description, pr.name as project, c.name as client, c.is_internal "
        "from phase ph join scope s on s.scope_id=ph.scope_id join project pr on pr.project_id=s.project_id "
        "join client c on c.client_id=s.client_id left join paaf p on p.client_id=s.client_id and p.user_id=? "
        "and p.status='Approved' where s.status='Open' and ph.status='Open' and pr.status='Open' "
        "and (c.is_internal=1 or p.paaf_id is not null) "  # client work needs an approved PAAF, internal codes are open to everyone
        "order by c.is_internal, c.name, pr.name, s.scope_code, ph.name", (u,)).fetchall()
    roles = con.execute("select role_id, skill_code, name from role where active=1 order by skill_code").fetchall()
    if scopes:
        def label(s):
            if s["is_internal"]:  # e.g. "Internal - Business Services"
                return "%s - %s%s" % (esc(s["client"]), esc(s["description"]), "" if s["phase"] == "General" else " &gt; %s" % esc(s["phase"]))
            return "%s &gt; %s &gt; %s %s &gt; %s" % (esc(s["client"]), esc(s["project"]), esc(s["scope_code"]), esc(s["description"]), esc(s["phase"]))
        so = "".join('<option value="%d">%s</option>' % (s["phase_id"], label(s)) for s in scopes)
        ro = "".join('<option value="%d"%s>%s %s</option>' % (r["role_id"], " selected" if r["role_id"] == me["role_id"] else "", esc(r["skill_code"]), esc(r["name"])) for r in roles)
        keep = "".join('<input type="hidden" name="add" value="%d:%d">' % k for k in rows if not rows[k]["cells"])
        b.append('<form method="get" action="/timesheet"><input type="hidden" name="u" value="%d">'
                 '<input type="hidden" name="w" value="%s">%s<b>Add row:</b> Phase <select name="add_phase">%s</select> '
                 'Skill code <select name="add_role">%s</select> <input type="submit" value="Add row"></form>'
                 "<p><small>Your own skill code is pre-selected. Choose a different one if you are covering another role. "
                 "Hours NMC pays for itself (not billed to a client) go to an Internal code, for example Internal - Business Services.</small></p>" % (u, d0, keep, so, ro))
    else:
        b.append("<p>You have no approved PAAF for any client with open scopes, so there is nothing to book to.</p>")
    return layout(con, u, "My Timesheet", "".join(b))


def save_timesheet(con, form):
    u, w = int(form["u"][0]), datetime.date.fromisoformat(form["w"][0])
    action = form.get("action", ["Save draft"])[0]
    reason = form.get("reason", [""])[0].strip()
    adds = parse_adds(form.get("add", []))
    msgs, saved, cleared = [], 0, 0
    d0, d6 = w.isoformat(), (w + datetime.timedelta(days=6)).isoformat()
    for key, vals in form.items():
        if not key.startswith("h_"):
            continue
        _, pid, rid, date = key.split("_", 3)
        pid, rid = int(pid), int(rid)
        raw = vals[0].strip()
        info = con.execute("select s.scope_id, s.scope_code, ph.name as phase from phase ph join scope s on s.scope_id=ph.scope_id "
                           "where ph.phase_id=?", (pid,)).fetchone()
        if not info:
            continue
        sid = info["scope_id"]
        label = "%s %s, %s" % (info["scope_code"], info["phase"], date)
        existing = con.execute("select * from time_entry where user_id=? and phase_id=? and worked_role_id=? and work_date=?",
                               (u, pid, rid, date)).fetchone()
        if raw == "":
            if existing and existing["status"] == "Draft":
                try:
                    con.execute("update time_entry set last_edited_by=?, last_edited_at=? where entry_id=?",
                                (u, stamp(), existing["entry_id"]))
                    con.execute("delete from time_entry where entry_id=?", (existing["entry_id"],))
                    cleared += 1
                except sqlite3.DatabaseError as e:
                    msgs.append("%s: %s" % (label, e))
            elif existing and existing["status"] != "Submitted":
                msgs.append("%s: approved hours cannot be cleared. Change the hours (with a reason) or ask an admin." % label)
            continue
        try:
            hours = float(raw)
        except ValueError:
            msgs.append("%s: '%s' is not a number." % (label, raw))
            continue
        try:
            if existing:
                if existing["status"] == "Submitted" or abs(existing["hours"] - hours) < 1e-9:
                    continue
                if existing["status"] == "Draft":
                    con.execute("update time_entry set hours=?, last_edited_by=?, last_edited_at=? where entry_id=?",
                                (hours, u, stamp(), existing["entry_id"]))
                else:
                    if not reason:
                        msgs.append("%s: a reason is needed to change approved hours." % label)
                        continue
                    con.execute("update time_entry set hours=?, status='Edited', edit_count=edit_count+1, "
                                "last_change_reason=?, last_edited_by=?, last_edited_at=? where entry_id=?",
                                (hours, reason, u, stamp(), existing["entry_id"]))
            else:
                client_id, internal = con.execute("select s.client_id, c.is_internal from scope s join client c on c.client_id=s.client_id "
                                                  "where s.scope_id=?", (sid,)).fetchone()
                if internal:  # NMC pays for these hours: no PAAF, no sales rate
                    paaf_id = rate_id = None
                else:
                    rate = con.execute("select rate_id from rate where client_id=? and role_id=? and effective_from<=? "
                                       "order by effective_from desc limit 1", (client_id, rid, date)).fetchone()
                    paaf = con.execute("select paaf_id from paaf where user_id=? and client_id=?", (u, client_id)).fetchone()
                    if not rate:
                        msgs.append("%s: no rate for that skill code on this client." % label)
                        continue
                    if not paaf:
                        msgs.append("%s: you have no PAAF for this client." % label)
                        continue
                    paaf_id, rate_id = paaf[0], rate[0]
                con.execute("insert into time_entry (user_id, scope_id, phase_id, paaf_id, worked_role_id, rate_id, work_date, hours, "
                            "status, created_by) values (?,?,?,?,?,?,?,?, 'Draft', ?)",
                            (u, sid, pid, paaf_id, rid, rate_id, date, hours, u))
            saved += 1
        except sqlite3.DatabaseError as e:
            msgs.append("%s: %s" % (label, e))
    ok_msgs = []
    if saved:
        ok_msgs.append("%d entr%s saved." % (saved, "y" if saved == 1 else "ies"))
    if cleared:
        ok_msgs.append("%d draft entr%s cleared." % (cleared, "y" if cleared == 1 else "ies"))
    if action == "Submit week":
        try:
            n = con.execute("update time_entry set status='Submitted', last_edited_by=?, last_edited_at=? "
                            "where user_id=? and status='Draft' and work_date between ? and ?", (u, stamp(), u, d0, d6)).rowcount
            ok_msgs.append("%d entr%s submitted for approval." % (n, "y" if n == 1 else "ies"))
        except sqlite3.DatabaseError as e:
            msgs.append("Submit failed: %s" % e)
    con.commit()
    body_msgs = ok_msgs if not msgs else msgs + ok_msgs
    return view_timesheet(con, u, w, body_msgs, adds, ok=not msgs)


# ---------- Approvals ----------
def view_approvals(con, u, msgs=None, ok=False):
    queue = approval_queue(con, u)
    if queue is None:
        return layout(con, u, "Approvals", msgs_html(msgs, ok) + "<p>Hours are approved by the approver named on each booking code. "
                      "You are not named as an approver on any code, so there is nothing for you to approve.</p>")
    where, wargs = queue
    rows = con.execute(
        "select t.entry_id, bu.name as booked_by, c.name as client, pr.name as project, s.scope_code, ph.name as phase, ro.skill_code, t.work_date, t.hours, "
        "t.status, t.last_change_reason, (select old_hours from time_entry_audit a where a.entry_id=t.entry_id "
        "and a.new_status='Edited' order by a.audit_id desc limit 1) as was "
        "from time_entry t join scope s on s.scope_id=t.scope_id join phase ph on ph.phase_id=t.phase_id "
        "join project pr on pr.project_id=s.project_id join client c on c.client_id=s.client_id "
        "join app_user bu on bu.user_id=t.user_id join role ro on ro.role_id=t.worked_role_id "
        "where t.status in ('Submitted','Edited')" + where + " order by bu.name, t.work_date", wargs).fetchall()
    body = msgs_html(msgs, ok)
    if permission_of(con, u) == "controller":
        body += ("<p><small>These are hours booked by approvers to the scopes they approve. Nobody else may approve them "
                 "(not even the approver, who never approves their own hours), so a cost controller does.</small></p>")
    if not rows:
        return layout(con, u, "Approvals", body + "<p>Nothing waiting for your approval.</p>")
    trs = []
    for r in rows:
        trs.append("<tr><td align=\"center\"><input type=\"checkbox\" name=\"entry_id\" value=\"%d\"></td><td>%s</td><td>%s</td><td>%s</td>"
                   "<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td align=\"right\">%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                       r["entry_id"], esc(r["booked_by"]), esc(r["client"]), esc(r["project"]), esc(r["scope_code"]), esc(r["phase"]), esc(r["skill_code"]), esc(r["work_date"]),
                       fmt(r["hours"]), esc(r["status"]), esc(fmt(r["was"]) if r["was"] is not None else ""), esc(r["last_change_reason"] or "")))
    head = ("<tr><th><input type=\"checkbox\" title=\"Select all\" onclick=\"var c=document.getElementsByName('entry_id');"
            "for(var i=0;i<c.length;i++){c[i].checked=this.checked;}\"></th><th>Booked by</th><th>Client</th><th>Project</th><th>Scope</th>"
            "<th>Phase</th><th>Skill code</th><th>Date</th><th>Hours</th><th>Status</th><th>Was</th><th>Edit reason</th></tr>")
    body += ('<form method="post" action="/approvals/act">%s<p><small>Tick the entries to action, or tick the box in the header to select all '
             'and then untick any you have an issue with. Entries left unticked stay in your queue.</small></p>'
             '<table border="1" cellpadding="3" cellspacing="0">%s%s</table>'
             '<p><input type="submit" name="action" value="Approve selected"></p>'
             '<p>Reason (needed to reject) %s <input type="submit" name="action" value="Reject selected"> '
             '<small>Rejected entries go back to the person as drafts. Edited entries can only be approved.</small></p></form>'
             % (hidden(u=u), head, "".join(trs), txt("reason", "", 40)))
    return layout(con, u, "Approvals", body)


def act_approval(con, form):
    u = int(form["u"][0])
    ids = [int(x) for x in form.get("entry_id", [])]
    action, reason = form["action"][0], form.get("reason", [""])[0].strip()
    approve = action.startswith("Approve")
    if approval_queue(con, u) is None:
        return view_approvals(con, u)
    if not ids:
        return view_approvals(con, u, ["Tick at least one entry first."])
    if not approve and not reason:
        return view_approvals(con, u, ["Give a reason to reject."])
    done, msgs = 0, []
    for eid in ids:
        if not may_action(con, u, eid):
            msgs.append("Entry %d: only the approver named on its booking code can approve or reject it "
                        "(a cost controller can, for hours the approver booked themselves)." % eid)
            continue
        try:
            if approve:
                con.execute("update time_entry set status='Approved', approved_by=?, approved_at=?, last_edited_by=?, "
                            "last_edited_at=? where entry_id=?", (u, stamp(), u, stamp(), eid))
            else:
                cur = con.execute("select status from time_entry where entry_id=?", (eid,)).fetchone()
                if cur and cur[0] != "Submitted":
                    msgs.append("Entry %d: only submitted entries can be rejected." % eid)
                    continue
                con.execute("update time_entry set status='Draft', reject_reason=?, last_edited_by=?, last_edited_at=? "
                            "where entry_id=?", (reason, u, stamp(), eid))
            done += 1
        except sqlite3.DatabaseError as e:
            msgs.append("Entry %d: %s" % (eid, e))
    con.commit()
    verb = "approved" if approve else "sent back to draft"
    ok_msg = "%d entr%s %s." % (done, "y" if done == 1 else "ies", verb)
    return view_approvals(con, u, ([ok_msg] if done else []) + msgs, ok=not msgs)


# ---------- Reports ----------
def sub_row(cells, right_from=5):
    return "<tr>%s</tr>" % "".join('<td%s><b>%s</b></td>' % (' align="right"' if i >= right_from and c != "" else "", esc(c))
                                   for i, c in enumerate(cells))


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
VISIBLE_SCOPES = "(select scope_id from scope where approver_user_id=? union select scope_id from time_entry where user_id=?)"


def visible_to(rv, u):
    """SQL limiting time entries (t) on scopes (s) to what this person may see, and its arguments."""
    if rv["all"]:
        return "", ()
    return " and (t.user_id=? or s.approver_user_id=?)", (u, u)


def ctr_tree_rows(con, u=None, rv=ALL_REPORTS):
    """Client > Project > Scope > Phase > Skill code > Person > Entry. Hours/Total are approved only; Pending = Submitted + Edited.
    Sales rates and totals are only put in the page for people who may see £ values."""
    money = rv["money"]
    vis, vargs = visible_to(rv, u)
    ents = con.execute(
        "select c.name as client, pr.project_code, pr.name as project, s.scope_code, s.description, ph.phase_id, ph.name as phase, "
        "ro.skill_code, ro.name as role, rt.rate_id, rt.sales_rate, u.name as person, t.entry_id, t.work_date, t.hours, t.status "
        "from time_entry t join phase ph on ph.phase_id=t.phase_id join scope s on s.scope_id=ph.scope_id "
        "join project pr on pr.project_id=s.project_id join client c on c.client_id=s.client_id "
        "join rate rt on rt.rate_id=t.rate_id join role ro on ro.role_id=rt.role_id join app_user u on u.user_id=t.user_id "
        "where t.status<>'Draft'" + vis + " order by c.name, pr.name, s.scope_code, ph.name, ro.skill_code, u.name, t.work_date", vargs).fetchall()
    # also list phases that have no booked hours yet, so the structure is visible: all of them for cost controllers,
    # only the scopes they approve for everyone else
    empty = con.execute(
        "select c.name as client, pr.name as project, s.scope_code, s.description, ph.phase_id, ph.name as phase "
        "from phase ph join scope s on s.scope_id=ph.scope_id join project pr on pr.project_id=s.project_id "
        "join client c on c.client_id=s.client_id where s.status='Open' and c.is_internal=0 and ph.phase_id not in "
        "(select phase_id from time_entry where status<>'Draft')" + ("" if rv["all"] else " and s.approver_user_id=?"),
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
        ph = node(path[-1]["kids"], e["phase_id"], e["phase"], "Phase")
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
        node(s["kids"], e["phase_id"], e["phase"], "Phase")["phase_id"] = e["phase_id"]

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
    """Client > Project > Scope > Phase filter for the period report. Each dropdown only lists what fits the choices above it,
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
    phases = [(r["phase_id"], r["name"] if scope else "%s - %s" % (r["scope_code"], r["name"])) for r in con.execute(
        "select ph.phase_id, ph.name, s.scope_code from phase ph join scope s on s.scope_id=ph.scope_id "
        "join client c on c.client_id=s.client_id "
        "where c.is_internal=0 and (? is null or s.client_id=?) and (? is null or s.project_id=?) and (? is null or s.scope_id=?)" + in_scopes
        + " order by s.scope_code, ph.name", (client, client, project, project, scope, scope) + vargs)]
    phase = pick("phase", phases)
    return (client, project, scope, phase), (clients, projects, scopes, phases)


def view_reports(con, u, q):
    if permission_of(con, u) == "admin":  # Admin (IT) has no reports
        return no_access(con, u, "Reports")
    rv = report_view(con, u)
    money = rv["money"]
    d_from = q.get("from", [""])[0] or "2026-10-01"
    d_to = q.get("to", [""])[0] or "2026-12-31"
    (client, project, scope, phase), (clients, projects, scopes, phases) = period_filters(con, q, u, rv)
    chosen = (client, client, project, project, scope, scope, phase, phase)
    if money:
        per = con.execute("select week_start, client, project, scope_code, phase, skill_code, role, hours, total from v_period_report "
                          "where week_start between ? and ? and (scope_code, phase) in "
                          "(select s.scope_code, ph.name from phase ph join scope s on s.scope_id=ph.scope_id "
                          "where (? is null or s.client_id=?) and (? is null or s.project_id=?) and (? is null or s.scope_id=?) "
                          "and (? is null or ph.phase_id=?)) "
                          "order by week_start, client, project, scope_code, phase, skill_code",
                          (d_from, d_to) + chosen).fetchall()
    else:
        # the view above adds everyone's hours together, so for everyone else the same figures are built from their own entries only
        per = con.execute(
            "select date(t.work_date,'weekday 0','-6 days') as week_start, c.name as client, p.name as project, s.scope_code, "
            "ph.name as phase, r.skill_code, r.name as role, sum(t.hours) as hours, 0 as total "
            "from time_entry t join phase ph on ph.phase_id=t.phase_id join scope s on s.scope_id=ph.scope_id "
            "join project p on p.project_id=s.project_id join client c on c.client_id=s.client_id "
            "join rate rt on rt.rate_id=t.rate_id join role r on r.role_id=rt.role_id "
            "where t.status='Approved' and date(t.work_date,'weekday 0','-6 days') between ? and ? "
            "and (t.user_id=? or s.approver_user_id=?) "
            "and (? is null or s.client_id=?) and (? is null or s.project_id=?) and (? is null or s.scope_id=?) and (? is null or ph.phase_id=?) "
            "group by week_start, ph.phase_id, rt.rate_id order by week_start, client, project, s.scope_code, phase, r.skill_code",
            (d_from, d_to, u, u) + chosen).fetchall()
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

    t2 = ["<tr><th>Week commencing</th><th>Client</th><th>Project</th><th>Scope</th><th>Phase</th><th>Skill code</th>"
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
                     "Each phase is that phase's CTR, priced from the client rate card by skill code. All weeks to date.")
        period_note = ("These are the same approved hours as the CTR, split by week. Each line here is one week of a CTR line above, "
                       "so it is smaller than the all-weeks figure. When the date range covers every week, the total for range matches the CTR grand total.")
        filtered_note = "Filtered to the client, project, scope or phase chosen above, so the total for range is only part of the CTR grand total."
    else:
        tree_note = ("Actual booked hrs are approved hours only. Pending hrs are submitted or edited hours still waiting for approval. "
                     "Click [+] / [-] to open a row down to the person and their timesheet entries. You see your own hours and, for any scope you approve, "
                     "everyone's hours on it. All weeks to date.")
        period_note = ("These are the same approved hours as the CTR, split by week. Each line here is one week of a CTR line above, "
                       "so it is smaller than the all-weeks figure.")
        filtered_note = "Filtered to the client, project, scope or phase chosen above, so the total for range is only part of your hours."

    b = ["<h4>CTR (Cost Time Resource) - by client, project, scope and phase</h4>",
         "<p><small>%s</small></p>" % tree_note,
         '<p><input type="button" value="Expand all" onclick="openTo(9)"> <input type="button" value="Collapse all" onclick="openTo(0)"> '
         '<input type="button" value="Show to phase" onclick="openTo(3)"> <input type="button" value="Show to skill code" onclick="openTo(4)"> '
         '<input type="button" value="Show to person" onclick="openTo(5)"> <input type="button" value="Show to entries" onclick="openTo(6)"></p>',
         '<table id="ctr" border="1" cellpadding="3" cellspacing="0">%s%s%s</table>' % (head, tree_rows, foot),
         REPORT_JS,
         "<h4>Period reports (weekly) - approved hours</h4>",
         "<p><small>%s</small></p>" % period_note,
         '<form method="get" action="/reports">%s<p>Client %s Project %s Scope %s Phase %s</p>'
         '<p>From %s To %s '
         '<input type="submit" value="Show"> <input type="button" value="Reset" onclick="location.href=\'/reports?u=%d\'"></p></form>' % (
             hidden(u=u),
             sel("client", clients, client, True, "All clients", auto), sel("project", projects, project, True, "All projects", auto),
             sel("scope", scopes, scope, True, "All scopes", auto), sel("phase", phases, phase, True, "All phases", auto),
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
    return layout(con, u, "Reports", "".join(b))


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
    ph = con.execute("select ph.phase_id, ph.name as phase, ph.status as phase_status, s.scope_code, s.description, s.status as scope_status, "
                     "pr.project_code, pr.name as project, c.name as client, a.name as approver "
                     "from phase ph join scope s on s.scope_id=ph.scope_id join project pr on pr.project_id=s.project_id "
                     "join client c on c.client_id=s.client_id join app_user a on a.user_id=s.approver_user_id "
                     "where ph.phase_id=?", (pid,)).fetchone()
    if not ph:
        return layout(con, u, "CTR", "<p>CTR not found. Open one from the <a href=\"/reports?u=%d\">Reports</a> page.</p>" % u)
    ents = con.execute(
        "select t.entry_id, t.work_date, t.hours, t.status, t.approved_at, r.family, r.skill_code, r.name as title, rt.sales_rate, "
        "u.name as person, ap.name as approved_by "
        "from time_entry t join scope s on s.scope_id=t.scope_id join rate rt on rt.rate_id=t.rate_id join role r on r.role_id=rt.role_id "
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
    if fam != "Summary" and fam not in families:
        fam = "Summary"
    base = "/ctr?u=%d&amp;phase=%d" % (u, pid)
    tabs = ['<b>[Summary]</b>' if fam == "Summary" else '<a href="%s&amp;family=Summary">Summary</a>' % base]
    for f in families:
        tabs.append("<b>[%s]</b>" % esc(f) if f == fam else '<a href="%s&amp;family=%s">%s</a>' % (base, quote(f), esc(f)))

    head = ('<p><a href="/reports?u=%d">&lt;&lt; Back to Reports</a></p>'
            "<table border=\"0\" cellpadding=\"2\"><tr><td>Client</td><td><b>%s</b></td></tr><tr><td>Project</td><td><b>%s %s</b></td></tr>"
            "<tr><td>Scope</td><td><b>%s %s</b> (%s)</td></tr><tr><td>Phase</td><td><b>%s</b> (%s)</td></tr>"
            "<tr><td>Approver</td><td>%s</td></tr></table>" % (
                u, esc(ph["client"]), esc(ph["project_code"]), esc(ph["project"]), esc(ph["scope_code"]), esc(ph["description"]),
                esc(ph["scope_status"]), esc(ph["phase"]), esc(ph["phase_status"]), esc(ph["approver"])))
    b = [head, "<p>CTR tabs: %s</p>" % " | ".join(tabs)]
    if not ents:
        b.append("<p>No hours have been submitted to this phase yet.</p>")
        return layout(con, u, "CTR", "".join(b))

    tcol = "<th>Total</th>" if money else ""
    if fam == "Summary":
        b.append("<h4>CTR summary - %s %s, %s</h4><p><small>Actual booked hrs are approved hours. Pending hrs are submitted or edited hours waiting for approval. "
                 "Click a job family tab for its resources, people and timesheet entries.</small></p>" % (esc(ph["scope_code"]), esc(ph["description"]), esc(ph["phase"])))
        rows = []
        for f in families:
            h, t, p = tot([e for e in ents if e["family"] == f])
            rows.append("<tr><td><a href=\"%s&amp;family=%s\">%s</a></td>%s%s%s</tr>" % (
                base, quote(f), esc(f), td(fmt(h), True), td("%.2f" % t, True) if money else "", td(fmt(p), True)))
        h, t, p = tot(ents)
        rows.append(sub_row(["Total", fmt(h)] + (["%.2f" % t] if money else []) + [fmt(p)], right_from=1))
        b.append('<table border="1" cellpadding="3" cellspacing="0"><tr><th>Job family</th><th>Actual booked hrs</th>%s<th>Pending hrs</th></tr>%s</table>' % (tcol, "".join(rows)))
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
            rows.append("<tr>%s%s%s%s%s%s%s</tr>" % (
                td(esc(e["work_date"])), td(esc(e["person"])), td(esc("%s %s" % (e["skill_code"], e["title"]))), td(fmt(e["hours"]), True),
                td("%.2f" % (e["hours"] * e["sales_rate"]), True) if money else "", td(esc(e["status"])), td(esc(e["approved_by"] or ""))))
        b.append('<p><b>Timesheet entries</b> <small>(%sonly Approved entries count towards the CTR)</small></p>'
                 '<table border="1" cellpadding="3" cellspacing="0"><tr><th>Date</th><th>Person</th><th>Booked as</th><th>Hours</th>%s'
                 '<th>Status</th><th>Approved by</th></tr>%s</table>' % ("value = hours x sales rate; " if money else "", "<th>Value</th>" if money else "", "".join(rows)))
    return layout(con, u, "CTR - %s %s - %s" % (ph["scope_code"], ph["description"], ph["phase"]), "".join(b))


# ---------- Admin ----------
ADMIN_TABS = [("periods", "Weekly periods"), ("users", "Users"), ("roles", "Job families / titles"),
              ("clients", "Clients"), ("rates", "Client rate cards"), ("paafs", "PAAFs"), ("scopes", "Projects / scopes / phases"),
              ("internal", "Internal codes")]


# Who may use each Admin tab. IT manages users and job roles; cost controllers manage clients, rate cards, PAAFs, scopes and internal codes.
# Weekly periods is open to both controllers and admins until the cost team confirms who owns it (the audit trail is handled the same way).
TAB_ACCESS = {"periods": ("controller", "admin"), "users": ("admin",), "roles": ("admin",), "clients": ("controller",),
              "rates": ("controller",), "paafs": ("controller",), "scopes": ("controller",), "internal": ("controller",)}
PERMISSION_OPTIONS = [("user", "user"), ("controller", "controller (cost controller)"), ("admin", "admin (IT)")]


def tab_of(t):
    return {"projects": "scopes", "phases": "scopes"}.get(t, t)  # projects and phases are edited from the scopes tab


def can_use_tab(con, u, t):
    return permission_of(con, u) in TAB_ACCESS.get(tab_of(t), ())


def allowed_tabs(con, u):
    return [(k, v) for k, v in ADMIN_TABS if can_use_tab(con, u, k)]


def sel(name, options, selected=None, blank=False, blank_label="-", attrs=""):
    out = ['<select name="%s"%s>' % (name, attrs)]
    if blank:
        out.append('<option value="">%s</option>' % esc(blank_label))
    for val, label in options:
        out.append('<option value="%s"%s>%s</option>' % (esc(val), " selected" if str(val) == str(selected) else "", esc(label)))
    out.append("</select>")
    return "".join(out)


def txt(name, value="", size=20):
    return '<input type="text" name="%s" size="%d" value="%s">' % (name, size, esc(value))


def date_input(name, value=""):
    """A date box that opens the browser's calendar picker. It sends yyyy-mm-dd, which is what the server expects."""
    return '<input type="date" name="%s" value="%s">' % (name, esc(value))


def hidden(**kw):
    return "".join('<input type="hidden" name="%s" value="%s">' % (k, esc(v)) for k, v in kw.items())


def opts_roles(con, active_only=True):
    q = "select role_id, skill_code || ' ' || name from role %s order by skill_code" % ("where active=1" if active_only else "")
    return [tuple(r) for r in con.execute(q)]


def opts_clients(con, billable_only=False):
    """billable_only leaves out the Internal client, which has no rate card and needs no PAAFs."""
    return [tuple(r) for r in con.execute("select client_id, name from client %s order by name" % ("where is_internal=0" if billable_only else ""))]


def opts_projects(con):
    return [tuple(r) for r in con.execute("select p.project_id, c.name || ' - ' || p.name from project p join client c on c.client_id=p.client_id "
                                          "order by c.name, p.name")]


def opts_scopes(con):
    return [tuple(r) for r in con.execute("select s.scope_id, c.name || ' - ' || s.scope_code || ' ' || s.description from scope s "
                                          "join client c on c.client_id=s.client_id order by c.name, s.scope_code")]


def opts_users(con):
    return [tuple(r) for r in con.execute("select user_id, name from app_user order by name")]


def opts_approvers(con):
    """Only standard users can be named as an approver (cost controllers and admins never approve)."""
    return [tuple(r) for r in con.execute("select user_id, name from app_user where permission='user' order by name")]


def need_date(value, label, required=True):
    value = (value or "").strip()
    if not value:
        if required:
            raise ValueError("%s is required (yyyy-mm-dd)." % label)
        return None
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        raise ValueError("%s must be a date like 2026-11-09." % label)
    return value


def need_text(value, label):
    value = (value or "").strip()
    if not value:
        raise ValueError("%s is required." % label)
    return value


def edit_link(u, t, i):
    return '<a href="/admin/edit?u=%d&amp;t=%s&amp;id=%d">Edit</a>' % (u, t, i)


def admin_body(con, u, t):
    b = []
    if t == "periods":
        rows = [list(r) for r in con.execute("select week_start, status, closed_by, closed_at from period order by week_start")]
        b.append(table(["week_start", "status", "closed_by", "closed_at"], [r[:3] + [fmt_time(r[3])] for r in rows]))
        closes = "".join('<form method="post" action="/admin/period" style="display:inline">%s'
                         '<input type="submit" value="Close week %s"></form> ' % (hidden(u=u, week_start=r[0], action="close"), r[0])
                         for r in rows if r[1] == "Open")
        b.append("<p>%s</p>" % closes)
        b.append("<p><small>Each week opens automatically when the week starts. Close a week here when it should stop taking new hours.</small></p>")
        b.append('<form method="post" action="/admin/period">%s<b>Open a missed week</b> - Monday date %s '
                 '<input type="submit" value="Open week"></form>' % (hidden(u=u, action="open"), date_input("week_start")))
    elif t == "users":
        rows = [[r["nmc_id"], r["name"], r["employment_type"], r["skill_code"] + " " + r["role"], r["permission"], r["start_date"],
                 r["end_date"], edit_link(u, "users", r["user_id"])]
                for r in con.execute("select u.*, r.skill_code, r.name as role from app_user u join role r on r.role_id=u.role_id order by u.nmc_id")]
        b.append(table(["NMC ID", "Name", "Employment", "Role", "Permission", "Start", "End", ""], rows, raw_cols=(7,)))
        b.append('<h4>Add a user</h4><form method="post" action="/admin/add">%s<table border="0" cellpadding="2">'
                 "<tr><td>NMC ID</td><td>%s</td></tr><tr><td>Name</td><td>%s</td></tr>"
                 "<tr><td>Employment</td><td>%s</td></tr><tr><td>Role</td><td>%s</td></tr>"
                 "<tr><td>Permission</td><td>%s</td></tr><tr><td>Start date</td><td>%s</td></tr>"
                 "<tr><td>End date (optional)</td><td>%s</td></tr></table><input type=\"submit\" value=\"Add user\"></form>"
                 % (hidden(u=u, t="users"), txt("nmc_id", "", 8), txt("name", "", 30),
                    sel("employment_type", [("Staff", "Staff"), ("Contract", "Contract")]), sel("role_id", opts_roles(con)),
                    sel("permission", PERMISSION_OPTIONS), date_input("start_date", datetime.date.today().isoformat()),
                    date_input("end_date")))
    elif t == "roles":
        rows = [[r["family"], r["skill_code"], r["name"], "Yes" if r["active"] else "No", edit_link(u, "roles", r["role_id"])]
                for r in con.execute("select * from role order by family, skill_code")]
        b.append(table(["Job family", "Skill code", "Job title", "Active", ""], rows, raw_cols=(4,)))
        b.append('<h4>Add a job title</h4><form method="post" action="/admin/add">%s Job family %s Skill code %s Job title %s '
                 '<input type="submit" value="Add role"></form>' % (hidden(u=u, t="roles"), txt("family", "", 24), txt("skill_code", "", 8), txt("name", "", 30)))
    elif t == "clients":
        rows = [[r["name"], "Internal (not billed)" if r["is_internal"] else "Client"]
                for r in con.execute("select name, is_internal from client order by is_internal, name")]
        b.append(table(["Client", "Type"], rows))
        b.append('<h4>Add a client</h4><form method="post" action="/admin/add">%s Name %s <input type="submit" value="Add client"></form>'
                 % (hidden(u=u, t="clients"), txt("name", "", 30)))
    elif t == "rates":
        rows = [[r["client"], r["skill_code"], r["role"], "%.2f" % r["sales_rate"], r["effective_from"]]
                for r in con.execute("select c.name as client, ro.skill_code, ro.name as role, rt.sales_rate, rt.effective_from "
                                     "from rate rt join client c on c.client_id=rt.client_id join role ro on ro.role_id=rt.role_id "
                                     "order by c.name, rt.sales_rate, rt.effective_from")]
        b.append(table(["Client", "Skill code", "Role", "Sales rate", "Effective from"], rows))
        b.append('<h4>Add a rate</h4><p><small>Rates are never edited. To change a rate, add a new row with a later effective date. '
                 "Old rows stay, so hours already booked keep the price they were booked at.</small></p>"
                 '<form method="post" action="/admin/add">%s Client %s Skill code %s Sales rate %s Effective from %s '
                 '<input type="submit" value="Add rate"></form>'
                 % (hidden(u=u, t="rates"), sel("client_id", opts_clients(con, True)), sel("role_id", opts_roles(con)),
                    txt("sales_rate", "", 8), date_input("effective_from", datetime.date.today().isoformat())))
    elif t == "paafs":
        rows = []
        for r in con.execute("select p.*, u.name as uname, c.name as client, ro.skill_code, rt.sales_rate "
                             "from paaf p join app_user u on u.user_id=p.user_id join client c on c.client_id=p.client_id "
                             "join rate rt on rt.rate_id=p.rate_id join role ro on ro.role_id=rt.role_id order by c.name, u.name"):
            f = '<form method="post" action="/admin/paaf" style="display:inline">%s' % hidden(u=u, paaf_id=r["paaf_id"])
            if r["status"] == "Draft":
                f += ('<input type="submit" name="action" value="Authorise internally"> '
                      '<input type="submit" name="action" value="Mark sent"> <small>(sent = formal client approval)</small>')
            elif r["status"] == "Sent":
                f += ('Signed form ref %s Expiry (optional) %s <input type="submit" name="action" value="Approve"> '
                      '<input type="submit" name="action" value="Reject">' % (txt("document_ref", "", 28), date_input("expiry_date")))
            elif r["status"] == "Rejected":
                f += '<input type="submit" name="action" value="Reopen as draft">'
            f += "</form>"
            rows.append([r["paaf_id"], r["uname"], r["client"], r["skill_code"], "%g" % r["sales_rate"], r["status"], r["sent_date"],
                         r["decided_date"], r["document_ref"], r["expiry_date"], f])
        b.append(table(["#", "Name", "Client", "Skill code", "Sales rate", "Status", "Sent", "Decided", "Signed form", "Expiry", "Action"],
                       rows, raw_cols=(10,)))
        b.append('<h4>Raise a PAAF</h4><p><small>One PAAF per person per client. The rate is taken from the person\'s role on that '
                 "client's rate card. Mark it sent when it goes to the client, then approve it once the signed form comes back.</small></p>"
                 '<form method="post" action="/admin/add">%s Person %s Client %s <input type="submit" value="Raise PAAF"></form>'
                 % (hidden(u=u, t="paafs"), sel("user_id", opts_users(con)), sel("client_id", opts_clients(con, True))))
    elif t == "internal":
        b.append("<p><small>Internal codes are for hours NMC pays for itself, such as Business Services or Training. People pick one in their "
                 "timesheet (shown as Internal - name). They need no PAAF and have no rate, and they are kept out of the client reports. "
                 "Each code has an approver, who approves the hours booked to it. Close a code to stop new bookings to it.</small></p>")
        rows = [[r["scope_code"], r["description"], r["status"], r["approver"], edit_link(u, "internal", r["scope_id"])]
                for r in con.execute("select s.scope_id, s.scope_code, s.description, s.status, a.name as approver from scope s "
                                     "join client c on c.client_id=s.client_id join app_user a on a.user_id=s.approver_user_id "
                                     "where c.is_internal=1 order by s.scope_code")]
        b.append(table(["Code", "Name", "Status", "Approver", ""], rows, raw_cols=(4,)))
        b.append('<h4>Add an internal code</h4><form method="post" action="/admin/add">%s<table border="0" cellpadding="2">'
                 "<tr><td>Name</td><td>%s</td></tr><tr><td>Approver</td><td>%s</td></tr></table>"
                 '<input type="submit" value="Add internal code"></form>'
                 % (hidden(u=u, t="internal"), txt("description", "", 40), sel("approver_user_id", opts_approvers(con))))
    elif t == "scopes":
        b.append("<p><small>Structure: client &gt; project &gt; scope &gt; phase. Hours are booked to a phase and approved by the scope's approver.</small></p>")
        b.append("<h4>Projects</h4>")
        rows = [[r["client"], r["project_code"], r["name"], r["status"], edit_link(u, "projects", r["project_id"])]
                for r in con.execute("select p.*, c.name as client from project p join client c on c.client_id=p.client_id order by c.name, p.name")]
        b.append(table(["Client", "Code", "Project", "Status", ""], rows, raw_cols=(4,)))
        b.append('<form method="post" action="/admin/add">%s Client %s Code %s Name %s <input type="submit" value="Add project"></form>'
                 % (hidden(u=u, t="projects"), sel("client_id", opts_clients(con)), txt("project_code", "", 10), txt("name", "", 30)))
        b.append("<h4>Scopes</h4>")
        rows = [[r["client"], r["project"], r["scope_code"], r["description"], r["status"], r["approver"], edit_link(u, "scopes", r["scope_id"])]
                for r in con.execute("select s.scope_id, c.name as client, pr.name as project, s.scope_code, s.description, s.status, a.name as approver "
                                     "from scope s join client c on c.client_id=s.client_id join project pr on pr.project_id=s.project_id "
                                     "join app_user a on a.user_id=s.approver_user_id order by c.name, pr.name, s.scope_code")]
        b.append(table(["Client", "Project", "Scope", "Description", "Status", "Approver", ""], rows, raw_cols=(6,)))
        b.append('<p><b>Add a scope</b> <small>Every scope needs an approver, normally the project manager. A phase called General is created automatically; '
                 'add or rename phases below.</small></p>'
                 '<form method="post" action="/admin/add">%s<table border="0" cellpadding="2">'
                 "<tr><td>Project</td><td>%s</td></tr><tr><td>Scope code</td><td>%s</td></tr>"
                 "<tr><td>Description</td><td>%s</td></tr><tr><td>Status</td><td>%s</td></tr>"
                 "<tr><td>Approver</td><td>%s</td></tr></table><input type=\"submit\" value=\"Add scope\"></form>"
                 % (hidden(u=u, t="scopes"), sel("project_id", opts_projects(con)), txt("scope_code", "", 10), txt("description", "", 40),
                    sel("status", [("Open", "Open"), ("Pipeline", "Pipeline"), ("Closed", "Closed")]),
                    sel("approver_user_id", opts_approvers(con))))
        b.append("<h4>Phases</h4>")
        rows = [[r["client"], r["scope_code"], r["description"], r["name"], r["status"], edit_link(u, "phases", r["phase_id"])]
                for r in con.execute("select ph.*, s.scope_code, s.description, c.name as client from phase ph join scope s on s.scope_id=ph.scope_id "
                                     "join client c on c.client_id=s.client_id order by c.name, s.scope_code, ph.name")]
        b.append(table(["Client", "Scope", "Description", "Phase", "Status", ""], rows, raw_cols=(5,)))
        b.append('<form method="post" action="/admin/add">%s Scope %s Phase name %s <input type="submit" value="Add phase"></form>'
                 % (hidden(u=u, t="phases"), sel("scope_id", opts_scopes(con)), txt("name", "", 20)))
    return "".join(b)


def view_admin(con, u, t, msgs=None, ok=False):
    tabs = allowed_tabs(con, u)
    if not tabs or (t in dict(ADMIN_TABS) and not can_use_tab(con, u, t)):
        return no_access(con, u, "Admin")
    t = t if t in dict(tabs) else tabs[0][0]
    links = "".join('<a href="/admin?u=%d&amp;t=%s"%s>%s</a>' % (u, k, ' class="cur"' if k == t else "", esc(v)) for k, v in tabs)
    body = '%s<p class="tabs">%s</p><h4>%s</h4>%s' % (msgs_html(msgs, ok), links, esc(dict(tabs)[t]), admin_body(con, u, t))
    return layout(con, u, "Admin", body)


def view_admin_edit(con, u, t, i, msgs=None):
    if not can_use_tab(con, u, t):
        return no_access(con, u, "Admin")
    back = '<p><a href="/admin?u=%d&amp;t=%s">&lt;&lt; Back</a></p>' % (u, "scopes" if t in ("projects", "phases") else t)
    if t == "users":
        r = con.execute("select * from app_user where user_id=?", (i,)).fetchone()
        if not r:
            return layout(con, u, "Admin", "<p>Not found.</p>")
        form = ("<tr><td>NMC ID</td><td>%s</td></tr><tr><td>Name</td><td>%s</td></tr><tr><td>Employment</td><td>%s</td></tr>"
                "<tr><td>Role</td><td>%s</td></tr><tr><td>Permission</td><td>%s</td></tr><tr><td>Start date</td><td>%s</td></tr>"
                "<tr><td>End date</td><td>%s</td></tr>" % (
                    esc(r["nmc_id"]), txt("name", r["name"], 30), sel("employment_type", [("Staff", "Staff"), ("Contract", "Contract")], r["employment_type"]),
                    sel("role_id", opts_roles(con, active_only=False), r["role_id"]),
                    sel("permission", PERMISSION_OPTIONS, r["permission"]), esc(r["start_date"]), date_input("end_date", r["end_date"] or "")))
        note = "<p><small>Changing someone's role does not change their existing PAAFs or booked hours.</small></p>"
    elif t == "roles":
        r = con.execute("select * from role where role_id=?", (i,)).fetchone()
        if not r:
            return layout(con, u, "Admin", "<p>Not found.</p>")
        form = ("<tr><td>Job family</td><td>%s</td></tr><tr><td>Skill code</td><td>%s</td></tr><tr><td>Job title</td><td>%s</td></tr><tr><td>Active</td><td>%s</td></tr>"
                % (txt("family", r["family"], 24), txt("skill_code", r["skill_code"], 8), txt("name", r["name"], 30), sel("active", [("1", "Yes"), ("0", "No")], str(r["active"]))))
        note = "<p><small>Deactivating a role hides it from new users and new timesheet rows. It does not affect past entries.</small></p>"
    elif t in ("scopes", "internal"):  # an internal code is a scope of the Internal client
        r = con.execute("select s.*, c.name as client from scope s join client c on c.client_id=s.client_id where scope_id=?", (i,)).fetchone()
        if not r:
            return layout(con, u, "Admin", "<p>Not found.</p>")
        form = ("<tr><td>Client</td><td>%s</td></tr><tr><td>Scope code</td><td>%s</td></tr><tr><td>Description</td><td>%s</td></tr>"
                "<tr><td>Status</td><td>%s</td></tr><tr><td>Approver</td><td>%s</td></tr>" % (
                    esc(r["client"]), esc(r["scope_code"]), txt("description", r["description"], 40),
                    sel("status", [("Open", "Open"), ("Pipeline", "Pipeline"), ("Closed", "Closed")], r["status"]),
                    sel("approver_user_id", opts_approvers(con), r["approver_user_id"])))
        note = "<p><small>Changing the approver moves any waiting entries to the new approver's queue. Closing a scope stops new bookings.</small></p>"
    elif t == "projects":
        r = con.execute("select p.*, c.name as client from project p join client c on c.client_id=p.client_id where project_id=?", (i,)).fetchone()
        if not r:
            return layout(con, u, "Admin", "<p>Not found.</p>")
        form = ("<tr><td>Client</td><td>%s</td></tr><tr><td>Code</td><td>%s</td></tr><tr><td>Name</td><td>%s</td></tr><tr><td>Status</td><td>%s</td></tr>"
                % (esc(r["client"]), esc(r["project_code"]), txt("name", r["name"], 30), sel("status", [("Open", "Open"), ("Closed", "Closed")], r["status"])))
        note = "<p><small>Closing a project stops new bookings to all of its scopes.</small></p>"
    elif t == "phases":
        r = con.execute("select ph.*, s.scope_code from phase ph join scope s on s.scope_id=ph.scope_id where phase_id=?", (i,)).fetchone()
        if not r:
            return layout(con, u, "Admin", "<p>Not found.</p>")
        form = ("<tr><td>Scope</td><td>%s</td></tr><tr><td>Phase name</td><td>%s</td></tr><tr><td>Status</td><td>%s</td></tr>"
                % (esc(r["scope_code"]), txt("name", r["name"], 20), sel("status", [("Open", "Open"), ("Closed", "Closed")], r["status"])))
        note = "<p><small>Closing a phase stops new bookings to it. Existing hours are not affected.</small></p>"
    else:
        return layout(con, u, "Admin", "<p>Not found.</p>")
    body = '%s%s<form method="post" action="/admin/save">%s<table border="0" cellpadding="2">%s</table>%s<input type="submit" value="Save changes"></form>' % (
        msgs_html(msgs), back, hidden(u=u, t=t, id=i), form, note)
    return layout(con, u, "Edit", body)


def admin_add(con, form):
    u, t = int(form["u"][0]), form["t"][0]
    if not can_use_tab(con, u, t):
        return view_admin(con, u, t)
    g = lambda k: form.get(k, [""])[0].strip()
    try:
        if t == "users":
            con.execute("insert into app_user (nmc_id, employment_type, name, role_id, permission, start_date, end_date) values (?,?,?,?,?,?,?)",
                        (need_text(g("nmc_id"), "NMC ID"), g("employment_type"), need_text(g("name"), "Name"), int(g("role_id")),
                         g("permission"), need_date(g("start_date"), "Start date"), need_date(g("end_date"), "End date", False)))
            msg = "User added."
        elif t == "roles":
            con.execute("insert into role (skill_code, name, family) values (?,?,?)",
                        (need_text(g("skill_code"), "Skill code"), need_text(g("name"), "Job title"), need_text(g("family"), "Job family")))
            msg = "Role added."
        elif t == "clients":
            con.execute("insert into client (name) values (?)", (need_text(g("name"), "Client name"),))
            msg = "Client added."
        elif t == "rates":
            try:
                rate = float(g("sales_rate"))
            except ValueError:
                raise ValueError("Sales rate must be a number.")
            con.execute("insert into rate (client_id, role_id, sales_rate, effective_from) values (?,?,?,?)",
                        (int(g("client_id")), int(g("role_id")), rate, need_date(g("effective_from"), "Effective from")))
            msg = "Rate added."
        elif t == "projects":
            con.execute("insert into project (project_code, name, client_id) values (?,?,?)",
                        (need_text(g("project_code"), "Project code"), need_text(g("name"), "Project name"), int(g("client_id"))))
            msg = "Project added."
        elif t == "scopes":
            pr = con.execute("select client_id from project where project_id=?", (int(g("project_id") or 0),)).fetchone()
            if not pr:
                raise ValueError("Choose a project (add one first if there are none).")
            cur = con.execute("insert into scope (scope_code, client_id, project_id, description, status, approver_user_id) values (?,?,?,?,?,?)",
                              (need_text(g("scope_code"), "Scope code"), pr[0], int(g("project_id")), need_text(g("description"), "Description"),
                               g("status"), int(g("approver_user_id"))))
            con.execute("insert into phase (scope_id, name) values (?, 'General')", (cur.lastrowid,))
            msg = "Scope added with a General phase."
        elif t == "phases":
            con.execute("insert into phase (scope_id, name) values (?,?)", (int(g("scope_id")), need_text(g("name"), "Phase name")))
            msg = "Phase added."
        elif t == "internal":
            # An internal code is a scope (with a General phase) under the Internal client's project. The code is numbered for you.
            pr = con.execute("select p.project_id, p.client_id from project p join client c on c.client_id=p.client_id "
                             "where c.is_internal=1 order by p.project_id limit 1").fetchone()
            if not pr:
                raise ValueError("There is no Internal client and project to add codes to.")
            n = con.execute("select coalesce(max(cast(substr(scope_code, 4) as integer)), 0) + 1 from scope "
                            "where scope_code like 'INT%' and substr(scope_code, 4) glob '[0-9]*'").fetchone()[0]
            code = "INT%02d" % n
            cur = con.execute("insert into scope (scope_code, client_id, project_id, description, status, approver_user_id) values (?,?,?,?, 'Open', ?)",
                              (code, pr["client_id"], pr["project_id"], need_text(g("description"), "Name"), int(g("approver_user_id"))))
            con.execute("insert into phase (scope_id, name) values (?, 'General')", (cur.lastrowid,))
            msg = "Internal code %s added." % code
        elif t == "paafs":
            user_id, client_id = int(g("user_id")), int(g("client_id"))
            rate = con.execute("select rt.rate_id from rate rt join app_user u on u.role_id=rt.role_id where u.user_id=? and rt.client_id=? "
                               "and rt.effective_from<=? order by rt.effective_from desc limit 1",
                               (user_id, client_id, datetime.date.today().isoformat())).fetchone()
            if not rate:
                raise ValueError("That client's rate card has no rate for this person's role. Add the rate first.")
            con.execute("insert into paaf (user_id, client_id, rate_id, status) values (?,?,?,'Draft')", (user_id, client_id, rate[0]))
            msg = "PAAF raised as a draft."
        else:
            raise ValueError("Unknown form.")
        con.commit()
        return view_admin(con, u, t, [msg], ok=True)
    except ValueError as e:
        return view_admin(con, u, t, [str(e)])
    except sqlite3.IntegrityError as e:
        text = str(e)
        if "UNIQUE" in text:
            text = "That already exists (it must be unique)."
            if t == "paafs":
                text = "That person already has a PAAF for this client."
        return view_admin(con, u, t, [text])
    except sqlite3.DatabaseError as e:
        return view_admin(con, u, t, [str(e)])


def admin_save(con, form):
    u, t, i = int(form["u"][0]), form["t"][0], int(form["id"][0])
    if not can_use_tab(con, u, t):
        return view_admin(con, u, t)
    g = lambda k: form.get(k, [""])[0].strip()
    try:
        if t == "users":
            con.execute("update app_user set name=?, employment_type=?, role_id=?, permission=?, end_date=? where user_id=?",
                        (need_text(g("name"), "Name"), g("employment_type"), int(g("role_id")), g("permission"),
                         need_date(g("end_date"), "End date", False), i))
        elif t == "roles":
            con.execute("update role set skill_code=?, name=?, family=?, active=? where role_id=?",
                        (need_text(g("skill_code"), "Skill code"), need_text(g("name"), "Job title"), need_text(g("family"), "Job family"), int(g("active")), i))
        elif t in ("scopes", "internal"):
            con.execute("update scope set description=?, status=?, approver_user_id=? where scope_id=?",
                        (need_text(g("description"), "Description"), g("status"), int(g("approver_user_id")), i))
        elif t == "projects":
            con.execute("update project set name=?, status=? where project_id=?", (need_text(g("name"), "Project name"), g("status"), i))
        elif t == "phases":
            con.execute("update phase set name=?, status=? where phase_id=?", (need_text(g("name"), "Phase name"), g("status"), i))
        else:
            raise ValueError("Unknown form.")
        con.commit()
        return view_admin(con, u, "scopes" if t in ("projects", "phases") else t, ["Saved."], ok=True)
    except ValueError as e:
        return view_admin_edit(con, u, t, i, [str(e)])
    except sqlite3.DatabaseError as e:
        text = "That already exists (it must be unique)." if "UNIQUE" in str(e) else str(e)
        return view_admin_edit(con, u, t, i, [text])


def act_paaf(con, form):
    u, pid, action = int(form["u"][0]), int(form["paaf_id"][0]), form["action"][0]
    if not can_use_tab(con, u, "paafs"):
        return view_admin(con, u, "paafs")
    today = datetime.date.today().isoformat()
    g = lambda k: form.get(k, [""])[0].strip()
    try:
        p = con.execute("select * from paaf where paaf_id=?", (pid,)).fetchone()
        if not p:
            raise ValueError("PAAF not found.")
        if action == "Mark sent" and p["status"] == "Draft":
            con.execute("update paaf set status='Sent', sent_date=? where paaf_id=?", (today, pid))
            msg = "PAAF %d marked as sent." % pid
        elif action == "Authorise internally" and p["status"] in ("Draft", "Sent"):
            con.execute("update paaf set status='Approved', decided_date=? where paaf_id=?", (today, pid))
            msg = "PAAF %d authorised internally. The person can now book to that client." % pid
        elif action == "Approve" and p["status"] == "Sent":
            ref = need_text(g("document_ref"), "The signed form reference")
            exp = need_date(g("expiry_date"), "Expiry", False)
            con.execute("update paaf set status='Approved', decided_date=?, document_ref=?, expiry_date=? where paaf_id=?", (today, ref, exp, pid))
            msg = "PAAF %d approved. The person can now book to that client." % pid
        elif action == "Reject" and p["status"] == "Sent":
            con.execute("update paaf set status='Rejected', decided_date=? where paaf_id=?", (today, pid))
            msg = "PAAF %d rejected." % pid
        elif action == "Reopen as draft" and p["status"] == "Rejected":
            con.execute("update paaf set status='Draft', sent_date=NULL, decided_date=NULL where paaf_id=?", (pid,))
            msg = "PAAF %d reopened as a draft." % pid
        else:
            raise ValueError("That action is not available for a PAAF with status %s." % p["status"])
        con.commit()
        return view_admin(con, u, "paafs", [msg], ok=True)
    except ValueError as e:
        return view_admin(con, u, "paafs", [str(e)])
    except sqlite3.DatabaseError as e:
        return view_admin(con, u, "paafs", [str(e)])


def act_period(con, form):
    u, ws, action = int(form["u"][0]), form["week_start"][0].strip(), form["action"][0]
    if not can_use_tab(con, u, "periods"):
        return view_admin(con, u, "periods")
    try:
        if action == "open":
            if con.execute("select 1 from period where week_start=?", (ws,)).fetchone():
                return view_admin(con, u, "periods", ["Week %s already exists, so there is nothing to open." % ws])
            con.execute("insert into period (week_start) values (?)", (ws,))
            msg = ["Week %s opened." % ws]
        else:
            con.execute("update period set status='Closed', closed_by=?, closed_at=? where week_start=?", (u, stamp(), ws))
            msg = ["Week %s closed." % ws]
        con.commit()
        return view_admin(con, u, "periods", msg, ok=True)
    except sqlite3.DatabaseError as e:
        return view_admin(con, u, "periods", ["%s" % e])


def view_audit(con, u):
    if permission_of(con, u) not in ("controller", "admin"):  # who owns the audit trail is still to be confirmed with the cost team
        return no_access(con, u, "Audit trail")
    rows = con.execute(
        "select a.audit_id, a.changed_at, ch.name as changed_by, a.action, bu.name as booked_by, s.scope_code, t.work_date, "
        "a.old_status, a.new_status, a.old_hours, a.new_hours, a.reason from time_entry_audit a "
        "join time_entry t on t.entry_id=a.entry_id join app_user bu on bu.user_id=t.user_id "
        "left join app_user ch on ch.user_id=a.changed_by join scope s on s.scope_id=t.scope_id "
        "order by a.audit_id desc limit 200").fetchall()
    heads = ["#", "When", "Changed by", "Action", "Booked by", "Scope", "Date", "Old status", "New status", "Old hours", "New hours", "Reason"]
    return layout(con, u, "Audit trail (latest 200)", table(heads, [[fmt(v) if i in (9, 10) else fmt_time(v) if i == 1 else v
                                                                    for i, v in enumerate(r)] for r in rows]))


# ---------- server ----------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_html(self, text, code=200):
        data = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_asset(self, path):
        try:
            with open(ASSETS[path][0], "rb") as f:
                data = f.read()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ASSETS[path][1])
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def user_id(self, con, q):
        try:
            return int(q["u"][0])
        except (KeyError, ValueError):
            return con.execute("select user_id from app_user order by user_id limit 1").fetchone()[0]

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ASSETS:
            return self.send_asset(url.path)
        q = parse_qs(url.query)
        con = db()
        try:
            ensure_current_period(con)
            u = self.user_id(con, q)
            if url.path in ("/", "/timesheet"):
                try:
                    w = monday(datetime.date.fromisoformat(q["w"][0])) if "w" in q else monday(datetime.date.today())
                except ValueError:
                    w = monday(datetime.date.today())
                adds = parse_adds(q.get("add", []))
                if "add_phase" in q and "add_role" in q:
                    adds += parse_adds(["%s:%s" % (q["add_phase"][0], q["add_role"][0])])
                self.send_html(view_timesheet(con, u, w, adds=adds))
            elif url.path == "/approvals":
                self.send_html(view_approvals(con, u))
            elif url.path == "/reports":
                self.send_html(view_reports(con, u, q))
            elif url.path == "/ctr":
                self.send_html(view_ctr(con, u, q))
            elif url.path == "/admin":
                self.send_html(view_admin(con, u, q.get("t", [""])[0]))
            elif url.path == "/admin/edit":
                self.send_html(view_admin_edit(con, u, q.get("t", [""])[0], int(q.get("id", ["0"])[0])))
            elif url.path == "/audit":
                self.send_html(view_audit(con, u))
            else:
                self.send_html("<p>Not found</p>", 404)
        finally:
            con.close()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
        con = db()
        try:
            ensure_current_period(con)
            if self.path == "/timesheet/save":
                self.send_html(save_timesheet(con, form))
            elif self.path == "/approvals/act":
                self.send_html(act_approval(con, form))
            elif self.path == "/admin/period":
                self.send_html(act_period(con, form))
            elif self.path == "/admin/add":
                self.send_html(admin_add(con, form))
            elif self.path == "/admin/save":
                self.send_html(admin_save(con, form))
            elif self.path == "/admin/paaf":
                self.send_html(act_paaf(con, form))
            else:
                self.send_html("<p>Not found</p>", 404)
        finally:
            con.close()


class Server(ThreadingHTTPServer):
    # Threaded so Chrome's idle pre-opened connections can't block real requests.
    daemon_threads = True


def make_server(port=8000):
    return Server(("127.0.0.1", port), Handler)


def schema_problem(path):
    """A message if the database at path was built with an older schema than this app needs, otherwise None."""
    con = sqlite3.connect(path)
    try:
        have = con.execute("pragma user_version").fetchone()[0]
    finally:
        con.close()
    if have >= REQUIRED_SCHEMA:
        return None
    return ("The database %s was built with an older schema (version %d, this app needs %d), so pages would fail.\n"
            "Build a fresh one with:  python scripts/build.py\n"
            "That makes build/timewriting.db and leaves this file alone. If TW_DB is set, clear it to use the new file "
            "(PowerShell: Remove-Item Env:TW_DB)." % (path, have, REQUIRED_SCHEMA))


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    if not os.path.exists(DB_PATH):
        sys.exit("No database at %s. Run: python scripts/build.py" % DB_PATH)
    problem = schema_problem(DB_PATH)
    if problem:
        sys.exit(problem)
    print("Timewriting mock running at http://localhost:%d  (Ctrl+C to stop)" % port)
    make_server(port).serve_forever()
