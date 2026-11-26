"""The Reports page as the commercial team want it: the manhour report, and the Ltd company / PAYE contractor hours for AAA. The other reports are hidden, not removed."""
import io
import os
import sys
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pgtest import clone  # noqa: E402
import app  # noqa: E402
from nmc import core, reports  # noqa: E402

CONTROLLER = 29
EWAN = 5            # Contractor - LTD in the seed: hours on 26 Oct to 6 Nov


class TestContractorReports(unittest.TestCase):
    def setUp(self):
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

    def rows(self, sql, *args):
        con = app.db()
        try:
            return [tuple(r) for r in con.execute(sql, args)]
        finally:
            con.close()

    def run_sql(self, sql, *args):
        con = app.db()
        try:
            con.execute(sql, args)
            con.commit()
        finally:
            con.close()

    def xlsx_text(self, path):
        with urllib.request.urlopen(self.base + path) as r:
            self.assertIn("spreadsheetml", r.headers["Content-Type"])
            z = zipfile.ZipFile(io.BytesIO(r.read()))
        return "".join(z.read(n).decode("utf-8") for n in z.namelist() if n.endswith(".xml"))

    def main(self, path):
        return self.get(path).split("<main>")[1]

    def paye(self, user=EWAN):
        self.run_sql("update app_user set employment_type='Contractor - PAYE' where user_id=?", user)

    # ---- the page ----
    def test_the_reports_page_has_only_the_wanted_reports(self):
        page = self.get("/reports?u=%d" % CONTROLLER)
        for key in ("ltd", "paye_week", "paye_month"):
            self.assertIn('id="rb-%s"' % key, page, key)
        self.assertIn("Manhours by client", page)
        for hidden in ("ctr", "period", "budget", "aaa", "weekly", "internal", "loc", "allow", "month"):
            self.assertNotIn('id="rb-%s"' % hidden, page, hidden)
        self.assertNotIn("Timesheet grid", page)
        self.assertNotIn("Actual booked hrs", page)                     # the CTR tree is not on the page

    def test_the_hidden_reports_still_work(self):
        page = self.get("/reports?u=%d&all=1" % CONTROLLER)
        for key in ("ctr", "period", "budget", "aaa", "internal", "loc", "allow", "month"):
            self.assertIn('id="rb-%s"' % key, page, key)
        self.assertIn('id="rp-month" style="display:block"', self.get("/reports?u=%d&r=month&m=2026-11" % CONTROLLER))      # an old link still opens its report
        self.assertIn("spreadsheetml", urllib.request.urlopen(self.base + "/reports/aaa.xlsx?u=%d&from=2026-10-01&to=2026-11-30" % CONTROLLER).headers["Content-Type"])

    def test_an_approver_sees_the_manhour_report_but_not_the_contractor_hours(self):
        page = self.get("/reports?u=7")
        self.assertIn("Manhours by client", page)
        self.assertNotIn('id="rb-ltd"', page)
        for kind in ("ltd", "paye_week", "paye_month"):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(self.base + "/reports/export.xlsx?u=7&k=%s" % kind)
            self.assertEqual(cm.exception.code, 404, kind)
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(self.base + "/reports/export.xlsx?u=5&k=ltd")                      # a standard user has no reports

    # ---- Ltd company hours: a month's total per person and location ----
    def test_ltd_company_hours_are_the_months_total_per_person_and_location(self):
        want = self.rows("select p.nmc_id, lo.name, p.name, sum(t.hours) from time_entry t join app_user p on p.user_id=t.user_id join location lo on lo.location_id=t.location_id "
                         "where t.status<>'Draft' and p.employment_type='Contractor - LTD' and t.work_date between '2026-11-01' and '2026-11-30' "
                         "group by p.nmc_id, lo.location_id, lo.name, p.name order by p.nmc_id, lo.location_id")
        self.assertTrue(want)
        title, sub, head, rows = reports.contractor_hours(self.con(), "ltd", {"month": ["2026-11"]})
        self.assertEqual(head, ["Person: NMC User ID", "Timesheet Location", "Person: Full Name (Firstname Lastname)", "Date", "Time Booked (hrs)"])
        self.assertEqual([(r[0], r[1], r[2], r[4]) for r in rows], want)
        self.assertEqual({r[3] for r in rows}, {"Nov 2026"})
        page = self.main("/reports?u=%d&r=ltd&month=2026-11" % CONTROLLER)
        self.assertIn("Ewan Shaw", page)
        self.assertIn("Nov 2026", page)
        text = self.xlsx_text("/reports/export.xlsx?u=%d&k=ltd&month=2026-11" % CONTROLLER)
        self.assertIn("Ewan Shaw", text)
        self.assertIn("Time Booked (hrs)", text)

    def test_drafts_are_left_out_and_only_ltd_contractors_are_in_the_ltd_report(self):
        self.run_sql("update time_entry set status='Draft', reopen_reason='Reopened to correct', last_edited_by=?, last_edited_at='2026-11-20 10:00:00' "
                     "where entry_id=(select min(entry_id) from time_entry where user_id=? and status='Approved' and work_date between '2026-11-01' and '2026-11-30')", CONTROLLER, EWAN)
        _, _, _, before = reports.contractor_hours(self.con(), "ltd", {"month": ["2026-11"]})
        every = self.rows("select coalesce(sum(t.hours), 0) from time_entry t join app_user p on p.user_id=t.user_id where p.employment_type='Contractor - LTD' "
                          "and t.work_date between '2026-11-01' and '2026-11-30'")[0][0]
        counted = self.rows("select coalesce(sum(t.hours), 0) from time_entry t join app_user p on p.user_id=t.user_id where t.status<>'Draft' and p.employment_type='Contractor - LTD' "
                            "and t.work_date between '2026-11-01' and '2026-11-30'")[0][0]
        self.assertGreater(every, counted)                                                           # the seed has Ltd contractors' drafts: none of them are in the figure
        self.assertEqual(sum(r[4] for r in before), counted)
        self.run_sql("update app_user set employment_type='Staff' where user_id=?", EWAN)             # no longer a Ltd contractor: out of the report
        _, _, _, after = reports.contractor_hours(self.con(), "ltd", {"month": ["2026-11"]})
        self.assertNotIn("Ewan Shaw", {r[2] for r in after})
        self.assertIn("Ewan Shaw", {r[2] for r in before})

    # ---- PAYE weekly hours for AAA ----
    def test_paye_weekly_hours_are_each_weeks_total_for_paye_contractors_only(self):
        self.paye()
        _, sub, _, rows = reports.contractor_hours(self.con(), "paye_week", {"from": ["2026-10-26"], "to": ["2026-11-08"]})
        self.assertEqual({r[3] for r in rows}, {"26 Oct 2026", "02 Nov 2026"})                       # a row per week
        self.assertEqual({r[2] for r in rows}, {"Ewan Shaw"})                                         # nobody else is PAYE
        for r in rows:
            wk = {"26 Oct 2026": ("2026-10-26", "2026-11-01"), "02 Nov 2026": ("2026-11-02", "2026-11-08")}[r[3]]
            self.assertEqual(r[4], self.rows("select sum(hours) from time_entry where user_id=? and status<>'Draft' and work_date between ? and ? and location_id=(select location_id from location where name=?)",
                                             EWAN, wk[0], wk[1], r[1])[0][0])
        page = self.main("/reports?u=%d&r=paye_week&from=2026-10-26&to=2026-11-08" % CONTROLLER)
        self.assertIn("PAYE weekly hours", page)
        self.assertIn("26 Oct 2026", page)
        self.assertIn("Ewan Shaw", page)
        self.assertIn("Ewan Shaw", self.xlsx_text("/reports/export.xlsx?u=%d&k=paye_week&from=2026-10-26&to=2026-11-08" % CONTROLLER))

    def test_the_paye_weekly_report_defaults_to_the_last_complete_week(self):
        a, b = reports.week_range({})
        self.assertEqual((a.weekday(), (b - a).days), (0, 6))
        self.assertLess(b, __import__('datetime').date.today())

    # ---- PAYE monthly hours for AAA ----
    def test_paye_monthly_hours_are_split_by_week_and_by_the_days_in_the_month(self):
        self.paye()
        total = lambda d1, d2: self.rows("select coalesce(sum(hours),0) from time_entry where user_id=? and status<>'Draft' and work_date between ? and ?", EWAN, d1, d2)[0][0]
        _, _, _, october = reports.contractor_hours(self.con(), "paye_month", {"month": ["2026-10"]})
        _, _, _, november = reports.contractor_hours(self.con(), "paye_month", {"month": ["2026-11"]})
        self.assertEqual(sum(r[4] for r in october), total("2026-10-01", "2026-10-31"))
        self.assertEqual(sum(r[4] for r in november), total("2026-11-01", "2026-11-30"))
        # the week commencing 26 Oct runs into November: October counts its days (26 to 31 Oct) and November the rest (1 Nov), each under that week
        self.assertEqual(sum(r[4] for r in october if r[3] == "26 Oct 2026"), total("2026-10-26", "2026-10-31"))
        self.assertEqual(sum(r[4] for r in november if r[3] == "26 Oct 2026"), total("2026-11-01", "2026-11-01"))
        text = self.xlsx_text("/reports/export.xlsx?u=%d&k=paye_month&month=2026-11" % CONTROLLER)
        self.assertIn("Ewan Shaw", text)

    def con(self):
        return app.db()


if __name__ == "__main__":
    unittest.main()
