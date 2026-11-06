# NMC Timewriting System: v1 mock database

SQLite mock of the v1 data model for the in-house timesheet system (CMAP replacement). All data is fake.

## Layout

```
db/
  schema.sql      tables, triggers (business rules), views
  seed.sql        fake data (10 users, 2 clients, 6 scopes, 28 entries over 2 weeks)
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

Open `build/timewriting.db` in VS Code with a SQLite extension. Useful queries:

```sql
SELECT * FROM v_ctr;                          -- Cost Time Resource per scope and role (approved only)
SELECT * FROM v_approver_queue;               -- waiting on approvers
SELECT * FROM v_period_report
 WHERE week_start BETWEEN '2026-10-26' AND '2026-11-02';
```

## Rules enforced in the database

- Booking needs an Open scope, an Approved PAAF for the scope's client, a rate that matches the client and worked role, and an Open weekly period.
- Lifecycle: Draft, Submitted, Approved, Edited. Reject returns to Draft with a reason. Editing an approved entry sets Edited, needs a reason, and needs re-approval.
- Approval is by the scope's approver or an admin, never the entry owner.
- Every edit names its editor; every create, edit, status change and deletion is audited. Only Draft entries can be deleted (clear draft).