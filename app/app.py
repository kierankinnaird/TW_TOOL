"""Front end for the v1 mock database. Plain HTML with one inline stylesheet, served by Flask (the pages themselves are built as plain strings).

Needs Flask:   pip install -r requirements.txt
Run from the repo root:   python app/app.py        then open  http://localhost:8000
Uses build/timewriting.db (create it first with: python scripts/build.py).
There is no login: pick who you are from the "Signed in as" box (mock).
"""
import datetime
import functools
import html
import logging
import os
import sqlite3
import sys
from flask import Flask, Response, request
from werkzeug.serving import make_server as make_wsgi_server
from urllib.parse import parse_qs, quote, urlencode, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("TW_DB", os.path.join(ROOT, "build", "timewriting.db"))
# Databases built before this version lack columns and rules the app relies on. Matches PRAGMA user_version in db/schema.sql.
REQUIRED_SCHEMA = 16
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


def table(headers, rows, raw_cols=(), raw_heads=False):
    out = ['<table border="1" cellpadding="3" cellspacing="0"><tr>']
    out += ["<th>%s</th>" % (h if raw_heads else esc(h)) for h in headers]
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


# Who approves hours on a scope: the client's main approver and the scope's secondary approvers (view v_scope_approver).
# SQL fragments for hours (entry t on scope s) and for allowances (entry a on scope s). "I may approve it" means: I am an approver of the
# scope and it is not mine. A cost controller approves entries whose booker is the scope's only approver (nobody else could).
def _may(alias):
    return ("exists (select 1 from v_scope_approver va where va.scope_id=s.scope_id and va.user_id=?) and %s.user_id<>?" % alias)


def _only_approver(alias):
    return ("exists (select 1 from v_scope_approver va where va.scope_id=s.scope_id and va.user_id=%s.user_id) "
            "and not exists (select 1 from v_scope_approver vb where vb.scope_id=s.scope_id and vb.user_id<>%s.user_id)" % (alias, alias))


def is_approver(con, u):
    """Approver is not a permission: it means being a client's main approver or a secondary approver on at least one scope."""
    return con.execute("select 1 from client where approver_user_id=? union all select 1 from scope_approver where user_id=? limit 1",
                       (u, u)).fetchone() is not None


def approval_queue(con, u):
    """What this person approves, as SQL on time entries (t) and scopes (s) with its arguments, or None if they approve nothing.
    An approver of a scope approves hours booked to it, but never their own. When the booker is the scope's only approver nobody else
    could approve the hours, so a cost controller approves those."""
    if permission_of(con, u) == "controller":
        return " and " + _only_approver("t"), ()
    if is_approver(con, u):
        return " and " + _may("t"), (u, u)
    return None


def may_action(con, u, eid):
    """Can this person approve or reject this entry? An approver of its scope can (the database refuses their own hours);
    a cost controller can, but only when the booker is the scope's only approver."""
    q = approval_queue(con, u)
    if q is None:
        return False
    return con.execute("select 1 from time_entry t join scope s on s.scope_id=t.scope_id where t.entry_id=?" + q[0], (eid,) + tuple(q[1])).fetchone() is not None


def pending_for(con, u):
    queue = approval_queue(con, u)
    if queue is None:
        return 0
    n = con.execute("select count(*) from time_entry t join scope s on s.scope_id=t.scope_id "
                    "where t.status in ('Submitted','Edited')" + queue[0], queue[1]).fetchone()[0]
    aq = allowance_queue(con, u)
    if aq:
        n += con.execute("select count(*) from allowance_entry a join scope s on s.scope_id=a.scope_id where a.status='Submitted'" + aq[0], aq[1]).fetchone()[0]
    return n


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

NAV_KEY = {"My": "timesheet", "Approvals": "approvals", "Missing": "missing", "Reopen": "reopen", "Reports": "reports", "CTR": "reports",
           "Admin": "admin", "Edit": "admin", "Audit": "audit"}


def layout(con, u, title, body):
    users = con.execute("select a.user_id, a.name, r.name as role_name, a.permission from app_user a "
                        "join role r on r.role_id=a.role_id order by a.name").fetchall()
    opts = "".join('<option value="%d"%s>%s - %s%s</option>' % (
        x["user_id"], " selected" if x["user_id"] == u else "", esc(x["name"]), esc(x["role_name"]),
        " (%s)" % x["permission"] if x["permission"] != "user" else "") for x in users)
    me = me_row(con, u)
    perm = me["permission"] if me else None
    n_back, n_open = con.execute("select sum(reject_reason is not null), sum(reopen_reason is not null) from time_entry "
                                 "where user_id=? and status='Draft'", (u,)).fetchone()
    note = ", ".join(x for x in ("%d sent back" % n_back if n_back else "", "%d reopened" % n_open if n_open else "") if x)
    items = [] if perm == "admin" else [("timesheet", "My Timesheet%s" % (" (%s)" % note if note else ""), "/timesheet?u=%d" % u)]  # the IT admin's account books no time
    if approval_queue(con, u) is not None:
        items.append(("approvals", "Approvals (%d)" % pending_for(con, u), "/approvals?u=%d" % u))
    if perm == "controller" or is_approver(con, u):  # who has not completed a timesheet: everyone for cost controllers, their own people for approvers
        items.append(("missing", "Missing timesheets", "/missing?u=%d" % u))
    if perm == "controller":  # reopening a person's submitted or approved timesheet
        items.append(("reopen", "Reopen timesheet", "/reopen?u=%d" % u))
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
def locations(con, active_only=True):
    """Where hours can be worked (On site, Offshore, ...): the hour types managed by the IT admin."""
    return [tuple(r) for r in con.execute("select location_id, name from location %s order by location_id" % ("where active=1" if active_only else ""))]


def parse_pairs(vals):
    """Allowance rows added but not saved yet, as (phase, allowance) pairs from 'phase:allowance' values."""
    out = []
    for v in vals:
        try:
            p, a = [int(x) for x in v.split(":")]
        except ValueError:
            continue
        out.append((p, a))
    return list(dict.fromkeys(out))


def allowance_queue(con, u):
    """The allowances (a, on scopes s) this person approves, as SQL and arguments, or None. Same rules as approval_queue for hours."""
    if permission_of(con, u) == "controller":
        return " and " + _only_approver("a"), ()
    if is_approver(con, u):
        return " and " + _may("a"), (u, u)
    return None


def may_action_allowance(con, u, aid):
    q = allowance_queue(con, u)
    if q is None:
        return False
    return con.execute("select 1 from allowance_entry a join scope s on s.scope_id=a.scope_id where a.allowance_entry_id=?" + q[0], (aid,) + tuple(q[1])).fetchone() is not None


def parse_adds(vals):
    """Rows added to the grid but not saved yet, as (phase, skill code, location, overtime). 'phase:role' alone means On site, normal time."""
    out = []
    for v in vals:
        try:
            p = [int(x) for x in v.split(":")]
        except ValueError:
            continue
        if len(p) == 2:
            p += [1, 0]
        if len(p) == 4:
            out.append(tuple(p))
    return out


TIMESHEET_JS = """<script>
// Keeps the row, day and week totals (normal time, overtime and total) in step with what is typed, before anything is saved.
function fmtH(x){return String(parseFloat(x.toFixed(2)));}
function fill(sel, m){var ds=document.querySelectorAll(sel), k; for(k=0;k<ds.length;k++){ds[k].firstChild.textContent=fmtH(m[ds[k].getAttribute('data-day')]||0);}}
function put(sel, v){var g=document.querySelector(sel); if(g){g.firstChild.textContent=fmtH(v);}}
function recalc(){
  var rows=document.querySelectorAll('tr.row'), dn={}, dO={}, dt={}, gn=0, go=0, i, j;
  for(i=0;i<rows.length;i++){
    var ot=rows[i].getAttribute('data-ot')==='1', cells=rows[i].querySelectorAll('td.c'), rt=0;
    for(j=0;j<cells.length;j++){
      var inp=cells[j].querySelector('input[type=text]');
      var v=parseFloat(inp?inp.value:cells[j].getAttribute('data-h'));
      if(isNaN(v)){v=0;}
      rt+=v; var d=cells[j].getAttribute('data-day');
      dt[d]=(dt[d]||0)+v;
      if(ot){dO[d]=(dO[d]||0)+v;}else{dn[d]=(dn[d]||0)+v;}
    }
    rows[i].querySelector('td.rt').textContent=fmtH(rt);
    if(ot){go+=rt;}else{gn+=rt;}
  }
  fill('td.dn', dn); fill('td.do', dO); fill('td.dt', dt);
  put('td.gn', gn); put('td.go', go); put('td.gt', gn+go);
}
document.addEventListener('input',function(e){if(e.target.name&&e.target.name.indexOf('h_')===0){recalc();}});
</script>"""


