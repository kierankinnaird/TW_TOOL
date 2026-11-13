"""Admin tabs (periods, users, roles, clients, rates, PAAFs, scopes, internal codes, locations, allowance rates, budgets) and the audit trail."""
import csv
import datetime
import re
import sqlite3
from urllib.parse import quote
from nmc.core import (
    date_input, edit_link, esc, fmt,
    fmt_time, hidden, layout, msgs_html,
    need_date, need_text, no_access, opts_approvers,
    opts_clients, opts_projects, opts_roles, opts_scopes,
    opts_users, permission_of, rate_options, sel,
    stamp, table, txt)


# ---------- Admin ----------
ADMIN_TABS = [("periods", "Weekly periods"), ("users", "Users"), ("roles", "Job families / titles"),
              ("clients", "Clients"), ("rates", "Client rate cards"), ("paafs", "PAAFs"), ("scopes", "Projects / scopes / CTRs"),
              ("internal", "Internal codes"), ("locations", "Locations (hour types)"), ("allowance_rates", "Allowance rates"),
              ("budgets", "Budgets (from P6)")]


# Who may use each Admin tab. IT manages users and job roles; cost controllers manage clients, rate cards, PAAFs, scopes and internal codes.
# Weekly periods is open to both controllers and admins until the cost team confirms who owns it (the audit trail is handled the same way).
TAB_ACCESS = {"periods": ("controller", "admin"), "users": ("admin",), "roles": ("admin",), "clients": ("controller",),
              "rates": ("controller",), "paafs": ("controller",), "scopes": ("controller",), "internal": ("controller",),
              "locations": ("admin",), "allowance_rates": ("controller",), "budgets": ("controller",)}


PERMISSION_OPTIONS = [("user", "user"), ("controller", "controller (cost controller)"), ("admin", "admin (IT)")]


def tab_of(t):
    return {"projects": "scopes", "phases": "scopes"}.get(t, t)  # projects and phases are edited from the scopes tab


def can_use_tab(con, u, t):
    return permission_of(con, u) in TAB_ACCESS.get(tab_of(t), ())


def allowed_tabs(con, u):
    return [(k, v) for k, v in ADMIN_TABS if can_use_tab(con, u, k)]


PAAF_STATES = [("current", "On the list"), ("ended", "Ended")]


PAAF_SORTS = {"id": ("p.paaf_id", "p.paaf_id desc"),
              "name": ("u.name, p.paaf_id", "u.name desc, p.paaf_id desc"),
              "client": ("c.name, u.name, p.paaf_id", "c.name desc, u.name desc, p.paaf_id desc"),
              "expiry": ("p.expiry_date is null, p.expiry_date, p.paaf_id", "p.expiry_date is not null, p.expiry_date desc, p.paaf_id desc")}


def paaf_filters(q):
    """The PAAF list's filters and order, from a query or a posted form (values are lists): person, client, status, sort (a leading - is descending)."""
    def one(k):
        return (q.get(k) or [""])[0].strip()

    def num(k):
        try:
            return int(one(k))
        except ValueError:
            return None
    sort = one("f_sort") or "id"
    desc = sort.startswith("-")
    if sort.lstrip("-") not in PAAF_SORTS:
        sort, desc = "id", False
    status = one("f_status")
    return {"person": num("f_person"), "client": num("f_client"), "status": status if status in dict(PAAF_STATES) else "", "sort": sort.lstrip("-"), "desc": desc}


def paaf_hidden(f):
    """The list's filters and order carried through a form, so an action leaves you looking at the same list."""
    return hidden(f_person=f["person"] or "", f_client=f["client"] or "", f_status=f["status"], f_sort=("-" if f["desc"] else "") + f["sort"])


