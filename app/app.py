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
from urllib.parse import parse_qs, urlparse

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
        "select u.*, r.name as role_name, r.skill_code from app_user u join role r on r.role_id=u.role_id "
        "where u.user_id=?", (u,)).fetchone()


def pending_for(con, u):
    me = me_row(con, u)
    if me and me["permission"] == "admin":
        return con.execute("select count(*) from time_entry where status in ('Submitted','Edited')").fetchone()[0]
    return con.execute(
        "select count(*) from time_entry t join scope s on s.scope_id=t.scope_id "
        "where t.status in ('Submitted','Edited') and s.approver_user_id=?", (u,)).fetchone()[0]


def layout(con, u, title, body):
    users = con.execute("select user_id, name from app_user order by name").fetchall()
    opts = "".join('<option value="%d"%s>%s</option>' % (x["user_id"], " selected" if x["user_id"] == u else "", esc(x["name"]))
                   for x in users)
    me = me_row(con, u)
    menu = ['<a href="/timesheet?u=%d">My Timesheet</a>' % u,
            '<a href="/approvals?u=%d">Approvals (%d)</a>' % (u, pending_for(con, u)),
            '<a href="/reports?u=%d">Reports</a>' % u]
    if me and me["permission"] == "admin":
        menu += ['<a href="/admin?u=%d&amp;t=periods">Admin</a>' % u, '<a href="/audit?u=%d">Audit trail</a>' % u]
    who = "%s (%s, %s)" % (esc(me["name"]), esc(me["role_name"]), esc(me["permission"])) if me else ""
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
        "select t.*, s.scope_code, s.description, c.name as client, ro.skill_code "
        "from time_entry t join scope s on s.scope_id=t.scope_id join client c on c.client_id=s.client_id "
        "join role ro on ro.role_id=t.worked_role_id where t.user_id=? and t.work_date between ? and ? "
        "order by c.name, s.scope_code, ro.skill_code", (u, d0, d6)).fetchall()

    rows = {}
    for e in entries:
        k = (e["scope_id"], e["worked_role_id"])
        rows.setdefault(k, {"client": e["client"], "scope": "%s %s" % (e["scope_code"], e["description"]),
                            "skill": e["skill_code"], "cells": {}})["cells"][e["work_date"]] = e
    for (sid, rid) in adds:
        if (sid, rid) in rows:
            continue
        s = con.execute("select s.*, c.name as client from scope s join client c on c.client_id=s.client_id "
                        "where scope_id=?", (sid,)).fetchone()
        r = con.execute("select * from role where role_id=?", (rid,)).fetchone()
        if s and r:
            rows[(sid, rid)] = {"client": s["client"], "scope": "%s %s" % (s["scope_code"], s["description"]),
                                "skill": r["skill_code"], "cells": {}}

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
    head = ["Client", "Scope", "Skill code"] + [d.strftime("%a %d/%m") for d in days] + ["Total"]
    trs = []
    day_tot = {d.isoformat(): 0.0 for d in days}
    for (sid, rid), r in rows.items():
        tds = ["<td>%s</td><td>%s</td><td>%s</td>" % (esc(r["client"]), esc(r["scope"]), esc(r["skill"]))]
        rt = 0.0
        for d in days:
            iso = d.isoformat()
            e = r["cells"].get(iso)
            name = "h_%d_%d_%s" % (sid, rid, iso)
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
    foot = '<tr><td colspan="3" align="right"><b>Total</b></td>%s<td align="right"><b>%s</b></td></tr>' % (
        "".join('<td align="center"><b>%s</b></td>' % fmt(day_tot[d.isoformat()]) for d in days), fmt(sum(day_tot.values())))
    b.append('<table border="1" cellpadding="3" cellspacing="0"><tr>%s</tr>%s%s</table>' % (
        "".join("<th>%s</th>" % esc(h) for h in head), "".join(trs), foot))
    b.append("<p><small>D = draft, S = submitted (locked), A = approved, E = edited, waiting for re-approval.</small></p>")
    b.append('<p>Reason for changing approved hours (needed if you change a figure marked A or E): '
             '<input type="text" name="reason" size="60"></p>')
    b.append('<p><input type="submit" name="action" value="Save draft"> '
             '<input type="submit" name="action" value="Submit week"></p></form>')

    # add a row
    scopes = con.execute(
        "select s.scope_id, s.scope_code, s.description, c.name as client from scope s "
        "join client c on c.client_id=s.client_id join paaf p on p.client_id=s.client_id and p.user_id=? "
        "and p.status='Approved' where s.status='Open' order by c.name, s.scope_code", (u,)).fetchall()
    roles = con.execute("select role_id, skill_code, name from role where active=1 order by skill_code").fetchall()
    if scopes:
        so = "".join('<option value="%d">%s - %s %s</option>' % (s["scope_id"], esc(s["client"]), esc(s["scope_code"]), esc(s["description"])) for s in scopes)
        ro = "".join('<option value="%d"%s>%s %s</option>' % (r["role_id"], " selected" if r["role_id"] == me["role_id"] else "", esc(r["skill_code"]), esc(r["name"])) for r in roles)
        keep = "".join('<input type="hidden" name="add" value="%d:%d">' % k for k in rows if not rows[k]["cells"])
        b.append('<form method="get" action="/timesheet"><input type="hidden" name="u" value="%d">'
                 '<input type="hidden" name="w" value="%s">%s<b>Add row:</b> Scope <select name="add_scope">%s</select> '
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
    msgs, saved = [], 0
    d0, d6 = w.isoformat(), (w + datetime.timedelta(days=6)).isoformat()
    for key, vals in form.items():
        if not key.startswith("h_"):
            continue
        _, sid, rid, date = key.split("_", 3)
        sid, rid = int(sid), int(rid)
        raw = vals[0].strip()
        label = "%s, %s" % (con.execute("select scope_code from scope where scope_id=?", (sid,)).fetchone()[0], date)
        existing = con.execute("select * from time_entry where user_id=? and scope_id=? and worked_role_id=? and work_date=?",
                               (u, sid, rid, date)).fetchone()
        if raw == "":
            if existing:
                msgs.append("%s: cannot clear a saved entry yet (not supported in this version)." % label)
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
                con.execute("insert into time_entry (user_id, scope_id, paaf_id, worked_role_id, rate_id, work_date, hours, "
                            "status, created_by) values (?,?,?,?,?,?,?, 'Draft', ?)",
                            (u, sid, paaf[0], rid, rate[0], date, hours, u))
            saved += 1
        except sqlite3.DatabaseError as e:
            msgs.append("%s: %s" % (label, e))
    ok_msgs = []
    if saved:
        ok_msgs.append("%d entr%s saved." % (saved, "y" if saved == 1 else "ies"))
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
        "select t.entry_id, bu.name as booked_by, c.name as client, s.scope_code, ro.skill_code, t.work_date, t.hours, "
        "t.status, t.last_change_reason, (select old_hours from time_entry_audit a where a.entry_id=t.entry_id "
        "and a.new_status='Edited' order by a.audit_id desc limit 1) as was "
        "from time_entry t join scope s on s.scope_id=t.scope_id join client c on c.client_id=s.client_id "
        "join app_user bu on bu.user_id=t.user_id join role ro on ro.role_id=t.worked_role_id "
        "where t.status in ('Submitted','Edited') %s order by bu.name, t.work_date" % where).fetchall()
    out = []
    for r in rows:
        form = ('<form method="post" action="/approvals/act"><input type="hidden" name="u" value="%d">'
                '<input type="hidden" name="entry_id" value="%d"><input type="submit" name="action" value="Approve">' % (u, r["entry_id"]))
        if r["status"] == "Submitted":
            form += ' &nbsp; Reason <input type="text" name="reason" size="25"> <input type="submit" name="action" value="Reject">'
        form += "</form>"
        out.append([r["booked_by"], r["client"], r["scope_code"], r["skill_code"], r["work_date"], fmt(r["hours"]),
                    r["status"], fmt(r["was"]) if r["was"] is not None else "", r["last_change_reason"] or "", form])
    body = msgs_html(msgs, ok)
    if out:
        body += table(["Booked by", "Client", "Scope", "Skill code", "Date", "Hours", "Status", "Was", "Edit reason", "Action"],
                      out, raw_cols=(9,))
    else:
        body += "<p>Nothing waiting for your approval.</p>"
    return layout(con, u, "Approvals", body)


