"""Shared foundation: database access, formatting, the approval rules, the page layout and the form helpers every area uses."""
import datetime
import html
import os
import sqlite3


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


DB_PATH = os.environ.get("TW_DB", os.path.join(ROOT, "build", "timewriting.db"))


# Databases built before this version lack columns and rules the app relies on. Matches PRAGMA user_version in db/schema.sql.
REQUIRED_SCHEMA = 22


# Static files the app may serve. Only these exact URLs, never a path built from the request.
def asset_url(path):
    """A static file's address with its last-changed time on the end, so a browser fetches the new file when it changes instead of using a cached one."""
    try:
        return "%s?v=%d" % (path, os.path.getmtime(ASSETS[path][0]))
    except (KeyError, OSError):
        return path


ASSETS = {"/static/TASAOrbiterVF.woff2": (os.path.join(ROOT, "app", "static", "TASAOrbiterVF.woff2"), "font/woff2"),
          "/static/nmc-logo.png": (os.path.join(ROOT, "app", "static", "nmc-logo.png"), "image/png"),
          "/static/nmc-tab-icon.png": (os.path.join(ROOT, "app", "static", "nmc-tab-icon.png"), "image/png")}


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
    """A stored timestamp as dd/mm/yyyy HH:MM:SS (the way dates read everywhere on screen, no fractions of a second)."""
    v = "" if v is None else str(v)[:19]
    return "%s %s" % (dmy(v[:10]), v[11:]) if len(v) >= 16 else dmy(v)


def dmy(v, short=False):
    """A stored date (yyyy-mm-dd) as dd/mm/yyyy, or dd/mm when short. Anything that is not a date comes back as it was."""
    v = "" if v is None else str(v)
    if len(v) >= 10 and v[4] == "-" and v[7] == "-":
        return "%s/%s" % (v[8:10], v[5:7]) if short else "%s/%s/%s" % (v[8:10], v[5:7], v[0:4])
    return v


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
# SQL fragments for hours (entry t on scope s). "I may approve it" means: I am an approver of the
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
    return n


