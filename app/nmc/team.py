"""Looking at other people's timesheets: a line manager (or the approver named on someone's record) sees their people and can open any week of any of their
timesheets; cost controllers can look at anyone's. Read-only, and drafts stay private (they are not submitted yet)."""
import datetime
from nmc.core import date_input, dmy, esc, fmt, hidden, layout, monday, no_access, permission_of


def can_view(con, viewer, person):
    """May this person look at that person's timesheet? Themselves, their line manager or named approver, and cost controllers."""
    if viewer == person or permission_of(con, viewer) == "controller":
        return True
    return con.execute("select 1 from app_user where user_id=? and (line_manager_id=? or approver_id=?)", (person, viewer, viewer)).fetchone() is not None


def view_team(con, u, q):
    """The people this person manages or approves for (everyone, for a cost controller, with a search), with last week and this week at a glance."""
    ctl = permission_of(con, u) == "controller"
    if not ctl and con.execute("select 1 from app_user where line_manager_id=? or approver_id=? limit 1", (u, u)).fetchone() is None:
        return no_access(con, u, "Team")
    needle = ((q or {}).get("f_q", [""])[0]).strip()
    this_w = monday(datetime.date.today())
    last_w = this_w - datetime.timedelta(days=7)
    rows = con.execute(
        "select p.user_id, p.nmc_id, p.name, p.employment_type, r.name as title, p.books_time, p.hours_per_week, "
        "coalesce(ap.name, lm.name) as approver_name, "
        "(select submitted_hours from v_weekly_hours h where h.user_id=p.user_id and h.week_start=?) as last_sub, "
        "(select total_hours from v_weekly_hours h where h.user_id=p.user_id and h.week_start=?) as this_total "
        "from app_user p join role r on r.role_id=p.role_id left join app_user lm on lm.user_id=p.line_manager_id left join app_user ap on ap.user_id=p.approver_id "
        "where p.user_id<>? and (? or p.line_manager_id=? or p.approver_id=?) "
        "and (? = '' or p.name ilike '%' || ? || '%' or p.nmc_id ilike '%' || ? || '%') order by p.name",
        (last_w.isoformat(), this_w.isoformat(), u, ctl, u, u, needle, needle, needle)).fetchall()
    b = []
    if ctl:
        b.append('<form method="get" action="/team">%s<p>Find a person (name or NMC ID) <input type="text" name="f_q" size="20" value="%s"> <input type="submit" value="Search"></p></form>' % (
            hidden(u=u), esc(needle)))
    else:
        b.append("<p><small>The people you manage, and anyone whose approver you are named as. Open a person to see any week of their timesheet.</small></p>")
    if not rows:
        return layout(con, u, "Team", "".join(b) + "<p>Nobody to show.</p>")
    trs = []
    for r in rows:
        trs.append('<tr><td><b>%s</b><span class="sub">%s</span></td><td>%s</td><td>%s</td><td align="right">%s</td><td align="right">%s</td><td align="right">%s</td><td>%s</td></tr>' % (
            esc(r["name"]), esc("%s, %s" % (r["title"], r["nmc_id"])), esc(r["employment_type"]), esc(r["approver_name"] or "a cost controller"),
            fmt(r["last_sub"]) if r["last_sub"] else "&ndash;", fmt(r["this_total"]) if r["this_total"] else "&ndash;", fmt(r["hours_per_week"]),
            ('<a href="/team/timesheet?u=%d&amp;p=%d&amp;w=%s">Timesheet</a> &middot; <a href="/team/timesheet?u=%d&amp;p=%d&amp;w=%s">Last week</a>' % (
                u, r["user_id"], this_w.isoformat(), u, r["user_id"], last_w.isoformat())) if r["books_time"] else "<small>does not write time</small>"))
    b.append('<table border="1" cellpadding="3" cellspacing="0" class="ap"><tr><th>Person</th><th>Employment</th><th>Approved by</th><th>Last week (submitted h)</th><th>This week (h so far)</th>'
             '<th>Contracted h</th><th></th></tr>%s</table>' % "".join(trs))
    return layout(con, u, "Team", "".join(b))


