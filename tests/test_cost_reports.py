"""The cost team's four regular reports, each by client: Booked v CTR, Budget and actual hours, the Weekly cost report and the Invoice back up."""
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
from nmc import core, costreports  # noqa: E402

CONTROLLER = 29
APACHE, SPIRIT = 1, 2


class TestCostReports(unittest.TestCase):
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

    def rep(self, kind, **q):
        con = app.db()
        try:
            return costreports.report(con, kind, {k: [str(v)] for k, v in q.items()})
        finally:
            con.close()

    def xlsx_text(self, path):
        with urllib.request.urlopen(self.base + path) as r:
            self.assertIn("spreadsheetml", r.headers["Content-Type"])
            z = zipfile.ZipFile(io.BytesIO(r.read()))
        return "".join(z.read(n).decode("utf-8") for n in z.namelist() if n.endswith(".xml"))

    def hours(self, client, d1="2026-01-01", d2="2026-12-31", value=False):
        return self.rows("select coalesce(sum(t.hours %s), 0) from time_entry t join scope s on s.scope_id=t.scope_id %s where s.client_id=? and t.status<>'Draft' and t.work_date between ? and ?"
                         % ("* r.sales_rate" if value else "", "join rate r on r.rate_id=t.rate_id" if value else ""), client, d1, d2)[0][0]

    def test_the_reports_page_has_the_four_cost_reports_for_cost_controllers_only(self):
        page = self.get("/reports?u=%d" % CONTROLLER)
        for key in ("ctr_week", "budget_hours", "cost_week", "invoice"):
            self.assertIn('id="rb-%s"' % key, page, key)
        self.assertNotIn('id="rb-ctr_week"', self.get("/reports?u=7"))                       # an approver has no cost reports
        for who in (7, 5):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(self.base + "/reports/cost.xlsx?u=%d&k=invoice&client=1" % who)
            self.assertEqual(cm.exception.code, 403, who)

    def test_the_buttons_open_reports_that_are_already_on_the_page(self):
        # a button only shows or hides a panel that is in the page, so every panel must hold its table before anything is clicked
        page = self.get("/reports?u=%d" % CONTROLLER)
        for key, word in (("ctr_week", "Project: Budget Hours"), ("budget_hours", "Remaining Hours"), ("cost_week", "Booked Value"), ("invoice", "Person: Full Name")):
            panel = page.split('id="rp-%s"' % key)[1].split('class="rpt"')[0]
            self.assertIn(word, panel, key)
            self.assertIn("<table", panel, key)

    def test_every_report_is_filtered_by_client(self):
        for kind in ("ctr_week", "budget_hours", "cost_week", "invoice"):
            kw = {"from": "2026-10-01", "to": "2026-11-30"}
            apache, spirit = self.rep(kind, client=APACHE, **kw), self.rep(kind, client=SPIRIT, **kw)
            ca = {c[0].split(" - ")[0] for _, c in apache["rows"] if c[0] and not c[0].startswith("Total")}
            cs = {c[0].split(" - ")[0] for _, c in spirit["rows"] if c[0] and not c[0].startswith("Total")}
            self.assertTrue(ca and cs, kind)
            self.assertTrue(all(x.startswith(("BB", "FB")) for x in ca), (kind, ca))      # Apache's scopes
            self.assertTrue(all(x.startswith("A") for x in cs), (kind, cs))               # Spirit's scopes
            self.assertEqual(apache["params"]["client_name"], "Apache")
            self.assertEqual(spirit["params"]["client_name"], "Spirit")
        page = self.get("/reports?u=%d&r=budget_hours&client=2" % CONTROLLER)
        self.assertIn("A1500-PM-D-001", page)
        self.assertNotIn("BB0001-PM-D-001", page.split("<main>")[1])

    def test_the_project_filter_narrows_to_one_project(self):
        every = self.rep("budget_hours", client=APACHE)
        one = self.rep("budget_hours", client=APACHE, project=2)                             # Platform Decommissioning only
        self.assertLess(len(one["rows"]), len(every["rows"]))
        self.assertTrue(all(c[0].startswith("BB0002") or c[0].startswith("FB") or c[0] == "Total" for _, c in one["rows"]))

    def test_budget_and_actual_hours(self):
        rep = self.rep("budget_hours", client=APACHE)
        self.assertEqual(rep["header"], ["Project No - Title", "Budget Hours", "Actual Hours", "Remaining Hours"])
        body = [c for k, c in rep["rows"] if k == "row"]
        for ctr, budget, actual, remaining in body:
            if budget is not None:
                self.assertAlmostEqual(remaining, budget - actual)
        self.assertAlmostEqual(sum(c[2] for c in body), self.hours(APACHE))                   # every non-draft hour of the client, once
        wellbay = [c for c in body if c[0].startswith("BB0002")][0]
        self.assertEqual(wellbay[1], 120)                                                      # the seed's budget for BB0002

    def test_booked_v_ctr_is_hours_by_week_and_adds_up(self):
        rep = self.rep("ctr_week", client=SPIRIT, **{"from": "2026-10-26", "to": "2026-11-08"})
        self.assertEqual(rep["header"][:3], ["Project: Project No - Title", "Stage Name", "Project: Budget Hours"])
        self.assertEqual(rep["header"][3:], ["26/10/2026", "02/11/2026", "Total"])
        body = [c for k, c in rep["rows"] if k == "row"]
        for c in body:
            self.assertAlmostEqual(c[-1], sum(x or 0 for x in c[3:-1]))
        grand = rep["rows"][-1][1]
        self.assertEqual(rep["rows"][-1][0], "grand")
        self.assertAlmostEqual(grand[-1], self.hours(SPIRIT, "2026-10-26", "2026-11-08"))
        self.assertAlmostEqual(grand[3], self.hours(SPIRIT, "2026-10-26", "2026-11-01"))     # the first week column

    def test_the_weekly_cost_report_is_booked_value_against_budget_value(self):
        rep = self.rep("cost_week", client=APACHE, **{"from": "2026-10-01", "to": "2026-11-30"})
        self.assertEqual(rep["header"], ["Project: Project No - Title", "Budget Value", "Booked Value", "Remaining Value"])
        body = [c for k, c in rep["rows"] if k == "row"]
        self.assertAlmostEqual(sum(c[2] for c in body), self.hours(APACHE, "2026-10-01", "2026-11-30", value=True), places=1)
        for ctr, budget, booked, remaining in body:
            self.assertGreater(booked, 0)                                                     # only CTRs with hours booked
            if budget is not None:
                self.assertAlmostEqual(remaining, budget - booked, places=1)
        self.assertEqual(rep["rows"][-1][0], "grand")
        # a lump sum CTR is budgeted at its agreed price
        self.rows("select 1")
        con = app.db()
        con.execute("update phase set payment_terms='Lump Sum', lump_sum_value=50000 where phase_id=1")
        con.commit()
        con.close()
        rep = self.rep("cost_week", client=APACHE, **{"from": "2026-10-01", "to": "2026-11-30"})
        self.assertEqual([c[1] for k, c in rep["rows"] if c[0].startswith("BB0001-PM-D-001")][0], 50000)

    def test_a_budget_split_by_role_is_valued_role_by_role_at_the_clients_rates(self):
        rep = self.rep("cost_week", client=APACHE, **{"from": "2026-10-01", "to": "2026-11-30"})
        label = [c[0] for k, c in rep["rows"] if k == "row" and c[1] is not None][0]
        pid = self.rows("select ph.phase_id from phase ph join scope s on s.scope_id=ph.scope_id where s.scope_code || '-' || coalesce(ph.ctr_code, upper(ph.name)) = ?",
                        label.split(" - ")[0])[0][0]
        roles = [r[0] for r in self.rows("select role_id from rate where client_id=? order by role_id limit 2", APACHE)]
        today = __import__("datetime").date.today().isoformat()
        self.run_sql("insert into ctr_budget (phase_id, hours, as_of) values (?, 30, '2099-01-01')", pid)
        bid = self.rows("select budget_id from ctr_budget where phase_id=? and as_of='2099-01-01'", pid)[0][0]
        self.run_sql("insert into ctr_budget_role (budget_id, role_id, hours) values (?,?,10)", bid, roles[0])
        self.run_sql("insert into ctr_budget_role (budget_id, role_id, hours) values (?,?,20)", bid, roles[1])
        rates = {r[0]: float(r[1]) for r in self.rows(
            "select role_id, (select sales_rate from rate x where x.client_id=rate.client_id and x.role_id=rate.role_id and x.effective_from <= ? order by x.effective_from desc limit 1) "
            "from rate where client_id=? and role_id in (?,?)", today, APACHE, roles[0], roles[1])}
        rep = self.rep("cost_week", client=APACHE, **{"from": "2026-10-01", "to": "2026-11-30"})
        budget = [c[1] for k, c in rep["rows"] if k == "row" and c[0] == label][0]
        self.assertAlmostEqual(budget, 10 * rates[roles[0]] + 20 * rates[roles[1]], places=2)

    def test_the_invoice_back_up_is_each_persons_hours_by_day(self):
        rep = self.rep("invoice", client=APACHE, **{"from": "2026-11-02", "to": "2026-11-08"})
        self.assertEqual(rep["header"][:6], ["Project: Project No - Title", "Person: Full Name (Firstname Lastname)", "Role", "Stage Name", "Asset", "PO Number"])
        self.assertEqual(rep["header"][6:], ["02/11/2026", "03/11/2026", "04/11/2026", "05/11/2026", "06/11/2026", "07/11/2026", "08/11/2026", "Total", "Value"])
        rows = [c for k, c in rep["rows"] if k == "row"]
        self.assertTrue(any(r[1] == "Ewan Shaw" for r in rows))
        for r in rows:
            self.assertAlmostEqual(r[-2], sum(x or 0 for x in r[6:-2]))
        self.assertAlmostEqual(rep["rows"][-1][1][-2], self.hours(APACHE, "2026-11-02", "2026-11-08"))
        self.assertAlmostEqual(rep["rows"][-1][1][-1], self.hours(APACHE, "2026-11-02", "2026-11-08", value=True), places=1)
        subs = [c for k, c in rep["rows"] if k == "sub"]
        self.assertTrue(subs and subs[0][0].startswith("Total "))                              # a total for each CTR
        self.assertTrue(any(r[4] == "Alpha Platform" and r[5] == "4500123456" for r in rows))   # the asset and its PO

    def test_each_report_downloads_as_excel_for_the_chosen_client(self):
        for kind, word in (("ctr_week", "Booked v CTR"), ("budget_hours", "Budget"), ("cost_week", "Weekly Cost Report"), ("invoice", "Invoice back up")):
            text = self.xlsx_text("/reports/cost.xlsx?u=%d&k=%s&client=2&from=2026-10-01&to=2026-11-30" % (CONTROLLER, kind))
            self.assertIn(word, text, kind)
            self.assertIn("Spirit", text, kind)
            self.assertIn("A1500", text, kind)
            self.assertNotIn("BB0001-PM-D-001", text, kind)
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(self.base + "/reports/cost.xlsx?u=%d&k=nonsense&client=1" % CONTROLLER)

    def test_the_stage_is_the_ctrs_discipline(self):
        rep = self.rep("ctr_week", client=APACHE)
        stage = {c[0].split(" - ")[0]: c[1] for k, c in rep["rows"] if k == "row"}
        self.assertEqual(stage["BB0001-PM-D-001"], "Project Management")                         # a Define CTR, but the discipline is what the cost team want
        self.run_sql("update phase set discipline_id=(select discipline_id from discipline where code='TS') where phase_id=(select min(phase_id) from phase where ctr_code='PM-D-001')")
        rep = self.rep("invoice", client=APACHE, **{"from": "2026-11-01", "to": "2026-11-30"})
        self.assertIn("Technical Safety", {c[3] for k, c in rep["rows"] if k == "row"})

    def test_grouping_by_scope_keeps_every_figure_and_adds_a_total_for_each_scope(self):
        for kind, last in (("ctr_week", -1), ("budget_hours", 2), ("cost_week", 2), ("invoice", -1)):
            q = {"client": APACHE, "from": "2026-10-01", "to": "2026-11-30"}
            plain, grouped = self.rep(kind, **q), self.rep(kind, group="scope", **q)
            self.assertEqual(sorted(map(str, (c for k, c in plain["rows"] if k == "row"))), sorted(map(str, (c for k, c in grouped["rows"] if k == "row"))), kind)   # same rows, same figures
            self.assertEqual(plain["rows"][-1], grouped["rows"][-1], kind)                         # the grand total is unchanged
            totals = [c for k, c in grouped["rows"] if k == "group"]
            self.assertTrue(totals and all(c[0].startswith("Total ") for c in totals), kind)
            col = len(plain["header"]) - 1 if kind in ("ctr_week", "invoice") else 2
            self.assertAlmostEqual(sum(c[col] or 0 for c in totals), plain["rows"][-1][1][col] or 0, places=1, msg=kind)    # the scope totals add up to the grand total
            self.assertIn("grouped by scope", grouped["subtitle"])
            order = [c[0].split(" - ")[0].split("-")[0] for k, c in grouped["rows"] if k == "row"]
            seen = []
            for o in order:
                if not seen or seen[-1] != o:
                    seen.append(o)
            self.assertEqual(len(seen), len(set(seen)), kind)                                      # each scope's CTRs sit together
        page = self.get("/reports?u=%d&r=ctr_week&client=%d&group=scope" % (CONTROLLER, APACHE))
        self.assertIn('<option value="scope" selected>', page)
        self.assertIn('class="group"', page)
        self.assertIn("group=scope", self.get("/reports?u=%d&r=ctr_week&client=%d&group=scope" % (CONTROLLER, APACHE)))        # the Excel link keeps the grouping
        text = self.xlsx_text("/reports/cost.xlsx?u=%d&k=invoice&client=%d&from=2026-10-01&to=2026-11-30&group=scope" % (CONTROLLER, APACHE))
        self.assertIn("Grouped by = Scope", text)

    def test_the_excel_follows_the_layout_of_the_cmap_exports(self):
        import re
        rep = self.rep("invoice", client=APACHE, **{"from": "2026-11-02", "to": "2026-11-08"})
        text = self.xlsx_text("/reports/cost.xlsx?u=%d&k=invoice&client=%d&from=2026-11-02&to=2026-11-08" % (CONTROLLER, APACHE))
        self.assertIn("Exported from NMC Timewriting", text)
        self.assertIn("Date = 02/11/2026 - 08/11/2026", text)
        self.assertIn("Name = Apache", text)
        self.assertIn('sheet name="Export"', text)
        self.assertIn("###,###,###.00;", text)                                                  # CMAP's number format
        self.assertIn('style="thick"', text)                                                    # totals sit between heavy lines
        merged = re.findall(r'<mergeCell ref="A(\d+):A(\d+)"/>', text)
        self.assertTrue(merged)                                                                 # a CTR is written once and merged down its people
        self.assertEqual(sum(1 for k, c in rep["rows"] if k == "row") - sum(int(b) - int(a) for a, b in merged), len({c[0] for k, c in rep["rows"] if k == "row"}))


if __name__ == "__main__":
    unittest.main()
