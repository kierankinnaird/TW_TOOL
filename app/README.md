# NMC Timewriting System: v1 mock database

SQLite mock of the v1 data model for the in-house timesheet system (CMAP replacement). The people and job titles in the demo data are real NMC staff, a selection taken from the staff directory (the rest are left out so they can be used to test onboarding); rates, projects, codes and hours are made up.

## Layout

```
db/
  schema.sql      tables, triggers (business rules), views
  seed.sql        demo data (3 locations, 2 clients plus the Internal client with 3 internal codes, 33 job titles in job families (4 with no rate card), 31 users, 4 projects, 9 scopes, 10 CTRs, 64 entries over 2 weeks, including two offshore weeks with allowances)
app/
  app.py          the Flask app: routes, security headers, the server and the startup checks. The pages themselves are in the nmc package:
  nmc/core.py       shared helpers, the approval rules, the page layout and the one inline stylesheet (STYLE), and form helpers
  nmc/timesheet.py  the weekly timesheet, saving, booking rules and copy last week
  nmc/approvals.py  approvals, missing timesheets and reopening
  nmc/reports.py    reports, the CTR page, grid, manhour report and the Excel downloads
  nmc/dashboard.py  the cost controllers' dashboard and its SVG charts
  nmc/admin.py      the Admin tabs and the audit trail
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

The web layer is Flask. `python app/app.py` runs Werkzeug's development server, which is fine for the mock; a real deployment would run `flask_app` under a production WSGI server (for example waitress or gunicorn). Every page is a function that takes the database connection and returns HTML; the routes in `app.py` only read the request and call it.

## Look and feel

NMC brand colours (orange `#fe6700`, black `#0f0f12`, off-white `#f5f5f6`), the TASA Orbiter font and square corners, all in one stylesheet in `nmc/core.py` (the `STYLE` block) with no external requests. The font is used from the machine if it is installed, otherwise the copy in `app/static/` is served; the route serves that one file only, never a path taken from the request. If the font file is missing, the app falls back to the system sans-serif font.

## Use

```
python scripts/build.py                      # creates build/timewriting.db
python -m unittest discover -s tests -v      # runs the tests
```

Front end (no login: pick who you are in the "Signed in as" box):

```
python scripts/build.py
pip install -r requirements.txt           # once: Flask
python app/app.py                            # then open http://localhost:8000
```

The app checks the database's schema version when it starts (`PRAGMA user_version` in `db/schema.sql`, `REQUIRED_SCHEMA` in `app/nmc/core.py`; raise both together when a schema change would break older databases). If it says the database was built with an older schema, run `python scripts/build.py` to make a fresh `build/timewriting.db`. That leaves any other database file alone, so a database you pointed `TW_DB` at keeps its data; clear `TW_DB` (PowerShell: `Remove-Item Env:TW_DB`) to use the new one.

Pages: My Timesheet (weekly grid), Approvals (approvers and cost controllers), Missing timesheets (approvers and cost controllers), Reports (CTR and weekly period reports), Admin and Audit trail (cost controllers and the IT admin, each seeing their own tabs). Rebuilding the database resets the data.

## Roles

Each person has one permission level: `user`, `controller` (cost controller) or `admin` (IT). Everyone can enter, save and submit their own time. Approver is not a level: it means being a client's main approver or a secondary approver on a scope, and only standard users can be named. Approvers book their own time like anyone else, including to the scopes they approve (they hold a PAAF for that client); nobody approves their own hours, so a cost controller approves the hours an approver books to their own scope. Apart from that, cost controllers and admins never approve hours.

| | Standard user | Approver (a user named on a scope) | Cost controller | Admin (IT) |
|---|---|---|---|---|
| Own timesheet | Yes | Yes | Yes | Yes |
| Approvals page, approve or reject | No | Yes, for their scopes only, never their own hours | Only hours an approver booked to their own scope | No |
| Missing timesheets (who has not submitted, by week) | No | Their own people: those on the personnel list of a client they approve, plus anyone who booked to their internal codes | Everyone active that week | No |
| Reports | Own hours only, no values | Own hours plus everyone's hours on their scopes, no values | Everything, with £ values and sales rates | No |
| £ values and sales rates | No | No | Yes | No |
| Admin tabs | None | None | Weekly periods, clients, rate cards, PAAFs, projects / scopes / CTRs, internal codes, allowance rates, budgets | Weekly periods, users, job roles, locations |
| Audit trail | No | No | Yes | Yes |