STYLE = """
@font-face{font-family:"TASA Orbiter";font-style:normal;font-weight:400 600;font-display:swap;
  src:local("TASA Orbiter"),local("TASAOrbiter"),url(__FONT__) format("woff2")}
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
.brand{display:flex;align-items:center;font-size:1.1rem;font-weight:500;letter-spacing:-.02em}
.brand img{height:34px;width:auto;display:block;margin-right:.9rem}
.who{display:flex;align-items:center;gap:.6rem;font-size:.85rem;color:var(--grey-light);margin:0}
.top select{background:var(--ink);color:var(--white);border:1px solid rgba(255,255,255,.25)}
.top input[type=submit]{background:transparent;color:var(--white);border:1px solid rgba(255,255,255,.3)}
.top input[type=submit]:hover{background:var(--orange);border-color:var(--orange);color:var(--white)}
.shell{display:flex;align-items:flex-start;gap:1.5rem;padding:1.5rem;max-width:1480px;margin:0 auto}
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
.tools{display:flex;justify-content:flex-end;align-items:center;gap:.6rem;margin:0 0 .6rem}
dialog.dlg{border:1px solid var(--black);padding:0;width:min(640px,92vw);background:var(--white);color:var(--black)}
dialog.dlg::backdrop{background:rgba(15,15,18,.5)}
.dlg-in{padding:1.2rem 1.5rem}
.dlg-in h4{margin:0 0 .4rem;font-size:1.15rem}
.dlg-in select[size],.dlg-in input[type=text]#addq{width:100%}
.dlg-in select[size]{height:auto;padding:.3rem}
.dlg-actions{display:flex;gap:.6rem;align-items:center;margin-top:.9rem}
.dlg-in input[type=button]{background:var(--white);color:var(--black);border:1px solid var(--grey-light)}
table.ts tr.otrow td{background:#fff8f2}
table.ts tr.otrow td.we{background:#f6ece4}
button.delrow{background:none;border:0;padding:0 .4rem;font:inherit;font-size:1.3rem;line-height:1;color:var(--grey-mid);cursor:pointer;height:auto}
button.delrow:hover{color:#b3261e}
table.ts a.addot{font-size:.8rem;color:var(--orange)}
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
input[type=text],input[type=date],input[type=month],select{font:inherit;font-size:.9rem;height:2rem;padding:0 .6rem;color:var(--black);background:var(--white);
  border:1px solid var(--grey-light);}
input[type=text]:focus,input[type=date]:focus,input[type=month]:focus,select:focus{outline:2px solid var(--orange);outline-offset:1px;border-color:var(--orange)}
input.bad{border-color:#b3261e;background:#fdecea}
td.over{color:#b3261e}
p.xl{margin:.3rem 0 .4rem}
p.xl a{display:inline-block;padding:.2rem .7rem;font-size:.8rem;color:var(--grey-dark);border:1px solid var(--line);background:var(--white);text-decoration:none}
p.xl a:hover{color:var(--orange);border-color:var(--orange)}
button[name=approve_person]{display:block;margin:.3rem 0 0;font:inherit;font-size:.78rem;padding:.15rem .6rem;border:1px solid rgba(30,30,36,.3);background:transparent;cursor:pointer;white-space:nowrap}
button[name=approve_person]:hover{border-color:var(--orange);color:var(--orange)}
.rgroup{display:flex;flex-wrap:wrap;align-items:center;gap:.4rem;margin:.3rem 0}
.rgroup .gl{flex:0 0 9rem;font-size:.8rem;color:var(--grey-dark)}
.mnav{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem 1.1rem;margin:0 0 .6rem}
.mnav form{display:flex;align-items:center;gap:.4rem;margin:0}
.mnav .cur-month{font-size:1.05rem;font-weight:500}
details.note{margin:.4rem 0 .8rem}
details.note summary{cursor:pointer;font-size:.8rem;color:var(--grey-dark)}
details.note summary:hover{color:var(--orange)}
details.note p{margin:.3rem 0}
.empty{padding:1rem 1.2rem;background:var(--bg);border:1px dashed var(--grey-light);color:var(--grey-dark)}
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


# Long grey explanations (more than a couple of lines) fold away behind "What is this?", so a page leads with its figures. Nothing is removed.
NOTES_JS = r"""<script>
(function(){var main=document.querySelector('main');if(!main)return;
main.querySelectorAll('p').forEach(function(p){var sm=p.children.length===1&&p.firstElementChild.tagName==='SMALL'?p.firstElementChild:null;
if(!sm||p.textContent.trim().length<170||p.closest('.msg,.addbox,dialog,td,th,details'))return;
var d=document.createElement('details'),s=document.createElement('summary');d.className='note';s.textContent='What is this?';d.appendChild(s);
p.parentNode.insertBefore(d,p);d.appendChild(p);});})();
</script>"""


# Puts a "Download table as Excel" link above every table of figures on a page (not tables you type into, or one-line layout tables).
# The browser sends the table's text to /export/table.xlsx and gets a spreadsheet back, so what is downloaded is what is on screen.
EXPORT_JS = r"""<script>
(function(){
var main=document.querySelector('main');if(!main)return;
function text(c){var k=c.cloneNode(true);k.querySelectorAll('.tg,select,input,textarea,button,script').forEach(function(e){e.remove()});
return k.textContent.replace(/\s+/g,' ').trim().replace(/ Open CTR$/,'');}
function editable(t){var f=t.querySelectorAll('input,select,textarea');for(var i=0;i<f.length;i++){
if(f[i].tagName!=='INPUT'||!/^(hidden|submit|button|checkbox|radio)$/.test(f[i].type))return true;}return false;}
function title(t){var e=t;while(e&&e!==main){var p=e.previousElementSibling;while(p){if(/^H[1-4]$/.test(p.tagName))return p.textContent.trim();p=p.previousElementSibling;}e=e.parentElement;}
var h=main.querySelector('h1');return h?h.textContent.trim():'Table';}
main.querySelectorAll('table').forEach(function(t){
if(t.getAttribute('border')==='0'||t.classList.contains('ts')||t.querySelector('table')||t.rows.length<2||!t.querySelector('th')||t.rows[0].cells.length<2||editable(t))return;
var panel=t.closest('.rpt');if(panel&&panel.querySelector('a[href*="/reports/export.xlsx"],a[href*="period.xlsx"]')&&panel.querySelectorAll('table').length===1)return;
var name=title(t),p=document.createElement('p'),a=document.createElement('a');
a.href='#';a.textContent='Download table as Excel';p.className='xl';p.appendChild(a);
a.onclick=function(ev){ev.preventDefault();var rows=[];
for(var i=0;i<t.rows.length;i++){var r=[],cs=t.rows[i].cells;for(var j=0;j<cs.length;j++){r.push(text(cs[j]));for(var k=1;k<(cs[j].colSpan||1);k++)r.push('');}rows.push(r);}
fetch('/export/table.xlsx',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:name,rows:rows})})
.then(function(r){return r.blob();}).then(function(b){var u=URL.createObjectURL(b),d=document.createElement('a');
d.href=u;d.download=(name.replace(/[^A-Za-z0-9]+/g,'-').replace(/^-|-$/g,'')||'table')+'.xlsx';document.body.appendChild(d);d.click();d.remove();setTimeout(function(){URL.revokeObjectURL(u);},1000);});};
t.parentNode.insertBefore(p,t);});
})();
</script>"""


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
        "<title>%s - NMC Timewriting</title><link rel='icon' type='image/png' href='%s'><style>%s</style></head><body>"
        '<header class="top"><div class="brand"><img src="%s" alt="NMC Energy"></div>'
        '<form class="who" method="get" action="/timesheet">Signed in as '
        '<select name="u">%s</select><input type="submit" value="Switch user"></form></header>'
        '<div class="shell"><nav class="side">%s<div class="me">%s</div></nav>'
        '<main><h1>%s</h1><div class="card">%s</div></main></div>%s</body></html>'
    ) % (esc(title), asset_url("/static/nmc-tab-icon.png"), page_style(), asset_url("/static/nmc-logo.png"), opts, menu, who, esc(title), body, NOTES_JS + EXPORT_JS)


def page_style():
    return STYLE.replace("__FONT__", asset_url("/static/TASAOrbiterVF.woff2"))


def missing_page(what="That page"):
    """The page for an address that does not exist: the same header, with a way back, instead of a bare line of text."""
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            "<title>Page not found - NMC Timewriting</title><link rel='icon' type='image/png' href='%s'><style>%s</style></head><body>"
            '<header class="top"><div class="brand"><img src="%s" alt="NMC Energy"></div></header>'
            '<div class="shell"><main><h1>Page not found</h1><div class="card"><p>%s does not exist, or the address has changed.</p>'
            '<p><a href="/timesheet">Go to My Timesheet</a></p></div></main></div></body></html>'
            ) % (asset_url("/static/nmc-tab-icon.png"), page_style(), asset_url("/static/nmc-logo.png"), esc(what))


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
    """billable_only leaves out the Internal client, which has no rate card."""
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