def act_approval(con, form):
    u, eid = int(form["u"][0]), int(form["entry_id"][0])
    action, reason = form["action"][0], form.get("reason", [""])[0].strip()
    try:
        if action == "Approve":
            con.execute("update time_entry set status='Approved', approved_by=?, approved_at=?, last_edited_by=?, "
                        "last_edited_at=? where entry_id=?", (u, stamp(), u, stamp(), eid))
            msg = ["Entry %d approved." % eid]
        else:
            con.execute("update time_entry set status='Draft', reject_reason=?, last_edited_by=?, last_edited_at=? "
                        "where entry_id=?", (reason, u, stamp(), eid))
            msg = ["Entry %d sent back to draft." % eid]
        con.commit()
        return view_approvals(con, u, msg, ok=True)
    except sqlite3.DatabaseError as e:
        return view_approvals(con, u, ["%s" % e])


# ---------- Reports ----------
def view_reports(con, u, q):
    d_from = q.get("from", ["2026-10-01"])[0]
    d_to = q.get("to", ["2026-12-31"])[0]
    ctr = con.execute("select client, scope_code, scope, skill_code, role, hours, sales_rate, total, pending_hours "
                      "from v_ctr order by client, scope_code, skill_code").fetchall()
    per = con.execute("select week_start, client, scope_code, skill_code, role, hours, total from v_period_report "
                      "where week_start between ? and ? order by week_start, client, scope_code, skill_code",
                      (d_from, d_to)).fetchall()
    b = ["<h4>CTR (Cost Time Resource) - approved hours by scope and skill code</h4>",
         table(["Client", "Scope", "Description", "Skill code", "Role", "Hours", "Sales rate", "Total", "Pending hours"],
               [[r[i] if i not in (6, 7) else "%.2f" % r[i] for i in range(9)] for r in ctr]),
         "<p><b>Grand total (approved): %.2f</b></p>" % sum(r["total"] for r in ctr),
         "<h4>Period reports (weekly) - approved hours</h4>",
         '<form method="get" action="/reports"><input type="hidden" name="u" value="%d">From (yyyy-mm-dd) '
         '<input type="text" name="from" size="10" value="%s"> To <input type="text" name="to" size="10" value="%s"> '
         '<input type="submit" value="Show"></form>' % (u, esc(d_from), esc(d_to)),
         table(["Week commencing", "Client", "Scope", "Skill code", "Role", "Hours", "Total"],
               [[r[0], r[1], r[2], r[3], r[4], fmt(r[5]), "%.2f" % r[6]] for r in per]),
         "<p><b>Total for range: %.2f hours, %.2f</b></p>" % (sum(r["hours"] for r in per), sum(r["total"] for r in per))]
    return layout(con, u, "Reports", "".join(b))