def admin_body(con, u, t, q=None):
    b = []
    if t == "periods":
        rows = [list(r) for r in con.execute("select week_start, status, closed_by, closed_at from period order by week_start")]
        b.append(table(["week_start", "status", "closed_by", "closed_at"], [r[:3] + [fmt_time(r[3])] for r in rows]))
        closes = "".join('<form method="post" action="/admin/period" style="display:inline">%s'
                         '<input type="submit" value="Close week %s"></form> ' % (hidden(u=u, week_start=r[0], action="close"), r[0])
                         for r in rows if r[1] == "Open")
        b.append("<p>%s</p>" % closes)
        reopens = "".join('<form method="post" action="/admin/period" style="display:inline">%s'
                          '<input type="submit" value="Reopen week %s"></form> ' % (hidden(u=u, week_start=r[0], action="reopen"), r[0])
                          for r in rows if r[1] == "Closed")
        if reopens:
            b.append("<p>%s</p>" % reopens)
        b.append("<p><small>Each week opens automatically when the week starts. Close a week here when it should stop taking new hours. A closed week can be reopened, for example so a reopened timesheet can be corrected.</small></p>")
        b.append('<form method="post" action="/admin/period">%s<b>Open a missed week</b> - Monday date %s '
                 '<input type="submit" value="Open week"></form>' % (hidden(u=u, action="open"), date_input("week_start")))
    elif t == "users":
        needle = ((q or {}).get("f_q", [""])[0]).strip()
        b.append('<form method="get" action="/admin">%s<p>Find a person (name or NMC ID) <input type="text" name="f_q" size="20" value="%s"> '
                 '<input type="submit" value="Search"> <input type="button" value="Clear" onclick="location.href=\'/admin?u=%d&amp;t=users\'"></p></form>' % (
                     hidden(u=u, t="users"), esc(needle), u))
        rows = [[r["nmc_id"], r["name"], r["employment_type"], r["skill_code"] + " " + r["role"], r["manager"] or "", r["permission"], r["start_date"],
                 r["end_date"], edit_link(u, "users", r["user_id"])]
                for r in con.execute("select u.*, r.skill_code, r.name as role, m.name as manager from app_user u join role r on r.role_id=u.role_id "
                                     "left join app_user m on m.user_id=u.line_manager_id "
                                     "where (? = '' or u.name like '%' || ? || '%' or u.nmc_id like '%' || ? || '%') order by u.nmc_id", (needle, needle, needle))]
        b.append(table(["NMC ID", "Name", "Employment", "Role", "Line manager", "Permission", "Start", "End", ""], rows, raw_cols=(8,)))
        b.append('<h4>Add a user</h4><form method="post" action="/admin/add">%s<table border="0" cellpadding="2">'
                 "<tr><td>NMC ID</td><td>%s</td></tr><tr><td>Name</td><td>%s</td></tr>"
                 "<tr><td>Employment</td><td>%s</td></tr><tr><td>Role</td><td>%s</td></tr><tr><td>Line manager</td><td>%s</td></tr>"
                 "<tr><td>Permission</td><td>%s</td></tr><tr><td>Start date</td><td>%s</td></tr>"
                 "<tr><td>End date (optional)</td><td>%s</td></tr></table><input type=\"submit\" value=\"Add user\"></form>"
                 % (hidden(u=u, t="users"), txt("nmc_id", "", 8), txt("name", "", 30),
                    sel("employment_type", [("Staff", "Staff"), ("Contract", "Contract")]), sel("role_id", opts_roles(con)),
                    sel("line_manager_id", opts_users(con), None, True, "None"),
                    sel("permission", PERMISSION_OPTIONS), date_input("start_date", datetime.date.today().isoformat()),
                    date_input("end_date")))
    elif t == "roles":
        family = ((q or {}).get("f_family", [""])[0]).strip()
        known = {r[0] for r in con.execute("select distinct family from role")}
        if family and family in known:
            # one job family: its job titles, how many people hold each, and the means to rename the family or add a title to it
            b.append('<p><a href="/admin?u=%d&amp;t=roles">&lt;&lt; All job families</a></p><h4>%s</h4>' % (u, esc(family)))
            rows = [[r["skill_code"], r["name"], r["people"], "Yes" if r["active"] else "No", edit_link(u, "roles", r["role_id"])]
                    for r in con.execute("select ro.*, (select count(*) from app_user x where x.role_id=ro.role_id) as people "
                                         "from role ro where ro.family=? order by ro.skill_code", (family,))]
            b.append(table(["Skill code", "Job title", "People in this role", "Active", ""], rows, raw_cols=(4,)))
            b.append('<h4>Add a job title to %s</h4><form method="post" action="/admin/add">%s%s Skill code %s Job title %s '
                     '<input type="submit" value="Add job title"></form>' % (esc(family), hidden(u=u, t="roles", family=family), "", txt("skill_code", "", 8), txt("name", "", 30)))
            b.append('<h4>Rename this family</h4><p><small>Renames the family on all of its job titles.</small></p>'
                     '<form method="post" action="/admin/save">%s New name %s <input type="submit" value="Rename family"></form>' % (
                         hidden(u=u, t="roles", id=0, old_family=family), txt("new_family", family, 24)))
        else:
            fams = con.execute("select family, count(*) as titles, sum(active) as active_titles, "
                               "(select count(*) from app_user x join role r2 on r2.role_id=x.role_id where r2.family=role.family) as people "
                               "from role group by family order by family").fetchall()
            rows = ['<tr><td><a href="/admin?u=%d&amp;t=roles&amp;f_family=%s">%s</a></td><td align="right">%d</td><td align="right">%d</td><td align="right">%d</td></tr>' % (
                u, quote(f["family"]), esc(f["family"]), f["titles"], f["active_titles"], f["people"]) for f in fams]
            b.append("<p><small>Job families (departments) group job titles. Click a family to see its job titles and rename it.</small></p>")
            b.append('<table border="1" cellpadding="3" cellspacing="0"><tr><th>Job family</th><th>Job titles</th><th>Active titles</th><th>People</th></tr>%s</table>' % "".join(rows))
            b.append('<h4>Add a job title</h4><p><small>Type an existing family name to add to it, or a new name to start a new family.</small></p>'
                     '<form method="post" action="/admin/add">%s Job family <input type="text" name="family" size="24" list="families"><datalist id="families">%s</datalist> '
                     'Skill code %s Job title %s <input type="submit" value="Add role"></form>' % (
                         hidden(u=u, t="roles"), "".join('<option value="%s">' % esc(f["family"]) for f in fams), txt("skill_code", "", 8), txt("name", "", 30)))
    elif t == "clients":
        rows = [[r["name"], "Internal (not billed)" if r["is_internal"] else "Client", r["approver"] or "none yet", edit_link(u, "clients", r["client_id"])]
                for r in con.execute("select c.client_id, c.name, c.is_internal, a.name as approver from client c "
                                     "left join app_user a on a.user_id=c.approver_user_id order by c.is_internal, c.name")]
        b.append("<p><small>Each client has a main approver, who approves hours booked to any of its scopes. A scope can also have secondary approvers "
                 "(Projects / scopes / CTRs tab). Nobody approves their own hours; if the booker is a scope's only approver, a cost controller approves them.</small></p>")
        b.append(table(["Client", "Type", "Main approver", ""], rows, raw_cols=(3,)))
        b.append('<h4>Add a client</h4><form method="post" action="/admin/add">%s Name %s Main approver %s <input type="submit" value="Add client"></form>'
                 % (hidden(u=u, t="clients"), txt("name", "", 30), sel("approver_user_id", opts_approvers(con), None, True, "Choose an approver")))
    elif t == "rates":
        try:
            f_client = int((q or {}).get("f_client", [""])[0])
        except ValueError:
            f_client = None
        today = datetime.date.today().isoformat()
        b.append('<form method="get" action="/admin">%s<p>Client %s <input type="submit" value="Filter"> '
                 '<small>Current is the rate in force today; Superseded rates stay so past hours keep their price; Future starts later.</small></p></form>' % (
                     hidden(u=u, t="rates"), sel("f_client", opts_clients(con, True), f_client, True, "All clients")))
        rows = [[r["client"], r["skill_code"], r["role"], "%.2f" % r["sales_rate"], r["effective_from"], r["state"]]
                for r in con.execute("select c.name as client, ro.skill_code, ro.name as role, rt.sales_rate, rt.effective_from, "
                                     "case when rt.effective_from > ? then 'Future' when rt.effective_from = (select max(r2.effective_from) from rate r2 "
                                     "where r2.client_id=rt.client_id and r2.role_id=rt.role_id and r2.effective_from <= ?) then 'Current' else 'Superseded' end as state "
                                     "from rate rt join client c on c.client_id=rt.client_id join role ro on ro.role_id=rt.role_id "
                                     "where (? is null or rt.client_id=?) order by c.name, ro.skill_code, rt.effective_from desc", (today, today, f_client, f_client))]
        b.append(table(["Client", "Skill code", "Role", "Sales rate", "Effective from", "State"], rows))
        b.append('<h4>Add a rate</h4><p><small>Rates are never edited. To change a rate, add a new row with a later effective date. '
                 "Old rows stay, so hours already booked keep the price they were booked at.</small></p>"
                 '<form method="post" action="/admin/add">%s Client %s Skill code %s Sales rate %s Effective from %s '
                 '<input type="submit" value="Add rate"></form>'
                 % (hidden(u=u, t="rates"), sel("client_id", opts_clients(con, True)), sel("role_id", opts_roles(con)),
                    txt("sales_rate", "", 8), date_input("effective_from", datetime.date.today().isoformat())))
    elif t == "paafs":
        flt = paaf_filters(q or {})
        today = datetime.date.today().isoformat()
        where, wargs = "", []
        for key, col in (("person", "p.user_id"), ("client", "p.client_id")):
            if flt[key]:
                where += " and %s=?" % col
                wargs.append(flt[key])
        if flt["status"] == "current":
            where += " and (p.expiry_date is null or p.expiry_date >= ?)"
            wargs.append(today)
        elif flt["status"] == "ended":
            where += " and p.expiry_date < ?"
            wargs.append(today)
        total = con.execute("select count(*) from paaf").fetchone()[0]
        order = PAAF_SORTS[flt["sort"]][1 if flt["desc"] else 0]
        rows = []
        found = con.execute("select p.*, u.name as uname, c.name as client, ro.skill_code, rt.sales_rate "
                            "from paaf p join app_user u on u.user_id=p.user_id join client c on c.client_id=p.client_id "
                            "join rate rt on rt.rate_id=p.rate_id join role ro on ro.role_id=rt.role_id where 1=1" + where + " order by " + order, wargs).fetchall()
        b.append("<p><small>The people who work on each client's projects: the list that goes to the client. None of our clients approve individuals, so a person is "
                 "on a client's list as soon as they are added and can book to that client's CTRs until their end date.</small></p>")
        b.append('<form method="get" action="/admin">%s<p>Person %s Client %s Show %s <input type="submit" value="Filter"> '
                 '<input type="button" value="Clear filters" onclick="location.href=\'/admin?u=%d&amp;t=paafs\'"> '
                 '<small>Showing %d of %d entries, in order of the # column. Click a column heading to sort by it, and again to reverse it.</small></p></form>' % (
                     hidden(u=u, t="paafs", f_sort=("-" if flt["desc"] else "") + flt["sort"]),
                     sel("f_person", [(r["user_id"], r["name"]) for r in con.execute(
                         "select distinct a.user_id, a.name from app_user a join paaf p on p.user_id=a.user_id order by a.name")], flt["person"], True, "Everyone"),
                     sel("f_client", [tuple(r) for r in con.execute(
                         "select distinct c.client_id, c.name from client c join paaf p on p.client_id=c.client_id order by c.name")], flt["client"], True, "All clients"),
                     sel("f_status", PAAF_STATES, flt["status"], True, "Everyone listed"), u, len(found), total))

        def head(label, key):
            flip = "-" + key if (flt["sort"] == key and not flt["desc"]) else key
            arrow = (" &#9650;" if not flt["desc"] else " &#9660;") if flt["sort"] == key else ""
            return '<a href="/admin?u=%d&amp;t=paafs&amp;f_person=%s&amp;f_client=%s&amp;f_status=%s&amp;f_sort=%s" style="color:inherit">%s%s</a>' % (
                u, flt["person"] or "", flt["client"] or "", quote(flt["status"]), quote(flip), label, arrow)
        for r in found:
            ended = bool(r["expiry_date"] and r["expiry_date"] < today)
            f = '<form method="post" action="/admin/paaf" style="display:inline">%s%s' % (hidden(u=u, paaf_id=r["paaf_id"]), paaf_hidden(flt))
            f += ('%s <input type="submit" name="action" value="Change rate"> <br>End date %s <input type="submit" name="action" value="Save end date"> '
                  '<small>(empty = no end)</small></form>' % (sel("rate_id", rate_options(con, r["client_id"]), r["rate_id"]), date_input("expiry_date", r["expiry_date"] or "")))
            rows.append([r["paaf_id"], r["uname"], r["client"], r["skill_code"], "%g" % r["sales_rate"], ("Ended %s" % r["expiry_date"]) if ended else (r["expiry_date"] or ""), f])
        heads = [head("#", "id"), head("Name", "name"), head("Client", "client"), "Skill code", "Sales rate", head("Ends", "expiry"), "Action"]  # raw: the headings hold sort links
        if rows:
            b.append(table(heads, rows, raw_cols=(6,), raw_heads=True))
        else:
            b.append("<p>Nobody matches these filters.</p>")
        b.append('<h4>Add a person to a client</h4><p><small>One entry per person per client. Pick the rate to assign from the client\'s rate card, or leave it '
                 "on the default, the rate for the person's own role. The rate card line you pick is the person's position on that client: it sets the rate for their hours "
                 "when they book as their own role, at the rate in force on the work date (covering another role uses the card line for that role). "
                 "The rate can be changed at any time; hours already booked keep the rate they were booked at. Set an end date when someone leaves the client's projects.</small></p>"
                 '<form method="post" action="/admin/add">%s Person %s Client %s Rate %s <input type="submit" value="Add person"></form>'
                 % (hidden(u=u, t="paafs"), sel("user_id", opts_users(con)), sel("client_id", opts_clients(con, True)),
                    sel("rate_id", rate_options(con), None, True, "Default: the person's own role")))
    elif t == "allowance_rates":
        today = datetime.date.today().isoformat()
        grid = []
        for al in con.execute("select allowance_id, name from allowance_type where active=1 order by allowance_id"):
            cur = con.execute("select amount from allowance_rate where allowance_id=? and effective_from<=? order by effective_from desc limit 1", (al["allowance_id"], today)).fetchone()
            grid.append('<tr><td>%s</td><td><input type="text" size="8" name="r_%d" value="%s"></td></tr>' % (esc(al["name"]), al["allowance_id"], fmt(cur[0] if cur else 0)))
        b.append("<p><small>What one day of each allowance is worth. Allowances are for staff only (contractors cannot claim them). Rates are never edited: change the figure "
                 "here and it is saved as a new rate from the date you give, so earlier claims keep the value they had.</small></p>")
        b.append('<form method="post" action="/admin/allowance_rates">%s<table border="1" cellpadding="3" cellspacing="0"><tr><th>Allowance</th><th>&pound; per day</th></tr>%s</table>'
                 '<p>New rate applies from %s <input type="submit" value="Save rate"></p></form>' % (hidden(u=u), "".join(grid), date_input("effective_from", today)))
        hist = [[r["name"], "%.2f" % r["amount"], r["effective_from"]]
                for r in con.execute("select at.name, ar.amount, ar.effective_from from allowance_rate ar join allowance_type at on at.allowance_id=ar.allowance_id "
                                     "order by at.allowance_id, ar.effective_from desc")]
        b.append("<h4>Every rate, newest first</h4>" + table(["Allowance", "Per day", "From"], hist))
    elif t == "budgets":
        b.append("<p><small>Budget hours per CTR come from P6, which holds hours only. Paste either (1) a P6 export copied from SSMS or saved as CSV, with the columns "
                 "<b>project, WBS name, activity code, budget hours, actual hours, remaining hours, % complete</b> (the CTR No is read from the end of the WBS name, "
                 "for example <i>Process Engineering - A0101-PR-D-001</i>; you see a preview and choose the P6 project before anything is loaded), or (2) one CTR per line: "
                 "the CTR No then the budget hours. Comma or tab separated; a heading line is ignored. Each load is saved as a snapshot with its as-of date and never edited, "
                 "so earlier budgets are kept; the latest is compared with the approved hours booked here on the Reports page, the dashboard and each CTR's page. "
                 "A CTR whose figures have not changed is left alone.</small></p>")
        b.append('<form method="post" action="/admin/budgets">%s<p>As of %s</p><p><textarea name="data" rows="8" cols="80" placeholder="BB0001-PM-D-001,160"></textarea></p>'
                 '<input type="submit" value="Load budgets"></form>' % (hidden(u=u), date_input("as_of", datetime.date.today().isoformat())))
        rows = [[r["phase"], r["client"], fmt(r["budget_hours"]), r["as_of"], fmt(r["actual_hours"])]
                for r in con.execute("select * from v_ctr_budget where budget_hours is not null order by phase")]
        b.append("<h4>Budgets now held</h4>" + (table(["CTR No", "Client", "Budget hrs", "As of", "Actual hrs (approved)"], rows) if rows else "<p>None loaded yet.</p>"))
    elif t == "locations":
        b.append("<p><small>Where hours are worked. People pick one on each timesheet row (and tick Overtime for overtime). "
                 "Rename a location or switch it off to stop new bookings to it; hours already booked keep it. At least one must stay active.</small></p>")
        rows = [[r["name"], "Yes" if r["active"] else "No", edit_link(u, "locations", r["location_id"])]
                for r in con.execute("select * from location order by location_id")]
        b.append(table(["Location", "Active", ""], rows, raw_cols=(2,)))
        b.append('<h4>Add a location</h4><form method="post" action="/admin/add">%s Name %s <input type="submit" value="Add location"></form>'
                 % (hidden(u=u, t="locations"), txt("name", "", 24)))
    elif t == "internal":
        b.append("<p><small>Internal codes are for hours NMC pays for itself, such as Business Services or Training. People pick one in their "
                 "timesheet (shown as Internal - name). They need no PAAF and have no rate, and they are kept out of the client reports. "
                 "Each code has an approver, who approves the hours booked to it. Close a code to stop new bookings to it.</small></p>")
        rows = [[r["scope_code"], r["description"], r["status"], r["approvers"] or "", edit_link(u, "internal", r["scope_id"])]
                for r in con.execute("select s.scope_id, s.scope_code, s.description, s.status, "
                                     "(select group_concat(x.name, ', ') from (select au.name from v_scope_approver va join app_user au on au.user_id=va.user_id "
                                     "where va.scope_id=s.scope_id order by au.name) x) as approvers from scope s "
                                     "join client c on c.client_id=s.client_id where c.is_internal=1 order by s.scope_code")]
        b.append(table(["Code", "Name", "Status", "Approvers", ""], rows, raw_cols=(4,)))
        b.append('<h4>Add an internal code</h4><form method="post" action="/admin/add">%s<table border="0" cellpadding="2">'
                 "<tr><td>Name</td><td>%s</td></tr></table>"
                 '<input type="submit" value="Add internal code"></form><p><small>The Internal client\'s main approver (set on the Clients tab) approves its hours; '
                 'add secondary approvers on the code\'s Edit page.</small></p>'
                 % (hidden(u=u, t="internal"), txt("description", "", 40)))
    elif t == "scopes":
        b.append("<p><small>Structure: client &gt; project &gt; scope &gt; CTR. A CTR is the booking code hours are booked to. Hours are approved by the client's main approver "
                 "(set on the Clients tab) or a secondary approver named on the scope.</small></p>")
        b.append("<h4>Projects</h4>")
        rows = [[r["client"], r["project_code"], r["name"], r["status"], edit_link(u, "projects", r["project_id"])]
                for r in con.execute("select p.*, c.name as client from project p join client c on c.client_id=p.client_id order by c.name, p.name")]
        b.append(table(["Client", "Code", "Project", "Status", ""], rows, raw_cols=(4,)))
        b.append('<form method="post" action="/admin/add">%s Client %s Code %s Name %s <input type="submit" value="Add project"></form>'
                 % (hidden(u=u, t="projects"), sel("client_id", opts_clients(con)), txt("project_code", "", 10), txt("name", "", 30)))
        b.append("<h4>Scopes</h4>")
        rows = [[r["client"], r["project"], r["scope_code"], r["description"], r["status"], r["secondary"] or "none", edit_link(u, "scopes", r["scope_id"])]
                for r in con.execute("select s.scope_id, c.name as client, pr.name as project, s.scope_code, s.description, s.status, "
                                     "(select group_concat(x.name, ', ') from (select au.name from scope_approver sa join app_user au on au.user_id=sa.user_id "
                                     "where sa.scope_id=s.scope_id order by au.name) x) as secondary "
                                     "from scope s join client c on c.client_id=s.client_id join project pr on pr.project_id=s.project_id "
                                     "order by c.name, pr.name, s.scope_code")]
        b.append(table(["Client", "Project", "Scope", "Description", "Status", "Secondary approvers", ""], rows, raw_cols=(6,)))
        b.append('<p><b>Add a scope</b> <small>A CTR called General is created automatically; add or rename CTRs below. The client\'s main approver approves the scope\'s hours; '
                 'name secondary approvers on the scope\'s Edit page.</small></p>'
                 '<form method="post" action="/admin/add">%s<table border="0" cellpadding="2">'
                 "<tr><td>Project</td><td>%s</td></tr><tr><td>Scope code</td><td>%s</td></tr>"
                 "<tr><td>Description</td><td>%s</td></tr><tr><td>Status</td><td>%s</td></tr></table>"
                 '<input type="submit" value="Add scope"></form>'
                 % (hidden(u=u, t="scopes"), sel("project_id", opts_projects(con)), txt("scope_code", "", 10), txt("description", "", 40),
                    sel("status", [("Open", "Open"), ("Pipeline", "Pipeline"), ("Closed", "Closed")])))
        b.append("<h4>CTRs</h4>")
        rows = []
        for r in con.execute("select ph.*, s.scope_code, s.description as scope_desc, c.name as client, "
                             "(select count(*) from phase_person pp where pp.phase_id=ph.phase_id) as n_people "
                             "from phase ph join scope s on s.scope_id=ph.scope_id join client c on c.client_id=s.client_id "
                             "order by c.name, s.scope_code, ph.name"):
            controls = []
            if r["restricted"]:
                controls.append("%d named %s only" % (r["n_people"], "person" if r["n_people"] == 1 else "people"))
            if r["book_from"] or r["book_to"]:
                controls.append("%s to %s" % (r["book_from"] or "any date", r["book_to"] or "any date"))
            rows.append([r["client"], r["scope_code"], r["scope_desc"], r["name"], r["description"] or "", r["status"],
                         "; ".join(controls) or "open to all", edit_link(u, "phases", r["phase_id"])])
        b.append(table(["Client", "Scope", "Scope description", "CTR (booking code)", "CTR description", "Status", "Who and when can book", ""], rows, raw_cols=(7,)))
        b.append('<form method="post" action="/admin/add">%s Scope %s CTR name %s <input type="submit" value="Add CTR"></form>'
                 % (hidden(u=u, t="phases"), sel("scope_id", opts_scopes(con)), txt("name", "", 20)))
    return "".join(b)