In the demo data, Alisdair Woodhead approves for Apache and Kenneth Nicolson for Spirit (both standard users), and Graeme Hogg, the managing director, approves the Internal (overhead) codes. Cost engineers book to projects like anyone else, so their hours get the standard approval from the scope's approver. Each user has an optional **line manager** (Admin, Users); it is recorded only for now, with no workflow attached. The cost controllers are the two cost engineers, Emma Noble and Abbi Murray, and Stephanie Apedaile (commercial lead), who is not a cost engineer: cost permission is set per person on the Users tab, never taken from a job title, so anyone can be given it when the system goes live. NMC Admin is the IT account. Sales rates and £ values are never put in the page for anyone but a cost controller; the reports, their filters and the CTR page only show other people what they may see. Still to come from the roles matrix: reopening a timesheet, transaction controls, and more. Who owns weekly periods and the audit trail is still to be confirmed with the cost team, so for now both controllers and admins can use them.

Admin tabs: weekly periods (close a week, or open a missed one), users, roles / skill codes, clients, client rate cards, the client personnel lists (PAAFs: add a person, change their rate, set an end date), projects, scopes (secondary approvers are named on the scope) and CTRs, internal codes, and locations (the hour types). Users, roles, scopes, internal codes and locations can be edited; rates are never edited, you add a new one with a later effective date.

Weekly periods open themselves: the first request in a new week creates that week's period, so nobody has to open it by hand. A week an admin has closed stays closed. The admin tab can still open a week that was missed, for example after the app was not used for a while.

On the timesheet, the row, day and week totals update as hours are typed, before saving. Under the grid there are three lines: Normal time, Overtime and Total.