# ---------- Admin ----------
ADMIN = {
    "periods": ("Weekly periods", "select week_start, status, closed_by, closed_at from period order by week_start"),
    "users": ("Users", "select u.nmc_id, u.name, u.employment_type, r.skill_code, r.name as role, u.permission, u.start_date, u.end_date "
                       "from app_user u join role r on r.role_id=u.role_id order by u.nmc_id"),
    "roles": ("Roles / skill codes", "select role_id, skill_code, name, active from role order by skill_code"),
    "rates": ("Client rate cards", "select c.name as client, r.skill_code, r.name as role, rt.sales_rate, rt.effective_from "
                                    "from rate rt join client c on c.client_id=rt.client_id join role r on r.role_id=rt.role_id "
                                    "order by c.name, rt.sales_rate"),
    "paafs": ("PAAFs", "select p.paaf_id, u.name, c.name as client, r.skill_code, rt.sales_rate, p.status, p.sent_date, "
                       "p.decided_date, p.document_ref from paaf p join app_user u on u.user_id=p.user_id "
                       "join client c on c.client_id=p.client_id join rate rt on rt.rate_id=p.rate_id "
                       "join role r on r.role_id=rt.role_id order by c.name, u.name"),
    "scopes": ("Projects / scopes", "select c.name as client, s.scope_code, s.description, s.status, a.name as approver "
                                    "from scope s join client c on c.client_id=s.client_id "
                                    "join app_user a on a.user_id=s.approver_user_id order by c.name, s.scope_code"),
}


def view_admin(con, u, t, msgs=None, ok=False):
    me = me_row(con, u)
    if not me or me["permission"] != "admin":
        return layout(con, u, "Admin", "<p>Admin only. Switch to Alasdair Murray to see this page.</p>")
    t = t if t in ADMIN else "periods"
    links = " | ".join('<a href="/admin?u=%d&amp;t=%s">%s</a>' % (u, k, esc(v[0])) for k, v in ADMIN.items())
    title, sql = ADMIN[t]
    rows = [list(r) for r in con.execute(sql).fetchall()]
    b = [msgs_html(msgs, ok), "<p>%s</p><h4>%s</h4>" % (links, esc(title)), table([c[0] for c in con.execute(sql).description], rows)]
    if t == "periods":
        closes = "".join('<form method="post" action="/admin/period" style="display:inline"><input type="hidden" name="u" value="%d">'
                         '<input type="hidden" name="week_start" value="%s"><input type="hidden" name="action" value="close">'
                         '<input type="submit" value="Close week %s"></form> ' % (u, r[0], r[0]) for r in rows if r[1] == "Open")
        b.append("<p>%s</p>" % closes)
        b.append('<form method="post" action="/admin/period"><input type="hidden" name="u" value="%d">'
                 '<input type="hidden" name="action" value="open">Open a new week - Monday date (yyyy-mm-dd) '
                 '<input type="text" name="week_start" size="10"> <input type="submit" value="Open week"></form>' % u)
    else:
        b.append("<p><small>View only in this version.</small></p>")
    return layout(con, u, "Admin", "".join(b))


def act_period(con, form):
    u, ws, action = int(form["u"][0]), form["week_start"][0].strip(), form["action"][0]
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
                if "add_scope" in q and "add_role" in q:
                    adds += parse_adds(["%s:%s" % (q["add_scope"][0], q["add_role"][0])])
                self.send_html(view_timesheet(con, u, w, adds=adds))
            elif url.path == "/approvals":
                self.send_html(view_approvals(con, u))
            elif url.path == "/reports":
                self.send_html(view_reports(con, u, q))
            elif url.path == "/admin":
                self.send_html(view_admin(con, u, q.get("t", ["periods"])[0]))
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