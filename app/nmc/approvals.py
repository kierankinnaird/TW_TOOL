"""Approvals, missing timesheets and reopening a timesheet."""
import datetime
from urllib.parse import quote
from nmc import pg
from nmc.core import (
    dmy,
    approval_queue, date_input, esc,
    fmt, hidden, is_approver, layout,
    may_action, monday, msgs_html,
    no_access, permission_of, sel, stamp,
    table, txt)


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
function tickAll(c){var e=document.getElementsByName('entry_ids'), p=document.querySelectorAll('input.pp'), i;
  for(i=0;i<e.length;i++){e[i].checked=c.checked;} for(i=0;i<p.length;i++){p[i].checked=c.checked;}}
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
    waiting = con.execute(
        "select wk, sum(n) as n from (select monday_of(t.work_date) as wk, count(*) as n from time_entry t join scope s on s.scope_id=t.scope_id "
        "where t.status in ('Submitted','Edited')" + where + " group by wk "
        ") x group by wk order by wk", list(wargs)).fetchall()
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
    if not people:
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
        # one line per booking (scope, CTR, skill code, location, normal or overtime) with a column per day, not a line per day
        bookings = {}
        for e in es:
            bookings.setdefault((e["scope_code"], e["phase"], e["skill_code"], e["location"], e["overtime"], e["client"], e["project"]), []).append(e)
        lines = []
        for (scope, phase, skill, loc, ot, client, project), bes in bookings.items():
            pend_ids = [x["entry_id"] for x in bes if x["status"] in ("Submitted", "Edited")]
            cells = {x["work_date"]: x for x in bes}
            statuses = sorted({x["status"] for x in bes})
            was = ", ".join("%s: %s" % (datetime.date.fromisoformat(x["work_date"]).strftime("%a"), fmt(x["was"])) for x in bes if x["was"] is not None)
            reasons = "; ".join(dict.fromkeys(x["last_change_reason"] for x in bes if x["last_change_reason"]))
            lines.append("<tr><td align=\"center\">%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>%s"
                         "<td align=\"right\"><b>%s</b></td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                             '<input type="checkbox" name="entry_ids" value="%s" data-p="%d">' % (",".join(str(i) for i in pend_ids), uid) if pend_ids else "",
                             esc(client), esc(project), esc(scope), esc(phase), esc(skill), esc(loc), "<b>Overtime</b>" if ot else "Normal",
                             "".join('<td align="center">%s</td>' % (fmt(cells[d.isoformat()]["hours"]) if d.isoformat() in cells else "&ndash;") for d in days),
                             fmt(sum(x["hours"] for x in bes)), esc(", ".join(statuses)) if len(statuses) == 1 else "Part approved", esc(was), esc(reasons)))
        trs.append('<tr class="drow" id="%s"><td></td><td colspan="%d"><table border="1" cellpadding="3" cellspacing="0">'
                   "<tr><th></th><th>Client</th><th>Project</th><th>Scope</th><th>CTR</th><th>Skill code</th><th>Location</th><th>Type</th>%s"
                   "<th>Total</th><th>Status</th><th>Was</th><th>Edit reason</th></tr>%s</table></td></tr>" % (
                       drow, ncol - 2, "".join("<th>%s</th>" % d.strftime("%a %d/%m") for d in days), "".join(lines)))
    foot = ('<tr><td></td><td align="right"><b>All people</b></td>%s<td align="right"><b>%s</b></td><td></td><td></td></tr>' % (
        "".join('<td align="center"><b>%s</b></td>' % (fmt(day_tot[d.isoformat()]) if day_tot[d.isoformat()] else "&ndash;") for d in days), fmt(sum(day_tot.values()))))
    body += ('<form method="post" action="/approvals/act">%s<p><small>One line per person, with the submitted and approved hours for each day. '
             '<a href="#" class="js" style="display:none" onclick="return openAll(true)">Show all detail</a>'
             '<span class="js" style="display:none"> | </span>'
             '<a href="#" class="js" style="display:none" onclick="return openAll(false)">Hide all detail</a>. '
             'Open a line to see everything booked against that person. Tick the boxes (a person, or single entries in their detail) to action them, '
             'or tick the box in the header to select all and then untick any you have an issue with. Entries left unticked stay in your queue. '
             'Use Previous week to see earlier approved hours.</small></p>'
             '%s%s'
             '<p><input type="submit" name="action" value="Approve selected"></p>'
             '<p>Reason (needed to reject) %s <input type="submit" name="action" value="Reject selected"> '
             '<small>Rejected entries go back to the person as drafts. Edited entries can only be approved.</small></p></form>'
             % (hidden(u=u, w=d0), ('<table border="1" cellpadding="3" cellspacing="0">%s%s%s</table>' % (head, "".join(trs), foot)) if people else "",
                APPROVALS_JS, txt("reason", "", 40)))
    return layout(con, u, "Approvals", body)


