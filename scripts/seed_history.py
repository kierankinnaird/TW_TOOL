"""Demo history: months of timesheets for everyone, so reports, month end, approvals and the AAA export have a lot to show.

    python scripts/build.py --history

Nothing here is random in the sense of changing between builds: the same database comes out every time. What it builds:

  * a bigger catalogue of work: more Apache and Spirit projects, scopes and CTRs (with assets and POs), so people can book to many different CTRs;
  * client-specific job titles with clearly different rates (Apache's "Senior Piping Engineer" is Spirit's "Lead Piping Engineer"; Spirit's
    "Senior Project Engineer" is Apache's "Project Engineer"; the document controller is Senior on Apache and Lead on Spirit), and a PAAF for every
    title each person books under, plus some PAAFs held but not yet used and some people covering a second title;
  * twelve weeks of approved timesheets (3 Aug to 25 Oct) for everyone who writes time: full-time people most days, part-timers a few days a week,
    leave, sick days, the Scottish summer bank holiday, internal time, two weeks on / two weeks off offshore rotations, people who book only to internal
    codes, and people who spread each day over several CTRs and projects;
  * the weeks since: 2 Nov filled in for those the seed has nothing for, 9 Nov submitted (some already approved) and 16 Nov part drafts, part submitted,
    so the approvals and missing-timesheets pages have plenty to work on;
  * a handful of corrections after approval, so the audit trail and the manhour report adjustments have something in them.
"""
import datetime
import random

FIRST_MONDAY = "2026-08-03"
WEEKS = 12
BANK_HOLIDAYS = {"2026-08-03"}                   # Scottish summer bank holiday
FULL_TIME = {1, 3, 5, 9, 10, 11, 13, 17, 18, 20, 21, 26}
OFFSHORE_ROTA = {11: 0, 18: 2, 24: 1}            # user -> week offset in a four-week cycle: two weeks on, two weeks off offshore
COST_CONTROLLER = 29
SICK, COMPASSIONATE = "INT04", "INT05"
NO_SIDE_TIME = {"INT04", "INT05", "INT06"}       # internal codes that are not "a little training or admin": absence and leadership

# ---------- more work to book to ----------
CTR_TYPES = [("PM", "Project Management"), ("EN", "Engineering"), ("PI", "Piping Engineering"), ("EL", "Electrical Engineering"), ("ST", "Structural Engineering"),
             ("IN", "Instrument Engineering"), ("PR", "Process Engineering"), ("CN", "Construction Engineering"), ("TS", "Technical Safety"), ("QC", "QA/QC")]
# assets: (client, name, PO number or None)
NEW_ASSETS = [(1, "Charlie Jetty", "4500777001"), (1, "Delta Pipeline", None), (2, "Calder Compression", "4500674196/00002 rev1"), (2, "Morecambe Satellite", "4900042224")]
# projects: (client, code, name) -> scopes: (scope code, description, asset name or None, ctr types by code, payment terms)
NEW_PROJECTS = [
    (1, "APJ-03", "Jetty Decommissioning"), (1, "APJ-04", "Subsea Abandonment"), (2, "SPJ-02", "Rivers Terminal Upgrade"), (2, "SPJ-03", "Calder Compression")]
NEW_SCOPES = [  # project code (or an existing project id) -> scopes
    ("APJ-03", "BB0003", "Jetty Removal Study", "Charlie Jetty", ("PM", "EN", "ST", "CN")),
    ("APJ-03", "BB0004", "Jetty Removal Execution", "Charlie Jetty", ("PM", "ST", "CN", "TS", "QC")),
    ("APJ-03", "BB0005", "Pipeline Flushing", "Delta Pipeline", ("PM", "PI", "PR")),
    ("APJ-04", "BB0006", "Subsea Survey", "Delta Pipeline", ("PM", "EN", "IN")),
    ("APJ-04", "BB0007", "Well Plug and Abandon Support", "Alpha Platform", ("PM", "EN", "TS", "QC")),
    (1, "BB0008", "Topsides Engineering Support", "Alpha Platform", ("PM", "EN", "EL", "IN", "PI", "ST")),
    (2, "FB0002", "Platform Removal Planning", "Bravo Platform", ("PM", "EN", "CN")),
    ("SPJ-02", "A1700", "Terminal Controls Upgrade", "Rivers Terminal", ("PM", "IN", "EL", "PR")),
    ("SPJ-02", "A1800", "Terminal Pipework", "Rivers Terminal", ("PM", "PI", "ST", "CN", "QC")),
    ("SPJ-02", "A1900", "Flare System Study", "Rivers Terminal", ("PM", "PR", "TS")),
    ("SPJ-03", "A2000", "Compressor Package", "Calder Compression", ("PM", "EN", "PI", "EL", "IN", "ST")),
    ("SPJ-03", "A2100", "Power Supply", "Calder Compression", ("PM", "EL")),
    ("SPJ-03", "A2200", "Compression Tie-ins", "Calder Compression", ("PM", "PI", "CN", "QC")),
    (3, "A2300", "Hub Operations Support", "Morecambe Satellite", ("PM", "EN", "TS")),
    (3, "A2400", "Estimating Support", "Morecambe Satellite", ("PM",)),
]


