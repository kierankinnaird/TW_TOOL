"""Demo history: twelve weeks of timesheets before the seed's own two weeks, so reports, month end and the AAA export have months to show.

    python scripts/build.py --history

Nothing here is random in the sense of changing between builds: the same database comes out every time. Everyone books the way the seed
already shows them (the same scopes, CTRs, roles and rates); full-time people most days, others a few days a week, with a week or two of
leave, the Scottish summer bank holiday off, a little internal time (training, business services) and two people on a two-weeks-on,
two-weeks-off offshore rotation, which is what earns the offshore nights. Every week is entered, approved by the scope's approver and closed,
with a handful of after-approval corrections so the audit trail has something in it.
"""
import datetime
import random

FIRST_MONDAY = "2026-08-03"
WEEKS = 12
BANK_HOLIDAYS = {"2026-08-03"}                   # Scottish summer bank holiday
FULL_TIME = {1, 3, 5, 9, 10, 11, 13, 17, 18, 20, 21, 26}
OFFSHORE_ROTA = {11: 0, 18: 2, 24: 1}            # user -> week offset in a four-week cycle: two weeks offshore, two weeks onshore
INTERNAL_PHASES = {7: 8, 8: 9, 9: 10}            # internal scope -> its phase
INTERNAL_APPROVER = 31


def derive_combos(con, combos, full_time, rnd):
    """Booking patterns for people the seed has no hours for (the company's people loaded from the tenant export): a billable role books to the
    open client scopes the client has a rate for (one or two of them), anything else books internal time. Approvers, cost controllers and the IT
    admin are left out: they do not book here, and nobody approves their own hours."""
    skip = {r[0] for r in con.execute("select user_id from v_scope_approver")} | {r[0] for r in con.execute("select user_id from app_user where permission<>'user'")}
    scopes = con.execute("select s.scope_id, s.client_id, (select min(phase_id) from phase p where p.scope_id=s.scope_id and p.status='Open') "
                         "from scope s join client c on c.client_id=s.client_id join project pr on pr.project_id=s.project_id "
                         "where c.is_internal=0 and s.status='Open' and pr.status='Open' order by s.scope_id").fetchall()
    derived = set()
    for uid, role in con.execute("select user_id, role_id from app_user where permission='user' order by user_id").fetchall():
        if uid in combos or uid in skip:
            continue
        derived.add(uid)
        mine = []
        for scope, client, phase in scopes:
            rate = con.execute("select rate_id from rate where client_id=? and role_id=? order by effective_from desc limit 1", (client, role)).fetchone()
            if rate and phase:
                mine.append((scope, phase, role, rate[0], 1, 0))
        if mine:
            mine = rnd.sample(mine, min(2, len(mine)))
            combos[uid] = [m + (3 if i == 0 else 1,) for i, m in enumerate(mine)]
            if rnd.random() < 0.7:
                full_time.add(uid)
        else:                                                      # no billable work for this role: internal time only
            combos[uid] = [(7, INTERNAL_PHASES[7], role, None, 1, 0, 1)]
    return derived


