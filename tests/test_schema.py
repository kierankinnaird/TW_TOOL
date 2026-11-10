"""Schema tests. Run from the repo root: python -m unittest discover -s tests -v"""
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from build import build  # noqa: E402

NEW_ENTRY = (
    "INSERT INTO time_entry (user_id, scope_id, phase_id, paaf_id, worked_role_id, rate_id, work_date, hours, created_by) "
    "VALUES (?,?,?,?,?,?,?,?,?)"
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
        self.assertEqual(self.scalar("select count(*) from app_user"), 30)
        self.assertEqual(self.scalar("select count(*) from time_entry"), 52)
        self.assertEqual(self.scalar("select count(*) from rate"), 60)

    def test_rates_rise_with_seniority_for_each_client(self):
        order = ["Cost Engineer", "Planner", "Project Engineer", "Project Controls Manager",
                 "Engineering Manager", "Project Manager"]
        for client in ("Apache", "Spirit"):
            rows = dict(self.con.execute(
                "select r.name, rt.sales_rate from rate rt join role r using(role_id) "
                "join client c using(client_id) where c.name=?", (client,)))
            self.assertEqual([rows[n] for n in order], sorted(rows[n] for n in order), client)

    def test_job_families_and_titles_from_the_estimate(self):
        self.assertEqual(self.scalar("select count(*) from role"), 32)
        families = {r[0] for r in self.con.execute("select family from role")}
        for f in ["Project Management", "Engineering Management", "Project Engineering", "Piping Engineering", "Technical Safety", "HSES", "QA/QC"]:
            self.assertIn(f, families)
        self.assertEqual(self.scalar("select count(*) from role where family is null or family=''"), 0)

    def test_every_user_has_a_job_title_and_every_title_is_priced_for_both_clients(self):
        self.assertEqual(self.scalar("select count(*) from app_user where role_id not in (select role_id from role)"), 0)
        self.assertEqual(self.scalar("select count(*) from role r where (select count(*) from rate where role_id=r.role_id) not in (0, 2)"), 0)
        # the only titles without a rate card never bill a client
        self.assertEqual({r[0] for r in self.con.execute("select name from role r where not exists (select 1 from rate where role_id=r.role_id)")},
                         {"System Administrator", "Commercial Lead"})

    def test_spirit_rates_match_the_morecambe_hub_estimate(self):
        rows = dict(self.con.execute("select r.name, rt.sales_rate from rate rt join role r using(role_id) join client c using(client_id) where c.name='Spirit'"))
        self.assertEqual((rows["Project Manager"], rows["Engineering Manager"], rows["Lead Technical Safety Engineer"], rows["Document Controller"]), (120, 110, 120, 65))

    def test_no_abbreviated_job_titles(self):
        names = [r[0] for r in self.con.execute("select name from role")]
        self.assertIn("Planner", names)
        self.assertFalse([n for n in names if "Snr" in n])


class TestBookingRules(Base):
    # Seed ids: Ewan Shaw=5 (paaf 1, Apache), David Hart=4 (paaf 4 'Sent'), Emma Noble=1 (paaf 3 Apache, 5 Spirit)
    def test_valid_booking_is_accepted_and_audited(self):
        self.con.execute(NEW_ENTRY, (5, 1, 1, 1, 4, 4, "2026-11-06", 8, 5))
        self.assertEqual(self.scalar("select count(*) from time_entry_audit where action='CREATE'"), 53)

    def test_unapproved_paaf_blocks_booking(self):
        self.blocked(NEW_ENTRY, (4, 1, 1, 4, 4, 4, "2026-11-06", 8, 4), "No approved PAAF")

    def test_paaf_is_per_client_not_per_scope(self):
        # Ewan's single Apache PAAF covers every Apache scope...
        self.con.execute(NEW_ENTRY, (5, 2, 3, 1, 4, 4, "2026-11-06", 4, 5))
        # ...but not Spirit.
        self.blocked(NEW_ENTRY, (5, 5, 6, 1, 4, 10, "2026-11-06", 4, 5))

    def test_closed_scope_blocks_booking(self):
        self.blocked(NEW_ENTRY, (1, 3, 4, 3, 6, 6, "2026-11-06", 8, 1), "Scope is not Open")

    def test_pipeline_scope_blocks_booking(self):
        self.blocked(NEW_ENTRY, (1, 4, 5, 5, 6, 12, "2026-11-06", 8, 1), "Scope is not Open")

    def test_rate_must_match_client_and_worked_role(self):
        self.blocked(NEW_ENTRY, (5, 1, 1, 1, 5, 4, "2026-11-06", 8, 5), "Rate does not match")  # LP role, SP rate
        self.blocked(NEW_ENTRY, (5, 1, 1, 1, 4, 10, "2026-11-06", 8, 5), "Rate does not match")  # Spirit rate on Apache

    def test_cover_booking_uses_higher_role_rate(self):
        self.con.execute(NEW_ENTRY, (5, 1, 1, 1, 5, 5, "2026-11-06", 8, 5))  # Planner covering Project Controls Manager
        self.assertEqual(self.scalar("select sales_rate from rate where rate_id=5"), 100)

    def test_hours_limits(self):
        self.blocked(NEW_ENTRY, (5, 1, 1, 1, 4, 4, "2026-11-06", 0, 5))
        self.blocked(NEW_ENTRY, (5, 1, 1, 1, 4, 4, "2026-11-06", 25, 5))

    def test_new_entries_cannot_start_approved(self):
        self.blocked(
            "INSERT INTO time_entry (user_id, scope_id, paaf_id, worked_role_id, rate_id, work_date, hours, status, created_by) "
            "VALUES (5,1,1,4,4,'2026-11-06',8,'Approved',5)", msg="Draft or Submitted")


class TestHierarchy(Base):
    """client > project > scope > phase. Hours are booked to a phase."""
    def test_every_scope_has_a_phase_and_a_project_of_the_same_client(self):
        self.assertEqual(self.scalar("select count(*) from scope where scope_id not in (select scope_id from phase)"), 0)
        self.assertEqual(self.scalar("select count(*) from scope s join project p using(project_id) where p.client_id<>s.client_id"), 0)

    def test_phase_must_belong_to_the_scope(self):
        self.blocked(NEW_ENTRY, (5, 1, 3, 1, 4, 4, "2026-11-06", 8, 5), "Phase does not belong")   # phase 3 is BB0002's

    def test_booking_to_a_second_phase_of_a_scope(self):
        self.con.execute(NEW_ENTRY, (5, 1, 2, 1, 4, 4, "2026-11-06", 8, 5))                       # BB0001 Execute

    def test_closed_phase_blocks_booking(self):
        self.con.execute("UPDATE phase SET status='Closed' WHERE phase_id=2")
        self.blocked(NEW_ENTRY, (5, 1, 2, 1, 4, 4, "2026-11-06", 8, 5), "Phase is not Open")

    def test_closed_project_blocks_booking(self):
        self.con.execute("UPDATE project SET status='Closed' WHERE project_id=1")
        self.blocked(NEW_ENTRY, (5, 1, 1, 1, 4, 4, "2026-11-06", 8, 5), "Project is not Open")

    def test_scope_client_must_match_project_client(self):
        self.blocked("INSERT INTO scope (scope_code, client_id, project_id, description, status, approver_user_id) "
                     "VALUES ('X1', 2, 1, 'Wrong client', 'Open', 7)", msg="must match its project client")

    def test_moving_an_entry_to_another_scopes_phase_is_blocked(self):
        eid = self.scalar("select entry_id from time_entry where status='Submitted' and scope_id=1 limit 1")
        self.blocked("UPDATE time_entry SET phase_id=3, last_edited_by=5, last_edited_at='t1' WHERE entry_id=?", (eid,), "Phase does not belong")

    def test_ctr_view_has_one_line_per_phase_and_skill_code(self):
        rows = self.con.execute("select phase, skill_code, hours from v_ctr where scope_code='BB0001' and skill_code='PE-01'").fetchall()
        self.assertEqual([(r[0], r[1], r[2]) for r in rows], [("Execute", "PE-01", 8), ("Define", "PE-01", 8)] if rows[0][0] == "Execute" else [("Define", "PE-01", 8), ("Execute", "PE-01", 8)])
        self.assertEqual(self.scalar("select sum(total) from v_ctr"), self.scalar("select sum(total) from v_period_report"))


class TestPeriods(Base):
    def test_closed_week_blocks_new_entries(self):
        self.blocked(NEW_ENTRY, (5, 1, 1, 1, 4, 4, "2026-10-28", 8, 5), "period is not open")

    def test_missing_week_blocks_new_entries(self):
        self.blocked(NEW_ENTRY, (5, 1, 1, 1, 4, 4, "2026-12-01", 8, 5), "period is not open")

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

    def test_only_the_named_scope_approver_can_approve(self):
        eid = self.first("status='Submitted' and scope_id=1")  # scope 1 (Apache) approver = Alisdair Woodhead (7)
        sql = "UPDATE time_entry SET status='Approved', approved_by=?, last_edited_by=?, last_edited_at='t1' WHERE entry_id=?"
        self.blocked(sql, (5, 5, eid), "Only the scope approver")   # Ewan Shaw: not an approver
        self.blocked(sql, (8, 8, eid), "Only the scope approver")   # Kenneth Nicolson approves the Spirit scopes, not this one
        self.blocked(sql, (29, 29, eid), "Only the scope approver")  # the cost controller never approves
        self.blocked(sql, (30, 30, eid), "Only the scope approver")  # nor does the IT admin
        self.con.execute(sql, (7, 7, eid))                          # Alisdair: the named approver

    def test_nobody_approves_their_own_entry(self):
        eid = self.scalar("select entry_id from time_entry where user_id=5 and status='Submitted' limit 1")
        self.blocked("UPDATE time_entry SET status='Approved', approved_by=5, last_edited_by=5, last_edited_at='t1' "
                     "WHERE entry_id=?", (eid,), "not their own")

    def test_only_draft_entries_can_be_deleted(self):
        self.blocked("DELETE FROM time_entry WHERE status='Approved'", msg="Only Draft entries")
        self.blocked("DELETE FROM time_entry WHERE status='Submitted'", msg="Only Draft entries")
        self.blocked("DELETE FROM time_entry", msg="Only Draft entries")

    def test_draft_delete_must_name_the_user_and_is_audited(self):
        self.con.execute("INSERT INTO time_entry (user_id, scope_id, phase_id, paaf_id, worked_role_id, rate_id, work_date, hours, "
                         "status, created_by) VALUES (5,1,1,1,4,4,'2026-11-06',8,'Draft',5)")  # new Draft, no editor yet
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
        self.assertEqual(self.scalar("select count(*) from time_entry"), 51)

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
        self.assertEqual(levels, {"user": 26, "controller": 3, "admin": 1})

    def test_cost_permission_does_not_depend_on_job_title(self):
        # the two cost engineers are controllers, and so is the commercial lead, who is not a cost engineer
        controllers = {r[0]: r[1] for r in self.con.execute("select u.name, r.name from app_user u join role r using(role_id) where permission='controller'")}
        self.assertEqual(controllers, {"Emma Noble": "Cost Engineer", "Abbi Murray": "Cost Engineer", "Stephanie Apedaile": "Commercial Lead"})
        # and anyone can be given it, whatever their title (as long as they approve nothing)
        self.con.execute("UPDATE app_user SET permission='controller' WHERE name='Daniel Evans'")
        self.assertEqual(self.scalar("select permission from app_user where name='Daniel Evans'"), "controller")
        self.blocked("UPDATE app_user SET permission='superuser' WHERE user_id=1")

    def test_no_controller_or_admin_is_an_approver_in_the_demo_data(self):
        self.assertEqual(self.scalar("select count(*) from scope s join app_user u on u.user_id=s.approver_user_id where u.permission<>'user'"), 0)

    def test_only_a_standard_user_can_be_named_as_approver(self):
        new = ("INSERT INTO scope (scope_code, client_id, project_id, description, status, approver_user_id) "
               "VALUES ('Z1', 1, 1, 'Test', 'Open', ?)")
        self.blocked(new, (29,), "standard user")   # the cost controller
        self.blocked(new, (30,), "standard user")   # the IT admin
        self.con.execute(new, (6,))                 # a standard user is fine
        self.blocked("UPDATE scope SET approver_user_id=29 WHERE scope_code='BB0001'", msg="standard user")

    def test_the_approvers_can_book_to_the_client_they_approve(self):
        # Alisdair Woodhead (7, Apache) and Kenneth Nicolson (8, Spirit) each hold an approved PAAF for their client, and Head of Projects is priced
        for user, client in ((7, "Apache"), (8, "Spirit")):
            self.assertEqual(self.scalar("select count(*) from paaf p join client c using(client_id) where p.user_id=? and c.name=? and p.status='Approved'", user, client), 1)
        self.assertEqual(self.scalar("select count(*) from rate r join role ro using(role_id) where ro.name='Head of Projects'"), 2)

    def test_a_cost_controller_approves_hours_booked_by_the_scope_approver_and_nothing_else(self):
        # Kenneth (8) approves A1500 (scope 5, phase 6) and books to it with his Spirit PAAF 36 and the Head of Projects rate 60
        eid = self.con.execute(NEW_ENTRY, (8, 5, 6, 36, 30, 60, "2026-11-04", 4, 8)).lastrowid
        sql = "UPDATE time_entry SET status='Approved', approved_by=?, last_edited_by=?, last_edited_at='t1' WHERE entry_id=?"
        self.blocked(sql, (8, 8, eid), "not their own entry")        # never their own
        self.blocked(sql, (7, 7, eid), "Only the scope approver")    # another approver
        self.blocked(sql, (30, 30, eid), "Only the scope approver")  # the admin never approves
        self.con.execute(sql, (29, 29, eid))                         # a cost controller does
        self.assertEqual(self.scalar("select approved_by from time_entry where entry_id=?", eid), 29)
        # ...but a controller still cannot approve anyone else's hours (Ewan Shaw's on scope 1)
        other = self.scalar("select entry_id from time_entry where status='Submitted' and scope_id=1 limit 1")
        self.blocked(sql, (29, 29, other), "Only the scope approver")

    def test_an_approver_cannot_be_made_a_controller_or_admin_while_still_named(self):
        self.blocked("UPDATE app_user SET permission='controller' WHERE user_id=8", msg="Remove this person as scope approver")
        self.blocked("UPDATE app_user SET permission='admin' WHERE user_id=7", msg="Remove this person as scope approver")
        self.con.execute("UPDATE app_user SET permission='controller' WHERE user_id=5")  # Ewan Shaw approves nothing, so this is fine


class TestActiveDatesAndPaafExpiry(Base):
    """Only live people can book (start and end dates), and a PAAF stops covering hours after its expiry date."""
    # Ewan Shaw (5) started on 1 Mar 2026 and has no end date. His Apache PAAF is 1 (rate 4, Planner). BB0001 is scope 1, phase 1.
    def book(self, date, user=5, scope=1, phase=1, paaf=1, role=4, rate=4):
        return self.con.execute(NEW_ENTRY, (user, scope, phase, paaf, role, rate, date, 2, user))

    def test_nobody_can_book_before_their_start_date(self):
        self.blocked(NEW_ENTRY, (5, 1, 1, 1, 4, 4, "2026-02-27", 2, 5), msg="not active on that date")   # the Friday before 1 March
        self.book("2026-11-06")                                                                              # long after: fine

    def test_nobody_can_book_after_their_end_date_but_the_last_day_is_fine(self):
        self.con.execute("UPDATE app_user SET end_date='2026-11-06' WHERE user_id=5")
        self.book("2026-11-06")                                       # the last day
        self.blocked(NEW_ENTRY, (5, 1, 1, 1, 4, 4, "2026-11-07", 2, 5), msg="not active on that date")

    def test_internal_hours_follow_the_same_dates(self):
        self.con.execute("UPDATE app_user SET end_date='2026-11-04' WHERE user_id=5")
        self.blocked(NEW_ENTRY, (5, 7, 8, None, 4, None, "2026-11-05", 2, 5), msg="not active on that date")
        self.con.execute(NEW_ENTRY, (5, 7, 8, None, 4, None, "2026-11-04", 2, 5))

    def test_a_person_with_no_end_date_stays_live(self):
        self.assertIsNone(self.scalar("select end_date from app_user where user_id=5"))
        self.con.execute("INSERT INTO period (week_start) VALUES ('2026-12-14')")  # a far later week, opened
        self.book("2026-12-18")                                                    # still allowed: no end date

    def test_a_paaf_stops_covering_hours_after_it_expires(self):
        self.con.execute("UPDATE paaf SET expiry_date='2026-11-05' WHERE paaf_id=1")
        self.book("2026-11-05")                                                                              # the expiry day is covered
        self.blocked(NEW_ENTRY, (5, 1, 1, 1, 4, 4, "2026-11-06", 2, 5), msg="PAAF has expired")
        # internal hours never needed a PAAF, so an expired one changes nothing
        self.con.execute(NEW_ENTRY, (5, 7, 8, None, 4, None, "2026-11-06", 2, 5))

    def test_a_paaf_with_no_expiry_date_is_open_ended(self):
        self.assertIsNone(self.scalar("select expiry_date from paaf where paaf_id=1"))
        self.book("2026-11-06")

    def test_the_seed_data_respects_the_rules(self):
        # every booking in the seed is on or after the person's start date, on or before their end date, and within their PAAF
        bad = self.scalar("select count(*) from time_entry t join app_user u on u.user_id=t.user_id where t.work_date < u.start_date "
                          "or (u.end_date is not null and t.work_date > u.end_date)")
        self.assertEqual(bad, 0)
        bad = self.scalar("select count(*) from time_entry t join paaf p on p.paaf_id=t.paaf_id where p.expiry_date is not null and t.work_date > p.expiry_date")
        self.assertEqual(bad, 0)


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
        # Stephanie (29) books INT01 (no PAAF needed) and cannot reopen it herself
        mine = self.con.execute(NEW_ENTRY, (29, 7, 8, None, 32, None, "2026-11-04", 2, 29)).lastrowid  # role 32 = Commercial Lead
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
    """Every entry has a location (On site / Offshore / Standard) and an overtime flag. Overtime has no separate rate yet."""
    def test_the_locations_are_seeded_and_new_entries_default_to_on_site_normal_time(self):
        self.assertEqual([tuple(r) for r in self.con.execute("select location_id, name, active from location order by location_id")],
                         [(1, "On site", 1), (2, "Offshore", 1), (3, "Standard", 1)])
        eid = self.con.execute(NEW_ENTRY, (5, 1, 1, 1, 4, 4, "2026-11-06", 8, 5)).lastrowid
        self.assertEqual(tuple(self.con.execute("select location_id, overtime from time_entry where entry_id=?", (eid,)).fetchone()), (1, 0))

    def test_the_seed_has_an_offshore_day_and_an_overtime_day(self):
        self.assertEqual(self.scalar("select count(*) from time_entry where location_id=2"), 1)
        self.assertEqual(self.scalar("select count(*) from time_entry where overtime=1"), 1)

    def test_location_and_overtime_must_be_valid(self):
        sql = ("INSERT INTO time_entry (user_id, scope_id, phase_id, paaf_id, worked_role_id, rate_id, work_date, hours, created_by, location_id, overtime) "
               "VALUES (5,1,1,1,4,4,'2026-11-06',2,5,?,?)")
        self.blocked(sql, (99, 0))   # no such location
        self.blocked(sql, (1, 2))    # overtime is 0 or 1
        self.con.execute(sql, (2, 1))  # offshore overtime is fine

    def test_one_person_can_book_the_same_code_and_day_as_normal_time_and_as_overtime(self):
        self.con.execute(NEW_ENTRY, (5, 1, 1, 1, 4, 4, "2026-11-06", 8, 5))
        self.con.execute("INSERT INTO time_entry (user_id, scope_id, phase_id, paaf_id, worked_role_id, rate_id, work_date, hours, created_by, overtime) "
                         "VALUES (5,1,1,1,4,4,'2026-11-06',2,5,1)")
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
    """Internal hours: paid by NMC, booked to Internal codes. No PAAF, no rate, kept out of the client reports."""
    # David Hart (user 4) has no approved PAAF for any client. INT01 Business Services is scope 7, phase 8.
    def book(self, paaf=None, rate=None, scope=7, phase=8, date="2026-11-04"):
        return self.con.execute(NEW_ENTRY, (4, scope, phase, paaf, 4, rate, date, 3, 4))

    def test_the_internal_client_and_its_codes_are_seeded_without_rates(self):
        self.assertEqual(self.scalar("select count(*) from client where is_internal=1"), 1)
        self.assertEqual(self.scalar("select count(*) from scope s join client c using(client_id) where c.is_internal=1"), 3)
        self.assertEqual(self.scalar("select count(*) from rate r join client c using(client_id) where c.is_internal=1"), 0)

    def test_anyone_can_book_internal_hours_with_no_paaf_and_no_rate(self):
        self.book()
        row = self.con.execute("select paaf_id, rate_id, status from time_entry where user_id=4").fetchone()
        self.assertEqual(tuple(row), (None, None, "Submitted"))  # the column default; the app saves drafts

    def test_internal_hours_cannot_carry_a_paaf_or_a_rate(self):
        self.blocked(NEW_ENTRY, (5, 7, 8, 1, 4, None, "2026-11-04", 3, 5), msg="Internal hours have no PAAF and no rate")
        self.blocked(NEW_ENTRY, (5, 7, 8, None, 4, 4, "2026-11-04", 3, 5), msg="Internal hours have no PAAF and no rate")

    def test_client_hours_still_need_a_paaf_and_a_rate(self):
        self.blocked(NEW_ENTRY, (5, 1, 1, None, 4, 4, "2026-11-06", 3, 5), msg="No approved PAAF")
        self.blocked(NEW_ENTRY, (5, 1, 1, 1, 4, None, "2026-11-06", 3, 5), msg="Rate does not match")

    def test_internal_codes_get_no_rate_card_and_no_paaf(self):
        internal = self.scalar("select client_id from client where is_internal=1")
        self.blocked("INSERT INTO rate (client_id, role_id, sales_rate) VALUES (?,?,?)", (internal, 1, 10), msg="Internal codes have no rate card")
        self.blocked("INSERT INTO paaf (user_id, client_id, rate_id) VALUES (?,?,?)", (4, internal, 1), msg="Internal codes do not need a PAAF")

    def test_the_usual_booking_rules_still_apply_to_internal_codes(self):
        self.blocked(NEW_ENTRY, (4, 7, 8, None, 4, None, "2026-10-28", 3, 4), msg="Weekly period is not open")  # closed week
        self.con.execute("UPDATE scope SET status='Closed' WHERE scope_id=7")
        self.blocked(NEW_ENTRY, (4, 7, 8, None, 4, None, "2026-11-04", 3, 4), msg="Scope is not Open")

    def test_approved_internal_hours_are_not_in_the_client_ctr_or_period_report(self):
        ctr_before = self.scalar("select sum(hours) from v_ctr")
        period_before = self.scalar("select sum(hours) from v_period_report")
        eid = self.book().lastrowid
        # approved by the scope approver (7), who is not the owner
        self.con.execute("UPDATE time_entry SET status='Approved', approved_by=7, approved_at='2026-11-05 09:00', "
                         "last_edited_by=7, last_edited_at='2026-11-05 09:00' WHERE entry_id=?", (eid,))
        self.assertEqual(self.scalar("select status from time_entry where entry_id=?", eid), "Approved")
        self.assertEqual(self.scalar("select sum(hours) from v_ctr"), ctr_before)
        self.assertEqual(self.scalar("select sum(hours) from v_period_report"), period_before)
        self.assertEqual(self.scalar("select count(*) from v_ctr where client='Internal'"), 0)
        self.assertEqual(self.scalar("select count(*) from v_period_report where client='Internal'"), 0)


if __name__ == "__main__":
    unittest.main()
