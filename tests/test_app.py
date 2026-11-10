import sqlite3
"""Front-end smoke tests. Run from the repo root: python -m unittest discover -s tests -v"""
import datetime
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "app"))
from build import build  # noqa: E402
import app  # noqa: E402


class TestApp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        app.DB_PATH = os.path.join(self.tmp, "t.db")
        build(app.DB_PATH).close()
        self.server = app.make_server(0)
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def get(self, path):
        with urllib.request.urlopen(self.base + path) as r:
            return r.read().decode("utf-8")

    def post(self, path, data):
        body = urllib.parse.urlencode(data, doseq=True).encode()
        with urllib.request.urlopen(self.base + path, body) as r:
            return r.read().decode("utf-8")

    def test_every_page_loads(self):
        for path in ["/", "/timesheet?u=5&w=2026-11-02", "/approvals?u=7", "/reports?u=5",
                     "/admin?u=7&t=users", "/admin?u=7&t=periods", "/audit?u=7"]:
            self.assertIn("NMC Timewriting", self.get(path), path)

    def test_brand_font_is_served_and_used(self):
        with urllib.request.urlopen(self.base + "/static/TASAOrbiterVF.woff2") as r:
            self.assertEqual(r.headers["Content-Type"], "font/woff2")
            self.assertEqual(r.read(4), b"wOF2")
        page = self.get("/reports?u=5")
        self.assertIn('font-family:"TASA Orbiter"', page)
        self.assertIn("/static/TASAOrbiterVF.woff2", page)

    def test_only_the_listed_static_file_is_served(self):
        for path in ["/static/app.py", "/static/../app.py", "/static/%2e%2e/app.py", "/static/TASAOrbiterVF.woff2/x"]:
            with self.assertRaises(urllib.error.HTTPError, msg=path) as cm:
                urllib.request.urlopen(self.base + path)
            self.assertEqual(cm.exception.code, 404, path)
            cm.exception.close()

    def test_styling_has_square_corners(self):
        self.assertNotIn("border-radius", self.get("/reports?u=5"))

    def test_timesheet_shows_cover_rows_and_totals(self):
        page = self.get("/timesheet?u=5&w=2026-11-02")
        self.assertIn("PM-04", page)
        self.assertIn("PM-02", page)
        self.assertIn("Week commencing 02/11/2026", page)

    def test_save_draft_then_submit_then_approve(self):
        # Ewan Shaw (5) books Friday 6 Nov on BB0001 (scope 1) as Planner (role 4)
        page = self.post("/timesheet/save", {"u": "5", "w": "2026-11-02", "action": "Save draft",
                                             "h_1_4_2026-11-06": "7.5", "reason": ""})
        self.assertIn("1 entry saved", page)
        page = self.post("/timesheet/save", {"u": "5", "w": "2026-11-02", "action": "Submit week", "reason": ""})
        self.assertIn("submitted for approval", page)
        con = app.db()
        eid = con.execute("select entry_id from time_entry where user_id=5 and work_date='2026-11-06'").fetchone()[0]
        self.assertEqual(con.execute("select status from time_entry where entry_id=?", (eid,)).fetchone()[0], "Submitted")
        con.close()
        # Alisdair Woodhead (7) is the approver of scope 1 (Apache)
        page = self.post("/approvals/act", {"u": "7", "entry_id": str(eid), "action": "Approve"})
        self.assertIn("approved", page)
        con = app.db()
        self.assertEqual(con.execute("select status, approved_by from time_entry where entry_id=?", (eid,)).fetchone()[:], ("Approved", 7))
        con.close()

    def test_wrong_person_cannot_approve_and_owner_cannot_self_approve(self):
        con = app.db()
        eid = con.execute("select entry_id from time_entry where status='Submitted' and scope_id=1 limit 1").fetchone()[0]
        owner = con.execute("select user_id from time_entry where entry_id=?", (eid,)).fetchone()[0]
        con.close()
        # neither a stranger nor the owner is an approver of this scope, so neither can approve it
        self.assertIn("only the approver named", self.post("/approvals/act", {"u": "1", "entry_id": str(eid), "action": "Approve"}))  # a cost controller
        self.assertIn("not named as an approver", self.post("/approvals/act", {"u": str(owner), "entry_id": str(eid), "action": "Approve"}))
        self.assertEqual(self.one("select status from time_entry where entry_id=?", eid)[0], "Submitted")

    def test_changing_approved_hours_needs_reason_then_becomes_edited(self):
        con = app.db()
        row = con.execute("select scope_id, worked_role_id, work_date, entry_id from time_entry "
                          "where user_id=5 and status='Approved' and work_date='2026-11-03'").fetchone()
        con.close()
        key = "h_%d_%d_%s" % (row[0], row[1], row[2])
        page = self.post("/timesheet/save", {"u": "5", "w": "2026-11-02", "action": "Save draft", key: "6", "reason": ""})
        self.assertIn("reason is needed", page)
        page = self.post("/timesheet/save", {"u": "5", "w": "2026-11-02", "action": "Save draft", key: "6", "reason": "Site log"})
        self.assertIn("1 entry saved", page)
        con = app.db()
        self.assertEqual(con.execute("select status, hours, edit_count from time_entry where entry_id=?", (row[3],)).fetchone()[:], ("Edited", 6, 1))
        con.close()

    def test_closed_week_blocks_new_hours_and_unapproved_paaf_blocks_booking(self):
        page = self.post("/timesheet/save", {"u": "5", "w": "2026-10-26", "action": "Save draft",
                                             "h_1_4_2026-10-30": "8", "reason": ""})
        self.assertIn("period is not open", page)
        page = self.post("/timesheet/save", {"u": "4", "w": "2026-11-02", "action": "Save draft",
                                             "h_1_4_2026-11-06": "8", "reason": ""})
        self.assertIn("No approved PAAF for this user and client", page)

    def test_clear_draft_entry_but_not_approved_or_submitted(self):
        self.post("/timesheet/save", {"u": "5", "w": "2026-11-02", "action": "Save draft",
                                      "h_1_4_2026-11-06": "7", "reason": ""})
        page = self.post("/timesheet/save", {"u": "5", "w": "2026-11-02", "action": "Save draft",
                                             "h_1_4_2026-11-06": "", "reason": ""})
        self.assertIn("1 draft entry cleared", page)
        con = app.db()
        self.assertEqual(con.execute("select count(*) from time_entry where user_id=5 and work_date='2026-11-06'").fetchone()[0], 0)
        self.assertEqual(con.execute("select count(*) from time_entry_audit where action='DELETE'").fetchone()[0], 1)
        con.close()
        # approved entry (Ewan Shaw, Tue 3 Nov) cannot be cleared
        page = self.post("/timesheet/save", {"u": "5", "w": "2026-11-02", "action": "Save draft",
                                             "h_1_4_2026-11-03": "", "reason": ""})
        self.assertIn("approved hours cannot be cleared", page)

    def test_admin_only_pages_and_period_actions(self):
        self.assertIn("not available to your role", self.get("/admin?u=5"))
        # a far-future Monday, so it is never the current week (the current week opens by itself)
        page = self.post("/admin/period", {"u": "29", "action": "open", "week_start": "2030-01-07"})
        self.assertIn("opened", page)
        page = self.post("/admin/period", {"u": "29", "action": "open", "week_start": "2030-01-07"})  # already there
        self.assertIn("already exists", page)
        page = self.post("/admin/period", {"u": "29", "action": "open", "week_start": "2030-01-08"})  # not a Monday
        self.assertNotIn("opened", page)

    def test_reports_date_range(self):
        page = self.get("/reports?u=5&from=2026-10-26&to=2026-10-26")
        self.assertIn("2026-10-26", page)
        self.assertNotIn("2026-11-02</td>", page.split("Period reports")[1])

    def period_part(self, path):
        return self.get(path).split("Period reports")[1]

    def test_period_report_filters_by_client_project_scope_and_phase(self):
        con = app.db()
        spirit = con.execute("select client_id from client where name='Spirit'").fetchone()[0]
        apache = con.execute("select client_id from client where name='Apache'").fetchone()[0]
        # a Spirit phase that has approved hours, so there are rows to show
        ph = con.execute("select ph.phase_id, ph.name, ph.scope_id from phase ph join scope s on s.scope_id=ph.scope_id "
                         "where s.client_id=? and exists (select 1 from time_entry t where t.phase_id=ph.phase_id and t.status='Approved') "
                         "order by ph.phase_id limit 1", (spirit,)).fetchone()
        sp = con.execute("select scope_id, scope_code, project_id from scope where scope_id=?", (ph["scope_id"],)).fetchone()
        ap_code = con.execute("select scope_code from scope where client_id=? limit 1", (apache,)).fetchone()[0]
        con.close()
        base = "/reports?u=29&from=2026-01-01&to=2026-12-31"
        everything = self.period_part(base)
        self.assertIn(">Spirit</td>", everything)
        self.assertIn(">Apache</td>", everything)
        only_spirit = self.period_part(base + "&client=%d" % spirit)
        self.assertIn(">Spirit</td>", only_spirit)
        self.assertNotIn(">Apache</td>", only_spirit)
        self.assertIn("Filtered to", only_spirit)
        self.assertNotIn("Filtered to", everything)
        # narrowing further keeps only that scope
        one_scope = self.period_part(base + "&client=%d&project=%d&scope=%d" % (spirit, sp["project_id"], sp["scope_id"]))
        self.assertNotIn(">%s</td>" % ap_code, one_scope)
        one_phase = self.period_part(base + "&client=%d&scope=%d&phase=%d" % (spirit, sp["scope_id"], ph["phase_id"]))
        self.assertIn(">%s</td>" % ph["name"], one_phase)

    def test_period_report_has_a_reset_button_back_to_the_unfiltered_page(self):
        page = self.get("/reports?u=5&client=2")
        self.assertIn("value=\"Reset\" onclick=\"location.href='/reports?u=5'\"", page)

    def test_period_report_drops_a_filter_that_does_not_fit_the_client(self):
        con = app.db()
        spirit = con.execute("select client_id from client where name='Spirit'").fetchone()[0]
        apache_project = con.execute("select project_id from project where client_id<>? limit 1", (spirit,)).fetchone()[0]
        con.close()
        base = "/reports?u=29&from=2026-01-01&to=2026-12-31"
        page = self.period_part(base + "&client=%d&project=%d" % (spirit, apache_project))
        self.assertIn(">Spirit</td>", page)  # the stray project is ignored, so Spirit still shows
        self.assertEqual(self.period_part(base + "&client=abc&scope=999"), self.period_part(base))


    # ---------- internal hours, automatic periods, live totals ----------
    def test_internal_hours_booked_without_a_paaf_and_reported_separately(self):
        # David Hart (4) has no approved PAAF for any client, but can still book to INT01 Business Services (phase 8)
        page = self.get("/timesheet?u=4&w=2026-11-02")
        self.assertIn("Internal - Business Services", page)
        self.assertNotIn("Apache &gt;", page)
        page = self.post("/timesheet/save", {"u": "4", "w": "2026-11-02", "action": "Save draft", "h_8_4_2026-11-04": "3.5", "reason": ""})
        self.assertIn("1 entry saved", page)
        self.assertEqual(tuple(self.one("select paaf_id, rate_id, status from time_entry where user_id=4 and work_date='2026-11-04'")),
                         (None, None, "Draft"))
        self.post("/timesheet/save", {"u": "4", "w": "2026-11-02", "action": "Submit week", "reason": ""})
        eid = self.one("select entry_id from time_entry where user_id=4 and work_date='2026-11-04'")[0]
        self.assertIn("approved", self.post("/approvals/act", {"u": "7", "entry_id": str(eid), "action": "Approve"}))
        page = self.get("/reports?u=7&from=2026-01-01&to=2026-12-31")
        tree, rest = page.split("Period reports")
        internal = rest.split("Internal hours")[1]
        self.assertIn("INT01 Business Services", internal)
        self.assertIn("David Hart", internal)
        self.assertIn('<td align="right">3.5</td>', internal)
        self.assertNotIn("INT01", tree)  # not in the CTR tree
        self.assertNotIn("Business Services", rest.split("Internal hours")[0])  # nor the period report
        self.assertNotIn(">Internal</option>", page)  # nor the client filter

    def test_admin_adds_and_edits_internal_codes(self):
        page = self.admin_post("/admin/add", {"t": "internal", "description": "IT Support", "approver_user_id": "7"})
        self.assertIn("Internal code INT04 added", page)
        self.assertIn("Internal - IT Support", self.get("/timesheet?u=4&w=2026-11-02"))
        sid = self.one("select scope_id from scope where scope_code='INT04'")[0]
        self.assertEqual(self.one("select count(*) from phase where scope_id=?", sid)[0], 1)  # a General phase came with it
        self.admin_post("/admin/save", {"t": "internal", "id": str(sid), "description": "IT Services", "status": "Closed", "approver_user_id": "7"})
        page = self.get("/timesheet?u=4&w=2026-11-02")
        self.assertNotIn("IT Services", page)  # closed codes stop taking bookings
        self.assertNotIn("IT Support", page)
        self.assertIn("IT Services", self.get("/admin?u=29&t=internal"))
        self.assertIn("IT Services", self.get("/admin/edit?u=29&t=internal&id=%d" % sid))
        self.assertIn("Internal (not billed)", self.get("/admin?u=29&t=clients"))
        for t in ("rates", "paafs"):  # the internal client is not offered for rate cards or PAAFs
            self.assertNotIn(">Internal</option>", self.get("/admin?u=29&t=" + t))
        before = self.one("select count(*) from scope")[0]
        for who in ("5", self.IT_ADMIN):  # a standard user, and the IT admin, cannot add codes
            self.admin_post("/admin/add", {"t": "internal", "description": "Sneaky", "approver_user_id": "7"}, u=who)
        self.assertEqual(self.one("select count(*) from scope")[0], before)
        # approvers on a code are standard users only: the controller and admin are not offered
        page = self.get("/admin?u=29&t=internal")
        for name in ("Emma Noble", "Abbi Murray", "Stephanie Apedaile", "NMC Admin"):  # the cost controllers and the admin
            self.assertNotIn(">%s</option>" % name, page.split("Add an internal code")[1], name)
        self.assertIn("Only a standard user", self.admin_post("/admin/add", {"t": "internal", "description": "Bad", "approver_user_id": "29"}))

    def test_current_week_opens_automatically_and_closed_weeks_stay_closed(self):
        week = app.monday(datetime.date.today()).isoformat()
        con = app.db()
        con.execute("delete from period where week_start=?", (week,))
        con.commit()
        con.close()
        self.get("/")
        self.assertEqual(self.one("select status from period where week_start=?", week)[0], "Open")
        self.admin_post("/admin/period", {"action": "close", "week_start": week})
        self.get("/")
        self.assertEqual(self.one("select status from period where week_start=?", week)[0], "Closed")  # not reopened

    def test_timesheet_has_what_the_live_totals_need(self):
        page = self.get("/timesheet?u=5&w=2026-11-02")
        self.assertIn("function recalc", page)
        for marker in ('class="row"', 'class="c" data-day="2026-11-02"', 'class="rt"', 'class="dt" data-day="2026-11-02"', 'class="gt"'):
            self.assertIn(marker, page)
        self.assertIn('data-h="8"', page)  # a submitted (locked) cell still counts, as text

    def test_timesheet_cells_have_no_status_letters_or_key(self):
        # Locked (submitted) hours are plain text and editable ones are boxes, so no D/S/A/E letters and no key.
        page = self.get("/timesheet?u=5&w=2026-11-02")
        self.assertNotRegex(page, r"<small>[DSAE]</small>")
        self.assertNotIn("D = draft", page)
        self.assertNotIn("marked A or E", page)
        self.assertIn("To clear a draft entry", page)  # the one useful hint from that line stays

    # ---------- date pickers ----------
    def test_every_date_box_is_a_calendar_picker(self):
        page = self.get("/reports?u=5&from=2026-10-26&to=2026-11-02")
        self.assertIn('<input type="date" name="from" value="2026-10-26">', page)
        self.assertIn('<input type="date" name="to" value="2026-11-02">', page)
        self.assertNotIn("(yyyy-mm-dd)", page)
        for path, names in (("/admin?u=29&t=periods", ["week_start"]), ("/admin?u=30&t=users", ["start_date", "end_date"]),
                            ("/admin?u=29&t=rates", ["effective_from"]), ("/admin/edit?u=30&t=users&id=1", ["end_date"])):
            page = self.get(path)
            for name in names:
                self.assertIn('<input type="date" name="%s"' % name, page, path)
            self.assertNotIn('type="text" name="%s"' % names[0], page, path)

    def test_a_cleared_report_date_falls_back_to_the_default_range(self):
        page = self.get("/reports?u=5&from=&to=")
        self.assertIn('<input type="date" name="from" value="2026-10-01">', page)
        self.assertIn('<input type="date" name="to" value="2026-12-31">', page)
        self.assertIn("Total for range", page)
        self.assertNotIn("<b>Total for range</b></td><td><b></b></td><td><b></b></td><td><b></b></td><td><b></b></td><td><b></b></td><td align=\"right\"><b>0</b>", page)

    # ---------- reports by role: values for cost controllers only, everyone else sees hours ----------
    def report_page(self, u, extra=""):
        page = self.get("/reports?u=%d&from=2026-01-01&to=2026-12-31%s" % (u, extra))
        tree, rest = page.split("Period reports", 1)
        return page, tree, rest

    def test_only_cost_controllers_see_values_and_rates(self):
        ctr_total = self.one("select sum(total) from v_ctr")[0]
        for u in (5, 6, 7):  # a standard user, and approvers of Apache and of other scopes
            page, tree, rest = self.report_page(u)
            self.assertNotIn("Sales rate", page, u)
            self.assertNotIn("<th>Total</th>", page, u)
            self.assertNotIn("%.2f" % ctr_total, page, u)
            self.assertNotRegex(page, r">\d[\d,]*\.\d\d<", u)  # no money-formatted figure anywhere in the page
        page, tree, rest = self.report_page(29)  # the cost controller sees them all
        self.assertIn("Sales rate", page)
        self.assertIn("<th>Total</th>", page)
        self.assertIn("%.2f" % ctr_total, page)
        self.assertRegex(page, r">\d[\d,]*\.\d\d<")  # the same pattern the others must not match does match here, so the check is real

    def test_a_standard_user_sees_only_their_own_hours(self):
        page, tree, rest = self.report_page(5)  # Ewan Shaw books to Apache only
        self.assertIn("Ewan Shaw</span>", tree)  # a person row in the tree (the user switcher uses <option>, not <span>)
        for other in ("Stuart Sherwood", "Emma Noble", "Daniel Evans"):
            self.assertNotIn("%s</span>" % other, tree, other)
        self.assertNotIn("A1500", page)  # a Spirit scope he has not booked to
        mine = self.one("select coalesce(sum(hours),0) from time_entry where user_id=5 and status='Approved'")[0]
        total_row = rest.split("Total for range")[1].split("</tr>")[0]
        self.assertIn("<b>%s</b>" % app.fmt(mine), total_row)  # the weekly report is his own approved hours only
        self.assertIn(">Apache</option>", page)  # the filters only offer what he can see
        self.assertNotIn(">Spirit</option>", page)
        self.assertIn(">Spirit</option>", self.report_page(29)[0])

    def test_an_approver_sees_everyones_hours_on_their_scopes_only(self):
        page, tree, rest = self.report_page(7)  # Alisdair Woodhead approves the Apache scopes
        self.assertIn("BB0001", tree)
        self.assertIn("BB0002", tree)
        self.assertIn("Stuart Sherwood</span>", tree)  # someone else booked to them
        self.assertNotIn("A1500", tree)                 # a Spirit scope: not his
        page, tree, rest = self.report_page(8)         # Kenneth Nicolson approves the Spirit scopes
        self.assertIn("A1500", tree)
        self.assertNotIn("BB0001", tree)
        self.assertNotIn("BB0002", tree)

    def test_ctr_page_shows_hours_only_and_nothing_outside_what_you_can_see(self):
        page = self.get("/ctr?u=5&phase=1")  # BB0001 Define: Ewan Shaw has hours here
        self.assertIn("Actual booked hrs", page)
        self.assertNotIn("<th>Total</th>", page)
        self.assertNotIn("Sales rate", page)
        family = re.search(r"family=([^\"&]+)", page).group(1)
        tab = self.get("/ctr?u=5&phase=1&family=%s" % family)
        for money in ("Sales rate", "<th>Total</th>", "<th>Value</th>"):
            self.assertNotIn(money, tab, money)
        self.assertNotRegex(tab, r">\d[\d,]*\.\d\d<")
        other = self.get("/ctr?u=5&phase=6")  # a Spirit phase he has no hours in: nothing is revealed, not even its name
        self.assertIn("no hours you can see", other)
        self.assertNotIn("Morecambe Hub", other)
        controller = self.get("/ctr?u=29&phase=6")
        self.assertIn("Morecambe Hub", controller)
        self.assertIn("<th>Total</th>", controller)

    def test_internal_hours_report_is_limited_to_what_you_can_see(self):
        self.post("/timesheet/save", {"u": "4", "w": "2026-11-02", "action": "Save draft", "h_8_4_2026-11-04": "3.5", "reason": ""})
        self.post("/timesheet/save", {"u": "4", "w": "2026-11-02", "action": "Submit week", "reason": ""})
        sees = {}
        for u in (4, 5, 7, 29):  # David Hart booked it, Ewan Shaw has nothing to do with it, Alisdair Woodhead approves INT01, the cost controller
            sees[u] = "David Hart" in self.report_page(u)[2].split("Internal hours")[1]
        self.assertEqual(sees, {4: True, 5: False, 7: True, 29: True})

    # ---------- missing timesheets (R31) ----------
    def missing(self, u, **params):
        """The Missing timesheets page as (page, {person: [cells]}) for week/filter params."""
        page = self.get("/missing?u=%d&%s" % (u, "&".join("%s=%s" % kv for kv in params.items())))
        rows = {}
        for r in page.split("<tr>")[1:]:
            cells = [re.sub(r"<[^>]+>", "", c) for c in re.findall(r"<td[^>]*>(.*?)</td>", r)]
            if len(cells) == 8:
                rows[cells[0]] = cells  # person, NMC ID, job title, employment, status, submitted hrs, draft hrs, sent back
        return page, rows

    def test_only_cost_controllers_and_approvers_can_open_missing_timesheets(self):
        for u in (5, 30):  # a standard user, the IT admin
            self.assertIn("not available to your role", self.get("/missing?u=%d" % u), u)
        for u in (7, 8, 29):  # the two approvers and the cost controller
            self.assertNotIn("not available to your role", self.get("/missing?u=%d" % u), u)
        for u, expected in ((5, False), (30, False), (7, True), (8, True), (29, True)):
            self.assertEqual('href="/missing?u=%d"' % u in self.get("/timesheet?u=%d" % u), expected, u)  # the menu link

    def test_a_cost_controller_sees_everyone_and_how_far_each_has_got(self):
        page, rows = self.missing(29, w="2026-11-02", s="all")
        self.assertEqual(len(rows), 30)  # everyone is active that week
        self.assertEqual(rows["Ewan Shaw"][4], "Submitted")
        self.assertEqual(rows["David Hart"][4], "Not started")
        # Ajin Sabu: two entries submitted (16 hrs) and one sent back to draft (8 hrs)
        self.assertEqual(rows["Ajin Sabu"][4:], ["Partly submitted", "16", "8", "1"])
        self.assertIn("1 partly submitted", page)
        # the default view shows only people who have not submitted, so Ewan Shaw is not in it
        page, rows = self.missing(29, w="2026-11-02")
        self.assertNotIn("Ewan Shaw", rows)
        self.assertIn("David Hart", rows)
        self.assertIn("Ajin Sabu", rows)
        self.assertEqual({r[4] for r in rows.values()}, {"Not started", "Partly submitted"})
        # filters
        self.assertEqual(set(self.missing(29, w="2026-11-02", s="Partly%20submitted")[1]), {"Ajin Sabu"})
        self.assertEqual({r[4] for r in self.missing(29, w="2026-11-02", s="Submitted")[1].values()}, {"Submitted"})
        self.assertEqual(self.missing(29, w="2026-11-02", s="nonsense")[1].keys(), rows.keys())  # an unknown filter falls back to the default

    def test_a_draft_only_week_is_reported_as_draft_only(self):
        self.post("/timesheet/save", {"u": "4", "w": "2026-11-02", "action": "Save draft", "h_8_4_2026-11-04": "3.5", "reason": ""})  # David Hart, INT01
        row = self.missing(29, w="2026-11-02", s="all")[1]["David Hart"]
        self.assertEqual(row[4:], ["Draft only", "", "3.5", ""])
        self.post("/timesheet/save", {"u": "4", "w": "2026-11-02", "action": "Submit week", "reason": ""})
        self.assertEqual(self.missing(29, w="2026-11-02", s="all")[1]["David Hart"][4:], ["Submitted", "3.5", "", ""])

    def test_an_approver_sees_only_their_own_people(self):
        _, kenneth = self.missing(8, w="2026-11-02", s="all")   # Spirit
        _, alisdair = self.missing(7, w="2026-11-02", s="all")  # Apache
        # Spirit PAAF holders are Kenneth's people, Apache PAAF holders are Alisdair's; neither sees themselves
        self.assertIn("Ajin Sabu", kenneth)
        self.assertIn("Gary Bridge", kenneth)        # Spirit only
        self.assertNotIn("Gary Bridge", alisdair)
        self.assertIn("Ewan Shaw", alisdair)         # Apache only
        self.assertNotIn("Ewan Shaw", kenneth)
        self.assertNotIn("Kenneth Nicolson", kenneth)
        self.assertNotIn("Alisdair Woodhead", alisdair)
        # a PAAF that is only 'Sent' does not count: David Hart is in neither list until he books to an internal code
        self.assertNotIn("David Hart", alisdair)
        self.assertNotIn("David Hart", kenneth)
        self.post("/timesheet/save", {"u": "4", "w": "2026-11-02", "action": "Save draft", "h_8_4_2026-11-04": "2", "reason": ""})  # INT01: Alisdair's code
        self.assertEqual(self.missing(7, w="2026-11-02", s="all")[1]["David Hart"][4], "Draft only")
        self.assertNotIn("David Hart", self.missing(8, w="2026-11-02", s="all")[1])
        # and each approver's list is a subset of everyone
        everyone = set(self.missing(29, w="2026-11-02", s="all")[1])
        self.assertLess(set(alisdair), everyone)
        self.assertLess(set(kenneth), everyone)

    def test_only_people_active_in_the_week_are_expected(self):
        def add(nmc, name, start, end):
            self.admin_post("/admin/add", {"t": "users", "nmc_id": nmc, "name": name, "employment_type": "Staff", "role_id": "6",
                                           "permission": "user", "start_date": start, "end_date": end})
        add("A951", "Starts Later", "2026-11-09", "")          # starts the Monday after the week
        add("A952", "Left Before", "2026-10-01", "2026-11-01")  # left the Sunday before the week
        add("A953", "Starts Midweek", "2026-11-05", "")        # starts on the Thursday
        add("A954", "Leaves Monday", "2026-10-01", "2026-11-02")  # last day is the Monday of the week
        rows = self.missing(29, w="2026-11-02", s="all")[1]
        self.assertNotIn("Starts Later", rows)
        self.assertNotIn("Left Before", rows)
        self.assertEqual(rows["Starts Midweek"][4], "Not started")
        self.assertEqual(rows["Leaves Monday"][4], "Not started")
        self.assertIn("Starts Later", self.missing(29, w="2026-11-09", s="all")[1])  # expected from the week it starts

    def test_the_week_defaults_to_the_last_completed_week_and_can_be_changed(self):
        last = app.monday(datetime.date.today()) - datetime.timedelta(days=7)
        self.assertIn("Week commencing %s" % last.strftime("%d/%m/%Y"), self.get("/missing?u=29"))
        self.assertIn("Week commencing %s" % last.strftime("%d/%m/%Y"), self.get("/missing?u=29&w=not-a-date"))
        page = self.get("/missing?u=29&w=2026-11-04")  # any day snaps to its Monday
        self.assertIn("Week commencing 02/11/2026", page)
        self.assertIn("w=2026-10-26", page)  # previous week
        self.assertIn("w=2026-11-09", page)  # next week
        self.assertIn('href="/missing?u=29&amp;w=%s&amp;s=missing">This week so far' % app.monday(datetime.date.today()).isoformat(), page)

    def test_an_approver_with_no_people_is_told_why(self):
        # Jamie Ireland (6) approves only a new internal code that nobody has booked to, so she has no people yet
        self.assertIn("not available to your role", self.get("/missing?u=6&w=2026-11-02"))  # not an approver until she is named on a code
        self.admin_post("/admin/add", {"t": "internal", "description": "Facilities", "approver_user_id": "6"})
        page = self.get("/missing?u=6&w=2026-11-02")
        self.assertIn("Nobody is expected", page)
        self.assertIn("approved PAAF for a client you approve", page)
        # once someone books to her internal code, that person is hers
        code = self.one("select s.scope_id, ph.phase_id from scope s join phase ph on ph.scope_id=s.scope_id where s.description='Facilities'")
        self.post("/timesheet/save", {"u": "5", "w": "2026-11-02", "action": "Save draft", "h_%d_4_2026-11-04" % code[1]: "1", "reason": ""})
        self.assertIn("Ewan Shaw", self.missing(6, w="2026-11-02", s="all")[1])

    # ---------- start and end dates, PAAF expiry (R07) ----------
    def test_no_input_boxes_outside_a_persons_start_and_end_dates(self):
        # Ewan Shaw (5) leaves on Wednesday 4 Nov: there are no boxes from the Thursday, and saving there is refused
        self.admin_post("/admin/save", {"t": "users", "id": "5", "name": "Ewan Shaw", "employment_type": "Contract", "role_id": "4",
                                        "permission": "user", "end_date": "2026-11-04"})
        page = self.get("/timesheet?u=5&w=2026-11-02")
        self.assertIn('name="h_1_4_2026-11-04"', page)  # his last day still has a box
        for day in ("05", "06", "07", "08"):
            self.assertNotIn('name="h_1_4_2026-11-%s"' % day, page, day)
        self.assertIn("until 04/11/2026", page)  # the note says why
        page = self.post("/timesheet/save", {"u": "5", "w": "2026-11-02", "action": "Save draft", "h_1_4_2026-11-06": "5", "reason": ""})
        self.assertIn("not active on that date", page)
        self.assertEqual(self.one("select count(*) from time_entry where user_id=5 and work_date='2026-11-06'")[0], 0)

    def test_a_week_outside_the_employment_dates_cannot_be_booked(self):
        self.admin_post("/admin/add", {"t": "users", "nmc_id": "A955", "name": "Not Started Yet", "employment_type": "Staff", "role_id": "6",
                                       "permission": "user", "start_date": "2026-11-09", "end_date": ""})
        uid = self.one("select user_id from app_user where nmc_id='A955'")[0]
        page = self.get("/timesheet?u=%d&w=2026-11-02" % uid)
        self.assertIn("outside your start and end dates", page)
        self.assertIn("you start on 09/11/2026", page)
        self.assertNotIn("Add row", page)
        self.assertIn("Add row", self.get("/timesheet?u=%d&w=2026-11-09" % uid))  # open from the week they start
        # a week part-way through is open, with a note: Midweek Starter begins on the Thursday
        self.admin_post("/admin/add", {"t": "users", "nmc_id": "A956", "name": "Midweek Starter", "employment_type": "Staff", "role_id": "6",
                                       "permission": "user", "start_date": "2026-11-05", "end_date": ""})
        uid = self.one("select user_id from app_user where nmc_id='A956'")[0]
        page = self.get("/timesheet?u=%d&w=2026-11-02&add_phase=8&add_role=6" % uid)  # INT01 as Cost Engineer
        self.assertIn("from 05/11/2026", page)
        self.assertNotIn("h_8_6_2026-11-03", page)
        self.assertIn("h_8_6_2026-11-05", page)

    def test_client_hours_cannot_be_booked_after_the_paaf_expires(self):
        # David Hart's Apache PAAF (4) is only 'Sent'. Approve it, valid until Wednesday 4 Nov.
        self.assertIn("approved", self.admin_post("/admin/paaf", {"paaf_id": "4", "action": "Approve",
                                                                  "document_ref": "paaf/apache/A004.pdf", "expiry_date": "2026-11-04"}))
        self.assertIn("Apache &gt; Wellbay Decommissioning", self.get("/timesheet?u=4&w=2026-11-02"))      # valid for part of that week
        self.assertNotIn("Apache &gt;", self.get("/timesheet?u=4&w=2026-11-09"))                           # expired before this one starts
        self.assertIn("1 entry saved", self.post("/timesheet/save", {"u": "4", "w": "2026-11-02", "action": "Save draft",
                                                                    "h_1_4_2026-11-03": "4", "reason": ""}))
        late = self.post("/timesheet/save", {"u": "4", "w": "2026-11-02", "action": "Save draft", "h_1_4_2026-11-05": "4", "reason": ""})
        self.assertIn("PAAF has expired", late)
        # internal hours do not depend on the PAAF
        self.assertIn("1 entry saved", self.post("/timesheet/save", {"u": "4", "w": "2026-11-02", "action": "Save draft",
                                                                    "h_8_4_2026-11-05": "2", "reason": ""}))

    # ---------- approvers book time too; a cost controller approves it ----------
    def test_an_approver_can_book_to_their_own_scope_and_a_cost_controller_approves_it(self):
        # Kenneth Nicolson (8) approves the Spirit scopes and holds a Spirit PAAF, so he can book to A1500 (phase 6) as Head of Projects (role 30)
        self.assertIn("A1500", self.get("/timesheet?u=8&w=2026-11-02"))
        self.assertIn("Nothing waiting", self.get("/approvals?u=29"))  # no approver has booked anything yet
        self.assertIn("1 entry saved", self.post("/timesheet/save", {"u": "8", "w": "2026-11-02", "action": "Save draft",
                                                                    "h_6_30_2026-11-04": "4", "reason": ""}))
        self.post("/timesheet/save", {"u": "8", "w": "2026-11-02", "action": "Submit week", "reason": ""})
        eid = self.one("select entry_id from time_entry where user_id=8 and work_date='2026-11-04'")[0]
        self.assertEqual(self.one("select status from time_entry where entry_id=?", eid)[0], "Submitted")
        # it is not in his own queue, and he cannot approve it himself
        self.assertNotIn('value="%d"' % eid, self.get("/approvals?u=8"))
        self.assertIn("not their own", self.post("/approvals/act", {"u": "8", "entry_id": str(eid), "action": "Approve selected"}))
        # nobody else who approves can either: not the Apache approver, not the IT admin
        for u in ("7", "30"):
            page = self.post("/approvals/act", {"u": u, "entry_id": str(eid), "action": "Approve selected"})
            self.assertTrue("only the approver named" in page or "not named as an approver" in page, u)
        self.assertEqual(self.one("select status from time_entry where entry_id=?", eid)[0], "Submitted")
        # the cost controller sees it and approves it
        self.assertIn('value="%d"' % eid, self.get("/approvals?u=29"))
        self.assertIn("1 entry approved", self.post("/approvals/act", {"u": "29", "entry_id": str(eid), "action": "Approve selected"}))
        self.assertEqual(tuple(self.one("select status, approved_by from time_entry where entry_id=?", eid)), ("Approved", 29))
        # Kenneth sees the hours in his reports without any value
        page = self.get("/reports?u=8&from=2026-01-01&to=2026-12-31")
        self.assertIn("A1500", page)
        self.assertNotIn("Sales rate", page)

    def test_a_cost_controller_can_reject_an_approvers_own_hours(self):
        self.post("/timesheet/save", {"u": "7", "w": "2026-11-02", "action": "Save draft", "h_3_2_2026-11-04": "3", "reason": ""})  # Alisdair (Apache) on BB0002
        self.post("/timesheet/save", {"u": "7", "w": "2026-11-02", "action": "Submit week", "reason": ""})
        eid = self.one("select entry_id from time_entry where user_id=7 and work_date='2026-11-04'")[0]
        self.assertIn("sent back to draft", self.post("/approvals/act", {"u": "29", "entry_id": str(eid), "action": "Reject selected", "reason": "Wrong code"}))
        self.assertEqual(tuple(self.one("select status, reject_reason from time_entry where entry_id=?", eid)), ("Draft", "Wrong code"))

    # ---------- cost permission is not tied to a job title ----------
    def test_cost_permission_can_be_given_to_anyone_not_only_cost_engineers(self):
        url = "/reports?u=%d&from=2026-01-01&to=2026-12-31"
        for u in (1, 2):  # the two cost engineers are controllers from the start
            self.assertIn("Sales rate", self.get(url % u), u)
        # Daniel Evans (9) is a Project Engineer: a standard user, who sees only his own hours and no values
        self.assertNotIn("Sales rate", self.get(url % 9))
        self.assertNotIn('href="/admin?u=9', self.get("/timesheet?u=9"))
        # the IT admin gives him cost permission on the Users tab
        self.assertIn("Saved", self.admin_post("/admin/save", {"t": "users", "id": "9", "name": "Daniel Evans", "employment_type": "Staff",
                                                               "role_id": "3", "permission": "controller", "end_date": ""}))
        self.assertIn("Sales rate", self.get(url % 9))                       # now sees everything with values
        self.assertIn('href="/admin?u=9', self.get("/timesheet?u=9"))        # and gets the cost controller's Admin tabs
        self.assertEqual(self.one("select r.name from app_user u join role r using(role_id) where u.user_id=9")[0], "Project Engineer")

    # ---------- schema version ----------
    def test_the_app_refuses_an_older_database_with_a_clear_message(self):
        self.assertIsNone(app.schema_problem(app.DB_PATH))                         # the test database was just built
        self.assertEqual(self.one("pragma user_version")[0], app.REQUIRED_SCHEMA)  # schema.sql and the app agree
        old = os.path.join(self.tmp, "old.db")
        con = sqlite3.connect(old)  # a database from before schema versions existed: user_version 0
        con.execute("create table client (client_id integer primary key, name text)")
        con.commit()
        con.close()
        problem = app.schema_problem(old)
        self.assertIn("older schema", problem)
        self.assertIn("python scripts/build.py", problem)
        # and starting the app on it stops with that message instead of failing page by page
        run = subprocess.run([sys.executable, os.path.join(ROOT, "app", "app.py"), "0"], capture_output=True, text=True,
                             timeout=30, env=dict(os.environ, TW_DB=old))
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("older schema", run.stderr)

    # ---------- timestamps ----------
    def test_audit_and_periods_show_times_to_the_second_only(self):
        self.post("/timesheet/save", {"u": "5", "w": "2026-11-02", "action": "Save draft", "h_1_4_2026-11-06": "7.5", "reason": ""})
        self.post("/timesheet/save", {"u": "5", "w": "2026-11-02", "action": "Submit week", "reason": ""})
        self.admin_post("/admin/period", {"action": "close", "week_start": "2026-11-02"})
        for path in ("/audit?u=29", "/admin?u=29&t=periods"):
            page = self.get(path)
            self.assertRegex(page, r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?!\.\d)", path)  # a time to the second is there...
            self.assertNotRegex(page, r"\d\d:\d\d:\d\d\.\d", path)  # ...and no fractions of a second
        stored = self.one("select last_edited_at from time_entry where user_id=5 and work_date='2026-11-06'")[0]
        self.assertRegex(stored, r"\d\d:\d\d:\d\d\.\d+")  # still stored precisely, see stamp()

    def test_save_and_submit_in_one_click_is_not_refused_as_a_repeat_edit(self):
        # Two edits of one entry by the same person within the same second need different last_edited_at values,
        # which is why stamp() keeps fractions of a second.
        self.post("/timesheet/save", {"u": "5", "w": "2026-11-02", "action": "Save draft", "h_1_4_2026-11-06": "7.5", "reason": ""})
        # an existing draft: changing its hours and submitting in one click updates the same entry twice in the same second
        page = self.post("/timesheet/save", {"u": "5", "w": "2026-11-02", "action": "Submit week",
                                             "h_1_4_2026-11-06": "6", "reason": ""})
        self.assertIn("1 entry saved", page)
        self.assertIn("submitted for approval", page)
        self.assertNotIn("Edits must set", page)
        self.assertEqual(tuple(self.one("select hours, status from time_entry where user_id=5 and work_date='2026-11-06'")), (6, "Submitted"))

    # ---------- admin forms ----------
    # Demo accounts: 29 Stephanie Apedaile (commercial lead) is a cost controller, 30 NMC Admin is the IT admin, 7 Alisdair Woodhead is a standard
    # user who approves. Users and job roles belong to the admin; clients, rates, PAAFs, scopes and internal codes to the controller.
    CONTROLLER, IT_ADMIN = "29", "30"

    def admin_post(self, path, data, u=None):
        if u is None:
            u = self.IT_ADMIN if data.get("t") in ("users", "roles") else self.CONTROLLER
        d = {"u": u}
        d.update(data)
        return self.post(path, d)

    def one(self, sql, *args):
        con = app.db()
        try:
            return con.execute(sql, args).fetchone()
        finally:
            con.close()

    def test_all_admin_tabs_and_edit_pages_load(self):
        for t, who in [("periods", 29), ("periods", 30), ("users", 30), ("roles", 30), ("clients", 29), ("rates", 29),
                       ("paafs", 29), ("scopes", 29), ("internal", 29)]:
            self.assertIn("Admin", self.get("/admin?u=%d&t=%s" % (who, t)), t)
        for t, i, who in [("users", 1, 30), ("roles", 1, 30), ("scopes", 1, 29), ("internal", 7, 29)]:
            self.assertIn("Save changes", self.get("/admin/edit?u=%d&t=%s&id=%d" % (who, t, i)), t)

    def test_non_admin_cannot_use_admin_forms(self):
        before = self.one("select count(*) from client")[0]
        self.admin_post("/admin/add", {"t": "clients", "name": "Sneaky"}, u="5")
        self.assertEqual(self.one("select count(*) from client")[0], before)
        self.admin_post("/admin/period", {"action": "open", "week_start": "2030-01-07"}, u="5")
        self.assertIsNone(self.one("select 1 from period where week_start='2030-01-07'"))
        self.assertIn("not available to your role", self.get("/admin/edit?u=5&t=users&id=1"))

    def test_each_admin_tab_is_limited_to_its_role(self):
        # The IT admin manages users and job roles only; the cost controller manages codes, clients, rates and PAAFs.
        # Neither can use the other's tabs, and a standard user has none.
        for who, allowed in (("30", {"periods", "users", "roles"}), ("29", {"periods", "clients", "rates", "paafs", "scopes", "internal"}), ("5", set())):
            page = self.get("/admin?u=%s" % who)
            for tab in ("periods", "users", "roles", "clients", "rates", "paafs", "scopes", "internal"):
                self.assertEqual("t=%s\"" % tab in page, tab in allowed, (who, tab))
            for tab in ("users", "roles", "clients", "rates", "paafs", "scopes", "internal"):
                if tab not in allowed:
                    self.assertIn("not available to your role", self.get("/admin?u=%s&t=%s" % (who, tab)), (who, tab))
        before = self.one("select count(*) from client")[0], self.one("select count(*) from app_user")[0]
        self.admin_post("/admin/add", {"t": "clients", "name": "FromIT"}, u="30")
        self.admin_post("/admin/add", {"t": "users", "nmc_id": "A950", "name": "FromController", "employment_type": "Staff", "role_id": "6",
                                       "permission": "user", "start_date": "2026-11-01", "end_date": ""}, u="29")
        self.assertEqual((self.one("select count(*) from client")[0], self.one("select count(*) from app_user")[0]), before)

    def test_menus_follow_the_role(self):
        def menu(u):
            page = self.get("/timesheet?u=%d" % u)
            return {k: ('href="/%s?u=%d' % (path, u)) in page for k, path in
                    (("approvals", "approvals"), ("reports", "reports"), ("admin", "admin"), ("audit", "audit"))}
        self.assertEqual(menu(5), {"approvals": False, "reports": True, "admin": False, "audit": False})    # standard user
        self.assertEqual(menu(6), {"approvals": False, "reports": True, "admin": False, "audit": False})    # another standard user
        self.assertEqual(menu(7), {"approvals": True, "reports": True, "admin": False, "audit": False})     # approver (Apache)
        self.assertEqual(menu(8), {"approvals": True, "reports": True, "admin": False, "audit": False})     # approver (Spirit)
        self.assertEqual(menu(29), {"approvals": True, "reports": True, "admin": True, "audit": True})      # cost controller (approvers' own hours)
        self.assertEqual(menu(30), {"approvals": False, "reports": False, "admin": True, "audit": True})    # IT admin
        self.assertIn("not available to your role", self.get("/reports?u=30"))
        self.assertIn("not available to your role", self.get("/ctr?u=30&phase=1"))

    def test_only_a_named_approver_sees_approvals_and_controllers_and_admins_cannot_approve(self):
        for u in (5, 30):  # a standard user who is not an approver, and the IT admin
            self.assertIn("not named as an approver", self.get("/approvals?u=%d" % u))
        eid = self.one("select entry_id from time_entry where status='Submitted' and scope_id=1 limit 1")[0]
        for u in ("5", "30"):
            self.assertIn("not named as an approver", self.post("/approvals/act", {"u": u, "entry_id": str(eid), "action": "Approve selected"}))
        # a cost controller approves only the approvers' own hours: this is an ordinary entry, so it is not theirs to approve or reject
        for action in ("Approve selected", "Reject selected"):
            self.assertIn("only the approver named", self.post("/approvals/act", {"u": "29", "entry_id": str(eid), "action": action, "reason": "x"}))
        self.assertEqual(self.one("select status from time_entry where entry_id=?", eid)[0], "Submitted")
        # an approver of a different scope is refused too
        self.assertIn("only the approver named", self.post("/approvals/act", {"u": "8", "entry_id": str(eid), "action": "Approve selected"}))
        self.assertIn("only the approver named", self.post("/approvals/act", {"u": "8", "entry_id": str(eid), "action": "Reject selected", "reason": "x"}))
        self.assertEqual(self.one("select status from time_entry where entry_id=?", eid)[0], "Submitted")

    def test_an_approver_cannot_approve_their_own_hours(self):
        # Alisdair Woodhead (7) approves INT01 and can also book to it, but not approve his own entry
        self.post("/timesheet/save", {"u": "7", "w": "2026-11-02", "action": "Save draft", "h_8_2_2026-11-04": "2", "reason": ""})
        self.post("/timesheet/save", {"u": "7", "w": "2026-11-02", "action": "Submit week", "reason": ""})
        eid = self.one("select entry_id from time_entry where user_id=7 and work_date='2026-11-04'")[0]
        self.assertIn("not their own", self.post("/approvals/act", {"u": "7", "entry_id": str(eid), "action": "Approve selected"}))
        self.assertEqual(self.one("select status from time_entry where entry_id=?", eid)[0], "Submitted")

    def test_add_client_role_and_reject_duplicates_and_blanks(self):
        self.assertIn("Client added", self.admin_post("/admin/add", {"t": "clients", "name": "Shell"}))
        self.assertIn("already exists", self.admin_post("/admin/add", {"t": "clients", "name": "Shell"}))
        self.assertIn("Role added", self.admin_post("/admin/add", {"t": "roles", "skill_code": "QA-09", "name": "QA Engineer", "family": "Quality"}))
        self.assertIn("is required", self.admin_post("/admin/add", {"t": "roles", "skill_code": "", "name": "x", "family": "y"}))

    def test_add_user_validates_dates_and_edit_user(self):
        data = {"t": "users", "nmc_id": "A901", "name": "Test Person", "employment_type": "Staff", "role_id": "6",
                "permission": "user", "start_date": "2026-11-01", "end_date": ""}
        self.assertIn("User added", self.admin_post("/admin/add", data))
        bad = dict(data, nmc_id="A902", start_date="01/11/2026")
        self.assertIn("must be a date", self.admin_post("/admin/add", bad))
        uid = self.one("select user_id from app_user where nmc_id='A901'")[0]
        self.assertIn("Saved", self.admin_post("/admin/save", {"t": "users", "id": str(uid), "name": "Test Person", "employment_type": "Contract",
                                                               "role_id": "4", "permission": "user", "end_date": "2026-12-31"}))
        self.assertEqual(tuple(self.one("select employment_type, role_id, end_date from app_user where user_id=?", uid)), ("Contract", 4, "2026-12-31"))

    def test_new_person_full_paaf_flow_then_can_book(self):
        self.admin_post("/admin/add", {"t": "users", "nmc_id": "A901", "name": "Test Person", "employment_type": "Staff", "role_id": "6",
                                       "permission": "user", "start_date": "2026-11-01", "end_date": ""})
        uid = self.one("select user_id from app_user where nmc_id='A901'")[0]
        self.assertIn("PAAF raised", self.admin_post("/admin/add", {"t": "paafs", "user_id": str(uid), "client_id": "1"}))
        # rate was resolved from the person's role (Cost Engineer on Apache)
        self.assertEqual(self.one("select rt.sales_rate from paaf p join rate rt on rt.rate_id=p.rate_id where p.user_id=?", uid)[0], 85.5)
        self.assertIn("already has a PAAF", self.admin_post("/admin/add", {"t": "paafs", "user_id": str(uid), "client_id": "1"}))
        pid = self.one("select paaf_id from paaf where user_id=?", uid)[0]
        book = {"u": str(uid), "w": "2026-11-02", "action": "Save draft", "h_1_6_2026-11-06": "4", "reason": ""}
        self.assertIn("No approved PAAF", self.post("/timesheet/save", book))                      # not approved yet
        self.assertIn("marked as sent", self.admin_post("/admin/paaf", {"paaf_id": str(pid), "action": "Mark sent"}))
        self.assertIn("signed form reference is required", self.admin_post("/admin/paaf", {"paaf_id": str(pid), "action": "Approve", "document_ref": ""}))
        self.assertIn("approved", self.admin_post("/admin/paaf", {"paaf_id": str(pid), "action": "Approve", "document_ref": "paaf/apache/A901.pdf", "expiry_date": ""}))
        self.assertIn("1 entry saved", self.post("/timesheet/save", book))                        # now allowed

    def test_paaf_needs_a_rate_for_the_role(self):
        self.admin_post("/admin/add", {"t": "clients", "name": "Shell"})
        cid = self.one("select client_id from client where name='Shell'")[0]
        page = self.admin_post("/admin/add", {"t": "paafs", "user_id": "5", "client_id": str(cid)})
        self.assertIn("Add the rate first", page)

    def test_paaf_actions_only_allowed_from_the_right_status(self):
        page = self.admin_post("/admin/paaf", {"paaf_id": "1", "action": "Mark sent"})   # PAAF 1 is already Approved
        self.assertIn("not available", page)

    def test_new_rate_applies_from_its_date_and_old_hours_keep_old_price(self):
        self.assertIn("Rate added", self.admin_post("/admin/add", {"t": "rates", "client_id": "1", "role_id": "4",
                                                                 "sales_rate": "100", "effective_from": "2026-11-05"}))
        self.assertIn("is not a number" if False else "Sales rate must be a number",
                      self.admin_post("/admin/add", {"t": "rates", "client_id": "1", "role_id": "4", "sales_rate": "abc", "effective_from": "2026-11-05"}))
        self.post("/timesheet/save", {"u": "5", "w": "2026-11-02", "action": "Save draft", "h_1_4_2026-11-06": "8", "reason": ""})
        self.assertEqual(self.one("select rt.sales_rate from time_entry t join rate rt on rt.rate_id=t.rate_id "
                                  "where t.user_id=5 and t.work_date='2026-11-06'")[0], 100)
        self.assertEqual(self.one("select rt.sales_rate from time_entry t join rate rt on rt.rate_id=t.rate_id "
                                  "where t.user_id=5 and t.work_date='2026-11-02'")[0], 85.5)

    def test_add_scope_with_approver_and_change_approver(self):
        self.assertIn("Scope added", self.admin_post("/admin/add", {"t": "scopes", "scope_code": "BB0003", "project_id": "1",
                                                                  "description": "New Study", "status": "Open", "approver_user_id": "8"}))
        sid = self.one("select scope_id from scope where scope_code='BB0003'")[0]
        self.assertEqual(self.one("select count(*) from phase where scope_id=? and name='General'", sid)[0], 1)
        self.assertEqual(self.one("select approver_user_id from scope where scope_id=?", sid)[0], 8)
        self.assertIn("Saved", self.admin_post("/admin/save", {"t": "scopes", "id": str(sid), "description": "New Study", "status": "Closed", "approver_user_id": "6"}))
        self.assertEqual(tuple(self.one("select status, approver_user_id from scope where scope_id=?", sid)), ("Closed", 6))
        # only standard users can be named: the cost controller and the IT admin never approve
        for bad in ("29", "30"):
            self.assertIn("Only a standard user", self.admin_post("/admin/save", {"t": "scopes", "id": str(sid), "description": "New Study",
                                                                                  "status": "Open", "approver_user_id": bad}))
            self.assertIn("Only a standard user", self.admin_post("/admin/add", {"t": "scopes", "scope_code": "BB0004", "project_id": "1",
                                                                                 "description": "X", "status": "Open", "approver_user_id": bad}))
        self.assertEqual(self.one("select approver_user_id from scope where scope_id=?", sid)[0], 6)

    def test_deactivated_role_is_hidden_from_new_timesheet_rows(self):
        self.admin_post("/admin/save", {"t": "roles", "id": "2", "skill_code": "EM-02", "name": "Engineering Manager", "family": "Engineering Management", "active": "0"})
        page = self.get("/timesheet?u=5&w=2026-11-02")
        self.assertNotIn("EM-02 Engineering Manager", page)
        self.assertIn("PM-02 Project Controls Manager", page)



    def test_user_switcher_shows_each_persons_job_title(self):
        page = self.get("/reports?u=7")
        for label in ["Alisdair Woodhead - Engineering Manager</option>", "Kenneth Nicolson - Head of Projects</option>",
                      "Emma Noble - Cost Engineer (controller)", "Stephanie Apedaile - Commercial Lead (controller)",
                      "NMC Admin - System Administrator (admin)", "Louis Ducharme - Operations Superintendent",
                      "Gary Davidson - Subsea Pipeline Engineer", "Janis Watt - Lead Technical Safety Engineer"]:
            self.assertIn(label, page)

    def test_roles_tab_groups_job_titles_by_family(self):
        page = self.get("/admin?u=30&t=roles")
        for text in ["Job family", "Piping Engineering", "Technical Safety", "Lead Process Engineer", "Document Controller"]:
            self.assertIn(text, page)

    def test_reports_show_the_new_job_titles(self):
        page = self.get("/reports?u=29")
        for text in ["PM-04 Planner", "EM-01 Operations Superintendent", "PI-01 Lead Piping Engineer", "TS-01 Lead Technical Safety Engineer"]:
            self.assertIn(text, page)

    # ---------- Paul's demo feedback ----------
    def queue_ids(self, approver):
        con = app.db()
        try:
            return [r[0] for r in con.execute("select t.entry_id from time_entry t join scope s on s.scope_id=t.scope_id "
                                              "where t.status='Submitted' and s.approver_user_id=?", (approver,))]
        finally:
            con.close()

    def test_bulk_approve_selected_and_untick_one(self):
        ids = self.queue_ids(7)
        self.assertGreaterEqual(len(ids), 2)
        page = self.get("/approvals?u=7")
        self.assertIn("Select all", page)
        self.assertIn("Approve selected", page)
        keep = ids[0]                                           # "untick" one: leave it out of the post
        page = self.post("/approvals/act", {"u": "7", "entry_id": [str(i) for i in ids[1:]], "action": "Approve selected"})
        self.assertIn("%d entries approved" % (len(ids) - 1), page)
        self.assertEqual(self.one("select status from time_entry where entry_id=?", keep)[0], "Submitted")
        self.assertEqual(self.one("select count(*) from time_entry where entry_id in (%s) and status='Approved'"
                                  % ",".join(str(i) for i in ids[1:]))[0], len(ids) - 1)

    def test_bulk_reject_needs_reason_and_ticks(self):
        ids = self.queue_ids(7)
        self.assertIn("Tick at least one", self.post("/approvals/act", {"u": "7", "action": "Reject selected", "reason": "x"}))
        self.assertIn("Give a reason", self.post("/approvals/act", {"u": "7", "entry_id": [str(ids[0])], "action": "Reject selected", "reason": ""}))
        self.assertIn("sent back to draft", self.post("/approvals/act", {"u": "7", "entry_id": [str(i) for i in ids], "action": "Reject selected", "reason": "Wrong scope"}))
        self.assertEqual(self.one("select status, reject_reason from time_entry where entry_id=?", ids[0])[:], ("Draft", "Wrong scope"))

    def test_bulk_approve_reports_wrong_approver_without_stopping(self):
        ids = self.queue_ids(7)
        # Kenneth Nicolson (8) approves the Spirit scopes, not this one: every entry is refused and reported, none is approved
        page = self.post("/approvals/act", {"u": "8", "entry_id": [str(i) for i in ids], "action": "Approve selected"})
        self.assertIn("only the approver named on its booking code", page)
        self.assertEqual(self.one("select count(*) from time_entry where entry_id in (%s) and status='Submitted'"
                                  % ",".join(str(i) for i in ids))[0], len(ids))

    def test_timesheet_shows_total_for_all_statuses(self):
        page = self.get("/timesheet?u=5&w=2026-11-02")
        self.assertIn("all statuses", page)
        self.assertRegex(page, r"Approved [\d.]+, Edited [\d.]+, Submitted [\d.]+, Draft [\d.]+")

    def test_report_subtotals_sit_in_the_columns(self):
        page = self.get("/reports?u=29")
        self.assertIn("Grand total", page)
        self.assertIn("Actual booked hrs", page)
        self.assertIn("Subtotal week commencing 2026-11-02", page)
        # period total for the full range equals the CTR grand total
        con = app.db()
        try:
            ctr = con.execute("select sum(total) from v_ctr").fetchone()[0]
            per = con.execute("select sum(total) from v_period_report").fetchone()[0]
        finally:
            con.close()
        self.assertAlmostEqual(ctr, per, places=2)
        self.assertIn("%.2f" % per, page)
        self.assertIn("<b>%.2f</b>" % ctr, page.split("Grand total")[1])

    def test_reports_tree_goes_client_project_scope_phase_skill_person_entry(self):
        page = self.get("/reports?u=29")
        for kind in ["Client", "Project", "Scope", "Phase", "Skill code", "Person", "Entry"]:
            self.assertIn("<td>%s</td>" % kind, page)
        for name in ["Wellbay Decommissioning", "BB0001 Wellbay Clearance", "Execute", "Ewan Shaw", "2026-11-03", "Morecambe Hub"]:
            self.assertIn(name, page)
        self.assertIn("Show to entries", page)

    def test_reports_tree_rolls_up_to_the_ctr_figures(self):
        con = app.db()
        try:
            rows, g_h, g_t, g_p = app.ctr_tree_rows(con)
            self.assertAlmostEqual(g_t, con.execute("select sum(total) from v_ctr").fetchone()[0], places=2)
            self.assertAlmostEqual(g_h, con.execute("select sum(hours) from v_ctr").fetchone()[0], places=2)
            self.assertAlmostEqual(g_p, con.execute("select sum(pending_hours) from v_ctr").fetchone()[0], places=2)
        finally:
            con.close()

    def test_timesheet_shows_project_and_phase_and_can_book_to_another_phase(self):
        page = self.get("/timesheet?u=9&w=2026-11-02")
        self.assertIn("<th>Project</th>", page)
        self.assertIn("<th>Phase</th>", page)
        self.assertIn("Apache &gt; Wellbay Decommissioning &gt; BB0001 Wellbay Clearance &gt; Execute", page)
        # phase 2 = BB0001 Execute (Daniel Evans has an approved Execute entry on 3 Nov), role 3 = Project Engineer
        self.assertIn("h_2_3_2026-11-03", page)
        self.assertIn("1 entry saved", self.post("/timesheet/save", {"u": "9", "w": "2026-11-02", "action": "Save draft",
                                                                    "h_2_3_2026-11-06": "4", "reason": ""}))
        self.assertEqual(self.one("select phase_id from time_entry where user_id=9 and work_date='2026-11-06'")[0], 2)

    def test_closed_phase_is_not_offered_and_blocks_booking(self):
        self.admin_post("/admin/save", {"t": "phases", "id": "2", "name": "Execute", "status": "Closed"})
        page = self.get("/timesheet?u=9&w=2026-11-02")
        self.assertNotIn("&gt; Execute</option>", page)
        self.assertIn("Phase is not Open", self.post("/timesheet/save", {"u": "9", "w": "2026-11-02", "action": "Save draft",
                                                                        "h_2_3_2026-11-06": "4", "reason": ""}))

    def test_approvals_page_shows_project_and_phase(self):
        page = self.get("/approvals?u=7")
        self.assertIn("<th>Project</th>", page)
        self.assertIn("<th>Phase</th>", page)

    def test_admin_add_project_scope_and_phase(self):
        self.assertIn("Project added", self.admin_post("/admin/add", {"t": "projects", "client_id": "1", "project_code": "APJ-03", "name": "Jetty Removal"}))
        pid = self.one("select project_id from project where project_code='APJ-03'")[0]
        self.assertIn("General phase", self.admin_post("/admin/add", {"t": "scopes", "project_id": str(pid), "scope_code": "BB0009",
                                                                     "description": "Jetty", "status": "Open", "approver_user_id": "6"}))
        self.assertEqual(self.one("select client_id from scope where scope_code='BB0009'")[0], 1)
        sid = self.one("select scope_id from scope where scope_code='BB0009'")[0]
        self.assertIn("Phase added", self.admin_post("/admin/add", {"t": "phases", "scope_id": str(sid), "name": "Execute"}))
        self.assertEqual(self.one("select count(*) from phase where scope_id=?", sid)[0], 2)
        self.assertIn("already exists", self.admin_post("/admin/add", {"t": "phases", "scope_id": str(sid), "name": "Execute"}))
        self.assertIn("Saved", self.admin_post("/admin/save", {"t": "projects", "id": str(pid), "name": "Jetty Removal", "status": "Closed"}))

    def test_internal_paaf_authorise_needs_no_client_form(self):
        self.admin_post("/admin/add", {"t": "users", "nmc_id": "A902", "name": "Internal Person", "employment_type": "Staff", "role_id": "6",
                                       "permission": "user", "start_date": "2026-11-01", "end_date": ""})
        uid = self.one("select user_id from app_user where nmc_id='A902'")[0]
        self.admin_post("/admin/add", {"t": "paafs", "user_id": str(uid), "client_id": "1"})
        pid = self.one("select paaf_id from paaf where user_id=?", uid)[0]
        self.assertIn("authorised internally", self.admin_post("/admin/paaf", {"paaf_id": str(pid), "action": "Authorise internally"}))
        book = {"u": str(uid), "w": "2026-11-02", "action": "Save draft", "h_1_6_2026-11-06": "4", "reason": ""}
        self.assertIn("1 entry saved", self.post("/timesheet/save", book))

    def test_ctr_page(self):
        con = sqlite3.connect(app.DB_PATH)
        con.row_factory = sqlite3.Row
        ph = con.execute("select phase_id from phase where name='Define' and scope_id=(select scope_id from scope where scope_code='BB0001')").fetchone()[0]
        fams = [r[0] for r in con.execute("select distinct r.family from time_entry t join rate rt on rt.rate_id=t.rate_id join role r on r.role_id=rt.role_id "
                                          "where t.phase_id=? and t.status<>'Draft'", (ph,))]
        v = con.execute("select coalesce(sum(hours),0), coalesce(sum(total),0) from v_ctr where phase_id=?", (ph,)).fetchone()
        con.close()
        self.assertTrue(fams)
        page = self.get("/ctr?u=29&phase=%d" % ph)
        self.assertIn("CTR summary", page)
        self.assertIn("%.2f" % v[1], page)
        for f in fams:
            self.assertIn(f, page)
            tab = self.get("/ctr?u=29&phase=%d&family=%s" % (ph, f.replace(" ", "%20")))
            self.assertIn("Resources", tab)
            self.assertIn("Timesheet entries", tab)
        self.assertIn("Open CTR", self.get("/reports?u=29"))
        self.assertIn("CTR not found", self.get("/ctr?u=29&phase=9999"))
        self.assertIn("CTR summary", self.get("/ctr?u=29&phase=%d&family=Nonsense" % ph))


if __name__ == "__main__":
    unittest.main()
