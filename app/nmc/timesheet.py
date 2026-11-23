"""The weekly timesheet: showing it, saving hours, booking rules and copying last week. Allowances are not claimed here (payroll sets them by location)."""
import datetime
from nmc import pg
from nmc.core import date_input, dmy, esc, fmt, layout, locations, me_row, monday, msgs_html, parse_adds, stamp, table


TIMESHEET_JS = """<script>
// Keeps the row, day and week totals (normal time, overtime and total) in step with what is typed, before anything is saved.
function fmtH(x){return String(parseFloat(x.toFixed(2)));}
function fill(sel, m){var ds=document.querySelectorAll(sel), k, t; for(k=0;k<ds.length;k++){t=fmtH(m[ds[k].getAttribute('data-day')]||0); if(ds[k].firstChild){ds[k].firstChild.textContent=t;}else{ds[k].textContent=t;}}}
function put(sel, v){var g=document.querySelector(sel); if(g){if(g.firstChild){g.firstChild.textContent=fmtH(v);}else{g.textContent=fmtH(v);}}}
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
  flagDays(dt);
}
function flagDays(dt){
  var names=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'], over=[], tds=document.querySelectorAll('td.dt, td.c'), i, d, t;
  for(i=0;i<tds.length;i++){d=tds[i].getAttribute('data-day'); if(!d){continue;}
    var bad=(dt[d]||0)>24; tds[i].classList.toggle('over', bad&&tds[i].classList.contains('dt'));
    var inp=tds[i].querySelector('input[type=text]'); if(inp){var v=inp.value.trim(); inp.classList.toggle('bad', bad||(v!==''&&(isNaN(Number(v))||Number(v)<0)));}
    if(bad&&tds[i].classList.contains('dt')){t=new Date(d+'T00:00:00'); over.push(names[t.getDay()]+' '+('0'+t.getDate()).slice(-2)+'/'+('0'+(t.getMonth()+1)).slice(-2)+' ('+fmtH(dt[d])+' hours)');}}
  var box=document.getElementById('dayerr'); if(box){box.style.display=over.length?'block':'none'; box.textContent=over.length?'Too many hours in one day: '+over.join(', ')+'. You cannot book more than 24 hours in a day, so those hours will not be saved.':'';}
}
function showOt(a){var tr=a.closest('tr'), nx=tr.nextElementSibling; if(nx){nx.style.display='';var i=nx.querySelector('input[type=text]'); if(i){i.focus();}} a.style.display='none'; return false;}
function filterCtr(q){q=q.toLowerCase();var s=document.getElementById('addphase'),o=s.options,i,first=null;
  for(i=0;i<o.length;i++){var m=o[i].text.toLowerCase().indexOf(q)>=0;o[i].hidden=!m;o[i].disabled=!m;if(m&&!first){first=o[i];}}
  if(first&&(s.selectedIndex<0||o[s.selectedIndex].disabled)){first.selected=true;}}
document.addEventListener('input',function(e){if(e.target.name&&e.target.name.indexOf('h_')===0){recalc();}});
window.addEventListener('load',recalc);
</script>"""


def stretches(d0, d6):
    """A week as one or two stretches of days. A week that runs over a calendar month end is split there (for example Mon 30 Nov and
    Tue 1 - Sun 6 Dec), so the month's hours can be submitted and locked while the new month stays open."""
    a, b = datetime.date.fromisoformat(d0), datetime.date.fromisoformat(d6)
    if a.month == b.month:
        return [(d0, d6)]
    last = b.replace(day=1) - datetime.timedelta(days=1)
    return [(d0, last.isoformat()), ((last + datetime.timedelta(days=1)).isoformat(), d6)]


def stretch_locked(con, u, d0, d6):
    """A stretch is locked once it has been submitted: it has hours past Draft and nothing left in Draft. Sent-back or
    reopened entries go back to Draft, which opens it again."""
    non_draft, draft = con.execute(
        "select count(*) filter (where status<>'Draft'), count(*) filter (where status='Draft') from time_entry where user_id=? and work_date between ? and ?", (u, d0, d6)).fetchone()
    return non_draft > 0 and draft == 0


