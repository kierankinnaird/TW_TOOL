# NMC Timewriting System: v1 mock database

PostgreSQL mock of the v1 data model for the in-house timesheet system (CMAP replacement). The people and job titles in the demo data are real NMC staff, a selection taken from the staff directory (the rest are left out so they can be used to test onboarding); rates, projects, codes and hours are made up.

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
  build.py        fills the PostgreSQL database in DATABASE_URL from schema + seed (emptying it first)
  export_schema_tables.py   writes build/schema_tables.csv (columns as a table)
  load_cmap.py    loads the people from CMAP's user grid (build.py --cmap grid.csv --users tenant-export.csv): ids, Staff / Contractor - LTD / Contractor - PAYE, contracted hours, dates, line managers, roles; anyone not in the grid is left out
  import_users.py, load_users.py   link and load people from the Microsoft 365 user export alone
  seed_history.py demo timesheet history (used by build.py --history)
tests/
  test_schema.py  unittest suite for the rules and views
  test_app.py     front-end smoke tests
docs/             source documents such as the SOR (git-ignored, kept on the local machine only)
build/            generated files (git-ignored)
docker-compose.yml   a local PostgreSQL server (and the app) for development and the tests
```

The web layer is Flask. `python app/app.py` runs Werkzeug's development server, which is fine for the mock; a real deployment would run `flask_app` under a production WSGI server (for example waitress or gunicorn). Every page is a function that takes the database connection and returns HTML; the routes in `app.py` only read the request and call it.

## Look and feel

NMC brand colours (orange `#fe6700`, black `#0f0f12`, off-white `#f5f5f6`), the TASA Orbiter font and square corners, all in one stylesheet in `nmc/core.py` (the `STYLE` block) with no external requests. The font is used from the machine if it is installed, otherwise the copy in `app/static/` is served; the route serves that one file only, never a path taken from the request. If the font file is missing, the app falls back to the system sans-serif font.

## Use

