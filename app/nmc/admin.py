"""Admin tabs (periods, users, roles, clients, rates, scopes, internal codes, locations, allowance rates) and the audit trail."""
import csv
import datetime
import json
from urllib.parse import quote
from nmc import pg
from nmc.core import (
    date_input, dmy, edit_link, esc, fmt,
    fmt_time, hidden, layout, msgs_html,
    audit_user_added, audit_user_changes, need_choice, need_date, need_email, need_hours, need_text, no_access,
    locations, opts_assets, opts_clients, opts_projects, opts_roles, opts_scopes, people_picker,
    opts_users, permission_of, sel,
    stamp, table, txt)


# ---------- Admin ----------
ADMIN_TABS = [("periods", "Weekly periods"), ("users", "Users"), ("roles", "Job families / titles"),
              ("clients", "Clients"), ("assets", "Assets / POs"), ("paaf", "PAAFs"), ("rates", "Client rate cards"), ("scopes", "Projects / scopes / CTRs"),
              ("internal", "Internal codes"), ("locations", "Locations (hour types)"), ("allowance_rates", "Allowance rates")]


# Who may use each Admin tab. Cost controllers have every tab: they maintain the system day to day (people, job roles, locations, clients, rate cards,
# scopes, internal codes, allowance rates and periods). The IT admin keeps the tabs it always had (periods, users, job roles, locations).
TAB_ACCESS = {"periods": ("controller", "admin"), "users": ("controller", "admin"), "roles": ("controller", "admin"), "clients": ("controller",), "assets": ("controller",), "paaf": ("controller",),
              "rates": ("controller",), "scopes": ("controller",), "internal": ("controller",),
              "locations": ("controller", "admin"), "allowance_rates": ("controller",)}


PAYMENT_TERMS = [("Reimbursable", "Reimbursable"), ("Budget", "Budget"), ("Lump Sum", "Lump Sum"), ("Non-reimbursable", "Non-reimbursable")]


def payment_terms(g):
    """The payment terms and lump sum value from a CTR form: (terms, value). Only a Lump Sum CTR carries a value, which may be left empty until agreed."""
    terms = g("payment_terms") or "Reimbursable"
    if terms not in dict(PAYMENT_TERMS):
        raise ValueError("Payment terms must be Reimbursable, Budget, Lump Sum or Non-reimbursable.")
    if terms != "Lump Sum" or not g("lump_sum_value"):
        return terms, None
    try:
        value = float(g("lump_sum_value").replace(",", "").lstrip("£"))
    except ValueError:
        raise ValueError("The lump sum value must be a number.")
    if value < 0:
        raise ValueError("The lump sum value cannot be negative.")
    return terms, value


BOOKS_TIME_OPTIONS = [("1", "Yes"), ("0", "No (a director, for example: no timesheet, never chased)")]
EMPLOYMENT_OPTIONS = [("Staff", "Staff"), ("Contractor - LTD", "Contractor - LTD"), ("Contractor - PAYE", "Contractor - PAYE")]
PERMISSION_OPTIONS = [("user", "user"), ("controller", "controller (cost controller)"), ("admin", "admin (IT)")]


def tab_of(t):
    return {"projects": "scopes", "phases": "scopes"}.get(t, t)  # projects and phases are edited from the scopes tab


def can_use_tab(con, u, t):
    return permission_of(con, u) in TAB_ACCESS.get(tab_of(t), ())


def allowed_tabs(con, u):
    return [(k, v) for k, v in ADMIN_TABS if can_use_tab(con, u, k)]


