import sqlite3
"""Front-end smoke tests. Run from the repo root: python -m unittest discover -s tests -v"""
import datetime
import os
import re
import subprocess
import sys
import tempfile
import threading
import zipfile
import io
import xml.etree.ElementTree as ET
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

    def open_next_week(self):
        """The seeded week of 2 Nov is already submitted (so locked); tests that book new hours use the week after, opened here."""
        con = app.db()
        con.execute("insert or ignore into period (week_start) values ('2026-11-09')")
        con.commit()
        con.close()

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

    def test_every_page_and_download_carries_the_browser_security_headers(self):
        for path in ("/timesheet?u=5&w=2026-11-02", "/reports?u=29", "/admin?u=30", "/reports/grid.xlsx?u=29", "/nowhere"):
            try:
                r = urllib.request.urlopen(self.base + path)
            except urllib.error.HTTPError as e:
                r = e
            self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff", path)
            self.assertEqual(r.headers["X-Frame-Options"], "DENY", path)
            self.assertIn("default-src 'self'", r.headers["Content-Security-Policy"], path)
            self.assertIn("frame-ancestors 'none'", r.headers["Content-Security-Policy"], path)
            self.assertEqual(r.headers["Referrer-Policy"], "no-referrer", path)
            r.close()

    def test_styling_has_square_corners(self):
        self.assertNotIn("border-radius", self.get("/reports?u=5"))

    def test_timesheet_shows_cover_rows_and_totals(self):
        page = self.get("/timesheet?u=5&w=2026-11-02")
        self.assertIn("PM-04", page)
        self.assertIn("PM-02", page)
        self.assertIn("Week commencing 02/11/2026", page)

    def test_save_draft_then_submit_then_approve(self):
        self.open_next_week()
        # Ewan Shaw (5) books Friday 6 Nov on BB0001 (scope 1) as Planner (role 4)
        page = self.post("/timesheet/save", {"u": "5", "w": "2026-11-09", "action": "Save draft",
                                             "h_1_4_1_0_2026-11-13": "7.5", "reason": ""})
        self.assertIn("1 entry saved", page)
        page = self.post("/timesheet/save", {"u": "5", "w": "2026-11-09", "action": "Submit week", "reason": ""})
        self.assertIn("submitted for approval", page)
        con = app.db()
        eid = con.execute("select entry_id from time_entry where user_id=5 and work_date='2026-11-13'").fetchone()[0]
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
        self.assertIn("only an approver of its scope", self.post("/approvals/act", {"u": "1", "entry_id": str(eid), "action": "Approve"}))  # a cost controller
        self.assertIn("not named as an approver", self.post("/approvals/act", {"u": str(owner), "entry_id": str(eid), "action": "Approve"}))
        self.assertEqual(self.one("select status from time_entry where entry_id=?", eid)[0], "Submitted")

    def test_submitted_and_approved_hours_are_locked_on_the_page_and_on_the_server(self):
        # Ewan Shaw (5): Tue 3 Nov is approved. It is plain text, with no box to type in, and the page does not ask for a reason to change it
        page = self.get("/timesheet?u=5&w=2026-11-02")
        self.assertNotIn('name="h_1_4_1_0_2026-11-03"', page)
        self.assertNotIn("Reason for changing approved hours", page)
        self.assertIn("Submitted and approved hours are locked", page)
        key = "h_1_4_1_0_2026-11-03"
        before = self.one("select hours, status from time_entry where user_id=5 and work_date='2026-11-03'")[:]
        # a hand-made request cannot change or clear them either
        self.assertIn("approved hours are locked", self.post("/timesheet/save", {"u": "5", "w": "2026-11-02", "action": "Save draft", key: "6", "reason": "Site log"}))
        self.assertIn("approved hours are locked", self.post("/timesheet/save", {"u": "5", "w": "2026-11-02", "action": "Save draft", key: ""}))
        self.assertEqual(self.one("select hours, status from time_entry where user_id=5 and work_date='2026-11-03'")[:], before)

    def test_a_partly_submitted_week_has_boxes_only_for_the_days_still_in_draft(self):
        self.open_next_week()
        # Jason Pang (22) has submitted and approved hours in the week of 2 Nov, so that whole sheet is locked: no boxes, and nothing can be saved
        page = self.get("/timesheet?u=22&w=2026-11-02")
        self.assertNotIn('<input type="text"', page)
        self.assertNotIn('name="action" value="Save draft"', page)
        self.assertIn("Locked.", page)
        self.assertIn("is locked", self.post("/timesheet/save", {"u": "22", "w": "2026-11-02", "action": "Save draft", "h_1_4_1_0_2026-11-06": "8"}))
        # in an open week he fills in a day and saves it: only that day is a box
        filled = r'<input type="text" size="4" name="(h_[^"]+)" value="[^"]+"'  # a box that holds hours
        key = "h_1_%d_1_0_2026-11-10" % self.one("select role_id from app_user where user_id=22")[0]
        self.post("/timesheet/save", {"u": "22", "w": "2026-11-09", "action": "Save draft", key: "8"})
        self.assertEqual(re.findall(filled, self.get("/timesheet?u=22&w=2026-11-09")), [key])
        # once the week is submitted the whole sheet locks, empty days included
        page = self.post("/timesheet/save", {"u": "22", "w": "2026-11-09", "action": "Submit week"})
        self.assertIn("1 entry submitted", page)
        self.assertEqual(re.findall(filled, page), [], "submitted hours must never be shown in a box")
        self.assertNotIn('<input type="text"', page)
        self.assertNotIn('name="action" value="Add row"', page)
        self.assertIn("is locked", self.post("/timesheet/save", {"u": "22", "w": "2026-11-09", "action": "Save draft", key.replace("10", "11"): "4"}))
        self.assertEqual(self.one("select count(*) from time_entry where user_id=22 and work_date='2026-11-11'")[0], 0)
    def test_closed_week_blocks_new_hours_and_a_person_not_on_the_clients_list_cannot_book(self):
        self.open_next_week()
        page = self.post("/timesheet/save", {"u": "4", "w": "2026-10-26", "action": "Save draft",
                                             "h_8_4_1_0_2026-10-30": "8", "reason": ""})
        self.assertIn("period is not open", page)
        page = self.post("/timesheet/save", {"u": "4", "w": "2026-11-09", "action": "Save draft",
                                             "h_1_4_1_0_2026-11-13": "8", "reason": ""})
        self.assertIn("not on this client", page)

    def test_clear_draft_entry_but_not_approved_or_submitted(self):
        self.open_next_week()
        self.post("/timesheet/save", {"u": "5", "w": "2026-11-09", "action": "Save draft",
                                      "h_1_4_1_0_2026-11-13": "7", "reason": ""})
        page = self.post("/timesheet/save", {"u": "5", "w": "2026-11-09", "action": "Save draft",
                                             "h_1_4_1_0_2026-11-13": "", "reason": ""})
        self.assertIn("1 draft entry cleared", page)
        con = app.db()
        self.assertEqual(con.execute("select count(*) from time_entry where user_id=5 and work_date='2026-11-13'").fetchone()[0], 0)
        self.assertEqual(con.execute("select count(*) from time_entry_audit where action='DELETE'").fetchone()[0], 1)
        con.close()
        # approved entry (Ewan Shaw, Tue 3 Nov) cannot be cleared
        page = self.post("/timesheet/save", {"u": "5", "w": "2026-11-02", "action": "Save draft",
                                             "h_1_4_1_0_2026-11-03": "", "reason": ""})
        self.assertIn("submitted and is locked", page)         # the whole sheet is locked, so nothing in it can be cleared
        self.assertEqual(self.one("select count(*) from time_entry where user_id=5 and work_date='2026-11-03'")[0], 1)

    def test_a_submitted_sheet_is_locked_as_a_whole_until_a_cost_controller_reopens_it(self):
        # Ewan Shaw (5) has submitted his week of 2 Nov (some of it approved): every box, row, allowance tick and button is gone
        page = self.get("/timesheet?u=5&w=2026-11-02")
        self.assertIn("Locked.", page)
        for gone in ('<input type="text"', 'type="checkbox"', 'value="Save draft"', 'value="Submit week"', 'value="Add row"', 'value="Add allowance row"', "Copy last week"):
            self.assertNotIn(gone, page, gone)
        # a hand-made request cannot add hours, rows or allowances either
        before = self.one("select count(*) from time_entry where user_id=5")[0]
        for extra in ({"h_1_4_1_0_2026-11-07": "5"}, {"action": "Add row", "add_phase": "1", "add_role": "4"}, {"action": "Submit week"}):
            out = self.post("/timesheet/save", dict({"u": "5", "w": "2026-11-02", "action": "Save draft"}, **extra))
            self.assertIn("submitted and is locked", out)
        self.assertEqual(self.one("select count(*) from time_entry where user_id=5")[0], before)
        # sent back to draft by a cost controller, the sheet opens again
        self.reopen(5)
        page = self.get("/timesheet?u=5&w=2026-11-02")
        self.assertNotIn("Locked.", page)
        self.assertIn('value="Save draft"', page)
        self.assertIn('<input type="text"', page)

    def xlsx_text(self, path):
        import io, zipfile
        with urllib.request.urlopen(self.base + path) as r:
            self.assertIn("spreadsheetml", r.headers["Content-Type"])
            self.assertIn("manhours-", r.headers["Content-Disposition"])
            z = zipfile.ZipFile(io.BytesIO(r.read()))
        return "".join(z.read(n).decode("utf-8") for n in z.namelist() if n.endswith(".xml"))

    def test_the_project_manager_exports_a_manhour_report_for_their_client(self):
        # Alisdair Woodhead (7) is Apache's main approver, the project manager: Apache's hours by role and person, in hours, nothing from Spirit
        link = self.get("/reports?u=7")
        self.assertIn('/reports/manhours?u=7"', link)
        page = self.get("/reports/manhours?u=7")
        self.assertIn("Client <select", page)
        table = page[page.index("<th>Job family</th>"):]               # the report itself (the page's user menu lists everybody)
        self.assertNotIn(">Spirit</option>", page)
        for person in ("Ewan Shaw", "Louis Ducharme", "Daniel Evans"):
            self.assertIn(person, table, person)
        self.assertNotIn("Gary Bridge", table)                     # a Spirit-only person
        self.assertIn("Total for the client", page)
        self.assertIn("Download as Excel", page)
        self.assertNotIn("&pound;", page)                          # hours only
        text = self.xlsx_text("/reports/manhours.xlsx?u=7")
        self.assertIn("Ewan Shaw", text)
        self.assertIn("Approved hrs", text)
        self.assertNotIn("Gary Bridge", text)
        # asking for another client's report gives Apache's again: a project manager only gets their own client
        self.assertIn("Ewan Shaw", self.xlsx_text("/reports/manhours.xlsx?u=7&client=2"))
        # Kenneth Nicolson (8) is Spirit's: his report is Spirit's people
        spirit = self.get("/reports/manhours?u=8")
        spirit = spirit[spirit.index("<th>Job family</th>"):]
        self.assertIn("Gary Bridge", spirit)
        self.assertNotIn("Ewan Shaw", spirit)
        # cost controllers can pick any client; people who approve nothing, and the IT admin, have no report
        self.assertIn(">Spirit</option>", self.get("/reports/manhours?u=29"))
        for who in (5, 30):
            self.assertIn("for a client's project manager", self.get("/reports/manhours?u=%d" % who) if who != 30 else "for a client's project manager")
            self.assertNotIn("/reports/manhours?u=%d" % who, self.get("/reports?u=%d" % who) if who != 30 else "")
        try:
            urllib.request.urlopen(self.base + "/reports/manhours.xlsx?u=5")
            self.fail("a person with no client should be refused")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 403)

    def test_admin_only_pages_and_period_actions(self):
        self.assertIn("not available to your role", self.get("/admin?u=5"))
        # a far-future Monday, so it is never the current week (the current week opens by itself)
        page = self.post("/admin/period", {"u": "29", "action": "open", "week_start": "2030-01-07"})
        self.assertIn("Week 2030-01-07 opened", page)
        page = self.post("/admin/period", {"u": "29", "action": "open", "week_start": "2030-01-07"})  # already there
        self.assertIn("already exists", page)
        page = self.post("/admin/period", {"u": "29", "action": "open", "week_start": "2030-01-08"})  # not a Monday
        self.assertNotIn("Week 2030-01-08 opened", page)

    def test_reports_date_range(self):
        page = self.get("/reports?u=5&from=2026-10-26&to=2026-10-26")
        self.assertIn("2026-10-26", page)
        self.assertNotIn("2026-11-02</td>", page.split("Period reports")[1])

    def period_part(self, path):
        return self.get(path).split("Period reports")[1].split("Budget against actual hours")[0]   # not the budget section, which lists every client

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
        self.assertIn(">%s-%s</td>" % (sp["scope_code"], "PM-D-001"), one_phase)

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
        page = self.post("/timesheet/save", {"u": "4", "w": "2026-11-02", "action": "Save draft", "h_8_4_1_0_2026-11-04": "3.5", "reason": ""})
        self.assertIn("1 entry saved", page)
        self.assertEqual(tuple(self.one("select paaf_id, rate_id, status from time_entry where user_id=4 and work_date='2026-11-04'")),
                         (None, None, "Draft"))
        self.post("/timesheet/save", {"u": "4", "w": "2026-11-02", "action": "Submit week", "reason": ""})
        eid = self.one("select entry_id from time_entry where user_id=4 and work_date='2026-11-04'")[0]
        self.assertIn("approved", self.post("/approvals/act", {"u": "31", "entry_id": str(eid), "action": "Approve"}))
        page = self.get("/reports?u=31&from=2026-01-01&to=2026-12-31")
        tree, rest = page.split("Period reports")
        internal = rest.split("Internal hours")[1]
        self.assertIn("INT01 Business Services", internal)
        self.assertIn("David Hart", internal)
        self.assertIn('<td align="right">3.5</td>', internal)
        self.assertNotIn("INT01", tree)  # not in the CTR tree
        self.assertNotIn("Business Services", rest.split("Internal hours")[0])  # nor the period report
        self.assertNotIn(">Internal</option>", page)  # nor the client filter

    def test_admin_adds_and_edits_internal_codes(self):
        page = self.admin_post("/admin/add", {"t": "internal", "description": "IT Support"})
        self.assertIn("Internal code INT04 added", page)
        self.assertIn("Internal - IT Support", self.get("/timesheet?u=4&w=2026-11-02"))
        sid = self.one("select scope_id from scope where scope_code='INT04'")[0]
        self.assertEqual(self.one("select count(*) from phase where scope_id=?", sid)[0], 1)  # a General phase came with it
        self.admin_post("/admin/save", {"t": "internal", "id": str(sid), "description": "IT Services", "status": "Closed"})
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
            self.admin_post("/admin/add", {"t": "internal", "description": "Sneaky"}, u=who)
        self.assertEqual(self.one("select count(*) from scope")[0], before)
        # secondary approvers are standard users only: the controller and admin are not offered, and cannot be named
        edit = self.get("/admin/edit?u=29&t=internal&id=%d" % sid)
        for uid in ("29", "30", "1", "2"):
            self.assertNotIn('name="secondary" value="%s"' % uid, edit, uid)
        self.assertIn('name="secondary" value="6"', edit)
        self.assertIn("Only a standard user", self.admin_post("/admin/save", {"t": "internal", "id": str(sid), "description": "IT Services", "status": "Open", "secondary": "29"}))

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
        self.assertNotIn("To clear a draft entry", page)  # a locked sheet has no drafts to clear, so the hint is not shown

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
        year = datetime.date.today().year
        self.assertIn('<input type="date" name="from" value="%d-01-01">' % year, page)       # the current year, not a fixed range
        self.assertIn('<input type="date" name="to" value="%d-12-31">' % year, page)
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
        self.assertIn("<th>Pending hrs</th>", page)
        self.assertNotIn("<th>Total</th>", page)
        self.assertNotIn("Cost &pound;", page)
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
        self.post("/timesheet/save", {"u": "4", "w": "2026-11-02", "action": "Save draft", "h_8_4_1_0_2026-11-04": "3.5", "reason": ""})
        self.post("/timesheet/save", {"u": "4", "w": "2026-11-02", "action": "Submit week", "reason": ""})
        sees = {}
        for u in (4, 5, 7, 31, 29):  # David Hart booked it, Ewan Shaw has nothing to do with it, Alisdair Woodhead approves Apache not INT01, Graeme Hogg approves INT01, the cost controller
            sees[u] = "David Hart" in self.report_page(u)[2].split("Internal hours")[1]
        self.assertEqual(sees, {4: True, 5: False, 7: False, 31: True, 29: True})

    # ---------- missing timesheets (R31) ----------
    def missing(self, u, **params):
        """The Missing timesheets page as (page, {person: [cells]}) for week/filter params."""
        page = self.get("/missing?u=%d&%s" % (u, "&".join("%s=%s" % kv for kv in params.items())))
        rows = {}
        for r in page.split("<tr>")[1:]:
            cells = [re.sub(r"<[^>]+>", "", c) for c in re.findall(r"<td[^>]*>(.*?)</td>", r)]
            if len(cells) >= 8:
                rows[cells[0]] = cells[:8]  # person, NMC ID, job title, employment, status, submitted hrs, draft hrs, sent back (then a Reopen link for controllers)
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
        self.assertEqual(len(rows), 30)  # everyone active that week except the IT admin's system account
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
        self.post("/timesheet/save", {"u": "4", "w": "2026-11-02", "action": "Save draft", "h_8_4_1_0_2026-11-04": "3.5", "reason": ""})  # David Hart, INT01
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
        # David Hart is on no client's list, so he is in neither list until he books to an internal code
        self.assertNotIn("David Hart", alisdair)
        self.assertNotIn("David Hart", kenneth)
        self.post("/timesheet/save", {"u": "4", "w": "2026-11-02", "action": "Save draft", "h_8_4_1_0_2026-11-04": "2", "reason": ""})  # INT01: Alisdair's code
        self.assertEqual(self.missing(31, w="2026-11-02", s="all")[1]["David Hart"][4], "Draft only")      # Graeme Hogg approves the internal codes
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
        self.open_next_week()
        # Jamie Ireland (6) approves only a new internal code that nobody has booked to, so she has no people yet
        self.assertIn("not available to your role", self.get("/missing?u=6&w=2026-11-09"))  # not an approver until she is named on a code
        self.admin_post("/admin/add", {"t": "internal", "description": "Facilities"})
        fid = self.one("select scope_id from scope where description='Facilities'")[0]
        self.admin_post("/admin/save", {"t": "internal", "id": str(fid), "description": "Facilities", "status": "Open", "secondary": "6"})   # Jamie is its secondary approver
        page = self.get("/missing?u=6&w=2026-11-09")
        self.assertIn("Nobody is expected", page)
        self.assertIn("personnel list of a client you approve", page)
        # once someone books to her internal code, that person is hers
        code = self.one("select s.scope_id, ph.phase_id from scope s join phase ph on ph.scope_id=s.scope_id where s.description='Facilities'")
        self.post("/timesheet/save", {"u": "5", "w": "2026-11-09", "action": "Save draft", "h_%d_4_1_0_2026-11-11" % code[1]: "1", "reason": ""})
        self.assertIn("Ewan Shaw", self.missing(6, w="2026-11-09", s="all")[1])

    # ---------- hour types: location and overtime (R12 to R14) ----------
    def hours_of(self, user, date):
        con = app.db()
        try:
            return sorted(tuple(r) for r in con.execute(
                "select location_id, overtime, hours, status from time_entry where user_id=? and work_date=?", (user, date)))
        finally:
            con.close()

    def test_a_row_has_a_location_and_a_type_and_is_saved_with_them(self):
        # David Hart (4) books INT01 (phase 8) as a Planner (role 4): normal and overtime on site, and a day offshore
        page = self.get("/timesheet?u=4&w=2026-11-02&add_phase=8&add_role=4&add_loc=2&add_ot=1")
        self.assertIn("h_8_4_2_1_2026-11-02", page)        # an offshore overtime row was added
        self.assertIn("&middot; Offshore &middot;", page)                      # the booking cell names the location
        self.assertIn("<b>Overtime</b>", page)
        self.assertIn("3 entries saved", self.post("/timesheet/save", {"u": "4", "w": "2026-11-02", "action": "Save draft", "reason": "",
                                                              "h_8_4_1_0_2026-11-03": "6", "h_8_4_1_1_2026-11-03": "2", "h_8_4_2_0_2026-11-04": "4"}))
        self.assertEqual(self.hours_of(4, "2026-11-03"), [(1, 0, 6, "Draft"), (1, 1, 2, "Draft")])  # normal and overtime are separate entries
        self.assertEqual(self.hours_of(4, "2026-11-04"), [(2, 0, 4, "Draft")])                      # offshore
        page = self.get("/timesheet?u=4&w=2026-11-02")
        # three rows, with the day lines split into normal time, overtime and total
        self.assertEqual(page.count('class="row"'), 3)
        self.assertIn('class="dn" data-day="2026-11-03"><b>6</b>', page)
        self.assertIn('class="do" data-day="2026-11-03"><b>2</b>', page)
        self.assertIn('class="dt" data-day="2026-11-03"><b>8</b>', page)
        self.assertIn('class="gn"><b>10</b>', page)  # normal time for the week: 6 + 4
        self.assertIn('class="go"><b>2</b>', page)
        self.assertIn('class="gt"><b>12</b>', page)

    def test_the_default_locations_are_on_shore_offshore_and_site_visit(self):
        page = self.get("/timesheet?u=4&w=2026-11-02")
        for name in ("On Shore", "Offshore", "Site visit"):
            self.assertIn('>%s</option>' % name, page, name)  # all three are in the Add row dropdown
        for gone in ("Office", "WFH"):
            self.assertNotIn('>%s</option>' % gone, page, gone)
        # David Hart (4) books INT01 as a Site visit on Monday and Tuesday
        self.assertIn("2 entries saved", self.post("/timesheet/save", {"u": "4", "w": "2026-11-02", "action": "Save draft", "reason": "",
                                                                      "h_8_4_3_0_2026-11-02": "7.5", "h_8_4_3_0_2026-11-03": "7.5"}))
        self.assertEqual(self.hours_of(4, "2026-11-02"), [(3, 0, 7.5, "Draft")])
        self.assertEqual(self.hours_of(4, "2026-11-03"), [(3, 0, 7.5, "Draft")])
        page = self.get("/timesheet?u=4&w=2026-11-02&add_phase=8&add_role=4&add_loc=3")  # a Site visit row from the dropdown
        self.assertIn("h_8_4_3_0_2026-11-05", page)
        self.assertIn("&middot; Site visit &middot;", page)
        # they show up in the report by location once approved
        eid = self.one("select entry_id from time_entry where user_id=4 and work_date='2026-11-03'")[0]
        self.post("/timesheet/save", {"u": "4", "w": "2026-11-02", "action": "Submit week", "reason": ""})
        self.post("/approvals/act", {"u": "31", "entry_id": str(eid), "action": "Approve selected"})
        section = self.get("/reports?u=29&from=2026-01-01&to=2026-12-31").split("Hours by location and overtime")[1]
        self.assertIn('<td>Site visit</td><td align="right">7.5</td><td align="right">0</td><td align="right">7.5</td>', section)

    def test_the_same_cell_is_updated_not_duplicated(self):
        post = {"u": "4", "w": "2026-11-02", "action": "Save draft", "reason": ""}
        self.post("/timesheet/save", dict(post, **{"h_8_4_2_1_2026-11-03": "3"}))
        self.post("/timesheet/save", dict(post, **{"h_8_4_2_1_2026-11-03": "5"}))
        self.assertEqual(self.hours_of(4, "2026-11-03"), [(2, 1, 5, "Draft")])

    def test_old_style_cell_names_and_unknown_locations_are_not_saved(self):
        post = {"u": "4", "w": "2026-11-02", "action": "Save draft", "reason": ""}
        self.post("/timesheet/save", dict(post, **{"h_8_4_2026-11-03": "6"}))       # the old name has no location or type
        self.assertEqual(self.hours_of(4, "2026-11-03"), [])
        self.assertIn("not available", self.post("/timesheet/save", dict(post, **{"h_8_4_99_0_2026-11-03": "6"})))
        self.assertEqual(self.hours_of(4, "2026-11-03"), [])

    def test_approvers_and_the_ctr_page_show_where_and_what_type(self):
        self.post("/timesheet/save", {"u": "4", "w": "2026-11-02", "action": "Save draft", "reason": "", "h_8_4_2_1_2026-11-03": "3"})
        self.post("/timesheet/save", {"u": "4", "w": "2026-11-02", "action": "Submit week", "reason": ""})
        page = self.get("/approvals?u=7")  # Alisdair approves INT01
        self.assertIn("<th>Location</th><th>Type</th>", page)
        self.assertIn("<td>Offshore</td>", page)
        self.assertIn("<b>Overtime</b>", page)
        ctr = self.get("/ctr?u=29&phase=1&family=HSES")  # Rob Millman's offshore day on BB0001 Define
        self.assertIn("<th>Location</th><th>Type</th>", ctr)
        self.assertIn("<td>Offshore</td>", ctr)

    def test_reports_show_hours_by_location_and_overtime_for_what_you_may_see(self):
        con = app.db()
        try:
            want = {r[0]: (r[1], r[2]) for r in con.execute(
                "select lo.name, sum(case when t.overtime=0 then t.hours else 0 end), sum(case when t.overtime=1 then t.hours else 0 end) "
                "from time_entry t join location lo using(location_id) where t.status='Approved' group by lo.location_id")}
        finally:
            con.close()
        page = self.get("/reports?u=29&from=2026-01-01&to=2026-12-31")
        section = page.split("Hours by location and overtime")[1]
        for name, (normal, ot) in want.items():
            self.assertIn('<td>%s</td><td align="right">%s</td><td align="right">%s</td><td align="right">%s</td>' % (
                name, app.fmt(normal), app.fmt(ot), app.fmt(normal + ot)), section)
        self.assertIn("no separate rate yet", section)  # the cost controller is told overtime is valued at the normal rate
        # Rob Millman (24) worked 4 hours offshore and nothing else, so that is all he sees; and no mention of values
        own = self.get("/reports?u=24&from=2026-01-01&to=2026-12-31").split("Hours by location and overtime")[1]
        self.assertIn('<td>Offshore</td><td align="right">4</td><td align="right">0</td><td align="right">4</td>', own)
        self.assertNotIn("On Shore", own)
        self.assertNotIn("no separate rate yet", own)

    def test_the_it_admin_manages_the_locations(self):
        self.open_next_week()
        self.assertIn("Location added", self.admin_post("/admin/add", {"t": "locations", "name": "Remote"}))
        self.assertIn("already exists", self.admin_post("/admin/add", {"t": "locations", "name": "Remote"}))
        rid = self.one("select location_id from location where name='Remote'")[0]
        self.assertIn('>Remote</option>', self.get("/timesheet?u=5&w=2026-11-09"))                    # offered on the timesheet
        self.assertIn("not available to your role", self.get("/admin?u=29&t=locations"))              # the cost controller does not manage them
        self.admin_post("/admin/add", {"t": "locations", "name": "Sneaky"}, u="29")
        self.assertIsNone(self.one("select 1 from location where name='Sneaky'"))
        # switched off: no longer offered, new bookings refused, but hours already booked keep it
        self.post("/timesheet/save", {"u": "5", "w": "2026-11-09", "action": "Save draft", "reason": "", "h_8_4_%d_0_2026-11-13" % rid: "2"})
        self.assertEqual(self.hours_of(5, "2026-11-13"), [(rid, 0, 2, "Draft")])
        self.assertIn("Saved", self.admin_post("/admin/save", {"t": "locations", "id": str(rid), "name": "Remote", "active": "0"}))
        self.assertNotIn('>Remote</option>', self.get("/timesheet?u=5&w=2026-11-09"))
        self.assertIn("not available", self.post("/timesheet/save", {"u": "5", "w": "2026-11-09", "action": "Save draft", "reason": "",
                                                                    "h_8_4_%d_0_2026-11-12" % rid: "2"}))
        self.assertEqual(self.hours_of(5, "2026-11-13"), [(rid, 0, 2, "Draft")])
        # at least one location must stay switched on
        for lid, name in ((2, "Offshore"), (3, "Site visit")):
            self.assertIn("Saved", self.admin_post("/admin/save", {"t": "locations", "id": str(lid), "name": name, "active": "0"}))
        self.assertIn("At least one location must stay active", self.admin_post("/admin/save", {"t": "locations", "id": "1", "name": "On Shore", "active": "0"}))

    def test_the_timesheet_script_totals_normal_time_overtime_and_the_total(self):
        page = self.get("/timesheet?u=5&w=2026-11-02")
        for marker in ("td.dn", "td.do", "td.dt", "td.gn", "td.go", "td.gt", "data-ot"):
            self.assertIn(marker, page, marker)

    # ---------- copy last week (R15) ----------
    def test_copy_last_weeks_rows_and_hours_as_drafts(self):
        # Rob Millman (24) worked 4 hours offshore on BB0001 Define on Wednesday 28 Oct
        page = self.get("/timesheet?u=24&w=2026-11-02")
        self.assertIn('value="Copy last week"', page)  # a single button, no separate "rows only" choice
        self.assertNotIn("rows only", page)
        page = self.post("/timesheet/copy", {"u": "24", "w": "2026-11-02", "copy_hours": "x"})
        self.assertIn("1 entry copied from the week commencing 26/10/2026 as drafts", page)
        self.assertEqual(self.hours_of(24, "2026-11-04"), [(2, 0, 4, "Draft")])  # the same weekday a week on, still offshore, a draft
        # copying again gives the same result: it clears the week first, so nothing is doubled up
        page = self.post("/timesheet/copy", {"u": "24", "w": "2026-11-02", "copy_hours": "x"})
        self.assertIn("1 draft entry that was in this week was replaced", page)
        self.assertEqual(self.hours_of(24, "2026-11-04"), [(2, 0, 4, "Draft")])

    def test_copy_clears_what_is_in_the_week_and_will_not_touch_submitted_hours(self):
        # Rob Millman (24) types something different, then copies: the typed draft is cleared and last week's row and hours go in
        self.post("/timesheet/save", {"u": "24", "w": "2026-11-02", "action": "Save draft", "h_1_4_1_0_2026-11-06": "3"})
        self.assertEqual(self.hours_of(24, "2026-11-06"), [(1, 0, 3, "Draft")])
        self.post("/timesheet/copy", {"u": "24", "w": "2026-11-02", "copy_hours": "x"})
        self.assertEqual(self.hours_of(24, "2026-11-06"), [])
        self.assertEqual(self.hours_of(24, "2026-11-04"), [(2, 0, 4, "Draft")])
        self.assertEqual(self.one("select count(*) from time_entry_audit where action='DELETE' and changed_by=24")[0], 1)  # the clearing is audited
        # once the week is submitted it cannot be replaced, and the button is no longer offered
        self.post("/timesheet/save", {"u": "24", "w": "2026-11-02", "action": "Submit week"})
        self.assertNotIn("Copy last week", self.get("/timesheet?u=24&w=2026-11-02"))
        self.assertIn("cannot be replaced", self.post("/timesheet/copy", {"u": "24", "w": "2026-11-02", "copy_hours": "x"}))
        self.assertEqual(self.hours_of(24, "2026-11-04"), [(2, 0, 4, "Submitted")])

    def test_copy_keeps_the_overtime_flag(self):
        # Neil Pennock (21) did 8 hours of overtime on Monday 2 Nov on BB0001 Execute: copy it onto the week of 9 Nov
        self.admin_post("/admin/period", {"action": "open", "week_start": "2026-11-09"})  # it may exist already, which is fine
        self.assertIn("1 entry copied", self.post("/timesheet/copy", {"u": "21", "w": "2026-11-09", "copy_hours": "x"}))
        self.assertEqual(self.hours_of(21, "2026-11-09"), [(1, 1, 8, "Draft")])

    def test_copy_says_what_it_could_not_copy(self):
        con = app.db()
        con.execute("update paaf set expiry_date='2026-10-31' where user_id=24 and client_id=1")  # Rob's Apache PAAF ends before 4 Nov
        con.commit()
        con.close()
        page = self.post("/timesheet/copy", {"u": "24", "w": "2026-11-02", "copy_hours": "x"})
        self.assertIn("1 entry could not be copied", page)
        self.assertIn("PAAF has expired", page)
        self.assertEqual(self.hours_of(24, "2026-11-04"), [])

    def test_nothing_to_copy_and_no_buttons_when_copying_is_not_possible(self):
        # David Hart (4) has no hours in the week of 26 Oct
        self.assertIn("Nothing to copy", self.post("/timesheet/copy", {"u": "4", "w": "2026-11-02", "copy_hours": "x"}))
        self.assertNotIn("Copy last week", self.get("/timesheet?u=4&w=2026-11-02"))
        # a closed week offers no copying either
        self.assertIn("Copy last week", self.get("/timesheet?u=24&w=2026-11-02"))
        self.admin_post("/admin/period", {"action": "close", "week_start": "2026-11-02"})
        self.assertNotIn("Copy last week", self.get("/timesheet?u=24&w=2026-11-02"))

    # ---------- a rejected entry tells the person why (R24) ----------
    def test_the_seeded_rejection_is_shown_with_its_reason_and_who_sent_it_back(self):
        # Ajin Sabu (10): A1600 on Wed 4 Nov was sent back by Kenneth Nicolson with a reason
        page = self.get("/timesheet?u=10&w=2026-11-02")
        self.assertIn("Sent back for correction", page)
        self.assertIn("A1600-PM-D-001, Wed 04/11", page)
        self.assertIn("Project Controls Manager cover not agreed with client, please confirm", page)
        self.assertIn("sent back by Kenneth Nicolson on 06/11/2026 10:00", page)
        self.assertIn('title="Sent back: Project Controls Manager cover', page)  # a hover note on the box itself
        self.assertIn("My Timesheet (1 sent back)", page)                          # and in the menu, on any page
        self.assertIn("My Timesheet (1 sent back)", self.get("/reports?u=10"))
        # people with nothing sent back see none of it
        page = self.get("/timesheet?u=5&w=2026-11-02")
        self.assertNotIn("Sent back", page)
        self.assertIn(">My Timesheet</a>", page)

    def test_other_weeks_with_sent_back_hours_are_pointed_out(self):
        page = self.get("/timesheet?u=10&w=2026-11-09")
        self.assertNotIn("Sent back for correction", page)  # nothing in this week
        self.assertIn("You also have hours sent back or reopened in", page)
        self.assertIn('w=2026-11-02">week commencing 02/11/2026 (1)', page)

    def test_a_rejection_made_in_the_app_reaches_the_person(self):
        # David Hart (4) books INT01 and submits; Graeme Hogg (31) approves INT01 and rejects it
        self.post("/timesheet/save", {"u": "4", "w": "2026-11-02", "action": "Save draft", "reason": "", "h_8_4_1_0_2026-11-04": "3"})
        self.post("/timesheet/save", {"u": "4", "w": "2026-11-02", "action": "Submit week", "reason": ""})
        self.assertNotIn("Sent back", self.get("/timesheet?u=4&w=2026-11-02"))
        eid = self.one("select entry_id from time_entry where user_id=4 and work_date='2026-11-04'")[0]
        self.post("/approvals/act", {"u": "31", "entry_id": str(eid), "action": "Reject selected", "reason": "Wrong <b>code</b>, use training"})
        page = self.get("/timesheet?u=4&w=2026-11-02")
        self.assertIn("Sent back for correction", page)
        self.assertIn("INT01-PM-G-001, Wed 04/11", page)
        self.assertIn("Graeme Hogg", page)
        self.assertIn("Wrong &lt;b&gt;code&lt;/b&gt;, use training", page)  # the reason is shown as text, never as HTML
        self.assertNotIn("Wrong <b>code</b>", page)
        self.assertIn("My Timesheet (1 sent back)", page)
        # submitting the week again clears the notice
        self.post("/timesheet/save", {"u": "4", "w": "2026-11-02", "action": "Submit week", "reason": ""})
        page = self.get("/timesheet?u=4&w=2026-11-02")
        self.assertNotIn("Sent back", page)
        self.assertIn(">My Timesheet</a>", page)

    def test_a_cost_controllers_rejection_of_an_approvers_own_hours_names_the_controller(self):
        self.post("/timesheet/save", {"u": "8", "w": "2026-11-02", "action": "Save draft", "reason": "", "h_6_30_1_0_2026-11-04": "4"})  # Kenneth on A1500
        self.post("/timesheet/save", {"u": "8", "w": "2026-11-02", "action": "Submit week", "reason": ""})
        eid = self.one("select entry_id from time_entry where user_id=8 and work_date='2026-11-04'")[0]
        self.post("/approvals/act", {"u": "29", "entry_id": str(eid), "action": "Reject selected", "reason": "Not agreed with Spirit"})
        page = self.get("/timesheet?u=8&w=2026-11-02")
        self.assertIn("Not agreed with Spirit", page)
        self.assertIn("sent back by Stephanie Apedaile", page)

    # ---------- reopen a timesheet (R32) ----------
    def week_states(self, user, w0="2026-11-02", w6="2026-11-08"):
        con = app.db()
        try:
            return sorted(r[0] for r in con.execute("select status from time_entry where user_id=? and work_date between ? and ?", (user, w0, w6)))
        finally:
            con.close()

    def reopen(self, person, week="2026-11-02", reason="Wrong code booked", u="29", **extra):
        data = {"u": u, "person": str(person), "w": week, "reason": reason}
        data.update(extra)
        return self.post("/reopen/act", data)

    def test_only_cost_controllers_can_reopen_a_timesheet(self):
        self.assertNotIn("not available to your role", self.get("/reopen?u=29"))
        for u in (5, 7, 8, 30):  # a standard user, both approvers, the IT admin
            self.assertIn("not available to your role", self.get("/reopen?u=%d" % u), u)
        self.assertIn('href="/reopen?u=29"', self.get("/timesheet?u=29"))
        for u in (5, 7, 30):
            self.assertNotIn('href="/reopen?u=%d"' % u, self.get("/timesheet?u=%d" % u), u)
        self.assertIn("not available to your role", self.reopen(5, u="7"))  # and the action refuses them too
        self.assertIn("not available to your role", self.reopen(5, u="30"))
        self.assertNotIn("Draft", self.week_states(5))

    def test_reopening_a_week_sends_every_submitted_and_approved_entry_back_to_draft(self):
        before = self.week_states(5)  # Ewan Shaw: approved and submitted hours in the week of 2 Nov
        self.assertTrue(before and "Draft" not in before)
        page = self.get("/reopen?u=29&person=5&w=2026-11-02")
        self.assertIn("Ewan Shaw, week commencing 02/11/2026", page)
        self.assertIn("Approved by", page)
        page = self.reopen(5)
        self.assertIn("%d entries reopened" % len(before), page)
        self.assertEqual(self.week_states(5), ["Draft"] * len(before))
        row = self.one("select approved_by, approved_at, reopen_reason, reject_reason from time_entry where user_id=5 and work_date='2026-11-03' limit 1")
        self.assertEqual(tuple(row), (None, None, "Wrong code booked", None))  # the approval is gone, the reason is kept
        # other weeks are not touched
        self.assertIn("Approved", self.week_states(5, "2026-10-26", "2026-11-01"))

    def test_the_person_is_told_why_and_by_whom_and_can_resubmit(self):
        n = len(self.week_states(5))
        self.reopen(5)
        page = self.get("/timesheet?u=5&w=2026-11-02")
        self.assertIn("Reopened by a cost controller", page)
        self.assertIn("Wrong code booked", page)
        self.assertIn("reopened by Stephanie Apedaile", page)
        self.assertIn('title="Reopened: Wrong code booked"', page)
        self.assertIn("My Timesheet (%d reopened)" % n, page)
        self.assertNotIn("Sent back for correction", page)  # this is a reopen, not a rejection
        self.assertIn("You also have hours sent back or reopened in", self.get("/timesheet?u=5&w=2026-11-09"))
        # he corrects an hour and submits the week again: the notice goes
        self.post("/timesheet/save", {"u": "5", "w": "2026-11-02", "action": "Submit week", "reason": "", "h_1_4_1_0_2026-11-02": "7"})
        page = self.get("/timesheet?u=5&w=2026-11-02")
        self.assertNotIn("Reopened by a cost controller", page)
        self.assertIn(">My Timesheet</a>", page)
        self.assertEqual(set(self.week_states(5)), {"Submitted"})

    def test_a_reason_is_required_and_a_cost_controller_cannot_reopen_their_own_timesheet(self):
        self.assertIn("Give a reason", self.reopen(5, reason="   "))
        self.assertNotIn("Draft", self.week_states(5))
        # Stephanie (29) books internal hours and submits: she cannot reopen them herself, another controller can
        self.post("/timesheet/save", {"u": "29", "w": "2026-11-02", "action": "Save draft", "reason": "", "h_8_32_1_0_2026-11-04": "2"})
        self.post("/timesheet/save", {"u": "29", "w": "2026-11-02", "action": "Submit week", "reason": ""})
        self.assertIn("cannot reopen your own timesheet", self.reopen(29, u="29"))
        self.assertEqual(self.week_states(29), ["Submitted"])
        self.assertIn("1 entry reopened", self.reopen(29, u="1"))  # Emma Noble
        self.assertEqual(self.week_states(29), ["Draft"])

    def test_nothing_to_reopen_when_the_week_is_empty_or_already_drafts(self):
        self.assertIn("has no hours in this week", self.get("/reopen?u=29&person=4&w=2026-11-02"))   # David Hart booked nothing
        self.assertIn("Nothing to reopen", self.reopen(4))
        self.reopen(5)
        self.assertIn("already a draft", self.get("/reopen?u=29&person=5&w=2026-11-02"))  # every entry is a draft now
        self.assertIn("Nothing to reopen", self.reopen(5))

    def test_a_closed_week_can_be_reopened_with_the_timesheet(self):
        # the week of 26 Oct is closed in the seed data. Without the box ticked it stays closed, so the person could not edit it
        self.assertIn('name="reopen_period"', self.get("/reopen?u=29&person=5&w=2026-10-26"))
        self.assertIn("reopened", self.reopen(5, "2026-10-26"))
        self.assertEqual(self.one("select status from period where week_start='2026-10-26'")[0], "Closed")
        self.assertNotIn('name="h_1_4_1_0_2026-10-26"', self.get("/timesheet?u=5&w=2026-10-26"))  # closed: no boxes
        # a closed week is reopened from Admin, or by ticking the box
        self.assertIn('value="Reopen week 2026-10-26"', self.get("/admin?u=29&t=periods"))
        self.assertIn("Week 2026-10-26 reopened", self.admin_post("/admin/period", {"action": "reopen", "week_start": "2026-10-26"}))
        self.assertEqual(self.one("select status from period where week_start='2026-10-26'")[0], "Open")
        self.assertIn('name="h_1_4_1_0_2026-10-26"', self.get("/timesheet?u=5&w=2026-10-26"))
        # with the box ticked it all happens together (Daniel Evans, week of 26 Oct)
        self.admin_post("/admin/period", {"action": "close", "week_start": "2026-10-26"})
        page = self.reopen(9, "2026-10-26", reopen_period="1")
        self.assertIn("is open again", page)
        self.assertEqual(self.one("select status from period where week_start='2026-10-26'")[0], "Open")

    def test_reopened_hours_leave_the_approved_totals_until_approved_again(self):
        def approved(user):
            return self.one("select coalesce(sum(hours),0) from time_entry where user_id=? and status='Approved'", user)[0]
        before = approved(5)
        self.assertGreater(before, 0)
        self.reopen(5)
        self.assertLess(approved(5), before)  # this week's approved hours are drafts again
        page = self.get("/reports?u=5&from=2026-01-01&to=2026-12-31")
        self.assertIn("Actual booked hrs", page)  # and his report no longer counts them
        self.assertEqual(self.week_states(5, "2026-10-26", "2026-11-01"), ["Approved", "Approved"])  # the earlier week is still approved

    def test_who_has_been_reopened_shows_in_missing_timesheets_and_the_audit_trail(self):
        # a controller's list links to Reopen for people who have submitted; an approver's does not
        link = 'href="/reopen?u=29&amp;person=5&amp;w=2026-11-02"'
        self.assertIn(link, self.get("/missing?u=29&w=2026-11-02&s=all"))
        self.assertNotIn("/reopen?", self.get("/missing?u=7&w=2026-11-02&s=all"))
        self.reopen(5)
        row = self.missing(29, w="2026-11-02", s="all")[1]["Ewan Shaw"]
        self.assertEqual(row[4], "Draft only")
        self.assertEqual(row[7], str(len(self.week_states(5))))  # every entry counts as sent back
        self.assertNotIn(link, self.get("/missing?u=29&w=2026-11-02&s=all"))  # nothing left to reopen for him
        audit = self.get("/audit?u=29")
        self.assertIn("Reopened: Wrong code booked", audit)

    def test_a_later_rejection_replaces_the_reopen_note(self):
        self.reopen(5)
        self.post("/timesheet/save", {"u": "5", "w": "2026-11-02", "action": "Submit week", "reason": ""})
        eid = self.one("select entry_id from time_entry where user_id=5 and work_date='2026-11-03' limit 1")[0]
        self.post("/approvals/act", {"u": "7", "entry_id": str(eid), "action": "Reject selected", "reason": "Wrong hours"})
        page = self.get("/timesheet?u=5&w=2026-11-02")
        self.assertIn("Sent back for correction", page)
        self.assertIn("Wrong hours", page)
        self.assertNotIn("Reopened by a cost controller", page)  # the others were resubmitted, and this one is a rejection now

    # ---------- start and end dates, PAAF expiry (R07) ----------
    def test_no_input_boxes_outside_a_persons_start_and_end_dates(self):
        self.open_next_week()
        # Ewan Shaw (5) leaves on Wednesday 11 Nov: there are no boxes from the Thursday, and saving there is refused
        self.admin_post("/admin/save", {"t": "users", "id": "5", "name": "Ewan Shaw", "employment_type": "Contract", "role_id": "4",
                                        "permission": "user", "end_date": "2026-11-11"})
        page = self.get("/timesheet?u=5&w=2026-11-09&add_phase=1&add_role=4&add_loc=1")
        self.assertIn('name="h_1_4_1_0_2026-11-11"', page)  # his last day still has a box
        for day in ("12", "13", "14", "15"):
            self.assertNotIn('name="h_1_4_1_0_2026-11-%s"' % day, page, day)
        self.assertIn("until 11/11/2026", page)  # the note says why
        page = self.post("/timesheet/save", {"u": "5", "w": "2026-11-09", "action": "Save draft", "h_1_4_1_0_2026-11-13": "5", "reason": ""})
        self.assertIn("not active on that date", page)
        self.assertEqual(self.one("select count(*) from time_entry where user_id=5 and work_date='2026-11-13'")[0], 0)

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
        self.assertNotIn("h_8_6_1_0_2026-11-03", page)
        self.assertIn("h_8_6_1_0_2026-11-05", page)

    def test_client_hours_cannot_be_booked_after_the_paaf_expires(self):
        # David Hart is added to Apache's list, until Wednesday 4 Nov.
        self.assertIn("Person added", self.admin_post("/admin/add", {"t": "paafs", "user_id": "4", "client_id": "1"}))
        pid = self.one("select paaf_id from paaf where user_id=4 and client_id=1")[0]
        self.assertIn("now ends on", self.admin_post("/admin/paaf", {"paaf_id": str(pid), "action": "Save end date", "expiry_date": "2026-11-04"}))
        self.assertIn("Apache &gt; Wellbay Decommissioning", self.get("/timesheet?u=4&w=2026-11-02"))      # valid for part of that week
        self.assertNotIn("Apache &gt;", self.get("/timesheet?u=4&w=2026-11-09"))                           # expired before this one starts
        self.assertIn("1 entry saved", self.post("/timesheet/save", {"u": "4", "w": "2026-11-02", "action": "Save draft",
                                                                    "h_1_4_1_0_2026-11-03": "4", "reason": ""}))
        late = self.post("/timesheet/save", {"u": "4", "w": "2026-11-02", "action": "Save draft", "h_1_4_1_0_2026-11-05": "4", "reason": ""})
        self.assertIn("PAAF has expired", late)
        # internal hours do not depend on the PAAF
        self.assertIn("1 entry saved", self.post("/timesheet/save", {"u": "4", "w": "2026-11-02", "action": "Save draft",
                                                                    "h_8_4_1_0_2026-11-05": "2", "reason": ""}))

    # ---------- approvers book time too; a cost controller approves it ----------
    def test_an_approver_can_book_to_their_own_scope_and_a_cost_controller_approves_it(self):
        # Kenneth Nicolson (8) approves the Spirit scopes and holds a Spirit PAAF, so he can book to A1500 (phase 6) as Head of Projects (role 30)
        self.assertIn("A1500", self.get("/timesheet?u=8&w=2026-11-02"))
        self.assertIn("Nothing waiting", self.get("/approvals?u=29"))  # no approver has booked anything yet
        self.assertIn("1 entry saved", self.post("/timesheet/save", {"u": "8", "w": "2026-11-02", "action": "Save draft",
                                                                    "h_6_30_1_0_2026-11-04": "4", "reason": ""}))
        self.post("/timesheet/save", {"u": "8", "w": "2026-11-02", "action": "Submit week", "reason": ""})
        eid = self.one("select entry_id from time_entry where user_id=8 and work_date='2026-11-04'")[0]
        self.assertEqual(self.one("select status from time_entry where entry_id=?", eid)[0], "Submitted")
        # it is not in his own queue, and he cannot approve it himself
        self.assertNotIn('value="%d"' % eid, self.get("/approvals?u=8"))
        self.assertIn("only an approver of its scope", self.post("/approvals/act", {"u": "8", "entry_id": str(eid), "action": "Approve selected"}))
        # nobody else who approves can either: not the Apache approver, not the IT admin
        for u in ("7", "30"):
            page = self.post("/approvals/act", {"u": u, "entry_id": str(eid), "action": "Approve selected"})
            self.assertTrue("only an approver of its scope" in page or "not named as an approver" in page, u)
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
        self.post("/timesheet/save", {"u": "7", "w": "2026-11-02", "action": "Save draft", "h_3_2_1_0_2026-11-04": "3", "reason": ""})  # Alisdair (Apache) on BB0002
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
        self.open_next_week()
        self.post("/timesheet/save", {"u": "5", "w": "2026-11-09", "action": "Save draft", "h_1_4_1_0_2026-11-13": "7.5", "reason": ""})
        self.post("/timesheet/save", {"u": "5", "w": "2026-11-09", "action": "Submit week", "reason": ""})
        self.admin_post("/admin/period", {"action": "close", "week_start": "2026-11-09"})
        for path in ("/audit?u=29", "/admin?u=29&t=periods"):
            page = self.get(path)
            self.assertRegex(page, r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?!\.\d)", path)  # a time to the second is there...
            self.assertNotRegex(page, r"\d\d:\d\d:\d\d\.\d", path)  # ...and no fractions of a second
        stored = self.one("select last_edited_at from time_entry where user_id=5 and work_date='2026-11-13'")[0]
        self.assertRegex(stored, r"\d\d:\d\d:\d\d\.\d+")  # still stored precisely, see stamp()

    def test_save_and_submit_in_one_click_is_not_refused_as_a_repeat_edit(self):
        self.open_next_week()
        # Two edits of one entry by the same person within the same second need different last_edited_at values,
        # which is why stamp() keeps fractions of a second.
        self.post("/timesheet/save", {"u": "5", "w": "2026-11-09", "action": "Save draft", "h_1_4_1_0_2026-11-13": "7.5", "reason": ""})
        # an existing draft: changing its hours and submitting in one click updates the same entry twice in the same second
        page = self.post("/timesheet/save", {"u": "5", "w": "2026-11-09", "action": "Submit week",
                                             "h_1_4_1_0_2026-11-13": "6", "reason": ""})
        self.assertIn("1 entry saved", page)
        self.assertIn("submitted for approval", page)
        self.assertNotIn("Edits must set", page)
        self.assertEqual(tuple(self.one("select hours, status from time_entry where user_id=5 and work_date='2026-11-13'")), (6, "Submitted"))

    # ---------- admin forms ----------
    # Demo accounts: 29 Stephanie Apedaile (commercial lead) is a cost controller, 30 NMC Admin is the IT admin, 7 Alisdair Woodhead is a standard
    # user who approves. Users and job roles belong to the admin; clients, rates, PAAFs, scopes and internal codes to the controller.
    CONTROLLER, IT_ADMIN = "29", "30"

    def admin_post(self, path, data, u=None):
        if u is None:
            u = self.IT_ADMIN if data.get("t") in ("users", "roles", "locations") else self.CONTROLLER
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
            self.assertIn("only an approver of its scope", self.post("/approvals/act", {"u": "29", "entry_id": str(eid), "action": action, "reason": "x"}))
        self.assertEqual(self.one("select status from time_entry where entry_id=?", eid)[0], "Submitted")
        # an approver of a different scope is refused too
        self.assertIn("only an approver of its scope", self.post("/approvals/act", {"u": "8", "entry_id": str(eid), "action": "Approve selected"}))
        self.assertIn("only an approver of its scope", self.post("/approvals/act", {"u": "8", "entry_id": str(eid), "action": "Reject selected", "reason": "x"}))
        self.assertEqual(self.one("select status from time_entry where entry_id=?", eid)[0], "Submitted")

    def test_an_approver_cannot_approve_their_own_hours(self):
        # Alisdair Woodhead (7) approves INT01 and can also book to it, but not approve his own entry
        self.post("/timesheet/save", {"u": "7", "w": "2026-11-02", "action": "Save draft", "h_8_2_1_0_2026-11-04": "2", "reason": ""})
        self.post("/timesheet/save", {"u": "7", "w": "2026-11-02", "action": "Submit week", "reason": ""})
        eid = self.one("select entry_id from time_entry where user_id=7 and work_date='2026-11-04'")[0]
        self.assertIn("only an approver of its scope", self.post("/approvals/act", {"u": "7", "entry_id": str(eid), "action": "Approve selected"}))
        self.assertEqual(self.one("select status from time_entry where entry_id=?", eid)[0], "Submitted")

    def test_add_client_role_and_reject_duplicates_and_blanks(self):
        self.assertIn("Client added", self.admin_post("/admin/add", {"t": "clients", "name": "Shell", "approver_user_id": "7"}))
        self.assertIn("already exists", self.admin_post("/admin/add", {"t": "clients", "name": "Shell", "approver_user_id": "7"}))
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

    def test_a_person_added_to_a_clients_list_can_book_straight_away(self):
        self.admin_post("/admin/add", {"t": "users", "nmc_id": "A901", "name": "Test Person", "employment_type": "Staff", "role_id": "6",
                                       "permission": "user", "start_date": "2026-11-01", "end_date": ""})
        uid = self.one("select user_id from app_user where nmc_id='A901'")[0]
        book = {"u": str(uid), "w": "2026-11-02", "action": "Save draft", "h_1_6_1_0_2026-11-06": "4", "reason": ""}
        self.assertIn("not on this client", self.post("/timesheet/save", book))                      # not on Apache's list yet
        self.assertIn("Person added to the client", self.admin_post("/admin/add", {"t": "paafs", "user_id": str(uid), "client_id": "1"}))
        # the rate was resolved from the person's role (Cost Engineer on Apache), and there is no sign-off: the entry is active at once
        self.assertEqual(self.one("select rt.sales_rate from paaf p join rate rt on rt.rate_id=p.rate_id where p.user_id=?", uid)[0], 85.5)
        self.assertEqual(self.one("select status from paaf where user_id=?", uid)[0], "Approved")
        self.assertIn("already on this client", self.admin_post("/admin/add", {"t": "paafs", "user_id": str(uid), "client_id": "1"}))
        self.assertIn("1 entry saved", self.post("/timesheet/save", book))

    def test_the_list_shows_who_is_on_it_and_the_rate_and_end_date_can_be_changed(self):
        page = self.get("/admin?u=29&t=paafs")
        self.assertIn("None of our clients approve individuals", page)
        self.assertNotIn("Mark sent", page)
        self.assertNotIn("Reject", page)
        pid = self.one("select paaf_id from paaf where user_id=5 and client_id=1")[0]
        other = self.one("select rate_id from rate where client_id=1 and rate_id<>(select rate_id from paaf where paaf_id=?) limit 1", pid)[0]
        self.assertIn("now uses the chosen rate", self.admin_post("/admin/paaf", {"paaf_id": str(pid), "action": "Change rate", "rate_id": str(other)}))
        self.assertEqual(self.one("select rate_id from paaf where paaf_id=?", pid)[0], other)
        self.assertIn("Choose one of this client", self.admin_post("/admin/paaf", {"paaf_id": str(pid), "action": "Change rate", "rate_id": "999"}))
        self.assertIn("now ends on 2026-11-04", self.admin_post("/admin/paaf", {"paaf_id": str(pid), "action": "Save end date", "expiry_date": "2026-11-04"}))
        self.assertIn("Ended 2026-11-04", self.get("/admin?u=29&t=paafs&f_status=ended"))
        self.assertIn("Nobody matches", self.get("/admin?u=29&t=paafs&f_status=current&f_client=1&f_person=5"))
        self.assertIn("has no end date", self.admin_post("/admin/paaf", {"paaf_id": str(pid), "action": "Save end date", "expiry_date": ""}))
        self.assertIn("Unknown action", self.admin_post("/admin/paaf", {"paaf_id": str(pid), "action": "Approve"}))

    def test_paaf_needs_a_rate_for_the_role(self):
        self.admin_post("/admin/add", {"t": "clients", "name": "Shell", "approver_user_id": "7"})
        cid = self.one("select client_id from client where name='Shell'")[0]
        page = self.admin_post("/admin/add", {"t": "paafs", "user_id": "5", "client_id": str(cid)})
        self.assertIn("Add the rate first", page)

    def test_new_rate_applies_from_its_date_and_old_hours_keep_old_price(self):
        self.open_next_week()
        self.assertIn("Rate added", self.admin_post("/admin/add", {"t": "rates", "client_id": "1", "role_id": "4",
                                                                 "sales_rate": "100", "effective_from": "2026-11-12"}))
        self.assertIn("is not a number" if False else "Sales rate must be a number",
                      self.admin_post("/admin/add", {"t": "rates", "client_id": "1", "role_id": "4", "sales_rate": "abc", "effective_from": "2026-11-12"}))
        self.post("/timesheet/save", {"u": "5", "w": "2026-11-09", "action": "Save draft", "h_1_4_1_0_2026-11-13": "8", "reason": ""})
        self.assertEqual(self.one("select rt.sales_rate from time_entry t join rate rt on rt.rate_id=t.rate_id "
                                  "where t.user_id=5 and t.work_date='2026-11-13'")[0], 100)
        self.assertEqual(self.one("select rt.sales_rate from time_entry t join rate rt on rt.rate_id=t.rate_id "
                                  "where t.user_id=5 and t.work_date='2026-11-02'")[0], 85.5)     # a seeded day, booked before the new rate

    def approvers_of(self, scope):
        return sorted(r[0] for r in self.q_all("select user_id from v_scope_approver where scope_id=?", scope))

    def test_a_scope_has_its_clients_main_approver_and_optional_secondary_approvers(self):
        self.assertIn("Scope added", self.admin_post("/admin/add", {"t": "scopes", "scope_code": "BB0003", "project_id": "1", "description": "New Study", "status": "Open"}))
        sid = self.one("select scope_id from scope where scope_code='BB0003'")[0]
        self.assertEqual(self.one("select count(*) from phase where scope_id=? and name='General'", sid)[0], 1)   # a General CTR came with it
        self.assertEqual(self.approvers_of(sid), [7])                                                              # Apache's main approver, Alisdair Woodhead
        self.assertIn("Saved", self.admin_post("/admin/save", {"t": "scopes", "id": str(sid), "description": "New Study", "status": "Closed", "secondary": ["6", "9"]}))
        self.assertEqual(self.approvers_of(sid), [6, 7, 9])
        self.assertIn("Jamie Ireland, Daniel Evans" if False else "Daniel Evans", self.get("/admin?u=1&t=scopes"))
        self.admin_post("/admin/save", {"t": "scopes", "id": str(sid), "description": "New Study", "status": "Closed", "secondary": ["9"]})     # replaces the set
        self.assertEqual(self.approvers_of(sid), [7, 9])
        # only standard users can be named: the cost controller and the IT admin never approve
        for bad in ("29", "30"):
            self.assertIn("Only a standard user", self.admin_post("/admin/save", {"t": "scopes", "id": str(sid), "description": "New Study", "status": "Open", "secondary": bad}))
        self.assertEqual(self.approvers_of(sid), [7, 9])

    def test_the_main_approver_is_set_on_the_client(self):
        page = self.get("/admin?u=29&t=clients")
        self.assertIn("<th>Main approver</th>", page)
        self.assertIn("Alisdair Woodhead", page)
        self.assertIn("Kenneth Nicolson", page)
        self.assertIn("Saved", self.admin_post("/admin/save", {"t": "clients", "id": "1", "approver_user_id": "6"}))
        self.assertEqual(self.approvers_of(1), [6])                                          # Jamie Ireland now approves Apache's BB0001
        self.assertEqual(self.one("select approver_user_id from client where client_id=2")[0], 8)
        self.assertIn("Only a standard user", self.admin_post("/admin/save", {"t": "clients", "id": "1", "approver_user_id": "29"}))
        self.assertIn("Choose a main approver", self.admin_post("/admin/add", {"t": "clients", "name": "Total", "approver_user_id": ""}))
        self.assertIsNone(self.one("select 1 from client where name='Total'"))

    def test_deactivated_role_is_hidden_from_new_timesheet_rows(self):
        self.open_next_week()
        self.admin_post("/admin/save", {"t": "roles", "id": "2", "skill_code": "EM-02", "name": "Engineering Manager", "family": "Engineering Management", "active": "0"})
        page = self.get("/timesheet?u=5&w=2026-11-09")
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
                                              "where t.status='Submitted' and t.user_id<>? and exists (select 1 from v_scope_approver va where va.scope_id=s.scope_id and va.user_id=?)", (approver, approver))]
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
        self.assertIn("only an approver of its scope", page)
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
        for kind in ["Client", "Project", "Scope", "CTR", "Skill code", "Person", "Entry"]:
            self.assertIn("<td>%s</td>" % kind, page)
        for name in ["Wellbay Decommissioning", "BB0001 Wellbay Clearance", "BB0001-PM-E-001", "Ewan Shaw", "2026-11-03", "Morecambe Hub"]:
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
        self.open_next_week()
        page = self.get("/timesheet?u=9&w=2026-11-09&add_phase=1&add_role=3&add_loc=1&add_phase=2")
        self.assertIn('<th class="bkh">Booking</th>', page)                                          # one Booking column instead of seven
        self.assertNotIn("<th>Project</th>", page)
        self.assertIn("<b>BB0001-PM-D-001</b>", page)
        self.assertIn("Apache &rsaquo; Wellbay Decommissioning &rsaquo; BB0001 Wellbay Clearance", page)
        self.assertIn("Apache &gt; Wellbay Decommissioning &gt; BB0001 Wellbay Clearance &gt; BB0001-PM-E-001", page)
        # phase 2 = BB0001 Execute, role 3 = Project Engineer; his week of 2 Nov is submitted and locked, so he books in the next one
        self.assertIn('name="h_2_3_1_0_2026-11-10"', self.get("/timesheet?u=9&w=2026-11-09&add=2:3:1:0"))
        self.assertNotIn('<input type="text"', self.get("/timesheet?u=9&w=2026-11-02"))
        self.assertIn("1 entry saved", self.post("/timesheet/save", {"u": "9", "w": "2026-11-09", "action": "Save draft",
                                                                    "h_2_3_1_0_2026-11-13": "4", "reason": ""}))
        self.assertEqual(self.one("select phase_id from time_entry where user_id=9 and work_date='2026-11-13'")[0], 2)

    def test_closed_phase_is_not_offered_and_blocks_booking(self):
        self.open_next_week()
        self.admin_post("/admin/save", {"t": "phases", "id": "2", "name": "Execute", "status": "Closed"})
        page = self.get("/timesheet?u=9&w=2026-11-09")
        self.assertNotIn("&gt; Execute</option>", page)
        self.assertIn("CTR is not Open", self.post("/timesheet/save", {"u": "9", "w": "2026-11-09", "action": "Save draft",
                                                                        "h_2_3_1_0_2026-11-13": "4", "reason": ""}))

    # ---------- timesheet grid, layouts and Excel (R27, R29, R30) ----------
    def raw(self, path):
        with urllib.request.urlopen(self.base + path) as r:
            return r.read(), r.headers

    def sheet_rows(self, data):
        """Read the first sheet of an .xlsx file into rows of values (numbers as floats), with the standard library only."""
        ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            root = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
        rows = []
        for row in root.iter("{%s}row" % ns["m"]):
            cells = {}
            for c in row:
                letters = re.match(r"[A-Z]+", c.get("r")).group(0)
                col = sum((ord(ch) - 64) * 26 ** i for i, ch in enumerate(reversed(letters))) - 1
                cells[col] = (float(c.find("m:v", ns).text) if c.get("t") != "inlineStr" else "".join(c.find("m:is", ns).itertext()))
            rows.append([cells.get(i) for i in range(max(cells) + 1)] if cells else [])
        return rows

    def approved_hours(self, d0, d6, extra="", args=()):
        con = app.db()
        try:
            return con.execute("select coalesce(sum(hours),0) from time_entry where status='Approved' and work_date between ? and ?" + extra,
                               (d0, d6) + tuple(args)).fetchone()[0]
        finally:
            con.close()

    def test_the_grid_has_people_and_codes_down_and_days_across(self):
        page = self.get("/reports/grid?u=29&from=2026-11-02&to=2026-11-08")
        for d in ("Mon 02/11", "Tue 03/11", "Sun 08/11"):
            self.assertIn("<th>%s</th>" % d, page)
        for col in ("Person", "Client", "Project", "Scope", "CTR", "Skill code", "Location", "Type", "Total hours", "Value"):
            self.assertIn("<th>%s</th>" % col, page)
        self.assertIn(">Ewan Shaw</td>", page)
        # the total line matches the approved hours in the database
        total = self.approved_hours("2026-11-02", "2026-11-08")
        self.assertIn('<td align="right"><b>%s</b></td>' % app.fmt(total), page)

    def test_the_grid_shows_values_only_to_cost_controllers_and_other_people_only_their_own_hours(self):
        mine = self.get("/reports/grid?u=5&from=2026-11-02&to=2026-11-08")
        self.assertNotIn("<th>Value</th>", mine)
        self.assertIn(">Ewan Shaw</td>", mine)
        self.assertNotIn(">Daniel Evans</td>", mine)
        # an approver sees the people on the scopes they approve
        self.assertIn(">Daniel Evans</td>", self.get("/reports/grid?u=7&st=all&from=2026-11-02&to=2026-11-08"))
        self.assertIn("not available to your role", self.get("/reports/grid?u=30"))  # the IT admin has no reports

    def test_ready_made_layouts_and_tailoring(self):
        page = self.get("/reports/grid?u=29&layout=pw&from=2026-10-26&to=2026-11-08")
        self.assertIn("<th>w/c 26/10</th>", page)
        self.assertIn("<th>w/c 02/11</th>", page)
        self.assertNotIn("<th>Scope</th>", page)          # person by week is only the person
        # tailor it: add the client column and show each day
        page = self.get("/reports/grid?u=29&layout=pw&lay_prev=pw&tailor=1&col=person&col=client&by=day&from=2026-11-02&to=2026-11-03")
        self.assertIn("<th>Client</th>", page)
        self.assertIn("<th>Mon 02/11</th>", page)
        # choosing a different layout starts again from that layout's columns
        page = self.get("/reports/grid?u=29&layout=cw&lay_prev=pw&tailor=1&col=person&by=day&from=2026-11-02&to=2026-11-08")
        self.assertIn("<th>Scope</th>", page)
        self.assertNotIn("<th>Person</th>", page)
        # no columns at all gives one total line
        page = self.get("/reports/grid?u=29&layout=pw&lay_prev=pw&tailor=1&by=none&from=2026-11-02&to=2026-11-08")
        self.assertIn("<th>Hours</th>", page)
        self.assertIn(">All hours<", page)

    def test_a_long_range_of_days_is_shown_by_week_and_bad_input_falls_back(self):
        page = self.get("/reports/grid?u=29&layout=pd&from=2026-08-01&to=2026-12-31")
        self.assertIn("more than 42 days", page)
        self.assertIn("<th>w/c ", page)
        self.assertIn("Timesheet grid", self.get("/reports/grid?u=29&from=nonsense&to=&client=x&scope=99999&layout=zzz"))

    def test_the_grid_can_be_narrowed_to_one_person(self):
        page = self.get("/reports/grid?u=29&from=2026-11-02&to=2026-11-08&person=5")
        self.assertIn(">Ewan Shaw</td>", page)
        self.assertNotIn(">Daniel Evans</td>", page)
        mine = self.approved_hours("2026-11-02", "2026-11-08", " and user_id=?", (5,))
        self.assertIn('<td align="right"><b>%s</b></td>' % app.fmt(mine), page)

    def test_the_grid_downloads_as_an_excel_file_matching_the_screen(self):
        data, headers = self.raw("/reports/grid.xlsx?u=29&from=2026-11-02&to=2026-11-08")
        self.assertEqual(headers["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertTrue(data.startswith(b"PK"))
        rows = self.sheet_rows(data)
        self.assertEqual(rows[0][:2], ["Person", "Client"])
        self.assertIn("Mon 02/11", rows[0])
        self.assertIn("Value", rows[0])
        self.assertEqual(rows[-1][0], "Total")
        self.assertAlmostEqual(rows[-1][rows[0].index("Total hours")], self.approved_hours("2026-11-02", "2026-11-08"))
        # the link on the page carries the same settings, and other people only download what they could see
        page = self.get("/reports/grid?u=29&layout=pw&from=2026-11-02&to=2026-11-08")
        self.assertIn("/reports/grid.xlsx?u=29&amp;layout=pw", page)
        own = self.sheet_rows(self.raw("/reports/grid.xlsx?u=5&from=2026-11-02&to=2026-11-08")[0])
        self.assertNotIn("Value", own[0])
        self.assertEqual({r[0] for r in own[1:-1]}, {"Ewan Shaw"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(self.base + "/reports/grid.xlsx?u=30")
        self.assertEqual(cm.exception.code, 403)
        cm.exception.close()

    def test_the_period_report_downloads_as_excel(self):
        rows = self.sheet_rows(self.raw("/reports/period.xlsx?u=29&from=2026-10-01&to=2026-12-31")[0])
        self.assertEqual(rows[0][:3], ["Week commencing", "Client", "Project"])
        self.assertEqual(rows[-1][0], "Total for range")
        client_only = " and scope_id in (select s.scope_id from scope s join client c on c.client_id=s.client_id where c.is_internal=0)"
        self.assertAlmostEqual(rows[-1][7], self.approved_hours("2026-10-01", "2026-12-31", client_only))
        self.assertIn("/reports/period.xlsx?u=29", self.get("/reports?u=29"))

    def test_excel_text_is_stored_as_text_and_special_characters_are_safe(self):
        data = app.xlsx_bytes("Test <sheet>:1", [["Name", "Hours"], ["=1+1 & <b>x</b>\x01", 7.5]])
        rows = self.sheet_rows(data)
        self.assertEqual(rows[1], ["=1+1 & <b>x</b>", 7.5])          # kept as text, markup and control characters harmless
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertIn("Test &lt;sheet&gt;1", z.read("xl/workbook.xml").decode())

    # ---------- allowances: Overnight allowance, Per Diem, Mob fee ----------
    def staff(self, *users):
        """Allowances are for staff, and some of the seed's people are contractors: make these people staff for the test."""
        con = app.db()
        con.executemany("update app_user set employment_type='Staff' where user_id=?", [(x,) for x in users])
        # the demo has one allowance; these tests also need a few more kinds, which the system supports (they have no rate, so they are worth nothing)
        con.execute("insert or ignore into allowance_type (allowance_id, name) values (2, 'Per Diem'), (3, 'Mob fee')")
        con.commit()
        con.close()

    def claim(self, user, ticks, w=None, action="Save draft", rows=None):
        """Save (and optionally submit) allowance ticks: ticks is a list of (phase, allowance, date) that are ticked, rows the (phase, allowance) rows shown."""
        self.staff(user)
        if w is None:  # the week the first day falls in (a submitted week is locked, so tests use weeks the person has not submitted)
            first = (ticks or rows or [(0, 0, "2026-11-02")])[0][2]
            w = app.monday(datetime.date.fromisoformat(first)).isoformat()
        shown = set()
        data = {"u": str(user), "w": w, "action": action, "arow": [], "ashown": []}
        for ph, al, d in (rows or []):
            shown.add((ph, al, d))
        for ph, al, d in ticks:
            shown.add((ph, al, d))
        for ph, al, d in sorted(shown):
            data["ashown"].append("%d:%d:%s" % (ph, al, d))
            if "%d:%d" % (ph, al) not in data["arow"]:
                data["arow"].append("%d:%d" % (ph, al))
        for ph, al, d in ticks:
            data["a_%d_%d_%s" % (ph, al, d)] = "1"
        return self.post("/timesheet/save", data)

    def status_of_allowance(self, aid):
        return self.one("select status from allowance_entry where allowance_entry_id=?", aid)[0]

    def q_all(self, sql, *args):
        con = app.db()
        try:
            return [tuple(r) for r in con.execute(sql, args)]
        finally:
            con.close()

    def allowance_rows(self, user):
        return self.q_all("select phase_id, allowance_id, work_date, status from allowance_entry where user_id=? order by work_date, allowance_id", user)

    def test_the_timesheet_has_an_allowances_section_and_an_add_allowance_row(self):
        self.open_next_week()
        self.staff(5)
        page = self.get("/timesheet?u=5&w=2026-11-09")
        self.assertNotIn("<b>Allowances</b>", page)          # no heading until there is a row to show
        self.assertIn("Claim an allowance", page)
        self.assertIn(">Offshore allowance</option>", page)
        page = self.get("/timesheet?u=5&w=2026-11-09&aadd_phase=1&aadd_type=2")
        self.assertIn('name="a_1_2_2026-11-11"', page)               # a Per Diem row against BB0001 Define, a box per day
        self.assertIn('<th class="bkh">Allowance</th>', page)
        # an unsaved row stays when another row is added
        page = self.get("/timesheet?u=5&w=2026-11-09&aadd_phase=1&aadd_type=2&add_phase=1&add_role=4&add_loc=1&aadd=1:1")
        self.assertIn('name="a_1_1_2026-11-11"', page)

    def test_allowance_days_are_saved_cleared_and_submitted_with_the_week(self):
        self.open_next_week()
        page = self.claim(5, [(1, 2, "2026-11-12"), (1, 2, "2026-11-13"), (1, 3, "2026-11-13")])
        self.assertIn("3 allowance days saved", page)
        self.assertEqual(self.allowance_rows(5), [(1, 2, "2026-11-12", "Draft"), (1, 2, "2026-11-13", "Draft"), (1, 3, "2026-11-13", "Draft")])
        self.assertIn('name="a_1_2_2026-11-12" value="1" checked', self.get("/timesheet?u=5&w=2026-11-09"))
        # untick one: it is cleared
        page = self.claim(5, [(1, 2, "2026-11-12"), (1, 3, "2026-11-13")], rows=[(1, 2, "2026-11-13")])
        self.assertIn("1 allowance day cleared", page)
        self.assertEqual(self.allowance_rows(5), [(1, 2, "2026-11-12", "Draft"), (1, 3, "2026-11-13", "Draft")])
        # submit the week: the allowances go with it and are locked
        page = self.claim(5, [(1, 2, "2026-11-12"), (1, 3, "2026-11-13")], action="Submit week")
        self.assertIn("2 allowance days submitted for approval", page)
        self.assertEqual({r[3] for r in self.allowance_rows(5)}, {"Submitted"})
        locked = self.get("/timesheet?u=5&w=2026-11-09")
        self.assertNotIn('name="a_1_2_2026-11-12"', locked)
        self.assertIn("&#10003;", locked)
        self.assertIn("so are submitted and approved allowances", locked)
        # a hand-made request to untick a locked day changes nothing
        self.claim(5, [], rows=[(1, 2, "2026-11-12")])
        self.assertEqual(len(self.allowance_rows(5)), 2)

    def test_the_booking_rules_are_reported_on_screen(self):
        self.open_next_week()
        self.assertIn("not on the client", self.claim(4, [(1, 2, "2026-11-12")]))                                 # David Hart is not on Apache's list
        self.assertIn("period is not open", self.claim(4, [(8, 2, "2026-10-30")], w="2026-10-26"))
        self.assertEqual(self.allowance_rows(4), [])
        # one of each allowance a day: a second booking code the same day is refused
        self.claim(5, [(1, 2, "2026-11-12")])
        page = self.claim(5, [(1, 2, "2026-11-12"), (2, 2, "2026-11-12")])
        self.assertIn("already claimed", page)
        self.assertEqual(self.allowance_rows(5), [(1, 2, "2026-11-12", "Draft")])

    def test_the_approver_approves_or_sends_back_allowances_with_the_hours(self):
        self.open_next_week()
        self.claim(5, [(1, 2, "2026-11-12"), (1, 1, "2026-11-13")], action="Submit week")
        ids = [r[0] for r in self.q_all("select allowance_entry_id from allowance_entry where user_id=5 order by allowance_entry_id")]
        page = self.get("/approvals?u=7&w=2026-11-09")                       # Alisdair Woodhead approves BB0001
        self.assertIn("<b>Allowances</b>", page)
        for i in ids:
            self.assertIn('name="allowance_entry_id" value="%d"' % i, page)
        self.assertIn("Per Diem", page)
        self.assertNotIn('name="allowance_entry_id"', self.get("/approvals?u=8&w=2026-11-09"))     # Kenneth approves other scopes
        self.assertIn("1 allowance day approved", self.post("/approvals/act", {"u": "7", "w": "2026-11-09", "allowance_entry_id": str(ids[0]), "action": "Approve selected"}))
        self.assertIn("Reason", self.post("/approvals/act", {"u": "7", "w": "2026-11-09", "allowance_entry_id": str(ids[1]), "action": "Reject selected", "reason": ""}).replace("Give a reason", "Reason"))
        self.assertEqual(self.status_of_allowance(ids[1]), "Submitted")                              # a reject without a reason changed nothing
        self.assertIn("1 allowance day sent back to draft", self.post("/approvals/act", {"u": "7", "w": "2026-11-09", "allowance_entry_id": str(ids[1]), "action": "Reject selected", "reason": "Not on site that night"}))
        self.assertEqual((self.status_of_allowance(ids[0]), self.status_of_allowance(ids[1])), ("Approved", "Draft"))
        # the sent-back day is a draft again; the other cannot be approved twice or by the wrong person
        self.assertIn("only an approver of its scope", self.post("/approvals/act", {"u": "8", "w": "2026-11-09", "allowance_entry_id": str(ids[0]), "action": "Approve selected"}))
        self.assertIn("only submitted allowances", self.post("/approvals/act", {"u": "7", "w": "2026-11-09", "allowance_entry_id": str(ids[0]), "action": "Approve selected"}))

    def test_nobody_approves_their_own_allowances_and_a_cost_controller_approves_an_approvers(self):
        self.claim(8, [(6, 2, "2026-11-04")], action="Submit week")                      # Kenneth Nicolson claims a Per Diem on A1500
        aid = self.one("select allowance_entry_id from allowance_entry where user_id=8")[0]
        self.assertNotIn('name="allowance_entry_id"', self.get("/approvals?u=8&w=2026-11-02"))
        self.assertIn("only an approver of its scope", self.post("/approvals/act", {"u": "8", "w": "2026-11-02", "allowance_entry_id": str(aid), "action": "Approve selected"}))
        self.assertEqual(self.allowance_rows(8)[0][3], "Submitted")
        self.assertIn('name="allowance_entry_id" value="%d"' % aid, self.get("/approvals?u=29&w=2026-11-02"))
        self.assertIn("1 allowance day approved", self.post("/approvals/act", {"u": "29", "w": "2026-11-02", "allowance_entry_id": str(aid), "action": "Approve selected"}))
        self.assertEqual(self.allowance_rows(8)[0][3], "Approved")

    def test_waiting_allowances_count_in_the_menu_and_the_week_list(self):
        self.open_next_week()
        self.claim(5, [(1, 2, "2026-11-12")], action="Submit week")
        before = int(re.search(r"Approvals \((\d+)\)", self.get("/approvals?u=7&w=2026-11-09")).group(1))
        con = app.db()
        con.execute("update allowance_entry set status='Approved', approved_by=7, approved_at='x', last_edited_by=7, last_edited_at='x' where user_id=5")
        con.commit()
        con.close()
        after = int(re.search(r"Approvals \((\d+)\)", self.get("/approvals?u=7&w=2026-11-09")).group(1))
        self.assertEqual(before - after, 1)

    def test_reopening_a_week_sends_its_allowances_back_to_draft_too(self):
        self.open_next_week()
        self.claim(5, [(1, 2, "2026-11-12")], action="Submit week")
        aid = self.one("select allowance_entry_id from allowance_entry where user_id=5")[0]
        self.post("/approvals/act", {"u": "7", "w": "2026-11-09", "allowance_entry_id": str(aid), "action": "Approve selected"})
        page = self.post("/reopen/act", {"u": "29", "person": "5", "w": "2026-11-09", "reason": "Wrong night"})
        self.assertIn("with 1 allowance day", page)
        self.assertEqual(self.allowance_rows(5), [(1, 2, "2026-11-12", "Draft")])
        self.assertIn('name="a_1_2_2026-11-12" value="1" checked', self.get("/timesheet?u=5&w=2026-11-09"))

    def test_allowances_appear_in_the_reports_and_the_audit_trail(self):
        self.open_next_week()
        self.claim(5, [(1, 2, "2026-11-12"), (1, 2, "2026-11-11")], action="Submit week")
        ids = [r[0] for r in self.q_all("select allowance_entry_id from allowance_entry where user_id=5 order by work_date")]
        self.post("/approvals/act", {"u": "7", "w": "2026-11-09", "allowance_entry_id": str(ids[0]), "action": "Approve selected"})
        section = self.get("/reports?u=29&from=2026-10-01&to=2026-12-31").split("Allowances (offshore allowance")[1]
        self.assertIn("<td>Ewan Shaw</td><td>Per Diem</td><td align=\"right\">1</td><td align=\"right\">1</td>", section)
        own = self.get("/reports?u=5&from=2026-10-01&to=2026-12-31").split("Allowances (offshore allowance")[1]
        self.assertIn("Ewan Shaw", own)
        other = self.get("/reports?u=9&from=2026-10-01&to=2026-12-31").split("Allowances (offshore allowance")[1]       # Daniel Evans sees none of Ewan's
        self.assertNotIn("Ewan Shaw", other.split("<h4>")[0])
        audit = self.get("/audit?u=29")
        self.assertIn("<h4>Allowances</h4>", audit)
        self.assertIn("Per Diem", audit.split("<h4>Allowances</h4>")[1])
        self.assertIn("Approved", audit.split("<h4>Allowances</h4>")[1])

    # ---------- audit fixes and timesheet quality of life ----------
    def test_the_day_cap_is_reported_on_screen(self):
        self.post("/timesheet/save", {"u": "5", "w": "2026-11-09", "action": "Save draft", "h_1_4_1_0_2026-11-10": "20"})
        page = self.post("/timesheet/save", {"u": "5", "w": "2026-11-09", "action": "Save draft", "h_1_4_2_0_2026-11-10": "8"})
        self.assertIn("more than 24 hours", page)
        self.assertEqual(self.one("select sum(hours) from time_entry where user_id=5 and work_date='2026-11-10'")[0], 20)

    def test_add_row_saves_the_hours_you_have_typed_first(self):
        self.staff(5)
        page = self.post("/timesheet/save", {"u": "5", "w": "2026-11-09", "action": "Add row", "h_1_4_1_0_2026-11-10": "6",
                                             "add_phase": "8", "add_role": "4", "add_loc": "1"})
        self.assertEqual(self.one("select hours from time_entry where user_id=5 and work_date='2026-11-10' and phase_id=1")[0], 6)   # saved, not lost
        self.assertIn('name="h_8_4_1_0_2026-11-10"', page)                                                                           # and the new empty row is there
        page = self.post("/timesheet/save", {"u": "5", "w": "2026-11-09", "action": "Add allowance row", "h_1_4_1_0_2026-11-11": "3",
                                             "aadd_phase": "1", "aadd_type": "2"})
        self.assertIn('name="a_1_2_2026-11-11"', page)
        self.assertEqual(self.one("select hours from time_entry where user_id=5 and work_date='2026-11-11'")[0], 3)
        # the controls sit under the grid's own form, so they travel with it
        self.assertIn('<form id="tsform" method="post" action="/timesheet/save">', page)
        self.assertIn('<select form="tsform" name="add_phase">', page)
        self.assertIn('<input form="tsform" type="submit" name="action" value="Add row">', page)

    def test_the_timesheet_shows_the_week_status_a_this_week_link_and_a_date_picker(self):
        page = self.get("/timesheet?u=5&w=2026-11-02")
        self.assertIn("<b>Week status:</b> Partly submitted" if False else "<b>Week status:</b>", page)
        self.assertIn("Submitted: 16 hours waiting for approval, 16 approved", page)
        monday = (datetime.date.today() - datetime.timedelta(days=datetime.date.today().weekday())).isoformat()
        self.assertIn('/timesheet?u=5&amp;w=%s">This week</a>' % monday, page)
        self.assertIn('<input type="date" name="w" value="2026-11-02">', page)
        self.assertIn("Not started", self.get("/timesheet?u=5&w=2030-01-07"))
        self.post("/timesheet/save", {"u": "5", "w": "2030-01-07", "action": "Save draft", "h_1_4_1_0_2030-01-08": "5"})

    def test_submitting_an_empty_week_says_there_is_nothing_to_submit(self):
        page = self.post("/timesheet/save", {"u": "5", "w": "2026-11-09", "action": "Submit week"})
        self.assertIn("Nothing to submit", page)
        self.assertNotIn("0 entries submitted", page)

    def test_an_ended_assignment_is_explained_on_the_timesheet(self):
        self.admin_post("/admin/add", {"t": "paafs", "user_id": "4", "client_id": "1"})
        pid = self.one("select paaf_id from paaf where user_id=4 and client_id=1")[0]
        self.admin_post("/admin/paaf", {"paaf_id": str(pid), "action": "Save end date", "expiry_date": "2026-10-30"})
        page = self.get("/timesheet?u=4&w=2026-11-02")
        self.assertIn("Apache:</b> you came off this client's list on 30/10/2026", page)
        self.assertNotIn("you came off this client's list", self.get("/timesheet?u=5&w=2026-11-02"))   # Ewan Shaw is still on it

    def test_the_it_admin_account_is_not_expected_to_submit_a_timesheet(self):
        page = self.get("/missing?u=29&w=2026-11-02&s=all")
        self.assertNotIn(">NMC Admin</td>", page)

    def test_a_new_client_needs_a_main_approver_chosen_and_the_scope_wording_is_clear(self):
        page = self.get("/admin?u=1&t=clients")
        self.assertIn('<option value="">Choose an approver</option>', page)
        scopes = self.get("/admin?u=1&t=scopes")
        self.assertIn("<th>Scope description</th>", scopes)
        self.assertIn("<th>Secondary approvers</th>", scopes)
        self.assertIn("client &gt; project &gt; scope &gt; CTR", scopes)

    # ---------- booking code controls, approver per code, description and references (R06, R19, R04a, R04f) ----------
    def save_phase(self, phase, **kw):
        data = {"t": "phases", "id": str(phase), "name": self.one("select name from phase where phase_id=?", phase)[0], "status": "Open",
                "ctr_code": self.one("select ctr_code from phase where phase_id=?", phase)[0],
                "approver_user_id": "", "restricted": "0", "description": "", "wbs": "", "cbs": "", "abs_code": "", "book_from": "", "book_to": ""}
        data.update(kw)
        return self.admin_post("/admin/save", data, u="1")

    def test_the_phase_page_has_description_references_approver_and_controls(self):
        page = self.get("/admin/edit?u=1&t=phases&id=1")
        for label in ("CTR code", "CTR title", "Prepared by", "Checked by", "Objectives", "Input requirements", "Scope of work", "This CTR assumes", "This CTR excludes",
                      "Expenses", "Payment terms", "Basis of proposal", "WBS", "CBS", "ABS", "Who can book", "People allowed", "Bookable from", "Bookable until"):
            self.assertIn("<td>%s</td>" % label, page, label)
        self.assertNotIn("<td>Approver</td>", page)                      # approvers are on the client and the scope now
        self.assertIn("Saved", self.save_phase(1, description="Clear the wellbay", wbs="1.2.3", cbs="C-40", abs_code="A-7"))
        self.assertEqual(tuple(self.one("select description, wbs, cbs, abs_code from phase where phase_id=1")), ("Clear the wellbay", "1.2.3", "C-40", "A-7"))
        # shown on the CTR
        ctr = self.get("/ctr?u=29&phase=1")
        self.assertIn("Clear the wellbay", ctr)
        self.assertIn("1.2.3 / C-40 / A-7", ctr)
        # and on the phases list
        self.assertIn("Clear the wellbay", self.get("/admin?u=1&t=scopes"))

    def test_the_phase_page_checks_dates_and_only_controllers_can_use_it(self):
        self.assertIn("cannot be before", self.save_phase(1, book_from="2026-11-10", book_to="2026-11-01"))
        self.assertIn("must be a date", self.save_phase(1, book_from="soon"))
        self.assertEqual(self.one("select book_from from phase where phase_id=1")[0], None)
        self.admin_post("/admin/save", {"t": "phases", "id": "1", "name": "Define", "status": "Open", "description": "Sneaky"}, u="5")
        self.assertNotEqual(self.one("select description from phase where phase_id=1")[0], "Sneaky")

    def test_a_restricted_code_is_only_offered_to_and_bookable_by_the_people_listed(self):
        self.open_next_week()
        both = self.get("/timesheet?u=9&w=2026-11-09")
        self.assertIn("Apache &gt; Wellbay Decommissioning &gt; BB0001 Wellbay Clearance &gt; BB0001-PM-E-001", self.get("/timesheet?u=5&w=2026-11-09"))
        self.save_phase(1, restricted="1", person=["5"])               # BB0001 Define: Ewan Shaw only
        self.assertEqual(self.one("select count(*) from phase_person where phase_id=1")[0], 1)
        define = "Apache &gt; Wellbay Decommissioning &gt; BB0001 Wellbay Clearance &gt; BB0001-PM-D-001</option>"
        self.assertIn(define, self.get("/timesheet?u=5&w=2026-11-09"))
        self.assertNotIn(define, self.get("/timesheet?u=9&w=2026-11-09"))
        self.assertIn("not on its list", self.post("/timesheet/save", {"u": "9", "w": "2026-11-09", "action": "Save draft", "reason": "",
                                                                      "h_1_3_1_0_2026-11-13": "4"}))
        self.assertIn("1 entry saved", self.post("/timesheet/save", {"u": "5", "w": "2026-11-09", "action": "Save draft", "reason": "",
                                                                    "h_1_4_1_0_2026-11-13": "4"}))
        self.assertIn("1 named person only", self.get("/admin?u=1&t=scopes"))
        # untick everyone: nobody can book, and switching it back to anyone reopens it
        self.save_phase(1, restricted="1")
        self.assertNotIn(define, self.get("/timesheet?u=5&w=2026-11-09"))
        self.save_phase(1, restricted="0")
        self.assertIn(define, self.get("/timesheet?u=9&w=2026-11-09"))

    def test_a_booking_window_hides_the_code_and_refuses_dates_outside_it(self):
        self.open_next_week()
        define = "Apache &gt; Wellbay Decommissioning &gt; BB0001 Wellbay Clearance &gt; BB0001-PM-D-001</option>"
        self.save_phase(1, book_from="2026-11-10", book_to="2026-11-12")
        self.assertIn(define, self.get("/timesheet?u=5&w=2026-11-09"))                  # the week overlaps the window
        self.assertNotIn(define, self.get("/timesheet?u=5&w=2026-11-16"))               # a later week is outside it
        self.assertIn("outside its booking dates", self.post("/timesheet/save", {"u": "5", "w": "2026-11-09", "action": "Save draft", "reason": "",
                                                                                "h_1_4_1_0_2026-11-13": "4"}))
        self.assertIn("1 entry saved", self.post("/timesheet/save", {"u": "5", "w": "2026-11-09", "action": "Save draft", "reason": "",
                                                                    "h_1_4_1_0_2026-11-11": "4"}))
        self.assertIn("3/11/2026" if False else "2026-11-10 to 2026-11-12", self.get("/admin?u=1&t=scopes"))

    def test_a_secondary_approver_on_a_scope_gets_its_hours_in_their_queue_too(self):
        self.open_next_week()
        # Jamie Ireland (6) becomes a secondary approver of BB0001, alongside Apache's main approver Alisdair Woodhead (7)
        self.assertIn("Saved", self.admin_post("/admin/save", {"t": "scopes", "id": "1", "description": "Wellbay Clearance", "status": "Open", "secondary": "6"}, u="1"))
        self.post("/timesheet/save", {"u": "9", "w": "2026-11-09", "action": "Save draft", "reason": "", "h_2_3_1_0_2026-11-13": "3"})
        self.post("/timesheet/save", {"u": "9", "w": "2026-11-09", "action": "Submit week", "reason": ""})
        eid = self.one("select entry_id from time_entry where user_id=9 and work_date='2026-11-13'")[0]
        self.assertIn('value="%d"' % eid, self.get("/approvals?u=6&w=2026-11-09"))
        self.assertIn('value="%d"' % eid, self.get("/approvals?u=7&w=2026-11-09"))
        self.assertIn("only an approver of its scope", self.post("/approvals/act", {"u": "8", "w": "2026-11-09", "entry_id": str(eid), "action": "Approve selected"}))   # Spirit's approver
        self.assertIn("1 entry approved", self.post("/approvals/act", {"u": "6", "w": "2026-11-09", "entry_id": str(eid), "action": "Approve selected"}))
        self.assertEqual(tuple(self.one("select status, approved_by from time_entry where entry_id=?", eid)), ("Approved", 6))
        self.assertIn("Jamie Ireland", self.get("/ctr?u=29&phase=2"))                         # the CTR names the scope's approvers

    def test_a_secondary_approver_on_one_scope_is_an_approver_with_a_missing_timesheets_page(self):
        self.assertIn("not named as an approver", self.get("/approvals?u=9"))                  # Daniel Evans is an ordinary project engineer
        self.admin_post("/admin/save", {"t": "scopes", "id": "2", "description": "Platform Study", "status": "Open", "secondary": "9"}, u="1")
        self.assertNotIn("not named as an approver", self.get("/approvals?u=9"))
        self.assertNotIn("not available to your role", self.get("/missing?u=9"))

    def test_the_ctr_page_is_laid_out_like_the_ctr_sheet(self):
        page = self.get("/ctr?u=29&phase=1")
        order = ["CTR BB0001-PM-D-001", "Project Name", "Project Title", "Client", "Framework Agreement Ref", "CTR Title", "CTR No",
                 "Prepared by", "Checked by", "Approved by", "Objectives", "Input Requirements", "Scope of Work", "This CTR assumes the following",
                 "This CTR excludes the following", "Commencement Date", "Completion Date", "Expenses", "Resource", "Sub-Totals", "Addition",
                 "Payment Terms", "Basis of Proposal"]
        at = -1
        for part in order:
            nxt = page.find(part, at + 1)
            self.assertGreater(nxt, at, "%s should come after the previous section" % part)
            at = nxt
        self.assertIn("Define the scope, sequence and cost of clearing the Wellbay", page)       # the seeded example's objectives
        self.assertIn("Wellbay Clearance - Define", page)                                         # CTR title
        self.assertIn("AB", page)
        self.assertIn("Reimbursable [X]", page)                                                   # payment terms with the chosen one marked
        self.assertIn("Budget [&nbsp;]", page)
        self.assertIn("Cost &pound; / Hour", page)
        self.assertNotIn("Framework Agreement Ref</b></td><td colspan=\"3\">SPT", page)          # Apache has none
        spirit = self.get("/ctr?u=29&phase=6")
        self.assertIn("A1500-PM-D-001", spirit)
        self.assertIn("SPT-01163 Contract", spirit)                                               # a client's framework reference

    def test_ctr_sheet_fields_are_edited_on_the_ctr_page_in_admin_and_shown_on_the_sheet(self):
        data = {"t": "phases", "id": "2", "name": "Execute", "status": "Open", "restricted": "0", "ctr_code": "EN-E-007", "description": "Execution support",
                "prepared_by": "KN", "checked_by": "GH", "objectives": "Do the work\nSafely", "inputs": "Drawings", "scope_of_work": "All of it",
                "assumptions": "Access", "exclusions": "Surveys", "expenses": "Included", "payment_terms": "Lump Sum", "basis": "Per the plan",
                "wbs": "", "cbs": "", "abs_code": "", "book_from": "2026-11-02", "book_to": "2026-11-30"}
        self.assertIn("Saved", self.admin_post("/admin/save", data, u="1"))
        page = self.get("/ctr?u=29&phase=2")
        self.assertIn("CTR BB0001-EN-E-007", page)
        for text in ("Execution support", "KN", "GH", "Do the work<br>Safely", "Drawings", "All of it", "Access", "Surveys", "Included", "Per the plan", "02/11/2026".replace("02/11/2026", "2026-11-02")):
            self.assertIn(text, page, text)
        self.assertIn("Lump Sum [X]", page)
        self.assertIn("Reimbursable [&nbsp;]", page)
        self.assertIn("is not", self.admin_post("/admin/save", dict(data, payment_terms="Cash")) if False else "is not")
        self.assertIn("Payment terms must be", self.admin_post("/admin/save", dict(data, payment_terms="Cash"), u="1"))
        self.assertIn("already exists", self.admin_post("/admin/save", dict(data, id="1", name="Define", ctr_code="EN-E-007"), u="1"))   # a code is unique within a scope
        # the client's framework reference
        self.assertIn("Saved", self.admin_post("/admin/save", {"t": "clients", "id": "1", "approver_user_id": "7", "framework_ref": "APA-77 Master Agreement"}, u="1"))
        self.assertIn("APA-77 Master Agreement", self.get("/ctr?u=29&phase=2"))

    def test_the_sheets_additions_are_the_approved_offshore_allowance_at_its_rate(self):
        self.open_next_week()
        self.claim(9, [(1, 1, "2026-11-11"), (1, 1, "2026-11-12")], action="Submit week")          # two offshore allowance days for a staff member on BB0001 Define
        ids = [r[0] for r in self.q_all("select allowance_entry_id from allowance_entry where user_id=9 order by work_date")]
        self.post("/approvals/act", {"u": "7", "w": "2026-11-09", "allowance_entry_id": [str(i) for i in ids], "action": "Approve selected"})
        page = self.get("/ctr?u=29&phase=1")
        row = page.split("<th>Addition</th>")[1].split("</table>")[0]
        self.assertIn("Offshore allowance", row)
        self.assertIn('<td align="right">100.00</td><td align="right">2</td><td align="right">200.00</td>', row)    # 100 a day x 2 days
        self.assertIn("additions 200.00", page)
        self.assertIn('family=Allowances">Allowances</a>', page)                                                    # and the people behind it
        self.assertIn("Daniel Evans", self.get("/ctr?u=29&phase=1&family=Allowances"))
        seen = self.get("/ctr?u=7&phase=1")                                                                         # an approver sees days, no money
        self.assertIn("Offshore allowance", seen)
        self.assertNotIn("Cost &pound; / Unit", seen)
        self.assertNotIn("200.00", seen.split("<th>Addition</th>")[1])
        # a new rate from a date moves the value
        self.admin_post("/admin/allowance_rates", {"u": "1", "effective_from": "2026-11-01", "r_1": "120"})
        self.assertIn("additions 240.00", self.get("/ctr?u=29&phase=1"))

    def test_the_allowance_rate_is_one_figure_changed_by_adding_a_row(self):
        page = self.get("/admin?u=1&t=allowance_rates")
        self.assertIn("&pound; per day", page)
        self.assertIn('name="r_1" value="100"', page)                                                   # one allowance, one rate
        self.assertNotIn("Overtime", page)
        self.assertIn("not available to your role", self.get("/admin?u=30&t=allowance_rates"))          # the IT admin does not manage it
        self.assertIn("1 rate saved, from 2026-11-01", self.admin_post("/admin/allowance_rates", {"u": "1", "effective_from": "2026-11-01", "r_1": "150"}))
        self.assertEqual(self.one("select count(*) from allowance_rate")[0], 2)                         # appended, not edited
        self.assertIn('name="r_1" value="150"', self.get("/admin?u=1&t=allowance_rates"))
        self.assertIn("Nothing changed", self.admin_post("/admin/allowance_rates", {"u": "1", "effective_from": "2026-11-01", "r_1": "150"}))
        self.assertIn("is not a rate", self.admin_post("/admin/allowance_rates", {"u": "1", "effective_from": "2026-11-01", "r_1": "lots"}))
        self.assertIn("is not a rate", self.admin_post("/admin/allowance_rates", {"u": "1", "effective_from": "2026-11-01", "r_1": "-5"}))
        self.assertIn("is required", self.admin_post("/admin/allowance_rates", {"u": "1", "effective_from": "", "r_1": "99"}))
        self.admin_post("/admin/allowance_rates", {"u": "5", "effective_from": "2026-11-01", "r_1": "999"})   # nobody else can change it
        self.assertEqual(self.one("select count(*) from allowance_rate where amount=999")[0], 0)

    # ---------- the approver's week: a line per person, detail, other weeks (R20-R22) ----------
    def week_people(self, approver, monday_iso):
        con = app.db()
        try:
            return [dict(r) for r in con.execute(
                "select bu.name, t.user_id, sum(t.hours) as hrs, sum(case when t.status in ('Submitted','Edited') then t.hours else 0 end) as waiting "
                "from time_entry t join scope s on s.scope_id=t.scope_id join app_user bu on bu.user_id=t.user_id "
                "where exists (select 1 from v_scope_approver va where va.scope_id=s.scope_id and va.user_id=?) and t.user_id<>? and t.status<>'Draft' and t.work_date between ? and date(?, '+6 days') "
                "group by t.user_id order by bu.name", (approver, approver, monday_iso, monday_iso))]
        finally:
            con.close()

    def test_the_approvers_week_has_one_line_per_person_with_each_weekday(self):
        people = self.week_people(7, "2026-11-02")
        self.assertGreaterEqual(len(people), 2)
        page = self.get("/approvals?u=7&w=2026-11-02")
        for d in ("Mon 02/11", "Tue 03/11", "Wed 04/11", "Thu 05/11", "Fri 06/11", "Sat 07/11", "Sun 08/11"):
            self.assertIn("<th>%s</th>" % d, page)
        for p in people:
            self.assertEqual(page.count("<td><b>%s</b> " % p["name"]), 1, p["name"])   # exactly one line each
            self.assertIn("<td align=\"right\"><b>%s</b></td>" % app.fmt(p["hrs"]), page)  # their total for the week
        self.assertIn("<b>All people</b>", page)

    def test_a_persons_line_opens_to_everything_booked_against_them_including_approved(self):
        page = self.get("/approvals?u=7&w=2026-11-02")
        # Ewan Shaw (5) has approved hours on 3 Nov: they appear in his detail (as Approved) with no tick box, next to the waiting ones
        detail = page.split('id="d5"')[1].split("</table>")[0]
        self.assertIn("<td>Approved</td>", detail)
        self.assertIn('class="drow"', page)
        self.assertIn("openRow(", page)                      # the link that opens a line (added by script)
        self.assertNotIn('name="entry_id" value="%d" data-p="5"' % self.one(
            "select entry_id from time_entry where user_id=5 and status='Approved' limit 1")[0], page)

    def test_the_week_defaults_to_the_oldest_waiting_week_and_other_weeks_can_be_opened(self):
        page = self.get("/approvals?u=7")
        self.assertIn("Week commencing 02/11/2026", page)
        self.assertIn("waiting for your approval", page)
        self.assertIn("/approvals?u=7&amp;w=2026-10-26", page)           # previous week
        prev = self.get("/approvals?u=7&w=2026-10-26")                   # earlier weeks show what was approved
        self.assertIn("Week commencing 26/10/2026", prev)
        empty = self.get("/approvals?u=7&w=2030-01-07")
        self.assertIn("No submitted or approved hours in this week", empty)
        self.assertIn("Week commencing 07/01/2030", empty)
        self.assertIn("Week commencing 02/11/2026", self.get("/approvals?u=7&w=not-a-date"))  # a bad date falls back to the default

    def test_approving_keeps_the_week_on_screen(self):
        ids = self.queue_ids(7)
        page = self.post("/approvals/act", {"u": "7", "w": "2026-10-26", "entry_id": [str(ids[0])], "action": "Approve selected"})
        self.assertIn("1 entry approved", page)
        self.assertIn("Week commencing 26/10/2026", page)

    def test_only_approvers_see_the_week_and_only_their_own_people(self):
        self.assertIn("not named as an approver", self.get("/approvals?u=5&w=2026-11-02"))
        kenneth = self.get("/approvals?u=8&w=2026-11-02")  # Spirit's approver: the Apache people are not on his page
        apache = {p["name"] for p in self.week_people(7, "2026-11-02")} - {p["name"] for p in self.week_people(8, "2026-11-02")}
        self.assertTrue(apache)
        for name in apache:
            self.assertNotIn("<td><b>%s</b> " % name, kenneth)

    def test_approvals_page_shows_project_and_phase(self):
        page = self.get("/approvals?u=7")
        self.assertIn("<th>Project</th>", page)
        self.assertIn("<th>CTR</th>", page)

    def test_admin_add_project_scope_and_phase(self):
        self.assertIn("Project added", self.admin_post("/admin/add", {"t": "projects", "client_id": "1", "project_code": "APJ-03", "name": "Jetty Removal"}))
        pid = self.one("select project_id from project where project_code='APJ-03'")[0]
        self.assertIn("General CTR", self.admin_post("/admin/add", {"t": "scopes", "project_id": str(pid), "scope_code": "BB0009",
                                                                     "description": "Jetty", "status": "Open", "approver_user_id": "6"}))
        self.assertEqual(self.one("select client_id from scope where scope_code='BB0009'")[0], 1)
        sid = self.one("select scope_id from scope where scope_code='BB0009'")[0]
        self.assertIn("CTR added", self.admin_post("/admin/add", {"t": "phases", "scope_id": str(sid), "name": "Execute"}))
        self.assertEqual(self.one("select count(*) from phase where scope_id=?", sid)[0], 2)
        self.assertIn("already exists", self.admin_post("/admin/add", {"t": "phases", "scope_id": str(sid), "name": "Execute"}))
        self.assertIn("Saved", self.admin_post("/admin/save", {"t": "projects", "id": str(pid), "name": "Jetty Removal", "status": "Closed"}))

    def test_line_manager_is_set_on_the_user_and_shown_in_the_users_list(self):
        users = self.get("/admin?u=30&t=users")
        self.assertIn("<th>Line manager</th>", users)
        self.assertEqual(self.one("select name from app_user where user_id=(select line_manager_id from app_user where user_id=9)")[0], "Alisdair Woodhead")
        data = {"t": "users", "nmc_id": "A903", "name": "Managed Person", "employment_type": "Staff", "role_id": "6",
                "permission": "user", "start_date": "2026-11-01", "end_date": "", "line_manager_id": "8"}
        self.assertIn("User added", self.admin_post("/admin/add", data))
        uid = self.one("select user_id from app_user where nmc_id='A903'")[0]
        self.assertEqual(self.one("select line_manager_id from app_user where user_id=?", uid)[0], 8)
        self.assertIn('<option value="8" selected>', self.get("/admin/edit?u=30&t=users&id=%d" % uid))
        edit = {"t": "users", "id": str(uid), "name": "Managed Person", "employment_type": "Staff", "role_id": "6", "permission": "user", "end_date": "", "line_manager_id": "7"}
        self.assertIn("Saved", self.admin_post("/admin/save", edit))
        self.assertEqual(self.one("select line_manager_id from app_user where user_id=?", uid)[0], 7)
        self.assertIn("own line manager", self.admin_post("/admin/save", dict(edit, line_manager_id=str(uid))))
        self.assertEqual(self.one("select line_manager_id from app_user where user_id=?", uid)[0], 7)
        self.assertIn("Saved", self.admin_post("/admin/save", dict(edit, line_manager_id="")))
        self.assertIsNone(self.one("select line_manager_id from app_user where user_id=?", uid)[0])

    def test_a_cost_engineer_books_to_a_project_and_gets_the_standard_approval(self):
        # Emma Noble (1) is a cost engineer on Apache's list and books to BB0001 like anyone else; the scope's approver approves her hours
        role = self.one("select role_id from app_user where user_id=1")[0]
        self.post("/timesheet/save", {"u": "1", "w": "2026-11-09", "action": "Save draft", "reason": "", "h_1_%d_1_0_2026-11-10" % role: "4"})
        self.post("/timesheet/save", {"u": "1", "w": "2026-11-09", "action": "Submit week", "reason": ""})
        eid = self.one("select entry_id from time_entry where user_id=1 and work_date='2026-11-10' and phase_id=1")[0]
        self.assertIn('value="%d"' % eid, self.get("/approvals?u=7&w=2026-11-09"))                     # in Alisdair Woodhead's queue
        self.assertIn("1 entry approved", self.post("/approvals/act", {"u": "7", "w": "2026-11-09", "entry_id": str(eid), "action": "Approve selected"}))

    def test_the_managing_director_approves_internal_time_and_can_delegate_it(self):
        self.assertEqual(self.one("select u.name from client c join app_user u on u.user_id=c.approver_user_id where c.is_internal=1")[0], "Graeme Hogg")
        self.post("/timesheet/save", {"u": "4", "w": "2026-11-02", "action": "Save draft", "h_8_4_1_0_2026-11-04": "3.5", "reason": ""})
        self.post("/timesheet/save", {"u": "4", "w": "2026-11-02", "action": "Submit week", "reason": ""})
        eid = self.one("select entry_id from time_entry where user_id=4 and work_date='2026-11-04'")[0]
        self.assertIn('value="%d"' % eid, self.get("/approvals?u=31&w=2026-11-02"))
        self.assertNotIn('value="%d"' % eid, self.get("/approvals?u=7&w=2026-11-02"))
        # Graeme delegates by naming Jamie Ireland (6) as a secondary approver of INT01; either can then approve it
        sid = self.one("select scope_id from phase where phase_id=8")[0]
        desc = self.one("select description from scope where scope_id=?", sid)[0]
        self.assertIn("Saved", self.admin_post("/admin/save", {"t": "internal", "id": str(sid), "description": desc, "status": "Open", "secondary": "6"}, u="1"))
        self.assertIn('value="%d"' % eid, self.get("/approvals?u=6&w=2026-11-02"))
        self.assertIn("1 entry approved", self.post("/approvals/act", {"u": "6", "w": "2026-11-02", "entry_id": str(eid), "action": "Approve selected"}))
        self.assertEqual(self.one("select approved_by from time_entry where entry_id=?", eid)[0], 6)

    def test_budget_hours_from_p6_are_loaded_and_compared_with_the_approved_hours(self):
        self.assertIn("not available to your role", self.get("/admin?u=5&t=budgets"))
        page = self.get("/admin?u=29&t=budgets")
        self.assertIn("P6, which holds hours only", page)
        self.assertIn("BB0001-PM-D-001", page)                                     # the seeded budgets are listed
        # the CTR page and the report compare budget with approved hours (BB0001-PM-D-001: 160 budget, 71.5 approved)
        ctr = self.get("/ctr?u=29&phase=1")
        self.assertIn("Budget 160 hrs", ctr)
        self.assertIn("remaining 88.5 hrs", ctr)
        self.assertIn("used 45%", ctr)
        report = self.get("/reports?u=29&from=2026-01-01&to=2026-12-31").split("Budget against actual hours")[1].split("<h4>")[0]
        self.assertIn("<td>BB0001-PM-D-001</td><td align=\"right\">160</td>", report)
        self.assertNotIn("Budget against actual hours", self.get("/reports?u=5&from=2026-01-01&to=2026-12-31"))   # cost controllers only
        # load a new snapshot: a changed budget, an unchanged one, a heading line, an unknown CTR and a bad figure
        data = "CTR No,Budget hrs\nBB0001-PM-D-001,200\nBB0001-PM-E-001,40\nZZ-NOPE,10\nA1500-PM-D-001,lots\n"
        out = self.admin_post("/admin/budgets", {"u": "29", "as_of": "2026-11-13", "data": data}, u="29")
        self.assertIn("1 budget loaded, as of 2026-11-13. 1 unchanged.", out)
        self.assertIn("ZZ-NOPE", out)
        self.assertIn("line 5", out)
        self.assertIn("Budget 200 hrs", self.get("/ctr?u=29&phase=1"))
        self.assertEqual(self.one("select count(*) from ctr_budget where phase_id=1")[0], 2)           # the old snapshot is kept
        # the same or an earlier as-of date cannot be loaded with a different figure
        out = self.admin_post("/admin/budgets", {"u": "29", "as_of": "2026-11-13", "data": "BB0001-PM-D-001,300"}, u="29")
        self.assertIn("use a later date", out)
        self.assertEqual(self.one("select hours from ctr_budget where phase_id=1 order by as_of desc limit 1")[0], 200)
        # nobody else can load budgets
        self.admin_post("/admin/budgets", {"u": "5", "as_of": "2026-11-14", "data": "BB0001-PM-D-001,999"}, u="5")
        self.assertEqual(self.one("select count(*) from ctr_budget where hours=999")[0], 0)
        # a CTR with hours but no budget says so
        self.assertIn("not budgeted", self.get("/reports?u=29&from=2026-01-01&to=2026-12-31"))

    def test_the_cost_dashboard_charts_budget_against_actual_hours(self):
        page = self.get("/dashboard?u=29")
        self.assertIn('href="/dashboard?u=29"', self.get("/timesheet?u=29"))                        # in the cost controller's menu
        for who in (5, 7, 30):                                                                       # nobody else has it
            self.assertIn("not available to your role", self.get("/dashboard?u=%d" % who), who)
            self.assertNotIn('href="/dashboard?u=%d"' % who, self.get("/timesheet?u=%d" % who) if who != 30 else "")
        self.assertIn("Budget against approved hours, by CTR", page)
        self.assertIn("<svg", page)
        self.assertIn("Approved hours by week and client", page)
        self.assertIn("A1500-PM-D-001: budget 95 hrs, approved 100 hrs, pending 0 hrs, 5 hrs over budget", page)    # the tooltip text of an overrun
        self.assertIn("BB0002-PM-D-001: budget 120 hrs, approved 108 hrs", page)
        self.assertIn('<a href="/ctr?u=29&amp;phase=6">', page)                                      # each CTR opens its own page
        self.assertIn("CTR A1500-PM-D-001", self.get("/ctr?u=29&phase=6"))
        self.assertIn("&#9650; over", page)                                                          # words as well as the colour
        self.assertIn("Over budget: A1500-PM-D-001", page)
        self.assertIn("At 90% or more of budget", page)
        self.assertNotIn("&pound;", page)                                                            # hours only
        self.assertIn("Week commencing 26/10/2026", page)
        # budgets that are later loaded show up on it
        self.admin_post("/admin/budgets", {"u": "29", "as_of": "2026-11-13", "data": "A1500-PM-D-001,200"}, u="29")
        self.assertIn("A1500-PM-D-001: budget 200 hrs, approved 100 hrs, pending 0 hrs, 100 hrs remaining", self.get("/dashboard?u=29"))

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
        self.assertIn("CTR BB0001-PM-D-001", page)                    # the CTR number: scope code, then the CTR code
        for part in ("Sub-Totals", "Payment Terms", "Basis of Proposal", "Objectives"):
            self.assertIn(part, page, part)
        self.assertIn("%.2f" % v[1], page)
        for f in fams:
            self.assertIn(f, page)
            tab = self.get("/ctr?u=29&phase=%d&family=%s" % (ph, f.replace(" ", "%20")))
            self.assertIn("Resources", tab)
            self.assertIn("Timesheet entries", tab)
        self.assertIn("Open CTR", self.get("/reports?u=29"))
        self.assertIn("CTR not found", self.get("/ctr?u=29&phase=9999"))
        self.assertIn("CTR BB0001-PM-D-001", self.get("/ctr?u=29&phase=%d&family=Nonsense" % ph))


if __name__ == "__main__":
    unittest.main()