def view_timesheet(con, u, w, msgs=None, adds=None, ok=False, aadds=None):
    adds = adds or []
    aadds = aadds or []
    me = me_row(con, u)
    if not me:
        return layout(con, u, "My Timesheet", "<p>Unknown user.</p>")
    if me["permission"] == "admin":
        return layout(con, u, "My Timesheet", '<p>The IT admin account is a system account and does not book time. '
                      'Use <a href="/admin?u=%d">Admin</a> to manage users, job titles and locations.</p>' % u)
    days = [w + datetime.timedelta(days=i) for i in range(7)]
    d0, d6 = days[0].isoformat(), days[6].isoformat()
    period = con.execute("select * from period where week_start=?", (d0,)).fetchone()
    open_week = bool(period and period["status"] == "Open")
    start, end = me["start_date"], me["end_date"]  # hours can only be booked on dates from the start date to the end date

    def active(iso):
        return iso >= start and (end is None or iso <= end)

    def dmy(iso):
        return datetime.date.fromisoformat(iso).strftime("%d/%m/%Y")

    week_inactive = start > d6 or (end is not None and end < d0)
    entries = con.execute(
        "select t.*, s.scope_code, s.description, c.name as client, pr.name as project, (s.scope_code || '-' || coalesce(ph.ctr_code, upper(ph.name))) as phase, ro.skill_code, lo.name as location_name "
        "from time_entry t join phase ph on ph.phase_id=t.phase_id join scope s on s.scope_id=ph.scope_id "
        "join project pr on pr.project_id=s.project_id join client c on c.client_id=s.client_id "
        "join role ro on ro.role_id=t.worked_role_id join location lo on lo.location_id=t.location_id "
        "where t.user_id=? and t.work_date between ? and ? "
        "order by c.name, pr.name, s.scope_code, ph.name, ro.skill_code, t.location_id, t.overtime", (u, d0, d6)).fetchall()

    rows = {}
    for e in entries:
        k = (e["phase_id"], e["worked_role_id"], e["location_id"], e["overtime"])  # one row per phase, skill code, location and normal / overtime
        rows.setdefault(k, {"client": e["client"], "project": e["project"], "scope": "%s %s" % (e["scope_code"], e["description"]),
                            "phase": e["phase"], "skill": e["skill_code"], "location": e["location_name"], "overtime": e["overtime"],
                            "cells": {}})["cells"][e["work_date"]] = e
    for (pid, rid, lid, ot) in adds:
        if (pid, rid, lid, ot) in rows:
            continue
        s = con.execute("select s.*, c.name as client, pr.name as project, (s.scope_code || '-' || coalesce(ph.ctr_code, upper(ph.name))) as phase from phase ph "
                        "join scope s on s.scope_id=ph.scope_id join project pr on pr.project_id=s.project_id "
                        "join client c on c.client_id=s.client_id where ph.phase_id=?", (pid,)).fetchone()
        r = con.execute("select * from role where role_id=?", (rid,)).fetchone()
        loc = con.execute("select name from location where location_id=?", (lid,)).fetchone()
        if s and r and loc and ot in (0, 1):
            rows[(pid, rid, lid, ot)] = {"client": s["client"], "project": s["project"], "scope": "%s %s" % (s["scope_code"], s["description"]),
                                         "phase": s["phase"], "skill": r["skill_code"], "location": loc[0], "overtime": ot, "cells": {}}

    b = []
    b.append(msgs_html(msgs, ok))
    prev_w, next_w = (w - datetime.timedelta(days=7)).isoformat(), (w + datetime.timedelta(days=7)).isoformat()
    b.append('<p><a href="/timesheet?u=%d&amp;w=%s">&lt;&lt; Previous week</a> &nbsp; '
             "<b>Week commencing %s</b> &nbsp; "
             '<a href="/timesheet?u=%d&amp;w=%s">Next week &gt;&gt;</a> &nbsp; <a href="/timesheet?u=%d&amp;w=%s">This week</a></p>' % (
                 u, prev_w, w.strftime("%d/%m/%Y"), u, next_w, u, monday(datetime.date.today()).isoformat()))
    b.append('<form method="get" action="/timesheet"><input type="hidden" name="u" value="%d"><p>Go to the week containing %s <input type="submit" value="Go"></p></form>' % (
        u, date_input("w", d0)))
    st = {"Draft": 0.0, "Submitted": 0.0, "Approved": 0.0}
    for e in entries:
        st["Approved" if e["status"] in ("Approved", "Edited") else e["status"]] += e["hours"]
    if not entries:
        status_line = "Not started"
    elif st["Submitted"] == 0 and st["Approved"] == 0:
        status_line = "Draft: nothing submitted yet"
    elif st["Draft"]:
        status_line = "Partly submitted: %s hours still in draft" % fmt(st["Draft"])
    elif st["Submitted"]:
        status_line = "Submitted: %s hours waiting for approval, %s approved" % (fmt(st["Submitted"]), fmt(st["Approved"]))
    else:
        status_line = "Approved: all %s hours" % fmt(st["Approved"])
    b.append("<p><b>Week status:</b> %s</p>" % status_line)
    if not period:
        b.append("<p>This week has not been opened for timewriting. Ask an admin to open it.</p>")
    elif not open_week:
        b.append("<p><b>This week is closed.</b> New hours cannot be added. Approved hours can only be changed "
                 "with a reason (controlled edit).</p>")
    if week_inactive:
        b.append("<p><b>This week is outside your start and end dates</b> (%s), so no hours can be booked to it.</p>" % (
            "you start on %s" % dmy(start) if start > d6 else "you finished on %s" % dmy(end)))
    elif start > d0 or (end is not None and end < d6):
        b.append("<p><small>Hours can only be booked between your start and end dates (%s): days outside them show a dash.</small></p>" % (
            "from %s" % dmy(start) if start > d0 and (end is None or end >= d6) else
            "until %s" % dmy(end) if (start <= d0) else "from %s until %s" % (dmy(start), dmy(end))))

    def when(t):  # 2026-11-06 10:00:00 -> 06/11/2026 10:00
        return "%s/%s/%s %s" % (t[8:10], t[5:7], t[0:4], t[11:16]) if t and len(t) >= 16 else ""

    def banner(title, advice, found, reason_key, audit_sql, verb):
        groups = {}  # entries sent back or reopened together share one reason, so it is shown once
        for e in found:
            a = con.execute(audit_sql, (e["entry_id"],)).fetchone()
            by = " (%s by %s on %s)" % (verb, esc(a["who"] or "someone"), when(a["changed_at"])) if a else ""
            groups.setdefault((e[reason_key], by), []).append("%s %s, %s: %s hours (%s%s)" % (
                esc(e["scope_code"]), esc(e["phase"]), datetime.date.fromisoformat(e["work_date"]).strftime("%a %d/%m"), fmt(e["hours"]),
                esc(e["location_name"]), ", overtime" if e["overtime"] else ""))
        lines = ["<li><b>Reason:</b> %s%s<br>%s</li>" % (esc(reason), by, "; ".join(items)) for (reason, by), items in groups.items()]
        b.append('<div class="msg bad"><p><b>%s</b></p><ul>%s</ul><p>%s</p></div>' % (title, "".join(lines), advice))

    sent_back = [e for e in entries if e["status"] == "Draft" and e["reject_reason"]]
    if sent_back:
        banner("Sent back for correction", "Change the hours or the code if needed, then <b>Submit week</b> again.", sent_back, "reject_reason",
               "select ch.name as who, a.changed_at from time_entry_audit a left join app_user ch on ch.user_id=a.changed_by "
               "where a.entry_id=? and a.old_status='Submitted' and a.new_status='Draft' order by a.audit_id desc limit 1", "sent back")
    reopened = [e for e in entries if e["status"] == "Draft" and e["reopen_reason"]]
    if reopened:
        banner("Reopened by a cost controller", "Change what is needed, then <b>Submit week</b> again.", reopened, "reopen_reason",
               "select ch.name as who, a.changed_at from time_entry_audit a left join app_user ch on ch.user_id=a.changed_by "
               "where a.entry_id=? and a.new_status='Draft' and a.reason like 'Reopened:%' order by a.audit_id desc limit 1", "reopened")
    elsewhere = [r for r in con.execute(
        "select date(work_date,'weekday 0','-6 days') as wk, count(*) as n from time_entry "
        "where user_id=? and status='Draft' and (reject_reason is not null or reopen_reason is not null) group by wk order by wk", (u,)) if r["wk"] != d0]
    if elsewhere:
        b.append("<p><small>You also have hours sent back or reopened in: %s.</small></p>" % ", ".join(
            '<a href="/timesheet?u=%d&amp;w=%s">week commencing %s (%d)</a>' % (
                u, r["wk"], datetime.date.fromisoformat(r["wk"]).strftime("%d/%m/%Y"), r["n"]) for r in elsewhere))
    prev_d = w - datetime.timedelta(days=7)
    prev_n = con.execute("select count(*) from time_entry where user_id=? and work_date between ? and ?",
                         (u, prev_d.isoformat(), (prev_d + datetime.timedelta(days=6)).isoformat())).fetchone()[0]
    locked_n = sum(1 for e in entries if e["status"] != "Draft")
    if prev_n and open_week and not week_inactive and not locked_n:  # a week with submitted or approved hours in it cannot be replaced
        has_drafts = any(e["status"] == "Draft" for e in entries)
        b.append('<form method="post" action="/timesheet/copy"%s><input type="hidden" name="u" value="%d"><input type="hidden" name="w" value="%s">'
                 '<p>Last week (commencing %s) you booked %d entr%s. '
                 '<input type="submit" name="copy_hours" value="Copy last week"> '
                 '<small>This replaces whatever is in this week now with last week\'s rows and hours, saved as drafts for you to check.</small></p></form>' % (
                     ' onsubmit="return confirm(\'This clears the draft hours already in this week. Continue?\');"' if has_drafts else "",
                     u, d0, prev_d.strftime("%d/%m/%Y"), prev_n, "y" if prev_n == 1 else "ies"))

    adds_hidden = "".join('<input type="hidden" name="add" value="%d:%d:%d:%d">' % k for k in rows if not rows[k]["cells"])
    b.append('<form id="tsform" method="post" action="/timesheet/save">'
             '<input type="hidden" name="u" value="%d"><input type="hidden" name="w" value="%s">%s' % (u, d0, adds_hidden))
    head = ["Client", "Project", "Scope", "CTR", "Skill code", "Location", "Type"] + [d.strftime("%a %d/%m") for d in days] + ["Total"]
    trs = []
    day_tot = {d.isoformat(): 0.0 for d in days}
    day_norm, day_ot = dict(day_tot), dict(day_tot)  # the same, split into normal time and overtime
    for (pid, rid, lid, ot), r in rows.items():
        tds = ['<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td style="white-space:nowrap">%s</td><td style="white-space:nowrap">%s</td><td>%s</td>' % (
            esc(r["client"]), esc(r["project"]), esc(r["scope"]), esc(r["phase"]), esc(r["skill"]), esc(r["location"]),
            "<b>Overtime</b>" if ot else "Normal")]
        rt = 0.0
        for d in days:
            iso = d.isoformat()
            e = r["cells"].get(iso)
            name = "h_%d_%d_%d_%d_%s" % (pid, rid, lid, ot, iso)
            fixed = ""  # hours the person cannot type over (shown as text), so the live totals can still count them
            if e is None:
                cell = '<input type="text" size="4" name="%s" value="">' % name if open_week and active(iso) else ("&ndash;" if open_week else "&nbsp;")
            else:
                rt += e["hours"]
                day_tot[iso] += e["hours"]
                (day_ot if ot else day_norm)[iso] += e["hours"]
                if e["status"] != "Draft" or not open_week:  # submitted, approved and edited hours are locked until a cost controller reopens them
                    cell = fmt(e["hours"])
                    fixed = ' data-h="%s"' % fmt(e["hours"])
                else:
                    why = (' title="Sent back: %s"' % esc(e["reject_reason"]) if e["status"] == "Draft" and e["reject_reason"] else
                           ' title="Reopened: %s"' % esc(e["reopen_reason"]) if e["status"] == "Draft" and e["reopen_reason"] else "")
                    cell = '<input type="text" size="4" name="%s" value="%s"%s>' % (name, fmt(e["hours"]), why)
            tds.append('<td align="center" class="c" data-day="%s"%s>%s</td>' % (iso, fixed, cell))
        tds.append('<td align="right" class="rt">%s</td>' % fmt(rt))
        trs.append('<tr class="row" data-ot="%d">%s</tr>' % (ot, "".join(tds)))
    if not trs:
        trs.append('<tr><td colspan="%d">No rows yet. Add a row below.</td></tr>' % len(head))
    def sum_row(label, day_cls, week_cls, per_day):  # Normal time, Overtime and Total lines under the grid
        return '<tr><td colspan="7" align="right"><b>%s</b></td>%s<td align="right" class="%s"><b>%s</b></td></tr>' % (
            label, "".join('<td align="center" class="%s" data-day="%s"><b>%s</b></td>' % (day_cls, d.isoformat(), fmt(per_day[d.isoformat()])) for d in days),
            week_cls, fmt(sum(per_day.values())))
    foot = sum_row("Normal time", "dn", "gn", day_norm) + sum_row("Overtime", "do", "go", day_ot) + sum_row("Total", "dt", "gt", day_tot)
    b.append('<table border="1" cellpadding="3" cellspacing="0"><tr>%s</tr>%s%s</table>' % (
        "".join("<th>%s</th>" % esc(h) for h in head), "".join(trs), foot))
    b.append(TIMESHEET_JS)
    by_status = {"Approved": 0.0, "Edited": 0.0, "Submitted": 0.0, "Draft": 0.0}
    for e in entries:
        by_status[e["status"]] += e["hours"]
    b.append("<p><b>Hours this week (all statuses): %s</b> &nbsp; Approved %s, Edited %s, Submitted %s, Draft %s</p>" % (
        fmt(sum(by_status.values())), fmt(by_status["Approved"]), fmt(by_status["Edited"]), fmt(by_status["Submitted"]), fmt(by_status["Draft"])))
    b.append("<p><small>To clear a draft entry, empty its box and click Save draft.</small></p>")
    # allowances: a row per booking code and allowance, a tick box per day
    arows = {}
    for a in con.execute(
            "select a.*, at.name as allowance, s.scope_code, s.description, c.name as client, pr.name as project, (s.scope_code || '-' || coalesce(ph.ctr_code, upper(ph.name))) as phase "
            "from allowance_entry a join allowance_type at on at.allowance_id=a.allowance_id join phase ph on ph.phase_id=a.phase_id "
            "join scope s on s.scope_id=a.scope_id join project pr on pr.project_id=s.project_id join client c on c.client_id=s.client_id "
            "where a.user_id=? and a.work_date between ? and ? order by c.name, pr.name, s.scope_code, ph.name, a.allowance_id", (u, d0, d6)):
        arows.setdefault((a["phase_id"], a["allowance_id"]), {"client": a["client"], "project": a["project"], "scope": "%s %s" % (a["scope_code"], a["description"]),
                                                              "phase": a["phase"], "allowance": a["allowance"], "cells": {}})["cells"][a["work_date"]] = a
    for (pid, aid) in aadds:
        if (pid, aid) in arows:
            continue
        i = con.execute("select s.scope_code, s.description, c.name as client, pr.name as project, (s.scope_code || '-' || coalesce(ph.ctr_code, upper(ph.name))) as phase, at.name as allowance "
                        "from phase ph join scope s on s.scope_id=ph.scope_id join project pr on pr.project_id=s.project_id "
                        "join client c on c.client_id=s.client_id, allowance_type at where ph.phase_id=? and at.allowance_id=? and at.active=1", (pid, aid)).fetchone()
        if i:
            arows[(pid, aid)] = {"client": i["client"], "project": i["project"], "scope": "%s %s" % (i["scope_code"], i["description"]),
                                 "phase": i["phase"], "allowance": i["allowance"], "cells": {}}
    atypes = con.execute("select allowance_id, name from allowance_type where active=1 order by allowance_id").fetchall() if me["employment_type"] == "Staff" else []
    if arows:
        b.append("<p><b>Allowances</b> <small>Tick the days you are claiming. One of each allowance per day. They are submitted and approved with your hours.</small></p>")
    if arows:
        ahead = ["Client", "Project", "Scope", "CTR", "Allowance"] + [d.strftime("%a %d/%m") for d in days] + ["Days"]
        atrs = []
        for (pid, aid), r in arows.items():
            tds = ['<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td style="white-space:nowrap">%s</td>' % (
                esc(r["client"]), esc(r["project"]), esc(r["scope"]), esc(r["phase"]), esc(r["allowance"]))]
            n_days = 0
            for d in days:
                iso = d.isoformat()
                a = r["cells"].get(iso)
                if a is not None:
                    n_days += 1
                if a is not None and a["status"] != "Draft":
                    cell = '<span title="%s">&#10003;</span>' % esc(a["status"])
                elif open_week and active(iso):
                    cell = ('<input type="checkbox" name="a_%d_%d_%s" value="1"%s><input type="hidden" name="ashown" value="%d:%d:%s">' % (
                        pid, aid, iso, " checked" if a is not None else "", pid, aid, iso))
                else:
                    cell = "&#10003;" if a is not None else "&ndash;"
                tds.append('<td align="center">%s</td>' % cell)
            tds.append('<td align="right">%d<input type="hidden" name="arow" value="%d:%d"></td>' % (n_days, pid, aid))
            atrs.append("<tr>%s</tr>" % "".join(tds))
        b.append('<table border="1" cellpadding="3" cellspacing="0"><tr>%s</tr>%s</table>' % ("".join("<th>%s</th>" % esc(h) for h in ahead), "".join(atrs)))
    if any(e["status"] != "Draft" for e in entries) or any(a["status"] != "Draft" for r in arows.values() for a in r["cells"].values()):
        b.append("<p><small>Submitted and approved hours are locked, and so are submitted and approved allowances. If one needs changing, ask a cost controller to reopen the timesheet.</small></p>")
    b.append('<p><input type="submit" name="action" value="Save draft"> '
             '<input type="submit" name="action" value="Submit week"></p></form>')

    # add a row
    scopes = con.execute(
        "select ph.phase_id, (s.scope_code || '-' || coalesce(ph.ctr_code, upper(ph.name))) as phase, ph.name as phase_name, s.scope_code, s.description, pr.name as project, c.name as client, c.is_internal "
        "from phase ph join scope s on s.scope_id=ph.scope_id join project pr on pr.project_id=s.project_id "
        "join client c on c.client_id=s.client_id left join paaf p on p.client_id=s.client_id and p.user_id=? "
        "and p.status='Approved' and (p.expiry_date is null or p.expiry_date >= ?) "
        "where s.status='Open' and ph.status='Open' and pr.status='Open' "
        "and (c.is_internal=1 or p.paaf_id is not null) "  # client work needs an approved PAAF, internal codes are open to everyone
        "and (ph.restricted=0 or exists (select 1 from phase_person pp where pp.phase_id=ph.phase_id and pp.user_id=?)) "  # transaction controls:
        "and (ph.book_to is null or ph.book_to >= ?) and (ph.book_from is null or ph.book_from <= ?) "  # people, and booking dates
        "order by c.is_internal, c.name, pr.name, s.scope_code, ph.name", (u, d0, u, d0, d6)).fetchall()
    roles = con.execute("select role_id, skill_code, name from role where active=1 order by skill_code").fetchall()
    for pf in con.execute("select c.name as client, p.expiry_date from paaf p join client c on c.client_id=p.client_id "
                          "where p.user_id=? and c.is_internal=0 and p.expiry_date is not null and p.expiry_date < ? order by c.name", (u, d0)):
        b.append("<p><small><b>%s:</b> you came off this client's list on %s, so its CTRs are not offered from that date.</small></p>" % (esc(pf["client"]), dmy(pf["expiry_date"])))
    if week_inactive:
        pass  # the note above says why nothing can be added
    elif scopes:
        def label(s):
            if s["is_internal"]:  # e.g. "Internal - Business Services"
                return "%s - %s%s" % (esc(s["client"]), esc(s["description"]), "" if s["phase_name"] == "General" else " &gt; %s" % esc(s["phase"]))
            return "%s &gt; %s &gt; %s %s &gt; %s" % (esc(s["client"]), esc(s["project"]), esc(s["scope_code"]), esc(s["description"]), esc(s["phase"]))
        so = "".join('<option value="%d">%s</option>' % (s["phase_id"], label(s)) for s in scopes)
        ro = "".join('<option value="%d"%s>%s %s</option>' % (r["role_id"], " selected" if r["role_id"] == me["role_id"] else "", esc(r["skill_code"]), esc(r["name"])) for r in roles)
        keep = "".join('<input type="hidden" name="add" value="%d:%d:%d:%d">' % k for k in rows if not rows[k]["cells"])
        akeep = "".join('<input type="hidden" name="aadd" value="%d:%d">' % k for k in arows if not arows[k]["cells"])
        lo = "".join('<option value="%d">%s</option>' % (lid, esc(name)) for lid, name in locations(con))
        b.append('<p><b>Add row:</b> CTR <select form="tsform" name="add_phase">%s</select> '
                 'Skill code <select form="tsform" name="add_role">%s</select> Location <select form="tsform" name="add_loc">%s</select> '
                 '<label><input form="tsform" type="checkbox" name="add_ot" value="1"> Overtime</label> '
                 '<input form="tsform" type="submit" name="action" value="Add row"> <small>(hours you have typed are saved first)</small></p>'
                 "<p><small>Your own skill code is pre-selected. Choose a different one if you are covering another role. "
                 "Hours NMC pays for itself (not billed to a client) go to an Internal code, for example Internal - Business Services. "
                 "Tick Overtime for overtime hours: they go on their own line and have no separate rate yet.</small></p>" % (so, ro, lo))
        if atypes:
            ao = "".join('<option value="%d">%s</option>' % (t["allowance_id"], esc(t["name"])) for t in atypes)
            b.append('<p><b>Add allowance:</b> Booking code <select form="tsform" name="aadd_phase">%s</select> Allowance <select form="tsform" name="aadd_type">%s</select> '
                     '<input form="tsform" type="submit" name="action" value="Add allowance row"></p>'
                     "<p><small>An allowance is claimed against a booking code, so the code's approver approves it.</small></p>" % (so, ao))
    else:
        b.append("<p>You are not on the personnel list of any client with open scopes, so there is nothing to book to.</p>")
    return layout(con, u, "My Timesheet", "".join(b))