def act_approval(con, form):
    u = int(form["u"][0])
    ids = [int(x) for x in form.get("entry_id", [])] + [int(x) for v in form.get("entry_ids", []) for x in v.split(",") if x.strip().isdigit()]
    if form.get("approve_person"):  # the Approve all button on a person's line: everything of theirs waiting this week, no ticking
        try:
            who, wk = int(form["approve_person"][0]), monday(datetime.date.fromisoformat(form["w"][0]))
        except (ValueError, KeyError):
            who = None
        if who:
            end = (wk + datetime.timedelta(days=6)).isoformat()
            hq = approval_queue(con, u)
            if hq:
                ids = [r[0] for r in con.execute("select t.entry_id from time_entry t join scope s on s.scope_id=t.scope_id where t.status in ('Submitted','Edited') "
                                                 "and t.user_id=? and t.work_date between ? and ?" + hq[0], [who, wk.isoformat(), end] + list(hq[1]))]
        form = dict(form, action=["Approve selected"])
    action, reason = form["action"][0], form.get("reason", [""])[0].strip()
    approve = action.startswith("Approve")
    q = {"w": form["w"]} if form.get("w") else {}
    if approval_queue(con, u) is None:
        return view_approvals(con, u, q=q)
    if not ids:
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
        except pg.DatabaseError as e:
            msgs.append("Entry %d: %s" % (eid, pg.error_text(e)))
    con.commit()
    verb = "approved" if approve else "sent back to draft"
    ok_msgs = []
    if done:
        ok_msgs.append("%d entr%s %s." % (done, "y" if done == 1 else "ies", verb))
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


PEOPLE_WEEKS = 8  # how far back an approver's booking history reaches to work out who their people are


def view_missing(con, u, q):
    """Who has not completed their timesheet for a week. Cost controllers see everyone who is active that week. An approver sees
    their own people: anyone who has booked to a scope they approve (the scopes of a client they are main approver of, or scopes they are named
    on), internal codes included, in the last PEOPLE_WEEKS weeks up to the week shown."""
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
        since = (w - datetime.timedelta(weeks=PEOPLE_WEEKS)).isoformat()
        scope_sql = (" and p.user_id<>? and p.user_id in (select t.user_id from time_entry t join v_scope_approver va on va.scope_id=t.scope_id "
                     "where va.user_id=? and t.work_date between ? and ?)")
        scope_args = [u, u, since, d6]
    # everyone who is active at some point in the week (start and end dates)
    people = con.execute(
        "select p.user_id, p.nmc_id, p.name, p.employment_type, r.name as title from app_user p join role r on r.role_id=p.role_id "
        "where p.books_time=1 and p.start_date<=? and (p.end_date is null or p.end_date>=?)" + scope_sql + " order by p.name", [d6, d0] + scope_args).fetchall()
    week = {r["user_id"]: r for r in con.execute(
        "select user_id, count(*) filter (where status<>'Draft') as n_sub, count(*) filter (where status='Draft') as n_draft, "
        "sum(case when status<>'Draft' then hours else 0 end) as h_sub, sum(case when status='Draft' then hours else 0 end) as h_draft, "
        "count(*) filter (where status='Draft' and (reject_reason is not null or reopen_reason is not null)) as n_back from time_entry where work_date between ? and ? group by user_id", (d0, d6))}

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
            "" if perm == "controller" else " Your people are those who have booked to your scopes in the last %d weeks." % PEOPLE_WEEKS))
        return layout(con, u, "Missing timesheets", "".join(b))
    b.append("<p><b>%d</b> expected: %s.</p>" % (len(people), ", ".join(
        link(w, st, "%d %s" % (counts[st], st.lower())) for st in order)))
    b.append("<p><small>%s Submitted counts submitted, approved and edited hours. Draft hours are saved but not submitted. "
             "Sent back counts entries an approver rejected or a cost controller reopened that have not been submitted again. "
             "Everyone whose start and end dates cover any part of the week is expected, including the weeks still in progress.</small></p>" % (
                 "Everyone who is active that week." if perm == "controller" else
                 "Your people: anyone who has booked to a scope you approve in the last %d weeks." % PEOPLE_WEEKS))
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
    wks = [r[0] for r in con.execute("select distinct monday_of(work_date) as wk from time_entry where user_id=? and status<>'Draft' "
                                     "order by 1 desc limit 12", (person,))]
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
                   [[dmy(e["work_date"]), e["phase"], e["skill_code"], e["location_name"],
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
    if not ids:
        return view_reopen(con, u, q, ["Nothing to reopen: there are no submitted or approved hours in that week."])
    msgs, n = [], 0
    for eid in ids:
        try:
            con.execute("update time_entry set status='Draft', approved_by=NULL, approved_at=NULL, reject_reason=NULL, reopen_reason=?, "
                        "last_edited_by=?, last_edited_at=? where entry_id=?", (reason, u, stamp(), eid))
            n += 1
        except pg.DatabaseError as e:
            msgs.append("Entry %d: %s" % (eid, pg.error_text(e)))
    ok_msgs = []
    if n:
        who = con.execute("select name from app_user where user_id=?", (person,)).fetchone()[0]
        ok_msgs.append("%d entr%s reopened%s. They are drafts again, with your reason, for %s to correct and submit." % (
            n, "y" if n == 1 else "ies", "", who))
        if "reopen_period" in form:
            con.execute("insert into period (week_start) values (?) on conflict do nothing", (d0,))
            con.execute("update period set status='Open', closed_by=NULL, closed_at=NULL where week_start=?", (d0,))
            ok_msgs.append("The week commencing %s is open again." % w.strftime("%d/%m/%Y"))
    con.commit()
    return view_reopen(con, u, q, msgs + ok_msgs, ok=not msgs)
