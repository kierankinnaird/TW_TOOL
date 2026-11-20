-- NMC Timewriting System - v1 mock database (SQLite)
PRAGMA foreign_keys = ON;
-- Schema version. The app refuses to start on a database with a lower number (see REQUIRED_SCHEMA in app/app.py),
-- so raise both together whenever an older database could no longer work with the app.
PRAGMA user_version = 22;  -- 22: the offshore allowance is per night spent offshore and worked out from the hours (no claims, no allowance approvals)
                          -- 21: the PAAF table is gone (the client's list of people is kept outside the app)
                          -- 2: a cost controller approves the hours a scope approver books to their own scope
                          -- 3: hours can only be booked within a person's start and end dates, and before a PAAF expires
                          -- 4: every entry has a location (On site / Offshore / Standard) and an overtime flag
                          -- 5: a cost controller can reopen submitted or approved hours (reopen_reason)
                          -- 6: booking codes (phases) can have their own approver, a description, WBS / CBS / ABS references,
                          --    and transaction controls (a list of people and a booking window)
                          -- 7: the position (rate card line) assigned on a person's PAAF sets the rate for their own-role hours
                          -- 8: the audit trail can only be added to, never changed or deleted
                          -- 9: rate rows can only be added: a rate is never edited or deleted, a new row with a later date takes over
                          -- 10: allowances (Overnight allowance, Per Diem, Mob fee) claimed per day, submitted and approved with the hours
                          -- 11: a person cannot book more than 24 hours in one day across all rows
                          -- 12: allowances are for staff only, and are valued from an append-only rate table (by allowance, location and overtime)
                          -- 13: approvers are a main approver per client plus secondary approvers per scope; the phase is the CTR (booking code)
                          -- 20: a CTR can be Non-reimbursable (left out of billing) or Lump sum, with the agreed lump sum value
                          -- 19: a PAAF is only the client's list of people: it no longer limits who books or sets the rate (the rate is the client's rate for the skill code booked)
                          -- 18: a budget snapshot can carry P6's actual hours and % complete (from the P6 export)
                          -- 17: budget hours per CTR (from P6), compared with the approved hours booked here
                          -- 16: a line manager on each user; a PAAF is simply the client's list of people (active as soon as it is added, no sign-off)
                          -- 15: one offshore allowance at one rate (no location or overtime matrix); default locations On Shore / Offshore / Site visit
                          -- 14: a CTR carries the fields of the CTR sheet (CTR code, objectives, inputs, scope, assumptions, payment terms ...) and a client a framework reference

-- Controlled list of job titles, grouped into job families. Editable by admin/superuser.
CREATE TABLE role (
    role_id     INTEGER PRIMARY KEY,
    skill_code  TEXT NOT NULL UNIQUE,               -- e.g. PM-04
    name        TEXT NOT NULL UNIQUE,               -- job title
    family      TEXT NOT NULL,                      -- job family, e.g. Project Management
    active      INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1))
);

-- is_internal = 1 marks NMC's own "Internal" client: hours booked to its codes are paid by NMC, not billed to a client.
-- They have no rate, and are left out of the client CTR and period reports.
CREATE TABLE client (
    client_id   INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    is_internal INTEGER NOT NULL DEFAULT 0 CHECK (is_internal IN (0,1)),
    approver_user_id INTEGER REFERENCES app_user(user_id),  -- the client's main approver: approves hours booked to any of its scopes
    framework_ref TEXT                                      -- framework agreement / contract reference, shown on each CTR sheet
);

-- Client rate card: one row per client + role + effective date.
CREATE TABLE rate (
    rate_id        INTEGER PRIMARY KEY,
    client_id      INTEGER NOT NULL REFERENCES client(client_id),
    role_id        INTEGER NOT NULL REFERENCES role(role_id),
    sales_rate     NUMERIC NOT NULL CHECK (sales_rate >= 0),
    effective_from TEXT NOT NULL DEFAULT '2026-01-01',
    UNIQUE (client_id, role_id, effective_from)
);

-- Who can access / enter time.
CREATE TABLE app_user (
    user_id         INTEGER PRIMARY KEY,
    nmc_id          TEXT NOT NULL UNIQUE,            -- e.g. A001
    employment_type TEXT NOT NULL CHECK (employment_type IN ('Staff','Contract')),
    name            TEXT NOT NULL,
    role_id         INTEGER NOT NULL REFERENCES role(role_id),
    permission      TEXT NOT NULL DEFAULT 'user' CHECK (permission IN ('user','controller','admin')),
    start_date      TEXT NOT NULL,
    end_date        TEXT,
    line_manager_id INTEGER REFERENCES app_user(user_id) CHECK (line_manager_id IS NULL OR line_manager_id <> user_id)
);