def book_entry(con, u, pid, rid, lid, ot, date, hours):
    """Create one Draft entry for u. Returns None when it is booked, or a short reason when it cannot be (no rate, no PAAF, location
    switched off). The database rules (open scope, start and end dates, PAAF expiry, open week) raise sqlite3.DatabaseError instead."""
    info = con.execute("select s.scope_id, s.client_id, c.is_internal from phase ph join scope s on s.scope_id=ph.scope_id "
                       "join client c on c.client_id=s.client_id where ph.phase_id=?", (pid,)).fetchone()
    if not info:
        return "that booking code does not exist."
    loc = con.execute("select active from location where location_id=?", (lid,)).fetchone()
    if not loc or not loc[0] or ot not in (0, 1):
        return "that location is not available."
    if info["is_internal"]:  # NMC pays for these hours: no PAAF, no sales rate
        paaf_id = rate_id = None
    else:
        paaf = con.execute("select p.paaf_id, r.role_id as position from paaf p join rate r on r.rate_id=p.rate_id "
                           "where p.user_id=? and p.client_id=?", (u, info["client_id"])).fetchone()
        if not paaf:
            return "you are not on this client's personnel list."
        # Booking as their own role uses the position assigned on their PAAF; covering another role uses that role's line of the rate card.
        # Either way the rate is the one in force on the work date (rates are never edited, a new one is added with a start date).
        own = con.execute("select 1 from app_user where user_id=? and role_id=?", (u, rid)).fetchone()
        rate = con.execute("select rate_id from rate where client_id=? and role_id=? and effective_from<=? "
                           "order by effective_from desc limit 1", (info["client_id"], paaf["position"] if own else rid, date)).fetchone()
        if not rate:
            return "no rate for that skill code on this client."
        rate_id, paaf_id = rate[0], paaf["paaf_id"]
    con.execute("insert into time_entry (user_id, scope_id, phase_id, paaf_id, worked_role_id, rate_id, work_date, hours, "
                "status, created_by, location_id, overtime) values (?,?,?,?,?,?,?,?, 'Draft', ?, ?, ?)",
                (u, info["scope_id"], pid, paaf_id, rid, rate_id, date, hours, u, lid, ot))
    return None


def save_timesheet(con, form):
    u, w = int(form["u"][0]), datetime.date.fromisoformat(form["w"][0])
    action = form.get("action", ["Save draft"])[0]
    adds = parse_adds(form.get("add", []))
    msgs, saved, cleared = [], 0, 0
    d0, d6 = w.isoformat(), (w + datetime.timedelta(days=6)).isoformat()
    for key, vals in form.items():
        if not key.startswith("h_"):
            continue
        try:
            _, pid, rid, lid, ot, date = key.split("_", 5)
            pid, rid, lid, ot = int(pid), int(rid), int(lid), int(ot)
        except ValueError:
            continue
        raw = vals[0].strip()
        info = con.execute("select s.scope_id, s.scope_code, (s.scope_code || '-' || coalesce(ph.ctr_code, upper(ph.name))) as phase from phase ph join scope s on s.scope_id=ph.scope_id "
                           "where ph.phase_id=?", (pid,)).fetchone()
        if not info:
            continue
        label = "%s %s, %s" % (info["scope_code"], info["phase"], date)
        existing = con.execute("select * from time_entry where user_id=? and phase_id=? and worked_role_id=? and work_date=? "
                               "and location_id=? and overtime=?", (u, pid, rid, date, lid, ot)).fetchone()
        if raw == "":
            if existing and existing["status"] == "Draft":
                try:
                    con.execute("update time_entry set last_edited_by=?, last_edited_at=? where entry_id=?",
                                (u, stamp(), existing["entry_id"]))
                    con.execute("delete from time_entry where entry_id=?", (existing["entry_id"],))
                    cleared += 1
                except sqlite3.DatabaseError as e:
                    msgs.append("%s: %s" % (label, e))
            elif existing:
                msgs.append("%s: %s hours are locked. Ask a cost controller to reopen the timesheet." % (label, existing["status"].lower()))
            continue
        try:
            hours = float(raw)
        except ValueError:
            msgs.append("%s: '%s' is not a number." % (label, raw))
            continue
        try:
            if existing:
                if abs(existing["hours"] - hours) < 1e-9:
                    continue
                if existing["status"] != "Draft":
                    msgs.append("%s: %s hours are locked. Ask a cost controller to reopen the timesheet." % (label, existing["status"].lower()))
                    continue
                con.execute("update time_entry set hours=?, last_edited_by=?, last_edited_at=? where entry_id=?",
                            (hours, u, stamp(), existing["entry_id"]))
            else:
                problem = book_entry(con, u, pid, rid, lid, ot, date, hours)
                if problem:
                    msgs.append("%s: %s" % (label, problem))
                    continue
            saved += 1
        except sqlite3.DatabaseError as e:
            msgs.append("%s: %s" % (label, e))
    # allowances: only the tick boxes that were on the page ('ashown') are looked at, so locked days are never touched
    aadds = parse_pairs(form.get("arow", []))
    if action == "Add row" and form.get("add_phase") and form.get("add_role"):  # saved above; now show the new empty row
        adds += parse_adds(["%s:%s:%s:%s" % (form["add_phase"][0], form["add_role"][0], form.get("add_loc", ["1"])[0], "1" if "add_ot" in form else "0")])
    if action == "Add allowance row" and form.get("aadd_phase") and form.get("aadd_type"):
        aadds = list(dict.fromkeys(aadds + parse_pairs(["%s:%s" % (form["aadd_phase"][0], form["aadd_type"][0])])))
    a_saved = a_cleared = 0
    for shown in form.get("ashown", []):
        try:
            pid, aid, date = shown.split(":")
            pid, aid = int(pid), int(aid)
            datetime.date.fromisoformat(date)
        except ValueError:
            continue
        if not d0 <= date <= d6:
            continue
        checked = "a_%d_%d_%s" % (pid, aid, date) in form
        info = con.execute("select s.scope_id, s.scope_code, (s.scope_code || '-' || coalesce(ph.ctr_code, upper(ph.name))) as phase, at.name as allowance from phase ph join scope s on s.scope_id=ph.scope_id, "
                           "allowance_type at where ph.phase_id=? and at.allowance_id=?", (pid, aid)).fetchone()
        if not info:
            continue
        label = "%s on %s (%s %s)" % (info["allowance"], date, info["scope_code"], info["phase"])
        existing = con.execute("select * from allowance_entry where user_id=? and allowance_id=? and work_date=?", (u, aid, date)).fetchone()
        try:
            if checked and not existing:
                con.execute("insert into allowance_entry (user_id, scope_id, phase_id, allowance_id, work_date, created_by, last_edited_by, last_edited_at) "
                            "values (?,?,?,?,?,?,?,?)", (u, info["scope_id"], pid, aid, date, u, u, stamp()))
                a_saved += 1
            elif checked and existing and existing["phase_id"] != pid:
                msgs.append("%s: already claimed against another booking code that day." % label)
            elif not checked and existing and existing["phase_id"] == pid and existing["status"] == "Draft":
                con.execute("update allowance_entry set last_edited_by=?, last_edited_at=? where allowance_entry_id=?", (u, stamp(), existing["allowance_entry_id"]))
                con.execute("delete from allowance_entry where allowance_entry_id=?", (existing["allowance_entry_id"],))
                a_cleared += 1
        except sqlite3.DatabaseError as e:
            msgs.append("%s: %s" % (label, e))
    ok_msgs = []
    if saved:
        ok_msgs.append("%d entr%s saved." % (saved, "y" if saved == 1 else "ies"))
    if cleared:
        ok_msgs.append("%d draft entr%s cleared." % (cleared, "y" if cleared == 1 else "ies"))
    if a_saved:
        ok_msgs.append("%d allowance day%s saved." % (a_saved, "" if a_saved == 1 else "s"))
    if a_cleared:
        ok_msgs.append("%d allowance day%s cleared." % (a_cleared, "" if a_cleared == 1 else "s"))
    if action == "Submit week":
        try:
            n = con.execute("update time_entry set status='Submitted', last_edited_by=?, last_edited_at=? "
                            "where user_id=? and status='Draft' and work_date between ? and ?", (u, stamp(), u, d0, d6)).rowcount
            na_before = None
            na = con.execute("update allowance_entry set status='Submitted', last_edited_by=?, last_edited_at=? "
                             "where user_id=? and status='Draft' and work_date between ? and ?", (u, stamp(), u, d0, d6)).rowcount
            if n:
                ok_msgs.append("%d entr%s submitted for approval." % (n, "y" if n == 1 else "ies"))
            if na:
                ok_msgs.append("%d allowance day%s submitted for approval." % (na, "" if na == 1 else "s"))
            if not n and not na:
                msgs.append("Nothing to submit: there are no draft hours or allowances in this week.")
        except sqlite3.DatabaseError as e:
            msgs.append("Submit failed: %s" % e)
    con.commit()
    body_msgs = ok_msgs if not msgs else msgs + ok_msgs
    return view_timesheet(con, u, w, body_msgs, adds, ok=not msgs, aadds=aadds)


def copy_week(con, form):
    """Replace the week shown with last week's rows and hours on the same weekdays, saved as drafts. The draft hours already in the week
    are cleared first. A week that has submitted or approved hours cannot be replaced, and what cannot be copied is reported."""
    u, w = int(form["u"][0]), datetime.date.fromisoformat(form["w"][0])
    prev = w - datetime.timedelta(days=7)
    src = con.execute("select * from time_entry where user_id=? and work_date between ? and ? order by work_date, entry_id",
                      (u, prev.isoformat(), (prev + datetime.timedelta(days=6)).isoformat())).fetchall()
    since = prev.strftime("%d/%m/%Y")
    if not src:
        return view_timesheet(con, u, w, ["Nothing to copy: you have no hours in the week commencing %s." % since])
    d0, d6 = w.isoformat(), (w + datetime.timedelta(days=6)).isoformat()
    if con.execute("select 1 from time_entry where user_id=? and status<>'Draft' and work_date between ? and ?", (u, d0, d6)).fetchone():
        return view_timesheet(con, u, w, ["This week has submitted or approved hours, so it cannot be replaced. "
                                          "Ask a cost controller to reopen the timesheet first."])
    cleared = 0
    for e in con.execute("select entry_id from time_entry where user_id=? and status='Draft' and work_date between ? and ?", (u, d0, d6)).fetchall():
        con.execute("update time_entry set last_edited_by=?, last_edited_at=? where entry_id=?", (u, stamp(), e["entry_id"]))
        con.execute("delete from time_entry where entry_id=?", (e["entry_id"],))
        cleared += 1
    copied = 0
    problems = {}
    for e in src:
        date = (datetime.date.fromisoformat(e["work_date"]) + datetime.timedelta(days=7)).isoformat()
        try:
            problem = book_entry(con, u, e["phase_id"], e["worked_role_id"], e["location_id"], e["overtime"], date, e["hours"])
        except sqlite3.DatabaseError as ex:
            problem = str(ex)
        if problem:
            problems[problem] = problems.get(problem, 0) + 1
        else:
            copied += 1
    con.commit()
    msgs = ["%d entr%s could not be copied: %s" % (n, "y" if n == 1 else "ies", reason) for reason, n in problems.items()]
    if copied:
        msgs.append("%d entr%s copied from the week commencing %s as drafts. Check the hours, then Submit week." % (
            copied, "y" if copied == 1 else "ies", since))
    if cleared:
        msgs.append("%d draft entr%s that w%s in this week %s replaced." % (
            cleared, "y" if cleared == 1 else "ies", "as" if cleared == 1 else "ere", "was" if cleared == 1 else "were"))
    return view_timesheet(con, u, w, msgs, ok=not problems)


# ---------- Approvals ----------
APPROVALS_JS = """<script>
// Detail rows start hidden (they stay visible without scripting); a link per person opens them, and a person's tick box ticks their entries.
(function(){
  var d=document.querySelectorAll('tr.drow'), t=document.querySelectorAll('.js'), i;
  for(i=0;i<d.length;i++){d[i].style.display='none';}
  for(i=0;i<t.length;i++){t[i].style.display='';}
})();
function openRow(a){var r=document.getElementById(a.getAttribute('data-r')); r.style.display=(r.style.display==='none')?'':'none'; return false;}
function openAll(show){var d=document.querySelectorAll('tr.drow'), i; for(i=0;i<d.length;i++){d[i].style.display=show?'':'none';} return false;}
function tickPerson(c){var e=document.querySelectorAll('input[data-p="'+c.getAttribute('data-p')+'"]'), i; for(i=0;i<e.length;i++){e[i].checked=c.checked;}}
function tickAll(c){var e=document.getElementsByName('entry_id'), g=document.getElementsByName('allowance_entry_id'), p=document.querySelectorAll('input.pp'), i;
  for(i=0;i<e.length;i++){e[i].checked=c.checked;} for(i=0;i<g.length;i++){g[i].checked=c.checked;} for(i=0;i<p.length;i++){p[i].checked=c.checked;}}
</script>"""


