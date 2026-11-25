"""Shared foundation: database access, formatting, the approval rules, the page layout and the form helpers every area uses."""
import datetime
import html
import os
from . import pg


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


DB_URL = None   # the database to use; None means DATABASE_URL (see pg.database_url). Tests point it at a throwaway database.


# Databases built before this version lack columns and rules the app relies on. Matches the version in schema_info in db/schema.sql.
REQUIRED_SCHEMA = 27


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
    return pg.connect(DB_URL)


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
    con.execute("insert into period (week_start) values (?) on conflict do nothing", (monday(datetime.date.today()).isoformat(),))
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


# Internal codes that are absence rather than work: they are shown by their own name, without "Internal - " in front.
ABSENCE_CODES = ("Sick", "Compassionate")


# Who approves whose hours: each person's whole timesheet goes to their approver if one is named on their record, otherwise to their line manager
# (view v_person_approver). Someone with neither has no approver, and a cost controller approves their hours.
def is_approver(con, u):
    """Approver is not a permission: it means approving somebody's timesheet, as their line manager or as the approver named on their record."""
    return con.execute("select 1 from v_person_approver where approver_id=? limit 1", (u,)).fetchone() is not None


def has_team(con, u):
    """Is this person a line manager, or the named approver, of anyone? (They can look at their people's timesheets.)"""
    return con.execute("select 1 from app_user where line_manager_id=? or approver_id=? limit 1", (u, u)).fetchone() is not None


def can_see_reports(con, u):
    """Reports are for cost controllers and for people with a team (hours only, their team's). A standard user has the timesheet and its history."""
    return permission_of(con, u) == "controller" or has_team(con, u)


def approval_queue(con, u):
    """What this person approves, as SQL on time entries (t) with its arguments, or None if they approve nothing: the hours of the people they approve
    for, never their own. A cost controller also approves the hours of people who have nobody to approve for them."""
    mine = "exists (select 1 from v_person_approver pa where pa.user_id=t.user_id and pa.approver_id=?)"
    if permission_of(con, u) == "controller":
        return " and (%s or not exists (select 1 from v_person_approver pn where pn.user_id=t.user_id)) and t.user_id<>?" % mine, (u, u)
    if is_approver(con, u):
        return " and %s and t.user_id<>?" % mine, (u, u)
    return None


def may_action(con, u, eid):
    """Can this person approve or reject this entry? Its person's approver can (the database refuses their own hours); a cost controller can when the
    person has no approver."""
    q = approval_queue(con, u)
    if q is None:
        return False
    return con.execute("select 1 from time_entry t where t.entry_id=?" + q[0], (eid,) + tuple(q[1])).fetchone() is not None


