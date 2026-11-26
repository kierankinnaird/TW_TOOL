"""Schema tests. Run from the repo root: python -m unittest discover -s tests -v"""
import os
import sys
import unittest

from pgtest import build  # noqa: E402   (a throwaway PostgreSQL database per test, with the seed)
from nmc import pg  # noqa: E402

NEW_ENTRY = (
    "INSERT INTO time_entry (user_id, scope_id, phase_id, worked_role_id, rate_id, work_date, hours, created_by) "
    "VALUES (?,?,?,?,?,?,?,?) RETURNING entry_id"
)


class Base(unittest.TestCase):
    def setUp(self):
        self.con = build()  # a fresh database with the seed data

    def tearDown(self):
        self.con.close()

    def scalar(self, sql, *args):
        return self.con.execute(sql, args).fetchone()[0]

    def blocked(self, sql, args=(), msg=None):
        with self.assertRaises(pg.DatabaseError) as cm:
            self.con.execute(sql, args)
        if msg:
            self.assertIn(msg, str(cm.exception))


class TestSeed(Base):
    def test_counts(self):
        self.assertEqual(self.scalar("select count(*) from app_user"), 31)
        self.assertEqual(self.scalar("select count(*) from time_entry"), 64)
        self.assertEqual(self.scalar("select count(*) from rate"), 62)       # 60, and a rate each for the two document controller titles

    def test_rates_rise_with_seniority_for_each_client(self):
        order = ["Cost Engineer", "Planner", "Project Engineer", "Project Controls Manager",
                 "Engineering Manager", "Project Manager"]
        for client in ("Apache", "Spirit"):
            rows = dict(self.con.execute(
                "select r.name, rt.sales_rate from rate rt join role r using(role_id) "
                "join client c using(client_id) where c.name=?", (client,)))
            self.assertEqual([rows[n] for n in order], sorted(rows[n] for n in order), client)

    def test_job_families_and_titles_from_the_estimate(self):
        self.assertEqual(self.scalar("select count(*) from role"), 35)
        families = {r[0] for r in self.con.execute("select family from role")}
        for f in ["Project Management", "Engineering Management", "Project Engineering", "Piping Engineering", "Technical Safety", "HSES", "QA/QC"]:
            self.assertIn(f, families)
        self.assertEqual(self.scalar("select count(*) from role where family is null or family=''"), 0)

    def test_every_user_has_a_job_title_and_every_title_is_priced_for_both_clients(self):
        self.assertEqual(self.scalar("select count(*) from app_user where role_id not in (select role_id from role)"), 0)
        self.assertEqual(self.scalar("select count(*) from role r where (select count(*) from rate where role_id=r.role_id) not in (0, 2) and r.role_id not in (34, 35)"), 0)       # the two document controller titles are priced by one client each
        # the only titles without a rate card never bill a client
        self.assertEqual({r[0] for r in self.con.execute("select name from role r where not exists (select 1 from rate where role_id=r.role_id)")},
                         {"System Administrator", "Commercial Lead", "Managing Director"})

    def test_spirit_rates_match_the_morecambe_hub_estimate(self):
        rows = dict(self.con.execute("select r.name, rt.sales_rate from rate rt join role r using(role_id) join client c using(client_id) where c.name='Spirit'"))
        self.assertEqual((rows["Project Manager"], rows["Engineering Manager"], rows["Lead Technical Safety Engineer"], rows["Document Controller"]), (120, 110, 120, 65))

    def test_no_abbreviated_job_titles(self):
        names = [r[0] for r in self.con.execute("select name from role")]
        self.assertIn("Planner", names)
        self.assertFalse([n for n in names if "Snr" in n])


class TestBookingRules(Base):
    # Seed ids: Ewan Shaw=5 (Planner), David Hart=4, Emma Noble=1 (Cost Engineer)
    def test_valid_booking_is_accepted_and_audited(self):
        self.con.execute(NEW_ENTRY, (5, 1, 1, 4, 4, "2026-11-06", 8, 5))
        self.assertEqual(self.scalar("select count(*) from time_entry_audit where action='CREATE'"), 65)

    def test_client_hours_are_booked_under_a_paaf_at_the_clients_rate(self):
        # David Hart has no PAAF for Apache's project and the Planner title: refused until he is given one
        self.blocked(NEW_ENTRY, (4, 1, 1, 4, 4, "2026-11-06", 8, 4), "No PAAF")
        self.con.execute("insert into paaf (project_id, role_id, user_id, start_date) values (1, 4, 4, '2026-01-01')")
        self.con.execute(NEW_ENTRY, (4, 1, 1, 4, 4, "2026-11-06", 8, 4))
        self.con.execute("insert into paaf (project_id, role_id, user_id, start_date) values (3, 4, 5, '2026-01-01')")
        spirit_planner = self.scalar("select rate_id from rate where client_id=2 and role_id=4 order by effective_from desc limit 1")
        self.con.execute(NEW_ENTRY, (5, 5, 6, 4, spirit_planner, "2026-11-06", 4, 5))
        self.blocked(NEW_ENTRY, (5, 5, 6, 4, 4, "2026-11-06", 4, 5), "Rate does not match")  # but the rate must be the client's own

    def test_closed_scope_blocks_booking(self):
        self.blocked(NEW_ENTRY, (1, 3, 4, 6, 6, "2026-11-06", 8, 1), "Scope is not Open")

    def test_pipeline_scope_blocks_booking(self):
        self.blocked(NEW_ENTRY, (1, 4, 5, 6, 12, "2026-11-06", 8, 1), "Scope is not Open")

    def test_rate_must_match_client_and_worked_role(self):
        self.blocked(NEW_ENTRY, (5, 1, 1, 5, 4, "2026-11-06", 8, 5), "Rate does not match")  # LP role, SP rate
        self.blocked(NEW_ENTRY, (5, 1, 1, 4, 10, "2026-11-06", 8, 5), "Rate does not match")  # Spirit rate on Apache

    def test_cover_booking_uses_higher_role_rate(self):
        self.con.execute(NEW_ENTRY, (5, 1, 1, 5, 5, "2026-11-06", 8, 5))  # Planner covering Project Controls Manager
        self.assertEqual(self.scalar("select sales_rate from rate where rate_id=5"), 100)

    def test_hours_limits(self):
        self.blocked(NEW_ENTRY, (5, 1, 1, 4, 4, "2026-11-06", 0, 5))
        self.blocked(NEW_ENTRY, (5, 1, 1, 4, 4, "2026-11-06", 25, 5))

    def test_new_entries_cannot_start_approved(self):
        self.blocked(
            "INSERT INTO time_entry (user_id, scope_id, worked_role_id, rate_id, work_date, hours, status, created_by) "
            "VALUES (5,1,4,4,'2026-11-06',8,'Approved',5)", msg="Draft or Submitted")


