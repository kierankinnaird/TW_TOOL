# NMC Timewriting System: v1 mock database

SQLite mock of the v1 data model for the in-house timesheet system (CMAP replacement). The people and job titles in the demo data are real NMC staff, a selection taken from the staff directory (the rest are left out so they can be used to test onboarding); rates, projects, codes and hours are made up.

## Layout

```
db/
  schema.sql      tables, triggers (business rules), views
  seed.sql        demo data (2 clients plus the Internal client with 3 internal codes, 32 job titles in job families (3 with no rate card), 30 users, 4 projects, 9 scopes, 10 phases, 52 entries over 2 weeks)
app/
  app.py          front end (plain HTML and one inline stylesheet, standard library only, no framework)
  static/         the TASA Orbiter font file, the only static file the app serves
scripts/
  build.py        builds build/timewriting.db from schema + seed
  export_schema_tables.py   writes build/schema_tables.csv (columns as a table)
tests/
  test_schema.py  unittest suite for the rules and views
  test_app.py     front-end smoke tests
docs/             source documents such as the SOR (git-ignored, kept on the local machine only)
build/            generated .db files (git-ignored)
```

## Look and feel

NMC brand colours (orange `#fe6700`, black `#0f0f12`, off-white `#f5f5f6`), the TASA Orbiter font and square corners, all in one stylesheet in `app.py` (the `STYLE` block) with no external requests. The font is used from the machine if it is installed, otherwise the copy in `app/static/` is served; the route serves that one file only, never a path taken from the request. If the font file is missing, the app falls back to the system sans-serif font.

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

The app checks the database's schema version when it starts (`PRAGMA user_version` in `db/schema.sql`, `REQUIRED_SCHEMA` in `app/app.py`; raise both together when a schema change would break older databases). If it says the database was built with an older schema, run `python scripts/build.py` to make a fresh `build/timewriting.db`. That leaves any other database file alone, so a database you pointed `TW_DB` at keeps its data; clear `TW_DB` (PowerShell: `Remove-Item Env:TW_DB`) to use the new one.

Pages: My Timesheet (weekly grid), Approvals (approvers only), Reports (CTR and weekly period reports), Admin and Audit trail (cost controllers and the IT admin, each seeing their own tabs). Rebuilding the database resets the data.

## Roles

Each person has one permission level: `user`, `controller` (cost controller) or `admin` (IT). Everyone can enter, save and submit their own time. Approver is not a level: it means being named as the approver on a scope, and only standard users can be named. Cost controllers and admins never approve hours.

| | Standard user | Approver (a user named on a scope) | Cost controller | Admin (IT) |
|---|---|---|---|---|
| Own timesheet | Yes | Yes | Yes | Yes |
| Approvals page, approve or reject | No | Yes, for their scopes only, never their own hours | No | No |
| Reports | Own hours only, no values | Own hours plus everyone's hours on their scopes, no values | Everything, with £ values and sales rates | No |
| £ values and sales rates | No | No | Yes | No |
| Admin tabs | None | None | Weekly periods, clients, rate cards, PAAFs, projects / scopes / phases, internal codes | Weekly periods, users, job roles |
| Audit trail | No | No | Yes | Yes |

In the demo data, Alisdair Woodhead approves for Apache and Kenneth Nicolson for Spirit (both standard users). The cost controllers are the two cost engineers, Emma Noble and Abbi Murray, and Stephanie Apedaile (commercial lead), who is not a cost engineer: cost permission is set per person on the Users tab, never taken from a job title, so anyone can be given it when the system goes live. NMC Admin is the IT account. Sales rates and £ values are never put in the page for anyone but a cost controller; the reports, their filters and the CTR page only show other people what they may see. Still to come from the roles matrix: a missing-timesheets view, reopening a timesheet, transaction controls, and more. Who owns weekly periods and the audit trail is still to be confirmed with the cost team, so for now both controllers and admins can use them.

Admin tabs: weekly periods (close a week, or open a missed one), users, roles / skill codes, clients, client rate cards, PAAFs (raise, mark sent, approve with the signed form reference, reject), projects, scopes (each needs an approver) and phases, and internal codes. Users, roles, scopes and internal codes can be edited; rates are never edited, you add a new one with a later effective date.

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
- Approval is only by the person named as the scope's approver, never the entry owner. Cost controllers and admins never approve, and cannot be named as an approver.
- Every edit names its editor; every create, edit, status change and deletion is audited. Only Draft entries can be deleted (clear draft).

## CTR page

On the Reports tree, each Phase row has an "Open CTR" link to `/ctr?phase=<id>`. The CTR page shows the phase header (client, project, scope, phase, approver) and tabs like the CTR workbook: a Summary tab (one line per job family, Actual booked hrs / Total / Pending hrs) and one tab per job family with its resources (job title, skill code, sales rate), people and timesheet entries. Only Approved hours count towards Actual booked hrs and Total.