def view_person_timesheet(con, u, q):
    """One person's timesheet for a week, read-only: what they have submitted or had approved (drafts are theirs alone)."""
    try:
        person = int(q.get("p", [""])[0])
    except ValueError:
        person = None
    if person is None or not can_view(con, u, person):
        return no_access(con, u, "Team")
    who = con.execute("select p.name, p.nmc_id, p.hours_per_week, r.name as title from app_user p join role r on r.role_id=p.role_id where p.user_id=?", (person,)).fetchone()
    if not who:
        return no_access(con, u, "Team")
    try:
        w = monday(datetime.date.fromisoformat(q["w"][0]))
    except (KeyError, ValueError, IndexError):
        w = monday(datetime.date.today())
    days = [w + datetime.timedelta(days=i) for i in range(7)]
    d0, d6 = days[0].isoformat(), days[6].isoformat()
    own = person == u
    entries = con.execute(
        "select t.work_date, t.hours, t.status, t.phase_id, t.worked_role_id, t.location_id, (s.scope_code || '-' || coalesce(ph.ctr_code, upper(ph.name))) as phase, "
        "s.scope_code, s.description, c.name as client, pr.name as project, ro.skill_code, lo.name as location_name "
        "from time_entry t join phase ph on ph.phase_id=t.phase_id join scope s on s.scope_id=ph.scope_id join project pr on pr.project_id=s.project_id "
        "join client c on c.client_id=s.client_id join role ro on ro.role_id=t.worked_role_id join location lo on lo.location_id=t.location_id "
        "where t.user_id=? and t.work_date between ? and ?" + ("" if own else " and t.status<>'Draft'") +
        " order by c.name, pr.name, s.scope_code, ph.name, ro.skill_code, t.location_id", (person, d0, d6)).fetchall()
    rows = {}
    for e in entries:
        rows.setdefault((e["phase_id"], e["worked_role_id"], e["location_id"]), {"e": e, "cells": {}, "status": set()})["cells"][e["work_date"]] = e["hours"]
        rows[(e["phase_id"], e["worked_role_id"], e["location_id"])]["status"].add(e["status"])
    prev_w, next_w = (w - datetime.timedelta(days=7)).isoformat(), (w + datetime.timedelta(days=7)).isoformat()
    link = lambda wk, label: '<a href="/team/timesheet?u=%d&amp;p=%d&amp;w=%s">%s</a>' % (u, person, wk, label)
    b = ['<p><a href="/team?u=%d">&lsaquo; Back to the team</a></p>' % u,
         '<h4>%s <span class="sub">%s, %s</span></h4>' % (esc(who["name"]), esc(who["title"]), esc(who["nmc_id"])),
         '<div class="apnav">%s <b>Week commencing %s</b> %s %s<form method="get" action="/team/timesheet">%s%s<span>Go to the week containing</span> %s <input type="submit" value="Show"></form></div>' % (
             link(prev_w, "&lsaquo; Previous week"), w.strftime("%d/%m/%Y"), link(next_w, "Next week &rsaquo;"), link(monday(datetime.date.today()).isoformat(), "This week"),
             hidden(u=u), '<input type="hidden" name="p" value="%d">' % person, date_input("w", d0))]
    if not rows:
        b.append("<p>Nothing submitted for this week.%s</p>" % ("" if own else " (Draft hours are private to the person until they submit.)"))
    else:
        day_tot = {d.isoformat(): 0.0 for d in days}
        trs = []
        for r in rows.values():
            e = r["e"]
            st = "Part approved" if len(r["status"]) > 1 else next(iter(r["status"]))
            tot = sum(r["cells"].values())
            for iso, h in r["cells"].items():
                day_tot[iso] += h
            trs.append('<tr><td><b>%s</b><span class="sub">%s &rsaquo; %s &middot; %s &middot; %s</span></td>%s<td align="right"><b>%s</b></td><td>%s</td></tr>' % (
                esc(e["phase"]), esc(e["client"]), esc(e["project"]), esc(e["skill_code"]), esc(e["location_name"]),
                "".join('<td class="dy%s" align="center">%s</td>' % ("" if r["cells"].get(d.isoformat()) else " zero", fmt(r["cells"][d.isoformat()]) if d.isoformat() in r["cells"] else "&ndash;") for d in days),
                fmt(tot), esc(st)))
        total = sum(day_tot.values())
        head = "".join('<th class="dy">%s</th>' % d.strftime("%a %d/%m") for d in days)
        foot = '<tr><td align="right"><b>Total</b></td>%s<td align="right"><b>%s</b></td><td></td></tr>' % (
            "".join('<td class="dy" align="center"><b>%s</b></td>' % (fmt(day_tot[d.isoformat()]) if day_tot[d.isoformat()] else "&ndash;") for d in days), fmt(total))
        b.append('<table border="1" cellpadding="3" cellspacing="0" class="ap"><tr><th>Booking</th>%s<th>Total</th><th>Status</th></tr>%s%s</table>' % (head, "".join(trs), foot))
        over = total - float(who["hours_per_week"])
        b.append('<p class="apsum">%s h this week against %s contracted%s.</p>' % (
            fmt(total), fmt(who["hours_per_week"]), (", %s h over" % fmt(over)) if over > 0 else ""))
        if not own:
            b.append("<p><small>Only submitted and approved hours are shown: drafts stay private to the person until they submit.</small></p>")
    # the last eight weeks at a glance, so an earlier week is one click away
    glance = con.execute("select week_start, " + ("total_hours" if own else "submitted_hours") + " as h from v_weekly_hours where user_id=? and week_start<=? order by week_start desc limit 8",
                         (person, d6)).fetchall()
    if glance:
        b.append("<h4>Recent weeks</h4><p>%s</p>" % " &nbsp; ".join(
            "%s (%s h)" % (link(g["week_start"], dmy(g["week_start"], True)), fmt(g["h"])) for g in glance if g["h"]))
    return layout(con, u, "Team", "".join(b))