class TestHierarchy(Base):
    """client > project > scope > phase. Hours are booked to a phase."""
    def test_every_scope_has_a_phase_and_a_project_of_the_same_client(self):
        self.assertEqual(self.scalar("select count(*) from scope where scope_id not in (select scope_id from phase)"), 0)
        self.assertEqual(self.scalar("select count(*) from scope s join project p using(project_id) where p.client_id<>s.client_id"), 0)

    def test_phase_must_belong_to_the_scope(self):
        self.blocked(NEW_ENTRY, (5, 1, 3, 4, 4, "2026-11-06", 8, 5), "CTR does not belong")   # phase 3 is BB0002's

    def test_booking_to_a_second_phase_of_a_scope(self):
        self.con.execute(NEW_ENTRY, (5, 1, 2, 4, 4, "2026-11-06", 8, 5))                       # BB0001 Execute

    def test_closed_phase_blocks_booking(self):
        self.con.execute("UPDATE phase SET status='Closed' WHERE phase_id=2")
        self.blocked(NEW_ENTRY, (5, 1, 2, 4, 4, "2026-11-06", 8, 5), "CTR is not Open")

    def test_closed_project_blocks_booking(self):
        self.con.execute("UPDATE project SET status='Closed' WHERE project_id=1")
        self.blocked(NEW_ENTRY, (5, 1, 1, 4, 4, "2026-11-06", 8, 5), "Project is not Open")

    def test_scope_client_must_match_project_client(self):
        self.blocked("INSERT INTO scope (scope_code, client_id, project_id, description, status) "
                     "VALUES ('X1', 2, 1, 'Wrong client', 'Open')", msg="must match its project client")

    def test_moving_an_entry_to_another_scopes_phase_is_blocked(self):
        eid = self.scalar("select entry_id from time_entry where status='Submitted' and scope_id=1 limit 1")
        self.blocked("UPDATE time_entry SET phase_id=3, last_edited_by=5, last_edited_at='t1' WHERE entry_id=?", (eid,), "CTR does not belong")

    def test_ctr_view_has_one_line_per_phase_and_skill_code(self):
        rows = self.con.execute("select phase, skill_code, hours from v_ctr where scope_code='BB0001' and skill_code='PE-01'").fetchall()
        self.assertEqual([(r[0], r[1], r[2]) for r in rows], [("BB0001-PM-E-001", "PE-01", 8), ("BB0001-PM-D-001", "PE-01", 8)] if rows[0][0].endswith("E-001") else [("BB0001-PM-D-001", "PE-01", 8), ("BB0001-PM-E-001", "PE-01", 8)])
        self.assertEqual(self.scalar("select sum(total) from v_ctr"), self.scalar("select sum(total) from v_period_report"))


class TestPeriods(Base):
    def test_closed_week_blocks_new_entries(self):
        self.blocked(NEW_ENTRY, (5, 1, 1, 4, 4, "2026-10-28", 8, 5), "period is not open")

    def test_missing_week_blocks_new_entries(self):
        self.blocked(NEW_ENTRY, (5, 1, 1, 4, 4, "2026-12-01", 8, 5), "period is not open")

    def test_sunday_belongs_to_the_week_starting_the_previous_monday(self):
        self.assertEqual(self.scalar("select monday_of('2026-11-08')"), "2026-11-02")
        self.assertEqual(self.scalar("select monday_of('2026-11-02')"), "2026-11-02")

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
            "UPDATE time_entry SET status='Approved', approved_by=7, last_edited_by=7, last_edited_at='t2' WHERE entry_id=?",
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

    def test_only_the_persons_line_manager_can_approve(self):
        eid = self.first("status='Submitted' and user_id in (select user_id from app_user where line_manager_id=7)")   # Alisdair Woodhead (7) manages them
        sql = "UPDATE time_entry SET status='Approved', approved_by=?, last_edited_by=?, last_edited_at='t1' WHERE entry_id=?"
        self.blocked(sql, (5, 5, eid), "Only this person's approver")    # a colleague
        self.blocked(sql, (8, 8, eid), "Only this person's approver")    # Kenneth Nicolson manages other people, not this one
        self.blocked(sql, (29, 29, eid), "Only this person's approver")  # the cost controller approves only when nobody else can
        self.blocked(sql, (30, 30, eid), "Only this person's approver")  # nor does the IT admin
        self.con.execute(sql, (7, 7, eid))                               # their line manager

    def test_nobody_approves_their_own_entry(self):
        eid = self.scalar("select entry_id from time_entry where user_id=5 and status='Submitted' limit 1")
        self.blocked("UPDATE time_entry SET status='Approved', approved_by=5, last_edited_by=5, last_edited_at='t1' "
                     "WHERE entry_id=?", (eid,), "never your own")

    def test_only_draft_entries_can_be_deleted(self):
        self.blocked("DELETE FROM time_entry WHERE status='Approved'", msg="Only Draft entries")
        self.blocked("DELETE FROM time_entry WHERE status='Submitted'", msg="Only Draft entries")
        self.blocked("DELETE FROM time_entry", msg="Only Draft entries")

    def test_draft_delete_must_name_the_user_and_is_audited(self):
        self.con.execute("INSERT INTO time_entry (user_id, scope_id, phase_id, worked_role_id, rate_id, work_date, hours, "
                         "status, created_by) VALUES (5,1,1,4,4,'2026-11-06',8,'Draft',5)")  # new Draft, no editor yet
        eid = self.scalar("select max(entry_id) from time_entry")
        self.blocked("DELETE FROM time_entry WHERE entry_id=?", (eid,), "must name the user")
        self.con.execute("UPDATE time_entry SET last_edited_by=5, last_edited_at='t9' WHERE entry_id=?", (eid,))
        self.con.execute("DELETE FROM time_entry WHERE entry_id=?", (eid,))
        self.assertEqual(self.scalar("select count(*) from time_entry where entry_id=?", eid), 0)
        row = self.con.execute("select action, changed_by, old_status, old_hours from time_entry_audit "
                               "where entry_id=? order by audit_id desc limit 1", (eid,)).fetchone()
        self.assertEqual(tuple(row), ("DELETE", 5, "Draft", 8))

    def test_rejected_entry_back_in_draft_can_be_cleared(self):
        eid = self.scalar("select entry_id from time_entry where status='Draft' limit 1")  # Ajin Sabu's rejected entry
        self.con.execute("UPDATE time_entry SET last_edited_by=10, last_edited_at='t9' WHERE entry_id=?", (eid,))
        self.con.execute("DELETE FROM time_entry WHERE entry_id=?", (eid,))
        self.assertEqual(self.scalar("select count(*) from time_entry"), 63)

    def test_every_status_change_is_audited(self):
        n_status = self.scalar("select count(*) from time_entry_audit where action='STATUS'")
        self.assertGreater(n_status, 0)
        missing = self.scalar("select count(*) from time_entry_audit where action='STATUS' and changed_by is null")
        self.assertEqual(missing, 0)