def add_catalogue(con):
    """More projects, scopes and CTRs for both clients (with assets and POs). Returns [(scope id, client id, phase id)] for every open client CTR."""
    assets = {}
    for client, name, po in NEW_ASSETS:
        assets[name] = con.execute("insert into asset (client_id, name, po_number) values (?,?,?) returning asset_id", (client, name, po)).fetchone()[0]
    for aid, name in con.execute("select asset_id, name from asset"):
        assets.setdefault(name, aid)
    projects = {}
    for client, code, name in NEW_PROJECTS:
        projects[code] = (con.execute("insert into project (project_code, name, client_id) values (?,?,?) returning project_id", (code, name, client)).fetchone()[0], client)
    for n, (proj, code, desc, asset, types) in enumerate(NEW_SCOPES):
        if isinstance(proj, int):
            pid, client = proj, con.execute("select client_id from project where project_id=?", (proj,)).fetchone()[0]
        else:
            pid, client = projects[proj]
        scope = con.execute("insert into scope (scope_code, client_id, project_id, asset_id, description, status) values (?,?,?,?,?,'Open') returning scope_id",
                            (code, client, pid, assets[asset], desc)).fetchone()[0]
        for k, t in enumerate(types):
            title = dict(CTR_TYPES)[t]
            terms, value = "Reimbursable", None
            if n % 5 == 2 and k == 0:
                terms, value = "Lump Sum", 25000 + 5000 * n
            elif n % 7 == 3 and k == 1:
                terms = "Non-reimbursable"
            elif n % 4 == 1 and k == 2:
                terms = "Budget"
            con.execute("insert into phase (scope_id, name, description, ctr_code, payment_terms, lump_sum_value) values (?,?,?,?,?,?)",
                        (scope, title, title, "%s-D-001" % t, terms, value))
    con.execute("update phase set discipline_id=(select discipline_id from discipline where code=split_part(phase.ctr_code, '-', 1)) where discipline_id is null")
    return [tuple(r) for r in con.execute(
        "select s.scope_id, s.client_id, ph.phase_id from scope s join client c on c.client_id=s.client_id join phase ph on ph.scope_id=s.scope_id "
        "where c.is_internal=0 and s.status='Open' and ph.status='Open' order by s.scope_id, ph.phase_id")]