def view_admin(con, u, t, msgs=None, ok=False, q=None):
    tabs = allowed_tabs(con, u)
    if not tabs or (t in dict(ADMIN_TABS) and not can_use_tab(con, u, t)):
        return no_access(con, u, "Admin")
    t = t if t in dict(tabs) else tabs[0][0]
    links = "".join('<a href="/admin?u=%d&amp;t=%s"%s>%s</a>' % (u, k, ' class="cur"' if k == t else "", esc(v)) for k, v in tabs)
    body = '%s<p class="tabs">%s</p><h4>%s</h4>%s' % (msgs_html(msgs, ok), links, esc(dict(tabs)[t]), admin_body(con, u, t, q))
    return layout(con, u, "Admin", body)


def view_admin_edit(con, u, t, i, msgs=None):
    if not can_use_tab(con, u, t):
        return no_access(con, u, "Admin")
    back = '<p><a href="/admin?u=%d&amp;t=%s">&lt;&lt; Back</a></p>' % (u, "scopes" if t in ("projects", "phases") else t)
    if t == "users":
        r = con.execute("select * from app_user where user_id=?", (i,)).fetchone()
        if not r:
            return layout(con, u, "Admin", "<p>Not found.</p>")
        form = ("<tr><td>NMC ID</td><td>%s</td></tr><tr><td>Name</td><td>%s</td></tr><tr><td>Employment</td><td>%s</td></tr>"
                "<tr><td>Role</td><td>%s</td></tr><tr><td>Line manager</td><td>%s</td></tr><tr><td>Permission</td><td>%s</td></tr><tr><td>Start date</td><td>%s</td></tr>"
                "<tr><td>End date</td><td>%s</td></tr>" % (
                    esc(r["nmc_id"]), txt("name", r["name"], 30), sel("employment_type", [("Staff", "Staff"), ("Contract", "Contract")], r["employment_type"]),
                    sel("role_id", opts_roles(con, active_only=False), r["role_id"]),
                    sel("line_manager_id", [x for x in opts_users(con) if x[0] != i], r["line_manager_id"], True, "None"),
                    sel("permission", PERMISSION_OPTIONS, r["permission"]), esc(r["start_date"]), date_input("end_date", r["end_date"] or "")))
        note = "<p><small>Changing someone's role does not change their existing PAAFs or booked hours.</small></p>"
    elif t == "roles":
        r = con.execute("select * from role where role_id=?", (i,)).fetchone()
        if not r:
            return layout(con, u, "Admin", "<p>Not found.</p>")
        form = ("<tr><td>Job family</td><td>%s</td></tr><tr><td>Skill code</td><td>%s</td></tr><tr><td>Job title</td><td>%s</td></tr><tr><td>Active</td><td>%s</td></tr>"
                % (txt("family", r["family"], 24), txt("skill_code", r["skill_code"], 8), txt("name", r["name"], 30), sel("active", [("1", "Yes"), ("0", "No")], str(r["active"]))))
        note = "<p><small>Deactivating a role hides it from new users and new timesheet rows. It does not affect past entries.</small></p>"
    elif t == "locations":
        r = con.execute("select * from location where location_id=?", (i,)).fetchone()
        if not r:
            return layout(con, u, "Admin", "<p>Not found.</p>")
        form = ("<tr><td>Name</td><td>%s</td></tr><tr><td>Active</td><td>%s</td></tr>" % (
            txt("name", r["name"], 24), sel("active", [("1", "Yes"), ("0", "No")], str(r["active"]))))
        note = "<p><small>Switching a location off stops new bookings to it. Hours already booked keep it.</small></p>"
    elif t == "clients":
        r = con.execute("select c.*, a.name as approver from client c left join app_user a on a.user_id=c.approver_user_id where c.client_id=?", (i,)).fetchone()
        if not r:
            return layout(con, u, "Admin", "<p>Not found.</p>")
        form = ("<tr><td>Client</td><td>%s</td></tr><tr><td>Main approver</td><td>%s</td></tr><tr><td>Framework agreement ref</td><td>%s</td></tr>" % (
            esc(r["name"]), sel("approver_user_id", opts_approvers(con), r["approver_user_id"], True, "None"), txt("framework_ref", r["framework_ref"] or "", 40)))
        note = ("<p><small>The main approver approves hours booked to any scope of this client (never their own). Changing it moves the waiting entries to the new "
                "approver's queue.</small></p>")
    elif t in ("scopes", "internal"):  # an internal code is a scope of the Internal client
        r = con.execute("select s.*, c.name as client, a.name as main_approver from scope s join client c on c.client_id=s.client_id "
                        "left join app_user a on a.user_id=c.approver_user_id where scope_id=?", (i,)).fetchone()
        if not r:
            return layout(con, u, "Admin", "<p>Not found.</p>")
        chosen = {x[0] for x in con.execute("select user_id from scope_approver where scope_id=?", (i,))}
        boxes = " ".join('<label><input type="checkbox" name="secondary" value="%d"%s> %s</label>' % (
            p[0], " checked" if p[0] in chosen else "", esc(p[1])) for p in opts_approvers(con))
        form = ("<tr><td>Client</td><td>%s</td></tr><tr><td>Scope code</td><td>%s</td></tr><tr><td>Description</td><td>%s</td></tr>"
                "<tr><td>Status</td><td>%s</td></tr><tr><td>Main approver (from the client)</td><td>%s</td></tr><tr><td>Secondary approvers</td><td>%s</td></tr>" % (
                    esc(r["client"]), esc(r["scope_code"]), txt("description", r["description"], 40),
                    sel("status", [("Open", "Open"), ("Pipeline", "Pipeline"), ("Closed", "Closed")], r["status"]),
                    esc(r["main_approver"] or "none"), boxes))
        note = ("<p><small>Hours on this scope are approved by the client's main approver or any secondary approver ticked here (never their own). "
                "Closing a scope stops new bookings.</small></p>")
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
        allowed = {x[0] for x in con.execute("select user_id from phase_person where phase_id=?", (i,))}
        people = " ".join('<label><input type="checkbox" name="person" value="%d"%s> %s</label>' % (
            p["user_id"], " checked" if p["user_id"] in allowed else "", esc(p["name"]))
            for p in con.execute("select user_id, name from app_user order by name"))
        def area(name, value, rows=3):
            return '<textarea name="%s" rows="%d" cols="70">%s</textarea>' % (name, rows, esc(value or ""))
        form = ("<tr><td>Scope</td><td>%s</td></tr><tr><td>CTR name</td><td>%s</td></tr><tr><td>Status</td><td>%s</td></tr>"
                "<tr><td>CTR code</td><td>%s <small>discipline - phase letter - number, e.g. PM-D-001. The scope code goes in front: %s-&hellip;</small></td></tr>"
                "<tr><td>CTR title</td><td>%s</td></tr><tr><td>Prepared by</td><td>%s</td></tr><tr><td>Checked by</td><td>%s</td></tr>"
                "<tr><td>Objectives</td><td>%s</td></tr><tr><td>Input requirements</td><td>%s</td></tr><tr><td>Scope of work</td><td>%s</td></tr>"
                "<tr><td>This CTR assumes</td><td>%s</td></tr><tr><td>This CTR excludes</td><td>%s</td></tr>"
                "<tr><td>Expenses</td><td>%s</td></tr><tr><td>Payment terms</td><td>%s</td></tr><tr><td>Basis of proposal</td><td>%s</td></tr>"
                "<tr><td>WBS</td><td>%s</td></tr><tr><td>CBS</td><td>%s</td></tr><tr><td>ABS</td><td>%s</td></tr>"
                "<tr><td>Who can book</td><td>%s</td></tr><tr><td>People allowed</td><td>%s</td></tr>"
                "<tr><td>Bookable from</td><td>%s</td></tr><tr><td>Bookable until</td><td>%s</td></tr>"
                % (esc(r["scope_code"]), txt("name", r["name"], 20), sel("status", [("Open", "Open"), ("Closed", "Closed")], r["status"]),
                   txt("ctr_code", r["ctr_code"] or "", 14), esc(r["scope_code"]),
                   txt("description", r["description"] or "", 50), txt("prepared_by", r["prepared_by"] or "", 20), txt("checked_by", r["checked_by"] or "", 20),
                   area("objectives", r["objectives"]), area("inputs", r["inputs"]), area("scope_of_work", r["scope_of_work"]),
                   area("assumptions", r["assumptions"]), area("exclusions", r["exclusions"]),
                   txt("expenses", r["expenses"] or "Reimbursable", 20),
                   sel("payment_terms", [("Reimbursable", "Reimbursable"), ("Budget", "Budget"), ("Lump Sum", "Lump Sum")], r["payment_terms"]),
                   area("basis", r["basis"]),
                   txt("wbs", r["wbs"] or "", 20), txt("cbs", r["cbs"] or "", 20), txt("abs_code", r["abs_code"] or "", 20),
                   sel("restricted", [("0", "Anyone with an approved PAAF (or an internal code)"), ("1", "Only the people ticked below")], str(r["restricted"])),
                   people, date_input("book_from", r["book_from"] or ""), date_input("book_to", r["book_to"] or "")))
        note = ("<p><small>Closing a CTR stops new bookings to it. Existing hours are not affected. Hours are approved by the client's main approver or the scope's "
                "secondary approvers (set on the scope). Transaction controls: by default anyone who may book to the client "
                "can book to this code. Choose \"Only the people ticked\" to limit it to a list (nobody can book if the list is empty), and set dates to limit "
                "which work dates can be booked. These controls stop new bookings only; hours already booked are not affected. WBS, CBS and ABS are "
                "optional reference fields.</small></p>")
    else:
        return layout(con, u, "Admin", "<p>Not found.</p>")
    body = '%s%s<form method="post" action="/admin/save">%s<table border="0" cellpadding="2">%s</table>%s<input type="submit" value="Save changes"></form>' % (
        msgs_html(msgs), back, hidden(u=u, t=t, id=i), form, note)
    return layout(con, u, "Edit", body)