class TestViews(Base):
    def test_ctr_counts_approved_only_and_shows_pending_separately(self):
        row = self.con.execute(
            "select hours, pending_hours from v_ctr where scope_code='BB0001' and skill_code='PM-02'").fetchone()
        approved = self.scalar("select sum(hours) from time_entry t join scope s using(scope_id) join rate r using(rate_id) "
                               "join role ro using(role_id) where s.scope_code='BB0001' and ro.skill_code='PM-02' "
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
        self.assertIn(("PM-04",), lines)
        self.assertIn(("PM-02",), lines)


class TestPermissionLevels(Base):
    """user / controller / admin. Approver is not a level: it means being named on a scope, and only standard users can be."""
    def test_there_are_three_levels_and_the_demo_has_controllers_and_one_admin(self):
        levels = dict(self.con.execute("select permission, count(*) from app_user group by permission").fetchall())
        self.assertEqual(levels, {"user": 27, "controller": 3, "admin": 1})

    def test_cost_permission_does_not_depend_on_job_title(self):
        # the two cost engineers are controllers, and so is the commercial lead, who is not a cost engineer
        controllers = {r[0]: r[1] for r in self.con.execute("select u.name, r.name from app_user u join role r using(role_id) where permission='controller'")}
        self.assertEqual(controllers, {"Emma Noble": "Cost Engineer", "Abbi Murray": "Cost Engineer", "Stephanie Apedaile": "Commercial Lead"})
        # and anyone can be given it, whatever their title (as long as they approve nothing)
        self.con.execute("UPDATE app_user SET permission='controller' WHERE name='Daniel Evans'")
        self.assertEqual(self.scalar("select permission from app_user where name='Daniel Evans'"), "controller")
        self.blocked("UPDATE app_user SET permission='superuser' WHERE user_id=1")

    def test_no_controller_or_admin_is_an_approver_in_the_demo_data(self):
        self.assertEqual(self.scalar("select count(*) from v_scope_approver va join app_user u on u.user_id=va.user_id where u.permission<>'user'"), 0)
        self.assertGreater(self.scalar("select count(*) from v_scope_approver"), 0)

    def test_the_queue_shows_each_persons_waiting_hours_to_their_approver(self):
        self.assertEqual({r[0] for r in self.con.execute("select distinct approver_user_id from v_approver_queue where entry_id in "
                                                         "(select entry_id from time_entry where user_id=5)")}, {7})
        self.con.execute("UPDATE app_user SET approver_id=8 WHERE user_id=5")
        self.assertEqual({r[0] for r in self.con.execute("select distinct approver_user_id from v_approver_queue where entry_id in "
                                                         "(select entry_id from time_entry where user_id=5)")}, {8})

    def test_the_approvers_can_book_to_the_client_they_approve(self):
        # Head of Projects (the approvers' job title) is priced on both rate cards, so Alisdair (Apache) and Kenneth (Spirit) can book
        self.assertEqual(self.scalar("select count(*) from rate r join role ro using(role_id) where ro.name='Head of Projects'"), 2)

    def test_a_cost_controller_approves_the_hours_of_someone_with_nobody_to_approve_for_them(self):
        self.con.execute("UPDATE app_user SET line_manager_id=NULL WHERE user_id=9")                  # Daniel Evans: no line manager, no approver named
        eid = self.con.execute(NEW_ENTRY, (9, 1, 1, 3, 3, "2026-11-04", 4, 9)).fetchone()[0]
        sql = "UPDATE time_entry SET status='Approved', approved_by=?, last_edited_by=?, last_edited_at='t1' WHERE entry_id=?"
        self.blocked(sql, (9, 9, eid), "Only this person's approver")        # never their own
        self.blocked(sql, (7, 7, eid), "Only this person's approver")        # his old line manager no longer is
        self.blocked(sql, (30, 30, eid), "Only this person's approver")      # the admin never approves
        self.con.execute(sql, (29, 29, eid))                                 # a cost controller does
        self.assertEqual(self.scalar("select approved_by from time_entry where entry_id=?", eid), 29)
        # ...but a controller still cannot approve the hours of someone who has an approver
        other = self.scalar("select entry_id from time_entry where status='Submitted' and user_id=5 limit 1")
        self.blocked(sql, (29, 29, other), "Only this person's approver")

    def test_an_approver_named_on_the_record_replaces_the_line_manager_for_approval(self):
        eid = self.scalar("select entry_id from time_entry where user_id=5 and status='Submitted' limit 1")       # Ewan Shaw, managed by Alisdair (7)
        sql = "UPDATE time_entry SET status='Approved', approved_by=?, last_edited_by=?, last_edited_at='t1' WHERE entry_id=?"
        self.assertEqual(self.scalar("select approver_id from v_person_approver where user_id=5"), 7)
        self.con.execute("UPDATE app_user SET approver_id=8 WHERE user_id=5")                                      # Kenneth makes more sense for Ewan's work
        self.assertEqual(self.scalar("select approver_id from v_person_approver where user_id=5"), 8)
        self.blocked(sql, (7, 7, eid), "Only this person's approver")                                              # the line manager no longer approves
        self.con.execute(sql, (8, 8, eid))
        self.con.execute("UPDATE app_user SET approver_id=NULL WHERE user_id=5")                                   # back to the line manager
        self.assertEqual(self.scalar("select approver_id from v_person_approver where user_id=5"), 7)

    def test_a_person_cannot_be_their_own_approver_and_the_record_fields_are_checked(self):
        self.blocked("UPDATE app_user SET approver_id=5 WHERE user_id=5", msg="check constraint")
        self.blocked("UPDATE app_user SET employment_type='Contract' WHERE user_id=5", msg="check constraint")        # Staff, Contractor - LTD or Contractor - PAYE only
        self.con.execute("UPDATE app_user SET employment_type='Contractor - PAYE' WHERE user_id=5")
        self.blocked("UPDATE app_user SET hours_per_week=0 WHERE user_id=5", msg="check constraint")
        self.blocked("UPDATE app_user SET hours_per_week=200 WHERE user_id=5", msg="check constraint")
        self.blocked("UPDATE app_user SET default_location_id=99 WHERE user_id=5", msg="foreign key")
        self.con.execute("UPDATE app_user SET default_location_id=2, hours_per_week=37.5 WHERE user_id=5")
        self.assertEqual(self.scalar("select default_location_id from app_user where user_id=4"), 1)                  # everyone else starts On Shore, 40 hours
        self.assertEqual(self.scalar("select hours_per_week from app_user where user_id=4"), 40)

    def test_weekly_hours_are_there_to_work_overtime_out_from_later(self):
        row = self.con.execute("select total_hours, submitted_hours, hours_per_week, hours_over_base from v_weekly_hours where user_id=5 and week_start='2026-11-02'").fetchone()
        self.assertEqual(row[0], self.scalar("select sum(hours) from time_entry where user_id=5 and work_date between '2026-11-02' and '2026-11-08'"))
        self.assertEqual(row[2], 40)
        self.assertEqual(row[3], max(row[0] - 40, 0))
        self.con.execute("UPDATE app_user SET hours_per_week=10 WHERE user_id=5")                                      # a part-timer: the same hours are now over base
        again = self.con.execute("select hours_over_base from v_weekly_hours where user_id=5 and week_start='2026-11-02'").fetchone()[0]
        self.assertEqual(again, row[0] - 10)


class TestActiveDates(Base):
    """Only live people can book (start and end dates)."""
    # Ewan Shaw (5) started on 1 Mar 2026 and has no end date. He is a Planner (rate 4). BB0001 is scope 1, phase 1.
    def book(self, date, user=5, scope=1, phase=1, role=4, rate=4):
        return self.con.execute(NEW_ENTRY, (user, scope, phase, role, rate, date, 2, user))

    def test_nobody_can_book_before_their_start_date(self):
        self.blocked(NEW_ENTRY, (5, 1, 1, 4, 4, "2026-02-27", 2, 5), msg="not active on that date")   # the Friday before 1 March
        self.book("2026-11-06")                                                                              # long after: fine

    def test_nobody_can_book_after_their_end_date_but_the_last_day_is_fine(self):
        self.con.execute("UPDATE app_user SET end_date='2026-11-06' WHERE user_id=5")
        self.book("2026-11-06")                                       # the last day
        self.blocked(NEW_ENTRY, (5, 1, 1, 4, 4, "2026-11-07", 2, 5), msg="not active on that date")

    def test_internal_hours_follow_the_same_dates(self):
        self.con.execute("UPDATE app_user SET end_date='2026-11-04' WHERE user_id=5")
        self.blocked(NEW_ENTRY, (5, 7, 8, 4, None, "2026-11-05", 2, 5), msg="not active on that date")
        self.con.execute(NEW_ENTRY, (5, 7, 8, 4, None, "2026-11-04", 2, 5))

    def test_a_person_with_no_end_date_stays_live(self):
        self.assertIsNone(self.scalar("select end_date from app_user where user_id=5"))
        self.con.execute("INSERT INTO period (week_start) VALUES ('2026-12-14')")  # a far later week, opened
        self.book("2026-12-18")                                                    # still allowed: no end date

    def test_the_seed_data_respects_the_rules(self):
        # every booking in the seed is on or after the person's start date and on or before their end date
        bad = self.scalar("select count(*) from time_entry t join app_user u on u.user_id=t.user_id where t.work_date < u.start_date "
                          "or (u.end_date is not null and t.work_date > u.end_date)")
        self.assertEqual(bad, 0)


class TestDailyHourCap(Base):
    """A person cannot book more than 24 hours in one day, however the hours are spread over rows."""
    INS = ("INSERT INTO time_entry (user_id, scope_id, phase_id, worked_role_id, rate_id, work_date, hours, created_by, location_id, overtime) "
           "VALUES (5,1,1,4,4,'2026-11-06',?,5,?,?)")

    def test_the_day_total_is_capped_across_locations_and_overtime(self):
        self.con.execute(self.INS, (20, 1, 0))
        self.blocked(self.INS, (5, 2, 0), "more than 24 hours")      # offshore, same day
        self.blocked(self.INS, (5, 1, 1), "more than 24 hours")      # overtime, same day
        self.con.execute(self.INS, (4, 2, 0))                        # exactly 24 in all is allowed
        self.assertEqual(self.scalar("select sum(hours) from time_entry where user_id=5 and work_date='2026-11-06'"), 24)

    def test_editing_hours_up_is_capped_too_and_other_people_and_days_are_separate(self):
        self.con.execute(self.INS, (10, 1, 0))
        self.con.execute(self.INS, (10, 2, 0))
        eid = self.scalar("select max(entry_id) from time_entry where user_id=5")
        self.blocked("UPDATE time_entry SET hours=15, last_edited_by=5, last_edited_at='x' WHERE entry_id=?", (eid,), "more than 24 hours")
        self.con.execute("UPDATE time_entry SET hours=14, last_edited_by=5, last_edited_at='x' WHERE entry_id=?", (eid,))
        self.con.execute("INSERT INTO time_entry (user_id, scope_id, phase_id, worked_role_id, rate_id, work_date, hours, created_by) VALUES (5,1,1,4,4,'2026-11-07',24,5)")


class TestOffshoreNights(Base):
    """The offshore allowance is paid per night spent offshore, to staff only. Nothing is claimed: nights are worked out from the hours."""
    def setUp(self):
        super().setUp()
        self.con.execute("UPDATE app_user SET employment_type='Staff' WHERE user_id=5")   # Ewan Shaw is a contractor in the seed
        for w in ("2026-11-09", "2026-11-16", "2026-11-23"):
            self.con.execute("insert into period (week_start) values (?) on conflict do nothing", (w,))

    def off(self, date, user=5, phase=1, hours=8, loc=2, scope=1, role=4, rate=4):
        return self.con.execute("INSERT INTO time_entry (user_id, scope_id, phase_id, worked_role_id, rate_id, work_date, hours, created_by, location_id, status) "
                                "VALUES (?,?,?,?,?,?,?,?,?,'Draft') RETURNING entry_id", (user, scope, phase, role, rate, date, hours, user, loc)).fetchone()[0]

    def nights(self, user=5):
        return [r[0] for r in self.con.execute("select work_date from v_offshore_night where user_id=? order by work_date", (user,))]

    def test_a_trip_of_n_days_is_n_minus_1_nights_so_monday_to_sunday_is_six(self):
        for d in range(9, 16):
            self.off("2026-11-%02d" % d)
        self.assertEqual(self.nights(), ["2026-11-%02d" % d for d in range(9, 15)])   # Mon-Sat: the last day, Sunday, earns no night

    def test_a_single_offshore_day_earns_no_night_and_gaps_break_a_trip(self):
        self.off("2026-11-09")
        self.assertEqual(self.nights(), [])
        self.off("2026-11-11")                       # Wednesday: not the next day
        self.assertEqual(self.nights(), [])

    def test_a_trip_is_counted_across_weeks(self):
        for d in ("2026-11-13", "2026-11-14", "2026-11-15", "2026-11-16"):   # Fri, Sat, Sun, then Monday of the next week
            self.off(d)
        self.assertEqual(self.nights(), ["2026-11-13", "2026-11-14", "2026-11-15"])
        self.assertEqual(self.scalar("select count(*) from v_allowance_value where user_id=5 and work_date between '2026-11-09' and '2026-11-15'"), 3)

    def test_contractors_earn_no_allowance(self):
        self.con.execute("UPDATE app_user SET employment_type='Contractor - LTD' WHERE user_id=5")
        self.off("2026-11-09")
        self.off("2026-11-10")
        self.assertEqual(self.nights(), [])

    def test_only_a_location_that_earns_the_allowance_counts(self):
        self.off("2026-11-09", loc=3)
        self.off("2026-11-10", loc=3)                # a site visit is not offshore
        self.assertEqual(self.nights(), [])
        self.con.execute("UPDATE location SET earns_allowance=1 WHERE location_id=3")   # a cost controller could decide otherwise
        self.assertEqual(self.nights(), ["2026-11-09"])

    def test_a_night_follows_the_status_of_the_hours_it_belongs_to(self):
        a, _ = self.off("2026-11-09"), self.off("2026-11-10")
        status = lambda: self.scalar("select status from v_offshore_night where user_id=5 and work_date='2026-11-09'")
        self.assertEqual(status(), "Draft")
        self.con.execute("UPDATE time_entry SET status='Submitted', last_edited_by=5, last_edited_at='t1' WHERE entry_id=?", (a,))
        self.assertEqual(status(), "Submitted")
        self.con.execute("UPDATE time_entry SET status='Approved', approved_by=7, last_edited_by=7, last_edited_at='t2' WHERE entry_id=?", (a,))
        self.assertEqual(status(), "Approved")

    def test_the_night_is_charged_to_the_code_with_most_offshore_hours_that_day(self):
        self.off("2026-11-09", phase=1, hours=3)
        self.off("2026-11-09", phase=2, hours=5)     # the second Apache code has more of the day
        self.off("2026-11-10", phase=1, hours=8)
        self.assertEqual(self.scalar("select phase_id from v_offshore_night where user_id=5"), 2)

    def test_a_night_is_worth_the_rate_in_force_on_that_night(self):
        for d in ("2026-11-09", "2026-11-10", "2026-11-11"):
            self.off(d)
        self.con.execute("INSERT INTO allowance_rate (allowance_id, amount, effective_from) VALUES (1,120,'2026-11-10')")
        self.assertEqual([r[0] for r in self.con.execute("select amount from v_allowance_value where user_id=5 order by work_date")], [100, 120])

    def test_the_standard_rate_is_100_a_night_and_rates_are_append_only(self):
        self.assertEqual([tuple(r) for r in self.con.execute("select allowance_id, amount, effective_from from allowance_rate")], [(1, 100, "2026-01-01")])
        self.blocked("UPDATE allowance_rate SET amount=1", msg="never edited")
        self.blocked("DELETE FROM allowance_rate", msg="never deleted")
        self.blocked("INSERT INTO allowance_rate (allowance_id, amount, effective_from) VALUES (1,150,'2026-01-01')", msg="unique constraint")
        self.blocked("INSERT INTO allowance_rate (allowance_id, amount, effective_from) VALUES (1,-5,'2026-06-01')", msg="check constraint")

    def test_the_value_does_not_depend_on_overtime_or_how_many_hours(self):
        self.off("2026-11-09", hours=2)
        self.off("2026-11-10", hours=12)
        self.assertEqual(self.scalar("select amount from v_allowance_value where user_id=5"), 100)

    def test_internal_hours_offshore_count_too(self):
        self.off("2026-11-09", scope=7, phase=8, rate=None)
        self.off("2026-11-10", scope=7, phase=8, rate=None)
        self.assertEqual(self.nights(), ["2026-11-09"])

    def test_nothing_is_claimed_there_is_no_claims_table(self):
        self.assertEqual(self.scalar("select count(*) from information_schema.tables where table_schema='public' and table_name in ('allowance_entry','allowance_audit')"), 0)


class TestCtrBudgets(Base):
    """Budget hours per CTR from P6: snapshots that are never edited, compared with approved hours."""
    def test_budgets_are_append_only_and_unique_per_date(self):
        self.blocked("UPDATE ctr_budget SET hours=1", msg="never edited")
        self.blocked("DELETE FROM ctr_budget", msg="never deleted")
        self.blocked("INSERT INTO ctr_budget (phase_id, hours, as_of) VALUES (1, 5, '2026-11-06')", msg="unique constraint")
        self.blocked("INSERT INTO ctr_budget (phase_id, hours, as_of) VALUES (1, -5, '2026-12-01')", msg="check constraint")

    def test_the_view_compares_the_latest_budget_with_approved_hours_only(self):
        row = self.con.execute("select budget_hours, actual_hours, pending_hours from v_ctr_budget where phase_id=1").fetchone()
        self.assertEqual(tuple(row), (160, 71.5, 71))
        self.con.execute("INSERT INTO ctr_budget (phase_id, hours, as_of) VALUES (1, 180, '2026-11-20')")
        self.assertEqual(self.scalar("select budget_hours from v_ctr_budget where phase_id=1"), 180)      # the later snapshot wins
        self.assertEqual(self.scalar("select count(*) from ctr_budget where phase_id=1"), 2)
        self.assertIsNone(self.scalar("select budget_hours from v_ctr_budget where phase_id=5"))           # no budget: NULL, still listed
        self.assertEqual(self.scalar("select count(*) from v_ctr_budget where client='Internal'"), 0)    # internal codes have no budgets


class TestBuildExtras(unittest.TestCase):
    def test_extra_sql_is_applied_after_the_seed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            extra = os.path.join(d, "extra.sql")
            with open(extra, "w", encoding="utf-8") as f:
                f.write("INSERT INTO scope (scope_code, client_id, project_id, description, status) VALUES ('X9999', 2, 3, 'Local scope', 'Open');")
            with build(None, extra=[extra]) as con:
                self.assertEqual(con.execute("select count(*) from scope where scope_code='X9999'").fetchone()[0], 1)
            with build(None) as con:
                self.assertEqual(con.execute("select count(*) from scope where scope_code='X9999'").fetchone()[0], 0)


class TestRatesAreAppendOnly(Base):
    """FR-12: a rate is never edited; a new row with a later effective date takes over."""
    def test_a_rate_cannot_be_edited_or_deleted(self):
        self.blocked("UPDATE rate SET sales_rate=1 WHERE rate_id=4", msg="never edited")
        self.blocked("UPDATE rate SET effective_from='2020-01-01' WHERE rate_id=4", msg="never edited")
        self.blocked("DELETE FROM rate WHERE rate_id=4", msg="never deleted")
        self.assertGreater(self.scalar("select sales_rate from rate where rate_id=4"), 1)

    def test_a_new_row_with_a_later_date_takes_over_and_old_hours_keep_their_price(self):
        old_rate = self.scalar("select sales_rate from rate where rate_id=4")
        entry = self.scalar("select entry_id from time_entry where rate_id=4 limit 1")
        self.con.execute("INSERT INTO rate (client_id, role_id, sales_rate, effective_from) "
                         "SELECT client_id, role_id, sales_rate + 10, '2027-01-01' FROM rate WHERE rate_id=4")
        self.assertEqual(self.scalar("select rate_id from time_entry where entry_id=?", entry), 4)
        self.assertEqual(self.scalar("select sales_rate from rate where rate_id=4"), old_rate)


class TestAuditTrailIsAppendOnly(Base):
    """NFR-05: every change to hours is recorded and the record cannot be altered or removed."""
    def test_audit_rows_cannot_be_changed(self):
        self.assertGreater(self.scalar("select count(*) from time_entry_audit"), 0)
        self.blocked("UPDATE time_entry_audit SET reason='tidied up'", msg="cannot be changed")
        self.blocked("UPDATE time_entry_audit SET new_hours=0 WHERE audit_id=1", msg="cannot be changed")

    def test_audit_rows_cannot_be_deleted(self):
        before = self.scalar("select count(*) from time_entry_audit")
        self.blocked("DELETE FROM time_entry_audit WHERE audit_id=1", msg="cannot be deleted")
        self.blocked("DELETE FROM time_entry_audit", msg="cannot be deleted")
        self.assertEqual(self.scalar("select count(*) from time_entry_audit"), before)

    def test_normal_work_still_adds_audit_rows(self):
        before = self.scalar("select count(*) from time_entry_audit")
        self.con.execute(NEW_ENTRY, (5, 1, 1, 4, 4, "2026-11-06", 2, 5))
        self.assertGreater(self.scalar("select count(*) from time_entry_audit"), before)


class TestPaymentTerms(Base):
    def test_only_a_lump_sum_ctr_carries_a_value(self):
        self.con.execute("UPDATE phase SET payment_terms='Lump Sum', lump_sum_value=5000 WHERE phase_id=1")
        self.blocked("UPDATE phase SET payment_terms='Reimbursable' WHERE phase_id=1", msg="check constraint")        # the value must go first
        self.blocked("UPDATE phase SET lump_sum_value=-1 WHERE phase_id=1", msg="check constraint")
        self.con.execute("UPDATE phase SET payment_terms='Non-reimbursable', lump_sum_value=NULL WHERE phase_id=1")
        self.blocked("UPDATE phase SET lump_sum_value=100 WHERE phase_id=1", msg="check constraint")


class TestRateForSkillCode(Base):
    """Rates: hours are priced at the client's rate for the skill code booked, not by any position."""
    def apache_rate(self, role):
        return self.scalar("select rate_id from rate where client_id=1 and role_id=? order by effective_from desc limit 1", role)

    def test_a_person_is_priced_at_the_rate_of_the_skill_code_they_book(self):
        # Ewan Shaw is a Planner (role 4): booking as a Planner needs the Planner rate, not another position's
        self.blocked(NEW_ENTRY, (5, 1, 1, 4, self.apache_rate(3), "2026-11-06", 8, 5), "Rate does not match")
        self.con.execute(NEW_ENTRY, (5, 1, 1, 4, self.apache_rate(4), "2026-11-06", 8, 5))

    def test_the_rate_does_not_cover_other_roles_or_other_clients(self):
        # covering another role (5) with the Project Engineer rate is not allowed, and neither is a rate of another client
        self.blocked(NEW_ENTRY, (5, 1, 1, 5, self.apache_rate(3), "2026-11-06", 8, 5), "Rate does not match")
        other = self.scalar("select rate_id from rate where client_id=2 and role_id=3 limit 1")
        self.blocked(NEW_ENTRY, (5, 1, 1, 4, other, "2026-11-06", 8, 5), "Rate does not match")


class TestBookingCodeControls(Base):
    """R06 transaction controls (a list of people, a booking window), R04a/R04f description and references."""
    # BB0001 is scope 1 (approver Alisdair Woodhead, 7), phases 1 Define and 2 Execute. Ewan Shaw (5) and Daniel Evans (9) book to it.
    def book(self, user, date, phase=1, role=4, rate=4):
        return self.con.execute(NEW_ENTRY, (user, 1, phase, role, rate, date, 2, user))

    def test_a_code_is_open_to_anyone_by_default(self):
        self.assertEqual(self.scalar("select restricted from phase where phase_id=1"), 0)
        self.book(5, "2026-11-06")

    def test_a_restricted_code_can_only_be_booked_by_the_people_on_its_list(self):
        self.con.execute("UPDATE phase SET restricted=1 WHERE phase_id=1")
        self.blocked(NEW_ENTRY, (5, 1, 1, 4, 4, "2026-11-06", 2, 5), msg="restricted")          # nobody listed yet
        self.con.execute("INSERT INTO phase_person (phase_id, user_id) VALUES (1, 5)")
        self.book(5, "2026-11-06")                                                                  # Ewan is on the list
        self.con.execute("INSERT INTO period (week_start) VALUES ('2026-11-02') ON CONFLICT DO NOTHING")
        self.blocked(NEW_ENTRY, (9, 1, 1, 3, 3, "2026-11-06", 2, 9),
                     msg="not on its list")                                                         # Daniel is not
        self.book(9, "2026-11-06", phase=2, role=3, rate=3)                                         # another code of the scope is unaffected

    def test_a_booking_window_limits_the_work_dates(self):
        self.con.execute("UPDATE phase SET book_from='2026-11-03', book_to='2026-11-05' WHERE phase_id=1")
        self.blocked(NEW_ENTRY, (5, 1, 1, 4, 4, "2026-11-02", 2, 5), msg="outside its booking dates")
        self.blocked(NEW_ENTRY, (5, 1, 1, 4, 4, "2026-11-06", 2, 5), msg="outside its booking dates")
        self.book(5, "2026-11-03")
        self.book(5, "2026-11-05")                                                                  # both ends are inside the window

    def test_a_window_can_be_open_ended(self):
        self.con.execute("UPDATE phase SET book_from='2026-11-05' WHERE phase_id=1")
        self.blocked(NEW_ENTRY, (5, 1, 1, 4, 4, "2026-11-04", 2, 5), msg="outside its booking dates")
        self.book(5, "2026-11-06")

    def test_controls_do_not_change_hours_already_booked(self):
        before = self.scalar("select count(*) from time_entry where phase_id=1")
        self.con.execute("UPDATE phase SET restricted=1, book_from='2030-01-01' WHERE phase_id=1")
        self.assertEqual(self.scalar("select count(*) from time_entry where phase_id=1"), before)

    def test_a_code_can_carry_a_description_and_wbs_cbs_abs_references(self):
        self.con.execute("UPDATE phase SET description='Clear the wellbay', wbs='1.2.3', cbs='C-40', abs_code='A-7' WHERE phase_id=1")
        self.assertEqual(tuple(self.con.execute("select description, wbs, cbs, abs_code from phase where phase_id=1").fetchone()),
                         ("Clear the wellbay", "1.2.3", "C-40", "A-7"))
        self.assertIsNone(self.scalar("select wbs from phase where phase_id=2"))            # all of them are optional


class TestReopen(Base):
    """A cost controller can reopen submitted or approved hours (back to Draft) with a reason, and nobody else can."""
    REOPEN = ("UPDATE time_entry SET status='Draft', approved_by=NULL, approved_at=NULL, reopen_reason=?, "
              "last_edited_by=?, last_edited_at=? WHERE entry_id=?")

    def entry(self, status, user=5):
        return self.scalar("select entry_id from time_entry where status=? and user_id=? limit 1", status, user)

    def test_a_cost_controller_reopens_approved_and_submitted_hours_with_a_reason(self):
        for n, (status, user) in enumerate((("Approved", 5), ("Submitted", 3))):
            eid = self.entry(status, user)
            self.con.execute(self.REOPEN, ("Wrong code booked", 29, "t%d" % n, eid))  # Stephanie Apedaile, a cost controller
            row = self.con.execute("select status, approved_by, reopen_reason from time_entry where entry_id=?", (eid,)).fetchone()
            self.assertEqual(tuple(row), ("Draft", None, "Wrong code booked"), status)
            audit = self.con.execute("select action, reason, old_status, new_status, changed_by from time_entry_audit where entry_id=? "
                                     "order by audit_id desc limit 1", (eid,)).fetchone()
            self.assertEqual(tuple(audit), ("STATUS", "Reopened: Wrong code booked", status, "Draft", 29))

    def test_nobody_else_can_reopen(self):
        approved, submitted = self.entry("Approved", 5), self.entry("Submitted", 3)
        for who in (7, 8, 30, 5):  # an approver, the other approver, the IT admin, a standard user
            self.blocked(self.REOPEN, ("Because", who, "t1", approved), "Only a cost controller")
            self.blocked(self.REOPEN, ("Because", who, "t2", submitted), "Only a cost controller")

    def test_reopening_needs_a_reason_and_approved_hours_have_no_other_way_back_to_draft(self):
        eid = self.entry("Approved", 5)
        self.blocked(self.REOPEN, ("", 29, "t1", eid), "must give a reason")
        self.blocked(self.REOPEN, (None, 29, "t2", eid), "must give a reason")
        # a plain status change, or a rejection reason, does not turn approved hours back into a draft
        self.blocked("UPDATE time_entry SET status='Draft', last_edited_by=29, last_edited_at='t3' WHERE entry_id=?", (eid,), "must give a reason")
        self.blocked("UPDATE time_entry SET status='Draft', reject_reason='No', last_edited_by=7, last_edited_at='t4' WHERE entry_id=?", (eid,),
                     "must give a reason")
        self.assertEqual(self.scalar("select status from time_entry where entry_id=?", eid), "Approved")

    def test_a_cost_controller_cannot_reopen_their_own_hours(self):
        # Stephanie (29) books INT01  and cannot reopen it herself
        mine = self.con.execute(NEW_ENTRY, (29, 7, 8, 32, None, "2026-11-04", 2, 29)).fetchone()[0]  # role 32 = Commercial Lead
        self.blocked(self.REOPEN, ("Oops", 29, "t1", mine), "cannot reopen their own")
        self.con.execute(self.REOPEN, ("Oops", 1, "t2", mine))  # another cost controller (Emma Noble) can

    def test_rejecting_still_works_and_is_not_recorded_as_a_reopen(self):
        eid = self.entry("Submitted", 3)
        self.con.execute("UPDATE time_entry SET status='Draft', reject_reason='Wrong scope', last_edited_by=7, last_edited_at='t1' WHERE entry_id=?", (eid,))
        audit = self.con.execute("select reason from time_entry_audit where entry_id=? order by audit_id desc limit 1", (eid,)).fetchone()
        self.assertEqual(audit[0], "Wrong scope")
        self.blocked("UPDATE time_entry SET status='Draft', last_edited_by=7, last_edited_at='t2' WHERE entry_id=?", (self.entry("Submitted", 3),),
                     "needs a reason")


class TestHourTypes(Base):
    """Every entry has a location (On Shore / Offshore / Site visit) and an overtime flag. Overtime has no separate rate yet."""
    def test_the_locations_are_seeded_and_new_entries_default_to_on_site_normal_time(self):
        self.assertEqual([tuple(r) for r in self.con.execute("select location_id, name, active from location order by location_id")],
                         [(1, "On Shore", 1), (2, "Offshore", 1), (3, "Site visit", 1)])
        eid = self.con.execute(NEW_ENTRY, (5, 1, 1, 4, 4, "2026-11-06", 8, 5)).fetchone()[0]
        self.assertEqual(tuple(self.con.execute("select location_id, overtime from time_entry where entry_id=?", (eid,)).fetchone()), (1, 0))

    def test_the_seed_has_offshore_weeks_and_overtime_days(self):
        self.assertEqual(self.scalar("select count(*) from time_entry where location_id=2"), 12)               # Rob's day, Louis's week, Lloyd's four days
        self.assertEqual(self.scalar("select count(distinct user_id) from time_entry where location_id=2"), 3)
        self.assertEqual(self.scalar("select count(*) from time_entry where overtime=1"), 3)                  # Neil's day and two of Louis's

    def test_the_seed_has_nights_for_two_staff_offshore_and_none_for_a_day_trip(self):
        got = {(r[0], r[1]): r[2] for r in self.con.execute("select user_id, status, count(*) from v_offshore_night group by 1, 2")}
        # Louis (11): Mon-Fri offshore = 4 nights, Mon-Wed approved; Lloyd (18): Tue-Fri offshore = 3 nights, submitted; Rob's single day is none
        self.assertEqual(got, {(11, "Approved"): 3, (11, "Submitted"): 1, (18, "Submitted"): 3})
        self.assertEqual(self.scalar("select sum(amount) from v_allowance_value where status='Approved'"), 300)
        self.assertEqual(self.scalar("select count(*) from v_offshore_night n join app_user u using(user_id) where u.employment_type<>'Staff'"), 0)

    def test_location_and_overtime_must_be_valid(self):
        sql = ("INSERT INTO time_entry (user_id, scope_id, phase_id, worked_role_id, rate_id, work_date, hours, created_by, location_id, overtime) "
               "VALUES (5,1,1,4,4,'2026-11-06',2,5,?,?)")
        self.blocked(sql, (99, 0))   # no such location
        self.blocked(sql, (1, 2))    # overtime is 0 or 1
        self.con.execute(sql, (2, 1))  # offshore overtime is fine

    def test_one_person_can_book_the_same_code_and_day_as_normal_time_and_as_overtime(self):
        self.con.execute(NEW_ENTRY, (5, 1, 1, 4, 4, "2026-11-06", 8, 5))
        self.con.execute("INSERT INTO time_entry (user_id, scope_id, phase_id, worked_role_id, rate_id, work_date, hours, created_by, overtime) "
                         "VALUES (5,1,1,4,4,'2026-11-06',2,5,1)")
        self.assertEqual(self.scalar("select count(*) from time_entry where user_id=5 and work_date='2026-11-06'"), 2)

    def test_changing_the_location_or_overtime_of_approved_hours_is_a_controlled_edit(self):
        eid = self.scalar("select entry_id from time_entry where status='Approved' and location_id=1 and overtime=0 limit 1")
        stamp = "last_edited_by=7, last_edited_at='t1'"
        self.blocked("UPDATE time_entry SET location_id=2, %s WHERE entry_id=?" % stamp, (eid,), "Changing an approved entry sets its status to Edited")
        self.blocked("UPDATE time_entry SET overtime=1, %s WHERE entry_id=?" % stamp, (eid,), "Changing an approved entry sets its status to Edited")
        self.con.execute("UPDATE time_entry SET location_id=2, status='Edited', edit_count=edit_count+1, last_change_reason='Was offshore', %s WHERE entry_id=?" % stamp, (eid,))
        # the audit trail keeps what it was before
        self.assertEqual(tuple(self.con.execute("select old_location_id, old_overtime from time_entry_audit where entry_id=? and action='STATUS' "
                                                "order by audit_id desc limit 1", (eid,)).fetchone()), (1, 0))

    def test_a_location_in_use_cannot_be_deleted(self):
        self.blocked("DELETE FROM location WHERE location_id=2")  # the offshore example entry refers to it


class TestInternalHours(Base):
    """Internal hours: paid by NMC, booked to Internal codes. No rate, kept out of the client reports."""
    # INT01 Business Services is scope 7, phase 8.
    def book(self, rate=None, scope=7, phase=8, date="2026-11-04"):
        return self.con.execute(NEW_ENTRY, (4, scope, phase, 4, rate, date, 3, 4))

    def test_the_internal_client_and_its_codes_are_seeded_without_rates(self):
        self.assertEqual(self.scalar("select count(*) from client where is_internal=1"), 1)
        self.assertEqual(self.scalar("select count(*) from scope s join client c using(client_id) where c.is_internal=1"), 12)
        self.assertEqual({r[0] for r in self.con.execute("select s.description from scope s join client c using(client_id) where c.is_internal=1")},
                         {"Sick", "Compassionate", "Business Development / Strategy", "Executive Leadership", "Commercial / Business", "Training", "HSEQ",
                          "Engineering", "Business Services", "Projects", "HR", "Other"})     # the company's list, without tender and bid support
        self.assertEqual(self.scalar("select count(*) from rate r join client c using(client_id) where c.is_internal=1"), 0)

    def test_anyone_can_book_internal_hours_with_no_rate(self):
        self.book()
        row = self.con.execute("select rate_id, status from time_entry where user_id=4").fetchone()
        self.assertEqual(tuple(row), (None, "Submitted"))  # the column default; the app saves drafts

    def test_internal_hours_cannot_carry_a_rate(self):
        self.blocked(NEW_ENTRY, (5, 7, 8, 4, 4, "2026-11-04", 3, 5), msg="Internal hours have no rate")

    def test_client_hours_still_need_a_rate(self):
        self.blocked(NEW_ENTRY, (5, 1, 1, 4, None, "2026-11-06", 3, 5), msg="Rate does not match")

    def test_internal_codes_get_no_rate_card(self):
        internal = self.scalar("select client_id from client where is_internal=1")
        self.blocked("INSERT INTO rate (client_id, role_id, sales_rate) VALUES (?,?,?)", (internal, 1, 10), msg="Internal codes have no rate card")

    def test_the_usual_booking_rules_still_apply_to_internal_codes(self):
        self.blocked(NEW_ENTRY, (4, 7, 8, 4, None, "2026-10-28", 3, 4), msg="Weekly period is not open")  # closed week
        self.con.execute("UPDATE scope SET status='Closed' WHERE scope_id=7")
        self.blocked(NEW_ENTRY, (4, 7, 8, 4, None, "2026-11-04", 3, 4), msg="Scope is not Open")

    def test_approved_internal_hours_are_not_in_the_client_ctr_or_period_report(self):
        ctr_before = self.scalar("select sum(hours) from v_ctr")
        period_before = self.scalar("select sum(hours) from v_period_report")
        eid = self.book().fetchone()[0]
        # approved by the person's line manager (Alisdair Woodhead, 7), who is not the owner
        self.con.execute("UPDATE time_entry SET status='Approved', approved_by=7, approved_at='2026-11-05 09:00', "
                         "last_edited_by=7, last_edited_at='2026-11-05 09:00' WHERE entry_id=?", (eid,))
        self.assertEqual(self.scalar("select status from time_entry where entry_id=?", eid), "Approved")
        self.assertEqual(self.scalar("select sum(hours) from v_ctr"), ctr_before)
        self.assertEqual(self.scalar("select sum(hours) from v_period_report"), period_before)
        self.assertEqual(self.scalar("select count(*) from v_ctr where client='Internal'"), 0)
        self.assertEqual(self.scalar("select count(*) from v_period_report where client='Internal'"), 0)



class DemoHistory(unittest.TestCase):
    """python scripts/build.py --history: twelve weeks of approved timesheets before the seed's own weeks."""

    @classmethod
    def setUpClass(cls):
        cls.con = build(history=True)
        cls.plain = build()

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls.plain.close()

    def scalar(self, sql, *args):
        return self.con.execute(sql, args).fetchone()[0]

    def test_it_adds_three_months_of_weeks_and_leaves_the_seed_alone(self):
        self.assertEqual(self.scalar("select min(work_date) from time_entry"), "2026-08-04")
        self.assertEqual(self.scalar("select count(*) from period where week_start < '2026-10-26'"), 12)
        self.assertEqual(self.scalar("select count(*) from period where week_start < '2026-10-26' and status='Closed'"), 12)
        self.assertGreater(self.scalar("select count(*) from time_entry"), 1000)
        seed_part = "select user_id, scope_id, work_date, hours, status from time_entry where work_date >= '2026-10-26' order by 1,2,3,4,5"
        # the seed's own entries are all still there, unchanged; the first of its weeks also gets hours for people the seed has none for
        self.assertLessEqual(set(self.plain.execute(seed_part).fetchall()), set(self.con.execute(seed_part).fetchall()))
        self.assertEqual(self.scalar("select count(*) from time_entry where work_date >= '2026-11-02'"), self.plain.execute("select count(*) from time_entry where work_date >= '2026-11-02'").fetchone()[0])
        self.assertEqual(self.scalar("select status from period where week_start='2026-10-26'"), "Closed")

    def test_every_historical_hour_is_approved_by_the_scopes_approver(self):
        old = "work_date < '2026-10-26'"
        self.assertEqual(self.scalar("select count(*) from time_entry where %s and status not in ('Approved','Edited')" % old), 0)
        bad = self.scalar("select count(*) from time_entry t where %s and t.approved_by is not null and not exists "
                          "(select 1 from v_scope_approver a where a.scope_id=t.scope_id and a.user_id=t.approved_by)" % old)
        self.assertEqual(bad, 0)
        self.assertEqual(self.scalar("select count(*) from time_entry where %s and approved_by = user_id" % old), 0)

    def test_it_spans_every_month_staff_and_contractors_and_has_corrections_in_the_audit_trail(self):
        months = [r[0] for r in self.con.execute("select distinct substr(work_date, 1, 7) from time_entry where work_date < '2026-10-26' order by 1")]
        self.assertEqual(months, ["2026-08", "2026-09", "2026-10"])
        self.assertGreater(self.scalar("select count(*) from time_entry t join app_user p using(user_id) where p.employment_type like 'Contractor%' and work_date < '2026-10-26'"), 100)
        self.assertGreater(self.scalar("select count(*) from time_entry_audit where reason like 'Corrected after a query%'"), 5)
        self.assertEqual(self.scalar("select count(*) from time_entry where work_date = '2026-08-03'"), 0)            # the bank holiday

    def test_offshore_nights_come_from_the_rotation_and_only_staff_earn_them(self):
        self.assertGreater(self.scalar("select count(*) from v_offshore_night where work_date < '2026-10-26'"), 50)
        self.assertEqual(self.scalar("select count(*) from v_offshore_night n join app_user p using(user_id) where p.employment_type<>'Staff'"), 0)

    def test_it_is_the_same_every_time(self):
        other = build(history=True)
        try:
            q = "select user_id, scope_id, work_date, hours, status from time_entry order by user_id, work_date, scope_id, entry_id"
            self.assertEqual(other.execute(q).fetchall(), self.con.execute(q).fetchall())
        finally:
            other.close()


if __name__ == "__main__":
    unittest.main()