def view_approvals(con, u, msgs=None, ok=False, q=None):
    """The approver's week: one line per person with each weekday, a way to open a line to see everything booked against them, and
    previous and next weeks (approved hours stay visible). Cost controllers see the hours booked by approvers to the scopes they approve."""
    queue = approval_queue(con, u)
    if queue is None:
        return layout(con, u, "Approvals", msgs_html(msgs, ok) + "<p>Hours are approved by the approver named on each booking code. "
                      "You are not named as an approver on any code, so there is nothing for you to approve.</p>")
    where, wargs = queue
    q = q or {}
    aq = allowance_queue(con, u)
    waiting = con.execute(
        "select wk, sum(n) as n from (select date(t.work_date,'weekday 0','-6 days') as wk, count(*) as n from time_entry t join scope s on s.scope_id=t.scope_id "
        "where t.status in ('Submitted','Edited')" + where + " group by wk "
        "union all select date(a.work_date,'weekday 0','-6 days') as wk, count(*) as n from allowance_entry a join scope s on s.scope_id=a.scope_id "
        "where a.status='Submitted'" + aq[0] + " group by wk) group by wk order by wk", list(wargs) + list(aq[1])).fetchall()
    try:
        w = monday(datetime.date.fromisoformat(q["w"][0]))
    except (KeyError, ValueError, IndexError):
        w = monday(datetime.date.fromisoformat(waiting[0]["wk"])) if waiting else monday(datetime.date.today())  # the oldest week still waiting
    d0 = w.isoformat()
    days = [w + datetime.timedelta(days=i) for i in range(7)]
    entries = con.execute(
        "select t.entry_id, t.user_id, bu.name as booked_by, c.name as client, pr.name as project, s.scope_code, (s.scope_code || '-' || coalesce(ph.ctr_code, upper(ph.name))) as phase, ro.skill_code, "
        "lo.name as location, t.overtime, t.work_date, t.hours, t.status, t.last_change_reason, "
        "(select old_hours from time_entry_audit a where a.entry_id=t.entry_id and a.new_status='Edited' order by a.audit_id desc limit 1) as was "
        "from time_entry t join scope s on s.scope_id=t.scope_id join phase ph on ph.phase_id=t.phase_id "
        "join project pr on pr.project_id=s.project_id join client c on c.client_id=s.client_id "
        "join app_user bu on bu.user_id=t.user_id join role ro on ro.role_id=t.worked_role_id join location lo on lo.location_id=t.location_id "
        "where t.status<>'Draft' and t.work_date between ? and ?" + where + " order by bu.name, t.work_date, s.scope_code, ph.name, ro.skill_code",
        [d0, days[-1].isoformat()] + list(wargs)).fetchall()
    people = {}
    for e in entries:
        people.setdefault(e["user_id"], []).append(e)
    allowances = con.execute(
        "select a.allowance_entry_id, a.user_id, bu.name as booked_by, at.name as allowance, a.work_date, a.status, c.name as client, s.scope_code, (s.scope_code || '-' || coalesce(ph.ctr_code, upper(ph.name))) as phase "
        "from allowance_entry a join scope s on s.scope_id=a.scope_id join phase ph on ph.phase_id=a.phase_id join client c on c.client_id=s.client_id "
        "join allowance_type at on at.allowance_id=a.allowance_id join app_user bu on bu.user_id=a.user_id "
        "where a.status<>'Draft' and a.work_date between ? and ?" + aq[0] + " order by bu.name, a.work_date, a.allowance_id",
        [d0, days[-1].isoformat()] + list(aq[1])).fetchall()

    def link(wk, label):
        return '<a href="/approvals?u=%d&amp;w=%s">%s</a>' % (u, wk.isoformat(), label)

    body = msgs_html(msgs, ok)
    if permission_of(con, u) == "controller":
        body += ("<p><small>These are hours booked by approvers to the scopes they approve. Nobody else may approve them "
                 "(not even the approver, who never approves their own hours), so a cost controller does.</small></p>")
    n_wait = sum(r["n"] for r in waiting)
    body += '<p>%s &nbsp; <b>Week commencing %s</b> &nbsp; %s &nbsp; %s</p>' % (
        link(w - datetime.timedelta(days=7), "&lt;&lt; Previous week"), w.strftime("%d/%m/%Y"),
        link(w + datetime.timedelta(days=7), "Next week &gt;&gt;"), link(monday(datetime.date.today()), "This week"))
    body += '<form method="get" action="/approvals">%s<p>Week containing %s <input type="submit" value="Show"></p></form>' % (hidden(u=u), date_input("w", d0))
    if n_wait:
        body += "<p><b>%d entr%s waiting for your approval</b>: %s.</p>" % (n_wait, "y" if n_wait == 1 else "ies", ", ".join(
            link(datetime.date.fromisoformat(r["wk"]), "%s (%d)" % (datetime.date.fromisoformat(r["wk"]).strftime("%d/%m/%Y"), r["n"])) for r in waiting))
    else:
        body += "<p>Nothing waiting for your approval.</p>"
    if not people and not allowances:
        return layout(con, u, "Approvals", body + "<p>No submitted or approved hours in this week from the people you approve.</p>")

    head = ('<tr><th><input type="checkbox" title="Select all" onclick="tickAll(this)"></th><th>Person</th>%s<th>Total</th><th>Waiting</th><th>Approved</th></tr>' %
            "".join("<th>%s</th>" % d.strftime("%a %d/%m") for d in days))
    ncol = 2 + 7 + 3
    day_tot = {d.isoformat(): 0.0 for d in days}
    trs = []
    for uid, es in people.items():
        per_day = {d.isoformat(): 0.0 for d in days}
        wait_h = appr_h = 0.0
        n_pending = 0
        for e in es:
            per_day[e["work_date"]] += e["hours"]
            day_tot[e["work_date"]] += e["hours"]
            if e["status"] in ("Submitted", "Edited"):
                wait_h += e["hours"]
                n_pending += 1
            else:
                appr_h += e["hours"]
        drow = "d%d" % uid
        trs.append('<tr><td align="center">%s</td><td><b>%s</b> <a href="#" class="js" style="display:none" data-r="%s" onclick="return openRow(this)">detail</a> %s</td>%s'
                   '<td align="right"><b>%s</b></td><td align="right">%s</td><td align="right">%s</td></tr>' % (
                       '<input type="checkbox" class="pp js" style="display:none" data-p="%d" onclick="tickPerson(this)">' % uid if n_pending else "",
                       esc(es[0]["booked_by"]), drow,
                       ('<button type="submit" name="approve_person" value="%d" title="Approve everything waiting for this person this week">Approve all waiting</button>' % uid) if n_pending else "",
                       "".join('<td align="center">%s</td>' % (fmt(per_day[d.isoformat()]) if per_day[d.isoformat()] else "&ndash;") for d in days),
                       fmt(wait_h + appr_h), fmt(wait_h) if wait_h else "", fmt(appr_h) if appr_h else ""))
        lines = []
        for e in es:
            pend = e["status"] in ("Submitted", "Edited")
            lines.append("<tr><td align=\"center\">%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                         "<td align=\"right\">%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                             '<input type="checkbox" name="entry_id" value="%d" data-p="%d">' % (e["entry_id"], uid) if pend else "",
                             esc(e["client"]), esc(e["project"]), esc(e["scope_code"]), esc(e["phase"]), esc(e["skill_code"]),
                             esc(e["location"]), "<b>Overtime</b>" if e["overtime"] else "Normal", esc(e["work_date"]),
                             fmt(e["hours"]), esc(e["status"]), esc(fmt(e["was"]) if e["was"] is not None else ""), esc(e["last_change_reason"] or "")))
        trs.append('<tr class="drow" id="%s"><td></td><td colspan="%d"><table border="1" cellpadding="3" cellspacing="0">'
                   "<tr><th></th><th>Client</th><th>Project</th><th>Scope</th><th>CTR</th><th>Skill code</th><th>Location</th><th>Type</th>"
                   "<th>Date</th><th>Hours</th><th>Status</th><th>Was</th><th>Edit reason</th></tr>%s</table></td></tr>" % (drow, ncol - 2, "".join(lines)))
    foot = ('<tr><td></td><td align="right"><b>All people</b></td>%s<td align="right"><b>%s</b></td><td></td><td></td></tr>' % (
        "".join('<td align="center"><b>%s</b></td>' % (fmt(day_tot[d.isoformat()]) if day_tot[d.isoformat()] else "&ndash;") for d in days), fmt(sum(day_tot.values()))))
    allow_html = ""
    if allowances:
        allow_html = ('<p><b>Allowances</b> <small>Claims for the offshore allowance, approved or sent back in the same way as hours.</small></p>'
                      '<table border="1" cellpadding="3" cellspacing="0"><tr><th></th><th>Person</th><th>Allowance</th><th>Date</th><th>Client</th><th>Scope</th><th>CTR</th><th>Status</th></tr>%s</table>' % "".join(
                          '<tr><td align="center">%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
                              '<input type="checkbox" name="allowance_entry_id" value="%d">' % a["allowance_entry_id"] if a["status"] == "Submitted" else "",
                              esc(a["booked_by"]), esc(a["allowance"]), esc(a["work_date"]), esc(a["client"]), esc(a["scope_code"]), esc(a["phase"]), esc(a["status"]))
                          for a in allowances))
    body += ('<form method="post" action="/approvals/act">%s<p><small>One line per person, with the submitted and approved hours for each day. '
             '<a href="#" class="js" style="display:none" onclick="return openAll(true)">Show all detail</a>'
             '<span class="js" style="display:none"> | </span>'
             '<a href="#" class="js" style="display:none" onclick="return openAll(false)">Hide all detail</a>. '
             'Open a line to see everything booked against that person. Tick the boxes (a person, or single entries in their detail) to action them, '
             'or tick the box in the header to select all and then untick any you have an issue with. Entries left unticked stay in your queue. '
             'Use Previous week to see earlier approved hours.</small></p>'
             '%s%s%s'
             '<p><input type="submit" name="action" value="Approve selected"></p>'
             '<p>Reason (needed to reject) %s <input type="submit" name="action" value="Reject selected"> '
             '<small>Rejected entries go back to the person as drafts. Edited entries can only be approved.</small></p></form>'
             % (hidden(u=u, w=d0), ('<table border="1" cellpadding="3" cellspacing="0">%s%s%s</table>' % (head, "".join(trs), foot)) if people else "",
                allow_html, APPROVALS_JS, txt("reason", "", 40)))
    return layout(con, u, "Approvals", body)


def act_approval(con, form):
    u = int(form["u"][0])
    ids = [int(x) for x in form.get("entry_id", [])]
    aids = [int(x) for x in form.get("allowance_entry_id", [])]
    if form.get("approve_person"):  # the Approve all button on a person's line: everything of theirs waiting this week, no ticking
        try:
            who, wk = int(form["approve_person"][0]), monday(datetime.date.fromisoformat(form["w"][0]))
        except (ValueError, KeyError):
            who = None
        if who:
            end = (wk + datetime.timedelta(days=6)).isoformat()
            hq, aq_ = approval_queue(con, u), allowance_queue(con, u)
            if hq:
                ids = [r[0] for r in con.execute("select t.entry_id from time_entry t join scope s on s.scope_id=t.scope_id where t.status in ('Submitted','Edited') "
                                                 "and t.user_id=? and t.work_date between ? and ?" + hq[0], [who, wk.isoformat(), end] + list(hq[1]))]
            if aq_:
                aids = [r[0] for r in con.execute("select a.allowance_entry_id from allowance_entry a join scope s on s.scope_id=a.scope_id where a.status='Submitted' "
                                                  "and a.user_id=? and a.work_date between ? and ?" + aq_[0], [who, wk.isoformat(), end] + list(aq_[1]))]
        form = dict(form, action=["Approve selected"])
    action, reason = form["action"][0], form.get("reason", [""])[0].strip()
    approve = action.startswith("Approve")
    q = {"w": form["w"]} if form.get("w") else {}
    if approval_queue(con, u) is None:
        return view_approvals(con, u, q=q)
    if not ids and not aids:
        return view_approvals(con, u, ["Tick at least one entry first."], q=q)
    if not approve and not reason:
        return view_approvals(con, u, ["Give a reason to reject."], q=q)
    done, msgs = 0, []
    for eid in ids:
        if not may_action(con, u, eid):
            msgs.append("Entry %d: only an approver of its scope can approve or reject it "
                        "(a cost controller can, when the booker is the scope's only approver)." % eid)
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
                con.execute("update time_entry set status='Draft', reject_reason=?, reopen_reason=NULL, last_edited_by=?, last_edited_at=? "
                            "where entry_id=?", (reason, u, stamp(), eid))
            done += 1
        except sqlite3.DatabaseError as e:
            msgs.append("Entry %d: %s" % (eid, e))
    a_done = 0
    for aid in aids:
        if not may_action_allowance(con, u, aid):
            msgs.append("Allowance %d: only an approver of its scope can approve or reject it "
                        "(a cost controller can, when the booker is the scope's only approver)." % aid)
            continue
        cur = con.execute("select status from allowance_entry where allowance_entry_id=?", (aid,)).fetchone()
        if not cur or cur[0] != "Submitted":
            msgs.append("Allowance %d: only submitted allowances can be approved or rejected." % aid)
            continue
        try:
            if approve:
                con.execute("update allowance_entry set status='Approved', approved_by=?, approved_at=?, last_edited_by=?, last_edited_at=? "
                            "where allowance_entry_id=?", (u, stamp(), u, stamp(), aid))
            else:
                con.execute("update allowance_entry set status='Draft', reason=?, last_edited_by=?, last_edited_at=? where allowance_entry_id=?",
                            (reason, u, stamp(), aid))
            a_done += 1
        except sqlite3.DatabaseError as e:
            msgs.append("Allowance %d: %s" % (aid, e))
    con.commit()
    verb = "approved" if approve else "sent back to draft"
    ok_msgs = []
    if done:
        ok_msgs.append("%d entr%s %s." % (done, "y" if done == 1 else "ies", verb))
    if a_done:
        ok_msgs.append("%d allowance day%s %s." % (a_done, "" if a_done == 1 else "s", verb))
    return view_approvals(con, u, ok_msgs + msgs, ok=not msgs, q=q)


# ---------- Missing timesheets ----------
MISSING_FILTERS = [("missing", "Not submitted"), ("Not started", "Not started"), ("Draft only", "Draft only"),
                   ("Partly submitted", "Partly submitted"), ("Submitted", "Submitted"), ("all", "Everyone")]


def week_status(n_submitted, n_draft):
    """Where one person's week stands. Submitted covers submitted, approved and edited entries; drafts include entries sent back."""
    if n_submitted == 0 and n_draft == 0:
        return "Not started"
    if n_submitted == 0:
        return "Draft only"
    return "Partly submitted" if n_draft else "Submitted"


def view_missing(con, u, q):
    """Who has not completed their timesheet for a week. Cost controllers see everyone who is active that week. An approver sees
    their own people: those with an approved PAAF for a client they approve, and anyone who has booked to their internal codes."""
    perm = permission_of(con, u)
    if perm != "controller" and not is_approver(con, u):
        return no_access(con, u, "Missing timesheets")
    try:
        w = monday(datetime.date.fromisoformat(q["w"][0]))
    except (KeyError, ValueError):
        w = monday(datetime.date.today()) - datetime.timedelta(days=7)  # the last completed week
    d0, d6 = w.isoformat(), (w + datetime.timedelta(days=6)).isoformat()
    show = q.get("s", ["missing"])[0]
    if show not in dict(MISSING_FILTERS):
        show = "missing"

    scope_sql, scope_args = "", []
    if perm != "controller":
        scope_sql = (" and p.user_id<>? and (p.user_id in (select user_id from paaf where status='Approved' and client_id in "
                     "(select s2.client_id from scope s2 join v_scope_approver va on va.scope_id=s2.scope_id where va.user_id=?)) or p.user_id in "
                     "(select t.user_id from time_entry t join scope s on s.scope_id=t.scope_id join client c on c.client_id=s.client_id "
                     "where c.is_internal=1 and exists (select 1 from v_scope_approver va where va.scope_id=s.scope_id and va.user_id=?)))")
        scope_args = [u, u, u]
    # everyone who is active at some point in the week (start and end dates)
    people = con.execute(
        "select p.user_id, p.nmc_id, p.name, p.employment_type, r.name as title from app_user p join role r on r.role_id=p.role_id "
        "where p.permission<>'admin' and p.start_date<=? and (p.end_date is null or p.end_date>=?)" + scope_sql + " order by p.name", [d6, d0] + scope_args).fetchall()
    week = {r["user_id"]: r for r in con.execute(
        "select user_id, sum(status<>'Draft') as n_sub, sum(status='Draft') as n_draft, "
        "sum(case when status<>'Draft' then hours else 0 end) as h_sub, sum(case when status='Draft' then hours else 0 end) as h_draft, "
        "sum(status='Draft' and (reject_reason is not null or reopen_reason is not null)) as n_back from time_entry where work_date between ? and ? group by user_id", (d0, d6))}

    order = ["Submitted", "Partly submitted", "Draft only", "Not started"]
    counts = dict.fromkeys(order, 0)
    rows = []
    for p in people:
        a = week.get(p["user_id"])
        status = week_status(a["n_sub"] if a else 0, a["n_draft"] if a else 0)
        counts[status] += 1
        if show == "all" or show == status or (show == "missing" and status != "Submitted"):
            rows.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td align="right">%s</td><td align="right">%s</td><td align="right">%s</td>%s</tr>' % (
                esc(p["name"]), esc(p["nmc_id"]), esc(p["title"]), esc(p["employment_type"]),
                status if status == "Submitted" else "<b>%s</b>" % status,
                fmt(a["h_sub"]) if a and a["h_sub"] else "", fmt(a["h_draft"]) if a and a["h_draft"] else "",
                a["n_back"] if a and a["n_back"] else "",
                ('<td><a href="/reopen?u=%d&amp;person=%d&amp;w=%s">Reopen</a></td>' % (u, p["user_id"], d0)
                 if status in ("Submitted", "Partly submitted") else "<td></td>") if perm == "controller" else ""))

    def link(w_, s_, label):
        return '<a href="/missing?u=%d&amp;w=%s&amp;s=%s">%s</a>' % (u, w_.isoformat(), quote(s_), label)

    this_week = monday(datetime.date.today())
    b = ['<p>%s &nbsp; <b>Week commencing %s</b> &nbsp; %s &nbsp; %s</p>' % (
        link(w - datetime.timedelta(days=7), show, "&lt;&lt; Previous week"), w.strftime("%d/%m/%Y"),
        link(w + datetime.timedelta(days=7), show, "Next week &gt;&gt;"), link(this_week, show, "This week so far")),
        '<form method="get" action="/missing">%s<p>Week containing %s Show %s <input type="submit" value="Show"></p></form>' % (
            hidden(u=u), date_input("w", d0), sel("s", MISSING_FILTERS, show, False, "-", ' onchange="this.form.submit()"'))]
    if not people:
        b.append("<p>Nobody is expected to submit a timesheet in this week from your list.%s</p>" % (
            "" if perm == "controller" else " Your people are those on the personnel list of a client you approve, "
            "and anyone who has booked to your internal codes."))
        return layout(con, u, "Missing timesheets", "".join(b))
    b.append("<p><b>%d</b> expected: %s.</p>" % (len(people), ", ".join(
        link(w, st, "%d %s" % (counts[st], st.lower())) for st in order)))
    b.append("<p><small>%s Submitted counts submitted, approved and edited hours. Draft hours are saved but not submitted. "
             "Sent back counts entries an approver rejected or a cost controller reopened that have not been submitted again. "
             "Everyone whose start and end dates cover any part of the week is expected, including the weeks still in progress.</small></p>" % (
                 "Everyone who is active that week." if perm == "controller" else
                 "Your people: those on the personnel list of a client you approve, and anyone who has booked to your internal codes."))
    if rows:
        b.append('<table border="1" cellpadding="3" cellspacing="0"><tr><th>Person</th><th>NMC ID</th><th>Job title</th><th>Employment</th>'
                 '<th>Status</th><th>Submitted hrs</th><th>Draft hrs</th><th>Sent back</th>%s</tr>%s</table>' % (
                     "<th></th>" if perm == "controller" else "", "".join(rows)))
    else:
        b.append("<p>%s</p>" % ("Everyone has submitted for this week." if show == "missing" else "Nobody matches this filter."))
    return layout(con, u, "Missing timesheets", "".join(b))