The data lives in PostgreSQL (14 or later). For development, `docker compose up -d db` starts a local server on `localhost:55432` (the default the app and scripts use, so nothing needs setting). To use another server, set `DATABASE_URL` (for example `postgresql://user:password@host:5432/nmc`); Azure's PostgreSQL Flexible Server works as it is. Create the database with the plain "C" collation so names sort by character code, as they always have here: `CREATE DATABASE nmc TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C';` (the compose file does this).

```
python scripts/build.py                      # fills the database in DATABASE_URL with demo data (it empties it first: never run it on real hours)
python -m unittest discover -s tests -v      # runs the tests (each gets its own throwaway database on the same server)
```

The tests make and drop their own databases, so the server needs a user that may create databases; set `TEST_DATABASE_URL` (the maintenance database, for example `.../postgres`) to use a server other than the local one.

Front end (no login: pick who you are in the "Signed in as" box):

```
python scripts/build.py
pip install -r requirements.txt           # once: Flask and the PostgreSQL driver
python app/app.py                            # then open http://localhost:8000
```

The app checks the database's schema version when it starts (the `schema_info` row in `db/schema.sql`, `REQUIRED_SCHEMA` in `app/nmc/core.py`; raise both together when a schema change would break older databases). If it says the database was built with an older schema, run `python scripts/build.py` to rebuild the demo database (this empties the database in `DATABASE_URL`, so it is for demo data only; a database holding real hours needs the change applied to it as a migration instead).

Pages: My Timesheet (weekly grid), My team, Approvals and Missing timesheets (line managers, approvers and cost controllers), Reports (CTR and weekly period reports), Admin and Audit trail (cost controllers and the IT admin, each seeing their own tabs). Rebuilding the database resets the data.

## Roles

Each person has one permission level: `user`, `controller` (cost controller) or `admin` (IT). Everyone can enter, save and submit their own time. **Approving is not a level, and it is per person, not per client or scope:** a person's whole timesheet is approved by their **line manager** (Admin, Users), or, when someone else makes more sense (a line manager who only works on Spirit, say, and a person on Apache scopes), by the **Approver** named on that person's record, which replaces the line manager for approval. Nobody approves their own hours. Someone with neither a line manager nor an approver has nobody to approve for them, so a cost controller does. The database enforces this (an approval by anyone else is refused). Anyone can be a line manager or an approver.

| | Standard user | Line manager / approver (of at least one person) | Cost controller | Admin (IT) |
|---|---|---|---|---|
| Own timesheet | Yes | Yes | Yes | Yes |
| My team: any week of their people's timesheets (read-only; drafts stay private) | No | Yes | Yes, anyone | No |
| Approvals page, approve or reject | No | Yes, for the people they approve for, never their own hours | Yes, for people with nobody set to approve | No |
| Missing timesheets (who has not submitted, by week) | No | Their own people: the people they manage or approve for | Everyone active that week | No |
| Reports | Own hours only, no values | Their own and their team's hours, no values | Everything, with £ values and sales rates | No |
| £ values and sales rates | No | No | Yes | No |
| Admin tabs | None | None | All of them: weekly periods, users, job roles, clients, rate cards, projects / scopes / CTRs, internal codes, locations, allowance rates, budgets (P6). They maintain the system day to day; the one thing they cannot do is change their own permission level | Weekly periods, users, job roles, locations, budgets (P6) |
| Audit trail | No | No | Yes | Yes |

In the demo data, Alisdair Woodhead manages most of the engineers and planners, Kenneth Nicolson one person, and Graeme Hogg, the managing director, the heads and the commercial team. **My team** (a line manager's or approver's menu) lists their people with last week and this week at a glance and opens any week of any of their timesheets, read-only; a cost controller can find anyone. A person's record also holds their **default location** (On Shore unless changed, for an offshore rep say: new timesheet rows start there), their **contracted hours a week** and their **employment**: Staff, Contractor - LTD or Contractor - PAYE.

Admin tabs: weekly periods (close a week, or open a missed one), users, roles / skill codes, clients, client rate cards, projects, scopes and CTRs, internal codes, and locations (the hour types). Users, roles, scopes, internal codes and locations can be edited; rates are never edited, you add a new one with a later effective date.

Weekly periods open themselves: the first request in a new week creates that week's period, so nobody has to open it by hand. A week an admin has closed stays closed. The admin tab can still open a week that was missed, for example after the app was not used for a while.

On the timesheet, the row, day and week totals update as hours are typed, before saving. Each row shows its skill code and location as small labels.

**Sent back:** when an approver (or, for an approver's own hours, a cost controller) rejects an entry, it goes back to the person as a draft with the reason. The person sees it three ways: a "Sent back for correction" notice on that week's timesheet (the entry, the reason, who sent it back and when), a hover note on the box itself, and "My Timesheet (n sent back)" in the menu on every page, plus a pointer to any other weeks that have some. The notice disappears when the week is submitted again. Reasons are shown as plain text. No email is sent: users have no email address stored.

**PAAFs:** the PAAF register (Admin, PAAFs, cost controllers) says who may write time on which client project and under which job title: one row per person, project and role (the client is the project's), with an optional reference. A person can hold several roles on a project. On the timesheet a person is offered only the CTRs of projects they hold a PAAF for (plus the internal codes, which need none) and, for each, only the job titles on their PAAFs for that project (an internal code takes their own title); someone with no PAAF is told to speak to commercial. The PAAF's role is the title billed to the client, so the row is priced at the client's rate for that title: one job can be Lead Document Controller on Spirit's project and Senior Document Controller on Apache's. A PAAF can only be created for a role the client has a rate for. The database refuses client hours without a matching PAAF (and a change of project, person or title on an entry); removing a PAAF stops new bookings only, hours already booked stay. The restricted-CTR list is a further limit on top. A PAAF has a **start date** (required) and an **end date** (optional): hours can only be booked on days from the start to the end (the database refuses other days, and the timesheet stops offering an expired PAAF), and a PAAF is renewed by changing its dates. The register holds **no documents**: the signed PAAFs are kept elsewhere and commercial updates this register; the reference only says where to find the document.

**The Reports page (for now):** it carries only what the commercial team asked for. Cost controllers get three contractor hours reports for AAA, hours only (submitted plus approved, drafts left out), with a table and an Excel download each: **Ltd company hours** (a month's total per Ltd contractor and location), **PAYE weekly hours** (each week's total per PAYE contractor and location, for any dates) and **PAYE monthly hours** (a month split by week commencing; a week over a month end shows only the days in that month). Everyone who has a team, and the cost controllers, also get the **Manhours by client** report (with the PDF to sign). The earlier reports (CTR tree, period, budget, internal hours, location, offshore allowance, AAA Consulting export, weekly hours, month end, timesheet grid) are **hidden, not removed**: `/reports?all=1` shows them all, a link with `r=<report>` opens that one, and setting `SHOW_ALL_REPORTS = True` in reports.py puts the buttons back.

**Assets and POs:** each client has assets (Admin, Assets / POs) and each asset can hold one purchase order number, which is optional because operators invoice in different ways. A scope can be given one of its own client's assets (the database refuses another client's); the asset and its PO then show on the manhour report.

**Manhour report by client:** on Reports, line managers and approvers (limited to their own people's hours) and the cost controllers (everything) can open the Manhour report by client and download it as Excel or PDF. It follows the layout of the CMAP/Spirit report: rows by CTR (with its asset and PO), then person and role, one column per week commencing, a subtotal for each CTR with several people and a total. Hours are **submitted plus approved** (Edited too), counted once, and hours only, never money. The report is built from a ledger of the audit trail (`HOUR_LEGS` in reports.py): each audit row takes the old hours out and puts the new hours in, dated by when the change was made, so adding up every period gives the hours as they stand now and nothing is counted twice. Work dated in the period shows its hours as they stood at the end of the period; a change made *in* the period to work dated *before* it (a late timesheet, a correction up or down, hours sent back to Draft) is listed under **Adjustments** against the week it belongs to and the day it was changed (+ added, - taken off), so a timesheet corrected in November reaches the November report. The PDF is landscape A4 with the NMC logo, the table, the adjustments and boxes at the end for the preparer, NMC and the client to sign (name, signature, date), ready to send to DocuSign. The other reports (CTR, period, grid) still show approved and pending hours side by side.

**Adding rows:** the **+ Add timesheet row** button above the grid opens a popup with a CTR list you can search by typing, the skill code and location (filled in with the person's own skill code and default location, and always visible), an Add row button and a Cancel. **Overtime is no longer flagged on a timesheet.** Each person's contracted hours a week are on their record, and the view `v_weekly_hours` (and the Weekly hours export on Reports) gives every person's total hours each week against them, so overtime can be worked out later.

**Deleting a row:** each booking row has a small × at its right while the sheet can be edited. It deletes the row and its draft hours (asking first if there are hours, and writing the removals to the audit trail); hours typed on the other rows are saved first. A row with submitted or approved hours has no ×, and the server refuses a hand-made request to delete it.

**Submitted sheets are locked:** once a week has been submitted, the whole sheet is locked: no boxes (empty days included), no new rows, no Save or Submit buttons, and the server refuses any hand-made request to change it. It opens again only when a cost controller reopens it, or when an approver sends entries back (those entries return to Draft).

**Timesheet conveniences:** the top of the timesheet shows the week's status (not started, draft, partly submitted, submitted, approved), a This week link and a date picker. Add row saves the hours you have typed before adding the new row, so nothing is lost. Submit week with nothing to submit says so. A person cannot book more than 24 hours in one day across all rows (enforced by the database). Reports default to the current year. The IT admin's system account is not listed as a missing timesheet. The rate card has a client filter and marks each rate Current, Superseded or Future. The audit trail can be filtered to one person's hours. Approvals has an Approve all waiting button on each person's line (works without scripting). Reopen timesheet lists the weeks that can be reopened. The IT admin account has no timesheet. Admin, Users has a name / NMC ID search. Admin, Job families / titles lists the families (departments) with their number of titles and people; click a family to see its job titles and who holds each, rename the family, or add a title to it.

**Allowances:** there is one, the **offshore allowance**, paid **per night spent offshore** and worth £100 a night. Nobody claims it: it is worked out from the hours. A night is earned when a member of staff has hours at a location that earns the allowance (Offshore, set on Admin, Locations) on a day **and on the next day**, so a trip of N days is N-1 nights: offshore Monday to Sunday is 6 nights, and a single offshore day earns none. Trips are counted across weeks and months, and because offshore staff book the weekend too, a stay over a weekend counts. The night belongs to the day before it and is charged to the CTR with most offshore hours that day. It is **staff only**: contractors earn none (it is in their rate), though their hours are still billed to the client as usual. A night is Approved only when the hours it belongs to are approved, Submitted once they are submitted and Draft before that, so there is nothing separate to approve, and reopening a timesheet takes the nights back with the hours. The value is the allowance rate in force on that night (Admin, Allowance rates: change it by adding a new rate from a date, so earlier nights keep their value; it is the same for every client). Approved nights appear on each CTR sheet under Additions (cost per night, nights, total) and on the CTR's Allowances tab, in the Reports Allowances section, on the timesheet as a note, and per person on the Month end page for payroll. Hours on Non-reimbursable CTRs are tracked like any others.

**PAAF:** there is no PAAF in the app. The list of who works for each client is kept outside it; anyone can book to any open CTR (CMAP works the same way), priced at the client's rate for the skill code they book.

**Month end:** a month is the calendar month (1 Feb to 28 Feb), whatever day it ends on. When a week runs over a month end (for example Mon 30 Nov, with Tue 1 Dec onwards in the next month), the timesheet offers **Submit up to 30/11**: it submits and locks the days up to the month end on their own, and the days after stay open to enter. Locked days cannot be changed or added to (a cost controller can reopen the timesheet, with a reason, as for any submitted week), and the usual Submit week still sends everything. The approver sees the submitted days straight away, so a partial week goes for approval. **Month end** (cost controllers, in the menu) shows, for any calendar month, who has not entered or submitted their hours (to chase by hand; nothing is closed from there, so nobody is stopped from entering time they are owed pay for), everyone's hours by status, and the month's hours by CTR and skill code with value, with a download as Excel for the report that goes out the day after month end. Hours are counted by the date they were worked, so a split week counts on each side of the month end.

**Append-only records:** the audit trail cannot be edited or deleted, and a rate is never edited or deleted: a change is a new rate row with a later effective date. Both are enforced by the database, not just the screens.

**Budget against actual (from P6):** P6 holds budget hours only, no money, and CTRs are still created here and keyed into P6 by hand. P6 is parked for version 1, so the Budgets tab is for the IT admin only. The IT admin loads the budget hours back from a P6 export on Admin, Budgets: paste lines of `CTR No, budget hours` (a heading line is ignored) with an as-of date. Each load is a snapshot that is never edited or deleted, and the latest is compared with the approved hours booked here. The comparison (budget, actual, pending, remaining, % used) is on each CTR's page and in a Budget against actual hours section at the bottom of Reports, for cost controllers only. A CTR with hours but no budget shows as not budgeted. The cost controllers' **Dashboard** (in the menu) charts it in hours only: tiles for budget, approved, pending, remaining, % used, and CTRs over or near budget, a bar chart of budget against approved hours for every CTR (over-budget CTRs in dark red and marked in words), and approved hours by week stacked by client. The dashboard can be filtered by client, project, scope and CTR (each list only offers what fits the choices before it), by how much of the budget is used (over budget, 90% or more, under 90%), and by a range of weeks for the weekly chart; the budget bars always compare with all approved hours to date. The charts are drawn on the server as plain SVG, so there are no scripts or outside requests, and each bar has a hover tooltip. Loading is by pasting. The box takes either simple `CTR No, hours` lines or a **P6 export**: run this in SSMS with a read-only account (the P6 database is SQL Server; the project numbers are your own), then copy the grid, or save it as CSV, and paste it in.

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

**CTR payment terms:** Reimbursable (the default), Budget, Lump Sum or Non-reimbursable, set when the CTR is added or on its Edit page. Non-reimbursable CTRs are marked on the CTR sheet and are the ones to leave out of future billing extracts. A Lump Sum CTR holds the price agreed with the client (empty until agreed); the CTR sheet shows the cost to date against it and the % used, worked out when the sheet is opened, not stored.

**Rate:** hours are priced at the client's rate card line for the skill code booked (the person's own job title unless they cover another role), at the rate in force on the work date.

**Structure:** Client > Project > Scope > CTR. A CTR is the booking code hours are booked to (it was called a phase; internally the table is still `phase`). Who approves hours has nothing to do with the client or scope: see Roles above.

**Timesheet grid and Excel:** Reports links to the Timesheet grid: hours by person, booking code and day. Choose a ready-made layout (person by code by day, person by day, person by week, code by person total, code by week, location and type by week), tailor the columns, show each day, each week or one total, and narrow by dates, client, scope or person. "Download as Excel" gives a real .xlsx of exactly what is on screen, and the period report has its own download. Cost controllers also get a Value column; everyone else sees only the hours they are allowed to see.

**Approvals:** the Approvals page is a weekly view. It opens on the oldest week that still has hours waiting and shows one line per person with their hours for each weekday, a week total, and the hours waiting and already approved. "detail" (or Show all detail) opens everything booked against that person that week, approved lines included, with a tick box on each entry still waiting; ticking a person ticks their waiting entries. Previous week, Next week, This week and a date picker go to other weeks, so earlier approved time stays visible. Approve selected and Reject selected (a reason is needed to reject) keep the same week on screen.

**Copy last week:** when last week has hours, this week is open and nothing in this week has been submitted or approved yet, the timesheet offers one button, "Copy last week". It replaces whatever is in this week with last week's rows and hours: the draft hours already there are cleared (and audited, and the browser asks first), then last week's hours are saved as drafts on the same weekdays, with the same code, skill code, location and overtime flag. If any hours in the week are already submitted or approved, the week cannot be replaced (a cost controller has to reopen it first). Anything that cannot be copied (for example a day outside your start and end dates) is reported, not skipped silently. Copying uses the same booking rules as saving, so rates are looked up for the new date.

## Hour types: location

Every timesheet row has a **location** (On Shore, Offshore or Site visit by default; the IT admin can add more), starting at the person's default location, so one person can book the same code on the same day on shore and offshore, each as its own row. The location decides the offshore allowance (see below).

- The list of locations is managed by the IT admin on the Locations tab (add, rename, switch off). A location that is switched off stops new bookings; hours already booked keep it. At least one must stay active.
- **Overtime is not flagged any more.** Older hours that were booked as overtime still show on a row of their own; the database column stays (always 0 for new hours).
- Approvers see the location on the Approvals page, the CTR page lists it for each entry, and Reports has an "Hours by location" section (hours only, limited to what the person may see).
- Cell names on the timesheet form are `h_<phase>_<skill code>_<location>_<overtime>_<date>`; the overtime part is always 0 for new rows.

## Internal hours

Hours NMC pays for itself rather than bills to a client are booked to an internal code, shown as `Internal - Business Services`. An internal code is a scope under the Internal client (a client flagged `is_internal`), with a General phase. The seeded codes are the company's list: Sick, Compassionate (shown as they are, as absence) and Internal - Business Development / Strategy, Executive Leadership, Commercial / Business, Training, HSEQ, Engineering, Business Services, Projects, HR and Other. Internal hours are approved like any others, by the person's line manager. The cost team adds, renames and closes codes on the Internal codes tab; each new code is numbered for you (INT13, INT14, ...).

- Anyone can book to an open code, internal or client.
- Internal hours have no sales rate (`rate_id` is empty on the entry), and the Internal client cannot be given a rate card.
- Everything else is the same as client hours: draft, submit, approval by the person's line manager (or approver), controlled edits, audit trail, open weekly period.
- They are left out of the CTR and the weekly period report, which are about billable value. Reports has a separate Internal hours section for the chosen date range.

Connect to the database with any PostgreSQL client (VS Code's PostgreSQL extension, pgAdmin, `psql`). Useful queries:

```sql
SELECT * FROM v_ctr;                          -- Cost Time Resource per phase and role (approved only)
SELECT * FROM v_approver_queue;               -- waiting on approvers
SELECT * FROM v_period_report
 WHERE week_start BETWEEN '2026-10-26' AND '2026-11-02';
