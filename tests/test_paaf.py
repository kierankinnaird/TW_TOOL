"""The PAAF register: who may write time on which client project, under which job title, and what the title does to the rate."""
import json
import os
import re
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
ARLENE = 23          # Document Controller: Lead Document Controller on Spirit's project, Senior Document Controller on Apache's
LEAD, SENIOR = 34, 35


class TestPaaf(unittest.TestCase):
    def setUp(self):
        core.DB_URL = clone()
        self.server = app.make_server(0)
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        con = app.db()
        con.execute("insert into period (week_start) values ('2026-11-09') on conflict do nothing")
        con.commit()
        con.close()

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

    def phase(self, scope_code):
        return self.rows("select ph.phase_id from phase ph join scope s on s.scope_id=ph.scope_id where s.scope_code=? order by ph.phase_id limit 1", scope_code)[0][0]

    def book(self, user, phase, role, date="2026-11-10", hours="8"):
        return self.post("/timesheet/save", {"u": str(user), "w": "2026-11-09", "action": "Save draft", "h_%d_%d_1_0_%s" % (phase, role, date): hours, "reason": ""})

    def roles_map(self, user):
        page = self.get("/timesheet?u=%d&w=2026-11-09" % user)
        return {int(k): v for k, v in json.loads(re.search(r"var ROLES=(\{.*?\});</script>", page).group(1)).items()}, page

    # ---- the database rule ----
    def test_client_hours_need_a_paaf_for_the_project_and_the_role(self):
        wellbay = self.phase("BB0001")
        # Arlene holds Senior Document Controller on Apache's projects: that books, priced at Apache's rate for the title
        self.book(ARLENE, wellbay, SENIOR)
        self.assertEqual(self.rows("select worked_role_id, r.sales_rate from time_entry t join rate r on r.rate_id=t.rate_id where user_id=? and work_date='2026-11-10'", ARLENE), [(SENIOR, 98)])
        # the same title on Spirit's project, or her own job title on Apache's: refused, nothing booked
        self.book(ARLENE, self.phase("A1500"), SENIOR, "2026-11-11")
        self.book(ARLENE, wellbay, 10, "2026-11-12")
        self.assertEqual(self.rows("select count(*) from time_entry where user_id=? and work_date in ('2026-11-11','2026-11-12')", ARLENE)[0][0], 0)
        with self.assertRaises(pg.DatabaseError) as cm:
            self.run_sql("insert into time_entry (user_id, scope_id, phase_id, worked_role_id, rate_id, work_date, hours, created_by) values (?,?,?,?,?,?,?,?)",
                         ARLENE, 5, self.phase("A1500"), SENIOR, 1, "2026-11-11", 8, ARLENE)
        self.assertIn("No PAAF", pg.error_text(cm.exception))

    def test_the_same_job_is_billed_under_each_clients_title(self):
        self.book(ARLENE, self.phase("A1500"), LEAD, "2026-11-11")                      # Spirit: Lead Document Controller at Spirit's rate
        self.book(ARLENE, self.phase("BB0001"), SENIOR, "2026-11-12")                  # Apache: Senior Document Controller at Apache's rate
        got = self.rows("select t.work_date, ro.name, r.sales_rate, c.name from time_entry t join rate r on r.rate_id=t.rate_id join role ro on ro.role_id=t.worked_role_id "
                        "join client c on c.client_id=r.client_id where t.user_id=? and t.work_date in ('2026-11-11','2026-11-12') order by 1", ARLENE)
        self.assertEqual(got, [("2026-11-11", "Lead Document Controller", 100, "Spirit"), ("2026-11-12", "Senior Document Controller", 98, "Apache")])

    def test_a_paaf_needs_a_rate_for_that_role_on_the_clients_card(self):
        with self.assertRaises(pg.DatabaseError) as cm:
            self.run_sql("insert into paaf (project_id, role_id, user_id, start_date) values (1, 33, 5, '2026-01-01')")       # Apache has no rate for Managing Director
        self.assertIn("no rate", pg.error_text(cm.exception))

    def test_one_paaf_per_person_project_and_role_but_several_roles_are_fine(self):
        with self.assertRaises(pg.UniqueViolation):
            self.run_sql("insert into paaf (project_id, role_id, user_id, start_date) values (1, 35, 23, '2026-01-01')")
        self.run_sql("insert into paaf (project_id, role_id, user_id, start_date) values (1, 3, 23, '2026-01-01')")           # a second title on the same project
        self.assertEqual(sorted(r[0] for r in self.rows("select role_id from paaf where user_id=23 and project_id=1")), [3, 35])

    def test_internal_codes_need_no_paaf_and_removing_one_keeps_the_hours(self):
        self.book(ARLENE, self.phase("INT01"), 10, "2026-11-10", "4")                             # internal: her own title, no PAAF involved
        self.assertEqual(self.rows("select count(*) from time_entry where user_id=? and work_date='2026-11-10'", ARLENE)[0][0], 1)
        self.book(ARLENE, self.phase("BB0001"), SENIOR, "2026-11-11")
        self.run_sql("delete from paaf where user_id=? and project_id=1 and role_id=?", ARLENE, SENIOR)
        self.assertEqual(self.rows("select count(*) from time_entry where user_id=? and work_date='2026-11-11'", ARLENE)[0][0], 1)   # booked hours stay
        self.book(ARLENE, self.phase("BB0001"), SENIOR, "2026-11-13")                              # but nothing new
        self.assertEqual(self.rows("select count(*) from time_entry where user_id=? and work_date='2026-11-13'", ARLENE)[0][0], 0)

    def test_changing_a_draft_rows_title_needs_a_paaf_for_the_new_title(self):
        wellbay = self.phase("BB0001")
        self.book(ARLENE, wellbay, SENIOR)
        out = self.post("/timesheet/save", {"u": str(ARLENE), "w": "2026-11-09", "action": "Save draft", "rs_%d_%d_1_0" % (wellbay, SENIOR): str(LEAD), "reason": ""})
        self.assertIn("PAAF", out)
        self.assertEqual(self.rows("select worked_role_id from time_entry where user_id=? and work_date='2026-11-10'", ARLENE), [(SENIOR,)])

    # ---- what people see ----
    def test_the_timesheet_offers_only_the_projects_and_titles_on_the_persons_paafs(self):
        roles, page = self.roles_map(ARLENE)
        by_phase = {self.phase("BB0001"): [SENIOR], self.phase("A1500"): [LEAD], self.phase("INT01"): [10]}
        for ph, want in by_phase.items():
            self.assertEqual([r[0] for r in roles[ph]], want, ph)
        self.assertEqual(roles[self.phase("BB0001")][0][1], "PM-10 Senior Document Controller")
        # someone with PAAFs on Apache only is never offered a Spirit CTR
        eight = self.rows("select distinct user_id from paaf where project_id in (1,2) and user_id not in (select user_id from paaf where project_id=3) limit 1")[0][0]
        roles2, _ = self.roles_map(eight)
        self.assertNotIn(self.phase("A1500"), roles2)
        self.assertIn(self.phase("BB0001"), roles2)

    def test_a_person_with_no_paaf_sees_internal_codes_only_and_is_told_who_to_ask(self):
        nobody = self.rows("select user_id from app_user where books_time=1 and permission='user' and user_id not in (select user_id from paaf) order by user_id limit 1")[0][0]
        roles, page = self.roles_map(nobody)
        internal = {r[0] for r in self.rows("select ph.phase_id from phase ph join scope s on s.scope_id=ph.scope_id join client c on c.client_id=s.client_id where c.is_internal=1")}
        self.assertTrue(set(roles) and set(roles) <= internal)
        self.assertIn("Speak to commercial", page)

    # ---- the register in Admin ----
    def test_the_cost_team_keeps_the_register_in_admin(self):
        page = self.get("/admin?u=%d&t=paaf" % CONTROLLER)
        self.assertIn("Senior Document Controller", page)
        self.assertIn("Arlene Chalmers", page)
        self.assertIn("not available to your role", self.get("/admin?u=5&t=paaf"))
        # add one title for two people at once, and again (nothing is added twice)
        out = self.post("/admin/add", {"u": CONTROLLER, "t": "paaf", "project_id": "3", "role_id": "3", "reference": "PAAF-X", "start_date": "2026-01-01", "person": ["4", "5"]})
        self.assertIn("2 PAAFs added.", out)
        self.assertIn("0 PAAFs added (2 already held)", self.post("/admin/add", {"u": CONTROLLER, "t": "paaf", "project_id": "3", "role_id": "3", "start_date": "2026-01-01", "person": ["4", "5"]}))
        self.assertEqual(self.rows("select count(*) from paaf where project_id=3 and role_id=3 and user_id in (4,5)")[0][0], 2)
        # a title with no rate for the client is refused with a clear message
        self.assertIn("no rate", self.post("/admin/add", {"u": CONTROLLER, "t": "paaf", "project_id": "1", "role_id": "33", "start_date": "2026-01-01", "person": ["4"]}))
        self.assertIn("Choose at least one person", self.post("/admin/add", {"u": CONTROLLER, "t": "paaf", "project_id": "3", "role_id": "3", "start_date": "2026-01-01"}))
        # edit the reference, then remove
        pid = self.rows("select paaf_id from paaf where project_id=3 and role_id=3 and user_id=4")[0][0]
        self.assertIn("PAAF-X", self.get("/admin/edit?u=%d&t=paaf&id=%d" % (CONTROLLER, pid)))
        self.post("/admin/save", {"u": CONTROLLER, "t": "paaf", "id": str(pid), "reference": "PAAF-Y", "start_date": "2026-01-01", "end_date": ""})
        self.assertEqual(self.rows("select reference from paaf where paaf_id=?", pid)[0][0], "PAAF-Y")
        self.assertIn("PAAF removed.", self.post("/admin/save", {"u": CONTROLLER, "t": "paaf", "id": str(pid), "remove": "Remove this PAAF"}))
        self.assertEqual(self.rows("select count(*) from paaf where paaf_id=?", pid)[0][0], 0)
        # the filter
        self.assertNotIn("Arlene Chalmers", self.get("/admin?u=%d&t=paaf&f_person=Ewan" % CONTROLLER).split("<main>")[1].split("<h4>Add PAAFs</h4>")[0])

    # ---- dates ----
    def test_hours_can_only_be_booked_on_days_the_paaf_is_valid(self):
        wellbay = self.phase("BB0001")
        self.run_sql("update paaf set start_date='2026-11-11', end_date='2026-11-12' where user_id=? and project_id=1 and role_id=?", ARLENE, SENIOR)
        self.book(ARLENE, wellbay, SENIOR, "2026-11-10")                      # before it starts
        self.book(ARLENE, wellbay, SENIOR, "2026-11-13")                      # after it has expired
        self.assertEqual(self.rows("select count(*) from time_entry where user_id=? and work_date in ('2026-11-10','2026-11-13')", ARLENE)[0][0], 0)
        self.book(ARLENE, wellbay, SENIOR, "2026-11-11")                      # first day, and the last day, are both valid
        self.book(ARLENE, wellbay, SENIOR, "2026-11-12")
        self.assertEqual(self.rows("select count(*) from time_entry where user_id=? and work_date in ('2026-11-11','2026-11-12')", ARLENE)[0][0], 2)
        with self.assertRaises(pg.DatabaseError) as cm:
            self.run_sql("update time_entry set work_date='2026-11-13', last_edited_by=?, last_edited_at='2026-11-12 10:00:00' where user_id=? and work_date='2026-11-12'", CONTROLLER, ARLENE)   # nor can a day be moved outside the dates
        self.assertIn("No PAAF", pg.error_text(cm.exception))

    def test_an_end_date_is_optional_but_cannot_be_before_the_start(self):
        with self.assertRaises(pg.DatabaseError):
            self.run_sql("insert into paaf (project_id, role_id, user_id, start_date, end_date) values (3, 3, 4, '2026-06-01', '2026-05-31')")
        self.run_sql("insert into paaf (project_id, role_id, user_id, start_date) values (3, 3, 4, '2026-06-01')")        # no end date: open ended
        self.book(4, self.phase("A1500"), 3, "2027-03-01")

    def test_an_expired_paaf_is_not_offered_on_the_timesheet(self):
        self.run_sql("update paaf set end_date='2026-11-01' where user_id=? and project_id=3", ARLENE)                     # Spirit's PAAF ended before the week of 9 Nov
        roles, page = self.roles_map(ARLENE)
        self.assertNotIn(self.phase("A1500"), roles)
        self.assertIn(self.phase("BB0001"), roles)
        self.run_sql("update paaf set end_date=NULL where user_id=? and project_id=3", ARLENE)                              # renewed by changing its dates
        self.assertIn(self.phase("A1500"), self.roles_map(ARLENE)[0])

    def test_the_cost_team_sets_and_renews_the_dates_in_admin(self):
        out = self.post("/admin/add", {"u": CONTROLLER, "t": "paaf", "project_id": "3", "role_id": "3", "start_date": "2026-01-01", "end_date": "2026-03-31", "reference": "DOC-1", "person": ["4"]})
        self.assertIn("1 PAAF added.", out)
        pid = self.rows("select paaf_id from paaf where project_id=3 and role_id=3 and user_id=4")[0][0]
        page = self.get("/admin?u=%d&t=paaf&f_person=David" % CONTROLLER)
        self.assertIn("Expired", page)
        self.assertIn("31/03/2026", page)
        self.assertIn("The end date cannot be before the start date.", self.post("/admin/add", {"u": CONTROLLER, "t": "paaf", "project_id": "3", "role_id": "4", "start_date": "2026-05-01", "end_date": "2026-04-01", "person": ["4"]}))
        self.assertIn("Start date is required", self.post("/admin/add", {"u": CONTROLLER, "t": "paaf", "project_id": "3", "role_id": "4", "start_date": "", "person": ["4"]}))
        self.post("/admin/save", {"u": CONTROLLER, "t": "paaf", "id": str(pid), "reference": "DOC-1", "start_date": "2026-01-01", "end_date": "2027-03-31"})         # renewed
        self.assertEqual(self.rows("select end_date from paaf where paaf_id=?", pid)[0][0], "2027-03-31")
        self.assertIn("Valid", self.get("/admin?u=%d&t=paaf&f_person=David" % CONTROLLER))
        self.assertIn("cannot be before", self.post("/admin/save", {"u": CONTROLLER, "t": "paaf", "id": str(pid), "start_date": "2026-05-01", "end_date": "2026-04-01"}))

    def test_the_register_holds_no_documents(self):
        cols = {r[0] for r in self.rows("select column_name from information_schema.columns where table_name='paaf'")}
        self.assertEqual(cols, {"paaf_id", "project_id", "role_id", "user_id", "reference", "start_date", "end_date"})      # a reference to where the document is kept, nothing more
        page = self.get("/admin?u=%d&t=paaf" % CONTROLLER)
        self.assertIn("held elsewhere", page)
        self.assertNotIn('type="file"', page)


if __name__ == "__main__":
    unittest.main()