# ---------- client-specific job titles ----------
def add_titles(con):
    """Give each client its own title and rate for the same job, so the difference is plain. For a "Lead X" job Spirit keeps "Lead X" and Apache gets "Senior X"
    at 15% less; for the other jobs Apache keeps the title and Spirit gets "Senior X" at 25% more (and the other way round for a "Senior X" job). A new title is
    priced for one client only, so a person holding Apache's title cannot book it to Spirit. Returns {(client id, base role id): (role id, rate id)}."""
    rate_of = {(r[0], r[1]): (r[2], r[3]) for r in con.execute("select client_id, role_id, sales_rate, rate_id from rate order by effective_from")}
    by_name = {r[1]: r[0] for r in con.execute("select role_id, name from role")}
    nxt = {}
    for code, in con.execute("select skill_code from role"):
        prefix, _, num = code.partition("-")
        if num.isdigit():
            nxt[prefix] = max(nxt.get(prefix, 0), int(num))
    variants = {}

    def title_for(base, client, name, factor):
        """The role (existing or new) to use for this client, with a rate for the client."""
        if name == base[1]:
            return base[0], rate_of[(client, base[0])][1]
        role = by_name.get(name)
        if role is None:
            prefix = base[2].partition("-")[0]
            nxt[prefix] = nxt.get(prefix, 0) + 1
            role = con.execute("insert into role (skill_code, name, family) values (?,?,?) returning role_id", ("%s-%02d" % (prefix, nxt[prefix]), name, base[3])).fetchone()[0]
            by_name[name] = role
        amount = round(rate_of[(client, base[0])][0] * factor, 2)
        if (client, role) not in rate_of:
            rid = con.execute("insert into rate (client_id, role_id, sales_rate, effective_from) values (?,?,?, '2026-01-01') returning rate_id", (client, role, amount)).fetchone()[0]
            rate_of[(client, role)] = (amount, rid)
        elif rate_of[(client, role)][0] != amount:             # the title already had a (placeholder) rate: this client's own rate takes over from June (rates are never edited)
            rid = con.execute("insert into rate (client_id, role_id, sales_rate, effective_from) values (?,?,?, '2026-06-01') returning rate_id", (client, role, amount)).fetchone()[0]
            rate_of[(client, role)] = (amount, rid)
        return role, rate_of[(client, role)][1]
    for role, name, code, family in con.execute("select role_id, name, skill_code, family from role where role_id <= 29 and role_id <> 10 order by role_id").fetchall():
        if (1, role) not in rate_of or (2, role) not in rate_of:
            continue
        base = (role, name, code, family)
        if name.startswith("Lead "):
            apache, spirit = ("Senior " + name[5:], 0.85), (name, 1.0)
        elif name.startswith("Senior "):
            apache, spirit = (name[7:], 0.85), (name, 1.0)
        else:
            apache, spirit = (name, 1.0), ("Senior " + name, 1.25)
        variants[(1, role)] = title_for(base, 1, apache[0], apache[1])
        variants[(2, role)] = title_for(base, 2, spirit[0], spirit[1])
    # the document controller: Senior on Apache, Lead on Spirit (both titles are in the seed, with their own rate for one client each)
    variants[(1, 10)] = (35, rate_of[(1, 35)][1])
    variants[(2, 10)] = (34, rate_of[(2, 34)][1])
    return variants


def ensure_paaf(con, uid, scope, role, seen):
    """Hold a PAAF for the project of this client scope and this role, so the hours can be booked (internal codes need none)."""
    key = (uid, scope, role)
    if key not in seen:
        seen.add(key)
        con.execute("insert into paaf (project_id, role_id, user_id, start_date) select s.project_id, ?, ?, '2026-01-01' from scope s join client c on c.client_id=s.client_id "
                    "where s.scope_id=? and c.is_internal=0 on conflict do nothing", (role, uid, scope))


def split_hours(h, k, rnd):
    """A day's hours split over k rows in half hours, every part more than zero."""
    if k == 2:
        a = max(1.0, round(h * rnd.choice((0.25, 0.5, 0.5, 0.75)) * 2) / 2)
        return [a, h - a] if h - a > 0 else [h]
    a = max(1.0, round(h * 0.5 * 2) / 2)
    b = max(0.5, round((h - a) * 0.5 * 2) / 2)
    return [a, b, h - a - b] if h - a - b > 0 else split_hours(h, 2, rnd)