# ---------- Reopen a timesheet ----------
def view_reopen(con, u, q, msgs=None, ok=False):
    """A cost controller picks a person and a week, sees what they booked, and sends the submitted or approved hours back to Draft."""
    if permission_of(con, u) != "controller":
        return no_access(con, u, "Reopen timesheet")
    try:
        person = int(q.get("person", [""])[0])
    except ValueError:
        person = None
    try:
        w = monday(datetime.date.fromisoformat(q["w"][0]))
    except (KeyError, ValueError):
        w = monday(datetime.date.today()) - datetime.timedelta(days=7)  # the last completed week
    d0, d6 = w.isoformat(), (w + datetime.timedelta(days=6)).isoformat()
    people = [tuple(r) for r in con.execute("select user_id, name from app_user order by name")]
    if person not in [p[0] for p in people]:
        person = None
    b = [msgs_html(msgs, ok),
         "<p><small>Reopening sends a person's submitted, approved or edited hours for a week back to Draft, so they can correct them and "
         "submit again. The approval is cleared, so reopened hours leave the approved totals in the reports until they are submitted and "
         "approved again. You must give a reason: it is shown to the person and kept in the audit trail. You cannot reopen your own timesheet.</small></p>",
         '<form method="get" action="/reopen">%s<p>Person %s Week containing %s <input type="submit" value="Show"></p></form>' % (
             hidden(u=u), sel("person", people, person, True, "Choose a person"), date_input("w", d0))]
    if person is None:
        return layout(con, u, "Reopen timesheet", "".join(b))
    name = dict(people)[person]
    wks = [r[0] for r in con.execute("select distinct date(work_date,'weekday 0','-6 days') as wk from time_entry where user_id=? and status<>'Draft' "
                                     "union select distinct date(work_date,'weekday 0','-6 days') from allowance_entry where user_id=? and status<>'Draft' "
                                     "order by 1 desc limit 12", (person, person))]
    b.append("<p><small>Weeks with submitted or approved hours: %s.</small></p>" % (
        ", ".join('<a href="/reopen?u=%d&amp;person=%d&amp;w=%s">%s</a>' % (u, person, wk, datetime.date.fromisoformat(wk).strftime("%d/%m/%Y")) for wk in wks) or "none"))
    ents = con.execute(
        "select t.*, s.scope_code, (s.scope_code || '-' || coalesce(ph.ctr_code, upper(ph.name))) as phase, ro.skill_code, lo.name as location_name, ap.name as approved_name "
        "from time_entry t join scope s on s.scope_id=t.scope_id join phase ph on ph.phase_id=t.phase_id "
        "join role ro on ro.role_id=t.worked_role_id join location lo on lo.location_id=t.location_id "
        "left join app_user ap on ap.user_id=t.approved_by where t.user_id=? and t.work_date between ? and ? "
        "order by t.work_date, s.scope_code, ph.name", (person, d0, d6)).fetchall()
    b.append("<h4>%s, week commencing %s</h4>" % (esc(name), w.strftime("%d/%m/%Y")))
    if not ents:
        b.append("<p>%s has no hours in this week.</p>" % esc(name))
        return layout(con, u, "Reopen timesheet", "".join(b))
    b.append(table(["Date", "Code", "Skill code", "Location", "Type", "Hours", "Status", "Approved by"],
                   [[e["work_date"], "%s %s" % (e["scope_code"], e["phase"]), e["skill_code"], e["location_name"],
                     "Overtime" if e["overtime"] else "Normal", fmt(e["hours"]), e["status"], e["approved_name"] or ""] for e in ents]))
    reopenable = [e for e in ents if e["status"] != "Draft"]
    period = con.execute("select status from period where week_start=?", (d0,)).fetchone()
    closed = period is None or period["status"] != "Open"
    if person == u:
        b.append("<p>You cannot reopen your own timesheet. Ask another cost controller.</p>")
    elif not reopenable:
        b.append("<p>Every entry in this week is already a draft, so there is nothing to reopen.</p>")
    else:
        extra = ""
        if closed:
            extra = ('<p><label><input type="checkbox" name="reopen_period" value="1" checked> Also reopen the weekly period</label> '
                     "<small>This week is closed, so %s cannot change these hours until it is open again. You can close it again from Admin "
                     "once they have been resubmitted.</small></p>" % esc(name))
        n = len(reopenable)
        b.append('<form method="post" action="/reopen/act">%s%s<p>Reason (shown to %s and kept in the audit trail) %s</p>'
                 '<p><input type="submit" value="Reopen %d entr%s"></p></form>' % (
                     hidden(u=u, person=person, w=d0), extra, esc(name), txt("reason", "", 60), n, "y" if n == 1 else "ies"))
    return layout(con, u, "Reopen timesheet", "".join(b))


def reopen_week(con, form):
    """Send a person's submitted, approved and edited hours for one week back to Draft. Needs a reason, and a cost controller who is not that person."""
    u = int(form["u"][0])
    if permission_of(con, u) != "controller":
        return no_access(con, u, "Reopen timesheet")
    person, w = int(form["person"][0]), monday(datetime.date.fromisoformat(form["w"][0]))
    q = {"person": [str(person)], "w": [w.isoformat()]}
    reason = form.get("reason", [""])[0].strip()
    if not reason:
        return view_reopen(con, u, q, ["Give a reason: it is shown to the person and kept in the audit trail."])
    if person == u:
        return view_reopen(con, u, q, ["You cannot reopen your own timesheet. Ask another cost controller."])
    d0, d6 = w.isoformat(), (w + datetime.timedelta(days=6)).isoformat()
    ids = [r[0] for r in con.execute("select entry_id from time_entry where user_id=? and status<>'Draft' and work_date between ? and ? "
                                     "order by entry_id", (person, d0, d6))]
    aids = [r[0] for r in con.execute("select allowance_entry_id from allowance_entry where user_id=? and status<>'Draft' and work_date between ? and ? "
                                      "order by allowance_entry_id", (person, d0, d6))]
    if not ids and not aids:
        return view_reopen(con, u, q, ["Nothing to reopen: there are no submitted or approved hours in that week."])
    msgs, n = [], 0
    for aid in aids:  # allowances go back to Draft with the hours
        try:
            con.execute("update allowance_entry set status='Draft', approved_by=NULL, approved_at=NULL, reason=?, last_edited_by=?, last_edited_at=? "
                        "where allowance_entry_id=?", (reason, u, stamp(), aid))
        except sqlite3.DatabaseError as e:
            msgs.append("Allowance %d: %s" % (aid, e))
    for eid in ids:
        try:
            con.execute("update time_entry set status='Draft', approved_by=NULL, approved_at=NULL, reject_reason=NULL, reopen_reason=?, "
                        "last_edited_by=?, last_edited_at=? where entry_id=?", (reason, u, stamp(), eid))
            n += 1
        except sqlite3.DatabaseError as e:
            msgs.append("Entry %d: %s" % (eid, e))
    ok_msgs = []
    if n or aids:
        who = con.execute("select name from app_user where user_id=?", (person,)).fetchone()[0]
        ok_msgs.append("%d entr%s reopened%s. They are drafts again, with your reason, for %s to correct and submit." % (
            n, "y" if n == 1 else "ies", (" with %d allowance day%s" % (len(aids), "" if len(aids) == 1 else "s")) if aids else "", who))
        if "reopen_period" in form:
            con.execute("insert or ignore into period (week_start) values (?)", (d0,))
            con.execute("update period set status='Open', closed_by=NULL, closed_at=NULL where week_start=?", (d0,))
            ok_msgs.append("The week commencing %s is open again." % w.strftime("%d/%m/%Y"))
    con.commit()
    return view_reopen(con, u, q, msgs + ok_msgs, ok=not msgs)


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
         '<a href="/reports/period.xlsx?%s">Download the period report as Excel</a></p>' % (u, esc(period_q)),
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


# ---------- Admin ----------
ADMIN_TABS = [("periods", "Weekly periods"), ("users", "Users"), ("roles", "Job families / titles"),
              ("clients", "Clients"), ("rates", "Client rate cards"), ("paafs", "PAAFs"), ("scopes", "Projects / scopes / CTRs"),
              ("internal", "Internal codes"), ("locations", "Locations (hour types)"), ("allowance_rates", "Allowance rates")]


# Who may use each Admin tab. IT manages users and job roles; cost controllers manage clients, rate cards, PAAFs, scopes and internal codes.
# Weekly periods is open to both controllers and admins until the cost team confirms who owns it (the audit trail is handled the same way).
TAB_ACCESS = {"periods": ("controller", "admin"), "users": ("admin",), "roles": ("admin",), "clients": ("controller",),
              "rates": ("controller",), "paafs": ("controller",), "scopes": ("controller",), "internal": ("controller",),
              "locations": ("admin",), "allowance_rates": ("controller",)}
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


def rate_options(con, client_id=None):
    """Rates on the client rate cards, for choosing the rate on a PAAF: (rate_id, 'Client - PM-04 Planner - 85 (from 2026-01-01)')."""
    return [(r["rate_id"], "%s - %s %s - %g (from %s)" % (r["client"], r["skill_code"], r["role"], r["sales_rate"], r["effective_from"]))
            for r in con.execute("select rt.rate_id, c.name as client, ro.skill_code, ro.name as role, rt.sales_rate, rt.effective_from from rate rt "
                                 "join client c on c.client_id=rt.client_id join role ro on ro.role_id=rt.role_id "
                                 "where (? is null or rt.client_id=?) order by c.name, ro.skill_code, rt.effective_from desc", (client_id, client_id))]


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


PAAF_STATES = [("current", "On the list"), ("ended", "Ended")]
PAAF_SORTS = {"id": ("p.paaf_id", "p.paaf_id desc"),
              "name": ("u.name, p.paaf_id", "u.name desc, p.paaf_id desc"),
              "client": ("c.name, u.name, p.paaf_id", "c.name desc, u.name desc, p.paaf_id desc"),
              "expiry": ("p.expiry_date is null, p.expiry_date, p.paaf_id", "p.expiry_date is not null, p.expiry_date desc, p.paaf_id desc")}


def paaf_filters(q):
    """The PAAF list's filters and order, from a query or a posted form (values are lists): person, client, status, sort (a leading - is descending)."""
    def one(k):
        return (q.get(k) or [""])[0].strip()

    def num(k):
        try:
            return int(one(k))
        except ValueError:
            return None
    sort = one("f_sort") or "id"
    desc = sort.startswith("-")
    if sort.lstrip("-") not in PAAF_SORTS:
        sort, desc = "id", False
    status = one("f_status")
    return {"person": num("f_person"), "client": num("f_client"), "status": status if status in dict(PAAF_STATES) else "", "sort": sort.lstrip("-"), "desc": desc}


def paaf_hidden(f):
    """The list's filters and order carried through a form, so an action leaves you looking at the same list."""
    return hidden(f_person=f["person"] or "", f_client=f["client"] or "", f_status=f["status"], f_sort=("-" if f["desc"] else "") + f["sort"])


