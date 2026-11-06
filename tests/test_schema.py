"""Schema tests. Run from the repo root: python -m unittest discover -s tests -v"""
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from build import build  # noqa: E402

NEW_ENTRY = (
    "INSERT INTO time_entry (user_id, scope_id, paaf_id, worked_role_id, rate_id, work_date, hours, created_by) "
    "VALUES (?,?,?,?,?,?,?,?)"
)


class Base(unittest.TestCase):
    def setUp(self):
        self.con = build()  # in-memory, with seed data

    def tearDown(self):
        self.con.close()

    def scalar(self, sql, *args):
        return self.con.execute(sql, args).fetchone()[0]

    def blocked(self, sql, args=(), msg=None):
        with self.assertRaises(sqlite3.DatabaseError) as cm:
            self.con.execute(sql, args)
        if msg:
            self.assertIn(msg, str(cm.exception))


class TestSeed(Base):
    def test_counts(self):
        self.assertEqual(self.scalar("select count(*) from app_user"), 10)
        self.assertEqual(self.scalar("select count(*) from time_entry"), 28)
        self.assertEqual(self.scalar("select count(*) from rate"), 12)

    def test_rates_rise_with_seniority_for_each_client(self):
        order = ["Cost Engineer", "Senior Planner", "Project Engineer", "Lead Planner",
                 "Engineering Manager", "Project Manager"]
        for client in ("Apache", "Spirit"):
            rows = dict(self.con.execute(
                "select r.name, rt.sales_rate from rate rt join role r using(role_id) "
                "join client c using(client_id) where c.name=?", (client,)))
            self.assertEqual([rows[n] for n in order], sorted(rows[n] for n in order), client)

    def test_no_abbreviated_job_titles(self):
        names = [r[0] for r in self.con.execute("select name from role")]
        self.assertIn("Senior Planner", names)
        self.assertFalse([n for n in names if "Snr" in n])


class TestBookingRules(Base):
    # Seed ids: Ewan=5 (paaf 1, Apache), Fraser=4 (paaf 4 'Sent'), Isla=1 (paaf 3 Apache, 5 Spirit)
    def test_valid_booking_is_accepted_and_audited(self):
        self.con.execute(NEW_ENTRY, (5, 1, 1, 4, 4, "2026-11-06", 8, 5))
        self.assertEqual(self.scalar("select count(*) from time_entry_audit where action='CREATE'"), 29)

    def test_unapproved_paaf_blocks_booking(self):
        self.blocked(NEW_ENTRY, (4, 1, 4, 4, 4, "2026-11-06", 8, 4), "No approved PAAF")

    def test_paaf_is_per_client_not_per_scope(self):
        # Ewan's single Apache PAAF covers every Apache scope...
        self.con.execute(NEW_ENTRY, (5, 2, 1, 4, 4, "2026-11-06", 4, 5))
        # ...but not Spirit.
        self.blocked(NEW_ENTRY, (5, 5, 1, 4, 10, "2026-11-06", 4, 5))

    def test_closed_scope_blocks_booking(self):
        self.blocked(NEW_ENTRY, (1, 3, 3, 6, 6, "2026-11-06", 8, 1), "Scope is not Open")

    def test_pipeline_scope_blocks_booking(self):
        self.blocked(NEW_ENTRY, (1, 4, 5, 6, 12, "2026-11-06", 8, 1), "Scope is not Open")

    def test_rate_must_match_client_and_worked_role(self):
        self.blocked(NEW_ENTRY, (5, 1, 1, 5, 4, "2026-11-06", 8, 5), "Rate does not match")  # LP role, SP rate
        self.blocked(NEW_ENTRY, (5, 1, 1, 4, 10, "2026-11-06", 8, 5), "Rate does not match")  # Spirit rate on Apache

    def test_cover_booking_uses_higher_role_rate(self):
        self.con.execute(NEW_ENTRY, (5, 1, 1, 5, 5, "2026-11-06", 8, 5))  # Senior Planner covering Lead Planner
        self.assertEqual(self.scalar("select sales_rate from rate where rate_id=5"), 115)

    def test_hours_limits(self):
        self.blocked(NEW_ENTRY, (5, 1, 1, 4, 4, "2026-11-06", 0, 5))
        self.blocked(NEW_ENTRY, (5, 1, 1, 4, 4, "2026-11-06", 25, 5))

    def test_new_entries_cannot_start_approved(self):
        self.blocked(
            "INSERT INTO time_entry (user_id, scope_id, paaf_id, worked_role_id, rate_id, work_date, hours, status, created_by) "
            "VALUES (5,1,1,4,4,'2026-11-06',8,'Approved',5)", msg="Draft or Submitted")


class TestPeriods(Base):
    def test_closed_week_blocks_new_entries(self):
        self.blocked(NEW_ENTRY, (5, 1, 1, 4, 4, "2026-10-28", 8, 5), "period is not open")

    def test_missing_week_blocks_new_entries(self):
        self.blocked(NEW_ENTRY, (5, 1, 1, 4, 4, "2026-12-01", 8, 5), "period is not open")

    def test_sunday_belongs_to_the_week_starting_the_previous_monday(self):
        self.assertEqual(self.scalar("select date('2026-11-08','weekday 0','-6 days')"), "2026-11-02")
        self.assertEqual(self.scalar("select date('2026-11-02','weekday 0','-6 days')"), "2026-11-02")

    def test_period_must_start_on_monday(self):
        self.blocked("INSERT INTO period (week_start) VALUES ('2026-11-03')")


