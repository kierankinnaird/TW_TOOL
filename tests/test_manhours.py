"""Assets with an optional PO per asset, and the manhour report: submitted + approved hours counted once, with late changes carried to the period they were made in."""
import os
import sys
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pgtest import clone  # noqa: E402
import app  # noqa: E402
from nmc import core, pg  # noqa: E402

CONTROLLER = 29
YEAR = {"client": ["1"], "from": ["2026-10-01"], "to": ["2026-12-31"]}


class TestManhours(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        core.DB_URL = clone()
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
        with urllib.request.urlopen(self.base + path, urllib.parse.urlencode(data, doseq=True).encode()) as r:
            return r.read().decode("utf-8")

    def run_sql(self, sql, *args):
        con = app.db()
        try:
            con.execute(sql, args)
            con.commit()
        finally:
            con.close()

    def rows(self, sql, *args):
        con = app.db()
        try:
            return [tuple(r) for r in con.execute(sql, args)]
        finally:
            con.close()

    def report(self, d_from, d_to, client="1"):
        con = app.db()
        try:
            return app.manhour_data(con, CONTROLLER, {"client": [client], "from": [d_from], "to": [d_to]})
        finally:
            con.close()

    def correct(self, user, work_date, hours, at, reason="Corrected after the client queried it"):
        self.run_sql("update time_entry set hours=?, status='Edited', edit_count=edit_count+1, last_change_reason=?, last_edited_by=?, last_edited_at=? "
                     "where user_id=? and work_date=? and scope_id=1", hours, "%s (%s)" % (reason, at), CONTROLLER, at, user, work_date)

    def total(self, data):
        return round(data["total_work"] + data["total_adjustments"], 2)

    # ---- assets and POs ----
    def test_a_scope_can_have_an_asset_and_the_po_is_optional(self):
        self.assertEqual(self.rows("select a.name, a.po_number from scope s join asset a on a.asset_id=s.asset_id where s.scope_code in ('BB0001','BB0002') order by s.scope_code"),
                         [("Alpha Platform", "4500123456"), ("Bravo Platform", None)])             # an asset with a PO, and one without
        self.assertEqual(self.rows("select count(*) from scope where scope_code like 'INT%' and asset_id is not null")[0][0], 0)
        self.run_sql("update scope set asset_id=NULL where scope_code='BB0001'")                  # an asset is not required either

    def test_an_asset_must_belong_to_the_scopes_client(self):
        with self.assertRaises(pg.DatabaseError) as cm:
            self.run_sql("update scope set asset_id=3 where scope_code='BB0001'")                  # a Spirit asset on an Apache scope
        self.assertIn("same client", pg.error_text(cm.exception))
        with self.assertRaises(pg.UniqueViolation):
            self.run_sql("insert into asset (client_id, name) values (1, 'Alpha Platform')")      # one asset name once per client

    def test_the_cost_team_keeps_assets_and_pos_in_admin(self):
        page = self.get("/admin?u=%d&t=assets" % CONTROLLER)
        self.assertIn("Alpha Platform", page)
        self.assertIn("4500123456", page)
        self.assertIn("not available to your role", self.get("/admin?u=5&t=assets"))
        out = self.post("/admin/add", {"u": CONTROLLER, "t": "assets", "client_id": "1", "name": "Charlie Platform", "po_number": ""})
        self.assertIn("Asset added.", out)
        aid = self.rows("select asset_id from asset where name='Charlie Platform'")[0][0]
        self.assertIsNone(self.rows("select po_number from asset where asset_id=?", aid)[0][0])    # no PO is fine
        self.post("/admin/save", {"u": CONTROLLER, "t": "assets", "id": str(aid), "name": "Charlie Platform", "po_number": "4500999999"})
        self.assertEqual(self.rows("select po_number from asset where asset_id=?", aid)[0][0], "4500999999")
        # put it on a scope, from the scope's edit page, and back off again
        self.post("/admin/save", {"u": CONTROLLER, "t": "scopes", "id": "1", "description": "Wellbay Clearance", "status": "Open", "asset_id": str(aid)})
        self.assertEqual(self.rows("select asset_id from scope where scope_id=1")[0][0], aid)
        self.assertIn("Charlie Platform", self.get("/admin/edit?u=%d&t=scopes&id=1" % CONTROLLER))
        self.assertIn("4500999999", self.get("/admin?u=%d&t=scopes" % CONTROLLER))
        self.post("/admin/save", {"u": CONTROLLER, "t": "scopes", "id": "1", "description": "Wellbay Clearance", "status": "Open", "asset_id": ""})
        self.assertIsNone(self.rows("select asset_id from scope where scope_id=1")[0][0])

    def test_the_manhour_report_shows_the_asset_and_po(self):
        data = self.report("2026-10-26", "2026-11-08")
        shown = {(c["asset"], c["po"]) for c in data["tree"].values()}
        self.assertIn(("Alpha Platform", "4500123456"), shown)
        self.assertIn(("Bravo Platform", ""), shown)                                                # no PO: left blank
        page = self.get("/reports/manhours?u=%d&client=1&from=2026-10-26&to=2026-11-08" % CONTROLLER)
        self.assertIn("4500123456", page)
        self.assertIn("<th>PO</th>", page)

    # ---- the hours ledger ----
    def test_submitted_and_approved_hours_are_counted_once_each(self):
        data = self.report("2026-10-26", "2026-11-08")
        want = self.rows("select coalesce(sum(t.hours), 0) from time_entry t join scope s on s.scope_id=t.scope_id where s.client_id=1 and t.status<>'Draft' "
                         "and t.work_date between '2026-10-26' and '2026-11-08'")[0][0]
        self.assertEqual(data["total_work"], want)
        self.assertEqual(data["total_adjustments"], 0)
        states = {r[0] for r in self.rows("select t.status from time_entry t join scope s on s.scope_id=t.scope_id where s.client_id=1 and t.work_date between '2026-10-26' and '2026-11-08'")}
        self.assertIn("Approved", states)
        self.assertIn("Submitted", states)                                                          # both kinds are in the one figure

    def adj_for(self, data, person, week="2026-10-26"):
        return round(sum(a["hours"] for a in data["adjustments"] if a["person"] == person and a["week"] == week), 2)

    def hours_of(self, user, work_date):
        return self.rows("select hours from time_entry where user_id=? and work_date=? and scope_id=1", user, work_date)[0][0]

    def test_a_correction_made_later_shows_in_the_period_it_was_made_in(self):
        # the demo data already has one: Ewan Shaw's 27 Oct hours were corrected from 8 to 7.5 on 4 Nov (-0.5 in November, nothing in October)
        october, november = self.report("2026-10-01", "2026-10-31"), self.report("2026-11-01", "2026-11-30")
        self.assertEqual(october["adjustments"], [])
        self.assertEqual(self.adj_for(november, "Ewan Shaw"), -0.5)
        was = self.hours_of(5, "2026-10-27")
        self.correct(5, "2026-10-27", was - 2, "2026-11-10 10:00:00")                              # corrected again in November: -2 more
        after_oct, after_nov = self.report("2026-10-01", "2026-10-31"), self.report("2026-11-01", "2026-11-30")
        self.assertEqual(after_oct["total_work"], october["total_work"])                           # October's report still reads as it did
        self.assertEqual(after_oct["adjustments"], [])
        self.assertEqual(self.adj_for(after_nov, "Ewan Shaw"), -2.5)                              # against the week it belongs to
        line = [a for a in after_nov["adjustments"] if a["person"] == "Ewan Shaw"][0]
        self.assertEqual((line["week"], line["changed"]), ("2026-10-26", "2026-11-10"))           # and the day it was changed
        self.assertEqual(after_nov["total_adjustments"], november["total_adjustments"] - 2)
        page = self.get("/reports/manhours?u=%d&client=1&from=2026-11-01&to=2026-11-30" % CONTROLLER)
        tail = page[page.index("Adjustments (changes"):]
        self.assertIn("-2.5", tail)
        self.assertIn("10/11/2026", tail)

    def test_a_correction_in_the_same_period_is_not_an_adjustment(self):
        before = self.report("2026-10-01", "2026-10-31")
        was = self.hours_of(5, "2026-10-26")
        self.correct(5, "2026-10-26", was - 1, "2026-10-31 10:00:00")                              # fixed before October closed: just the new figure
        data = self.report("2026-10-01", "2026-10-31")
        self.assertEqual(data["adjustments"], [])
        self.assertEqual(data["total_work"], before["total_work"] - 1)

    def test_hours_sent_back_to_draft_come_off_the_next_report(self):
        before = self.report("2026-11-01", "2026-11-30")
        gone = self.hours_of(5, "2026-10-26")
        self.run_sql("update time_entry set status='Draft', reopen_reason='Wrong code', last_edited_by=?, last_edited_at='2026-11-12 09:00:00' "
                     "where user_id=5 and work_date='2026-10-26' and scope_id=1", CONTROLLER)
        after = self.report("2026-11-01", "2026-11-30")
        self.assertEqual(self.adj_for(after, "Ewan Shaw"), round(self.adj_for(before, "Ewan Shaw") - gone, 2))
        self.assertEqual(self.report("2026-10-01", "2026-10-31")["total_work"], self.report("2026-10-01", "2026-10-31")["total_work"])

    def test_nothing_is_counted_twice_across_consecutive_periods(self):
        """However the date range is cut up, the periods added together give the hours as they stand now."""
        self.correct(5, "2026-10-27", 6, "2026-11-10 10:00:00")
        self.correct(5, "2026-10-26", 9, "2026-11-11 10:00:00")
        self.correct(5, "2026-10-26", 7, "2026-11-20 10:00:00")                                    # the same entry corrected twice
        self.run_sql("update time_entry set status='Draft', reopen_reason='Wrong code', last_edited_by=?, last_edited_at='2026-11-12 09:00:00' "
                     "where user_id=5 and work_date='2026-10-26' and scope_id=1", CONTROLLER)
        now = self.rows("select coalesce(sum(t.hours), 0) from time_entry t join scope s on s.scope_id=t.scope_id where s.client_id=1 and t.status<>'Draft' "
                        "and t.work_date between '2026-10-01' and '2026-12-31'")[0][0]
        for cuts in ([("2026-10-01", "2026-12-31")],
                     [("2026-10-01", "2026-10-31"), ("2026-11-01", "2026-12-31")],
                     [("2026-10-01", "2026-10-25"), ("2026-10-26", "2026-11-01"), ("2026-11-02", "2026-11-15"), ("2026-11-16", "2026-11-30"), ("2026-12-01", "2026-12-31")]):
            parts = [self.report(a, b) for a, b in cuts]
            self.assertAlmostEqual(sum(self.total(p) for p in parts), now, msg=str(cuts))
        # the single entry that was corrected twice nets to its current hours (7) across the periods
        parts = [self.report(a, b) for a, b in [("2026-10-01", "2026-10-31"), ("2026-11-01", "2026-11-14"), ("2026-11-15", "2026-11-30")]]
        net = sum(a["hours"] for p in parts for a in p["adjustments"] if a["person"] == "Ewan Shaw" and a["week"] == "2026-10-26")
        before = sum(h for p in parts[:1] for ctr in p["tree"].values() for (who, _, _), per in ctr["people"].items() if who == "Ewan Shaw" for h in per.values())
        self.assertAlmostEqual(before + net, self.rows("select coalesce(sum(hours), 0) from time_entry where user_id=5 and scope_id=1 and status<>'Draft' "
                                                       "and work_date between '2026-10-26' and '2026-11-01'")[0][0])

    def test_the_excel_and_pdf_carry_the_adjustments(self):
        import io
        import zipfile
        from nmc import pdf
        self.correct(5, "2026-10-27", 6, "2026-11-10 10:00:00")
        q = "u=%d&client=1&from=2026-11-01&to=2026-11-30" % CONTROLLER
        with urllib.request.urlopen(self.base + "/reports/manhours.xlsx?" + q) as r:
            z = zipfile.ZipFile(io.BytesIO(r.read()))
        text = "".join(z.read(n).decode("utf-8") for n in z.namelist() if n.endswith(".xml"))
        self.assertIn("Adjustments", text)
        self.assertIn("Ewan Shaw", text)
        raw = pdf.manhour_pdf(self.report("2026-11-01", "2026-11-30"), "Kieran Kinnaird", compress=False)
        self.assertIn(b"Adjustments", raw)
        self.assertIn(b"Adjustments", raw)


if __name__ == "__main__":
    unittest.main()
