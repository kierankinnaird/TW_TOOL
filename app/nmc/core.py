"""Shared foundation: database access, formatting, the approval rules, the page layout and the form helpers every area uses."""
import datetime
import html
import os
import sqlite3


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


DB_PATH = os.environ.get("TW_DB", os.path.join(ROOT, "build", "timewriting.db"))


# Databases built before this version lack columns and rules the app relies on. Matches PRAGMA user_version in db/schema.sql.
REQUIRED_SCHEMA = 17


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
.wk{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem 1.2rem;margin:0 0 .6rem}
.wk .cur-week{font-size:1.05rem;font-weight:500}
.wk form{margin:0 0 0 auto;display:flex;align-items:center;gap:.4rem}
.wk .nav a{display:inline-block;padding:.3rem .7rem;background:var(--white);border:1px solid var(--line)}
.wk .nav a:hover{border-color:var(--orange)}
.stat{display:inline-block;margin:0 0 .8rem;padding:.35rem .8rem;background:var(--white);border:1px solid var(--line);border-left:3px solid var(--orange)}
table.ts{width:100%}
table.ts th{text-align:center}
table.ts th.bkh{text-align:left}
table.ts td.bk{min-width:17rem}
table.ts td.bk b{font-weight:500}
table.ts .sub{color:var(--grey-dark);font-size:.8rem}
table.ts .chips{font-size:.8rem;color:var(--grey-dark)}
table.ts .chips b{color:var(--orange);font-weight:500}
table.ts .we{background:#f1f1f2}
table.ts th.we{background:var(--ink)}
table.ts td.c{width:4.2rem}
table.ts input[type=text]{width:3.4rem}
table.ts tr.sum td{background:#fafafa;border-bottom:1px solid var(--line)}
table.ts tr.tot td{background:var(--white);border-top:2px solid var(--black);font-size:1rem}
.actions{position:sticky;bottom:0;display:flex;align-items:center;gap:.8rem;margin:1rem -1.4rem -1.2rem;padding:.7rem 1.4rem;background:var(--white);border-top:1px solid var(--line)}
.actions p{margin:0}
.addbox{margin-top:1.4rem;padding:.9rem 1.1rem;background:var(--bg);border:1px solid var(--line)}
.addbox p{margin:.3rem 0}
.addbox details{margin:.5rem 0 0}
.addbox summary{cursor:pointer;color:var(--grey-dark);font-size:.9rem}
.addbox summary:hover{color:var(--orange)}
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
           "Admin": "admin", "Edit": "admin", "Audit": "audit", "Dashboard": "dashboard"}


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
    if perm == "controller":  # budget against actual hours, charts
        items.append(("dashboard", "Dashboard", "/dashboard?u=%d" % u))
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


# ---------- Reports ----------
def sub_row(cells, right_from=5):
    return "<tr>%s</tr>" % "".join('<td%s><b>%s</b></td>' % (' align="right"' if i >= right_from and c != "" else "", esc(c))
                                   for i, c in enumerate(cells))


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