def admin_add(con, form):
    u, t = int(form["u"][0]), form["t"][0]
    if not can_use_tab(con, u, t):
        return view_admin(con, u, t)
    g = lambda k: form.get(k, [""])[0].strip()
    try:
        if t == "users":
            con.execute("insert into app_user (nmc_id, employment_type, name, role_id, permission, start_date, end_date, line_manager_id) values (?,?,?,?,?,?,?,?)",
                        (need_text(g("nmc_id"), "NMC ID"), g("employment_type"), need_text(g("name"), "Name"), int(g("role_id")),
                         g("permission"), need_date(g("start_date"), "Start date"), need_date(g("end_date"), "End date", False),
                         int(g("line_manager_id")) if g("line_manager_id") else None))
            msg = "User added."
        elif t == "roles":
            con.execute("insert into role (skill_code, name, family) values (?,?,?)",
                        (need_text(g("skill_code"), "Skill code"), need_text(g("name"), "Job title"), need_text(g("family"), "Job family")))
            msg = "Role added."
            con.commit()
            return view_admin(con, u, t, [msg], ok=True, q={"f_family": [g("family")]})
        elif t == "clients":
            if not g("approver_user_id"):
                raise ValueError("Choose a main approver for the client.")
            con.execute("insert into client (name, approver_user_id) values (?,?)", (need_text(g("name"), "Client name"), int(g("approver_user_id"))))
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
            cur = con.execute("insert into scope (scope_code, client_id, project_id, description, status) values (?,?,?,?,?)",
                              (need_text(g("scope_code"), "Scope code"), pr[0], int(g("project_id")), need_text(g("description"), "Description"), g("status")))
            con.execute("insert into phase (scope_id, name) values (?, 'General')", (cur.lastrowid,))
            msg = "Scope added with a General CTR."
        elif t == "phases":
            con.execute("insert into phase (scope_id, name) values (?,?)", (int(g("scope_id")), need_text(g("name"), "CTR name")))
            msg = "CTR added."
        elif t == "locations":
            con.execute("insert into location (name) values (?)", (need_text(g("name"), "Location name"),))
            msg = "Location added."
        elif t == "internal":
            # An internal code is a scope (with a General phase) under the Internal client's project. The code is numbered for you.
            pr = con.execute("select p.project_id, p.client_id from project p join client c on c.client_id=p.client_id "
                             "where c.is_internal=1 order by p.project_id limit 1").fetchone()
            if not pr:
                raise ValueError("There is no Internal client and project to add codes to.")
            n = con.execute("select coalesce(max(cast(substr(scope_code, 4) as integer)), 0) + 1 from scope "
                            "where scope_code like 'INT%' and substr(scope_code, 4) glob '[0-9]*'").fetchone()[0]
            code = "INT%02d" % n
            cur = con.execute("insert into scope (scope_code, client_id, project_id, description, status) values (?,?,?,?, 'Open')",
                              (code, pr["client_id"], pr["project_id"], need_text(g("description"), "Name")))
            con.execute("insert into phase (scope_id, name) values (?, 'General')", (cur.lastrowid,))
            msg = "Internal code %s added." % code
        elif t == "paafs":
            user_id, client_id = int(g("user_id")), int(g("client_id"))
            if g("rate_id"):  # a rate picked from the dropdown must be on this client's rate card
                rate = con.execute("select rate_id from rate where rate_id=? and client_id=?", (int(g("rate_id")), client_id)).fetchone()
                if not rate:
                    raise ValueError("That rate is not on this client's rate card. Choose one of the client's rates.")
            else:
                rate = con.execute("select rt.rate_id from rate rt join app_user u on u.role_id=rt.role_id where u.user_id=? and rt.client_id=? "
                                   "and rt.effective_from<=? order by rt.effective_from desc limit 1",
                                   (user_id, client_id, datetime.date.today().isoformat())).fetchone()
            if not rate:
                raise ValueError("That client's rate card has no rate for this person's role. Add the rate first, or pick a rate.")
            con.execute("insert into paaf (user_id, client_id, rate_id, status, decided_date) values (?,?,?,'Approved',?)",
                        (user_id, client_id, rate[0], datetime.date.today().isoformat()))
            msg = "Person added to the client's list. They can book to its CTRs now."
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
                text = "That person is already on this client's list."
        return view_admin(con, u, t, [text])
    except sqlite3.DatabaseError as e:
        return view_admin(con, u, t, [str(e)])


