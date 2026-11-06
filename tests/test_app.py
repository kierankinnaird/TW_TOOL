"""Front-end smoke tests. Run from the repo root: python -m unittest discover -s tests -v"""
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
        self.assertIn("PL-01", page)
        self.assertIn("PL-02", page)
        self.assertIn("Week commencing 02/11/2026", page)

    def test_save_draft_then_submit_then_approve(self):
        # Ewan (5) books Friday 6 Nov on BB0001 (scope 1) as Senior Planner (role 4)
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

    def test_admin_only_pages_and_period_actions(self):
        self.assertIn("Admin only", self.get("/admin?u=5"))
        page = self.post("/admin/period", {"u": "7", "action": "open", "week_start": "2026-11-09"})
        self.assertIn("opened", page)
        page = self.post("/admin/period", {"u": "7", "action": "open", "week_start": "2026-11-10"})  # not a Monday
        self.assertNotIn("opened", page)

    def test_reports_date_range(self):
        page = self.get("/reports?u=5&from=2026-10-26&to=2026-10-26")
        self.assertIn("2026-10-26", page)
        self.assertNotIn("2026-11-02</td>", page.split("Period reports")[1])


if __name__ == "__main__":
    unittest.main()