def admin_body(con, u, t, q=None):
    b = []
    if t == "periods":
        rows = [list(r) for r in con.execute("select week_start, status, closed_by, closed_at from period order by week_start")]
        b.append(table(["week_start", "status", "closed_by", "closed_at"], [r[:3] + [fmt_time(r[3])] for r in rows]))
        closes = "".join('<form method="post" action="/admin/period" style="display:inline">%s'
                         '<input type="submit" value="Close week %s"></form> ' % (hidden(u=u, week_start=r[0], action="close"), r[0])
                         for r in rows if r[1] == "Open")
        b.append("<p>%s</p>" % closes)
        reopens = "".join('<form method="post" action="/admin/period" style="display:inline">%s'
                          '<input type="submit" value="Reopen week %s"></form> ' % (hidden(u=u, week_start=r[0], action="reopen"), r[0])
                          for r in rows if r[1] == "Closed")
        if reopens:
            b.append("<p>%s</p>" % reopens)
        b.append("<p><small>Each week opens automatically when the week starts. Close a week here when it should stop taking new hours. A closed week can be reopened, for example so a reopened timesheet can be corrected.</small></p>")
        b.append('<form method="post" action="/admin/period">%s<b>Open a missed week</b> - Monday date %s '
                 '<input type="submit" value="Open week"></form>' % (hidden(u=u, action="open"), date_input("week_start")))
    elif t == "users":
        needle = ((q or {}).get("f_q", [""])[0]).strip()
        b.append('<form method="get" action="/admin">%s<p>Find a person (name or NMC ID) <input type="text" name="f_q" size="20" value="%s"> '
                 '<input type="submit" value="Search"> <input type="button" value="Clear" onclick="location.href=\'/admin?u=%d&amp;t=users\'"></p></form>' % (
                     hidden(u=u, t="users"), esc(needle), u))
        rows = [[r["nmc_id"], r["name"], r["employment_type"], r["skill_code"] + " " + r["role"], r["manager"] or "", r["permission"], r["start_date"],
                 r["end_date"], edit_link(u, "users", r["user_id"])]
                for r in con.execute("select u.*, r.skill_code, r.name as role, m.name as manager from app_user u join role r on r.role_id=u.role_id "
                                     "left join app_user m on m.user_id=u.line_manager_id "
                                     "where (? = '' or u.name like '%' || ? || '%' or u.nmc_id like '%' || ? || '%') order by u.nmc_id", (needle, needle, needle))]
        b.append(table(["NMC ID", "Name", "Employment", "Role", "Line manager", "Permission", "Start", "End", ""], rows, raw_cols=(8,)))
        b.append('<h4>Add a user</h4><form method="post" action="/admin/add">%s<table border="0" cellpadding="2">'
                 "<tr><td>NMC ID</td><td>%s</td></tr><tr><td>Name</td><td>%s</td></tr>"
                 "<tr><td>Employment</td><td>%s</td></tr><tr><td>Role</td><td>%s</td></tr><tr><td>Line manager</td><td>%s</td></tr>"
                 "<tr><td>Permission</td><td>%s</td></tr><tr><td>Start date</td><td>%s</td></tr>"
                 "<tr><td>End date (optional)</td><td>%s</td></tr></table><input type=\"submit\" value=\"Add user\"></form>"
                 % (hidden(u=u, t="users"), txt("nmc_id", "", 8), txt("name", "", 30),
                    sel("employment_type", [("Staff", "Staff"), ("Contract", "Contract")]), sel("role_id", opts_roles(con)),
                    sel("line_manager_id", opts_users(con), None, True, "None"),
                    sel("permission", PERMISSION_OPTIONS), date_input("start_date", datetime.date.today().isoformat()),
                    date_input("end_date")))
    elif t == "roles":
        family = ((q or {}).get("f_family", [""])[0]).strip()
        known = {r[0] for r in con.execute("select distinct family from role")}
        if family and family in known:
            # one job family: its job titles, how many people hold each, and the means to rename the family or add a title to it
            b.append('<p><a href="/admin?u=%d&amp;t=roles">&lt;&lt; All job families</a></p><h4>%s</h4>' % (u, esc(family)))
            rows = [[r["skill_code"], r["name"], r["people"], "Yes" if r["active"] else "No", edit_link(u, "roles", r["role_id"])]
                    for r in con.execute("select ro.*, (select count(*) from app_user x where x.role_id=ro.role_id) as people "
                                         "from role ro where ro.family=? order by ro.skill_code", (family,))]
            b.append(table(["Skill code", "Job title", "People in this role", "Active", ""], rows, raw_cols=(4,)))
            b.append('<h4>Add a job title to %s</h4><form method="post" action="/admin/add">%s%s Skill code %s Job title %s '
                     '<input type="submit" value="Add job title"></form>' % (esc(family), hidden(u=u, t="roles", family=family), "", txt("skill_code", "", 8), txt("name", "", 30)))
            b.append('<h4>Rename this family</h4><p><small>Renames the family on all of its job titles.</small></p>'
                     '<form method="post" action="/admin/save">%s New name %s <input type="submit" value="Rename family"></form>' % (
                         hidden(u=u, t="roles", id=0, old_family=family), txt("new_family", family, 24)))
        else:
            fams = con.execute("select family, count(*) as titles, sum(active) as active_titles, "
                               "(select count(*) from app_user x join role r2 on r2.role_id=x.role_id where r2.family=role.family) as people "
                               "from role group by family order by family").fetchall()
            rows = ['<tr><td><a href="/admin?u=%d&amp;t=roles&amp;f_family=%s">%s</a></td><td align="right">%d</td><td align="right">%d</td><td align="right">%d</td></tr>' % (
                u, quote(f["family"]), esc(f["family"]), f["titles"], f["active_titles"], f["people"]) for f in fams]
            b.append("<p><small>Job families (departments) group job titles. Click a family to see its job titles and rename it.</small></p>")
            b.append('<table border="1" cellpadding="3" cellspacing="0"><tr><th>Job family</th><th>Job titles</th><th>Active titles</th><th>People</th></tr>%s</table>' % "".join(rows))
            b.append('<h4>Add a job title</h4><p><small>Type an existing family name to add to it, or a new name to start a new family.</small></p>'
                     '<form method="post" action="/admin/add">%s Job family <input type="text" name="family" size="24" list="families"><datalist id="families">%s</datalist> '
                     'Skill code %s Job title %s <input type="submit" value="Add role"></form>' % (
                         hidden(u=u, t="roles"), "".join('<option value="%s">' % esc(f["family"]) for f in fams), txt("skill_code", "", 8), txt("name", "", 30)))
    elif t == "clients":
        rows = [[r["name"], "Internal (not billed)" if r["is_internal"] else "Client", r["approver"] or "none yet", edit_link(u, "clients", r["client_id"])]
                for r in con.execute("select c.client_id, c.name, c.is_internal, a.name as approver from client c "
                                     "left join app_user a on a.user_id=c.approver_user_id order by c.is_internal, c.name")]
        b.append("<p><small>Each client has a main approver, who approves hours booked to any of its scopes. A scope can also have secondary approvers "
                 "(Projects / scopes / CTRs tab). Nobody approves their own hours; if the booker is a scope's only approver, a cost controller approves them.</small></p>")
        b.append(table(["Client", "Type", "Main approver", ""], rows, raw_cols=(3,)))
        b.append('<h4>Add a client</h4><form method="post" action="/admin/add">%s Name %s Main approver %s <input type="submit" value="Add client"></form>'
                 % (hidden(u=u, t="clients"), txt("name", "", 30), sel("approver_user_id", opts_approvers(con), None, True, "Choose an approver")))
    elif t == "rates":
        try:
            f_client = int((q or {}).get("f_client", [""])[0])
        except ValueError:
            f_client = None
        today = datetime.date.today().isoformat()
        b.append('<form method="get" action="/admin">%s<p>Client %s <input type="submit" value="Filter"> '
                 '<small>Current is the rate in force today; Superseded rates stay so past hours keep their price; Future starts later.</small></p></form>' % (
                     hidden(u=u, t="rates"), sel("f_client", opts_clients(con, True), f_client, True, "All clients")))
        rows = [[r["client"], r["skill_code"], r["role"], "%.2f" % r["sales_rate"], r["effective_from"], r["state"]]
                for r in con.execute("select c.name as client, ro.skill_code, ro.name as role, rt.sales_rate, rt.effective_from, "
                                     "case when rt.effective_from > ? then 'Future' when rt.effective_from = (select max(r2.effective_from) from rate r2 "
                                     "where r2.client_id=rt.client_id and r2.role_id=rt.role_id and r2.effective_from <= ?) then 'Current' else 'Superseded' end as state "
                                     "from rate rt join client c on c.client_id=rt.client_id join role ro on ro.role_id=rt.role_id "
                                     "where (? is null or rt.client_id=?) order by c.name, ro.skill_code, rt.effective_from desc", (today, today, f_client, f_client))]
        b.append(table(["Client", "Skill code", "Role", "Sales rate", "Effective from", "State"], rows))
        b.append('<h4>Add a rate</h4><p><small>Rates are never edited. To change a rate, add a new row with a later effective date. '
                 "Old rows stay, so hours already booked keep the price they were booked at.</small></p>"
                 '<form method="post" action="/admin/add">%s Client %s Skill code %s Sales rate %s Effective from %s '
                 '<input type="submit" value="Add rate"></form>'
                 % (hidden(u=u, t="rates"), sel("client_id", opts_clients(con, True)), sel("role_id", opts_roles(con)),
                    txt("sales_rate", "", 8), date_input("effective_from", datetime.date.today().isoformat())))
    elif t == "paafs":
        flt = paaf_filters(q or {})
        today = datetime.date.today().isoformat()
        where, wargs = "", []
        for key, col in (("person", "p.user_id"), ("client", "p.client_id")):
            if flt[key]:
                where += " and %s=?" % col
                wargs.append(flt[key])
        if flt["status"] == "current":
            where += " and (p.expiry_date is null or p.expiry_date >= ?)"
            wargs.append(today)
        elif flt["status"] == "ended":
            where += " and p.expiry_date < ?"
            wargs.append(today)
        total = con.execute("select count(*) from paaf").fetchone()[0]
        order = PAAF_SORTS[flt["sort"]][1 if flt["desc"] else 0]
        rows = []
        found = con.execute("select p.*, u.name as uname, c.name as client, ro.skill_code, rt.sales_rate "
                            "from paaf p join app_user u on u.user_id=p.user_id join client c on c.client_id=p.client_id "
                            "join rate rt on rt.rate_id=p.rate_id join role ro on ro.role_id=rt.role_id where 1=1" + where + " order by " + order, wargs).fetchall()
        b.append("<p><small>The people who work on each client's projects: the list that goes to the client. None of our clients approve individuals, so a person is "
                 "on a client's list as soon as they are added and can book to that client's CTRs until their end date.</small></p>")
        b.append('<form method="get" action="/admin">%s<p>Person %s Client %s Show %s <input type="submit" value="Filter"> '
                 '<input type="button" value="Clear filters" onclick="location.href=\'/admin?u=%d&amp;t=paafs\'"> '
                 '<small>Showing %d of %d entries, in order of the # column. Click a column heading to sort by it, and again to reverse it.</small></p></form>' % (
                     hidden(u=u, t="paafs", f_sort=("-" if flt["desc"] else "") + flt["sort"]),
                     sel("f_person", [(r["user_id"], r["name"]) for r in con.execute(
                         "select distinct a.user_id, a.name from app_user a join paaf p on p.user_id=a.user_id order by a.name")], flt["person"], True, "Everyone"),
                     sel("f_client", [tuple(r) for r in con.execute(
                         "select distinct c.client_id, c.name from client c join paaf p on p.client_id=c.client_id order by c.name")], flt["client"], True, "All clients"),
                     sel("f_status", PAAF_STATES, flt["status"], True, "Everyone listed"), u, len(found), total))

        def head(label, key):
            flip = "-" + key if (flt["sort"] == key and not flt["desc"]) else key
            arrow = (" &#9650;" if not flt["desc"] else " &#9660;") if flt["sort"] == key else ""
            return '<a href="/admin?u=%d&amp;t=paafs&amp;f_person=%s&amp;f_client=%s&amp;f_status=%s&amp;f_sort=%s" style="color:inherit">%s%s</a>' % (
                u, flt["person"] or "", flt["client"] or "", quote(flt["status"]), quote(flip), label, arrow)
        for r in found:
            ended = bool(r["expiry_date"] and r["expiry_date"] < today)
            f = '<form method="post" action="/admin/paaf" style="display:inline">%s%s' % (hidden(u=u, paaf_id=r["paaf_id"]), paaf_hidden(flt))
            f += ('%s <input type="submit" name="action" value="Change rate"> <br>End date %s <input type="submit" name="action" value="Save end date"> '
                  '<small>(empty = no end)</small></form>' % (sel("rate_id", rate_options(con, r["client_id"]), r["rate_id"]), date_input("expiry_date", r["expiry_date"] or "")))
            rows.append([r["paaf_id"], r["uname"], r["client"], r["skill_code"], "%g" % r["sales_rate"], ("Ended %s" % r["expiry_date"]) if ended else (r["expiry_date"] or ""), f])
        heads = [head("#", "id"), head("Name", "name"), head("Client", "client"), "Skill code", "Sales rate", head("Ends", "expiry"), "Action"]  # raw: the headings hold sort links
        if rows:
            b.append(table(heads, rows, raw_cols=(6,), raw_heads=True))
        else:
            b.append("<p>Nobody matches these filters.</p>")
        b.append('<h4>Add a person to a client</h4><p><small>One entry per person per client. Pick the rate to assign from the client\'s rate card, or leave it '
                 "on the default, the rate for the person's own role. The rate card line you pick is the person's position on that client: it sets the rate for their hours "
                 "when they book as their own role, at the rate in force on the work date (covering another role uses the card line for that role). "
                 "The rate can be changed at any time; hours already booked keep the rate they were booked at. Set an end date when someone leaves the client's projects.</small></p>"
                 '<form method="post" action="/admin/add">%s Person %s Client %s Rate %s <input type="submit" value="Add person"></form>'
                 % (hidden(u=u, t="paafs"), sel("user_id", opts_users(con)), sel("client_id", opts_clients(con, True)),
                    sel("rate_id", rate_options(con), None, True, "Default: the person's own role")))
    elif t == "allowance_rates":
        today = datetime.date.today().isoformat()
        grid = []
        for al in con.execute("select allowance_id, name from allowance_type where active=1 order by allowance_id"):
            cur = con.execute("select amount from allowance_rate where allowance_id=? and effective_from<=? order by effective_from desc limit 1", (al["allowance_id"], today)).fetchone()
            grid.append('<tr><td>%s</td><td><input type="text" size="8" name="r_%d" value="%s"></td></tr>' % (esc(al["name"]), al["allowance_id"], fmt(cur[0] if cur else 0)))
        b.append("<p><small>What one day of each allowance is worth. Allowances are for staff only (contractors cannot claim them). Rates are never edited: change the figure "
                 "here and it is saved as a new rate from the date you give, so earlier claims keep the value they had.</small></p>")
        b.append('<form method="post" action="/admin/allowance_rates">%s<table border="1" cellpadding="3" cellspacing="0"><tr><th>Allowance</th><th>&pound; per day</th></tr>%s</table>'
                 '<p>New rate applies from %s <input type="submit" value="Save rate"></p></form>' % (hidden(u=u), "".join(grid), date_input("effective_from", today)))
        hist = [[r["name"], "%.2f" % r["amount"], r["effective_from"]]
                for r in con.execute("select at.name, ar.amount, ar.effective_from from allowance_rate ar join allowance_type at on at.allowance_id=ar.allowance_id "
                                     "order by at.allowance_id, ar.effective_from desc")]
        b.append("<h4>Every rate, newest first</h4>" + table(["Allowance", "Per day", "From"], hist))
    elif t == "locations":
        b.append("<p><small>Where hours are worked. People pick one on each timesheet row (and tick Overtime for overtime). "
                 "Rename a location or switch it off to stop new bookings to it; hours already booked keep it. At least one must stay active.</small></p>")
        rows = [[r["name"], "Yes" if r["active"] else "No", edit_link(u, "locations", r["location_id"])]
                for r in con.execute("select * from location order by location_id")]
        b.append(table(["Location", "Active", ""], rows, raw_cols=(2,)))
        b.append('<h4>Add a location</h4><form method="post" action="/admin/add">%s Name %s <input type="submit" value="Add location"></form>'
                 % (hidden(u=u, t="locations"), txt("name", "", 24)))
    elif t == "internal":
        b.append("<p><small>Internal codes are for hours NMC pays for itself, such as Business Services or Training. People pick one in their "
                 "timesheet (shown as Internal - name). They need no PAAF and have no rate, and they are kept out of the client reports. "
                 "Each code has an approver, who approves the hours booked to it. Close a code to stop new bookings to it.</small></p>")
        rows = [[r["scope_code"], r["description"], r["status"], r["approvers"] or "", edit_link(u, "internal", r["scope_id"])]
                for r in con.execute("select s.scope_id, s.scope_code, s.description, s.status, "
                                     "(select group_concat(x.name, ', ') from (select au.name from v_scope_approver va join app_user au on au.user_id=va.user_id "
                                     "where va.scope_id=s.scope_id order by au.name) x) as approvers from scope s "
                                     "join client c on c.client_id=s.client_id where c.is_internal=1 order by s.scope_code")]
        b.append(table(["Code", "Name", "Status", "Approvers", ""], rows, raw_cols=(4,)))
        b.append('<h4>Add an internal code</h4><form method="post" action="/admin/add">%s<table border="0" cellpadding="2">'
                 "<tr><td>Name</td><td>%s</td></tr></table>"
                 '<input type="submit" value="Add internal code"></form><p><small>The Internal client\'s main approver (set on the Clients tab) approves its hours; '
                 'add secondary approvers on the code\'s Edit page.</small></p>'
                 % (hidden(u=u, t="internal"), txt("description", "", 40)))
    elif t == "scopes":
        b.append("<p><small>Structure: client &gt; project &gt; scope &gt; CTR. A CTR is the booking code hours are booked to. Hours are approved by the client's main approver "
                 "(set on the Clients tab) or a secondary approver named on the scope.</small></p>")
        b.append("<h4>Projects</h4>")
        rows = [[r["client"], r["project_code"], r["name"], r["status"], edit_link(u, "projects", r["project_id"])]
                for r in con.execute("select p.*, c.name as client from project p join client c on c.client_id=p.client_id order by c.name, p.name")]
        b.append(table(["Client", "Code", "Project", "Status", ""], rows, raw_cols=(4,)))
        b.append('<form method="post" action="/admin/add">%s Client %s Code %s Name %s <input type="submit" value="Add project"></form>'
                 % (hidden(u=u, t="projects"), sel("client_id", opts_clients(con)), txt("project_code", "", 10), txt("name", "", 30)))
        b.append("<h4>Scopes</h4>")
        rows = [[r["client"], r["project"], r["scope_code"], r["description"], r["status"], r["secondary"] or "none", edit_link(u, "scopes", r["scope_id"])]
                for r in con.execute("select s.scope_id, c.name as client, pr.name as project, s.scope_code, s.description, s.status, "
                                     "(select group_concat(x.name, ', ') from (select au.name from scope_approver sa join app_user au on au.user_id=sa.user_id "
                                     "where sa.scope_id=s.scope_id order by au.name) x) as secondary "
                                     "from scope s join client c on c.client_id=s.client_id join project pr on pr.project_id=s.project_id "
                                     "order by c.name, pr.name, s.scope_code")]
        b.append(table(["Client", "Project", "Scope", "Description", "Status", "Secondary approvers", ""], rows, raw_cols=(6,)))
        b.append('<p><b>Add a scope</b> <small>A CTR called General is created automatically; add or rename CTRs below. The client\'s main approver approves the scope\'s hours; '
                 'name secondary approvers on the scope\'s Edit page.</small></p>'
                 '<form method="post" action="/admin/add">%s<table border="0" cellpadding="2">'
                 "<tr><td>Project</td><td>%s</td></tr><tr><td>Scope code</td><td>%s</td></tr>"
                 "<tr><td>Description</td><td>%s</td></tr><tr><td>Status</td><td>%s</td></tr></table>"
                 '<input type="submit" value="Add scope"></form>'
                 % (hidden(u=u, t="scopes"), sel("project_id", opts_projects(con)), txt("scope_code", "", 10), txt("description", "", 40),
                    sel("status", [("Open", "Open"), ("Pipeline", "Pipeline"), ("Closed", "Closed")])))
        b.append("<h4>CTRs</h4>")
        rows = []
        for r in con.execute("select ph.*, s.scope_code, s.description as scope_desc, c.name as client, "
                             "(select count(*) from phase_person pp where pp.phase_id=ph.phase_id) as n_people "
                             "from phase ph join scope s on s.scope_id=ph.scope_id join client c on c.client_id=s.client_id "
                             "order by c.name, s.scope_code, ph.name"):
            controls = []
            if r["restricted"]:
                controls.append("%d named %s only" % (r["n_people"], "person" if r["n_people"] == 1 else "people"))
            if r["book_from"] or r["book_to"]:
                controls.append("%s to %s" % (r["book_from"] or "any date", r["book_to"] or "any date"))
            rows.append([r["client"], r["scope_code"], r["scope_desc"], r["name"], r["description"] or "", r["status"],
                         "; ".join(controls) or "open to all", edit_link(u, "phases", r["phase_id"])])
        b.append(table(["Client", "Scope", "Scope description", "CTR (booking code)", "CTR description", "Status", "Who and when can book", ""], rows, raw_cols=(7,)))
        b.append('<form method="post" action="/admin/add">%s Scope %s CTR name %s <input type="submit" value="Add CTR"></form>'
                 % (hidden(u=u, t="phases"), sel("scope_id", opts_scopes(con)), txt("name", "", 20)))
    return "".join(b)