def add_history(con, first_monday=FIRST_MONDAY, weeks=WEEKS):
    rnd = random.Random(2026)
    day = datetime.date.fromisoformat
    combos = {}
    for r in con.execute("select user_id, scope_id, phase_id, worked_role_id, rate_id, location_id, overtime, count(*) as n from time_entry "
                         "group by 1,2,3,4,5,6,7 order by user_id, n desc, scope_id"):
        combos.setdefault(r[0], []).append(r[1:])
    approver = {r[0]: r[1] for r in con.execute("select scope_id, user_id from v_scope_approver")}
    full_time = set(FULL_TIME)
    derived = derive_combos(con, combos, full_time, rnd)
    own_role = {r[0]: r[1] for r in con.execute("select user_id, role_id from app_user")}
    start = {r[0]: r[1] for r in con.execute("select user_id, start_date from app_user")}
    leave = {}
    for uid in combos:                                    # one or two blocks of leave in the twelve weeks
        for _ in range(rnd.choice((1, 2))):
            first = day(first_monday) + datetime.timedelta(days=7 * rnd.randrange(weeks) + rnd.randrange(5))
            for k in range(rnd.choice((2, 3, 5))):
                leave.setdefault(uid, set()).add((first + datetime.timedelta(days=k)).isoformat())
    edits = []
    for w in range(weeks + 1):
        monday = day(first_monday) + datetime.timedelta(days=7 * w)
        week = monday.isoformat()
        sunday = monday + datetime.timedelta(days=6)
        closed = None
        if w < weeks:
            con.execute("insert into period (week_start) values (?)", (week,))
        else:   # the seed's own first week (already closed): the people it has no hours for are filled in too, so no one is missing from it
            closed = con.execute("select closed_by, closed_at from period where week_start=?", (week,)).fetchone()
            if not derived or closed is None:
                break
            con.execute("update period set status='Open', closed_by=NULL, closed_at=NULL where week_start=?", (week,))
        made = []                                         # (entry_id, user, scope, date)
        for uid, mine in combos.items():
            if closed is not None and uid not in derived:
                continue
            rota = OFFSHORE_ROTA.get(uid)
            offshore = rota is not None and ((w + rota) % 4) < 2
            if offshore:
                days = [monday + datetime.timedelta(days=k) for k in range(7)]
            elif uid in full_time or rota is not None:
                days = [monday + datetime.timedelta(days=k) for k in range(5) if rnd.random() < 0.96]
            else:
                days = [monday + datetime.timedelta(days=k) for k in rnd.sample(range(5), rnd.choice((1, 2, 3)))]
            for d in sorted(days):
                iso = d.isoformat()
                if iso in BANK_HOLIDAYS or iso in leave.get(uid, ()) or iso < start[uid]:
                    continue
                pool = [c for c in mine if (c[4] == 2) == offshore] or mine if rota is not None else mine
                weights = [c[6] for c in pool]
                scope, phase, role, rate, loc, ot, _ = rnd.choices(pool, weights=weights)[0]
                hours = 12 if offshore else rnd.choice((8, 8, 8, 8, 7.5)) if uid in full_time or rota is not None else rnd.choice((4, 6, 8))
                if not offshore and rnd.random() < 0.12:                 # part of the day on internal time
                    iscope = rnd.choice((7, 8, 8))
                    con.execute("insert into time_entry (user_id, scope_id, phase_id, worked_role_id, rate_id, work_date, hours, location_id, overtime, created_by, created_at) "
                                "values (?,?,?,?,NULL,?,?,1,0,?,?)", (uid, iscope, INTERNAL_PHASES[iscope], own_role[uid], iso, 4, uid, "%s 17:00:00" % iso))
                    made.append((con.execute("select last_insert_rowid()").fetchone()[0], uid, iscope, iso))
                    hours = min(hours, 4)
                cur = con.execute("insert into time_entry (user_id, scope_id, phase_id, worked_role_id, rate_id, work_date, hours, location_id, overtime, created_by, created_at) "
                                  "values (?,?,?,?,?,?,?,?,?,?,?)", (uid, scope, phase, role, rate, iso, hours, loc, ot, uid, "%s 17:00:00" % iso))
                made.append((cur.lastrowid, uid, scope, iso))
        approved_day = (sunday + datetime.timedelta(days=2)).isoformat()      # approved on the Tuesday after the week
        for i, (eid, uid, scope, iso) in enumerate(made):
            who = INTERNAL_APPROVER if scope in INTERNAL_PHASES else approver[scope]
            at = "%s %02d:%02d:00" % (approved_day, 9 + i % 3, (i * 7) % 60)
            con.execute("update time_entry set status='Approved', approved_by=?, approved_at=?, last_edited_by=?, last_edited_at=? where entry_id=?", (who, at, who, at, eid))
            if i % 97 == 40 and scope not in INTERNAL_PHASES:                 # now and then a correction after approval
                edits.append((eid, who, approved_day, iso))
        if closed is None:
            con.execute("update period set status='Closed', closed_by=29, closed_at=? where week_start=?", ("%s 12:00:00" % approved_day, week))
        else:
            con.execute("update period set status='Closed', closed_by=?, closed_at=? where week_start=?", (closed[0], closed[1], week))
        for eid, who, approved, iso in edits:
            hrs = con.execute("select hours from time_entry where entry_id=?", (eid,)).fetchone()[0]
            when = "%s 14:00:00" % (day(approved) + datetime.timedelta(days=1)).isoformat()
            con.execute("update time_entry set hours=?, status='Edited', edit_count=edit_count+1, last_change_reason=?, last_edited_by=?, last_edited_at=? where entry_id=?",
                        (hrs - 0.5 if hrs > 4 else hrs + 0.5, "Corrected after a query from the client on %s" % iso[8:] + "/" + iso[5:7], who, when, eid))
            when2 = "%s 15:00:00" % (day(approved) + datetime.timedelta(days=1)).isoformat()
            con.execute("update time_entry set status='Approved', approved_by=?, approved_at=?, last_edited_by=?, last_edited_at=? where entry_id=?", (who, when2, who, when2, eid))
        edits = []
    con.commit()
