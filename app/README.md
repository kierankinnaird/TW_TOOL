# NMC Timewriting System: v1 mock database

SQLite mock of the v1 data model for the in-house timesheet system (CMAP replacement). All data is fake.

## Layout

```
db/
  schema.sql      tables, triggers (business rules), views
  seed.sql        fake data (2 clients, 29 job titles in job families, 28 users, 3 projects, 6 scopes, 7 phases, 52 entries over 2 weeks)
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

Admin has a tab for each of: weekly periods (open and close), users, roles / skill codes, clients, client rate cards, PAAFs (raise, mark sent, approve with the signed form reference, reject) and projects, scopes (each needs an approver) and phases. Users, roles and scopes can be edited; rates are never edited, you add a new one with a later effective date.

Open `build/timewriting.db` in VS Code with a SQLite extension. Useful queries:

```sql
SELECT * FROM v_ctr;                          -- Cost Time Resource per phase and role (approved only)
SELECT * FROM v_approver_queue;               -- waiting on approvers
SELECT * FROM v_period_report
 WHERE week_start BETWEEN '2026-10-26' AND '2026-11-02';
```

## Rules enforced in the database

- Booking is to a phase and needs an Open project, scope and phase, an Approved PAAF for the scope's client, a rate that matches the client and worked role, and an Open weekly period.
- Lifecycle: Draft, Submitted, Approved, Edited. Reject returns to Draft with a reason. Editing an approved entry sets Edited, needs a reason, and needs re-approval.
- Approval is by the scope's approver or an admin, never the entry owner.
- Every edit names its editor; every create, edit, status change and deletion is audited. Only Draft entries can be deleted (clear draft).

## CTR page

On the Reports tree, each Phase row has an "Open CTR" link to `/ctr?phase=<id>`. The CTR page shows the phase header (client, project, scope, phase, approver) and tabs like the CTR workbook: a Summary tab (one line per job family, Actual booked hrs / Total / Pending hrs) and one tab per job family with its resources (job title, skill code, sales rate), people and timesheet entries. Only Approved hours count towards Actual booked hrs and Total.