def admin_save(con, form):
    u, t, i = int(form["u"][0]), form["t"][0], int(form["id"][0])
    if not can_use_tab(con, u, t):
        return view_admin(con, u, t)
    g = lambda k: form.get(k, [""])[0].strip()
    try:
        if t == "users":
            manager = int(g("line_manager_id")) if g("line_manager_id") else None
            if manager == i:
                raise ValueError("A person cannot be their own line manager.")
            con.execute("update app_user set name=?, employment_type=?, role_id=?, permission=?, end_date=?, line_manager_id=? where user_id=?",
                        (need_text(g("name"), "Name"), g("employment_type"), int(g("role_id")), g("permission"),
                         need_date(g("end_date"), "End date", False), manager, i))
        elif t == "roles" and g("old_family"):
            if not g("new_family"):
                return view_admin(con, u, t, ["Give the family a name."], q={"f_family": [g("old_family")]})
            new = g("new_family")
            con.execute("update role set family=? where family=?", (new, g("old_family")))
            con.commit()
            return view_admin(con, u, t, ["Family renamed."], ok=True, q={"f_family": [new]})
        elif t == "roles":
            con.execute("update role set skill_code=?, name=?, family=?, active=? where role_id=?",
                        (need_text(g("skill_code"), "Skill code"), need_text(g("name"), "Job title"), need_text(g("family"), "Job family"), int(g("active")), i))
            con.commit()
            return view_admin(con, u, t, ["Saved."], ok=True, q={"f_family": [g("family")]})
        elif t == "locations":
            others = con.execute("select count(*) from location where active=1 and location_id<>?", (i,)).fetchone()[0]
            if int(g("active")) == 0 and others == 0:
                raise ValueError("At least one location must stay active.")
            con.execute("update location set name=?, active=? where location_id=?", (need_text(g("name"), "Location name"), int(g("active")), i))
        elif t == "clients":
            con.execute("update client set approver_user_id=?, framework_ref=? where client_id=?",
                        (int(g("approver_user_id")) if g("approver_user_id") else None, g("framework_ref") or None, i))
        elif t in ("scopes", "internal"):
            con.execute("update scope set description=?, status=? where scope_id=?", (need_text(g("description"), "Description"), g("status"), i))
            con.execute("delete from scope_approver where scope_id=?", (i,))
            for person in form.get("secondary", []):
                con.execute("insert or ignore into scope_approver (scope_id, user_id) values (?,?)", (i, int(person)))
        elif t == "projects":
            con.execute("update project set name=?, status=? where project_id=?", (need_text(g("name"), "Project name"), g("status"), i))
        elif t == "phases":
            b_from, b_to = need_date(g("book_from"), "Bookable from", False), need_date(g("book_to"), "Bookable until", False)
            if b_from and b_to and b_to < b_from:
                raise ValueError("Bookable until cannot be before bookable from.")
            terms = g("payment_terms") or "Reimbursable"
            if terms not in ("Reimbursable", "Budget", "Lump Sum"):
                raise ValueError("Payment terms must be Reimbursable, Budget or Lump Sum.")
            con.execute("update phase set name=?, status=?, description=?, wbs=?, cbs=?, abs_code=?, restricted=?, book_from=?, book_to=?, "
                        "ctr_code=?, objectives=?, inputs=?, scope_of_work=?, assumptions=?, exclusions=?, prepared_by=?, checked_by=?, expenses=?, payment_terms=?, basis=? "
                        "where phase_id=?",
                        (need_text(g("name"), "CTR name"), g("status"), g("description") or None, g("wbs") or None, g("cbs") or None,
                         g("abs_code") or None, 1 if g("restricted") == "1" else 0, b_from, b_to,
                         g("ctr_code") or None, g("objectives") or None, g("inputs") or None, g("scope_of_work") or None, g("assumptions") or None,
                         g("exclusions") or None, g("prepared_by") or None, g("checked_by") or None, g("expenses") or "Reimbursable", terms, g("basis") or None, i))
            con.execute("delete from phase_person where phase_id=?", (i,))
            for person in form.get("person", []):
                con.execute("insert or ignore into phase_person (phase_id, user_id) values (?,?)", (i, int(person)))
        else:
            raise ValueError("Unknown form.")
        con.commit()
        return view_admin(con, u, "scopes" if t in ("projects", "phases") else t, ["Saved."], ok=True)
    except ValueError as e:
        return view_admin_edit(con, u, t, i, [str(e)])
    except sqlite3.DatabaseError as e:
        text = "That already exists (it must be unique)." if "UNIQUE" in str(e) else str(e)
        return view_admin_edit(con, u, t, i, [text])