**Sent back:** when an approver (or, for an approver's own hours, a cost controller) rejects an entry, it goes back to the person as a draft with the reason. The person sees it three ways: a "Sent back for correction" notice on that week's timesheet (the entry, the reason, who sent it back and when), a hover note on the box itself, and "My Timesheet (n sent back)" in the menu on every page, plus a pointer to any other weeks that have some. The notice disappears when the week is submitted again. Reasons are shown as plain text. No email is sent: users have no email address stored.

**Manhour report by client:** on Reports, a client's project manager (its main approver, or anyone who approves one of its scopes) and the cost controllers can open the Manhour report by client and download it as Excel. It shows one client's approved hours broken down by job family, role and the people who worked in that role, one column per week commencing, with subtotals for roles that have several people, for each job family and for the client, plus a Pending hrs column. It is hours only, never money, and a project manager only ever gets their own client's people (a cost controller can pick any client). People who approve nothing, and the IT admin, do not get it.

**Adding rows and overtime (CMAP-style):** the **+ Add timesheet row** button above the grid opens a popup with a CTR list you can search by typing, an Add row button and a Cancel; a different skill code or location is in a collapsed section inside it. Overtime is no longer a tick box: every booking shows its normal-time row with a **+ Overtime** link, which reveals an overtime row directly under it (an overtime row that already has hours is shown automatically). The data is unchanged: overtime hours are still their own entries flagged as overtime.

**Deleting a row:** each booking row has a small × at its right while the sheet can be edited. It deletes the row and its draft hours (asking first if there are hours, and writing the removals to the audit trail); hours typed on the other rows are saved first. A row with submitted or approved hours has no ×, and the server refuses a hand-made request to delete it.

**Submitted sheets are locked:** once a week has been submitted, the whole sheet is locked: no boxes (empty days included), no new rows, no Save or Submit buttons, and the server refuses any hand-made request to change it. It opens again only when a cost controller reopens it, or when an approver sends entries back (those entries return to Draft).

**Timesheet conveniences:** the top of the timesheet shows the week's status (not started, draft, partly submitted, submitted, approved), a This week link and a date picker. Add row saves the hours you have typed before adding the new row, so nothing is lost. If a client PAAF is not approved yet, a line says so and why that client's codes are not offered. Submit week with nothing to submit says so. A person cannot book more than 24 hours in one day across all rows (enforced by the database). Reports default to the current year. The IT admin's system account is not listed as a missing timesheet. The rate card has a client filter and marks each rate Current, Superseded or Future. The audit trail can be filtered to one person's hours. Approvals has an Approve all waiting button on each person's line (works without scripting). Reopen timesheet lists the weeks that can be reopened. The IT admin account has no timesheet. Admin, Users has a name / NMC ID search. Admin, Job families / titles lists the families (departments) with their number of titles and people; click a family to see its job titles and who holds each, rename the family, or add a title to it.

**Allowances:** there is one, the **offshore allowance**, claimed per day against a CTR and worth £100 a day. It is for staff only (contractors cannot claim it; the database refuses it). **Allowances are not claimed on the timesheet**: payroll will set them by location, so the timesheet has no allowance rows, tick boxes or claim button, and Submit week submits hours only. What is built behind it is unchanged and waits for that payroll feed (until then the demo data is the only source). The rules are the same as hours: the person needs an approved PAAF (internal codes need none), the CTR must be open and not restricted against them, the week must be open, and the day must be within their start and end dates. An approver of the scope approves or sends them back (a reason is needed) on the same Approvals page, nobody approves their own, and Reopen timesheet sends them back to Draft with the hours. Everything is in the audit trail. The rate is one figure per allowance on Admin, Allowance rate (cost controllers): changing it saves a new rate from the date you give, so earlier claims keep their value. Approved claims appear on each CTR sheet under Additions (cost per unit, days, total) and on the CTR's Allowances tab, and in the Reports Allowances section. The schema can hold more than one allowance; the demo has just this one.

**PAAF list:** a PAAF is the list of people who work on a client's projects, which is what goes to the client. None of our clients approve individuals, so there is no sign-off: a person is on the list, and can book to that client's CTRs, as soon as they are added, until their end date. (The Draft / Sent / Rejected statuses and the signed form reference remain in the database for a client that asks for formal approval later.) Admin, PAAFs lists it in order of its # column. Filter by person, client and whether they are still on the list, and click the #, Name, Client or Ends heading to sort (again to reverse). The filters stay in place after you act.

**Append-only records:** the audit trail cannot be edited or deleted, and a rate is never edited or deleted: a change is a new rate row with a later effective date. Both are enforced by the database, not just the screens.

**Budget against actual (from P6):** P6 holds budget hours only, no money, and CTRs are still created here and keyed into P6 by hand. Cost controllers load the budget hours back from a P6 export on Admin, Budgets: paste lines of `CTR No, budget hours` (a heading line is ignored) with an as-of date. Each load is a snapshot that is never edited or deleted, and the latest is compared with the approved hours booked here. The comparison (budget, actual, pending, remaining, % used) is on each CTR's page and in a Budget against actual hours section at the bottom of Reports, for cost controllers only. A CTR with hours but no budget shows as not budgeted. The cost controllers' **Dashboard** (in the menu) charts it in hours only: tiles for budget, approved, pending, remaining, % used, and CTRs over or near budget, a bar chart of budget against approved hours for every CTR (over-budget CTRs in dark red and marked in words), and approved hours by week stacked by client. The dashboard can be filtered by client, project, scope and CTR (each list only offers what fits the choices before it), by how much of the budget is used (over budget, 90% or more, under 90%), and by a range of weeks for the weekly chart; the budget bars always compare with all approved hours to date. The charts are drawn on the server as plain SVG, so there are no scripts or outside requests, and each bar has a hover tooltip. Loading is by pasting. The box takes either simple `CTR No, hours` lines or a **P6 export**: run this in SSMS with a read-only account (the P6 database is SQL Server; the project numbers are your own), then copy the grid, or save it as CSV, and paste it in.

```sql
SELECT p.proj_short_name AS project, w.wbs_name, t.task_code,
       t.target_work_qty AS budget_hours, t.act_work_qty AS actual_hours,
       t.remain_work_qty AS remaining_hours, t.phys_complete_pct
FROM TASK t
JOIN PROJWBS w ON w.wbs_id = t.wbs_id
JOIN PROJECT p ON p.proj_id = t.proj_id
WHERE t.proj_id IN (/* the P6 project ids to export */) AND t.target_work_qty > 0
ORDER BY p.proj_short_name, w.wbs_name;
```

In NMC's P6 plans each CTR is a WBS element named like `Process Engineering - A0101-PR-D-001`, with one activity under it holding the hours, so the CTR No is read from the end of the WBS name (a row with no CTR No there, such as `Future Scope Estimate`, is listed as not loaded). A file can hold several P6 projects (for example a baseline and a later version of the same plan): you choose which one to load, and see a preview of every row first (matched to a CTR in this system, unchanged, changed, or why it would not be loaded) before anything is written. A CTR No that appears twice in the chosen project is never added up: it is flagged and not loaded. P6's own actual hours and % complete are stored with the budget snapshot (they are not shown on any page yet).

**Real Spirit data (local only):** `python scripts/build.py --extra docs/p6_spirit_pack.sql` builds the demo database and then applies extra SQL, here a git-ignored file made by `docs/make_p6_pack.py` from a P6 export of the live Morecambe plan (`docs/p6_morecambe7.tsv`). It adds Spirit's real scopes (A0101 and A0100), their CTRs and the P6 budgets, so the dashboard shows real figures; the real client data stays off git because `docs/` is ignored. Without `--extra` you get the made-up demo only.

**PAAF rate:** when a person is added to a client's list (Admin, PAAFs) the cost controller picks the person's position from a dropdown of that client's rate card, or leaves it on the default, the position for the person's own role. It can be changed at any time; hours already booked keep the rate they were booked at. When the person books as their own role, that position sets the rate (the rate in force on the work date); covering another role uses that role's rate card line.

**Structure and approvers:** Client > Project > Scope > CTR. A CTR is the booking code hours are booked to (it was called a phase; internally the table is still `phase`). Each client has a **main approver** (Admin, Clients) who approves hours booked to any of its scopes, and each scope can have **secondary approvers** (Admin, Projects / scopes / CTRs, the scope's Edit page). Any of them can approve; nobody approves their own hours. If the booker is a scope's only approver, nobody else could approve, so a cost controller does (once a second approver is named, the controller no longer does). **CTR settings:** a cost controller opens a CTR on Admin, Projects / scopes / CTRs. Besides its name and status each CTR has a description, optional WBS, CBS and ABS references, and transaction controls: either anyone who may book to the client, or only the people ticked, and optionally a Bookable from / until date window. The database enforces the list and the window on new bookings; the timesheet's Add row list leaves out CTRs the person cannot book that week. Hours already booked are not affected.

**Timesheet grid and Excel:** Reports links to the Timesheet grid: hours by person, booking code and day. Choose a ready-made layout (person by code by day, person by day, person by week, code by person total, code by week, location and type by week), tailor the columns, show each day, each week or one total, and narrow by dates, client, scope or person. "Download as Excel" gives a real .xlsx of exactly what is on screen, and the period report has its own download. Cost controllers also get a Value column; everyone else sees only the hours they are allowed to see.

**Approvals:** the Approvals page is a weekly view. It opens on the oldest week that still has hours waiting and shows one line per person with their hours for each weekday, a week total, and the hours waiting and already approved. "detail" (or Show all detail) opens everything booked against that person that week, approved lines included, with a tick box on each entry still waiting; ticking a person ticks their waiting entries. Previous week, Next week, This week and a date picker go to other weeks, so earlier approved time stays visible. Approve selected and Reject selected (a reason is needed to reject) keep the same week on screen.

**Copy last week:** when last week has hours, this week is open and nothing in this week has been submitted or approved yet, the timesheet offers one button, "Copy last week". It replaces whatever is in this week with last week's rows and hours: the draft hours already there are cleared (and audited, and the browser asks first), then last week's hours are saved as drafts on the same weekdays, with the same code, skill code, location and overtime flag. If any hours in the week are already submitted or approved, the week cannot be replaced (a cost controller has to reopen it first). Anything that cannot be copied (for example a PAAF that has expired, or a day outside your start and end dates) is reported, not skipped silently. Copying uses the same booking rules as saving, so rates are looked up for the new date.

## Hour types: location and overtime

Every timesheet row has a **location** (On Shore, Offshore or Site visit by default; the IT admin can add more) and a **type** (Normal or Overtime), so one person can book the same code on the same day as normal time, as overtime, on shore and offshore, each on its own row. "Add row" has a Location dropdown and an Overtime tick box. Every entry stores both (`location_id` and `overtime`); once hours are submitted or approved they are locked, and the audit trail keeps the old values if a cost controller reopens and changes them.

- The list of locations is managed by the IT admin on the Locations tab (add, rename, switch off). A location that is switched off stops new bookings; hours already booked keep it. At least one must stay active.
- **Overtime has no separate rate yet**, so overtime hours are valued at the normal rate in the CTR and weekly report totals. When the cost team has overtime rates they will need a place on the rate card (a second rate or a multiplier).
- Approvers see the location and type on the Approvals page, the CTR page lists them for each entry, and Reports has an "Hours by location and overtime" section (hours only, limited to what the person may see).
- Cell names on the timesheet form are `h_<phase>_<skill code>_<location>_<overtime>_<date>`.

## Internal hours

Hours NMC pays for itself rather than bills to a client are booked to an internal code, shown as `Internal - Business Services`. An internal code is a scope under the Internal client (a client flagged `is_internal`), with a General phase. Internal time is an overhead, so Graeme Hogg (managing director) is the Internal client's main approver; he can delegate by naming secondary approvers on a code. Admin adds, renames, closes and reassigns approvers for codes on the Internal codes tab; each code is numbered for you (INT01, INT02, ...).

- Anyone can book to an open internal code: no PAAF is needed.
- Internal hours have no sales rate (`paaf_id` and `rate_id` are empty on the entry), and the Internal client cannot be given a rate card or PAAFs.
- Everything else is the same as client hours: draft, submit, approve by an approver of the scope, controlled edits, audit trail, open weekly period.
- They are left out of the CTR and the weekly period report, which are about billable value. Reports has a separate Internal hours section for the chosen date range.

Open `build/timewriting.db` in VS Code with a SQLite extension. Useful queries:

```sql
SELECT * FROM v_ctr;                          -- Cost Time Resource per phase and role (approved only)
SELECT * FROM v_approver_queue;               -- waiting on approvers
SELECT * FROM v_period_report
 WHERE week_start BETWEEN '2026-10-26' AND '2026-11-02';
```

## Rules enforced in the database

- Booking is to a phase and needs an Open project, scope and phase, a place on the personnel list (PAAF) of the scope's client, a rate that matches the client and worked role, and an Open weekly period. Internal codes need none of the PAAF and rate, and cannot carry them. The work date must also fall between the person's start and end dates (no end date means still employed; the last day counts), and, for client work, on or before the PAAF's expiry date if it has one. On the timesheet, days outside a person's dates have no box.
- Lifecycle: Draft, Submitted, Approved. Only Draft hours can be typed over: submitted and approved hours show as plain text, and the server refuses a changed or cleared value for them too. Reject returns to Draft with a reason. A cost controller can reopen a submitted or approved week (Reopen timesheet in the menu, a reason is required, audited, never their own timesheet); it goes back to the person as drafts with the reason shown, and has to be submitted and approved again. Hours changed after approval by other means are marked Edited and need re-approval.
- Approval is by the client's main approver or a secondary approver of the scope, never the entry owner. The one exception: hours booked by the scope's only approver are approved by a cost controller. Admins never approve, and neither controllers nor admins can be named as an approver.
- Every edit names its editor; every create, edit, status change and deletion is audited. Only Draft entries can be deleted (clear draft).

## CTR page

**CTR sheet:** the Summary tab of the CTR page is laid out like the CTR sheets in the estimate workbook (for example `EN-D-001`): project name, title and client, date, the client's framework agreement reference, CTR title and CTR number (the scope code, then the CTR's own code such as `PM-D-001`, giving `A1500-PM-D-001`), prepared / checked / approved by, objectives, input requirements, scope of work, what it assumes and excludes, commencement and completion, then the Resource table (cost per hour, hours, total), the Additions (approved allowances at the allowance rates), the total, payment terms (Reimbursable, Budget or Lump Sum, marked with an X) and the basis of proposal. The text fields are edited on the CTR's Admin page (Projects / scopes / CTRs, Edit), the framework reference on the client. Hours and allowances are what was actually booked and approved, not a budget; cost columns are shown to cost controllers only.

On the Reports tree, each Phase row has an "Open CTR" link to `/ctr?phase=<id>`. The CTR page shows the phase header (client, project, scope, phase, approver) and tabs like the CTR workbook: a Summary tab (one line per job family, Actual booked hrs / Total / Pending hrs) and one tab per job family with its resources (job title, skill code, sales rate), people and timesheet entries. Only Approved hours count towards Actual booked hrs and Total.