def admin_body(con, u, t, q=None):
    b = []
    if t == "periods":
        rows = [list(r) for r in con.execute("select week_start, status, closed_by, closed_at from period order by week_start")]
        b.append(table(["Week start", "Status", "Closed by", "Closed at"], [r[:3] + [fmt_time(r[3])] for r in rows]))
        closes = "".join('<form method="post" action="/admin/period" style="display:inline">%s'
                         '<input type="submit" value="Close week %s"></form> ' % (hidden(u=u, week_start=r[0], action="close"), dmy(r[0]))
                         for r in rows if r[1] == "Open")
        b.append("<p>%s</p>" % closes)
        reopens = "".join('<form method="post" action="/admin/period" style="display:inline">%s'
                          '<input type="submit" value="Reopen week %s"></form> ' % (hidden(u=u, week_start=r[0], action="reopen"), dmy(r[0]))
                          for r in rows if r[1] == "Closed")
        if reopens:
            b.append("<p>%s</p>" % reopens)
        b.append("<p><small>Each week opens automatically when the week starts. Close a week here when it should stop taking new hours. A closed week can be reopened, for example so a reopened timesheet can be corrected.</small></p>")
        b.append('<form method="post" action="/admin/period">%s<b>Open a missed week</b> - Monday date %s '
                 '<input type="submit" value="Open week"></form>' % (hidden(u=u, action="open"), date_input("week_start")))
    elif t == "users":
        needle = ((q or {}).get("f_q", [""])[0]).strip()
        b.append('<form method="get" action="/admin">%s<p>Find a person (name, email or NMC ID) <input type="text" name="f_q" size="20" value="%s"> '
                 '<input type="submit" value="Search"> <input type="button" value="Clear" onclick="location.href=\'/admin?u=%d&amp;t=users\'"></p></form>' % (
                     hidden(u=u, t="users"), esc(needle), u))
        rows = [[r["nmc_id"], r["name"], r["email"] or "", r["employment_type"], r["skill_code"] + " " + r["role"], r["manager"] or "", r["approver"] or "", r["permission"], "Yes" if r["books_time"] else "No", dmy(r["start_date"]),
                 dmy(r["end_date"]), edit_link(u, "users", r["user_id"])]
                for r in con.execute("select u.*, r.skill_code, r.name as role, m.name as manager, ap.name as approver from app_user u join role r on r.role_id=u.role_id "
                                     "left join app_user m on m.user_id=u.line_manager_id left join app_user ap on ap.user_id=u.approver_id "
                                     "where (? = '' or u.name ilike '%' || ? || '%' or u.nmc_id ilike '%' || ? || '%' or u.email ilike '%' || ? || '%') order by u.nmc_id", (needle, needle, needle, needle))]
        b.append(table(["NMC ID", "Name", "Email", "Employment", "Role", "Line manager", "Approver", "Permission", "Writes time", "Start", "End", ""], rows, raw_cols=(11,)))
        b.append("<p><small>A person's timesheet is approved by their line manager. Name an Approver only when someone else makes more sense (a manager who works on a different client, say): "
                 "an empty Approver means the line manager.</small></p>")
        b.append('<h4>Add a user</h4><form method="post" action="/admin/add">%s<table border="0" cellpadding="2">'
                 "<tr><td>NMC ID</td><td>%s</td></tr><tr><td>Name</td><td>%s</td></tr><tr><td>Work email (the Microsoft account they sign in with)</td><td>%s</td></tr>"
                 "<tr><td>Employment</td><td>%s</td></tr><tr><td>Role</td><td>%s</td></tr><tr><td>Line manager</td><td>%s</td></tr>"
                 "<tr><td>Approver (only if not the line manager)</td><td>%s</td></tr>"
                 "<tr><td>Default location</td><td>%s</td></tr><tr><td>Hours per week</td><td>%s</td></tr>"
                 "<tr><td>Permission</td><td>%s</td></tr><tr><td>Writes time</td><td>%s</td></tr><tr><td>Start date</td><td>%s</td></tr>"
                 "<tr><td>End date (optional)</td><td>%s</td></tr></table><input type=\"submit\" value=\"Add user\"></form>"
                 % (hidden(u=u, t="users"), txt("nmc_id", "", 8), txt("name", "", 30), txt("email", "", 34),
                    sel("employment_type", EMPLOYMENT_OPTIONS), sel("role_id", opts_roles(con)),
                    sel("line_manager_id", opts_users(con), None, True, "None"), sel("approver_id", opts_users(con), None, True, "The line manager"),
                    sel("default_location_id", locations(con), "1"), txt("hours_per_week", "40", 5),
                    sel("permission", PERMISSION_OPTIONS), sel("books_time", BOOKS_TIME_OPTIONS), date_input("start_date", datetime.date.today().isoformat()),
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
        rows = [[r["name"], "Internal (not billed)" if r["is_internal"] else "Client", r["framework_ref"] or "", edit_link(u, "clients", r["client_id"])]
                for r in con.execute("select c.client_id, c.name, c.is_internal, c.framework_ref from client c order by c.is_internal, c.name")]
        b.append("<p><small>Hours are approved by each person's line manager (or the approver named on their record, set on the Users tab), not by client.</small></p>")
        b.append(table(["Client", "Type", "Framework agreement", ""], rows, raw_cols=(3,)))
        b.append('<h4>Add a client</h4><form method="post" action="/admin/add">%s Name %s <input type="submit" value="Add client"></form>'
                 % (hidden(u=u, t="clients"), txt("name", "", 30)))
    elif t == "rates":
        try:
            f_client = int((q or {}).get("f_client", [""])[0])
        except ValueError:
            f_client = None
        today = datetime.date.today().isoformat()
        b.append('<form method="get" action="/admin">%s<p>Client %s <input type="submit" value="Filter"> '
                 '<small>Current is the rate in force today; Superseded rates stay so past hours keep their price; Future starts later.</small></p></form>' % (
                     hidden(u=u, t="rates"), sel("f_client", opts_clients(con, True), f_client, True, "All clients")))
        rows = [[r["client"], r["skill_code"], r["role"], "%.2f" % r["sales_rate"], dmy(r["effective_from"]), r["state"]]
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
    elif t == "allowance_rates":
        today = datetime.date.today().isoformat()
        grid = []
        for al in con.execute("select allowance_id, name from allowance_type where active=1 order by allowance_id"):
            cur = con.execute("select amount from allowance_rate where allowance_id=? and effective_from<=? order by effective_from desc limit 1", (al["allowance_id"], today)).fetchone()
            grid.append('<tr><td>%s</td><td><input type="text" size="8" name="r_%d" value="%s"></td></tr>' % (esc(al["name"]), al["allowance_id"], fmt(cur[0] if cur else 0)))
        b.append("<p><small>What one night of the offshore allowance is worth. It is paid per night spent offshore, to staff only (contractors have it in their rate), and it is "
                 "worked out from the hours, so nobody claims it. Rates are never edited: change the figure here and it is saved as a new rate from the date you give, "
                 "so earlier nights keep the value they had.</small></p>")
        b.append('<form method="post" action="/admin/allowance_rates">%s<table border="1" cellpadding="3" cellspacing="0"><tr><th>Allowance</th><th>&pound; per night</th></tr>%s</table>'
                 '<p>New rate applies from %s <input type="submit" value="Save rate"></p></form>' % (hidden(u=u), "".join(grid), date_input("effective_from", today)))
        hist = [[r["name"], "%.2f" % r["amount"], dmy(r["effective_from"])]
                for r in con.execute("select at.name, ar.amount, ar.effective_from from allowance_rate ar join allowance_type at on at.allowance_id=ar.allowance_id "
                                     "order by at.allowance_id, ar.effective_from desc")]
        b.append("<h4>Every rate, newest first</h4>" + table(["Allowance", "Per night", "From"], hist))
    elif t == "assets":
        b.append("<p><small>A client's assets (a platform or a field). Each asset can hold one purchase order (PO) number, which is optional because operators invoice in "
                 "different ways. Pick an asset on a scope (Projects / scopes / CTRs tab); the manhour report then shows the asset and its PO.</small></p>")
        rows = [[r["client"], r["name"], r["po_number"] or "", str(r["n_scopes"]), edit_link(u, "assets", r["asset_id"])]
                for r in con.execute("select a.*, c.name as client, (select count(*) from scope s where s.asset_id=a.asset_id) as n_scopes "
                                     "from asset a join client c on c.client_id=a.client_id order by c.name, a.name")]
        b.append(table(["Client", "Asset", "PO number", "Scopes", ""], rows, raw_cols=(4,)) if rows else "<p>No assets yet.</p>")
        b.append('<h4>Add an asset</h4><form method="post" action="/admin/add">%s Client %s Asset %s PO number (optional) %s <input type="submit" value="Add asset"></form>'
                 % (hidden(u=u, t="assets"), sel("client_id", opts_clients(con, billable_only=True)), txt("name", "", 30), txt("po_number", "", 20)))
    elif t == "paaf":
        b.append("<p><small>The PAAF register: who may write time on which client project, and under which job title. A person sees only the CTRs of projects they hold a PAAF for, "
                 "and on each only the job titles on their PAAFs for that project. The title decides the rate: the hours are priced at the client's rate for that title (set on the "
                 "client rate card, so add the rate first). A person can hold several PAAFs on a project, one for each job title. Internal codes need no PAAF. "
                 "Hours can only be booked on days from the PAAF's start date to its end date (no end date means it has none set), so an expired PAAF cannot be booked on; to renew one, change its dates. "
                 "This is a register only: the signed PAAF documents are held elsewhere and commercial keeps this list up to date. The reference is where to find the document, not the document. "
                 "Removing a PAAF stops new bookings only; hours already booked stay.</small></p>")
        fp = ((q or {}).get("f_project", [""])[0]).strip()
        fq = ((q or {}).get("f_person", [""])[0]).strip()
        projects = [tuple(r) for r in con.execute("select p.project_id, c.name || ' - ' || p.name from project p join client c on c.client_id=p.client_id "
                                                  "where c.is_internal=0 order by c.name, p.name")]
        b.append('<form method="get" action="/admin">%s<p>Project %s Person %s <input type="submit" value="Show"></p></form>' % (
            hidden(u=u, t="paaf"), sel("f_project", projects, fp, True, "All projects"), txt("f_person", fq, 20)))
        where, args = "", []
        if fp.isdigit():
            where += " and pf.project_id=?"
            args.append(int(fp))
        if fq:
            where += " and us.name ilike ?"
            args.append("%" + fq + "%")
        found = con.execute("select pf.paaf_id, c.name as client, pr.name as project, ro.skill_code, ro.name as role, us.name as person, pf.reference, pf.start_date, pf.end_date "
                            "from paaf pf join project pr on pr.project_id=pf.project_id join client c on c.client_id=pr.client_id join role ro on ro.role_id=pf.role_id "
                            "join app_user us on us.user_id=pf.user_id where true" + where + " order by c.name, pr.name, us.name, ro.skill_code limit 501", args).fetchall()
        today = datetime.date.today().isoformat()

        def paaf_status(r):
            if r["start_date"] > today:
                return "Not started"
            return "Expired" if r["end_date"] and r["end_date"] < today else "Valid"
        rows = [[r["client"], r["project"], "%s %s" % (r["skill_code"], r["role"]), r["person"], dmy(r["start_date"]), dmy(r["end_date"]), paaf_status(r), r["reference"] or "",
                 edit_link(u, "paaf", r["paaf_id"])] for r in found[:500]]
        b.append(table(["Client", "Project", "Role", "Person", "From", "To", "Status", "Reference", ""], rows, raw_cols=(8,)) if rows else "<p>No PAAFs match.</p>")
        if len(found) > 500:
            b.append("<p><small>Showing the first 500. Pick a project or a person to narrow it down.</small></p>")
        b.append('<h4>Add PAAFs</h4><p><small>Choose a project and a job title, then add everyone who should hold that PAAF. The end date can be left empty. The reference is optional: where the signed PAAF is kept.</small></p>'
                 '<form method="post" action="/admin/add">%s<table border="0" cellpadding="2"><tr><td>Project</td><td>%s</td></tr>'
                 '<tr><td>Job title</td><td>%s</td></tr><tr><td>Start date</td><td>%s</td></tr><tr><td>End date</td><td>%s</td></tr>'
                 '<tr><td>Reference</td><td>%s</td></tr><tr><td>People</td><td>%s</td></tr></table>'
                 '<input type="submit" value="Add PAAFs"></form>' % (
                     hidden(u=u, t="paaf"), sel("project_id", projects), sel("role_id", opts_roles(con)), date_input("start_date", today), date_input("end_date", ""), txt("reference", "", 20),
                     people_picker("person", [tuple(r) for r in con.execute("select user_id, name from app_user where books_time=1 order by name")], set(), "Nobody chosen")))
    elif t == "locations":
        b.append("<p><small>Where hours are worked. People pick one on each timesheet row (it starts at their default location, set on their user record). "
                 "Rename a location or switch it off to stop new bookings to it; hours already booked keep it. At least one must stay active. "
                 "A location that <b>earns the allowance</b> (Offshore) pays the allowance for each night spent there: a night is earned when a member of staff has hours there that day and the next.</small></p>")
        rows = [[r["name"], "Yes" if r["active"] else "No", "Yes" if r["earns_allowance"] else "No", edit_link(u, "locations", r["location_id"])]
                for r in con.execute("select * from location order by location_id")]
        b.append(table(["Location", "Active", "Earns the allowance", ""], rows, raw_cols=(3,)))
        b.append('<h4>Add a location</h4><form method="post" action="/admin/add">%s Name %s <input type="submit" value="Add location"></form>'
                 % (hidden(u=u, t="locations"), txt("name", "", 24)))
    elif t == "internal":
        b.append("<p><small>Internal codes are for hours NMC pays for itself, such as Business Services or Training. People pick one in their "
                 "timesheet (shown as Internal - name; Sick and Compassionate are shown as they are). They have no rate, and they are kept out of the client reports. "
                 "Close a code to stop new bookings to it.</small></p>")
        rows = [[r["scope_code"], r["description"], r["status"], edit_link(u, "internal", r["scope_id"])]
                for r in con.execute("select s.scope_id, s.scope_code, s.description, s.status from scope s "
                                     "join client c on c.client_id=s.client_id where c.is_internal=1 order by s.scope_code")]
        b.append(table(["Code", "Name", "Status", ""], rows, raw_cols=(3,)))
        b.append('<h4>Add an internal code</h4><form method="post" action="/admin/add">%s<table border="0" cellpadding="2">'
                 "<tr><td>Name</td><td>%s</td></tr></table>"
                 '<input type="submit" value="Add internal code"></form>'
                 % (hidden(u=u, t="internal"), txt("description", "", 40)))
    elif t == "scopes":
        b.append("<p><small>Structure: client &gt; project &gt; scope &gt; CTR. A CTR is the booking code hours are booked to.</small></p>")
        b.append("<h4>Projects</h4>")
        rows = [[r["client"], r["project_code"], r["name"], r["status"], edit_link(u, "projects", r["project_id"])]
                for r in con.execute("select p.*, c.name as client from project p join client c on c.client_id=p.client_id order by c.name, p.name")]
        b.append(table(["Client", "Code", "Project", "Status", ""], rows, raw_cols=(4,)))
        b.append('<form method="post" action="/admin/add">%s Client %s Code %s Name %s <input type="submit" value="Add project"></form>'
                 % (hidden(u=u, t="projects"), sel("client_id", opts_clients(con)), txt("project_code", "", 10), txt("name", "", 30)))
        b.append("<h4>Scopes</h4>")
        rows = [[r["client"], r["project"], r["scope_code"], r["description"], r["asset"] or "", r["po_number"] or "", r["status"], edit_link(u, "scopes", r["scope_id"])]
                for r in con.execute("select s.scope_id, c.name as client, pr.name as project, s.scope_code, s.description, s.status, a.name as asset, a.po_number "
                                     "from scope s join client c on c.client_id=s.client_id join project pr on pr.project_id=s.project_id "
                                     "left join asset a on a.asset_id=s.asset_id order by c.name, pr.name, s.scope_code")]
        b.append(table(["Client", "Project", "Scope", "Description", "Asset", "PO", "Status", ""], rows, raw_cols=(7,)))
        b.append('<p><b>Add a scope</b> <small>A CTR called General is created automatically; add or rename CTRs below.</small></p>'
                 '<form method="post" action="/admin/add">%s<table border="0" cellpadding="2">'
                 "<tr><td>Project</td><td>%s</td></tr><tr><td>Scope code</td><td>%s</td></tr>"
                 "<tr><td>Description</td><td>%s</td></tr><tr><td>Asset (optional)</td><td>%s</td></tr><tr><td>Status</td><td>%s</td></tr></table>"
                 '<input type="submit" value="Add scope"></form>'
                 % (hidden(u=u, t="scopes"), sel("project_id", opts_projects(con)), txt("scope_code", "", 10), txt("description", "", 40),
                    sel("asset_id", opts_assets(con), None, True, "None"),
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
                controls.append("%s to %s" % (dmy(r["book_from"]) or "any date", dmy(r["book_to"]) or "any date"))
            rows.append([r["client"], r["scope_code"], r["scope_desc"], r["name"], r["description"] or "", r["status"],
                         "; ".join(controls) or "open to all", edit_link(u, "phases", r["phase_id"])])
        b.append(table(["Client", "Scope", "Scope description", "CTR (booking code)", "CTR description", "Status", "Who and when can book", ""], rows, raw_cols=(7,)))
        b.append('<p><a class="btn" href="/admin/edit?u=%d&amp;t=phases&amp;id=0">Add a CTR</a> <small>Opens the full CTR page: details, budget by role, who and when can book.</small></p>' % u)
    return "".join(b)


def view_admin(con, u, t, msgs=None, ok=False, q=None):
    tabs = allowed_tabs(con, u)
    if not tabs or (t in dict(ADMIN_TABS) and not can_use_tab(con, u, t)):
        return no_access(con, u, "Admin")
    t = t if t in dict(tabs) else tabs[0][0]
    links = "".join('<a href="/admin?u=%d&amp;t=%s"%s>%s</a>' % (u, k, ' class="cur"' if k == t else "", esc(v)) for k, v in tabs)
    body = '%s<p class="tabs">%s</p><h4>%s</h4>%s' % (msgs_html(msgs, ok), links, esc(dict(tabs)[t]), admin_body(con, u, t, q))
    return layout(con, u, "Admin", body)


def view_admin_edit(con, u, t, i, msgs=None, form=None):
    if not can_use_tab(con, u, t):
        return no_access(con, u, "Admin")
    back = '<p><a href="/admin?u=%d&amp;t=%s">&lt;&lt; Back</a></p>' % (u, "scopes" if t in ("projects", "phases") else t)
    if t == "users":
        r = con.execute("select * from app_user where user_id=?", (i,)).fetchone()
        if not r:
            return layout(con, u, "Admin", "<p>Not found.</p>")
        form = ("<tr><td>NMC ID</td><td>%s</td></tr><tr><td>Name</td><td>%s</td></tr><tr><td>Work email</td><td>%s</td></tr>"
                "<tr><td>Microsoft account</td><td>%s</td></tr><tr><td>Last signed in</td><td>%s</td></tr><tr><td>Employment</td><td>%s</td></tr>"
                "<tr><td>Role</td><td>%s</td></tr><tr><td>Line manager</td><td>%s</td></tr><tr><td>Approver (only if not the line manager)</td><td>%s</td></tr>"
                "<tr><td>Default location</td><td>%s</td></tr><tr><td>Hours per week</td><td>%s</td></tr>"
                "<tr><td>Permission</td><td>%s</td></tr><tr><td>Writes time</td><td>%s</td></tr><tr><td>Start date</td><td>%s</td></tr>"
                "<tr><td>End date</td><td>%s</td></tr>" % (
                    esc(r["nmc_id"]), txt("name", r["name"], 30), txt("email", r["email"] or "", 34),
                    "Linked" if r["entra_object_id"] else "Not linked yet (linked on first sign-in, or by the user import)", esc(dmy(r["last_login_at"]) or "Never"),
                    sel("employment_type", EMPLOYMENT_OPTIONS, r["employment_type"]),
                    sel("role_id", opts_roles(con, active_only=False), r["role_id"]),
                    sel("line_manager_id", [x for x in opts_users(con) if x[0] != i], r["line_manager_id"], True, "None"),
                    sel("approver_id", [x for x in opts_users(con) if x[0] != i], r["approver_id"], True, "The line manager"),
                    sel("default_location_id", locations(con, active_only=False), str(r["default_location_id"])), txt("hours_per_week", fmt(r["hours_per_week"]), 5),
                    sel("permission", PERMISSION_OPTIONS, r["permission"]), sel("books_time", BOOKS_TIME_OPTIONS, str(r["books_time"])), esc(dmy(r["start_date"])), date_input("end_date", r["end_date"] or "")))
        note = ("<p><small>Changing someone's role does not change their booked hours. Their timesheet is approved by their line manager; name an Approver only when "
                "someone else makes more sense. New timesheet rows start at their default location.</small></p>")
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
        form = ("<tr><td>Name</td><td>%s</td></tr><tr><td>Active</td><td>%s</td></tr><tr><td>Earns the allowance</td><td>%s</td></tr>" % (
            txt("name", r["name"], 24), sel("active", [("1", "Yes"), ("0", "No")], str(r["active"])), sel("earns_allowance", [("1", "Yes"), ("0", "No")], str(r["earns_allowance"]))))
        note = "<p><small>Switching a location off stops new bookings to it. Hours already booked keep it.</small></p>"
    elif t == "clients":
        r = con.execute("select c.* from client c where c.client_id=?", (i,)).fetchone()
        if not r:
            return layout(con, u, "Admin", "<p>Not found.</p>")
        form = ("<tr><td>Client</td><td>%s</td></tr><tr><td>Framework agreement ref</td><td>%s</td></tr>" % (
            esc(r["name"]), txt("framework_ref", r["framework_ref"] or "", 40)))
        note = "<p><small>The framework agreement reference is shown on each of this client's CTR sheets.</small></p>"
    elif t in ("scopes", "internal"):  # an internal code is a scope of the Internal client
        r = con.execute("select s.*, c.name as client from scope s join client c on c.client_id=s.client_id where scope_id=?", (i,)).fetchone()
        if not r:
            return layout(con, u, "Admin", "<p>Not found.</p>")
        asset_row = "" if t == "internal" else ("<tr><td>Asset</td><td>%s</td></tr>" % sel("asset_id", opts_assets(con, r["client_id"]), r["asset_id"], True, "None"))
        form = ("<tr><td>Client</td><td>%s</td></tr><tr><td>Scope code</td><td>%s</td></tr><tr><td>Description</td><td>%s</td></tr>%s"
                "<tr><td>Status</td><td>%s</td></tr>" % (
                    esc(r["client"]), esc(r["scope_code"]), txt("description", r["description"], 40), asset_row,
                    sel("status", [("Open", "Open"), ("Pipeline", "Pipeline"), ("Closed", "Closed")], r["status"])))
        note = "<p><small>Closing a scope stops new bookings. The asset (and its PO) comes from the client's assets, kept on the Assets / POs tab.</small></p>"
    elif t == "assets":
        r = con.execute("select a.*, c.name as client from asset a join client c on c.client_id=a.client_id where asset_id=?", (i,)).fetchone()
        if not r:
            return layout(con, u, "Admin", "<p>Not found.</p>")
        form = ("<tr><td>Client</td><td>%s</td></tr><tr><td>Asset</td><td>%s</td></tr><tr><td>PO number (optional)</td><td>%s</td></tr>"
                % (esc(r["client"]), txt("name", r["name"], 30), txt("po_number", r["po_number"] or "", 20)))
        note = "<p><small>One PO per asset. Leave it empty when the operator does not invoice against a PO.</small></p>"
    elif t == "paaf":
        r = con.execute("select pf.*, c.name as client, pr.name as project, ro.skill_code, ro.name as role, us.name as person from paaf pf join project pr on pr.project_id=pf.project_id "
                        "join client c on c.client_id=pr.client_id join role ro on ro.role_id=pf.role_id join app_user us on us.user_id=pf.user_id where pf.paaf_id=?", (i,)).fetchone()
        if not r:
            return layout(con, u, "Admin", "<p>Not found.</p>")
        form = ("<tr><td>Client</td><td>%s</td></tr><tr><td>Project</td><td>%s</td></tr><tr><td>Person</td><td>%s</td></tr><tr><td>Job title</td><td>%s %s</td></tr>"
                "<tr><td>Start date</td><td>%s</td></tr><tr><td>End date</td><td>%s <small>empty = no end date</small></td></tr>"
                "<tr><td>Reference</td><td>%s <small>where the signed PAAF is kept</small></td></tr>" % (
                    esc(r["client"]), esc(r["project"]), esc(r["person"]), esc(r["skill_code"]), esc(r["role"]),
                    date_input("start_date", r["start_date"]), date_input("end_date", r["end_date"] or ""), txt("reference", r["reference"] or "", 24)))
        note = ('<p><small>To renew a PAAF, change its dates. To change the person, project or job title, remove this PAAF and add a new one. Removing stops new bookings only; hours already booked stay.</small></p>'
                '<p><input type="submit" name="remove" value="Remove this PAAF" onclick="return confirm(\'Remove this PAAF? Hours already booked stay, but no new hours can be booked under it.\');"></p>')
    elif t == "projects":
        r = con.execute("select p.*, c.name as client from project p join client c on c.client_id=p.client_id where project_id=?", (i,)).fetchone()
        if not r:
            return layout(con, u, "Admin", "<p>Not found.</p>")
        form = ("<tr><td>Client</td><td>%s</td></tr><tr><td>Code</td><td>%s</td></tr><tr><td>Name</td><td>%s</td></tr><tr><td>Status</td><td>%s</td></tr>"
                % (esc(r["client"]), esc(r["project_code"]), txt("name", r["name"], 30), sel("status", [("Open", "Open"), ("Closed", "Closed")], r["status"])))
        note = "<p><small>Closing a project stops new bookings to all of its scopes.</small></p>"
    elif t == "phases":
        return ctr_page(con, u, i, msgs, form)
    else:
        return layout(con, u, "Admin", "<p>Not found.</p>")
    body = '%s%s<form method="post" action="/admin/save">%s<table border="0" cellpadding="2">%s</table>%s<input type="submit" value="Save changes"></form>' % (
        msgs_html(msgs), back, hidden(u=u, t=t, id=i), form, note)
    return layout(con, u, "Edit", body)


# ---------- The CTR page: create or edit a CTR, with its budget by role ----------
BUDGET_JS = r"""<script>
(function(){var box=document.querySelector('.bpick');if(!box)return;
var map=JSON.parse(box.getAttribute('data-roles')),client=box.getAttribute('data-client'),rows=box.querySelector('.brows'),tot=document.getElementById('btot');
function opts(s){var cur=s.value;s.textContent='';var o=document.createElement('option');o.value='';o.textContent='Pick a role';s.appendChild(o);
(map[client]||[]).forEach(function(p){var e=document.createElement('option');e.value=p[0];e.textContent=p[1];s.appendChild(e);});s.value=cur;}
function mk(){var d=document.createElement('div');d.className='brow';var s=document.createElement('select');s.name='b_role';
var h=document.createElement('input');h.type='text';h.name='b_hours';h.placeholder='hours';h.setAttribute('inputmode','decimal');
var x=document.createElement('button');x.type='button';x.className='chip';x.textContent='×';x.title='Remove this role';
d.appendChild(s);d.appendChild(h);d.appendChild(x);opts(s);rows.appendChild(d);}
function sum(){var t=0;rows.querySelectorAll('input[name=b_hours]').forEach(function(i){var v=parseFloat(i.value.replace(/,/g,''));if(v>0)t+=v;});tot.textContent=Math.round(t*100)/100;}
function spare(){var l=rows.lastElementChild;if(!l||l.querySelector('select').value)mk();}
rows.addEventListener('change',function(){spare();sum();});rows.addEventListener('input',sum);
rows.addEventListener('click',function(e){if(e.target.className==='chip'){var r=e.target.parentNode;r.parentNode.removeChild(r);spare();sum();}});
var sc=document.querySelector('select[name=scope_id]');
if(sc){sc.addEventListener('change',function(){client=sc.options[sc.selectedIndex].getAttribute('data-client');
rows.querySelectorAll('select').forEach(function(s){s.value='';opts(s);});sum();});}
spare();sum();})();
</script>"""

CTR_TEXT_FIELDS = (("objectives", "Objectives"), ("inputs", "Input requirements"), ("scope_of_work", "Scope of work"),
                   ("assumptions", "This CTR assumes"), ("exclusions", "This CTR excludes"))


def role_choices(con, client_id, keep=()):
    """The roles a client's CTR can be budgeted in: the roles on that client's rate card, so each client's own role names are used.
    A role already in the budget stays offered even if it has since been retired. Returns [(role_id, 'Name (code)')]."""
    rows = [(r["role_id"], "%s (%s)" % (r["name"], r["skill_code"])) for r in con.execute(
        "select distinct r.role_id, r.name, r.skill_code from rate rt join role r on r.role_id=rt.role_id where rt.client_id=? and r.active=1 order by r.name", (client_id,))]
    have = {x[0] for x in rows}
    for rid in keep:
        if rid not in have:
            r = con.execute("select name, skill_code from role where role_id=?", (rid,)).fetchone()
            if r:
                rows.append((rid, "%s (%s)" % (r["name"], r["skill_code"])))
    return rows


def latest_budget(con, phase_id):
    return con.execute("select budget_id, hours, as_of from ctr_budget where phase_id=? order by as_of desc limit 1", (phase_id,)).fetchone()


def budget_lines(con, budget_id):
    return {r["role_id"]: float(r["hours"]) for r in con.execute(
        "select br.role_id, br.hours from ctr_budget_role br join role r on r.role_id=br.role_id where br.budget_id=? order by r.name", (budget_id,))}


def parse_budget(form, allowed):
    """The budget by role from the posted rows (b_role, b_hours): {role_id: hours}. Empty rows are skipped."""
    roles, hours, lines = form.get("b_role", []), form.get("b_hours", []), {}
    for n, rid in enumerate(roles):
        raw = (hours[n] if n < len(hours) else "").strip().replace(",", "")
        if not rid and not raw:
            continue
        if not rid:
            raise ValueError("Pick a role for the budget hours you entered.")
        rid = int(rid)
        if rid not in allowed:
            raise ValueError("That role is not on this client's rate card.")
        if not raw:
            raise ValueError("Enter the budget hours for %s." % allowed[rid])
        try:
            h = round(float(raw), 2)
        except ValueError:
            raise ValueError("The budget hours for %s must be a number." % allowed[rid])
        if h <= 0:
            raise ValueError("The budget hours for %s must be more than 0 (remove the line to drop the role)." % allowed[rid])
        if rid in lines:
            raise ValueError("%s is listed twice: put its hours on one line." % allowed[rid])
        lines[rid] = h
    return lines


def ctr_client(con, scope_id):
    r = con.execute("select client_id from scope where scope_id=?", (scope_id,)).fetchone()
    return r["client_id"] if r else None


def ctr_page(con, u, i, msgs=None, form=None):
    """One page to create (i = 0) or edit a CTR, laid out in boxes: the CTR, commercial terms, budget by role, references, who and when can book, and the
    proposal text. Posted values are shown again when something is wrong."""
    creating = i == 0
    r = None
    if not creating:
        r = con.execute("select ph.*, s.scope_code, s.client_id from phase ph join scope s on s.scope_id=ph.scope_id where phase_id=?", (i,)).fetchone()
        if not r:
            return layout(con, u, "Admin", "<p>Not found.</p>")
    scopes = [tuple(x) for x in con.execute("select s.scope_id, c.name || ' - ' || s.scope_code || ' ' || s.description, s.client_id from scope s "
                                            "join client c on c.client_id=s.client_id order by c.name, s.scope_code")]

    def val(k, default=""):
        if form is not None:
            return form[k][0] if k in form else default
        if r is not None and k in r.keys() and r[k] is not None:
            return r[k]
        return default
    area = lambda k, rows=3: '<textarea name="%s" rows="%d">%s</textarea>' % (k, rows, esc(val(k)))
    fld = lambda label, box, note="": '<label>%s</label><div>%s%s</div>' % (esc(label), box, (' <small>%s</small>' % note) if note else "")
    scope_id = int(val("scope_id") or (r["scope_id"] if r else (scopes[0][0] if scopes else 0)) or 0)
    client_id = ctr_client(con, scope_id)
    if creating:
        scope_box = '<select name="scope_id">%s</select>' % "".join('<option value="%d" data-client="%d"%s>%s</option>' % (
            sid, cid, " selected" if sid == scope_id else "", esc(label)) for sid, label, cid in scopes)
        code_note = "discipline - phase letter - number, e.g. PM-D-001. The scope code goes in front."
    else:
        scope_box = esc(r["scope_code"])
        code_note = "discipline - phase letter - number, e.g. PM-D-001. The scope code goes in front: %s-&hellip;" % esc(r["scope_code"])
    disciplines = [(str(d[0]), d[1]) for d in con.execute("select discipline_id, name from discipline order by name")]
    lump_v = val("lump_sum_value")
    if form is None and lump_v != "":
        lump_v = "%g" % lump_v
    box_ctr = ("<h5>CTR</h5><div class=\"fld\">%s%s%s%s%s%s</div>" % (
        fld("Scope", scope_box), fld("CTR name", txt("name", val("name"), 24), "the booking code people pick on their timesheet"),
        fld("CTR title", txt("description", val("description"), 40)),
        fld("CTR code", txt("ctr_code", val("ctr_code"), 14), code_note),
        fld("Discipline", sel("discipline_id", disciplines, str(val("discipline_id")), True, "(from the CTR code)"), "shown as the stage on the cost reports"),
        fld("Status", sel("status", [("Open", "Open"), ("Closed", "Closed")], val("status", "Open")), "closing stops new bookings; hours already booked are not affected")))
    box_com = ("<h5>Commercial</h5><div class=\"fld\">%s%s%s%s%s%s</div>" % (
        fld("Payment terms", sel("payment_terms", PAYMENT_TERMS, val("payment_terms", "Reimbursable"))),
        fld("Lump sum value (\u00a3)", txt("lump_sum_value", lump_v, 12), "Lump Sum CTRs only: the price agreed with the client"),
        fld("Expenses", txt("expenses", val("expenses", "Reimbursable"), 20)), fld("Prepared by", txt("prepared_by", val("prepared_by"), 20)),
        fld("Checked by", txt("checked_by", val("checked_by"), 20)), fld("Basis of proposal", area("basis", 2))))
    # the budget by role
    cur = latest_budget(con, i) if not creating else None
    have = budget_lines(con, cur["budget_id"]) if cur else {}
    if form is not None:
        posted = [(rid, h) for rid, h in zip(form.get("b_role", []), form.get("b_hours", [])) if rid or h]
    else:
        posted = [(str(k), "%g" % v) for k, v in have.items()]
    keep = [int(rid) for rid, _ in posted if rid.isdigit()]
    roles_by_client = {str(cid): role_choices(con, cid, keep if cid == client_id else ()) for cid in {x[2] for x in scopes}}
    roles_by_client = {k: [[str(a), b] for a, b in v] for k, v in roles_by_client.items()}
    mine = roles_by_client.get(str(client_id), [])
    rows_html = "".join('<div class="brow"><select name="b_role"><option value="">Pick a role</option>%s</select><input type="text" name="b_hours" value="%s" placeholder="hours" inputmode="decimal">'
                        '<button type="button" class="chip" title="Remove this role">&times;</button></div>' % (
                            "".join('<option value="%s"%s>%s</option>' % (esc(a), " selected" if a == rid else "", esc(b)) for a, b in mine), esc(h)) for rid, h in posted + [("", "")])             # always a spare row at the end
    total = sum(float(h.replace(",", "") or 0) for _, h in posted if h.replace(",", "").replace(".", "", 1).isdigit())
    now = ("Current budget: <b>%g hrs</b> as of %s%s." % (cur["hours"], dmy(cur["as_of"]), "" if have else ", with no role breakdown (loaded as a total)")) if cur else "No budget yet."
    box_bud = ('<h5>Budget by role</h5><p class="bnote"><small>%s Roles come from this client\'s rate card. Pick a role and enter its hours; a new line appears each time. '
               'Changing the budget saves a new version with the date below; earlier versions are kept.</small></p>'
               '<div class="bpick" data-client="%s" data-roles="%s"><div class="brows">%s</div></div>'
               '<p class="btotal">Budget total <b id="btot">%g</b> hrs &nbsp; <span class="asof">as of %s</span></p>%s' % (
                   now, client_id or "", esc(json.dumps(roles_by_client)), rows_html, total,
                   date_input("budget_as_of", val("budget_as_of", datetime.date.today().isoformat())),
                   "" if mine else '<p class="bad"><small>This client has no rate card yet, so there are no roles to pick (Admin, Client rate cards).</small></p>'))
    box_ref = "<h5>References</h5><div class=\"fld\">%s%s%s</div>" % (
        fld("WBS", txt("wbs", val("wbs"), 20)), fld("CBS", txt("cbs", val("cbs"), 20)), fld("ABS", txt("abs_code", val("abs_code"), 20)))
    allowed = {x[0] for x in con.execute("select user_id from phase_person where phase_id=?", (i,))} if not creating else set()
    if form is not None:
        allowed = {int(x) for x in form.get("person", [])}
    people = people_picker("person", [tuple(x) for x in con.execute("select user_id, name from app_user order by name")], allowed, "Nobody listed")
    box_book = ("<h5>Who and when can book</h5><div class=\"fld\">%s%s%s%s</div>"
                "<p><small>By default anyone who may book to the client can book to this code. Choose \"Only the people ticked\" to limit it to a list "
                "(nobody can book if the list is empty), and set dates to limit which work dates can be booked. These controls stop new bookings only.</small></p>" % (
                    fld("Who can book", sel("restricted", [("0", "Anyone"), ("1", "Only the people listed below")], str(val("restricted", "0")))),
                    fld("People allowed", people), fld("Bookable from", date_input("book_from", val("book_from"))), fld("Bookable until", date_input("book_to", val("book_to")))))
    box_text = "<h5>Proposal</h5><div class=\"ctext\">%s</div>" % "".join("<div><label>%s</label>%s</div>" % (esc(label), area(k, 4)) for k, label in CTR_TEXT_FIELDS)
    back = '<p><a href="/admin?u=%d&amp;t=scopes">&lt;&lt; Back</a></p>' % u
    action, title = ("/admin/add", "New CTR") if creating else ("/admin/save", "CTR %s-%s" % (r["scope_code"], r["ctr_code"] or r["name"].upper()))
    save = '<input type="submit" value="%s">' % ("Create CTR" if creating else "Save changes")
    body = ('%s%s<form method="post" action="%s">%s<div class="ctrhead"><h4>%s</h4>%s</div><div class="ctrgrid">'
            '<div class="col"><div class="box">%s</div><div class="box">%s</div></div><div class="col"><div class="box">%s</div><div class="box">%s</div><div class="box">%s</div></div>'
            '<div class="box wide">%s</div></div><p>%s</p></form>%s' % (
                msgs_html(msgs), back, action, hidden(u=u, t="phases", id=i), esc(title), save, box_ctr, box_bud, box_com, box_ref, box_book, box_text, save, BUDGET_JS))
    return layout(con, u, "New CTR" if creating else "Edit", body)


def prepare_ctr(con, g, form, phase_id, scope_id):
    """Check a CTR form and return what is to be written, before anything is. Raises ValueError with a message the person can act on."""
    b_from, b_to = need_date(g("book_from"), "Bookable from", False), need_date(g("book_to"), "Bookable until", False)
    if b_from and b_to and b_to < b_from:
        raise ValueError("Bookable until cannot be before bookable from.")
    terms, lump = payment_terms(g)
    name = need_text(g("name"), "CTR name")
    client_id = ctr_client(con, scope_id)
    cur = latest_budget(con, phase_id) if phase_id else None
    have = budget_lines(con, cur["budget_id"]) if cur else {}
    lines = parse_budget(form, {rid: label for rid, label in role_choices(con, client_id, have.keys())})
    change = None
    if lines != have and (lines or have):
        as_of = need_date(g("budget_as_of") or datetime.date.today().isoformat(), "Budget as of")
        if cur and as_of <= cur["as_of"]:
            raise ValueError("A budget as of %s is already held for this CTR: set the budget date later than that to save the change (earlier versions are kept)." % dmy(cur["as_of"]))
        change = (as_of, lines)
    return {"name": name, "terms": terms, "lump": lump, "from": b_from, "to": b_to, "budget": change}


def write_ctr(con, u, g, form, i, v):
    con.execute("update phase set name=?, status=?, description=?, wbs=?, cbs=?, abs_code=?, restricted=?, book_from=?, book_to=?, "
                "ctr_code=?, discipline_id=coalesce(?, (select discipline_id from discipline where code=split_part(?, '-', 1))), objectives=?, inputs=?, scope_of_work=?, "
                "assumptions=?, exclusions=?, prepared_by=?, checked_by=?, expenses=?, payment_terms=?, lump_sum_value=?, basis=? where phase_id=?",
                (v["name"], g("status") or "Open", g("description") or None, g("wbs") or None, g("cbs") or None, g("abs_code") or None,
                 1 if g("restricted") == "1" else 0, v["from"], v["to"], g("ctr_code") or None, int(g("discipline_id")) if g("discipline_id") else None, g("ctr_code") or "",
                 g("objectives") or None, g("inputs") or None, g("scope_of_work") or None, g("assumptions") or None, g("exclusions") or None,
                 g("prepared_by") or None, g("checked_by") or None, g("expenses") or "Reimbursable", v["terms"], v["lump"], g("basis") or None, i))
    con.execute("delete from phase_person where phase_id=?", (i,))
    for person in form.get("person", []):
        con.execute("insert into phase_person (phase_id, user_id) values (?,?) on conflict do nothing", (i, int(person)))
    if v["budget"]:
        as_of, lines = v["budget"]
        bid = con.execute("insert into ctr_budget (phase_id, hours, as_of, loaded_by) values (?,?,?,?) returning budget_id",
                          (i, round(sum(lines.values()), 2), as_of, u)).fetchone()[0]
        for rid, h in lines.items():
            con.execute("insert into ctr_budget_role (budget_id, role_id, hours) values (?,?,?)", (bid, rid, h))


def apply_ctr(con, u, g, form, i):
    scope_id = con.execute("select scope_id from phase where phase_id=?", (i,)).fetchone()["scope_id"]
    write_ctr(con, u, g, form, i, prepare_ctr(con, g, form, i, scope_id))


def create_ctr(con, u, form, g):
    """Add a CTR from the full CTR page."""
    try:
        scope_id = int(g("scope_id") or 0)
        if not ctr_client(con, scope_id):
            raise ValueError("Choose a scope for the CTR.")
        v = prepare_ctr(con, g, form, None, scope_id)
        pid = con.execute("insert into phase (scope_id, name) values (?,?) returning phase_id", (scope_id, v["name"])).fetchone()[0]
        write_ctr(con, u, g, form, pid, v)
        con.commit()
    except ValueError as e:
        return ctr_page(con, u, 0, [str(e)], form)
    except pg.DatabaseError as e:
        return ctr_page(con, u, 0, ["That CTR already exists on this scope (the name must be unique)." if isinstance(e, pg.UniqueViolation) else pg.error_text(e)], form)
    return view_admin(con, u, "phases", ["CTR added."], ok=True)


def admin_add(con, form):
    u, t = int(form["u"][0]), form["t"][0]
    if not can_use_tab(con, u, t):
        return view_admin(con, u, t)
    g = lambda k: form.get(k, [""])[0].strip()
    try:
        if t == "users":
            cur = con.execute("insert into app_user (nmc_id, employment_type, name, role_id, permission, start_date, end_date, line_manager_id, email, books_time, approver_id, "
                        "default_location_id, hours_per_week) values (?,?,?,?,?,?,?,?,?,?,?,?,?) returning user_id",
                        (need_text(g("nmc_id"), "NMC ID"), need_choice(g("employment_type"), EMPLOYMENT_OPTIONS, "Employment"), need_text(g("name"), "Name"), int(g("role_id")),
                         g("permission"), need_date(g("start_date"), "Start date"), need_date(g("end_date"), "End date", False),
                         int(g("line_manager_id")) if g("line_manager_id") else None, need_email(g("email")), 0 if g("books_time") == "0" else 1,
                         int(g("approver_id")) if g("approver_id") else None, int(g("default_location_id") or 1), need_hours(g("hours_per_week"))))
            audit_user_added(con, u, cur.fetchone()[0])
            msg = "User added."
        elif t == "roles":
            con.execute("insert into role (skill_code, name, family) values (?,?,?)",
                        (need_text(g("skill_code"), "Skill code"), need_text(g("name"), "Job title"), need_text(g("family"), "Job family")))
            msg = "Role added."
            con.commit()
            return view_admin(con, u, t, [msg], ok=True, q={"f_family": [g("family")]})
        elif t == "clients":
            con.execute("insert into client (name) values (?)", (need_text(g("name"), "Client name"),))
            msg = "Client added."
        elif t == "assets":
            con.execute("insert into asset (client_id, name, po_number) values (?,?,?)",
                        (int(g("client_id")), need_text(g("name"), "Asset name"), g("po_number") or None))
            msg = "Asset added."
        elif t == "paaf":
            people = [int(x) for x in form.get("person", [])]
            if not people:
                raise ValueError("Choose at least one person.")
            start, end = need_date(g("start_date"), "Start date"), need_date(g("end_date"), "End date", False)
            if end and end < start:
                raise ValueError("The end date cannot be before the start date.")
            made = 0
            for who in people:
                if con.execute("select 1 from paaf where project_id=? and role_id=? and user_id=?", (int(g("project_id")), int(g("role_id")), who)).fetchone():
                    continue                                                                            # already held
                con.execute("insert into paaf (project_id, role_id, user_id, reference, start_date, end_date) values (?,?,?,?,?,?)",
                            (int(g("project_id")), int(g("role_id")), who, g("reference") or None, start, end))
                made += 1
            msg = "%d PAAF%s added%s." % (made, "" if made == 1 else "s", "" if made == len(people) else " (%d already held)" % (len(people) - made))
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
            cur = con.execute("insert into scope (scope_code, client_id, project_id, description, status, asset_id) values (?,?,?,?,?,?) returning scope_id",
                              (need_text(g("scope_code"), "Scope code"), pr[0], int(g("project_id")), need_text(g("description"), "Description"), g("status"),
                               int(g("asset_id")) if g("asset_id") else None))
            con.execute("insert into phase (scope_id, name) values (?, 'General')", (cur.fetchone()[0],))
            msg = "Scope added with a General CTR."
        elif t == "phases":
            return create_ctr(con, u, form, g)
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
                            "where scope_code like 'INT%' and substr(scope_code, 4) ~ '^[0-9]+$'").fetchone()[0]
            code = "INT%02d" % n
            cur = con.execute("insert into scope (scope_code, client_id, project_id, description, status) values (?,?,?,?, 'Open') returning scope_id",
                              (code, pr["client_id"], pr["project_id"], need_text(g("description"), "Name")))
            con.execute("insert into phase (scope_id, name) values (?, 'General')", (cur.fetchone()[0],))
            msg = "Internal code %s added." % code
        else:
            raise ValueError("Unknown form.")
        con.commit()
        return view_admin(con, u, t, [msg], ok=True)
    except ValueError as e:
        return view_admin(con, u, t, [str(e)])
    except pg.IntegrityError as e:
        text = "That already exists (it must be unique)." if isinstance(e, pg.UniqueViolation) else pg.error_text(e)
        return view_admin(con, u, t, [text])
    except pg.DatabaseError as e:
        return view_admin(con, u, t, [pg.error_text(e)])


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
            email = need_email(g("email"))
            before = con.execute("select * from app_user where user_id=?", (i,)).fetchone()
            if i == u and g("permission") != before["permission"]:
                raise ValueError("You cannot change your own permission level. Ask another cost controller or the IT admin.")
            approver = int(g("approver_id")) if g("approver_id") else None
            if approver == i:
                raise ValueError("A person cannot approve their own timesheet.")
            con.execute("update app_user set name=?, employment_type=?, role_id=?, permission=?, end_date=?, line_manager_id=?, email=?, books_time=?, "
                        "approver_id=?, default_location_id=?, hours_per_week=?, "
                        "entra_object_id=case when email is distinct from ? then null else entra_object_id end where user_id=?",
                        (need_text(g("name"), "Name"), need_choice(g("employment_type"), EMPLOYMENT_OPTIONS, "Employment"), int(g("role_id")), g("permission"),
                         need_date(g("end_date"), "End date", False), manager, email, 0 if g("books_time") == "0" else 1,
                         approver, int(g("default_location_id") or 1), need_hours(g("hours_per_week")), email, i))
            audit_user_changes(con, u, i, before)
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
            con.execute("update location set name=?, active=?, earns_allowance=? where location_id=?",
                        (need_text(g("name"), "Location name"), int(g("active")), 1 if g("earns_allowance") == "1" else 0, i))
        elif t == "clients":
            con.execute("update client set framework_ref=? where client_id=?", (g("framework_ref") or None, i))
        elif t == "paaf":
            if form.get("remove"):
                con.execute("delete from paaf where paaf_id=?", (i,))
                con.commit()
                return view_admin(con, u, t, ["PAAF removed."], ok=True)
            start, end = need_date(g("start_date"), "Start date"), need_date(g("end_date"), "End date", False)
            if end and end < start:
                raise ValueError("The end date cannot be before the start date.")
            con.execute("update paaf set reference=?, start_date=?, end_date=? where paaf_id=?", (g("reference") or None, start, end, i))
        elif t == "assets":
            con.execute("update asset set name=?, po_number=? where asset_id=?", (need_text(g("name"), "Asset name"), g("po_number") or None, i))
        elif t in ("scopes", "internal"):
            con.execute("update scope set description=?, status=? where scope_id=?", (need_text(g("description"), "Description"), g("status"), i))
            if t == "scopes":
                con.execute("update scope set asset_id=? where scope_id=?", (int(g("asset_id")) if g("asset_id") else None, i))
        elif t == "projects":
            con.execute("update project set name=?, status=? where project_id=?", (need_text(g("name"), "Project name"), g("status"), i))
        elif t == "phases":
            apply_ctr(con, u, g, form, i)
        else:
            raise ValueError("Unknown form.")
        con.commit()
        return view_admin(con, u, "scopes" if t in ("projects", "phases") else t, ["Saved."], ok=True)
    except ValueError as e:
        return view_admin_edit(con, u, t, i, [str(e)], form)
    except pg.DatabaseError as e:
        text = "That already exists (it must be unique)." if isinstance(e, pg.UniqueViolation) else pg.error_text(e)
        return view_admin_edit(con, u, t, i, [text], form)


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
    except pg.IntegrityError:
        con.rollback()
        return view_admin(con, u, "allowance_rates", ["A rate already exists from that date. Rates are never edited: choose a later date."])


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
    except pg.DatabaseError as e:
        return view_admin(con, u, "periods", [pg.error_text(e)])


def nowrap(v):
    return '<span style="white-space:nowrap">%s</span>' % esc(v if v is not None else "")


AUDIT_PAGE = 50      # rows on one page of the audit trail


def pager(href, page, pages, total, size):
    """Page numbers under a table: Showing 51-100 of 640, then Previous, 1 ... 4 5 6 ... 13, Next. href(n) is the address of page n."""
    if total == 0:
        return ""
    shown = sorted({1, pages} | {n for n in range(page - 2, page + 3) if 1 <= n <= pages})
    out, last = [], 0
    for n in shown:
        if n - last > 1:
            out.append("&hellip;")
        out.append("<b>%d</b>" % n if n == page else '<a href="%s">%d</a>' % (href(n), n))
        last = n
    first_row, last_row = (page - 1) * size + 1, min(page * size, total)
    return ('<p class="pager"><small>Showing %d&ndash;%d of %d</small>%s</p>' % (
        first_row, last_row, total,
        (" &nbsp; " + ('<a href="%s">&lsaquo; Previous</a> ' % href(page - 1) if page > 1 else "") + " ".join(out) +
         (' <a href="%s">Next &rsaquo;</a>' % href(page + 1) if page < pages else "")) if pages > 1 else ""))


def page_number(q, key, pages):
    try:
        n = int((q or {}).get(key, ["1"])[0])
    except ValueError:
        n = 1
    return max(1, min(n, pages))


def view_audit(con, u, q=None):
    """Who changed what: the hours (every create, edit, status change and deletion) and changes to people, each newest first, 50 to a page."""
    if permission_of(con, u) not in ("controller", "admin"):  # who owns the audit trail is still to be confirmed with the cost team
        return no_access(con, u, "Audit trail")
    q = q or {}
    try:
        person = int(q.get("person", [""])[0])
    except ValueError:
        person = None
    n_hours = con.execute("select count(*) from time_entry_audit a join time_entry t on t.entry_id=a.entry_id where (? is null or t.user_id=?)", (person, person)).fetchone()[0]
    n_people = con.execute("select count(*) from user_audit a where (? is null or a.user_id=?)", (person, person)).fetchone()[0]
    pages_h, pages_p = max(1, -(-n_hours // AUDIT_PAGE)), max(1, -(-n_people // AUDIT_PAGE))
    page_h, page_p = page_number(q, "page", pages_h), page_number(q, "cpage", pages_p)

    link_h = lambda n: "/audit?u=%d%s&amp;page=%d&amp;cpage=%d" % (u, "&amp;person=%d" % person if person else "", n, page_p)
    link_p = lambda n: "/audit?u=%d%s&amp;page=%d&amp;cpage=%d" % (u, "&amp;person=%d" % person if person else "", page_h, n)

    rows = con.execute(
        "select a.audit_id, a.changed_at, ch.name as changed_by, a.action, bu.name as booked_by, s.scope_code, t.work_date, "
        "a.old_status, a.new_status, a.old_hours, a.new_hours, a.reason from time_entry_audit a "
        "join time_entry t on t.entry_id=a.entry_id join app_user bu on bu.user_id=t.user_id "
        "left join app_user ch on ch.user_id=a.changed_by join scope s on s.scope_id=t.scope_id "
        "where (? is null or t.user_id=?) order by a.audit_id desc limit ? offset ?", (person, person, AUDIT_PAGE, (page_h - 1) * AUDIT_PAGE)).fetchall()
    heads = ["#", "When", "Changed by", "Action", "Booked by", "Scope", "Date", "Old status", "New status", "Old hours", "New hours", "Reason"]
    body = ('<form method="get" action="/audit">%s<p>Person whose hours changed %s <input type="submit" value="Filter"> '
            '<small>Newest first, %d to a page%s.</small></p></form>' % (
                hidden(u=u), sel("person", opts_users(con), person, True, "Everyone"), AUDIT_PAGE, " &middot; this person only" if person else ""))
    body += "<h4>Changes to hours</h4>"
    if rows:
        body += '<div class="scrollbox">%s</div>' % table(heads, [[fmt(v) if i in (9, 10) else nowrap(fmt_time(v)) if i == 1 else nowrap(dmy(v)) if i == 6 else str(v).capitalize() if i == 3 else v
                                                                   for i, v in enumerate(r)] for r in rows], raw_cols=(1, 6))
        body += pager(link_h, page_h, pages_h, n_hours, AUDIT_PAGE)
    else:
        body += "<p>No changes to hours yet.</p>"
    changes = con.execute(
        "select a.changed_at, coalesce(ch.name, 'System') as by_whom, p.name as person, a.action, a.field, a.old_value, a.new_value from user_audit a "
        "join app_user p on p.user_id=a.user_id left join app_user ch on ch.user_id=a.changed_by "
        "where (? is null or a.user_id=?) order by a.audit_id desc limit ? offset ?", (person, person, AUDIT_PAGE, (page_p - 1) * AUDIT_PAGE)).fetchall()
    body += "<h4>Changes to people</h4>"
    if changes:
        body += '<div class="scrollbox">%s</div>' % table(["When", "Changed by", "Person", "What", "Old", "New"],
                      [[nowrap(fmt_time(r["changed_at"])), r["by_whom"], r["person"], "Added" if r["action"] == "ADD" else r["field"], r["old_value"] or "", r["new_value"] or ""] for r in changes],
                      raw_cols=(0,))
        body += pager(link_p, page_p, pages_p, n_people, AUDIT_PAGE)
        body += "<p><small>Who was added, and each change to a person's email, permission, role, dates, line manager, whether they write time, or their linked Microsoft account.</small></p>"
    else:
        body += "<p>No changes to people yet.</p>"
    return layout(con, u, "Audit trail", body)