def view_admin(con, u, t, msgs=None, ok=False, q=None):
    tabs = allowed_tabs(con, u)
    if not tabs or (t in dict(ADMIN_TABS) and not can_use_tab(con, u, t)):
        return no_access(con, u, "Admin")
    t = t if t in dict(tabs) else tabs[0][0]
    links = "".join('<a href="/admin?u=%d&amp;t=%s"%s>%s</a>' % (u, k, ' class="cur"' if k == t else "", esc(v)) for k, v in tabs)
    body = '%s<p class="tabs">%s</p><h4>%s</h4>%s' % (msgs_html(msgs, ok), links, esc(dict(tabs)[t]), admin_body(con, u, t, q))
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
                "<tr><td>Role</td><td>%s</td></tr><tr><td>Line manager</td><td>%s</td></tr><tr><td>Permission</td><td>%s</td></tr><tr><td>Start date</td><td>%s</td></tr>"
                "<tr><td>End date</td><td>%s</td></tr>" % (
                    esc(r["nmc_id"]), txt("name", r["name"], 30), sel("employment_type", [("Staff", "Staff"), ("Contract", "Contract")], r["employment_type"]),
                    sel("role_id", opts_roles(con, active_only=False), r["role_id"]),
                    sel("line_manager_id", [x for x in opts_users(con) if x[0] != i], r["line_manager_id"], True, "None"),
                    sel("permission", PERMISSION_OPTIONS, r["permission"]), esc(r["start_date"]), date_input("end_date", r["end_date"] or "")))
        note = "<p><small>Changing someone's role does not change their existing PAAFs or booked hours.</small></p>"
    elif t == "roles":
        r = con.execute("select * from role where role_id=?", (i,)).fetchone()
        if not r:
            return layout(con, u, "Admin", "<p>Not found.</p>")
        form = ("<tr><td>Job family</td><td>%s</td></tr><tr><td>Skill code</td><td>%s</td></tr><tr><td>Job title</td><td>%s</td></tr><tr><td>Active</td><td>%s</td></tr>"
                % (txt("family", r["family"], 24), txt("skill_code", r["skill_code"], 8), txt("name", r["name"], 30), sel("active", [("1", "Yes"), ("0", "No")], str(r["active"]))))
        note = "<p><small>Deactivating a role hides it from new users and new timesheet rows. It does not affect past entries.</small></p>"
    elif t == "locations":
        r = con.execute("select * from location where location_id=?", (i,)).fetchone()
        if not r:
            return layout(con, u, "Admin", "<p>Not found.</p>")
        form = ("<tr><td>Name</td><td>%s</td></tr><tr><td>Active</td><td>%s</td></tr>" % (
            txt("name", r["name"], 24), sel("active", [("1", "Yes"), ("0", "No")], str(r["active"]))))
        note = "<p><small>Switching a location off stops new bookings to it. Hours already booked keep it.</small></p>"
    elif t == "clients":
        r = con.execute("select c.*, a.name as approver from client c left join app_user a on a.user_id=c.approver_user_id where c.client_id=?", (i,)).fetchone()
        if not r:
            return layout(con, u, "Admin", "<p>Not found.</p>")
        form = ("<tr><td>Client</td><td>%s</td></tr><tr><td>Main approver</td><td>%s</td></tr><tr><td>Framework agreement ref</td><td>%s</td></tr>" % (
            esc(r["name"]), sel("approver_user_id", opts_approvers(con), r["approver_user_id"], True, "None"), txt("framework_ref", r["framework_ref"] or "", 40)))
        note = ("<p><small>The main approver approves hours booked to any scope of this client (never their own). Changing it moves the waiting entries to the new "
                "approver's queue.</small></p>")
    elif t in ("scopes", "internal"):  # an internal code is a scope of the Internal client
        r = con.execute("select s.*, c.name as client, a.name as main_approver from scope s join client c on c.client_id=s.client_id "
                        "left join app_user a on a.user_id=c.approver_user_id where scope_id=?", (i,)).fetchone()
        if not r:
            return layout(con, u, "Admin", "<p>Not found.</p>")
        chosen = {x[0] for x in con.execute("select user_id from scope_approver where scope_id=?", (i,))}
        boxes = " ".join('<label><input type="checkbox" name="secondary" value="%d"%s> %s</label>' % (
            p[0], " checked" if p[0] in chosen else "", esc(p[1])) for p in opts_approvers(con))
        form = ("<tr><td>Client</td><td>%s</td></tr><tr><td>Scope code</td><td>%s</td></tr><tr><td>Description</td><td>%s</td></tr>"
                "<tr><td>Status</td><td>%s</td></tr><tr><td>Main approver (from the client)</td><td>%s</td></tr><tr><td>Secondary approvers</td><td>%s</td></tr>" % (
                    esc(r["client"]), esc(r["scope_code"]), txt("description", r["description"], 40),
                    sel("status", [("Open", "Open"), ("Pipeline", "Pipeline"), ("Closed", "Closed")], r["status"]),
                    esc(r["main_approver"] or "none"), boxes))
        note = ("<p><small>Hours on this scope are approved by the client's main approver or any secondary approver ticked here (never their own). "
                "Closing a scope stops new bookings.</small></p>")
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
        allowed = {x[0] for x in con.execute("select user_id from phase_person where phase_id=?", (i,))}
        people = " ".join('<label><input type="checkbox" name="person" value="%d"%s> %s</label>' % (
            p["user_id"], " checked" if p["user_id"] in allowed else "", esc(p["name"]))
            for p in con.execute("select user_id, name from app_user order by name"))
        def area(name, value, rows=3):
            return '<textarea name="%s" rows="%d" cols="70">%s</textarea>' % (name, rows, esc(value or ""))
        form = ("<tr><td>Scope</td><td>%s</td></tr><tr><td>CTR name</td><td>%s</td></tr><tr><td>Status</td><td>%s</td></tr>"
                "<tr><td>CTR code</td><td>%s <small>discipline - phase letter - number, e.g. PM-D-001. The scope code goes in front: %s-&hellip;</small></td></tr>"
                "<tr><td>CTR title</td><td>%s</td></tr><tr><td>Prepared by</td><td>%s</td></tr><tr><td>Checked by</td><td>%s</td></tr>"
                "<tr><td>Objectives</td><td>%s</td></tr><tr><td>Input requirements</td><td>%s</td></tr><tr><td>Scope of work</td><td>%s</td></tr>"
                "<tr><td>This CTR assumes</td><td>%s</td></tr><tr><td>This CTR excludes</td><td>%s</td></tr>"
                "<tr><td>Expenses</td><td>%s</td></tr><tr><td>Payment terms</td><td>%s</td></tr><tr><td>Basis of proposal</td><td>%s</td></tr>"
                "<tr><td>WBS</td><td>%s</td></tr><tr><td>CBS</td><td>%s</td></tr><tr><td>ABS</td><td>%s</td></tr>"
                "<tr><td>Who can book</td><td>%s</td></tr><tr><td>People allowed</td><td>%s</td></tr>"
                "<tr><td>Bookable from</td><td>%s</td></tr><tr><td>Bookable until</td><td>%s</td></tr>"
                % (esc(r["scope_code"]), txt("name", r["name"], 20), sel("status", [("Open", "Open"), ("Closed", "Closed")], r["status"]),
                   txt("ctr_code", r["ctr_code"] or "", 14), esc(r["scope_code"]),
                   txt("description", r["description"] or "", 50), txt("prepared_by", r["prepared_by"] or "", 20), txt("checked_by", r["checked_by"] or "", 20),
                   area("objectives", r["objectives"]), area("inputs", r["inputs"]), area("scope_of_work", r["scope_of_work"]),
                   area("assumptions", r["assumptions"]), area("exclusions", r["exclusions"]),
                   txt("expenses", r["expenses"] or "Reimbursable", 20),
                   sel("payment_terms", [("Reimbursable", "Reimbursable"), ("Budget", "Budget"), ("Lump Sum", "Lump Sum")], r["payment_terms"]),
                   area("basis", r["basis"]),
                   txt("wbs", r["wbs"] or "", 20), txt("cbs", r["cbs"] or "", 20), txt("abs_code", r["abs_code"] or "", 20),
                   sel("restricted", [("0", "Anyone with an approved PAAF (or an internal code)"), ("1", "Only the people ticked below")], str(r["restricted"])),
                   people, date_input("book_from", r["book_from"] or ""), date_input("book_to", r["book_to"] or "")))
        note = ("<p><small>Closing a CTR stops new bookings to it. Existing hours are not affected. Hours are approved by the client's main approver or the scope's "
                "secondary approvers (set on the scope). Transaction controls: by default anyone who may book to the client "
                "can book to this code. Choose \"Only the people ticked\" to limit it to a list (nobody can book if the list is empty), and set dates to limit "
                "which work dates can be booked. These controls stop new bookings only; hours already booked are not affected. WBS, CBS and ABS are "
                "optional reference fields.</small></p>")
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
            con.execute("insert into app_user (nmc_id, employment_type, name, role_id, permission, start_date, end_date, line_manager_id) values (?,?,?,?,?,?,?,?)",
                        (need_text(g("nmc_id"), "NMC ID"), g("employment_type"), need_text(g("name"), "Name"), int(g("role_id")),
                         g("permission"), need_date(g("start_date"), "Start date"), need_date(g("end_date"), "End date", False),
                         int(g("line_manager_id")) if g("line_manager_id") else None))
            msg = "User added."
        elif t == "roles":
            con.execute("insert into role (skill_code, name, family) values (?,?,?)",
                        (need_text(g("skill_code"), "Skill code"), need_text(g("name"), "Job title"), need_text(g("family"), "Job family")))
            msg = "Role added."
            con.commit()
            return view_admin(con, u, t, [msg], ok=True, q={"f_family": [g("family")]})
        elif t == "clients":
            if not g("approver_user_id"):
                raise ValueError("Choose a main approver for the client.")
            con.execute("insert into client (name, approver_user_id) values (?,?)", (need_text(g("name"), "Client name"), int(g("approver_user_id"))))
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
            cur = con.execute("insert into scope (scope_code, client_id, project_id, description, status) values (?,?,?,?,?)",
                              (need_text(g("scope_code"), "Scope code"), pr[0], int(g("project_id")), need_text(g("description"), "Description"), g("status")))
            con.execute("insert into phase (scope_id, name) values (?, 'General')", (cur.lastrowid,))
            msg = "Scope added with a General CTR."
        elif t == "phases":
            con.execute("insert into phase (scope_id, name) values (?,?)", (int(g("scope_id")), need_text(g("name"), "CTR name")))
            msg = "CTR added."
        elif t == "locations":
            con.execute("insert into location (name) values (?)", (need_text(g("name"), "Location name"),))
            msg = "Location added."
        elif t == "internal":
            # An internal code is a scope (with a General phase) under the Internal client's project. The code is numbered for you.
            pr = con.execute("select p.project_id, p.client_id from project p join client c on c.client_id=p.client_id "
                             "where c.is_internal=1 order by p.project_id limit 1").fetchone()
            if not pr:
                raise ValueError("There is no Internal client and project to add codes to.")
            n = con.execute("select coalesce(max(cast(substr(scope_code, 4) as integer)), 0) + 1 from scope "
                            "where scope_code like 'INT%' and substr(scope_code, 4) glob '[0-9]*'").fetchone()[0]
            code = "INT%02d" % n
            cur = con.execute("insert into scope (scope_code, client_id, project_id, description, status) values (?,?,?,?, 'Open')",
                              (code, pr["client_id"], pr["project_id"], need_text(g("description"), "Name")))
            con.execute("insert into phase (scope_id, name) values (?, 'General')", (cur.lastrowid,))
            msg = "Internal code %s added." % code
        elif t == "paafs":
            user_id, client_id = int(g("user_id")), int(g("client_id"))
            if g("rate_id"):  # a rate picked from the dropdown must be on this client's rate card
                rate = con.execute("select rate_id from rate where rate_id=? and client_id=?", (int(g("rate_id")), client_id)).fetchone()
                if not rate:
                    raise ValueError("That rate is not on this client's rate card. Choose one of the client's rates.")
            else:
                rate = con.execute("select rt.rate_id from rate rt join app_user u on u.role_id=rt.role_id where u.user_id=? and rt.client_id=? "
                                   "and rt.effective_from<=? order by rt.effective_from desc limit 1",
                                   (user_id, client_id, datetime.date.today().isoformat())).fetchone()
            if not rate:
                raise ValueError("That client's rate card has no rate for this person's role. Add the rate first, or pick a rate.")
            con.execute("insert into paaf (user_id, client_id, rate_id, status, decided_date) values (?,?,?,'Approved',?)",
                        (user_id, client_id, rate[0], datetime.date.today().isoformat()))
            msg = "Person added to the client's list. They can book to its CTRs now."
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
                text = "That person is already on this client's list."
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
            manager = int(g("line_manager_id")) if g("line_manager_id") else None
            if manager == i:
                raise ValueError("A person cannot be their own line manager.")
            con.execute("update app_user set name=?, employment_type=?, role_id=?, permission=?, end_date=?, line_manager_id=? where user_id=?",
                        (need_text(g("name"), "Name"), g("employment_type"), int(g("role_id")), g("permission"),
                         need_date(g("end_date"), "End date", False), manager, i))
        elif t == "roles" and g("old_family"):
            if not g("new_family"):
                return view_admin(con, u, t, ["Give the family a name."], q={"f_family": [g("old_family")]})
            new = g("new_family")
            con.execute("update role set family=? where family=?", (new, g("old_family")))
            con.commit()
            return view_admin(con, u, t, ["Family renamed."], ok=True, q={"f_family": [new]})
        elif t == "roles":
            con.execute("update role set skill_code=?, name=?, family=?, active=? where role_id=?",
                        (need_text(g("skill_code"), "Skill code"), need_text(g("name"), "Job title"), need_text(g("family"), "Job family"), int(g("active")), i))
            con.commit()
            return view_admin(con, u, t, ["Saved."], ok=True, q={"f_family": [g("family")]})
        elif t == "locations":
            others = con.execute("select count(*) from location where active=1 and location_id<>?", (i,)).fetchone()[0]
            if int(g("active")) == 0 and others == 0:
                raise ValueError("At least one location must stay active.")
            con.execute("update location set name=?, active=? where location_id=?", (need_text(g("name"), "Location name"), int(g("active")), i))
        elif t == "clients":
            con.execute("update client set approver_user_id=?, framework_ref=? where client_id=?",
                        (int(g("approver_user_id")) if g("approver_user_id") else None, g("framework_ref") or None, i))
        elif t in ("scopes", "internal"):
            con.execute("update scope set description=?, status=? where scope_id=?", (need_text(g("description"), "Description"), g("status"), i))
            con.execute("delete from scope_approver where scope_id=?", (i,))
            for person in form.get("secondary", []):
                con.execute("insert or ignore into scope_approver (scope_id, user_id) values (?,?)", (i, int(person)))
        elif t == "projects":
            con.execute("update project set name=?, status=? where project_id=?", (need_text(g("name"), "Project name"), g("status"), i))
        elif t == "phases":
            b_from, b_to = need_date(g("book_from"), "Bookable from", False), need_date(g("book_to"), "Bookable until", False)
            if b_from and b_to and b_to < b_from:
                raise ValueError("Bookable until cannot be before bookable from.")
            terms = g("payment_terms") or "Reimbursable"
            if terms not in ("Reimbursable", "Budget", "Lump Sum"):
                raise ValueError("Payment terms must be Reimbursable, Budget or Lump Sum.")
            con.execute("update phase set name=?, status=?, description=?, wbs=?, cbs=?, abs_code=?, restricted=?, book_from=?, book_to=?, "
                        "ctr_code=?, objectives=?, inputs=?, scope_of_work=?, assumptions=?, exclusions=?, prepared_by=?, checked_by=?, expenses=?, payment_terms=?, basis=? "
                        "where phase_id=?",
                        (need_text(g("name"), "CTR name"), g("status"), g("description") or None, g("wbs") or None, g("cbs") or None,
                         g("abs_code") or None, 1 if g("restricted") == "1" else 0, b_from, b_to,
                         g("ctr_code") or None, g("objectives") or None, g("inputs") or None, g("scope_of_work") or None, g("assumptions") or None,
                         g("exclusions") or None, g("prepared_by") or None, g("checked_by") or None, g("expenses") or "Reimbursable", terms, g("basis") or None, i))
            con.execute("delete from phase_person where phase_id=?", (i,))
            for person in form.get("person", []):
                con.execute("insert or ignore into phase_person (phase_id, user_id) values (?,?)", (i, int(person)))
        else:
            raise ValueError("Unknown form.")
        con.commit()
        return view_admin(con, u, "scopes" if t in ("projects", "phases") else t, ["Saved."], ok=True)
    except ValueError as e:
        return view_admin_edit(con, u, t, i, [str(e)])
    except sqlite3.DatabaseError as e:
        text = "That already exists (it must be unique)." if "UNIQUE" in str(e) else str(e)
        return view_admin_edit(con, u, t, i, [text])


