# NMC Timewriting System: v1 mock database

SQLite mock of the v1 data model for the in-house timesheet system (CMAP replacement). The people and job titles in the demo data are real NMC staff, a selection taken from the staff directory (the rest are left out so they can be used to test onboarding); rates, projects, codes and hours are made up.

## Layout

```
db/
  schema.sql      tables, triggers (business rules), views
  seed.sql        demo data (4 locations, 2 clients plus the Internal client with 3 internal codes, 32 job titles in job families (3 with no rate card), 30 users, 4 projects, 9 scopes, 10 phases, 52 entries over 2 weeks)
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

Pages: My Timesheet (weekly grid), Approvals (approvers and cost controllers), Missing timesheets (approvers and cost controllers), Reports (CTR and weekly period reports), Admin and Audit trail (cost controllers and the IT admin, each seeing their own tabs). Rebuilding the database resets the data.

## Roles

Each person has one permission level: `user`, `controller` (cost controller) or `admin` (IT). Everyone can enter, save and submit their own time. Approver is not a level: it means being named as the approver on a scope, and only standard users can be named. Approvers book their own time like anyone else, including to the scopes they approve (they hold a PAAF for that client); nobody approves their own hours, so a cost controller approves the hours an approver books to their own scope. Apart from that, cost controllers and admins never approve hours.

| | Standard user | Approver (a user named on a scope) | Cost controller | Admin (IT) |
|---|---|---|---|---|
| Own timesheet | Yes | Yes | Yes | Yes |
| Approvals page, approve or reject | No | Yes, for their scopes only, never their own hours | Only hours an approver booked to their own scope | No |
| Missing timesheets (who has not submitted, by week) | No | Their own people: those with an approved PAAF for a client they approve, plus anyone who booked to their internal codes | Everyone active that week | No |
| Reports | Own hours only, no values | Own hours plus everyone's hours on their scopes, no values | Everything, with £ values and sales rates | No |
| £ values and sales rates | No | No | Yes | No |
| Admin tabs | None | None | Weekly periods, clients, rate cards, PAAFs, projects / scopes / phases, internal codes | Weekly periods, users, job roles, locations |
| Audit trail | No | No | Yes | Yes |

In the demo data, Alisdair Woodhead approves for Apache and Kenneth Nicolson for Spirit (both standard users). The cost controllers are the two cost engineers, Emma Noble and Abbi Murray, and Stephanie Apedaile (commercial lead), who is not a cost engineer: cost permission is set per person on the Users tab, never taken from a job title, so anyone can be given it when the system goes live. NMC Admin is the IT account. Sales rates and £ values are never put in the page for anyone but a cost controller; the reports, their filters and the CTR page only show other people what they may see. Still to come from the roles matrix: reopening a timesheet, transaction controls, and more. Who owns weekly periods and the audit trail is still to be confirmed with the cost team, so for now both controllers and admins can use them.

Admin tabs: weekly periods (close a week, or open a missed one), users, roles / skill codes, clients, client rate cards, PAAFs (raise, mark sent, approve with the signed form reference, reject), projects, scopes (each needs an approver) and phases, internal codes, and locations (the hour types). Users, roles, scopes, internal codes and locations can be edited; rates are never edited, you add a new one with a later effective date.

Weekly periods open themselves: the first request in a new week creates that week's period, so nobody has to open it by hand. A week an admin has closed stays closed. The admin tab can still open a week that was missed, for example after the app was not used for a while.

On the timesheet, the row, day and week totals update as hours are typed, before saving. Under the grid there are three lines: Normal time, Overtime and Total.

**Sent back:** when an approver (or, for an approver's own hours, a cost controller) rejects an entry, it goes back to the person as a draft with the reason. The person sees it three ways: a "Sent back for correction" notice on that week's timesheet (the entry, the reason, who sent it back and when), a hover note on the box itself, and "My Timesheet (n sent back)" in the menu on every page, plus a pointer to any other weeks that have some. The notice disappears when the week is submitted again. Reasons are shown as plain text. No email is sent: users have no email address stored.

**Timesheet conveniences:** the top of the timesheet shows the week's status (not started, draft, partly submitted, submitted, approved), a This week link and a date picker. Add row and Add allowance row save the hours you have typed before adding the new row, so nothing is lost. If a client PAAF is not approved yet, a line says so and why that client's codes are not offered. Submit week with nothing to submit says so. A person cannot book more than 24 hours in one day across all rows (enforced by the database). Reports default to the current year. The IT admin's system account is not listed as a missing timesheet. The rate card has a client filter and marks each rate Current, Superseded or Future. The audit trail can be filtered to one person's hours. Approvals has an Approve all waiting button on each person's line (works without scripting). Reopen timesheet lists the weeks that can be reopened. The IT admin account has no timesheet. Admin, Users has a name / NMC ID search. Admin, Job families / titles lists the families (departments) with their number of titles and people; click a family to see its job titles and who holds each, rename the family, or add a title to it.

**Allowances:** Overnight allowance, Per Diem and Mob fee (the design document lists them as in scope for the first release). On the timesheet an Allowances table has a row per booking code and allowance with a tick box per day; one of each allowance per person per day. They follow the same rules as hours: the person needs an approved PAAF (internal codes need none), the code must be open and not restricted against them, the week must be open, and the day must be within their start and end dates. Submit week submits them with the hours, the booking code's approver approves or sends them back (a reason is needed) on the same Approvals page, nobody approves their own, a cost controller approves an approver's own, and Reopen timesheet sends them back to Draft with the hours. Everything is in the audit trail, which for allowances cannot be changed or deleted. Reports has an Allowances section (approved and waiting days per person and allowance). **Assumptions to confirm:** the claims are flat (a day is claimed or not), no amounts or rates are held, an allowance is claimed against a booking code, and they are not copied by Copy last week.

**PAAF list:** Admin, PAAFs lists the register in order of its # column. Filter by person, client and status, and click the #, Name, Client, Status or Expiry heading to sort (again to reverse). The filters stay in place after you act on a PAAF.

**Append-only records:** the audit trail cannot be edited or deleted, and a rate is never edited or deleted: a change is a new rate row with a later effective date. Both are enforced by the database, not just the screens.

**PAAF rate:** when a PAAF is raised (Admin, PAAFs) the cost controller picks the person's position from a dropdown of that client's rate card, or leaves it on the default, the position for the person's own role. It can be changed until the PAAF is approved. When the person books as their own role, that position sets the rate (the rate in force on the work date); covering another role uses that role's rate card line.

**Booking code controls:** a cost controller opens a phase (booking code) on Admin, Projects / scopes / phases. Besides its name and status each code has a description, optional WBS, CBS and ABS references, an optional approver of its own (blank means the scope's approver; only standard users can be named), and transaction controls: either anyone who may book to the client, or only the people ticked, and optionally a Bookable from / until date window. The database enforces the list and the window on new bookings; the timesheet's Add row list leaves out codes the person cannot book that week. Hours already booked are not affected.

**Timesheet grid and Excel:** Reports links to the Timesheet grid: hours by person, booking code and day. Choose a ready-made layout (person by code by day, person by day, person by week, code by person total, code by week, location and type by week), tailor the columns, show each day, each week or one total, and narrow by dates, client, scope or person. "Download as Excel" gives a real .xlsx of exactly what is on screen, and the period report has its own download. Cost controllers also get a Value column; everyone else sees only the hours they are allowed to see.

**Approvals:** the Approvals page is a weekly view. It opens on the oldest week that still has hours waiting and shows one line per person with their hours for each weekday, a week total, and the hours waiting and already approved. "detail" (or Show all detail) opens everything booked against that person that week, approved lines included, with a tick box on each entry still waiting; ticking a person ticks their waiting entries. Previous week, Next week, This week and a date picker go to other weeks, so earlier approved time stays visible. Approve selected and Reject selected (a reason is needed to reject) keep the same week on screen.

**Copy last week:** when last week has hours, this week is open and nothing in this week has been submitted or approved yet, the timesheet offers one button, "Copy last week". It replaces whatever is in this week with last week's rows and hours: the draft hours already there are cleared (and audited, and the browser asks first), then last week's hours are saved as drafts on the same weekdays, with the same code, skill code, location and overtime flag. If any hours in the week are already submitted or approved, the week cannot be replaced (a cost controller has to reopen it first). Anything that cannot be copied (for example a PAAF that has expired, or a day outside your start and end dates) is reported, not skipped silently. Copying uses the same booking rules as saving, so rates are looked up for the new date.

## Hour types: location and overtime

Every timesheet row has a **location** (On site, Offshore or Standard, which covers working in the office or from home) and a **type** (Normal or Overtime), so one person can book the same code on the same day as normal time, as overtime, on site and offshore, each on its own row. "Add row" has a Location dropdown and an Overtime tick box. Every entry stores both (`location_id` and `overtime`); once hours are submitted or approved they are locked, and the audit trail keeps the old values if a cost controller reopens and changes them.

- The list of locations is managed by the IT admin on the Locations tab (add, rename, switch off). A location that is switched off stops new bookings; hours already booked keep it. At least one must stay active.
- **Overtime has no separate rate yet**, so overtime hours are valued at the normal rate in the CTR and weekly report totals. When the cost team has overtime rates they will need a place on the rate card (a second rate or a multiplier).
- Approvers see the location and type on the Approvals page, the CTR page lists them for each entry, and Reports has an "Hours by location and overtime" section (hours only, limited to what the person may see).
- Cell names on the timesheet form are `h_<phase>_<skill code>_<location>_<overtime>_<date>`.

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

- Booking is to a phase and needs an Open project, scope and phase, an Approved PAAF for the scope's client, a rate that matches the client and worked role, and an Open weekly period. Internal codes need none of the PAAF and rate, and cannot carry them. The work date must also fall between the person's start and end dates (no end date means still employed; the last day counts), and, for client work, on or before the PAAF's expiry date if it has one. On the timesheet, days outside a person's dates have no box.
- Lifecycle: Draft, Submitted, Approved. Only Draft hours can be typed over: submitted and approved hours show as plain text, and the server refuses a changed or cleared value for them too. Reject returns to Draft with a reason. A cost controller can reopen a submitted or approved week (Reopen timesheet in the menu, a reason is required, audited, never their own timesheet); it goes back to the person as drafts with the reason shown, and has to be submitted and approved again. Hours changed after approval by other means are marked Edited and need re-approval.
- Approval is by the person named as the scope's approver, never the entry owner. The one exception: hours booked by the scope's own approver are approved by a cost controller. Admins never approve, and neither controllers nor admins can be named as an approver.
- Every edit names its editor; every create, edit, status change and deletion is audited. Only Draft entries can be deleted (clear draft).

## CTR page

On the Reports tree, each Phase row has an "Open CTR" link to `/ctr?phase=<id>`. The CTR page shows the phase header (client, project, scope, phase, approver) and tabs like the CTR workbook: a Summary tab (one line per job family, Actual booked hrs / Total / Pending hrs) and one tab per job family with its resources (job title, skill code, sales rate), people and timesheet entries. Only Approved hours count towards Actual booked hrs and Total.
