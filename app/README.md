# NMC Timewriting System: v1 mock database

SQLite mock of the v1 data model for the in-house timesheet system (CMAP replacement). All data is fake.

## Layout

```
db/
  schema.sql      tables, triggers (business rules), views
  seed.sql        fake data (2 clients plus the Internal client with 3 internal codes, 29 job titles in job families, 28 users, 4 projects, 9 scopes, 10 phases, 52 entries over 2 weeks)
app/
  app.py          basic front end (plain HTML, standard library only)
scripts/
  build.py        builds build/timewriting.db from schema + seed
  export_schema_tables.py   writes build/schema_tables.csv (columns as a table)
tests/
  test_schema.py  unittest suite for the rules and views
  test_app.py     front-end smoke tests
docs/
  open-questions.md   gaps parked for later iterations
build/            generated .db files (git-ignored)
```

## Use

```
python scripts/build.py                      # creates build/timewriting.db
python -m unittest discover -s tests -v      # runs the tests
```

Front end (no login: pick who you are in the "Signed in as" box):

```
python scripts/build.py
python app/app.py                            # then open http://localhost:8000
```

Pages: My Timesheet (weekly grid), Approvals, Reports (CTR and weekly period reports), Admin and Audit trail (admin users only, e.g. Alasdair Murray). Rebuilding the database resets the data.

Admin has a tab for each of: weekly periods (close a week, or open a missed one), users, roles / skill codes, clients, client rate cards, PAAFs (raise, mark sent, approve with the signed form reference, reject), projects, scopes (each needs an approver) and phases, and internal codes. Users, roles, scopes and internal codes can be edited; rates are never edited, you add a new one with a later effective date.

Weekly periods open themselves: the first request in a new week creates that week's period, so nobody has to open it by hand. A week an admin has closed stays closed. The admin tab can still open a week that was missed, for example after the app was not used for a while.

On the timesheet, the row, day and week totals update as hours are typed, before saving.

## Internal hours

Hours NMC pays for itself rather than bills to a client are booked to an internal code, shown as `Internal - Business Services`. An internal code is a scope under the Internal client (a client flagged `is_internal`), with a General phase. Admin adds, renames, closes and reassigns approvers for codes on the Internal codes tab; each code is numbered for you (INT01, INT02, ...).

- Anyone can book to an open internal code: no PAAF is needed.
- Internal hours have no sales rate (`paaf_id` and `rate_id` are empty on the entry), and the Internal client cannot be given a rate card or PAAFs.
- Everything else is the same as client hours: draft, submit, approve by the code's approver, controlled edits, audit trail, open weekly period.
- They are left out of the CTR and the weekly period report, which are about billable value. Reports has a separate Internal hours section for the chosen date range.

Open `build/timewriting.db` in VS Code with a SQLite extension. Useful queries:

```sql
SELECT * FROM v_ctr;                          -- Cost Time Resource per phase and role (approved only)
SELECT * FROM v_approver_queue;               -- waiting on approvers
SELECT * FROM v_period_report
 WHERE week_start BETWEEN '2026-10-26' AND '2026-11-02';
```

## Rules enforced in the database

- Booking is to a phase and needs an Open project, scope and phase, an Approved PAAF for the scope's client, a rate that matches the client and worked role, and an Open weekly period. Internal codes need none of the PAAF and rate, and cannot carry them.
- Lifecycle: Draft, Submitted, Approved, Edited. Reject returns to Draft with a reason. Editing an approved entry sets Edited, needs a reason, and needs re-approval.
- Approval is by the scope's approver or an admin, never the entry owner.
- Every edit names its editor; every create, edit, status change and deletion is audited. Only Draft entries can be deleted (clear draft).

## CTR page

On the Reports tree, each Phase row has an "Open CTR" link to `/ctr?phase=<id>`. The CTR page shows the phase header (client, project, scope, phase, approver) and tabs like the CTR workbook: a Summary tab (one line per job family, Actual booked hrs / Total / Pending hrs) and one tab per job family with its resources (job title, skill code, sales rate), people and timesheet entries. Only Approved hours count towards Actual booked hrs and Total.
