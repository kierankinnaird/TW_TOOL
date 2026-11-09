import sqlite3
"""Front-end smoke tests. Run from the repo root: python -m unittest discover -s tests -v"""
import datetime
import os
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
        for path in ["/", "/timesheet?u=5&w=2026-11-02", "/approvals?u=6", "/reports?u=5",
                     "/admin?u=7&t=users", "/admin?u=7&t=periods", "/audit?u=7"]:
            self.assertIn("NMC Timewriting", self.get(path), path)

    def test_timesheet_shows_cover_rows_and_totals(self):
        page = self.get("/timesheet?u=5&w=2026-11-02")
        self.assertIn("PM-04", page)
        self.assertIn("PM-02", page)
        self.assertIn("Week commencing 02/11/2026", page)

    def test_save_draft_then_submit_then_approve(self):
        # Ewan (5) books Friday 6 Nov on BB0001 (scope 1) as Planner (role 4)
        page = self.post("/timesheet/save", {"u": "5", "w": "2026-11-02", "action": "Save draft",
                                             "h_1_4_2026-11-06": "7.5", "reason": ""})
        self.assertIn("1 entry saved", page)
        page = self.post("/timesheet/save", {"u": "5", "w": "2026-11-02", "action": "Submit week", "reason": ""})
        self.assertIn("submitted for approval", page)
        con = app.db()
        eid = con.execute("select entry_id from time_entry where user_id=5 and work_date='2026-11-06'").fetchone()[0]
        self.assertEqual(con.execute("select status from time_entry where entry_id=?", (eid,)).fetchone()[0], "Submitted")
        con.close()
        # Rhona (6) is the approver of scope 1
        page = self.post("/approvals/act", {"u": "6", "entry_id": str(eid), "action": "Approve"})
        self.assertIn("approved", page)
        con = app.db()
        self.assertEqual(con.execute("select status, approved_by from time_entry where entry_id=?", (eid,)).fetchone()[:], ("Approved", 6))
        con.close()

    def test_wrong_person_cannot_approve_and_owner_cannot_self_approve(self):
        con = app.db()
        eid = con.execute("select entry_id from time_entry where status='Submitted' and scope_id=1 limit 1").fetchone()[0]
        owner = con.execute("select user_id from time_entry where entry_id=?", (eid,)).fetchone()[0]
        con.close()
        self.assertIn("scope approver or an admin", self.post("/approvals/act", {"u": "1", "entry_id": str(eid), "action": "Approve"}))
        self.assertIn("not their own", self.post("/approvals/act", {"u": str(owner), "entry_id": str(eid), "action": "Approve"}))

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
        # approved entry (Ewan, Tue 3 Nov) cannot be cleared
        page = self.post("/timesheet/save", {"u": "5", "w": "2026-11-02", "action": "Save draft",
                                             "h_1_4_2026-11-03": "", "reason": ""})
        self.assertIn("approved hours cannot be cleared", page)

    def test_admin_only_pages_and_period_actions(self):
        self.assertIn("Admin only", self.get("/admin?u=5"))
        # a far-future Monday, so it is never the current week (the current week opens by itself)
        page = self.post("/admin/period", {"u": "7", "action": "open", "week_start": "2030-01-07"})
        self.assertIn("opened", page)
        page = self.post("/admin/period", {"u": "7", "action": "open", "week_start": "2030-01-07"})  # already there
        self.assertIn("already exists", page)
        page = self.post("/admin/period", {"u": "7", "action": "open", "week_start": "2030-01-08"})  # not a Monday
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
        base = "/reports?u=5&from=2026-01-01&to=2026-12-31"
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
        base = "/reports?u=5&from=2026-01-01&to=2026-12-31"
        page = self.period_part(base + "&client=%d&project=%d" % (spirit, apache_project))
        self.assertIn(">Spirit</td>", page)  # the stray project is ignored, so Spirit still shows
        self.assertEqual(self.period_part(base + "&client=abc&scope=999"), self.period_part(base))


    # ---------- internal hours, automatic periods, live totals ----------
    def test_internal_hours_booked_without_a_paaf_and_reported_separately(self):
        # Fraser Gordon (4) has no approved PAAF for any client, but can still book to INT01 Business Services (phase 8)
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
        self.assertIn("Fraser Gordon", internal)
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
        self.assertIn("IT Services", self.get("/admin?u=7&t=internal"))
        self.assertIn("IT Services", self.get("/admin/edit?u=7&t=internal&id=%d" % sid))
        self.assertIn("Internal (not billed)", self.get("/admin?u=7&t=clients"))
        for t in ("rates", "paafs"):  # the internal client is not offered for rate cards or PAAFs
            self.assertNotIn(">Internal</option>", self.get("/admin?u=7&t=" + t))
        before = self.one("select count(*) from scope")[0]
        self.admin_post("/admin/add", {"t": "internal", "description": "Sneaky", "approver_user_id": "7"}, u="5")
        self.assertEqual(self.one("select count(*) from scope")[0], before)

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

    # ---------- timestamps ----------
    def test_audit_and_periods_show_times_to_the_second_only(self):
        self.post("/timesheet/save", {"u": "5", "w": "2026-11-02", "action": "Save draft", "h_1_4_2026-11-06": "7.5", "reason": ""})
        self.post("/timesheet/save", {"u": "5", "w": "2026-11-02", "action": "Submit week", "reason": ""})
        self.admin_post("/admin/period", {"action": "close", "week_start": "2026-11-02"})
        for path in ("/audit?u=7", "/admin?u=7&t=periods"):
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
    def admin_post(self, path, data, u="7"):
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
        for t in ["periods", "users", "roles", "clients", "rates", "paafs", "scopes"]:
            self.assertIn("Admin", self.get("/admin?u=7&t=%s" % t), t)
        for t, i in [("users", 1), ("roles", 1), ("scopes", 1)]:
            self.assertIn("Save changes", self.get("/admin/edit?u=7&t=%s&id=%d" % (t, i)), t)

    def test_non_admin_cannot_use_admin_forms(self):
        before = self.one("select count(*) from client")[0]
        self.admin_post("/admin/add", {"t": "clients", "name": "Sneaky"}, u="5")
        self.assertEqual(self.one("select count(*) from client")[0], before)
        self.admin_post("/admin/period", {"action": "open", "week_start": "2030-01-07"}, u="5")
        self.assertIsNone(self.one("select 1 from period where week_start='2030-01-07'"))
        self.assertIn("Admin only", self.get("/admin/edit?u=5&t=users&id=1"))

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

    def test_deactivated_role_is_hidden_from_new_timesheet_rows(self):
        self.admin_post("/admin/save", {"t": "roles", "id": "2", "skill_code": "EM-02", "name": "Engineering Manager", "family": "Engineering Management", "active": "0"})
        page = self.get("/timesheet?u=5&w=2026-11-02")
        self.assertNotIn("EM-02 Engineering Manager", page)
        self.assertIn("PM-02 Project Controls Manager", page)



    def test_user_switcher_shows_each_persons_job_title(self):
        page = self.get("/reports?u=7")
        for label in ["Alasdair Murray - Engineering Manager (admin)", "Hamish Lennox - Operations Superintendent",
                      "Lachlan Rennie - Subsea Pipeline Engineer", "Mairi Thomson - Lead Technical Safety Engineer"]:
            self.assertIn(label, page)

    def test_roles_tab_groups_job_titles_by_family(self):
        page = self.get("/admin?u=7&t=roles")
        for text in ["Job family", "Piping Engineering", "Technical Safety", "Lead Process Engineer", "Document Controller"]:
            self.assertIn(text, page)

    def test_reports_show_the_new_job_titles(self):
        page = self.get("/reports?u=7")
        for text in ["PM-04 Planner", "EM-01 Operations Superintendent", "PI-01 Lead Piping Engineer", "TS-01 Lead Technical Safety Engineer"]:
            self.assertIn(text, page)

    # ---------- Paul's demo feedback ----------
    def queue_ids(self, approver):
        con = app.db()
        try:
            where = "" if approver == 7 else "and s.approver_user_id=%d" % approver
            return [r[0] for r in con.execute("select t.entry_id from time_entry t join scope s on s.scope_id=t.scope_id "
                                              "where t.status='Submitted' %s" % where)]
        finally:
            con.close()

    def test_bulk_approve_selected_and_untick_one(self):
        ids = self.queue_ids(6)
        self.assertGreaterEqual(len(ids), 2)
        page = self.get("/approvals?u=6")
        self.assertIn("Select all", page)
        self.assertIn("Approve selected", page)
        keep = ids[0]                                           # "untick" one: leave it out of the post
        page = self.post("/approvals/act", {"u": "6", "entry_id": [str(i) for i in ids[1:]], "action": "Approve selected"})
        self.assertIn("%d entries approved" % (len(ids) - 1), page)
        self.assertEqual(self.one("select status from time_entry where entry_id=?", keep)[0], "Submitted")
        self.assertEqual(self.one("select count(*) from time_entry where entry_id in (%s) and status='Approved'"
                                  % ",".join(str(i) for i in ids[1:]))[0], len(ids) - 1)

    def test_bulk_reject_needs_reason_and_ticks(self):
        ids = self.queue_ids(6)
        self.assertIn("Tick at least one", self.post("/approvals/act", {"u": "6", "action": "Reject selected", "reason": "x"}))
        self.assertIn("Give a reason", self.post("/approvals/act", {"u": "6", "entry_id": [str(ids[0])], "action": "Reject selected", "reason": ""}))
        self.assertIn("sent back to draft", self.post("/approvals/act", {"u": "6", "entry_id": [str(i) for i in ids], "action": "Reject selected", "reason": "Wrong scope"}))
        self.assertEqual(self.one("select status, reject_reason from time_entry where entry_id=?", ids[0])[:], ("Draft", "Wrong scope"))

    def test_bulk_approve_reports_wrong_approver_without_stopping(self):
        ids = self.queue_ids(6)
        page = self.post("/approvals/act", {"u": "1", "entry_id": [str(i) for i in ids], "action": "Approve selected"})
        self.assertIn("scope approver or an admin", page)

    def test_timesheet_shows_total_for_all_statuses(self):
        page = self.get("/timesheet?u=5&w=2026-11-02")
        self.assertIn("all statuses", page)
        self.assertRegex(page, r"Approved [\d.]+, Edited [\d.]+, Submitted [\d.]+, Draft [\d.]+")

    def test_report_subtotals_sit_in_the_columns(self):
        page = self.get("/reports?u=5")
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
        page = self.get("/reports?u=5")
        for kind in ["Client", "Project", "Scope", "Phase", "Skill code", "Person", "Entry"]:
            self.assertIn("<td>%s</td>" % kind, page)
        for name in ["Wellbay Decommissioning", "BB0001 Wellbay Clearance", "Execute", "Ewan Mackay", "2026-11-03", "Morecambe Hub"]:
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
        # phase 2 = BB0001 Execute (Gregor has an approved Execute entry on 3 Nov), role 3 = Project Engineer
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
        page = self.get("/approvals?u=6")
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
        page = self.get("/ctr?u=1&phase=%d" % ph)
        self.assertIn("CTR summary", page)
        self.assertIn("%.2f" % v[1], page)
        for f in fams:
            self.assertIn(f, page)
            tab = self.get("/ctr?u=1&phase=%d&family=%s" % (ph, f.replace(" ", "%20")))
            self.assertIn("Resources", tab)
            self.assertIn("Timesheet entries", tab)
        self.assertIn("Open CTR", self.get("/reports?u=1"))
        self.assertIn("CTR not found", self.get("/ctr?u=1&phase=9999"))
        self.assertIn("CTR summary", self.get("/ctr?u=1&phase=%d&family=Nonsense" % ph))


if __name__ == "__main__":
    unittest.main()