def act_allowance_rates(con, form):
    u = int(form["u"][0])
    if not can_use_tab(con, u, "allowance_rates"):
        return view_admin(con, u, "allowance_rates")
    try:
        start = need_date(form.get("effective_from", [""])[0], "Effective from")
        changed = 0
        for key, vals in form.items():
            if not key.startswith("r_"):
                continue
            aid = int(key.split("_")[1])
            raw = vals[0].strip()
            try:
                amount = float(raw)
                if amount < 0:
                    raise ValueError
            except ValueError:
                raise ValueError("'%s' is not a rate: use a number of pounds, 0 or more." % raw)
            cur = con.execute("select amount from allowance_rate where allowance_id=? and effective_from<=? order by effective_from desc limit 1", (aid, start)).fetchone()
            if (cur is not None and abs(cur[0] - amount) < 1e-9) or (cur is None and amount == 0):
                continue
            con.execute("insert into allowance_rate (allowance_id, amount, effective_from) values (?,?,?)", (aid, amount, start))
            changed += 1
        con.commit()
        return view_admin(con, u, "allowance_rates", ["%d rate%s saved, from %s." % (changed, "" if changed == 1 else "s", start) if changed else "Nothing changed."], ok=True)
    except ValueError as e:
        con.rollback()
        return view_admin(con, u, "allowance_rates", [str(e)])
    except sqlite3.IntegrityError:
        con.rollback()
        return view_admin(con, u, "allowance_rates", ["A rate already exists from that date. Rates are never edited: choose a later date."])


def act_paaf(con, form):
    u, pid, action = int(form["u"][0]), int(form["paaf_id"][0]), form["action"][0]
    if not can_use_tab(con, u, "paafs"):
        return view_admin(con, u, "paafs")
    q = form  # the list's filters and order come back with the form
    g = lambda k: form.get(k, [""])[0].strip()
    try:
        p = con.execute("select * from paaf where paaf_id=?", (pid,)).fetchone()
        if not p:
            raise ValueError("That entry was not found.")
        if action == "Change rate":
            rate = con.execute("select rate_id from rate where rate_id=? and client_id=?", (int(g("rate_id") or 0), p["client_id"])).fetchone()
            if not rate:
                raise ValueError("Choose one of this client's rates.")
            con.execute("update paaf set rate_id=? where paaf_id=?", (rate[0], pid))
            msg = "Entry %d now uses the chosen rate." % pid
        elif action == "Save end date":
            end = need_date(g("expiry_date"), "End date", False)
            con.execute("update paaf set expiry_date=? where paaf_id=?", (end, pid))
            msg = "Entry %d now ends on %s." % (pid, end) if end else "Entry %d has no end date." % pid
        else:
            raise ValueError("Unknown action.")
        con.commit()
        return view_admin(con, u, "paafs", [msg], ok=True, q=q)
    except ValueError as e:
        return view_admin(con, u, "paafs", [str(e)], q=q)
    except sqlite3.DatabaseError as e:
        return view_admin(con, u, "paafs", [str(e)], q=q)


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
        elif action == "reopen":
            con.execute("update period set status='Open', closed_by=NULL, closed_at=NULL where week_start=?", (ws,))
            msg = ["Week %s reopened." % ws]
        else:
            con.execute("update period set status='Closed', closed_by=?, closed_at=? where week_start=?", (u, stamp(), ws))
            msg = ["Week %s closed." % ws]
        con.commit()
        return view_admin(con, u, "periods", msg, ok=True)
    except sqlite3.DatabaseError as e:
        return view_admin(con, u, "periods", ["%s" % e])


def view_audit(con, u, q=None):
    if permission_of(con, u) not in ("controller", "admin"):  # who owns the audit trail is still to be confirmed with the cost team
        return no_access(con, u, "Audit trail")
    try:
        person = int((q or {}).get("person", [""])[0])
    except ValueError:
        person = None
    rows = con.execute(
        "select a.audit_id, a.changed_at, ch.name as changed_by, a.action, bu.name as booked_by, s.scope_code, t.work_date, "
        "a.old_status, a.new_status, a.old_hours, a.new_hours, a.reason from time_entry_audit a "
        "join time_entry t on t.entry_id=a.entry_id join app_user bu on bu.user_id=t.user_id "
        "left join app_user ch on ch.user_id=a.changed_by join scope s on s.scope_id=t.scope_id "
        "where (? is null or t.user_id=?) order by a.audit_id desc limit 200", (person, person)).fetchall()
    heads = ["#", "When", "Changed by", "Action", "Booked by", "Scope", "Date", "Old status", "New status", "Old hours", "New hours", "Reason"]
    arows = con.execute(
        "select a.audit_id, a.changed_at, ch.name as changed_by, a.action, bu.name as person, at.name as allowance, a.work_date, a.old_status, a.new_status, a.reason "
        "from allowance_audit a left join app_user ch on ch.user_id=a.changed_by join app_user bu on bu.user_id=a.user_id "
        "join allowance_type at on at.allowance_id=a.allowance_id where (? is null or a.user_id=?) order by a.audit_id desc limit 200", (person, person)).fetchall()
    body = ('<form method="get" action="/audit">%s<p>Person whose hours changed %s <input type="submit" value="Filter"> '
            '<small>Showing the latest 200 changes%s.</small></p></form>' % (
                hidden(u=u), sel("person", opts_users(con), person, True, "Everyone"), " for this person" if person else "")) +         table(heads, [[fmt(v) if i in (9, 10) else fmt_time(v) if i == 1 else v for i, v in enumerate(r)] for r in rows])
    body += "<h4>Allowances</h4>" + table(["#", "When", "Changed by", "Action", "Booked by", "Allowance", "Date", "Old status", "New status", "Reason"],
                                           [[fmt_time(v) if i == 1 else v for i, v in enumerate(r)] for r in arows])
    return layout(con, u, "Audit trail (latest 200)", body)


# ---------- server ----------
# ---------- The web layer (Flask) ----------
# Every page is a function above that takes the database connection and returns HTML. The routes below only read the request,
# open the database, call the right function and send the result. Query strings and forms are read with parse_qs exactly as before
# (dicts of lists), so the page functions did not change.
flask_app = Flask(__name__, static_folder=None)
flask_app.url_map.strict_slashes = False
logging.getLogger("werkzeug").setLevel(logging.ERROR)  # no request log lines on the console


def html_response(text, code=200):
    return Response(text, status=code, mimetype="text/html")


def query():
    return parse_qs(request.query_string.decode("utf-8"))


def posted():
    return parse_qs(request.get_data(as_text=True), keep_blank_values=True)


def user_id(con, q):
    try:
        return int(q["u"][0])
    except (KeyError, ValueError):
        return con.execute("select user_id from app_user order by user_id limit 1").fetchone()[0]


def with_db(fn):
    """Open the database for one request, make sure the current week exists, and close it afterwards."""
    @functools.wraps(fn)
    def wrapper():
        con = db()
        try:
            ensure_current_period(con)
            return fn(con)
        finally:
            con.close()
    return wrapper


def page(rule, *, methods=("GET",)):
    """Register a route whose function receives (con, u, q) for a page, or (con, form) for a posted form."""
    def deco(fn):
        @with_db
        def view(con):
            if request.method == "POST":
                return html_response(fn(con, posted()))
            q = query()
            return html_response(fn(con, user_id(con, q), q))
        view.__name__ = "view_" + fn.__name__
        flask_app.add_url_rule(rule, endpoint=rule, view_func=view, methods=list(methods))
        return fn
    return deco


@flask_app.after_request
def security_headers(resp):
    """Browser protections on every response: no content sniffing, no framing (clickjacking), no referrer leaks, and a policy that only
    lets the page load its own resources (the page's own inline style and script are the only inline code it has)."""
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                            "img-src 'self' data:; font-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
    resp.headers.setdefault("Cache-Control", "no-store")
    return resp


@flask_app.errorhandler(404)
@flask_app.errorhandler(405)
def not_found(_):
    return html_response("<p>Not found</p>", 404)


def _serve_asset(path):
    def view():
        try:
            with open(ASSETS[path][0], "rb") as f:
                data = f.read()
        except OSError:
            return html_response("<p>Not found</p>", 404)
        return Response(data, mimetype=ASSETS[path][1], headers={"Cache-Control": "public, max-age=86400"})
    view.__name__ = "asset_" + path.strip("/").replace("/", "_").replace(".", "_")
    return view


for _path in ASSETS:
    flask_app.add_url_rule(_path, endpoint=_path, view_func=_serve_asset(_path))


@flask_app.route("/")
@flask_app.route("/timesheet")
@with_db
def timesheet_page(con):
    q = query()
    u = user_id(con, q)
    try:
        w = monday(datetime.date.fromisoformat(q["w"][0])) if "w" in q else monday(datetime.date.today())
    except ValueError:
        w = monday(datetime.date.today())
    adds = parse_adds(q.get("add", []))
    if "add_phase" in q and "add_role" in q:
        adds += parse_adds(["%s:%s:%s:%s" % (q["add_phase"][0], q["add_role"][0], q.get("add_loc", ["1"])[0], "1" if "add_ot" in q else "0")])
    aadds = parse_pairs(q.get("aadd", []))
    if "aadd_phase" in q and "aadd_type" in q:
        aadds += parse_pairs(["%s:%s" % (q["aadd_phase"][0], q["aadd_type"][0])])
    return html_response(view_timesheet(con, u, w, adds=adds, aadds=list(dict.fromkeys(aadds))))


page("/approvals")(lambda con, u, q: view_approvals(con, u, q=q))
page("/missing")(lambda con, u, q: view_missing(con, u, q))
page("/reopen")(lambda con, u, q: view_reopen(con, u, q))
page("/reports")(lambda con, u, q: view_reports(con, u, q))
page("/reports/grid")(lambda con, u, q: view_grid(con, u, q))
page("/ctr")(lambda con, u, q: view_ctr(con, u, q))
page("/admin")(lambda con, u, q: view_admin(con, u, q.get("t", [""])[0], q=q))
page("/admin/edit")(lambda con, u, q: view_admin_edit(con, u, q.get("t", [""])[0], int(q.get("id", ["0"])[0])))
page("/audit")(lambda con, u, q: view_audit(con, u, q))


@flask_app.route("/reports/grid.xlsx")
@flask_app.route("/reports/period.xlsx")
@with_db
def excel_download(con):
    q = query()
    u = user_id(con, q)
    if permission_of(con, u) == "admin":
        return html_response(no_access(con, u, "Reports"), 403)
    rv = report_view(con, u)
    grid = request.path.endswith("grid.xlsx")
    if grid:
        rows, sheet = grid_sheet(grid_data(con, u, q, rv)), "Timesheet grid"
    else:
        rows, sheet = period_sheet(con, u, q, rv)
    bold = (1, len(rows)) if len(rows) > 1 else (1,)
    return Response(xlsx_bytes(sheet, rows, bold), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="timewriting-%s.xlsx"' % ("grid" if grid else "period")})


page("/timesheet/save", methods=("POST",))(lambda con, form: save_timesheet(con, form))
page("/timesheet/copy", methods=("POST",))(lambda con, form: copy_week(con, form))
page("/reopen/act", methods=("POST",))(lambda con, form: reopen_week(con, form))
page("/approvals/act", methods=("POST",))(lambda con, form: act_approval(con, form))
page("/admin/period", methods=("POST",))(lambda con, form: act_period(con, form))
page("/admin/add", methods=("POST",))(lambda con, form: admin_add(con, form))
page("/admin/save", methods=("POST",))(lambda con, form: admin_save(con, form))
page("/admin/paaf", methods=("POST",))(lambda con, form: act_paaf(con, form))
page("/admin/allowance_rates", methods=("POST",))(lambda con, form: act_allowance_rates(con, form))


def make_server(port=8000):
    """A threaded development server on this machine (Werkzeug's, the one Flask itself uses). It has serve_forever, shutdown and server_address."""
    return make_wsgi_server("127.0.0.1", port, flask_app, threaded=True)


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