def add_history(con, first_monday=FIRST_MONDAY, weeks=WEEKS):
    rnd = random.Random(2026)
    day = datetime.date.fromisoformat
    paafs = set()
    catalogue = add_catalogue(con)
    variants = add_titles(con)
    scope_client = {r[0]: r[1] for r in con.execute("select scope_id, client_id from scope")}
    scope_code = {r[0]: r[1] for r in con.execute("select scope_id, scope_code from scope")}
    internal = {r[0]: r[1] for r in con.execute("select s.scope_id, min(p.phase_id) from scope s join client c on c.client_id=s.client_id join phase p on p.scope_id=s.scope_id "
                                               "where c.is_internal=1 group by s.scope_id")}
    side_scopes = [s for s in sorted(internal) if scope_code[s] not in NO_SIDE_TIME]
    sick_scope = next(s for s in internal if scope_code[s] == SICK)
    comp_scope = next(s for s in internal if scope_code[s] == COMPASSIONATE)
    rate_for = {(r[0], r[1]): r[2] for r in con.execute("select client_id, role_id, rate_id from rate order by effective_from")}
    controllers = [r[0] for r in con.execute("select user_id from app_user where permission='controller' order by user_id")]
    own_role = {r[0]: r[1] for r in con.execute("select user_id, role_id from app_user")}
    start = {r[0]: r[1] for r in con.execute("select user_id, start_date from app_user")}
    approver = {r[0]: r[1] for r in con.execute("select user_id, approver_id from v_person_approver")}      # each person's hours are approved by their approver (line manager)

    def approver_of(uid):
        a = approver.get(uid)
        if a and a != uid:
            return a
        return next(c for c in controllers if c != uid)                      # someone with nobody to approve for them: a cost controller (never themselves)

    def variant(scope, role, rate):
        v = variants.get((scope_client[scope], role)) if rate is not None else None
        return v if v else (role, rate)

    def client_options(role):
        """Every client CTR this role can book to, as combos (scope, phase, role, rate, location, overtime, weight), with the client's own title and rate."""
        out = []
        for scope, client, phase in catalogue:
            v = variants.get((client, role)) or ((role, rate_for[(client, role)]) if (client, role) in rate_for else None)
            if v:
                out.append((scope, phase, v[0], v[1], 1, 0, 0))
        return out

    # who books to what: the seed's own people as the seed shows them (with each client's title), then more CTRs for many of them
    combos = {}
    for r in con.execute("select user_id, scope_id, phase_id, worked_role_id, rate_id, location_id, overtime, count(*) as n from time_entry "
                         "group by 1,2,3,4,5,6,7 order by user_id, n desc, scope_id"):
        uid, (scope, phase, role, rate, loc, ot, n) = r[0], r[1:]
        v = variant(scope, role, rate)
        combos.setdefault(uid, []).append((scope, phase, v[0], v[1], loc, ot, n))
    for uid in list(combos):
        if rnd.random() < 0.6:
            clients = {scope_client[c[0]] for c in combos[uid] if scope_client[c[0]] != 3}
            pool = [o for o in client_options(own_role[uid]) if scope_client[o[0]] in clients and all(o[1] != c[1] for c in combos[uid])]
            for o in rnd.sample(pool, min(len(pool), rnd.choice((1, 2, 3, 4)))):
                combos[uid].append(o[:6] + (rnd.choice((1, 2, 3)),))
    full_time = set(FULL_TIME)
    internal_only, multi = set(), set()
    for uid, role in con.execute("select user_id, role_id from app_user where books_time=1 order by user_id").fetchall():
        if uid in combos:
            continue
        options = client_options(role)
        if options and rnd.random() > 0.12:                                      # most people with a priced job book to client CTRs, one to eight of them
            k = rnd.choice((1, 2, 2, 3, 3, 4, 5, 7, 8))
            mine = rnd.sample(options, min(k, len(options)))
            combos[uid] = [m[:6] + (rnd.choice((1, 2, 3, 6)) if i else 6,) for i, m in enumerate(mine)]
            if k >= 5:
                multi.add(uid)
        else:                                                                     # an unpriced job (a director, say), or one who simply books internal: internal codes only
            codes = rnd.sample(side_scopes, rnd.choice((2, 3, 4, 6)))
            combos[uid] = [(s, internal[s], role, None, 1, 0, rnd.choice((1, 2, 5, 8))) for s in codes]
            internal_only.add(uid)
        if rnd.random() < 0.7:
            full_time.add(uid)
    for uid in combos:                                                            # people the seed has: some are heavy multi-CTR bookers too
        if len({c[1] for c in combos[uid]}) >= 5:
            multi.add(uid)
    split_p = {uid: 0.7 if uid in multi else (0.3 if uid in internal_only else 0.2) for uid in combos}
    compassion = set(rnd.sample(sorted(combos), 3))
    leave = {}
    for uid in combos:                                                            # one or two blocks of leave
        for _ in range(rnd.choice((1, 2))):
            first = day(first_monday) + datetime.timedelta(days=7 * rnd.randrange(weeks) + rnd.randrange(5))
            for k in range(rnd.choice((2, 3, 5))):
                leave.setdefault(uid, set()).add((first + datetime.timedelta(days=k)).isoformat())

    def book_week(monday, w, uids, status_of, only_missing=False):
        """Everyone's hours for one week; returns [(entry id, user, scope, date, status)]."""
        have = set()
        if only_missing:
            have = {(r[0], r[1]) for r in con.execute("select user_id, work_date from time_entry where work_date between ? and ?",
                                                      (monday.isoformat(), (monday + datetime.timedelta(days=6)).isoformat()))}
        made = []

        def put(uid, scope, phase, role, rate, loc, ot, iso, hrs, status):
            if scope not in internal:
                ensure_paaf(con, uid, scope, role, paafs)
            eid = con.execute("insert into time_entry (user_id, scope_id, phase_id, worked_role_id, rate_id, work_date, hours, location_id, overtime, status, created_by, created_at) "
                              "values (?,?,?,?,?,?,?,?,?,?,?,?) returning entry_id", (uid, scope, phase, role, rate, iso, hrs, loc, ot, status, uid, "%s 17:00:00" % iso)).fetchone()[0]
            made.append((eid, uid, scope, iso, status))
        for uid in uids:
            mine = combos[uid]
            rota = OFFSHORE_ROTA.get(uid)
            offshore = rota is not None and ((w + rota) % 4) < 2
            if offshore:
                days = [monday + datetime.timedelta(days=k) for k in range(7)]
            elif uid in full_time or rota is not None:
                days = [monday + datetime.timedelta(days=k) for k in range(5) if rnd.random() < 0.96]
            else:
                days = [monday + datetime.timedelta(days=k) for k in rnd.sample(range(5), rnd.choice((1, 2, 3)))]
            status = status_of(uid)
            for d in sorted(days):
                iso = d.isoformat()
                if iso in BANK_HOLIDAYS or iso < start[uid] or (uid, iso) in have:
                    continue
                if iso in leave.get(uid, ()):
                    if rnd.random() < 0.3:                                        # some of the leave days are sick days
                        put(uid, sick_scope, internal[sick_scope], own_role[uid], None, 1, 0, iso, 8, status)
                    continue
                if uid in compassion and iso == (day(first_monday) + datetime.timedelta(days=7 * 5 + 2)).isoformat():
                    put(uid, comp_scope, internal[comp_scope], own_role[uid], None, 1, 0, iso, 8, status)
                    continue
                pool = [c for c in mine if (c[4] == 2) == offshore] or mine if rota is not None else mine
                hours = 12 if offshore else rnd.choice((8, 8, 8, 8, 7.5)) if uid in full_time or rota is not None else rnd.choice((4, 6, 8))
                if not offshore and uid not in internal_only and rnd.random() < 0.12:    # part of the day on internal time
                    iscope = rnd.choice(side_scopes)
                    put(uid, iscope, internal[iscope], own_role[uid], None, 1, 0, iso, 4, status)
                    hours = min(hours, 4)
                first = rnd.choices(pool, weights=[c[6] for c in pool])[0]
                chosen = [first]
                if not offshore and len(pool) > 1 and rnd.random() < split_p[uid]:   # the day is spread over two or three CTRs
                    others = [c for c in pool if c[1] != first[1]]
                    chosen += rnd.sample(others, min(len(others), rnd.choice((1, 1, 2))))
                parts = split_hours(hours, len(chosen), rnd) if len(chosen) > 1 else [hours]
                for c, hrs in zip(chosen, parts):
                    put(uid, c[0], c[1], c[2], c[3], c[4], c[5], iso, hrs, status)
        return made

    def approve(made, approved_day, only=None):
        edits = []
        for i, (eid, uid, scope, iso, status) in enumerate(made):
            if status != "Submitted" or (only is not None and uid not in only):
                continue
            who = approver_of(uid)
            at = "%s %02d:%02d:00" % (approved_day, 9 + i % 3, (i * 7) % 60)
            con.execute("update time_entry set status='Approved', approved_by=?, approved_at=?, last_edited_by=?, last_edited_at=? where entry_id=?", (who, at, who, at, eid))
            if i % 97 == 40 and scope not in internal:                           # now and then a correction after approval
                edits.append((eid, who, approved_day, iso))
        for eid, who, approved, iso in edits:
            hrs = con.execute("select hours from time_entry where entry_id=?", (eid,)).fetchone()[0]
            when = "%s 14:00:00" % (day(approved) + datetime.timedelta(days=1)).isoformat()
            con.execute("update time_entry set hours=?, status='Edited', edit_count=edit_count+1, last_change_reason=?, last_edited_by=?, last_edited_at=? where entry_id=?",
                        (hrs - 0.5 if hrs > 1 else hrs + 0.5, "Corrected after a query from the client on %s" % iso[8:] + "/" + iso[5:7], who, when, eid))
            when2 = "%s 15:00:00" % (day(approved) + datetime.timedelta(days=1)).isoformat()
            con.execute("update time_entry set status='Approved', approved_by=?, approved_at=?, last_edited_by=?, last_edited_at=? where entry_id=?", (who, when2, who, when2, eid))

    everyone = sorted(combos)
    derived = [u for u in everyone if con.execute("select 1 from time_entry where user_id=? limit 1", (u,)).fetchone() is None]   # the people the seed has no hours for
    for w in range(weeks + 1):
        monday = day(first_monday) + datetime.timedelta(days=7 * w)
        week = monday.isoformat()
        sunday = monday + datetime.timedelta(days=6)
        closed = None
        if w < weeks:
            con.execute("insert into period (week_start) values (?)", (week,))
            made = book_week(monday, w, everyone, lambda u: "Submitted")
        else:   # the seed's own first week (already closed): the people it has no hours for are filled in too, so no one is missing from it
            closed = con.execute("select closed_by, closed_at from period where week_start=?", (week,)).fetchone()
            if not derived or closed is None:
                break
            con.execute("update period set status='Open', closed_by=NULL, closed_at=NULL where week_start=?", (week,))
            made = book_week(monday, w, derived, lambda u: "Submitted")
        approved_day = (sunday + datetime.timedelta(days=2)).isoformat()      # approved on the Tuesday after the week
        approve(made, approved_day)
        if closed is None:
            con.execute("update period set status='Closed', closed_by=?, closed_at=? where week_start=?", (COST_CONTROLLER, "%s 12:00:00" % approved_day, week))
        else:
            con.execute("update period set status='Closed', closed_by=?, closed_at=? where week_start=?", (closed[0], closed[1], week))
    # the weeks since the seed's own two: everyone filled in, in every state
    w0 = (day("2026-11-02") - day(first_monday)).days // 7
    for n, (mon, kind) in enumerate((("2026-11-02", "fill"), ("2026-11-09", "submitted"), ("2026-11-16", "mixed"))):
        con.execute("insert into period (week_start) values (?) on conflict do nothing", (mon,))
        drafts = set(rnd.sample(everyone, len(everyone) * 45 // 100)) if kind == "mixed" else set()
        made = book_week(day(mon), w0 + n, everyone, (lambda u, d=drafts: "Draft" if u in d else "Submitted"), only_missing=(kind == "fill"))
        if kind in ("fill", "submitted"):                                       # some of it already approved, the rest waiting
            approve(made, "2026-11-%02d" % (5 + 7 * n), only=set(rnd.sample(everyone, len(everyone) * (60 if kind == "fill" else 35) // 100)))
    # PAAFs: a title that has a client-specific one replaces the shared title once the person holds the client's; then a fuller register
    for (client, base), (role, _) in variants.items():
        if role != base:
            con.execute("delete from paaf p using project pr where p.project_id=pr.project_id and pr.client_id=? and p.role_id=? and exists "
                        "(select 1 from paaf q where q.user_id=p.user_id and q.project_id=p.project_id and q.role_id=?)", (client, base, role))
    con.execute("delete from paaf where user_id=23 and role_id=10 and exists (select 1 from paaf where user_id=23 and role_id=35)")   # the seed's document controller bills under each client's title now
    # the cost controllers and the IT admin can try booking too: a PAAF on every client project for the client's title for their job, where there is one
    for uid, role in con.execute("select user_id, role_id from app_user where permission in ('controller', 'admin') and books_time=1").fetchall():
        for project, client in con.execute("select project_id, client_id from project where client_id in (select client_id from client where is_internal=0)").fetchall():
            v = variants.get((client, role)) or ((role, None) if (client, role) in rate_for else None)
            if v:
                con.execute("insert into paaf (project_id, role_id, user_id, start_date) values (?,?,?, '2026-01-01') on conflict do nothing", (project, v[0], uid))
    # PAAFs held for projects not booked to yet, and some people covering a second title on a project
    held = con.execute("select p.user_id, p.project_id, p.role_id, pr.client_id from paaf p join project pr on pr.project_id=p.project_id order by p.paaf_id").fetchall()
    all_projects = [tuple(r) for r in con.execute("select project_id, client_id from project where client_id in (select client_id from client where is_internal=0)")]
    for uid, project, role, client in held:
        if rnd.random() < 0.3:
            others = [p for p, c in all_projects if c == client and p != project]
            if others:
                con.execute("insert into paaf (project_id, role_id, user_id, start_date) values (?,?,?, '2026-01-01') on conflict do nothing", (rnd.choice(others), role, uid))
        if rnd.random() < 0.12:
            cover = con.execute("select r.role_id from role r join rate x on x.role_id=r.role_id and x.client_id=? where r.family=(select family from role where role_id=?) and r.role_id<>? "
                                "order by r.role_id", (client, role, role)).fetchall()
            if cover:
                con.execute("insert into paaf (project_id, role_id, user_id, start_date, reference) values (?,?,?, '2026-01-01', ?) on conflict do nothing",
                            (project, rnd.choice(cover)[0], uid, "Cover"))
    for n, (uid, project, role, client) in enumerate(held[:40]):
        if n % 10 == 0:
            con.execute("update paaf set reference=? where user_id=? and project_id=? and role_id=?", ("PAAF-%04d" % (n + 1), uid, project, role))
    # two PAAFs to show the dates: one that has expired (it ended before the seed's own weeks) and one that has not started yet
    con.execute("insert into paaf (project_id, role_id, user_id, reference, start_date, end_date) values (3, 4, 4, 'PAAF-SPJ-014', '2026-01-01', '2026-09-30') on conflict do nothing")
    con.execute("insert into paaf (project_id, role_id, user_id, reference, start_date, end_date) values (3, 3, 4, 'PAAF-SPJ-015', '2027-01-04', '2027-12-31') on conflict do nothing")
    # budgets for every client CTR (P6 holds hours only), so the cost reports have something to compare: most CTRs have room left, a few are close or over
    for phase, actual in con.execute("select ph.phase_id, coalesce((select sum(t.hours) from time_entry t where t.phase_id=ph.phase_id and t.status<>'Draft'), 0) from phase ph "
                                     "join scope s on s.scope_id=ph.scope_id join client c on c.client_id=s.client_id where c.is_internal=0 and "
                                     "not exists (select 1 from ctr_budget b where b.phase_id=ph.phase_id) order by ph.phase_id").fetchall():
        hours = round(actual * rnd.choice((0.9, 1.05, 1.2, 1.3, 1.6, 2.0, 2.5)) / 10) * 10 if actual else rnd.choice((120, 250, 400, 800))
        con.execute("insert into ctr_budget (phase_id, hours, as_of, loaded_by) values (?,?, '2026-11-20', ?)", (phase, max(hours, 10), COST_CONTROLLER))
    # the seed's own budgets were set when there was little booked: they were revised on the same day
    con.execute("insert into ctr_budget (phase_id, hours, as_of, loaded_by) select b.phase_id, "
                "round((select sum(t.hours) from time_entry t where t.phase_id=b.phase_id and t.status<>'Draft') * 1.25 / 10) * 10, '2026-11-20', ? "
                "from ctr_budget b where b.as_of='2026-11-06' and exists (select 1 from time_entry t where t.phase_id=b.phase_id and t.status<>'Draft')", (COST_CONTROLLER,))
    con.commit()
    add_role_budgets(con)
    add_late_changes(con)
    add_november_adjustments(con)


def add_role_budgets(con):
    """Split the latest budget of every CTR that has hours booked by role, in proportion to the hours each role has booked (to the nearest half hour,
    adding up to the budget exactly), so the budget-by-role screens and the role-valued cost report have something to show."""
    done = 0
    for bid, phase, hours in con.execute(
            "select b.budget_id, b.phase_id, b.hours from ctr_budget b where b.as_of = (select max(x.as_of) from ctr_budget x where x.phase_id=b.phase_id) "
            "and not exists (select 1 from ctr_budget_role r where r.budget_id=b.budget_id) "
            "and exists (select 1 from time_entry t where t.phase_id=b.phase_id and t.status<>'Draft')").fetchall():
        booked = {r[0]: float(r[1]) for r in con.execute(
            "select t.worked_role_id, sum(t.hours) from time_entry t where t.phase_id=? and t.status<>'Draft' group by t.worked_role_id", (phase,))}
        total, units = sum(booked.values()), int(round(float(hours) * 2))
        share = {k: v / total * units for k, v in booked.items()}
        give = {k: int(v) for k, v in share.items()}
        for k in sorted(share, key=lambda k: share[k] - give[k], reverse=True)[:units - sum(give.values())]:
            give[k] += 1
        for role, halves in give.items():
            if halves > 0:
                con.execute("insert into ctr_budget_role (budget_id, role_id, hours) values (?,?,?)", (bid, role, halves / 2.0))
        done += 1
    con.commit()
    return done


def add_late_changes(con):
    """Corrections made after a four-week report period (28 Sep to 25 Oct) had ended, to work from inside it, so the manhour report for the next period has
    adjustments to show: some hours taken off, some added, one bundle sent back to draft by a cost controller. Also a correction made inside the period
    (which is not an adjustment: the report simply shows the corrected figure)."""
    rows = con.execute(
        "select t.entry_id, t.user_id, t.hours, t.work_date, t.scope_id from time_entry t join scope s on s.scope_id=t.scope_id "
        "where s.client_id in (1, 2) and t.status='Approved' and t.work_date between '2026-09-28' and '2026-10-23' and t.overtime=0 "
        "order by t.entry_id").fetchall()
    approver = {r[0]: r[1] for r in con.execute("select user_id, approver_id from v_person_approver")}
    by_client = {}
    for r in rows:
        client = con.execute("select client_id from scope where scope_id=?", (r[4],)).fetchone()[0]
        by_client.setdefault(client, []).append(r)
    plan = []   # (entry, change in hours, when, reason)
    for client, entries in by_client.items():
        picks = entries[3::151][:30]
        for n, e in enumerate(picks):
            if n % 6 == 5:
                plan.append((e, None, "2026-10-28 16:00:00", "Booked to the wrong job"))              # sent back to draft
            elif n % 2 == 0:
                plan.append((e, -1.0, "2026-10-29 10:%02d:00" % (n * 7), "Site log shows one hour less (client query)"))
            else:
                plan.append((e, 0.5, "2026-10-30 11:%02d:00" % (n * 7), "Half an hour missed off the timesheet"))
        if len(entries) > 5:
            e = entries[1]
            plan.append((e, -0.5, "2026-10-14 13:00:00", "Corrected before the period closed"))        # inside the period: no adjustment
    for (eid, uid, hours, work_date, scope), change, when, reason in plan:
        who = approver.get(uid) or COST_CONTROLLER
        if change is None:
            con.execute("update time_entry set status='Draft', reopen_reason=?, last_edited_by=?, last_edited_at=? where entry_id=?", (reason, COST_CONTROLLER, when, eid))
            continue
        con.execute("update time_entry set hours=?, status='Edited', edit_count=edit_count+1, last_change_reason=?, last_edited_by=?, last_edited_at=? where entry_id=?",
                    (hours + change, reason, who, when, eid))
        again = when[:-2] + "59"                                                                       # re-approved a little later
        con.execute("update time_entry set status='Approved', approved_by=?, approved_at=?, last_edited_by=?, last_edited_at=? where entry_id=?", (who, again, who, again, eid))
    con.commit()
    return len(plan)


def add_november_adjustments(con):
    """A second wave of corrections, made in November to work from earlier periods (September to 25 October, never the seeded weeks), so a report for the November period
    shows adjustments to work one, two and three periods back, for both clients, with hours added and taken off, and a person who is corrected twice. Returns the
    number of corrections. Safe to run on a database that already has the first wave: it only touches approved entries nobody has edited."""
    rows = con.execute(
        "select t.entry_id, t.user_id, t.hours, s.client_id from time_entry t join scope s on s.scope_id=t.scope_id "
        "where s.client_id in (1, 2) and t.status='Approved' and t.edit_count=0 and t.overtime=0 and t.hours>=2 and t.work_date between '2026-09-07' and '2026-10-25' "
        "order by t.entry_id").fetchall()
    approver = {r[0]: r[1] for r in con.execute("select user_id, approver_id from v_person_approver")}
    by_client = {}
    for r in rows:
        by_client.setdefault(r[3], []).append(r)
    plan = []
    for client, entries in by_client.items():
        picks = entries[7::37][:36]
        for n, e in enumerate(picks):
            day = 4 + (n % 14)                                                                          # corrections made 4 to 17 November
            when = "2026-11-%02d %02d:%02d:00" % (day, 9 + n % 8, (n * 11) % 60)
            if n % 3 == 0:
                plan.append((e, -1.0, when, "Site log shows one hour less (client query)"))
            elif n % 3 == 1:
                plan.append((e, 1.5, when, "Time missed off the timesheet, confirmed by the supervisor"))
            else:
                plan.append((e, -0.5, when, "Break not taken off"))
        if len(picks) > 3:
            e = picks[3]                                                                                # the same entry corrected a second time, later in the month
            plan.append((e, 0.5, "2026-11-19 15:30:00", "Corrected again after the client's review"))
    for (eid, uid, hours, client), change, when, reason in plan:
        who = approver.get(uid) or COST_CONTROLLER
        now = con.execute("select hours from time_entry where entry_id=?", (eid,)).fetchone()[0]
        con.execute("update time_entry set hours=?, status='Edited', edit_count=edit_count+1, last_change_reason=?, last_edited_by=?, last_edited_at=? where entry_id=?",
                    (now + change, reason, who, when, eid))
        again = when[:-2] + "59"
        con.execute("update time_entry set status='Approved', approved_by=?, approved_at=?, last_edited_by=?, last_edited_at=? where entry_id=?", (who, again, who, again, eid))
    con.commit()
    return len(plan)