def act_allowance_rates(con, form):
    u = int(form["u"][0])
    if not can_use_tab(con, u, "allowance_rates"):
        return view_admin(con, u, "allowance_rates")
    try:
        start = need_date(form.get("effective_from", [""])[0], "Effective from")
        changed = 0
        for key, vals in form.items():
            if not key.startswith("r_"):
                continue
            aid = int(key.split("_")[1])
            raw = vals[0].strip()
            try:
                amount = float(raw)
                if amount < 0:
                    raise ValueError
            except ValueError:
                raise ValueError("'%s' is not a rate: use a number of pounds, 0 or more." % raw)
            cur = con.execute("select amount from allowance_rate where allowance_id=? and effective_from<=? order by effective_from desc limit 1", (aid, start)).fetchone()
            if (cur is not None and abs(cur[0] - amount) < 1e-9) or (cur is None and amount == 0):
                continue
            con.execute("insert into allowance_rate (allowance_id, amount, effective_from) values (?,?,?)", (aid, amount, start))
            changed += 1
        con.commit()
        return view_admin(con, u, "allowance_rates", ["%d rate%s saved, from %s." % (changed, "" if changed == 1 else "s", start) if changed else "Nothing changed."], ok=True)
    except ValueError as e:
        con.rollback()
        return view_admin(con, u, "allowance_rates", [str(e)])
    except sqlite3.IntegrityError:
        con.rollback()
        return view_admin(con, u, "allowance_rates", ["A rate already exists from that date. Rates are never edited: choose a later date."])