-- Hierarchy: client > project > scope > phase. Hours are booked to a phase (the lowest level).
-- A phase is the CTR (the booking code). Approvers: a main approver on the client, secondary approvers on each scope.
CREATE TABLE project (
    project_id    INTEGER PRIMARY KEY,
    project_code  TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    client_id     INTEGER NOT NULL REFERENCES client(client_id),
    status        TEXT NOT NULL DEFAULT 'Open' CHECK (status IN ('Open','Closed'))
);

CREATE TABLE scope (
    scope_id          INTEGER PRIMARY KEY,
    scope_code        TEXT NOT NULL UNIQUE,           -- e.g. BB0001
    client_id         INTEGER NOT NULL REFERENCES client(client_id),
    project_id        INTEGER NOT NULL REFERENCES project(project_id),
    description       TEXT NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('Pipeline','Open','Closed'))
);

-- Secondary approvers on a scope (in addition to the client's main approver). Any of them can approve the scope's hours.
CREATE TABLE scope_approver (
    scope_id INTEGER NOT NULL REFERENCES scope(scope_id),
    user_id  INTEGER NOT NULL REFERENCES app_user(user_id),
    PRIMARY KEY (scope_id, user_id)
);

-- A scope is split into phases (e.g. Define, Execute). Every scope has at least one.
CREATE TABLE phase (
    phase_id   INTEGER PRIMARY KEY,
    scope_id   INTEGER NOT NULL REFERENCES scope(scope_id),
    name       TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'Open' CHECK (status IN ('Open','Closed')),
    description TEXT,                                   -- the CTR title
    -- The CTR sheet. The CTR number shown is the scope code plus ctr_code, e.g. A1500-PM-D-001 (discipline - phase letter - sequence).
    ctr_code   TEXT,
    objectives TEXT,
    inputs     TEXT,                                    -- input requirements
    scope_of_work TEXT,
    assumptions TEXT,
    exclusions TEXT,
    prepared_by TEXT,
    checked_by TEXT,
    expenses   TEXT NOT NULL DEFAULT 'Reimbursable',
    -- Non-reimbursable CTRs are left out of the billing extracts. A Lump Sum CTR works as a budget in money: lump_sum_value is the price
    -- agreed with the client, and the % billed is worked out when it is needed, not stored.
    payment_terms TEXT NOT NULL DEFAULT 'Reimbursable' CHECK (payment_terms IN ('Reimbursable','Budget','Lump Sum','Non-reimbursable')),
    lump_sum_value NUMERIC CHECK (lump_sum_value IS NULL OR (lump_sum_value >= 0 AND payment_terms = 'Lump Sum')),
    basis      TEXT,                                    -- basis of proposal
    wbs        TEXT,                                    -- optional WBS / CBS / ABS references
    cbs        TEXT,
    abs_code   TEXT,
    -- Transaction controls. restricted = 0: anyone can book to the code. restricted = 1: only the people listed in phase_person.
    -- book_from / book_to (both optional) limit the work dates that can be booked.
    restricted INTEGER NOT NULL DEFAULT 0 CHECK (restricted IN (0,1)),
    book_from  TEXT,
    book_to    TEXT,
    UNIQUE (scope_id, name),
    UNIQUE (scope_id, ctr_code)
);

-- People allowed to book to a restricted booking code.
CREATE TABLE phase_person (
    phase_id INTEGER NOT NULL REFERENCES phase(phase_id),
    user_id  INTEGER NOT NULL REFERENCES app_user(user_id),
    PRIMARY KEY (phase_id, user_id)
);

-- Approver is not a permission: it means being the main approver of a client or a secondary approver on a scope. Only standard users
-- can be named; cost controllers and admins never approve, so they cannot be approvers.
CREATE TRIGGER trg_client_approver_insert BEFORE INSERT ON client
BEGIN
    SELECT RAISE(ABORT, 'Only a standard user can be named as an approver')
    WHERE NEW.approver_user_id IS NOT NULL AND (SELECT permission FROM app_user WHERE user_id = NEW.approver_user_id) <> 'user';
END;
CREATE TRIGGER trg_client_approver_update BEFORE UPDATE OF approver_user_id ON client
BEGIN
    SELECT RAISE(ABORT, 'Only a standard user can be named as an approver')
    WHERE NEW.approver_user_id IS NOT NULL AND (SELECT permission FROM app_user WHERE user_id = NEW.approver_user_id) <> 'user';
END;
CREATE TRIGGER trg_scope_approver_insert BEFORE INSERT ON scope_approver
BEGIN
    SELECT RAISE(ABORT, 'Only a standard user can be named as an approver')
    WHERE (SELECT permission FROM app_user WHERE user_id = NEW.user_id) <> 'user';
END;
CREATE TRIGGER trg_user_permission_update BEFORE UPDATE OF permission ON app_user
BEGIN
    SELECT RAISE(ABORT, 'Remove this person as an approver before making them a controller or admin')
    WHERE NEW.permission <> 'user' AND (EXISTS (SELECT 1 FROM client WHERE approver_user_id = NEW.user_id)
                                        OR EXISTS (SELECT 1 FROM scope_approver WHERE user_id = NEW.user_id));
END;

-- Everyone who may approve hours on a scope: the client's main approver and the scope's secondary approvers.
CREATE VIEW v_scope_approver AS
SELECT s.scope_id, c.approver_user_id AS user_id FROM scope s JOIN client c ON c.client_id = s.client_id WHERE c.approver_user_id IS NOT NULL
UNION
SELECT scope_id, user_id FROM scope_approver;

-- A scope sits under a project of the same client.
CREATE TRIGGER trg_scope_project_client_insert BEFORE INSERT ON scope
BEGIN
    SELECT RAISE(ABORT, 'The scope client must match its project client')
    WHERE (SELECT client_id FROM project WHERE project_id = NEW.project_id) <> NEW.client_id;
END;
CREATE TRIGGER trg_scope_project_client_update BEFORE UPDATE OF client_id, project_id ON scope
BEGIN
    SELECT RAISE(ABORT, 'The scope client must match its project client')
    WHERE (SELECT client_id FROM project WHERE project_id = NEW.project_id) <> NEW.client_id;
END;

-- The internal client has no rate card.
-- Rates are append-only so past billing stays reproducible: a change is a new row with a later effective date.
CREATE TRIGGER trg_rate_no_update BEFORE UPDATE ON rate
BEGIN
    SELECT RAISE(ABORT, 'Rates are never edited: add a new row with a later effective date');
END;
CREATE TRIGGER trg_rate_no_delete BEFORE DELETE ON rate
BEGIN
    SELECT RAISE(ABORT, 'Rates are never deleted: add a new row with a later effective date');
END;

CREATE TRIGGER trg_rate_not_internal BEFORE INSERT ON rate
BEGIN
    SELECT RAISE(ABORT, 'Internal codes have no rate card')
    WHERE (SELECT is_internal FROM client WHERE client_id = NEW.client_id) = 1;
END;

-- Weekly periods (Mon-Sun), identified by the Monday. An entry's week is derived from work_date.
-- The app opens the current week automatically; admin can still open a missed week or close a week.
CREATE TABLE period (
    week_start  TEXT PRIMARY KEY CHECK (strftime('%w', week_start) = '1'),
    status      TEXT NOT NULL DEFAULT 'Open' CHECK (status IN ('Open','Closed')),
    closed_by   INTEGER REFERENCES app_user(user_id),
    closed_at   TEXT
);

-- Where hours are worked (an hour type). Managed by the IT admin; location 1 (On site) is the default for new entries.
CREATE TABLE location (
    location_id INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    active      INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    earns_allowance INTEGER NOT NULL DEFAULT 0 CHECK (earns_allowance IN (0,1))  -- 1 = a night here earns the allowance (Offshore)
);

-- Lifecycle: Draft -> Submitted -> Approved. Reject sends Submitted back to Draft (reason stored).
-- Editing an Approved entry makes it Edited (reason required) and it needs re-approval.
CREATE TABLE time_entry (
    entry_id    INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES app_user(user_id),
    scope_id    INTEGER NOT NULL REFERENCES scope(scope_id),
    phase_id    INTEGER NOT NULL REFERENCES phase(phase_id), -- lowest booking level; must belong to scope_id
    worked_role_id INTEGER NOT NULL REFERENCES role(role_id), -- skill code booked under (defaults to user's role)
    rate_id     INTEGER REFERENCES rate(rate_id),            -- client sales rate for worked role, snapshot at booking; NULL for internal hours
    work_date   TEXT NOT NULL,
    hours       NUMERIC NOT NULL CHECK (hours > 0 AND hours <= 24),
    location_id INTEGER NOT NULL DEFAULT 1 REFERENCES location(location_id),  -- where the hours were worked
    overtime    INTEGER NOT NULL DEFAULT 0 CHECK (overtime IN (0,1)),         -- 1 = an overtime line (no separate rate yet)
    status      TEXT NOT NULL DEFAULT 'Submitted' CHECK (status IN ('Draft','Submitted','Approved','Edited')),
    approved_by INTEGER REFERENCES app_user(user_id),
    approved_at TEXT,
    reject_reason      TEXT,                          -- set when sent back to Draft
    reopen_reason      TEXT,                          -- set when a cost controller reopens submitted or approved hours
    last_change_reason TEXT,                          -- required for controlled edits
    edit_count  INTEGER NOT NULL DEFAULT 0,           -- times edited after approval
    created_by     INTEGER NOT NULL REFERENCES app_user(user_id),  -- who keyed it (may differ from user_id)
    created_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_edited_by INTEGER REFERENCES app_user(user_id),
    last_edited_at TEXT
);

-- Audit event for every create, edit, status change and deletion of a Draft.
CREATE TABLE time_entry_audit (
    audit_id    INTEGER PRIMARY KEY,
    entry_id    INTEGER NOT NULL,
    action      TEXT NOT NULL CHECK (action IN ('CREATE','UPDATE','STATUS','DELETE')),
    changed_by  INTEGER REFERENCES app_user(user_id),
    changed_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reason      TEXT,
    old_status  TEXT, new_status TEXT,
    old_hours   NUMERIC, new_hours NUMERIC,
    old_scope_id INTEGER, old_phase_id INTEGER, old_worked_role_id INTEGER, old_rate_id INTEGER, old_work_date TEXT,
    old_location_id INTEGER, old_overtime INTEGER
);

-- Booking guard: Open scope, matching rate for the client, Open weekly period.
CREATE TRIGGER trg_time_entry_valid
BEFORE INSERT ON time_entry
BEGIN
    SELECT RAISE(ABORT, 'New entries must be Draft or Submitted')
    WHERE NEW.status NOT IN ('Draft','Submitted');
    -- Only people who are live can book: the work date must fall within their start and end dates (no end date = still employed).
    SELECT RAISE(ABORT, 'This person is not active on that date (check their start and end dates)')
    WHERE EXISTS (SELECT 1 FROM app_user
                  WHERE user_id = NEW.user_id AND (NEW.work_date < start_date OR (end_date IS NOT NULL AND NEW.work_date > end_date)));
    SELECT RAISE(ABORT, 'Scope is not Open')
    WHERE (SELECT status FROM scope WHERE scope_id = NEW.scope_id) <> 'Open';
    SELECT RAISE(ABORT, 'CTR does not belong to this scope')
    WHERE NOT EXISTS (SELECT 1 FROM phase WHERE phase_id = NEW.phase_id AND scope_id = NEW.scope_id);
    SELECT RAISE(ABORT, 'Project is not Open')
    WHERE (SELECT pr.status FROM scope sc JOIN project pr ON pr.project_id = sc.project_id WHERE sc.scope_id = NEW.scope_id) <> 'Open';
    SELECT RAISE(ABORT, 'CTR is not Open')
    WHERE (SELECT status FROM phase WHERE phase_id = NEW.phase_id) <> 'Open';
    -- Transaction controls on the booking code: a list of people, and a window of dates (both optional).
    SELECT RAISE(ABORT, 'This booking code is restricted and this person is not on its list of people')
    WHERE (SELECT restricted FROM phase WHERE phase_id = NEW.phase_id) = 1
      AND NOT EXISTS (SELECT 1 FROM phase_person WHERE phase_id = NEW.phase_id AND user_id = NEW.user_id);
    SELECT RAISE(ABORT, 'This booking code cannot be booked on that date (outside its booking dates)')
    WHERE EXISTS (SELECT 1 FROM phase WHERE phase_id = NEW.phase_id
                  AND ((book_from IS NOT NULL AND NEW.work_date < book_from) OR (book_to IS NOT NULL AND NEW.work_date > book_to)));
    -- Client work needs the client's rate for the skill code booked. Internal hours have no rate.
    SELECT RAISE(ABORT, 'Rate does not match the scope client and worked role')
    WHERE (SELECT c.is_internal FROM scope sc JOIN client c ON c.client_id = sc.client_id WHERE sc.scope_id = NEW.scope_id) = 0
      AND NOT EXISTS (
        SELECT 1 FROM rate r JOIN scope s ON s.client_id = r.client_id
        WHERE r.rate_id = NEW.rate_id AND s.scope_id = NEW.scope_id AND r.role_id = NEW.worked_role_id);
    SELECT RAISE(ABORT, 'Internal hours have no rate')
    WHERE (SELECT c.is_internal FROM scope sc JOIN client c ON c.client_id = sc.client_id WHERE sc.scope_id = NEW.scope_id) = 1
      AND NEW.rate_id IS NOT NULL;
    SELECT RAISE(ABORT, 'Weekly period is not open')
    WHERE NOT EXISTS (
        SELECT 1 FROM period
        WHERE week_start = date(NEW.work_date, 'weekday 0', '-6 days') AND status = 'Open');
END;

-- Edit rules.
CREATE TRIGGER trg_time_entry_edit_rules
BEFORE UPDATE ON time_entry
BEGIN
    -- no anonymous changes
    SELECT RAISE(ABORT, 'Edits must set last_edited_by and last_edited_at')
    WHERE NEW.last_edited_by IS NULL OR NEW.last_edited_at IS NULL
       OR (NEW.last_edited_by IS OLD.last_edited_by AND NEW.last_edited_at IS OLD.last_edited_at);

    SELECT RAISE(ABORT, 'CTR does not belong to this scope')
    WHERE NOT EXISTS (SELECT 1 FROM phase WHERE phase_id = NEW.phase_id AND scope_id = NEW.scope_id);

    -- changing an approved entry makes it Edited
    SELECT RAISE(ABORT, 'Changing an approved entry sets its status to Edited')
    WHERE OLD.status IN ('Approved','Edited') AND NEW.status <> 'Edited'
      AND (NEW.hours IS NOT OLD.hours OR NEW.scope_id IS NOT OLD.scope_id OR NEW.phase_id IS NOT OLD.phase_id OR NEW.work_date IS NOT OLD.work_date
           OR NEW.worked_role_id IS NOT OLD.worked_role_id OR NEW.rate_id IS NOT OLD.rate_id
           OR NEW.location_id IS NOT OLD.location_id OR NEW.overtime IS NOT OLD.overtime
           OR NEW.user_id IS NOT OLD.user_id)
      AND NOT (OLD.status = 'Edited' AND NEW.status = 'Approved');

    -- controlled edits need a reason
    SELECT RAISE(ABORT, 'A controlled edit needs a reason (last_change_reason)')
    WHERE NEW.status = 'Edited' AND OLD.status IN ('Approved','Edited')
      AND (NEW.last_change_reason IS NULL OR NEW.last_change_reason = ''
           OR NEW.last_change_reason IS OLD.last_change_reason);

    -- reject (Submitted -> Draft) needs a reason: a reject_reason, or a reopen_reason when a cost controller reopens it
    SELECT RAISE(ABORT, 'Rejecting an entry needs a reason (reject_reason)')
    WHERE OLD.status = 'Submitted' AND NEW.status = 'Draft'
      AND (NEW.reject_reason IS NULL OR NEW.reject_reason = '')
      AND (NEW.reopen_reason IS NULL OR NEW.reopen_reason = '' OR NEW.reopen_reason IS OLD.reopen_reason);

    -- reopen: only a cost controller can send submitted or approved hours back to Draft, and only with a reason
    SELECT RAISE(ABORT, 'Only a cost controller can reopen submitted or approved hours, and they must give a reason (reopen_reason)')
    WHERE NEW.status = 'Draft' AND OLD.status IN ('Approved','Edited')
      AND (NEW.reopen_reason IS NULL OR NEW.reopen_reason = '' OR NEW.reopen_reason IS OLD.reopen_reason
           OR (SELECT permission FROM app_user WHERE user_id = NEW.last_edited_by) <> 'controller');
    SELECT RAISE(ABORT, 'Only a cost controller can reopen submitted hours')
    WHERE NEW.status = 'Draft' AND OLD.status = 'Submitted'
      AND NEW.reopen_reason IS NOT NULL AND NEW.reopen_reason IS NOT OLD.reopen_reason
      AND (SELECT permission FROM app_user WHERE user_id = NEW.last_edited_by) <> 'controller';
    SELECT RAISE(ABORT, 'A cost controller cannot reopen their own hours')
    WHERE NEW.status = 'Draft' AND OLD.status <> 'Draft'
      AND NEW.reopen_reason IS NOT NULL AND NEW.reopen_reason IS NOT OLD.reopen_reason
      AND NEW.last_edited_by = NEW.user_id;

    -- approval: an approver of the scope (the client's main approver or a secondary approver), never the entry owner. If the booker is
    -- the only approver there is nobody else, so a cost controller approves those hours. Admins never approve.
    SELECT RAISE(ABORT, 'Only an approver of the scope can approve (and not their own entry); a cost controller approves hours when the booker is the only approver')
    WHERE NEW.status = 'Approved' AND OLD.status <> 'Approved'
      AND (NEW.approved_by IS NULL
           OR NEW.approved_by = NEW.user_id
           OR NOT (
                (EXISTS (SELECT 1 FROM v_scope_approver WHERE scope_id = NEW.scope_id AND user_id = NEW.approved_by)
                 AND (SELECT permission FROM app_user WHERE user_id = NEW.approved_by) = 'user')
                OR
                ((SELECT permission FROM app_user WHERE user_id = NEW.approved_by) = 'controller'
                 AND EXISTS (SELECT 1 FROM v_scope_approver WHERE scope_id = NEW.scope_id AND user_id = NEW.user_id)
                 AND NOT EXISTS (SELECT 1 FROM v_scope_approver WHERE scope_id = NEW.scope_id AND user_id <> NEW.user_id))));
END;

-- The audit trail is append-only: rows can be added (by the triggers below) but never changed or removed.
CREATE TRIGGER trg_audit_no_update BEFORE UPDATE ON time_entry_audit
BEGIN
    SELECT RAISE(ABORT, 'The audit trail cannot be changed');
END;
CREATE TRIGGER trg_audit_no_delete BEFORE DELETE ON time_entry_audit
BEGIN
    SELECT RAISE(ABORT, 'The audit trail cannot be deleted');
END;

CREATE TRIGGER trg_time_entry_audit_insert
AFTER INSERT ON time_entry
BEGIN
    INSERT INTO time_entry_audit(entry_id, action, changed_by, changed_at, new_status, new_hours)
    VALUES (NEW.entry_id, 'CREATE', NEW.created_by, NEW.created_at, NEW.status, NEW.hours);
END;

CREATE TRIGGER trg_time_entry_audit_update
AFTER UPDATE ON time_entry
BEGIN
    INSERT INTO time_entry_audit(entry_id, action, changed_by, changed_at, reason,
        old_status, new_status, old_hours, new_hours, old_scope_id, old_phase_id, old_worked_role_id, old_rate_id, old_work_date,
        old_location_id, old_overtime)
    VALUES (OLD.entry_id,
        CASE WHEN OLD.status <> NEW.status THEN 'STATUS' ELSE 'UPDATE' END,
        NEW.last_edited_by, NEW.last_edited_at,
        CASE WHEN NEW.status = 'Draft' AND OLD.status <> 'Draft' AND NEW.reopen_reason IS NOT NULL AND NEW.reopen_reason IS NOT OLD.reopen_reason
                  THEN 'Reopened: ' || NEW.reopen_reason
             WHEN NEW.status = 'Draft' AND OLD.status = 'Submitted' THEN NEW.reject_reason
             WHEN NEW.status = 'Edited' THEN NEW.last_change_reason END,
        OLD.status, NEW.status, OLD.hours, NEW.hours,
        OLD.scope_id, OLD.phase_id, OLD.worked_role_id, OLD.rate_id, OLD.work_date, OLD.location_id, OLD.overtime);
END;

-- Only Draft entries can be deleted ("clear draft"), and the deleter must be named first
-- (set last_edited_by / last_edited_at, then delete). The deletion is audited.
CREATE TRIGGER trg_time_entry_delete_rules
BEFORE DELETE ON time_entry
BEGIN
    SELECT RAISE(ABORT, 'Only Draft entries can be deleted')
    WHERE OLD.status <> 'Draft';
    SELECT RAISE(ABORT, 'Deleting an entry must name the user (set last_edited_by and last_edited_at first)')
    WHERE OLD.last_edited_by IS NULL OR OLD.last_edited_at IS NULL;
END;

CREATE TRIGGER trg_time_entry_audit_delete
AFTER DELETE ON time_entry
BEGIN
    INSERT INTO time_entry_audit(entry_id, action, changed_by, changed_at, reason,
        old_status, old_hours, old_scope_id, old_phase_id, old_worked_role_id, old_rate_id, old_work_date, old_location_id, old_overtime)
    VALUES (OLD.entry_id, 'DELETE', OLD.last_edited_by, OLD.last_edited_at, 'Draft cleared',
        OLD.status, OLD.hours, OLD.scope_id, OLD.phase_id, OLD.worked_role_id, OLD.rate_id, OLD.work_date, OLD.location_id, OLD.overtime);
END;

-- CTR (Cost Time Resource): per phase, one line per role: hours x sales rate = total.
-- Totals count Approved entries only; pending_hours = Submitted + Edited awaiting approval.
CREATE VIEW v_ctr AS
SELECT c.name AS client, p.project_code, p.name AS project, s.scope_code, s.description AS scope,
       ph.phase_id, s.scope_code || '-' || COALESCE(ph.ctr_code, UPPER(ph.name)) AS phase, r.skill_code, r.name AS role,
       SUM(CASE WHEN t.status = 'Approved' THEN t.hours ELSE 0 END) AS hours,
       rt.sales_rate,
       SUM(CASE WHEN t.status = 'Approved' THEN t.hours * rt.sales_rate ELSE 0 END) AS total,
       SUM(CASE WHEN t.status IN ('Submitted','Edited') THEN t.hours ELSE 0 END) AS pending_hours
FROM time_entry t
JOIN phase ph   ON ph.phase_id = t.phase_id
JOIN scope s    ON s.scope_id = ph.scope_id
JOIN project p  ON p.project_id = s.project_id
JOIN client c   ON c.client_id = s.client_id
JOIN rate rt    ON rt.rate_id = t.rate_id
JOIN role r     ON r.role_id = rt.role_id
WHERE t.status <> 'Draft'
GROUP BY ph.phase_id, rt.rate_id;

-- Weekly period report (approved hours). Filter by week_start BETWEEN two dates for a date range.
CREATE VIEW v_period_report AS
SELECT date(t.work_date, 'weekday 0', '-6 days') AS week_start,
       c.name AS client, p.name AS project, s.scope_code, s.scope_code || '-' || COALESCE(ph.ctr_code, UPPER(ph.name)) AS phase, r.skill_code, r.name AS role,
       SUM(t.hours) AS hours, SUM(t.hours * rt.sales_rate) AS total
FROM time_entry t
JOIN phase ph ON ph.phase_id = t.phase_id
JOIN scope s  ON s.scope_id = ph.scope_id
JOIN project p ON p.project_id = s.project_id
JOIN client c ON c.client_id = s.client_id
JOIN rate rt  ON rt.rate_id = t.rate_id
JOIN role r   ON r.role_id = rt.role_id
WHERE t.status = 'Approved'
GROUP BY week_start, ph.phase_id, rt.rate_id;

-- Entries waiting on each approver (one row per approver who may approve the entry).
CREATE VIEW v_approver_queue AS
SELECT va.user_id AS approver_user_id, s.scope_code, u.name AS booked_by, t.work_date, t.hours, t.status, t.entry_id
FROM time_entry t JOIN scope s ON s.scope_id = t.scope_id JOIN v_scope_approver va ON va.scope_id = t.scope_id AND va.user_id <> t.user_id
JOIN app_user u ON u.user_id = t.user_id
WHERE t.status IN ('Submitted','Edited');

-- ---------------------------------------------------------------------------------------------------------------------------
-- Allowances: the offshore allowance is paid per night spent offshore, to staff only (contractors have it in their rate). Nothing is
-- claimed or stored: the nights are worked out from the hours (see v_offshore_night), so they follow the hours' status and approval.
-- ---------------------------------------------------------------------------------------------------------------------------
CREATE TABLE allowance_type (
    allowance_id INTEGER PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    active       INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1))
);

-- One person cannot book more than 24 hours on one day, however many rows (codes, locations, overtime) the hours are spread over.
CREATE TRIGGER trg_time_entry_day_cap_insert
BEFORE INSERT ON time_entry
BEGIN
    SELECT RAISE(ABORT, 'A person cannot book more than 24 hours in one day')
    WHERE COALESCE((SELECT SUM(hours) FROM time_entry WHERE user_id = NEW.user_id AND work_date = NEW.work_date), 0) + NEW.hours > 24;
END;
CREATE TRIGGER trg_time_entry_day_cap_update
BEFORE UPDATE OF hours, work_date ON time_entry
BEGIN
    SELECT RAISE(ABORT, 'A person cannot book more than 24 hours in one day')
    WHERE COALESCE((SELECT SUM(hours) FROM time_entry WHERE user_id = NEW.user_id AND work_date = NEW.work_date AND entry_id <> NEW.entry_id), 0) + NEW.hours > 24;
END;

-- Allowance rates: what one day of an allowance is worth. Append-only like the client rate cards: a change is a new row with a later
-- effective date, so past claims keep their value.
CREATE TABLE allowance_rate (
    allowance_rate_id INTEGER PRIMARY KEY,
    allowance_id   INTEGER NOT NULL REFERENCES allowance_type(allowance_id),
    amount         NUMERIC NOT NULL CHECK (amount >= 0),
    effective_from TEXT NOT NULL,
    UNIQUE (allowance_id, effective_from)
);
CREATE TRIGGER trg_allowance_rate_no_update BEFORE UPDATE ON allowance_rate
BEGIN
    SELECT RAISE(ABORT, 'Allowance rates are never edited: add a new row with a later effective date');
END;
CREATE TRIGGER trg_allowance_rate_no_delete BEFORE DELETE ON allowance_rate
BEGIN
    SELECT RAISE(ABORT, 'Allowance rates are never deleted: add a new row with a later effective date');
END;

-- The nights spent offshore, worked out from the hours. A night belongs to the day before it (the night after Monday is Monday's) and
-- is earned when a member of staff has hours at a location that earns the allowance (Offshore) on that day AND on the next day, in this
-- week or the next: the last offshore day of a trip earns no night, so Monday to Sunday offshore is 6 nights and a single offshore day is none.
-- It is charged to the CTR with most offshore hours that day, and is Approved only when those hours are approved, Submitted when they
-- have been submitted, and Draft before that. Contractors earn none.
CREATE VIEW v_offshore_night AS
WITH day_ctr AS (
    SELECT t.user_id, t.work_date, t.scope_id, t.phase_id, SUM(t.hours) AS hours,
           MIN(CASE t.status WHEN 'Draft' THEN 0 WHEN 'Approved' THEN 2 ELSE 1 END) AS lvl
    FROM time_entry t JOIN location l ON l.location_id = t.location_id AND l.earns_allowance = 1
    GROUP BY t.user_id, t.work_date, t.scope_id, t.phase_id),
ranked AS (
    SELECT d.*, ROW_NUMBER() OVER (PARTITION BY d.user_id, d.work_date ORDER BY d.hours DESC, d.phase_id) AS rn FROM day_ctr d)
SELECT r.user_id, r.work_date, r.scope_id, r.phase_id,
       CASE r.lvl WHEN 0 THEN 'Draft' WHEN 1 THEN 'Submitted' ELSE 'Approved' END AS status
FROM ranked r JOIN app_user u ON u.user_id = r.user_id AND u.employment_type = 'Staff'
WHERE r.rn = 1 AND EXISTS (SELECT 1 FROM day_ctr n WHERE n.user_id = r.user_id AND n.work_date = date(r.work_date, '+1 day'));

-- Each night with its value: the allowance rate in force on that night (the first active allowance, the offshore allowance). No rate means 0.
CREATE VIEW v_allowance_value AS
SELECT n.user_id, n.scope_id, n.phase_id, a.allowance_id, n.work_date, n.status,
       COALESCE((SELECT r.amount FROM allowance_rate r WHERE r.allowance_id = a.allowance_id AND r.effective_from <= n.work_date
                 ORDER BY r.effective_from DESC LIMIT 1), 0) AS amount
FROM v_offshore_night n JOIN (SELECT allowance_id FROM allowance_type WHERE active = 1 ORDER BY allowance_id LIMIT 1) a;

-- Budget hours per CTR, as held in P6 (P6 holds hours only, no money). Cost controllers load them from a P6 export; each load is a snapshot
-- with an as-of date and is never edited or deleted, so you can see how a budget moved. The latest snapshot is the one compared.
CREATE TABLE ctr_budget (
    budget_id INTEGER PRIMARY KEY,
    phase_id  INTEGER NOT NULL REFERENCES phase(phase_id),
    hours     NUMERIC NOT NULL CHECK (hours >= 0),
    as_of     TEXT NOT NULL,
    p6_actual_hours  NUMERIC,   -- P6's own actual hours and physical % complete on that date, when loaded from a P6 export
    percent_complete NUMERIC,
    p6_project       TEXT,      -- the P6 project the figures came from
    loaded_by INTEGER REFERENCES app_user(user_id),
    loaded_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (phase_id, as_of)
);
CREATE TRIGGER trg_ctr_budget_no_update BEFORE UPDATE ON ctr_budget
BEGIN
    SELECT RAISE(ABORT, 'Budgets are never edited: load a new snapshot with a later as-of date');
END;
CREATE TRIGGER trg_ctr_budget_no_delete BEFORE DELETE ON ctr_budget
BEGIN
    SELECT RAISE(ABORT, 'Budgets are never deleted: load a new snapshot with a later as-of date');
END;

-- Budget against actual, hours only, for every CTR of a client (not Internal). Actual = Approved hours; pending = Submitted or Edited.
-- budget_hours is NULL when no budget has been loaded for the CTR.
CREATE VIEW v_ctr_budget AS
SELECT ph.phase_id, c.name AS client, p.project_code, s.scope_code, s.scope_code || '-' || COALESCE(ph.ctr_code, UPPER(ph.name)) AS phase,
       b.hours AS budget_hours, b.as_of,
       COALESCE((SELECT SUM(t.hours) FROM time_entry t WHERE t.phase_id = ph.phase_id AND t.status = 'Approved'), 0) AS actual_hours,
       COALESCE((SELECT SUM(t.hours) FROM time_entry t WHERE t.phase_id = ph.phase_id AND t.status IN ('Submitted','Edited')), 0) AS pending_hours
FROM phase ph
JOIN scope s   ON s.scope_id = ph.scope_id
JOIN project p ON p.project_id = s.project_id
JOIN client c  ON c.client_id = s.client_id AND c.is_internal = 0
LEFT JOIN ctr_budget b ON b.phase_id = ph.phase_id
     AND b.as_of = (SELECT MAX(x.as_of) FROM ctr_budget x WHERE x.phase_id = ph.phase_id);