class TestLifecycle(Base):
    def edit(self, sets, where, *args):
        self.con.execute(f"UPDATE time_entry SET {sets} WHERE {where}", args)

    def first(self, where):
        return self.scalar(f"select entry_id from time_entry where {where} limit 1")

    def test_edit_needs_named_editor(self):
        eid = self.first("status='Submitted'")
        self.blocked("UPDATE time_entry SET hours=3 WHERE entry_id=?", (eid,), "last_edited_by")

    def test_changing_approved_entry_requires_edited_status(self):
        eid = self.first("status='Approved' and scope_id=1 and work_date='2026-11-03'")
        self.blocked("UPDATE time_entry SET hours=3, last_edited_by=2, last_edited_at='t1' WHERE entry_id=?",
                     (eid,), "sets its status to Edited")

    def test_controlled_edit_needs_reason(self):
        eid = self.first("status='Approved' and scope_id=1 and work_date='2026-11-03'")
        self.blocked("UPDATE time_entry SET hours=3, status='Edited', last_edited_by=2, last_edited_at='t1' WHERE entry_id=?",
                     (eid,), "needs a reason")

    def test_controlled_edit_then_reapproval(self):
        eid = self.first("status='Approved' and scope_id=1 and work_date='2026-11-03'")
        self.con.execute(
            "UPDATE time_entry SET hours=6, status='Edited', edit_count=edit_count+1, last_change_reason='Fix', "
            "last_edited_by=7, last_edited_at='t1' WHERE entry_id=?", (eid,))
        self.assertEqual(self.scalar("select status from time_entry where entry_id=?", eid), "Edited")
        self.con.execute(
            "UPDATE time_entry SET status='Approved', approved_by=6, last_edited_by=6, last_edited_at='t2' WHERE entry_id=?",
            (eid,))
        row = self.con.execute("select status, hours, edit_count from time_entry where entry_id=?", (eid,)).fetchone()
        self.assertEqual(row, ("Approved", 6, 1))

    def test_reject_needs_reason_and_returns_to_draft(self):
        eid = self.first("status='Submitted' and scope_id=6")
        self.blocked("UPDATE time_entry SET status='Draft', last_edited_by=7, last_edited_at='t1' WHERE entry_id=?",
                     (eid,), "needs a reason")
        self.con.execute("UPDATE time_entry SET status='Draft', reject_reason='No', last_edited_by=7, "
                         "last_edited_at='t2' WHERE entry_id=?", (eid,))
        self.assertEqual(self.scalar("select reason from time_entry_audit where entry_id=? and new_status='Draft'", eid), "No")

    def test_only_scope_approver_or_admin_can_approve(self):
        eid = self.first("status='Submitted' and scope_id=1")  # scope 1 approver = Rhona (6)
        sql = "UPDATE time_entry SET status='Approved', approved_by=?, last_edited_by=?, last_edited_at='t1' WHERE entry_id=?"
        self.blocked(sql, (1, 1, eid), "scope approver or an admin")   # Isla: neither
        self.con.execute(sql, (7, 7, eid))                              # Alasdair: admin

    def test_nobody_approves_their_own_entry(self):
        eid = self.scalar("select entry_id from time_entry where user_id=5 and status='Submitted' limit 1")
        self.blocked("UPDATE time_entry SET status='Approved', approved_by=5, last_edited_by=5, last_edited_at='t1' "
                     "WHERE entry_id=?", (eid,), "not their own")

    def test_entries_cannot_be_deleted(self):
        self.blocked("DELETE FROM time_entry", msg="cannot be deleted")

    def test_every_status_change_is_audited(self):
        n_status = self.scalar("select count(*) from time_entry_audit where action='STATUS'")
        self.assertGreater(n_status, 0)
        missing = self.scalar("select count(*) from time_entry_audit where action='STATUS' and changed_by is null")
        self.assertEqual(missing, 0)


class TestViews(Base):
    def test_ctr_counts_approved_only_and_shows_pending_separately(self):
        row = self.con.execute(
            "select hours, pending_hours from v_ctr where scope_code='BB0001' and skill_code='PL-02'").fetchone()
        approved = self.scalar("select sum(hours) from time_entry t join scope s using(scope_id) join rate r using(rate_id) "
                               "join role ro using(role_id) where s.scope_code='BB0001' and ro.skill_code='PL-02' "
                               "and t.status='Approved'") or 0
        self.assertEqual(row[0], approved)
        self.assertGreater(row[1], 0)

    def test_ctr_total_is_hours_times_rate(self):
        for hours, rate, total in self.con.execute("select hours, sales_rate, total from v_ctr"):
            self.assertAlmostEqual(hours * rate, total, places=6)

    def test_period_report_date_range(self):
        rows = self.con.execute(
            "select distinct week_start from v_period_report where week_start between '2026-10-01' and '2026-10-31'").fetchall()
        self.assertEqual(rows, [("2026-10-26",)])
        both = self.con.execute("select distinct week_start from v_period_report order by 1").fetchall()
        self.assertEqual(both, [("2026-10-26",), ("2026-11-02",)])

    def test_approver_queue_only_contains_pending_entries(self):
        statuses = {r[0] for r in self.con.execute("select status from v_approver_queue")}
        self.assertLessEqual(statuses, {"Submitted", "Edited"})
        self.assertIn("Edited", statuses)

    def test_cover_hours_appear_on_their_own_line(self):
        lines = self.con.execute(
            "select skill_code from v_ctr where scope_code='BB0001'").fetchall()
        self.assertIn(("PL-01",), lines)
        self.assertIn(("PL-02",), lines)


if __name__ == "__main__":
    unittest.main()