def act_paaf(con, form):
    u, pid, action = int(form["u"][0]), int(form["paaf_id"][0]), form["action"][0]
    if not can_use_tab(con, u, "paafs"):
        return view_admin(con, u, "paafs")
    q = form  # the list's filters and order come back with the form
    g = lambda k: form.get(k, [""])[0].strip()
    try:
        p = con.execute("select * from paaf where paaf_id=?", (pid,)).fetchone()
        if not p:
            raise ValueError("That entry was not found.")
        if action == "Change rate":
            rate = con.execute("select rate_id from rate where rate_id=? and client_id=?", (int(g("rate_id") or 0), p["client_id"])).fetchone()
            if not rate:
                raise ValueError("Choose one of this client's rates.")
            con.execute("update paaf set rate_id=? where paaf_id=?", (rate[0], pid))
            msg = "Entry %d now uses the chosen rate." % pid
        elif action == "Save end date":
            end = need_date(g("expiry_date"), "End date", False)
            con.execute("update paaf set expiry_date=? where paaf_id=?", (end, pid))
            msg = "Entry %d now ends on %s." % (pid, end) if end else "Entry %d has no end date." % pid
        else:
            raise ValueError("Unknown action.")
        con.commit()
        return view_admin(con, u, "paafs", [msg], ok=True, q=q)
    except ValueError as e:
        return view_admin(con, u, "paafs", [str(e)], q=q)
    except sqlite3.DatabaseError as e:
        return view_admin(con, u, "paafs", [str(e)], q=q)


P6_CTR_RE = re.compile(r"([A-Z]{1,3}\d{3,5}-[A-Z]{2,3}-[A-Z]{1,3}-\d{3})\s*$", re.I)


def _cells(line):
    return [c.strip() for c in next(csv.reader([line], delimiter="\t" if "\t" in line else ","))]


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def p6_format(data):
    """True when the pasted text is a P6 export (seven or more columns) and not the simple 'CTR No, hours' lines."""
    for line in data.replace("\r", "").split("\n"):
        if line.strip():
            return len(_cells(line)) >= 7
    return False


def p6_parse(data):
    """Rows of a P6 export: project, WBS name, activity code, budget, actual, remaining, % complete, and the CTR No read from the end of the WBS name."""
    rows, bad = [], []
    lines = [x for x in data.replace("\r", "").split("\n") if x.strip()]
    for n, line in enumerate(lines, 1):
        c = _cells(line)
        budget = _num(c[3]) if len(c) >= 7 else None
        if budget is None or budget < 0:
            if n > 1:
                bad.append("line %d" % n)
            continue  # (the first line is a heading)
        m = P6_CTR_RE.search(c[1])
        rows.append({"project": c[0], "wbs": c[1], "code": c[2], "budget": budget, "actual": _num(c[4]), "remaining": _num(c[5]), "pct": _num(c[6]),
                     "ctr": m.group(1).upper() if m else None})
    return rows, bad


def p6_budgets(con, u, form, data, as_of):
    """A P6 export: a preview of what would be loaded for one P6 project (and nothing written), then the load itself once confirmed."""
    rows, bad = p6_parse(data)
    if not rows:
        raise ValueError("No rows were found in that export. The columns are: project, WBS name, activity code, budget hours, actual hours, remaining hours, % complete.")
    projects = list(dict.fromkeys(r["project"] for r in rows))
    chosen = form.get("p6_project", [""])[0]
    chosen = chosen if chosen in projects else projects[0]
    mine = [r for r in rows if r["project"] == chosen]
    ctrs = {r["no"].upper(): r["phase_id"] for r in con.execute(
        "select ph.phase_id, s.scope_code || '-' || coalesce(ph.ctr_code, upper(ph.name)) as no from phase ph join scope s on s.scope_id=ph.scope_id "
        "join client c on c.client_id=s.client_id where c.is_internal=0")}
    times = {}
    for r in mine:
        if r["ctr"]:
            times[r["ctr"]] = times.get(r["ctr"], 0) + 1
    for r in mine:
        r["phase_id"] = None
        if not r["ctr"]:
            r["result"], r["act"] = "No CTR No at the end of the WBS name", "skip"
        elif times[r["ctr"]] > 1:
            r["result"], r["act"] = "CTR No appears %d times in this project: not loaded" % times[r["ctr"]], "skip"
        elif r["ctr"] not in ctrs:
            r["result"], r["act"] = "Not a CTR in this system", "skip"
        else:
            r["phase_id"] = ctrs[r["ctr"]]
            cur = con.execute("select hours, as_of, p6_actual_hours, percent_complete from ctr_budget where phase_id=? order by as_of desc limit 1", (r["phase_id"],)).fetchone()
            same = lambda a, b: (a is None and b is None) or (a is not None and b is not None and abs(a - b) < 1e-6)
            if cur and same(cur["hours"], r["budget"]) and same(cur["p6_actual_hours"], r["actual"]) and same(cur["percent_complete"], r["pct"]):
                r["result"], r["act"] = "Unchanged", "same"
            elif cur and cur["as_of"] >= as_of:
                r["result"], r["act"] = "A budget as of %s is already held: use a later date" % cur["as_of"], "skip"
            else:
                r["result"], r["act"] = ("New" if not cur else "Changed (budget was %s hrs)" % fmt(cur["hours"])), "load"
    to_load = [r for r in mine if r["act"] == "load"]
    if "confirm" in form:
        for r in to_load:
            con.execute("insert into ctr_budget (phase_id, hours, as_of, p6_actual_hours, percent_complete, p6_project, loaded_by) values (?,?,?,?,?,?,?)",
                        (r["phase_id"], r["budget"], as_of, r["actual"], r["pct"], chosen, u))
        con.commit()
        skipped = sum(1 for r in mine if r["act"] == "skip")
        return view_admin(con, u, "budgets", ["%d budget%s loaded from P6 project %s, as of %s. %d unchanged, %d not loaded." % (
            len(to_load), "" if len(to_load) == 1 else "s", chosen, as_of, sum(1 for r in mine if r["act"] == "same"), skipped)], ok=True)
    body = ['<p><a href="/admin?u=%d&amp;t=budgets">&lt;&lt; Back to Budgets</a></p>' % u,
            "<p><b>Preview: nothing has been loaded yet.</b> P6 project <b>%s</b>, as of %s. %d of its %d rows would be loaded, %d are unchanged and %d would not be loaded.</p>" % (
                esc(chosen), esc(as_of), len(to_load), len(mine), sum(1 for r in mine if r["act"] == "same"), sum(1 for r in mine if r["act"] == "skip"))]
    if bad:
        body.append("<p><small>Lines that could not be read and are ignored: %s.</small></p>" % esc(", ".join(bad)))
    trs = ["<tr><th>WBS name</th><th>CTR No</th><th>Budget hrs</th><th>P6 actual hrs</th><th>% complete</th><th>Result</th></tr>"]
    for r in mine:
        trs.append('<tr><td>%s</td><td>%s</td><td align="right">%s</td><td align="right">%s</td><td align="right">%s</td><td>%s</td></tr>' % (
            esc(r["wbs"]), esc(r["ctr"] or ""), fmt(r["budget"]), fmt(r["actual"]), fmt(r["pct"]), esc(r["result"])))
    body.append('<table border="1" cellpadding="3" cellspacing="0">%s</table>' % "".join(trs))
    body.append('<form method="post" action="/admin/budgets">%s<input type="hidden" name="as_of" value="%s"><textarea name="data" style="display:none">%s</textarea>'
                "<p>P6 project %s <input type=\"submit\" value=\"Update preview\"> <small>(the same file can hold several P6 projects, such as a baseline and a later version: "
                "choose the one to load)</small></p>%s</form>" % (
                    hidden(u=u), esc(as_of), esc(data), sel("p6_project", [(p, p) for p in projects], chosen),
                    ('<p><input type="submit" name="confirm" value="Load %d budget%s from %s"></p>' % (len(to_load), "" if len(to_load) == 1 else "s", esc(chosen)))
                    if to_load else "<p>Nothing to load for this project.</p>"))
    return layout(con, u, "Admin", "".join(body))


