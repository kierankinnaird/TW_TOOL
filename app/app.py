"""Basic front end for the v1 mock database. Plain HTML, no styling, standard library only.

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
LETTER = {"Draft": "D", "Submitted": "S", "Approved": "A", "Edited": "E"}


# ---------- helpers ----------
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def esc(v):
    return html.escape("" if v is None else str(v))


def stamp():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")


def fmt(h):
    return "" if h is None else "%g" % h


def monday(d):
    return d - datetime.timedelta(days=d.weekday())


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


def pending_for(con, u):
    me = me_row(con, u)
    if me and me["permission"] == "admin":
        return con.execute("select count(*) from time_entry where status in ('Submitted','Edited')").fetchone()[0]
    return con.execute(
        "select count(*) from time_entry t join scope s on s.scope_id=t.scope_id "
        "where t.status in ('Submitted','Edited') and s.approver_user_id=?", (u,)).fetchone()[0]


def layout(con, u, title, body):
    users = con.execute("select a.user_id, a.name, r.name as role_name, a.permission from app_user a "
                        "join role r on r.role_id=a.role_id order by a.name").fetchall()
    opts = "".join('<option value="%d"%s>%s - %s%s</option>' % (
        x["user_id"], " selected" if x["user_id"] == u else "", esc(x["name"]), esc(x["role_name"]),
        " (admin)" if x["permission"] == "admin" else "") for x in users)
    me = me_row(con, u)
    menu = ['<a href="/timesheet?u=%d">My Timesheet</a>' % u,
            '<a href="/approvals?u=%d">Approvals (%d)</a>' % (u, pending_for(con, u)),
            '<a href="/reports?u=%d">Reports</a>' % u]
    if me and me["permission"] == "admin":
        menu += ['<a href="/admin?u=%d&amp;t=periods">Admin</a>' % u, '<a href="/audit?u=%d">Audit trail</a>' % u]
    who = "%s<br>%s (%s)<br>%s, %s" % (esc(me["name"]), esc(me["role_name"]), esc(me["skill_code"]), esc(me["family"]), esc(me["permission"])) if me else ""
    return (
        "<html><head><title>%s</title></head><body>"
        '<table width="100%%" border="0"><tr><td><b>NMC Timewriting</b> (mock)</td>'
        '<td align="right"><form method="get" action="/timesheet">Signed in as: '
        '<select name="u">%s</select> <input type="submit" value="Switch user"></form></td></tr></table><hr>'
        '<table width="100%%" border="0"><tr valign="top"><td width="160">%s<br><br><small>%s</small></td>'
        "<td><h3>%s</h3>%s</td></tr></table></body></html>"
    ) % (esc(title), opts, "<br>".join(menu), who, esc(title), body)


def msgs_html(msgs, ok=False):
    if not msgs:
        return ""
    return "<p><b>%s</b></p><ul>%s</ul>" % ("Done:" if ok else "Problems:", "".join("<li>%s</li>" % esc(m) for m in msgs))


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
        tds = ["<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>" % (
            esc(r["client"]), esc(r["project"]), esc(r["scope"]), esc(r["phase"]), esc(r["skill"]))]
        rt = 0.0
        for d in days:
            iso = d.isoformat()
            e = r["cells"].get(iso)
            name = "h_%d_%d_%s" % (pid, rid, iso)
            if e is None:
                cell = '<input type="text" size="4" name="%s" value="">' % name if open_week else "&nbsp;"
            else:
                rt += e["hours"]
                day_tot[iso] += e["hours"]
                st = LETTER[e["status"]]
                if e["status"] == "Submitted" or (e["status"] == "Draft" and not open_week):
                    cell = "%s <small>%s</small>" % (fmt(e["hours"]), st)
                else:
                    cell = '<input type="text" size="4" name="%s" value="%s"> <small>%s</small>' % (name, fmt(e["hours"]), st)
            tds.append('<td align="center">%s</td>' % cell)
        tds.append('<td align="right">%s</td>' % fmt(rt))
        trs.append("<tr>%s</tr>" % "".join(tds))
    if not trs:
        trs.append('<tr><td colspan="%d">No rows yet. Add a row below.</td></tr>' % len(head))
    foot = '<tr><td colspan="5" align="right"><b>Total</b></td>%s<td align="right"><b>%s</b></td></tr>' % (
        "".join('<td align="center"><b>%s</b></td>' % fmt(day_tot[d.isoformat()]) for d in days), fmt(sum(day_tot.values())))
    b.append('<table border="1" cellpadding="3" cellspacing="0"><tr>%s</tr>%s%s</table>' % (
        "".join("<th>%s</th>" % esc(h) for h in head), "".join(trs), foot))
    by_status = {"Approved": 0.0, "Edited": 0.0, "Submitted": 0.0, "Draft": 0.0}
    for e in entries:
        by_status[e["status"]] += e["hours"]
    b.append("<p><b>Hours this week (all statuses): %s</b> &nbsp; Approved %s, Edited %s, Submitted %s, Draft %s</p>" % (
        fmt(sum(by_status.values())), fmt(by_status["Approved"]), fmt(by_status["Edited"]), fmt(by_status["Submitted"]), fmt(by_status["Draft"])))
    b.append("<p><small>D = draft, S = submitted (locked), A = approved, E = edited, waiting for re-approval. To clear a draft entry, empty its box and click Save draft.</small></p>")
    b.append('<p>Reason for changing approved hours (needed if you change a figure marked A or E): '
             '<input type="text" name="reason" size="60"></p>')
    b.append('<p><input type="submit" name="action" value="Save draft"> '
             '<input type="submit" name="action" value="Submit week"></p></form>')

    # add a row
    scopes = con.execute(
        "select ph.phase_id, ph.name as phase, s.scope_code, s.description, pr.name as project, c.name as client "
        "from phase ph join scope s on s.scope_id=ph.scope_id join project pr on pr.project_id=s.project_id "
        "join client c on c.client_id=s.client_id join paaf p on p.client_id=s.client_id and p.user_id=? "
        "and p.status='Approved' where s.status='Open' and ph.status='Open' and pr.status='Open' "
        "order by c.name, pr.name, s.scope_code, ph.name", (u,)).fetchall()
    roles = con.execute("select role_id, skill_code, name from role where active=1 order by skill_code").fetchall()
    if scopes:
        so = "".join('<option value="%d">%s &gt; %s &gt; %s %s &gt; %s</option>' % (
            s["phase_id"], esc(s["client"]), esc(s["project"]), esc(s["scope_code"]), esc(s["description"]), esc(s["phase"])) for s in scopes)
        ro = "".join('<option value="%d"%s>%s %s</option>' % (r["role_id"], " selected" if r["role_id"] == me["role_id"] else "", esc(r["skill_code"]), esc(r["name"])) for r in roles)
        keep = "".join('<input type="hidden" name="add" value="%d:%d">' % k for k in rows if not rows[k]["cells"])
        b.append('<form method="get" action="/timesheet"><input type="hidden" name="u" value="%d">'
                 '<input type="hidden" name="w" value="%s">%s<b>Add row:</b> Phase <select name="add_phase">%s</select> '
                 'Skill code <select name="add_role">%s</select> <input type="submit" value="Add row"></form>'
                 "<p><small>Your own skill code is pre-selected. Choose a different one if you are covering another role.</small></p>" % (u, d0, keep, so, ro))
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
                client_id = con.execute("select client_id from scope where scope_id=?", (sid,)).fetchone()[0]
                rate = con.execute("select rate_id from rate where client_id=? and role_id=? and effective_from<=? "
                                   "order by effective_from desc limit 1", (client_id, rid, date)).fetchone()
                paaf = con.execute("select paaf_id from paaf where user_id=? and client_id=?", (u, client_id)).fetchone()
                if not rate:
                    msgs.append("%s: no rate for that skill code on this client." % label)
                    continue
                if not paaf:
                    msgs.append("%s: you have no PAAF for this client." % label)
                    continue
                con.execute("insert into time_entry (user_id, scope_id, phase_id, paaf_id, worked_role_id, rate_id, work_date, hours, "
                            "status, created_by) values (?,?,?,?,?,?,?,?, 'Draft', ?)",
                            (u, sid, pid, paaf[0], rid, rate[0], date, hours, u))
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
    me = me_row(con, u)
    where = "" if me and me["permission"] == "admin" else "and s.approver_user_id=%d" % u
    rows = con.execute(
        "select t.entry_id, bu.name as booked_by, c.name as client, pr.name as project, s.scope_code, ph.name as phase, ro.skill_code, t.work_date, t.hours, "
        "t.status, t.last_change_reason, (select old_hours from time_entry_audit a where a.entry_id=t.entry_id "
        "and a.new_status='Edited' order by a.audit_id desc limit 1) as was "
        "from time_entry t join scope s on s.scope_id=t.scope_id join phase ph on ph.phase_id=t.phase_id "
        "join project pr on pr.project_id=s.project_id join client c on c.client_id=s.client_id "
        "join app_user bu on bu.user_id=t.user_id join role ro on ro.role_id=t.worked_role_id "
        "where t.status in ('Submitted','Edited') %s order by bu.name, t.work_date" % where).fetchall()
    body = msgs_html(msgs, ok)
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
    if not ids:
        return view_approvals(con, u, ["Tick at least one entry first."])
    if not approve and not reason:
        return view_approvals(con, u, ["Give a reason to reject."])
    done, msgs = 0, []
    for eid in ids:
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


def ctr_tree_rows(con, u=None):
    """Client > Project > Scope > Phase > Skill code > Person > Entry. Hours/Total are approved only; Pending = Submitted + Edited."""
    ents = con.execute(
        "select c.name as client, pr.project_code, pr.name as project, s.scope_code, s.description, ph.phase_id, ph.name as phase, "
        "ro.skill_code, ro.name as role, rt.rate_id, rt.sales_rate, u.name as person, t.entry_id, t.work_date, t.hours, t.status "
        "from time_entry t join phase ph on ph.phase_id=t.phase_id join scope s on s.scope_id=ph.scope_id "
        "join project pr on pr.project_id=s.project_id join client c on c.client_id=s.client_id "
        "join rate rt on rt.rate_id=t.rate_id join role ro on ro.role_id=rt.role_id join app_user u on u.user_id=t.user_id "
        "where t.status<>'Draft' order by c.name, pr.name, s.scope_code, ph.name, ro.skill_code, u.name, t.work_date").fetchall()
    # also list phases that have no booked hours yet, so the structure is visible
    empty = con.execute(
        "select c.name as client, pr.name as project, s.scope_code, s.description, ph.phase_id, ph.name as phase "
        "from phase ph join scope s on s.scope_id=ph.scope_id join project pr on pr.project_id=s.project_id "
        "join client c on c.client_id=s.client_id where s.status='Open' and ph.phase_id not in "
        "(select phase_id from time_entry where status<>'Draft')").fetchall()

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
        sk = node(path[-1]["kids"], e["rate_id"], "%s %s" % (e["skill_code"], e["role"]), "Skill code")
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
    levels = ["Client", "Project", "Scope", "Phase", "Skill code", "Person", "Entry"]

    def walk(n, level, parent):
        counter[0] += 1
        rid = "r%d" % counter[0]
        kids = list(n["kids"].values())
        tg = '<span class="tg" style="cursor:pointer;font-family:monospace" onclick="tog(this)">[-]</span> ' if kids else "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; "
        weight = "b" if level < 5 else "span"
        pad = 4 + level * 16
        status = n["status"]
        out.append(
            '<tr data-id="%s" data-parent="%s" data-level="%d" data-kids="%d" data-open="1">'
            '<td>%s</td><td style="padding-left:%dpx"><%s>%s%s</%s></td><td>%s</td><td align="right">%s</td>'
            '<td align="right"><%s>%s</%s></td><td align="right"><%s>%.2f</%s></td><td align="right"><%s>%s</%s></td><td>%s</td></tr>' % (
                rid, parent, level, 1 if kids else 0, n["kind"], pad, weight, tg, label_html(n), weight, esc(n["skill"]),
                ("%.2f" % n["rate"]) if n["rate"] is not None else "",
                weight, fmt(n["h"]), weight, weight, n["t"], weight, weight, fmt(n["p"]), weight, esc(status)))
        for k in kids:
            walk(k, level + 1, rid)

    for c in root.values():
        walk(c, 0, "")
    g_h = sum(c["h"] for c in root.values())
    g_t = sum(c["t"] for c in root.values())
    g_p = sum(c["p"] for c in root.values())
    return "".join(out), g_h, g_t, g_p


def period_filters(con, q):
    """Client > Project > Scope > Phase filter for the period report. Each dropdown only lists what fits the choices above it,
    and a choice that no longer fits (e.g. a project of another client) is dropped. Returns (chosen ids, option lists)."""
    def pick(name, options):
        try:
            v = int(q.get(name, [""])[0])
        except ValueError:
            return None
        return v if v in [o[0] for o in options] else None

    clients = [tuple(r) for r in con.execute("select client_id, name from client order by name")]
    client = pick("client", clients)
    projects = [(r["project_id"], r["name"] if client else "%s - %s" % (r["client"], r["name"])) for r in con.execute(
        "select p.project_id, p.name, c.name as client from project p join client c on c.client_id=p.client_id "
        "where (? is null or p.client_id=?) order by c.name, p.name", (client, client))]
    project = pick("project", projects)
    scopes = [(r["scope_id"], "%s %s" % (r["scope_code"], r["description"])) for r in con.execute(
        "select scope_id, scope_code, description from scope where (? is null or client_id=?) and (? is null or project_id=?) "
        "order by scope_code", (client, client, project, project))]
    scope = pick("scope", scopes)
    phases = [(r["phase_id"], r["name"] if scope else "%s - %s" % (r["scope_code"], r["name"])) for r in con.execute(
        "select ph.phase_id, ph.name, s.scope_code from phase ph join scope s on s.scope_id=ph.scope_id "
        "where (? is null or s.client_id=?) and (? is null or s.project_id=?) and (? is null or s.scope_id=?) "
        "order by s.scope_code, ph.name", (client, client, project, project, scope, scope))]
    phase = pick("phase", phases)
    return (client, project, scope, phase), (clients, projects, scopes, phases)


def view_reports(con, u, q):
    d_from = q.get("from", ["2026-10-01"])[0]
    d_to = q.get("to", ["2026-12-31"])[0]
    (client, project, scope, phase), (clients, projects, scopes, phases) = period_filters(con, q)
    per = con.execute("select week_start, client, project, scope_code, phase, skill_code, role, hours, total from v_period_report "
                      "where week_start between ? and ? and (scope_code, phase) in "
                      "(select s.scope_code, ph.name from phase ph join scope s on s.scope_id=ph.scope_id "
                      "where (? is null or s.client_id=?) and (? is null or s.project_id=?) and (? is null or s.scope_id=?) "
                      "and (? is null or ph.phase_id=?)) "
                      "order by week_start, client, project, scope_code, phase, skill_code",
                      (d_from, d_to, client, client, project, project, scope, scope, phase, phase)).fetchall()
    auto = ' onchange="this.form.submit()"'
    filtered = any(x is not None for x in (client, project, scope, phase))
    tree_rows, g_h, g_t, g_p = ctr_tree_rows(con, u)
    head = ("<tr><th>Level</th><th>Name</th><th>Skill code</th><th>Sales rate</th><th>Actual booked hrs</th>"
            "<th>Total</th><th>Pending hrs</th><th>Status</th></tr>")
    foot = sub_row(["", "Grand total", "", "", fmt(g_h), "%.2f" % g_t, fmt(g_p), ""], right_from=4)

    t2 = ["<tr><th>Week commencing</th><th>Client</th><th>Project</th><th>Scope</th><th>Phase</th><th>Skill code</th>"
          "<th>Role</th><th>Hours</th><th>Total</th></tr>"]
    r_h = r_t = 0.0
    for wk in dict.fromkeys(r["week_start"] for r in per):
        lines = [r for r in per if r["week_start"] == wk]
        wh = wt = 0.0
        for r in lines:
            t2.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td align=\"right\">%s</td><td align=\"right\">%.2f</td></tr>" % (
                esc(r["week_start"]), esc(r["client"]), esc(r["project"]), esc(r["scope_code"]), esc(r["phase"]),
                esc(r["skill_code"]), esc(r["role"]), fmt(r["hours"]), r["total"]))
            wh += r["hours"]; wt += r["total"]
        t2.append(sub_row(["", "Subtotal week commencing %s" % wk, "", "", "", "", "", fmt(wh), "%.2f" % wt], right_from=7))
        r_h += wh; r_t += wt
    t2.append(sub_row(["", "Total for range", "", "", "", "", "", fmt(r_h), "%.2f" % r_t], right_from=7))

    b = ["<h4>CTR (Cost Time Resource) - by client, project, scope and phase</h4>",
         "<p><small>Actual booked hrs are approved hours only. Pending hrs are submitted or edited hours still waiting for approval. "
         "Click [+] / [-] to open a row down to the person and their timesheet entries. Hours and Total roll up at every level. "
         "Each phase is that phase's CTR, priced from the client rate card by skill code. All weeks to date.</small></p>",
         '<p><input type="button" value="Expand all" onclick="openTo(9)"> <input type="button" value="Collapse all" onclick="openTo(0)"> '
         '<input type="button" value="Show to phase" onclick="openTo(3)"> <input type="button" value="Show to skill code" onclick="openTo(4)"> '
         '<input type="button" value="Show to person" onclick="openTo(5)"> <input type="button" value="Show to entries" onclick="openTo(6)"></p>',
         '<table id="ctr" border="1" cellpadding="3" cellspacing="0">%s%s%s</table>' % (head, tree_rows, foot),
         REPORT_JS,
         "<h4>Period reports (weekly) - approved hours</h4>",
         "<p><small>These are the same approved hours as the CTR, split by week. Each line here is one week of a CTR line above, "
         "so it is smaller than the all-weeks figure. When the date range covers every week, the total for range matches the CTR grand total.</small></p>",
         '<form method="get" action="/reports">%s<p>Client %s Project %s Scope %s Phase %s</p>'
         '<p>From (yyyy-mm-dd) <input type="text" name="from" size="10" value="%s"> To <input type="text" name="to" size="10" value="%s"> '
         '<input type="submit" value="Show"> <input type="button" value="Reset" onclick="location.href=\'/reports?u=%d\'"></p></form>' % (
             hidden(u=u),
             sel("client", clients, client, True, "All clients", auto), sel("project", projects, project, True, "All projects", auto),
             sel("scope", scopes, scope, True, "All scopes", auto), sel("phase", phases, phase, True, "All phases", auto),
             esc(d_from), esc(d_to), u),
         "<p><small>Filtered to the client, project, scope or phase chosen above, so the total for range is only part of the CTR grand total.</small></p>"
         if filtered else "",
         '<table border="1" cellpadding="3" cellspacing="0">%s</table>' % "".join(t2)]
    return layout(con, u, "Reports", "".join(b))


def view_ctr(con, u, q):
    """One CTR (a phase): a Summary tab plus one tab per job family, like the tabs in the CTR workbook."""
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
        "from time_entry t join rate rt on rt.rate_id=t.rate_id join role r on r.role_id=rt.role_id "
        "join app_user u on u.user_id=t.user_id left join app_user ap on ap.user_id=t.approved_by "
        "where t.phase_id=? and t.status<>'Draft' order by r.family, r.skill_code, u.name, t.work_date", (pid,)).fetchall()

    def tot(rows):
        h = sum(e["hours"] for e in rows if e["status"] == "Approved")
        t = sum(e["hours"] * e["sales_rate"] for e in rows if e["status"] == "Approved")
        p = sum(e["hours"] for e in rows if e["status"] != "Approved")
        return h, t, p

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

    if fam == "Summary":
        b.append("<h4>CTR summary - %s %s, %s</h4><p><small>Actual booked hrs are approved hours. Pending hrs are submitted or edited hours waiting for approval. "
                 "Click a job family tab for its resources, people and timesheet entries.</small></p>" % (esc(ph["scope_code"]), esc(ph["description"]), esc(ph["phase"])))
        rows = []
        for f in families:
            h, t, p = tot([e for e in ents if e["family"] == f])
            rows.append("<tr><td><a href=\"%s&amp;family=%s\">%s</a></td><td align=\"right\">%s</td><td align=\"right\">%.2f</td><td align=\"right\">%s</td></tr>"
                        % (base, quote(f), esc(f), fmt(h), t, fmt(p)))
        h, t, p = tot(ents)
        rows.append(sub_row(["Total", fmt(h), "%.2f" % t, fmt(p)], right_from=1))
        b.append('<table border="1" cellpadding="3" cellspacing="0"><tr><th>Job family</th><th>Actual booked hrs</th><th>Total</th><th>Pending hrs</th></tr>%s</table>' % "".join(rows))
    else:
        fe = [e for e in ents if e["family"] == fam]
        b.append("<h4>%s</h4>" % esc(fam))
        # resources: one line per job title
        rows = []
        for key in dict.fromkeys((e["skill_code"], e["title"], e["sales_rate"]) for e in fe):
            part = [e for e in fe if (e["skill_code"], e["title"], e["sales_rate"]) == key]
            h, t, p = tot(part)
            rows.append("<tr><td>%s</td><td>%s</td><td align=\"right\">%.2f</td><td align=\"right\">%s</td><td align=\"right\">%.2f</td><td align=\"right\">%s</td></tr>"
                        % (esc(key[0]), esc(key[1]), key[2], fmt(h), t, fmt(p)))
        h, t, p = tot(fe)
        rows.append(sub_row(["", "Sub-total", "", fmt(h), "%.2f" % t, fmt(p)], right_from=3))
        b.append('<p><b>Resources</b></p><table border="1" cellpadding="3" cellspacing="0"><tr><th>Skill code</th><th>Job title</th><th>Sales rate</th>'
                 '<th>Actual booked hrs</th><th>Total</th><th>Pending hrs</th></tr>%s</table>' % "".join(rows))
        # people
        rows = []
        for key in dict.fromkeys((e["person"], e["skill_code"], e["title"], e["sales_rate"]) for e in fe):
            part = [e for e in fe if (e["person"], e["skill_code"], e["title"], e["sales_rate"]) == key]
            h, t, p = tot(part)
            rows.append("<tr><td>%s</td><td>%s</td><td align=\"right\">%s</td><td align=\"right\">%.2f</td><td align=\"right\">%s</td></tr>"
                        % (esc(key[0]), esc("%s %s" % (key[1], key[2])), fmt(h), t, fmt(p)))
        rows.append(sub_row(["Sub-total", "", fmt(tot(fe)[0]), "%.2f" % tot(fe)[1], fmt(tot(fe)[2])], right_from=2))
        b.append('<p><b>People</b></p><table border="1" cellpadding="3" cellspacing="0"><tr><th>Person</th><th>Booked as</th><th>Actual booked hrs</th>'
                 '<th>Total</th><th>Pending hrs</th></tr>%s</table>' % "".join(rows))
        # entries
        rows = []
        for e in sorted(fe, key=lambda x: (x["work_date"], x["person"])):
            rows.append("<tr><td>%s</td><td>%s</td><td>%s</td><td align=\"right\">%s</td><td align=\"right\">%.2f</td><td>%s</td><td>%s</td></tr>" % (
                esc(e["work_date"]), esc(e["person"]), esc("%s %s" % (e["skill_code"], e["title"])), fmt(e["hours"]),
                e["hours"] * e["sales_rate"], esc(e["status"]), esc(e["approved_by"] or "")))
        b.append('<p><b>Timesheet entries</b> <small>(value = hours x sales rate; only Approved entries count towards the CTR)</small></p>'
                 '<table border="1" cellpadding="3" cellspacing="0"><tr><th>Date</th><th>Person</th><th>Booked as</th><th>Hours</th><th>Value</th>'
                 '<th>Status</th><th>Approved by</th></tr>%s</table>' % "".join(rows))
    return layout(con, u, "CTR - %s %s - %s" % (ph["scope_code"], ph["description"], ph["phase"]), "".join(b))


# ---------- Admin ----------
ADMIN_TABS = [("periods", "Weekly periods"), ("users", "Users"), ("roles", "Job families / titles"),
              ("clients", "Clients"), ("rates", "Client rate cards"), ("paafs", "PAAFs"), ("scopes", "Projects / scopes / phases")]


def is_admin(con, u):
    me = me_row(con, u)
    return bool(me and me["permission"] == "admin")


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


def hidden(**kw):
    return "".join('<input type="hidden" name="%s" value="%s">' % (k, esc(v)) for k, v in kw.items())


def opts_roles(con, active_only=True):
    q = "select role_id, skill_code || ' ' || name from role %s order by skill_code" % ("where active=1" if active_only else "")
    return [tuple(r) for r in con.execute(q)]


def opts_clients(con):
    return [tuple(r) for r in con.execute("select client_id, name from client order by name")]


def opts_projects(con):
    return [tuple(r) for r in con.execute("select p.project_id, c.name || ' - ' || p.name from project p join client c on c.client_id=p.client_id "
                                          "order by c.name, p.name")]


def opts_scopes(con):
    return [tuple(r) for r in con.execute("select s.scope_id, c.name || ' - ' || s.scope_code || ' ' || s.description from scope s "
                                          "join client c on c.client_id=s.client_id order by c.name, s.scope_code")]


def opts_users(con):
    return [tuple(r) for r in con.execute("select user_id, name from app_user order by name")]


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
        b.append(table(["week_start", "status", "closed_by", "closed_at"], rows))
        closes = "".join('<form method="post" action="/admin/period" style="display:inline">%s'
                         '<input type="submit" value="Close week %s"></form> ' % (hidden(u=u, week_start=r[0], action="close"), r[0])
                         for r in rows if r[1] == "Open")
        b.append("<p>%s</p>" % closes)
        b.append('<form method="post" action="/admin/period">%s<b>Open a new week</b> - Monday date (yyyy-mm-dd) %s '
                 '<input type="submit" value="Open week"></form>' % (hidden(u=u, action="open"), txt("week_start", "", 10)))
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
                    sel("permission", [("user", "user"), ("admin", "admin")]), txt("start_date", datetime.date.today().isoformat(), 10),
                    txt("end_date", "", 10)))
    elif t == "roles":
        rows = [[r["family"], r["skill_code"], r["name"], "Yes" if r["active"] else "No", edit_link(u, "roles", r["role_id"])]
                for r in con.execute("select * from role order by family, skill_code")]
        b.append(table(["Job family", "Skill code", "Job title", "Active", ""], rows, raw_cols=(4,)))
        b.append('<h4>Add a job title</h4><form method="post" action="/admin/add">%s Job family %s Skill code %s Job title %s '
                 '<input type="submit" value="Add role"></form>' % (hidden(u=u, t="roles"), txt("family", "", 24), txt("skill_code", "", 8), txt("name", "", 30)))
    elif t == "clients":
        rows = [[r["name"]] for r in con.execute("select name from client order by name")]
        b.append(table(["Client"], rows))
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
                 '<form method="post" action="/admin/add">%s Client %s Skill code %s Sales rate %s Effective from (yyyy-mm-dd) %s '
                 '<input type="submit" value="Add rate"></form>'
                 % (hidden(u=u, t="rates"), sel("client_id", opts_clients(con)), sel("role_id", opts_roles(con)),
                    txt("sales_rate", "", 8), txt("effective_from", datetime.date.today().isoformat(), 10)))
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
                      '<input type="submit" name="action" value="Reject">' % (txt("document_ref", "", 28), txt("expiry_date", "", 10)))
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
                 % (hidden(u=u, t="paafs"), sel("user_id", opts_users(con)), sel("client_id", opts_clients(con))))
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
                    sel("approver_user_id", opts_users(con))))
        b.append("<h4>Phases</h4>")
        rows = [[r["client"], r["scope_code"], r["description"], r["name"], r["status"], edit_link(u, "phases", r["phase_id"])]
                for r in con.execute("select ph.*, s.scope_code, s.description, c.name as client from phase ph join scope s on s.scope_id=ph.scope_id "
                                     "join client c on c.client_id=s.client_id order by c.name, s.scope_code, ph.name")]
        b.append(table(["Client", "Scope", "Description", "Phase", "Status", ""], rows, raw_cols=(5,)))
        b.append('<form method="post" action="/admin/add">%s Scope %s Phase name %s <input type="submit" value="Add phase"></form>'
                 % (hidden(u=u, t="phases"), sel("scope_id", opts_scopes(con)), txt("name", "", 20)))
    return "".join(b)


def view_admin(con, u, t, msgs=None, ok=False):
    if not is_admin(con, u):
        return layout(con, u, "Admin", "<p>Admin only. Switch to Alasdair Murray to see this page.</p>")
    t = t if t in dict(ADMIN_TABS) else "periods"
    links = " | ".join('<a href="/admin?u=%d&amp;t=%s">%s</a>' % (u, k, esc(v)) for k, v in ADMIN_TABS)
    body = "%s<p>%s</p><h4>%s</h4>%s" % (msgs_html(msgs, ok), links, esc(dict(ADMIN_TABS)[t]), admin_body(con, u, t))
    return layout(con, u, "Admin", body)


def view_admin_edit(con, u, t, i, msgs=None):
    if not is_admin(con, u):
        return layout(con, u, "Admin", "<p>Admin only.</p>")
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
                    sel("permission", [("user", "user"), ("admin", "admin")], r["permission"]), esc(r["start_date"]), txt("end_date", r["end_date"] or "", 10)))
        note = "<p><small>Changing someone's role does not change their existing PAAFs or booked hours.</small></p>"
    elif t == "roles":
        r = con.execute("select * from role where role_id=?", (i,)).fetchone()
        if not r:
            return layout(con, u, "Admin", "<p>Not found.</p>")
        form = ("<tr><td>Job family</td><td>%s</td></tr><tr><td>Skill code</td><td>%s</td></tr><tr><td>Job title</td><td>%s</td></tr><tr><td>Active</td><td>%s</td></tr>"
                % (txt("family", r["family"], 24), txt("skill_code", r["skill_code"], 8), txt("name", r["name"], 30), sel("active", [("1", "Yes"), ("0", "No")], str(r["active"]))))
        note = "<p><small>Deactivating a role hides it from new users and new timesheet rows. It does not affect past entries.</small></p>"
    elif t == "scopes":
        r = con.execute("select s.*, c.name as client from scope s join client c on c.client_id=s.client_id where scope_id=?", (i,)).fetchone()
        if not r:
            return layout(con, u, "Admin", "<p>Not found.</p>")
        form = ("<tr><td>Client</td><td>%s</td></tr><tr><td>Scope code</td><td>%s</td></tr><tr><td>Description</td><td>%s</td></tr>"
                "<tr><td>Status</td><td>%s</td></tr><tr><td>Approver</td><td>%s</td></tr>" % (
                    esc(r["client"]), esc(r["scope_code"]), txt("description", r["description"], 40),
                    sel("status", [("Open", "Open"), ("Pipeline", "Pipeline"), ("Closed", "Closed")], r["status"]),
                    sel("approver_user_id", opts_users(con), r["approver_user_id"])))
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
    if not is_admin(con, u):
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
    if not is_admin(con, u):
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
        elif t == "scopes":
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
    if not is_admin(con, u):
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
    if not is_admin(con, u):
        return view_admin(con, u, "periods")
    try:
        if action == "open":
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
    me = me_row(con, u)
    if not me or me["permission"] != "admin":
        return layout(con, u, "Audit trail", "<p>Admin only.</p>")
    rows = con.execute(
        "select a.audit_id, a.changed_at, ch.name as changed_by, a.action, bu.name as booked_by, s.scope_code, t.work_date, "
        "a.old_status, a.new_status, a.old_hours, a.new_hours, a.reason from time_entry_audit a "
        "join time_entry t on t.entry_id=a.entry_id join app_user bu on bu.user_id=t.user_id "
        "left join app_user ch on ch.user_id=a.changed_by join scope s on s.scope_id=t.scope_id "
        "order by a.audit_id desc limit 200").fetchall()
    heads = ["#", "When", "Changed by", "Action", "Booked by", "Scope", "Date", "Old status", "New status", "Old hours", "New hours", "Reason"]
    return layout(con, u, "Audit trail (latest 200)", table(heads, [[fmt(v) if i in (9, 10) else v for i, v in enumerate(r)] for r in rows]))


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

    def user_id(self, con, q):
        try:
            return int(q["u"][0])
        except (KeyError, ValueError):
            return con.execute("select user_id from app_user order by user_id limit 1").fetchone()[0]

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        con = db()
        try:
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
                self.send_html(view_admin(con, u, q.get("t", ["periods"])[0]))
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


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    if not os.path.exists(DB_PATH):
        sys.exit("No database at %s. Run: python scripts/build.py" % DB_PATH)
    print("Timewriting mock running at http://localhost:%d  (Ctrl+C to stop)" % port)
    make_server(port).serve_forever()