```

## Rules enforced in the database

- Booking is to a phase and needs an Open project, scope and phase, a rate that matches the client and worked role, and an Open weekly period. Internal codes need no rate and cannot carry one. The work date must also fall between the person's start and end dates (no end date means still employed; the last day counts). On the timesheet, days outside a person's dates have no box.
- Lifecycle: Draft, Submitted, Approved. Only Draft hours can be typed over: submitted and approved hours show as plain text, and the server refuses a changed or cleared value for them too. Reject returns to Draft with a reason. A cost controller can reopen a submitted or approved week (Reopen timesheet in the menu, a reason is required, audited, never their own timesheet); it goes back to the person as drafts with the reason shown, and has to be submitted and approved again. Hours changed after approval by other means are marked Edited and need re-approval.
- Approval is by the person's approver (the approver named on their record, otherwise their line manager), never the entry owner. When a person has neither, a cost controller approves their hours. Admins never approve.
- Every edit names its editor; every create, edit, status change and deletion is audited. Only Draft entries can be deleted (clear draft).

## CTR page

**CTR sheet:** the Summary tab of the CTR page is laid out like the CTR sheets in the estimate workbook (for example `EN-D-001`): project name, title and client, date, the client's framework agreement reference, CTR title and CTR number (the scope code, then the CTR's own code such as `PM-D-001`, giving `A1500-PM-D-001`), prepared / checked / approved by, objectives, input requirements, scope of work, what it assumes and excludes, commencement and completion, then the Resource table (cost per hour, hours, total), the Additions (approved allowances at the allowance rates), the total, payment terms (Reimbursable, Budget or Lump Sum, marked with an X) and the basis of proposal. The text fields are edited on the CTR's Admin page (Projects / scopes / CTRs, Edit), the framework reference on the client. Hours and allowances are what was actually booked and approved, not a budget; cost columns are shown to cost controllers only.

On the Reports tree, each Phase row has an "Open CTR" link to `/ctr?phase=<id>`. The CTR page shows the phase header (client, project, scope, phase, approver) and tabs like the CTR workbook: a Summary tab (one line per job family, Actual booked hrs / Total / Pending hrs) and one tab per job family with its resources (job title, skill code, sales rate), people and timesheet entries. Only Approved hours count towards Actual booked hrs and Total.