def act_budgets(con, form):
    u = int(form["u"][0])
    if not can_use_tab(con, u, "budgets"):
        return view_admin(con, u, "budgets")
    try:
        as_of = need_date(form.get("as_of", [""])[0], "As of")
        data = form.get("data", [""])[0]
        lines = [x for x in data.replace("\r", "").split("\n") if x.strip()]
        if not lines:
            raise ValueError("Paste at least one line: CTR No, budget hours.")
        if p6_format(data):
            return p6_budgets(con, u, form, data, as_of)
        ctrs = {r["no"].upper(): r["phase_id"] for r in con.execute(
            "select ph.phase_id, s.scope_code || '-' || coalesce(ph.ctr_code, upper(ph.name)) as no from phase ph join scope s on s.scope_id=ph.scope_id "
            "join client c on c.client_id=s.client_id where c.is_internal=0")}
        loaded, same, unknown, bad = 0, 0, [], []
        for n, line in enumerate(lines, 1):
            cells = next(csv.reader([line], delimiter="\t" if "\t" in line else ","))
            cells = [c.strip() for c in cells]
            try:
                hours = float(cells[1])
                if hours < 0:
                    raise ValueError
            except (IndexError, ValueError):
                if n == 1:
                    continue  # a heading line
                bad.append("line %d" % n)
                continue
            pid = ctrs.get(cells[0].upper())
            if pid is None:
                unknown.append(cells[0])
                continue
            cur = con.execute("select hours, as_of from ctr_budget where phase_id=? order by as_of desc limit 1", (pid,)).fetchone()
            if cur and abs(cur["hours"] - hours) < 1e-9:
                same += 1
            elif cur and cur["as_of"] >= as_of:
                bad.append("%s (a budget as of %s is already held: use a later date)" % (cells[0], cur["as_of"]))
            else:
                con.execute("insert into ctr_budget (phase_id, hours, as_of, loaded_by) values (?,?,?,?)", (pid, hours, as_of, u))
                loaded += 1
        con.commit()
        msgs = ["%d budget%s loaded, as of %s. %d unchanged." % (loaded, "" if loaded == 1 else "s", as_of, same)]
        if unknown:
            msgs.append("Not recognised as a CTR No (nothing loaded for): %s." % ", ".join(unknown))
        if bad:
            msgs.append("Skipped: %s." % "; ".join(bad))
        return view_admin(con, u, "budgets", msgs, ok=not (unknown or bad))
    except ValueError as e:
        con.rollback()
        return view_admin(con, u, "budgets", [str(e)])


def act_period(con, form):
    u, ws, action = int(form["u"][0]), form["week_start"][0].strip(), form["action"][0]
    if not can_use_tab(con, u, "periods"):
        return view_admin(con, u, "periods")
    try:
        if action == "open":
            if con.execute("select 1 from period where week_start=?", (ws,)).fetchone():
                return view_admin(con, u, "periods", ["Week %s already exists, so there is nothing to open." % ws])
            con.execute("insert into period (week_start) values (?)", (ws,))
            msg = ["Week %s opened." % ws]
        elif action == "reopen":
            con.execute("update period set status='Open', closed_by=NULL, closed_at=NULL where week_start=?", (ws,))
            msg = ["Week %s reopened." % ws]
        else:
            con.execute("update period set status='Closed', closed_by=?, closed_at=? where week_start=?", (u, stamp(), ws))
            msg = ["Week %s closed." % ws]
        con.commit()
        return view_admin(con, u, "periods", msg, ok=True)
    except sqlite3.DatabaseError as e:
        return view_admin(con, u, "periods", ["%s" % e])


def view_audit(con, u, q=None):
    if permission_of(con, u) not in ("controller", "admin"):  # who owns the audit trail is still to be confirmed with the cost team
        return no_access(con, u, "Audit trail")
    try:
        person = int((q or {}).get("person", [""])[0])
    except ValueError:
        person = None
    rows = con.execute(
        "select a.audit_id, a.changed_at, ch.name as changed_by, a.action, bu.name as booked_by, s.scope_code, t.work_date, "
        "a.old_status, a.new_status, a.old_hours, a.new_hours, a.reason from time_entry_audit a "
        "join time_entry t on t.entry_id=a.entry_id join app_user bu on bu.user_id=t.user_id "
        "left join app_user ch on ch.user_id=a.changed_by join scope s on s.scope_id=t.scope_id "
        "where (? is null or t.user_id=?) order by a.audit_id desc limit 200", (person, person)).fetchall()
    heads = ["#", "When", "Changed by", "Action", "Booked by", "Scope", "Date", "Old status", "New status", "Old hours", "New hours", "Reason"]
    arows = con.execute(
        "select a.audit_id, a.changed_at, ch.name as changed_by, a.action, bu.name as person, at.name as allowance, a.work_date, a.old_status, a.new_status, a.reason "
        "from allowance_audit a left join app_user ch on ch.user_id=a.changed_by join app_user bu on bu.user_id=a.user_id "
        "join allowance_type at on at.allowance_id=a.allowance_id where (? is null or a.user_id=?) order by a.audit_id desc limit 200", (person, person)).fetchall()
    body = ('<form method="get" action="/audit">%s<p>Person whose hours changed %s <input type="submit" value="Filter"> '
            '<small>Showing the latest 200 changes%s.</small></p></form>' % (
                hidden(u=u), sel("person", opts_users(con), person, True, "Everyone"), " for this person" if person else "")) +         table(heads, [[fmt(v) if i in (9, 10) else fmt_time(v) if i == 1 else v for i, v in enumerate(r)] for r in rows])
    body += "<h4>Allowances</h4>" + table(["#", "When", "Changed by", "Action", "Booked by", "Allowance", "Date", "Old status", "New status", "Reason"],
                                           [[fmt_time(v) if i == 1 else v for i, v in enumerate(r)] for r in arows])
    return layout(con, u, "Audit trail (latest 200)", body)