def locked_days(con, u, d0, d6):
    """The dates in the week that sit in a submitted stretch: nothing can be added to or changed on them."""
    days = set()
    for a, b in stretches(d0, d6):
        if stretch_locked(con, u, a, b):
            n = (datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days
            days.update((datetime.date.fromisoformat(a) + datetime.timedelta(days=i)).isoformat() for i in range(n + 1))
    return days


def sheet_locked(con, u, d0, d6):
    """The whole sheet is locked when every stretch of the week is submitted. A week that crosses a month end can have its first
    stretch submitted and locked while the second stays open."""
    return all(stretch_locked(con, u, a, b) for a, b in stretches(d0, d6))


LOCKED_MSG = "This timesheet has been submitted and is locked. Ask a cost controller to reopen it if something needs changing."


def view_timesheet(con, u, w, msgs=None, adds=None, ok=False, typed=None):
    adds = adds or []
    typed = typed or {}  # what was typed in boxes that could not be saved, so it is not lost
    me = me_row(con, u)
    if not me:
        return layout(con, u, "My Timesheet", "<p>Unknown user.</p>")
    if me["permission"] == "admin":
        return layout(con, u, "My Timesheet", '<p>The IT admin account is a system account and does not book time. '
                      'Use <a href="/admin?u=%d">Admin</a> to manage users, job titles and locations.</p>' % u)
    if not me["books_time"]:
        return layout(con, u, "My Timesheet", '<div class="empty"><p><b>You are not set up to write time</b>, so there is no timesheet for you and nobody will chase you for one.</p>'
                      "<p>If that is wrong, ask the cost team to change it on your user record.</p></div>")
    days = [w + datetime.timedelta(days=i) for i in range(7)]
    d0, d6 = days[0].isoformat(), days[6].isoformat()
    period = con.execute("select * from period where week_start=?", (d0,)).fetchone()
    open_week = bool(period and period["status"] == "Open")
    locked = sheet_locked(con, u, d0, d6)
    can_edit = open_week and not locked  # a submitted sheet is locked as a whole: no new hours or rows either
    lock_days = locked_days(con, u, d0, d6)  # in a week over a month end, the submitted month's days alone are locked
    parts = stretches(d0, d6)
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
    # every booking shows its normal-time row and, directly under it, an overtime row: hidden until it has hours or "+ Overtime" is clicked
    for (pid, rid, lid, ot) in list(rows):
        if (pid, rid, lid, 1 - ot) not in rows:
            rows[(pid, rid, lid, 1 - ot)] = dict(rows[(pid, rid, lid, ot)], overtime=1 - ot, cells={}, auto=True)
    ordered = {}
    for k in rows:
        for ot in (0, 1):
            if (k[0], k[1], k[2], ot) in rows and (k[0], k[1], k[2], ot) not in ordered:
                ordered[(k[0], k[1], k[2], ot)] = rows[(k[0], k[1], k[2], ot)]
    rows = ordered

    b = []
    b.append(msgs_html(msgs, ok))
    b.append('<div id="dayerr" class="msg bad" style="display:none"></div>')
    prev_w, next_w = (w - datetime.timedelta(days=7)).isoformat(), (w + datetime.timedelta(days=7)).isoformat()
    b.append('<div class="wk"><span class="nav"><a href="/timesheet?u=%d&amp;w=%s">&lt;&lt; Previous week</a> '
             '<a href="/timesheet?u=%d&amp;w=%s">Next week &gt;&gt;</a> <a href="/timesheet?u=%d&amp;w=%s">This week</a></span>'
             '<span class="cur-week">Week commencing %s</span>'
             '<form method="get" action="/timesheet"><input type="hidden" name="u" value="%d">Go to the week containing %s <input type="submit" value="Go"></form></div>' % (
                 u, prev_w, u, next_w, u, monday(datetime.date.today()).isoformat(), w.strftime("%d/%m/%Y"), u, date_input("w", d0)))
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
    b.append('<p class="stat"><b>Week status:</b> %s</p>' % status_line)
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
            groups.setdefault((e[reason_key], by), []).append("%s, %s: %s hours (%s%s)" % (
                esc(e["phase"]), datetime.date.fromisoformat(e["work_date"]).strftime("%a %d/%m"), fmt(e["hours"]),
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
        "select monday_of(work_date) as wk, count(*) as n from time_entry "
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

    adds_hidden = "".join('<input type="hidden" name="add" value="%d:%d:%d:%d">' % k for k in rows if not rows[k]["cells"] and not rows[k].get("auto"))
    b.append('<form id="tsform" method="post" action="/timesheet/save">'
             '<input type="hidden" name="u" value="%d"><input type="hidden" name="w" value="%s">%s' % (u, d0, adds_hidden))
    head = ["Booking"] + [d.strftime("%a %d/%m") for d in days] + ["Total", ""]
    trs = []
    day_tot = {d.isoformat(): 0.0 for d in days}
    day_norm, day_ot = dict(day_tot), dict(day_tot)  # the same, split into normal time and overtime
    for (pid, rid, lid, ot), r in rows.items():
        ot_hidden = bool(ot and not r["cells"] and r.get("auto"))  # an overtime line nobody has asked for yet
        link = ""
        if not ot and can_edit and rows.get((pid, rid, lid, 1), {}).get("auto") and not rows[(pid, rid, lid, 1)]["cells"]:
            link = ' <a href="#" class="addot" onclick="return showOt(this)">+ Overtime</a>'
        if ot:
            tds = ['<td class="bk"><span class="sub">%s</span><br><span class="chips">%s &middot; %s &middot; <b>Overtime</b></span></td>' % (
                esc(r["phase"]), esc(r["skill"]), esc(r["location"]))]
        else:
            tds = ['<td class="bk"><b>%s</b><br><span class="sub">%s &rsaquo; %s &rsaquo; %s</span><br><span class="chips">%s &middot; %s &middot; Normal%s</span></td>' % (
                esc(r["phase"]), esc(r["client"]), esc(r["project"]), esc(r["scope"]), esc(r["skill"]), esc(r["location"]), link)]
        rt = 0.0
        for d in days:
            iso = d.isoformat()
            e = r["cells"].get(iso)
            name = "h_%d_%d_%d_%d_%s" % (pid, rid, lid, ot, iso)
            fixed = ""  # hours the person cannot type over (shown as text), so the live totals can still count them
            if e is None:
                cell = '<input type="text" size="4" name="%s" value="%s">' % (name, esc(typed.get(name, ""))) if can_edit and active(iso) and iso not in lock_days else ("&ndash;" if can_edit else "&nbsp;")
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
                    cell = '<input type="text" size="4" name="%s" value="%s"%s>' % (name, esc(typed.get(name, fmt(e["hours"]))), why)
            tds.append('<td align="center" class="c%s" data-day="%s"%s>%s</td>' % (" we" if d.weekday() >= 5 else "", iso, fixed, cell))
        tds.append('<td align="right" class="rt">%s</td>' % fmt(rt))
        mine = [rows[(pid, rid, lid, o)]["cells"].values() for o in (0, 1)]
        if not ot and can_edit and not any(e["status"] != "Draft" for cells in mine for e in cells):  # a booking with submitted or approved hours cannot be deleted
            has_hours = any(cells for cells in mine)
            tds.append('<td class="del"><button type="submit" name="delrow" value="h:%d:%d:%d" class="delrow" title="Delete this row"%s>&times;</button></td>' % (
                pid, rid, lid, ' onclick="return confirm(\'Delete this row and its draft hours?\')"' if has_hours else ""))
        else:
            tds.append('<td class="del"></td>')
        trs.append('<tr class="row%s" data-ot="%d"%s>%s</tr>' % (" otrow" if ot else "", ot, ' style="display:none"' if ot_hidden else "", "".join(tds)))
    if not trs:
        trs.append('<tr><td colspan="%d"><div class="empty"><b>Nothing booked this week yet.</b>%s</div></td></tr>' % (
            len(head), " Click <b>+ Add timesheet row</b> above to choose a CTR, then enter your hours." if can_edit else ""))

    def sum_row(label, day_cls, week_cls, per_day, row_cls=""):  # Normal time, Overtime and Total lines under the grid
        return '<tr class="%s"><td align="right"><b>%s</b></td>%s<td align="right" class="%s"><b>%s</b></td><td></td></tr>' % (
            row_cls, label, "".join('<td align="center" class="%s" data-day="%s"><b>%s</b></td>' % (day_cls, d.isoformat(), fmt(per_day[d.isoformat()])) for d in days),
            week_cls, fmt(sum(per_day.values())))
    has_ot = any(k[3] and (rows[k]["cells"] or not rows[k].get("auto")) for k in rows)  # the normal time and overtime lines only matter once there is overtime
    foot = ((sum_row("Normal time", "dn", "gn", day_norm, "sum") + sum_row("Overtime", "do", "go", day_ot, "sum")) if has_ot else
            '<tr style="display:none"><td></td>%s<td class="gn"></td></tr><tr style="display:none"><td></td>%s<td class="go"></td></tr>' % (
                "".join('<td class="dn" data-day="%s"><b>0</b></td>' % d.isoformat() for d in days), "".join('<td class="do" data-day="%s"><b>0</b></td>' % d.isoformat() for d in days)))
    foot += sum_row("Total", "dt", "gt", day_tot, "tot")
    hdr = "".join('<th class="%s">%s</th>' % ("bkh" if i == 0 else ("we" if 1 <= i <= 7 and days[i - 1].weekday() >= 5 else ""), esc(h).replace(" ", "<br>", 1) if 1 <= i <= 7 else esc(h))
                  for i, h in enumerate(head))
    b.append('<div class="tools"><!--ADDROW--></div>')
    b.append('<table class="ts" border="1" cellpadding="3" cellspacing="0"><tr>%s</tr>%s%s</table>' % (hdr, "".join(trs), foot))
    b.append(TIMESHEET_JS)
    # after Save, Add row, Copy and Submit (posted to /timesheet/...), put the address back to the timesheet itself so a refresh does not re-post the form
    b.append("<script>if(location.pathname.indexOf('/timesheet/')===0){history.replaceState(null,'','/timesheet?u=%d&w=%s');}</script>" % (u, d0))
    by_status = {"Approved": 0.0, "Edited": 0.0, "Submitted": 0.0, "Draft": 0.0}
    for e in entries:
        by_status[e["status"]] += e["hours"]
    b.append("<p><small><b>Hours this week (all statuses): %s</b> &nbsp; Approved %s, Edited %s, Submitted %s, Draft %s</small></p>" % (
        fmt(sum(by_status.values())), fmt(by_status["Approved"]), fmt(by_status["Edited"]), fmt(by_status["Submitted"]), fmt(by_status["Draft"])))
    nights = con.execute("select count(*) from v_offshore_night where user_id=? and work_date between ? and ?", (u, d0, d6)).fetchone()[0]
    if nights:  # worked out from the offshore hours, so nothing is claimed: a night is earned when offshore that day and the next
        b.append("<p><small><b>Offshore allowance:</b> %d night%s this week (a night is earned when you are offshore that day and the next, so the last offshore day of a trip earns none). "
                 "It is worked out from your hours, so there is nothing to claim.</small></p>" % (nights, "" if nights == 1 else "s"))
    if not locked:
        b.append("<p><small>To clear a draft entry, empty its box and click Save draft.</small></p>")
    if len(parts) == 2 and not locked:
        m = lambda iso: datetime.date.fromisoformat(iso).strftime("%d/%m")
        month, upto = datetime.date.fromisoformat(parts[0][1]).strftime("%B"), m(parts[0][1])
        if stretch_locked(con, u, *parts[0]):
            b.append("<p><small><b>Month end:</b> your hours up to %s are submitted and locked. %s onwards stays open for you to enter.</small></p>" % (upto, m(parts[1][0])))
        else:
            b.append("<p><small><b>Month end:</b> this week runs over the end of %s. Use <b>Submit up to %s</b> to submit and lock %s on its own; "
                     "%s onwards stays open for you to enter.</small></p>" % (month, upto, "the month's hours" if parts[0][0] != parts[0][1] else upto, m(parts[1][0])))
    if any(e["status"] != "Draft" for e in entries):
        b.append("<p><small>Submitted and approved hours are locked. If one needs changing, ask a cost controller to reopen the timesheet.</small></p>")
    if locked:
        b.append('<div class="actions"><b>Locked.</b> <small>This timesheet has been submitted. Ask a cost controller to reopen it if something needs changing.</small></div></form>')
    elif not can_edit:  # a week that is not open (not started by the admin, or closed): nothing can be saved or submitted
        b.append("</form>")
    else:
        part_btn = ""
        if len(parts) == 2 and not stretch_locked(con, u, *parts[0]):
            part_btn = '<input type="submit" name="submit_part" value="Submit up to %s"> ' % datetime.date.fromisoformat(parts[0][1]).strftime("%d/%m")
        b.append('<div class="actions"><input type="submit" name="action" value="Save draft"> %s'
                 '<input type="submit" name="action" value="Submit week"> <small>Save draft keeps your hours private; Submit week sends them for approval.</small></div></form>' % part_btn)

    # add a row
    scopes = con.execute(
        "select ph.phase_id, (s.scope_code || '-' || coalesce(ph.ctr_code, upper(ph.name))) as phase, ph.name as phase_name, s.scope_code, s.description, pr.name as project, c.name as client, c.is_internal "
        "from phase ph join scope s on s.scope_id=ph.scope_id join project pr on pr.project_id=s.project_id "
        "join client c on c.client_id=s.client_id "
        "where s.status='Open' and ph.status='Open' and pr.status='Open' "  # anyone can book to any open code, unless it is restricted
        "and (ph.restricted=0 or exists (select 1 from phase_person pp where pp.phase_id=ph.phase_id and pp.user_id=?)) "  # transaction controls:
        "and (ph.book_to is null or ph.book_to >= ?) and (ph.book_from is null or ph.book_from <= ?) "  # people, and booking dates
        "order by c.is_internal, c.name, pr.name, s.scope_code, ph.name", (u, d0, d6)).fetchall()
    roles = con.execute("select role_id, skill_code, name from role where active=1 order by skill_code").fetchall()
    add_button = ""  # the "Add timesheet row" button above the grid, when a row can be added
    if week_inactive or locked:
        pass  # the notes above say why nothing can be added
    elif scopes:
        def label(s):
            if s["is_internal"]:  # e.g. "Internal - Business Services"
                return "%s - %s%s" % (esc(s["client"]), esc(s["description"]), "" if s["phase_name"] == "General" else " &gt; %s" % esc(s["phase"]))
            return "%s &gt; %s &gt; %s %s &gt; %s" % (esc(s["client"]), esc(s["project"]), esc(s["scope_code"]), esc(s["description"]), esc(s["phase"]))
        so = "".join('<option value="%d">%s</option>' % (s["phase_id"], label(s)) for s in scopes)
        ro = "".join('<option value="%d"%s>%s %s</option>' % (r["role_id"], " selected" if r["role_id"] == me["role_id"] else "", esc(r["skill_code"]), esc(r["name"])) for r in roles)
        lo = "".join('<option value="%d">%s</option>' % (lid, esc(name)) for lid, name in locations(con))
        so_list = "".join('<option value="%d"%s>%s</option>' % (s["phase_id"], " selected" if i == 0 else "", label(s)) for i, s in enumerate(scopes))
        dlg = ['<dialog id="addrow" class="dlg"><div class="dlg-in"><h4>Add a timesheet row</h4>'
               '<p><small>Choose a CTR, typing in the box to search. Hours you have typed are saved first. Overtime is added on the row itself, with <b>+ Overtime</b>.</small></p>'
               '<p><input type="text" id="addq" placeholder="Search by client, project, scope or CTR No" autocomplete="off" oninput="filterCtr(this.value)"></p>'
               '<p><select form="tsform" name="add_phase" id="addphase" size="8">%s</select></p>'
               '<details><summary>Different skill code or location</summary>'
               '<p>Skill code <select form="tsform" name="add_role">%s</select> Location <select form="tsform" name="add_loc">%s</select></p>'
               "<p><small>Your own skill code is pre-selected. Choose a different one if you are covering another role. "
               "Hours NMC pays for itself (not billed to a client) go to an Internal code, for example Internal - Business Services.</small></p></details>"
               '<p class="dlg-actions"><input form="tsform" type="submit" name="action" value="Add row"> '
               '<input type="button" value="Cancel" onclick="this.closest(\'dialog\').close()"></p>' % (so_list, ro, lo)]
        dlg.append("</div></dialog>")
        b.append("".join(dlg))
        add_button = '' if not can_edit else '<input type="button" value="+ Add timesheet row" onclick="document.getElementById(\'addrow\').showModal()">'
    else:
        b.append("<p>There are no open booking codes you can book to.</p>")
    return layout(con, u, "My Timesheet", "".join(b).replace("<!--ADDROW-->", add_button))


def book_entry(con, u, pid, rid, lid, ot, date, hours):
    """Create one Draft entry for u. Returns None when it is booked, or a short reason when it cannot be (no rate, location switched
    off). The database rules (open scope, start and end dates, open week) raise a database error (pg.DatabaseError) instead."""
    info = con.execute("select s.scope_id, s.client_id, c.is_internal from phase ph join scope s on s.scope_id=ph.scope_id "
                       "join client c on c.client_id=s.client_id where ph.phase_id=?", (pid,)).fetchone()
    if not info:
        return "that booking code does not exist."
    loc = con.execute("select active from location where location_id=?", (lid,)).fetchone()
    if not loc or not loc[0] or ot not in (0, 1):
        return "that location is not available."
    if info["is_internal"]:  # NMC pays for these hours: no sales rate
        rate_id = None
    else:
        # The rate is the client's rate for the skill code booked (the person's job title, unless they are covering another role), at the
        # rate in force on the work date (rates are never edited, a new one is added with a start date).
        rate = con.execute("select rate_id from rate where client_id=? and role_id=? and effective_from<=? "
                           "order by effective_from desc limit 1", (info["client_id"], rid, date)).fetchone()
        if not rate:
            return "no rate for that skill code on this client."
        rate_id = rate[0]
    con.execute("insert into time_entry (user_id, scope_id, phase_id, worked_role_id, rate_id, work_date, hours, "
                "status, created_by, location_id, overtime) values (?,?,?,?,?,?,?, 'Draft', ?, ?, ?)",
                (u, info["scope_id"], pid, rid, rate_id, date, hours, u, lid, ot))
    return None


def friendly(problem):
    """The database's booking-rule messages, put the way a person would say them."""
    return str(problem).replace("A person cannot book more than 24 hours in one day", "you cannot book more than 24 hours in one day")


def save_timesheet(con, form):
    u, w = int(form["u"][0]), datetime.date.fromisoformat(form["w"][0])
    action = form.get("action", ["Save draft"])[0]
    adds = parse_adds(form.get("add", []))
    msgs, saved, cleared, typed = [], 0, 0, {}
    d0, d6 = w.isoformat(), (w + datetime.timedelta(days=6)).isoformat()
    if sheet_locked(con, u, d0, d6):  # nothing at all can be added to or changed on a submitted sheet
        return view_timesheet(con, u, w, [LOCKED_MSG])
    frozen = locked_days(con, u, d0, d6)  # days in an already submitted stretch (a month end inside the week)
    delete = form.get("delrow", [""])[0].split(":")  # "h:phase:role:location"
    try:
        del_h = tuple(int(x) for x in delete[1:4]) if delete[0] == "h" and len(delete) == 4 else None
    except ValueError:
        del_h = None
    for key, vals in form.items():
        if not key.startswith("h_"):
            continue
        try:
            _, pid, rid, lid, ot, date = key.split("_", 5)
            pid, rid, lid, ot = int(pid), int(rid), int(lid), int(ot)
        except ValueError:
            continue
        if del_h == (pid, rid, lid):  # the row being deleted is not saved first
            continue
        raw = vals[0].strip()
        info = con.execute("select s.scope_id, s.scope_code, (s.scope_code || '-' || coalesce(ph.ctr_code, upper(ph.name))) as phase from phase ph join scope s on s.scope_id=ph.scope_id "
                           "where ph.phase_id=?", (pid,)).fetchone()
        if not info:
            continue
        label = "%s on %s" % (datetime.date.fromisoformat(date).strftime("%a %d/%m"), info["phase"])
        existing = con.execute("select * from time_entry where user_id=? and phase_id=? and worked_role_id=? and work_date=? "
                               "and location_id=? and overtime=?", (u, pid, rid, date, lid, ot)).fetchone()
        if raw == "":
            if existing and existing["status"] == "Draft":
                try:
                    con.execute("update time_entry set last_edited_by=?, last_edited_at=? where entry_id=?",
                                (u, stamp(), existing["entry_id"]))
                    con.execute("delete from time_entry where entry_id=?", (existing["entry_id"],))
                    cleared += 1
                except pg.DatabaseError as e:
                    msgs.append("%s: %s" % (label, pg.error_text(e)))
            elif existing:
                msgs.append("%s: %s hours are locked. Ask a cost controller to reopen the timesheet." % (label, existing["status"].lower()))
            continue
        try:
            hours = float(raw)
        except ValueError:
            msgs.append("%s: '%s' is not a number." % (label, raw))
            typed[key] = raw
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
                if date in frozen:
                    msgs.append("%s: that day has been submitted and is locked. Ask a cost controller to reopen it." % label)
                    continue
                problem = book_entry(con, u, pid, rid, lid, ot, date, hours)
                if problem:
                    msgs.append("%s: %s" % (label, friendly(problem)))
                    typed[key] = raw
                    continue
            saved += 1
        except pg.DatabaseError as e:
            msgs.append("%s: %s" % (label, friendly(pg.error_text(e))))
            typed[key] = raw
    for key in typed:  # a row whose hours could not be saved stays on the grid so what was typed can be corrected
        _, pid, rid, lid, ot, _date = key.split("_", 5)
        adds += parse_adds(["%s:%s:%s:%s" % (pid, rid, lid, ot)])
    if action == "Add row" and form.get("add_phase") and form.get("add_role"):  # saved above; now show the new empty row
        adds += parse_adds(["%s:%s:%s:%s" % (form["add_phase"][0], form["add_role"][0], form.get("add_loc", ["1"])[0], "1" if "add_ot" in form else "0")])
    ok_msgs = []
    if del_h:
        rowset = con.execute("select entry_id, status from time_entry where user_id=? and phase_id=? and worked_role_id=? and location_id=? and work_date between ? and ?",
                             (u, del_h[0], del_h[1], del_h[2], d0, d6)).fetchall()
        if any(x["status"] != "Draft" for x in rowset):
            msgs.append("That row has submitted or approved hours, so it cannot be deleted. Ask a cost controller to reopen the timesheet.")
        else:
            try:
                for x in rowset:
                    con.execute("update time_entry set last_edited_by=?, last_edited_at=? where entry_id=?", (u, stamp(), x["entry_id"]))
                    con.execute("delete from time_entry where entry_id=?", (x["entry_id"],))
                adds = [a for a in adds if tuple(a[:3]) != del_h]
                ok_msgs.append("Row deleted%s." % (" (%d draft entr%s removed)" % (len(rowset), "y" if len(rowset) == 1 else "ies") if rowset else ""))
            except pg.DatabaseError as e:
                msgs.append("Delete failed: %s" % pg.error_text(e))
    if saved:
        ok_msgs.append("%d entr%s saved." % (saved, "y" if saved == 1 else "ies"))
    if cleared:
        ok_msgs.append("%d draft entr%s cleared." % (cleared, "y" if cleared == 1 else "ies"))
    part = form.get("submit_part", [""])[0]
    if part:
        action = "Submit up to"
    if action in ("Submit week", "Submit up to"):
        a, b = d0, d6
        if action == "Submit up to":
            a, b = stretches(d0, d6)[0]  # the days up to the month end; the days after it stay open
        try:
            n = con.execute("update time_entry set status='Submitted', last_edited_by=?, last_edited_at=? "
                            "where user_id=? and status='Draft' and work_date between ? and ?", (u, stamp(), u, a, b)).rowcount
            if n:
                ok_msgs.append("%d entr%s submitted for approval." % (n, "y" if n == 1 else "ies"))
            if not n:
                msgs.append("Nothing to submit: there are no draft hours in %s." % ("this week" if (a, b) == (d0, d6) else "the days up to %s" % datetime.date.fromisoformat(b).strftime("%d/%m")))
        except pg.DatabaseError as e:
            msgs.append("Submit failed: %s" % pg.error_text(e))
    con.commit()
    body_msgs = ok_msgs if not msgs else msgs + ok_msgs
    return view_timesheet(con, u, w, body_msgs, adds, ok=not msgs, typed=typed)


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
        except pg.DatabaseError as ex:
            problem = pg.error_text(ex)
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


def history_tabs(u, view, m):
    return ('<div class="tabs"><a href="/history?u=%d&amp;view=calendar&amp;m=%s"%s>Calendar</a><a href="/history?u=%d&amp;view=list"%s>List of weeks</a></div>' % (
        u, m, ' class="cur"' if view == "calendar" else "", u, ' class="cur"' if view == "list" else ""))


def view_history(con, u, q=None):
    """My past timesheets: a month calendar of the hours booked each day (the default), or a list of weeks. Either way each day or week opens that week's timesheet."""
    q = q or {}
    me = me_row(con, u)
    if not me or me["permission"] == "admin" or not me["books_time"]:
        return layout(con, u, "Timesheet history", "<p>This account does not write time, so there is no history.</p>")
    if q.get("view", ["calendar"])[0] != "list":
        return history_calendar(con, u, q)
    weeks = con.execute(
        "select monday_of(work_date) as wk, sum(hours) as total, "
        "sum(case when status in ('Approved','Edited') then hours else 0 end) as approved, sum(case when status='Submitted' then hours else 0 end) as submitted, "
        "sum(case when status='Draft' then hours else 0 end) as draft, count(distinct work_date) as days, "
        "sum(case when status='Draft' and (reject_reason is not null or reopen_reason is not null) then 1 else 0 end) as back "
        "from time_entry where user_id=? group by wk order by wk desc", (u,)).fetchall()
    months = con.execute(
        "select substr(work_date, 1, 7) as m, sum(hours) as total, sum(case when status in ('Approved','Edited') then hours else 0 end) as approved, "
        "count(distinct work_date) as days from time_entry where user_id=? group by m order by m desc", (u,)).fetchall()
    if not weeks:
        return layout(con, u, "Timesheet history", '<div class="empty"><p><b>Nothing booked yet.</b></p><p>Your past weeks will be listed here once you have entered hours on '
                      '<a href="/timesheet?u=%d">My Timesheet</a>.</p></div>' % u)
    b = ["<h4>By month</h4>",
         table(["Month", "Days booked", "Hours", "Approved hours"],
               [[datetime.date.fromisoformat(r["m"] + "-01").strftime("%B %Y"), r["days"], fmt(r["total"]), fmt(r["approved"])] for r in months]),
         "<h4>By week</h4>"]
    rows = []
    for r in weeks:
        status = ("Sent back or reopened: please check" if r["back"] else "Approved" if r["total"] == r["approved"] else "Part approved" if r["approved"] and r["submitted"] and not r["draft"] else "Waiting for approval" if r["submitted"] and not r["draft"]
                  else "Not submitted" if r["draft"] and not r["submitted"] and not r["approved"] else "Part submitted")
        rows.append([dmy(r["wk"]), r["days"], fmt(r["total"]), fmt(r["approved"]), fmt(r["submitted"]), fmt(r["draft"]), status,
                     '<a href="/timesheet?u=%d&amp;w=%s">Open</a>' % (u, r["wk"])])
    b.append(table(["Week commencing", "Days booked", "Hours", "Approved", "Submitted", "Draft", "State", ""], rows, raw_cols=(7,)))
    b.append("<p><small>Open a week to see its lines, or to correct hours that were sent back. Approved weeks can only be changed by the cost team.</small></p>")
    return layout(con, u, "Timesheet history", "".join(b))


def history_calendar(con, u, q):
    """One month as a Monday-to-Sunday grid: each day shows the hours booked and is coloured by where they stand; each week ends with its total and a link."""
    today = datetime.date.today()
    try:
        first = datetime.date.fromisoformat((q.get("m", [""])[0] or "x") + "-01")
    except ValueError:
        first = today.replace(day=1)
    last = (first.replace(day=28) + datetime.timedelta(days=4)).replace(day=1) - datetime.timedelta(days=1)
    start = first - datetime.timedelta(days=first.weekday())
    end = last + datetime.timedelta(days=6 - last.weekday())
    prev = (first - datetime.timedelta(days=1)).replace(day=1)
    nxt = last + datetime.timedelta(days=1)
    m = first.strftime("%Y-%m")
    ents = con.execute(
        "select t.work_date, t.hours, t.status, t.overtime, lo.name as location, (s.scope_code || '-' || coalesce(ph.ctr_code, upper(ph.name))) as ctr, "
        "(t.reject_reason is not null or t.reopen_reason is not null) as back "
        "from time_entry t join scope s on s.scope_id=t.scope_id join phase ph on ph.phase_id=t.phase_id join location lo on lo.location_id=t.location_id "
        "where t.user_id=? and t.work_date between ? and ? order by t.work_date, ctr", (u, start.isoformat(), end.isoformat())).fetchall()
    days = {}
    for e in ents:
        days.setdefault(e["work_date"], []).append(e)
    in_month = [e for e in ents if first.isoformat() <= e["work_date"] <= last.isoformat()]
    total = sum(e["hours"] for e in in_month)
    approved = sum(e["hours"] for e in in_month if e["status"] in ("Approved", "Edited"))
    b = [history_tabs(u, "calendar", m),
         '<div class="mnav"><span><a href="/history?u=%d&amp;m=%s">&lt;&lt; %s</a></span> <span class="cur-month">%s</span> <span><a href="/history?u=%d&amp;m=%s">%s &gt;&gt;</a></span> '
         '<span><a href="/history?u=%d&amp;m=%s">This month</a></span>'
         '<form method="get" action="/history"><input type="hidden" name="u" value="%d"> Month <input type="month" name="m" value="%s"> <input type="submit" value="Go"></form></div>' % (
             u, prev.strftime("%Y-%m"), prev.strftime("%b %Y"), first.strftime("%B %Y"), u, nxt.strftime("%Y-%m"), nxt.strftime("%b %Y"),
             u, today.strftime("%Y-%m"), u, m),
         '<p class="stat">%s hours booked in %s, %s of them approved</p>' % (fmt(total), esc(first.strftime("%B")), fmt(approved)),
         '<table class="cal"><tr>%s<th>Week</th></tr>' % "".join("<th>%s</th>" % d for d in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"))]
    d = start
    while d <= end:
        wk = [d + datetime.timedelta(days=i) for i in range(7)]
        cells, wtotal = [], 0
        for x in wk:
            es = days.get(x.isoformat(), [])
            hours = sum(e["hours"] for e in es)
            wtotal += hours
            cls = ["out"] if x.month != first.month else []
            if x.weekday() >= 5:
                cls.append("we")
            if x == today:
                cls.append("today")
            tip = ""
            if es:
                state = ("back" if any(e["back"] for e in es) else "draft" if any(e["status"] == "Draft" for e in es) else "sub" if any(e["status"] == "Submitted" for e in es) else "ok")
                cls.append(state)
                tip = ' title="%s"' % esc("; ".join("%s %sh" % (e["ctr"], fmt(e["hours"])) for e in es))
                tags = ", ".join(sorted({"offshore" for e in es if e["location"] == "Offshore"} | {"overtime" for e in es if e["overtime"]}))
                body = '<b>%s</b>%s' % (fmt(hours), ("<br><span>%s</span>" % esc(tags)) if tags else "")
            else:
                body = ""
            cells.append('<td class="%s"%s><a href="/timesheet?u=%d&amp;w=%s"><i>%d</i>%s</a></td>' % (" ".join(cls), tip, u, wk[0].isoformat(), x.day, body))
        b.append("<tr>%s<td class=\"wt\">%s<br><a href=\"/timesheet?u=%d&amp;w=%s\">Open</a></td></tr>" % ("".join(cells), fmt(wtotal) if wtotal else "", u, wk[0].isoformat()))
        d += datetime.timedelta(days=7)
    b.append("</table>")
    b.append('<p class="legend"><span class="ok">Approved</span> <span class="sub">Waiting for approval</span> <span class="draft">Not submitted</span> '
             '<span class="back">Sent back or reopened</span></p>')
    b.append("<p><small>Each day shows the hours booked. Hover a day to see its codes; select a day to open that week's timesheet.</small></p>")
    if not in_month and not con.execute("select 1 from time_entry where user_id=? limit 1", (u,)).fetchone():
        return layout(con, u, "Timesheet history", '<div class="empty"><p><b>Nothing booked yet.</b></p><p>Your past weeks will be shown here once you have entered hours on '
                      '<a href="/timesheet?u=%d">My Timesheet</a>.</p></div>' % u)
    return layout(con, u, "Timesheet history", "".join(b))