def pending_for(con, u):
    queue = approval_queue(con, u)
    if queue is None:
        return 0
    return con.execute("select count(*) from time_entry t where t.status in ('Submitted','Edited')" + queue[0], queue[1]).fetchone()[0]


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
table.ts .pill{display:inline-block;margin:.15rem .3rem 0 0;padding:0 .55rem;font-size:.75rem;line-height:1.5rem;border:1px solid var(--grey-light);background:var(--white);color:var(--ink);white-space:nowrap}
table.ts .pill.ot{border-color:var(--orange);color:var(--orange)}
table.ts td.rs{width:9.5rem;white-space:nowrap}
table.ts select.rowsel.chg{border-color:var(--orange);box-shadow:0 0 0 1px var(--orange)}
table.ts select.rowsel{width:9rem;max-width:9rem;height:1.9rem;font-size:.8rem;padding:0 .2rem;text-overflow:ellipsis}
.pick2{display:flex;flex-wrap:wrap;gap:.6rem 1.4rem;margin:.6rem 0}
.pick2 label{display:flex;align-items:center;gap:.5rem;font-size:.85rem;color:var(--grey-dark)}
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
.apnav{display:flex;flex-wrap:wrap;align-items:center;gap:.4rem 1.2rem;margin:0 0 .5rem}
.apnav form{margin:0;display:inline-flex;flex-wrap:wrap;align-items:center;gap:.4rem;font-size:.85rem;color:var(--grey-dark)}
.apnav b{font-weight:500}
.apsum{margin:.2rem 0 .7rem}
table.ap{width:100%;margin:.2rem 0}
table.ap th.dy,table.ap td.dy{text-align:center;width:3.6rem;padding-left:.2rem;padding-right:.2rem}
table.ap td.zero{color:var(--grey-light)}
table.ap .sub{display:block;font-size:.78rem;font-weight:400;color:var(--grey-dark)}
table.ap td{vertical-align:top}
table.ap tr.drow:hover td{background:#fafafa}
table.ap tr.drow>td{background:#fafafa;padding:.2rem .6rem .8rem}
table.ap tr.drow table{margin:0}
.badge{display:inline-block;font-size:.75rem;padding:.05rem .5rem;border:1px solid var(--line);margin:0 .3rem .2rem 0;white-space:nowrap}
.badge.wait{background:var(--tint);border-color:var(--orange)}
.badge.done{color:var(--grey-dark)}
.apact{position:sticky;bottom:0;background:var(--white);border-top:2px solid var(--black);padding:.6rem 0;display:flex;flex-wrap:wrap;align-items:center;gap:.5rem 1.6rem;margin-top:.6rem}
.apact small{color:var(--grey-dark)}
.scrollbox{max-height:27rem;overflow:auto;border:1px solid var(--line);margin:.3rem 0}
.scrollbox table{margin:0;width:100%}
.scrollbox th{position:sticky;top:0;z-index:1}
p.pager{margin:.3rem 0 1rem}
p.pager a,p.pager b{display:inline-block;min-width:1.6rem;text-align:center;padding:.1rem .3rem}
p.pager b{background:var(--black);color:var(--white);font-weight:450}
.picker{max-width:46rem}
.pk-q{width:100%;max-width:22rem;margin:0 0 .5rem}
.pk-chosen{display:flex;flex-wrap:wrap;gap:.3rem;margin:0 0 .5rem}
.chip{font:inherit;font-size:.8rem;padding:.15rem .6rem;border:1px solid var(--orange);background:var(--tint);color:var(--ink);cursor:pointer}
.chip:hover{background:var(--orange);color:var(--white)}
.pk-none{font-size:.8rem;color:var(--grey-mid)}
.pk-list{display:grid;grid-template-columns:repeat(auto-fill,minmax(13rem,1fr));gap:.1rem .8rem;max-height:11rem;overflow:auto;border:1px solid var(--line);padding:.4rem .6rem;background:var(--white)}
.pk-list label{display:flex;align-items:center;gap:.4rem;font-size:.85rem;padding:.1rem 0;cursor:pointer}
.pk-list label[hidden]{display:none}
.pk-list label:hover{color:var(--orange)}
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
table.cal{width:100%;border-collapse:collapse;table-layout:fixed;margin:.4rem 0}
table.cal th{background:var(--black);color:var(--white);font-weight:450;text-align:left;padding:.4rem .6rem;font-size:.8rem}
table.cal td{height:4.6rem;padding:0;border:1px solid var(--line);vertical-align:top;background:var(--white)}
table.cal td a{display:block;height:100%;padding:.3rem .5rem;text-decoration:none;color:var(--black)}
table.cal td a:hover{background:var(--tint)}
table.cal td i{font-style:normal;font-size:.72rem;color:var(--grey-mid);display:block}
table.cal td b{font-size:1.15rem;font-weight:500}
table.cal td span{font-size:.7rem;color:var(--grey-dark)}
table.cal td.we{background:#f6f6f7}
table.cal td.out{opacity:.45}
table.cal td.today{outline:2px solid var(--orange);outline-offset:-2px}
table.cal td.ok,.legend span.ok{background:#e8f3e8}
table.cal td.sub,.legend span.sub{background:var(--tint)}
table.cal td.draft,.legend span.draft{background:repeating-linear-gradient(135deg,#fff,#fff 5px,#ececee 5px,#ececee 8px)}
table.cal td.back,.legend span.back{background:#fdecea}
table.cal td.wt{background:var(--bg);font-size:.8rem;text-align:center;padding:.3rem;color:var(--grey-dark)}
table.cal td.wt a{display:inline;padding:0;text-decoration:underline}
.legend{display:flex;flex-wrap:wrap;gap:.5rem;font-size:.8rem}
.legend span{padding:.15rem .6rem;border:1px solid var(--line)}
.msg{margin:0 0 1rem;padding:.7rem 1rem;background:var(--bg);border-left:4px solid var(--black)}
.msg.bad{border-left-color:var(--orange);background:var(--tint)}
.msg p{margin:0 0 .3rem}
.msg ul{margin:0;padding-left:1.2rem}
.tg{color:var(--orange)}
@media (max-width:820px){.shell{flex-direction:column}.side{position:static;flex:none;width:100%}.top{flex-wrap:wrap}}
"""


NAV_KEY = {"My": "timesheet", "Timesheet": "history", "Approvals": "approvals", "Missing": "missing", "Reopen": "reopen", "Reports": "reports", "CTR": "reports",
           "Admin": "admin", "Edit": "admin", "Audit": "audit", "Dashboard": "dashboard", "Team": "team"}


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
if(t.getAttribute('border')==='0'||t.classList.contains('ts')||t.classList.contains('cal')||t.classList.contains('ap')||t.querySelector('table')||t.rows.length<2||!t.querySelector('th')||t.rows[0].cells.length<2||editable(t))return;
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
    n_back, n_open = con.execute("select count(*) filter (where reject_reason is not null), count(*) filter (where reopen_reason is not null) from time_entry "
                                 "where user_id=? and status='Draft'", (u,)).fetchone()
    note = ", ".join(x for x in ("%d sent back" % n_back if n_back else "", "%d reopened" % n_open if n_open else "") if x)
    items = [] if not me["books_time"] else [("timesheet", "My Timesheet%s" % (" (%s)" % note if note else ""), "/timesheet?u=%d" % u),
                                        ("history", "Timesheet history", "/history?u=%d" % u)]  # only people set up to write time have a timesheet (the shared IT account is not)
    if approval_queue(con, u) is not None:
        items.append(("approvals", "Approvals (%d)" % pending_for(con, u), "/approvals?u=%d" % u))
    if perm == "controller" or has_team(con, u):  # who has not completed a timesheet: everyone for cost controllers, their own people for line managers and approvers
        items.append(("team", "My team", "/team?u=%d" % u))
        items.append(("missing", "Missing timesheets", "/missing?u=%d" % u))
    if perm == "controller":  # reopening a person's submitted or approved timesheet
        items.append(("reopen", "Reopen timesheet", "/reopen?u=%d" % u))
    if perm == "controller":  # budget against actual hours, charts
        items.append(("dashboard", "Dashboard", "/dashboard?u=%d" % u))
    if can_see_reports(con, u):  # cost controllers and approvers; standard users have the timesheet and its history, the IT admin neither
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
    """Anyone can be a line manager or be named as someone's approver."""
    return [tuple(r) for r in con.execute("select user_id, name from app_user order by name")]


PICKER_JS = r"""<script>
(function(){if(window.__pk)return;window.__pk=1;
document.querySelectorAll('.picker').forEach(function(box){
var list=box.querySelector('.pk-list'),labels=[].slice.call(list.children),chips=box.querySelector('.pk-chosen'),q=box.querySelector('.pk-q');
q.hidden=false;chips.hidden=false;
function nm(l){return l.textContent.trim();}
function refresh(){chips.textContent='';var n=0,term=q.value.trim().toLowerCase();
labels.forEach(function(l){var c=l.querySelector('input');
if(c.checked){n++;var b=document.createElement('button');b.type='button';b.className='chip';b.title='Remove';b.textContent=nm(l)+' ×';b.onclick=function(){c.checked=false;refresh();};chips.appendChild(b);}
l.hidden=c.checked||(term&&nm(l).toLowerCase().indexOf(term)<0);});
if(!n){var e=document.createElement('span');e.className='pk-none';e.textContent=box.getAttribute('data-none')||'Nobody chosen';chips.appendChild(e);}}
list.addEventListener('change',refresh);q.addEventListener('input',refresh);
q.addEventListener('keydown',function(ev){if(ev.key==='Enter'){ev.preventDefault();var v=labels.filter(function(l){return !l.hidden;});if(v.length===1){v[0].querySelector('input').checked=true;q.value='';refresh();}}});
refresh();});})();
</script>"""


def people_picker(name, people, chosen, none="Nobody chosen"):
    """A list of people to tick (checkboxes posted under name). With scripting it becomes a search box: type to find someone, click to add them,
    and the people chosen show above as buttons that remove them. Without scripting it is the plain list of tick boxes."""
    boxes = "".join('<label><input type="checkbox" name="%s" value="%d"%s> %s</label>' % (name, pid, " checked" if pid in chosen else "", esc(pname)) for pid, pname in people)
    return ('<div class="picker" data-none="%s"><input type="text" class="pk-q" placeholder="Search people to add" autocomplete="off" aria-label="Search people" hidden>'
            '<div class="pk-chosen" hidden></div><div class="pk-list">%s</div></div>%s' % (esc(none), boxes, PICKER_JS))


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


USER_AUDIT_FIELDS = (("name", "Name"), ("email", "Email"), ("employment_type", "Employment"), ("role_id", "Role"), ("permission", "Permission"),
                     ("start_date", "Start date"), ("end_date", "End date"), ("line_manager_id", "Line manager"), ("approver_id", "Approver"),
                     ("default_location_id", "Default location"), ("hours_per_week", "Hours per week"), ("books_time", "Writes time"),
                     ("entra_object_id", "Microsoft account"))


def user_audit_text(con, field, value):
    """A stored value of a person as readable text for the audit trail."""
    if value is None or value == "":
        return "none"
    if field == "role_id":
        r = con.execute("select skill_code || ' ' || name from role where role_id=?", (value,)).fetchone()
        return r[0] if r else str(value)
    if field in ("line_manager_id", "approver_id"):
        r = con.execute("select name from app_user where user_id=?", (value,)).fetchone()
        return r[0] if r else str(value)
    if field == "default_location_id":
        r = con.execute("select name from location where location_id=?", (value,)).fetchone()
        return r[0] if r else str(value)
    if field == "hours_per_week":
        return fmt(value)
    if field == "books_time":
        return "Yes" if value else "No"
    if field == "entra_object_id":
        return "linked"          # the id itself is not shown
    if field in ("start_date", "end_date"):
        return dmy(value)
    return str(value)


def person_row(con, uid):
    """A person's row, with its columns readable by name."""
    return con.execute("select * from app_user where user_id=?", (uid,)).fetchone()


def audit_user_added(con, actor, uid):
    """Record that a person was added (by actor, or by the system when actor is None)."""
    row = con.execute("select name from app_user where user_id=?", (uid,)).fetchone()
    con.execute("insert into user_audit (user_id, changed_by, action, field, new_value) values (?,?,'ADD','Person',?)", (uid, actor, row[0]))


def audit_user_changes(con, actor, uid, old):
    """Record each field of a person that differs from old (their row as it was before the change)."""
    new = person_row(con, uid)
    for field, label in USER_AUDIT_FIELDS:
        if old[field] != new[field]:
            con.execute("insert into user_audit (user_id, changed_by, action, field, old_value, new_value) values (?,?,'CHANGE',?,?,?)",
                        (uid, actor, label, user_audit_text(con, field, old[field]), user_audit_text(con, field, new[field])))


def need_choice(value, options, label):
    """value when it is one of the options (value, label pairs), otherwise a message for the person."""
    if value not in {o[0] for o in options}:
        raise ValueError("Choose %s from the list." % label.lower())
    return value


def need_hours(value):
    """Contracted hours a week: a number above 0 and up to 168."""
    try:
        h = float((value or "40").strip())
    except ValueError:
        raise ValueError("Hours per week must be a number.")
    if not 0 < h <= 168:
        raise ValueError("Hours per week must be more than 0 and at most 168.")
    return h


def need_email(value):
    """A work email, lower case, or None when left empty (the person cannot sign in with Microsoft until it is set)."""
    value = (value or "").strip().lower()
    if not value:
        return None
    if " " in value or value.count("@") != 1 or value.startswith("@") or value.endswith("@") or "." not in value.split("@")[1]:
        raise ValueError("%s is not a valid email address." % value)
    return value


def edit_link(u, t, i):
    return '<a href="/admin/edit?u=%d&amp;t=%s&amp;id=%d">Edit</a>' % (u, t, i)
