-- NMC Timewriting System - v1 mock database (SQLite)
PRAGMA foreign_keys = ON;
-- Schema version. The app refuses to start on a database with a lower number (see REQUIRED_SCHEMA in app/app.py),
-- so raise both together whenever an older database could no longer work with the app.
PRAGMA user_version = 3;  -- 2: a cost controller approves the hours a scope approver books to their own scope
                          -- 3: hours can only be booked within a person's start and end dates, and before a PAAF expires

-- Controlled list of job titles, grouped into job families. Editable by admin/superuser.
CREATE TABLE role (
    role_id     INTEGER PRIMARY KEY,
    skill_code  TEXT NOT NULL UNIQUE,               -- e.g. PM-04
    name        TEXT NOT NULL UNIQUE,               -- job title
    family      TEXT NOT NULL,                      -- job family, e.g. Project Management
    active      INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1))
);

-- is_internal = 1 marks NMC's own "Internal" client: hours booked to its codes are paid by NMC, not billed to a client.
-- They need no PAAF and have no rate, and are left out of the client CTR and period reports.
CREATE TABLE client (
    client_id   INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    is_internal INTEGER NOT NULL DEFAULT 0 CHECK (is_internal IN (0,1))
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
    end_date        TEXT
);

-- Personnel Authorisation Approval Form: user x client, rate resolved from user's role.
CREATE TABLE paaf (
    paaf_id     INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES app_user(user_id),
    client_id   INTEGER NOT NULL REFERENCES client(client_id),
    rate_id     INTEGER NOT NULL REFERENCES rate(rate_id),
    status      TEXT NOT NULL DEFAULT 'Draft' CHECK (status IN ('Draft','Sent','Approved','Rejected')),
    sent_date   TEXT,
    decided_date TEXT,
    document_ref TEXT,                               -- link/path to signed client form
    expiry_date  TEXT,                               -- NULL = open-ended
    UNIQUE (user_id, client_id)
);

-- Hierarchy: client > project > scope > phase. Hours are booked to a phase (the lowest level).
-- The approver sits on the scope.
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
    status            TEXT NOT NULL CHECK (status IN ('Pipeline','Open','Closed')),
    approver_user_id  INTEGER NOT NULL REFERENCES app_user(user_id)
);

-- A scope is split into phases (e.g. Define, Execute). Every scope has at least one.
CREATE TABLE phase (
    phase_id   INTEGER PRIMARY KEY,
    scope_id   INTEGER NOT NULL REFERENCES scope(scope_id),
    name       TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'Open' CHECK (status IN ('Open','Closed')),
    UNIQUE (scope_id, name)
);

-- Approver is not a permission: it means being named as approver on a scope. Only standard users can be named.
-- Cost controllers and admins never approve, so they cannot be approvers.
CREATE TRIGGER trg_scope_approver_insert BEFORE INSERT ON scope
BEGIN
    SELECT RAISE(ABORT, 'Only a standard user can be named as a scope approver')
    WHERE (SELECT permission FROM app_user WHERE user_id = NEW.approver_user_id) <> 'user';
END;
CREATE TRIGGER trg_scope_approver_update BEFORE UPDATE OF approver_user_id ON scope
BEGIN
    SELECT RAISE(ABORT, 'Only a standard user can be named as a scope approver')
    WHERE (SELECT permission FROM app_user WHERE user_id = NEW.approver_user_id) <> 'user';
END;
CREATE TRIGGER trg_user_permission_update BEFORE UPDATE OF permission ON app_user
BEGIN
    SELECT RAISE(ABORT, 'Remove this person as scope approver before making them a controller or admin')
    WHERE NEW.permission <> 'user' AND EXISTS (SELECT 1 FROM scope WHERE approver_user_id = NEW.user_id);
END;

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

-- The internal client has no rate card and needs no PAAFs.
CREATE TRIGGER trg_rate_not_internal BEFORE INSERT ON rate
BEGIN
    SELECT RAISE(ABORT, 'Internal codes have no rate card')
    WHERE (SELECT is_internal FROM client WHERE client_id = NEW.client_id) = 1;
END;
CREATE TRIGGER trg_paaf_not_internal BEFORE INSERT ON paaf
BEGIN
    SELECT RAISE(ABORT, 'Internal codes do not need a PAAF')
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

-- Lifecycle: Draft -> Submitted -> Approved. Reject sends Submitted back to Draft (reason stored).
-- Editing an Approved entry makes it Edited (reason required) and it needs re-approval.
CREATE TABLE time_entry (
    entry_id    INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES app_user(user_id),
    scope_id    INTEGER NOT NULL REFERENCES scope(scope_id),
    phase_id    INTEGER NOT NULL REFERENCES phase(phase_id), -- lowest booking level; must belong to scope_id
    paaf_id     INTEGER REFERENCES paaf(paaf_id),            -- required for client work, NULL for internal hours
    worked_role_id INTEGER NOT NULL REFERENCES role(role_id), -- skill code booked under (defaults to user's role)
    rate_id     INTEGER REFERENCES rate(rate_id),            -- client sales rate for worked role, snapshot at booking; NULL for internal hours
    work_date   TEXT NOT NULL,
    hours       NUMERIC NOT NULL CHECK (hours > 0 AND hours <= 24),
    status      TEXT NOT NULL DEFAULT 'Submitted' CHECK (status IN ('Draft','Submitted','Approved','Edited')),
    approved_by INTEGER REFERENCES app_user(user_id),
    approved_at TEXT,
    reject_reason      TEXT,                          -- set when sent back to Draft
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
    old_scope_id INTEGER, old_phase_id INTEGER, old_worked_role_id INTEGER, old_rate_id INTEGER, old_work_date TEXT
);

-- Booking guard: Open scope, Approved PAAF for the scope's client, matching rate, Open weekly period.
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
    SELECT RAISE(ABORT, 'Phase does not belong to this scope')
    WHERE NOT EXISTS (SELECT 1 FROM phase WHERE phase_id = NEW.phase_id AND scope_id = NEW.scope_id);
    SELECT RAISE(ABORT, 'Project is not Open')
    WHERE (SELECT pr.status FROM scope sc JOIN project pr ON pr.project_id = sc.project_id WHERE sc.scope_id = NEW.scope_id) <> 'Open';
    SELECT RAISE(ABORT, 'Phase is not Open')
    WHERE (SELECT status FROM phase WHERE phase_id = NEW.phase_id) <> 'Open';
    -- Client work: needs an approved PAAF and a matching rate. Internal hours: neither.
    SELECT RAISE(ABORT, 'No approved PAAF for this user and client')
    WHERE (SELECT c.is_internal FROM scope sc JOIN client c ON c.client_id = sc.client_id WHERE sc.scope_id = NEW.scope_id) = 0
      AND NOT EXISTS (
        SELECT 1 FROM paaf p JOIN scope s ON s.client_id = p.client_id
        WHERE p.paaf_id = NEW.paaf_id AND p.user_id = NEW.user_id
          AND s.scope_id = NEW.scope_id AND p.status = 'Approved');
    -- A PAAF with an expiry date stops covering hours worked after that date (no expiry date = open-ended).
    SELECT RAISE(ABORT, 'The PAAF has expired for that date')
    WHERE EXISTS (SELECT 1 FROM paaf p WHERE p.paaf_id = NEW.paaf_id AND p.expiry_date IS NOT NULL AND NEW.work_date > p.expiry_date);
    SELECT RAISE(ABORT, 'Rate does not match the scope client and worked role')
    WHERE (SELECT c.is_internal FROM scope sc JOIN client c ON c.client_id = sc.client_id WHERE sc.scope_id = NEW.scope_id) = 0
      AND NOT EXISTS (
        SELECT 1 FROM rate r JOIN scope s ON s.client_id = r.client_id
        WHERE r.rate_id = NEW.rate_id AND s.scope_id = NEW.scope_id
          AND r.role_id = NEW.worked_role_id);
    SELECT RAISE(ABORT, 'Internal hours have no PAAF and no rate')
    WHERE (SELECT c.is_internal FROM scope sc JOIN client c ON c.client_id = sc.client_id WHERE sc.scope_id = NEW.scope_id) = 1
      AND (NEW.paaf_id IS NOT NULL OR NEW.rate_id IS NOT NULL);
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

    SELECT RAISE(ABORT, 'Phase does not belong to this scope')
    WHERE NOT EXISTS (SELECT 1 FROM phase WHERE phase_id = NEW.phase_id AND scope_id = NEW.scope_id);

    -- changing an approved entry makes it Edited
    SELECT RAISE(ABORT, 'Changing an approved entry sets its status to Edited')
    WHERE OLD.status IN ('Approved','Edited') AND NEW.status <> 'Edited'
      AND (NEW.hours IS NOT OLD.hours OR NEW.scope_id IS NOT OLD.scope_id OR NEW.phase_id IS NOT OLD.phase_id OR NEW.work_date IS NOT OLD.work_date
           OR NEW.worked_role_id IS NOT OLD.worked_role_id OR NEW.rate_id IS NOT OLD.rate_id
           OR NEW.user_id IS NOT OLD.user_id)
      AND NOT (OLD.status = 'Edited' AND NEW.status = 'Approved');

    -- controlled edits need a reason
    SELECT RAISE(ABORT, 'A controlled edit needs a reason (last_change_reason)')
    WHERE NEW.status = 'Edited' AND OLD.status IN ('Approved','Edited')
      AND (NEW.last_change_reason IS NULL OR NEW.last_change_reason = ''
           OR NEW.last_change_reason IS OLD.last_change_reason);

    -- reject (Submitted -> Draft) needs a reason
    SELECT RAISE(ABORT, 'Rejecting an entry needs a reason (reject_reason)')
    WHERE OLD.status = 'Submitted' AND NEW.status = 'Draft'
      AND (NEW.reject_reason IS NULL OR NEW.reject_reason = '');

    -- approval: the person named as the scope's approver, never the entry owner. One exception: hours booked BY the scope's
    -- approver have nobody else who may approve them, so a cost controller approves those. Admins never approve.
    SELECT RAISE(ABORT, 'Only the scope approver can approve (and not their own entry); a cost controller approves hours booked by the scope approver')
    WHERE NEW.status = 'Approved' AND OLD.status <> 'Approved'
      AND (NEW.approved_by IS NULL
           OR NEW.approved_by = NEW.user_id
           OR NOT (
                (NEW.approved_by = (SELECT approver_user_id FROM scope WHERE scope_id = NEW.scope_id)
                 AND (SELECT permission FROM app_user WHERE user_id = NEW.approved_by) = 'user')
                OR
                ((SELECT permission FROM app_user WHERE user_id = NEW.approved_by) = 'controller'
                 AND NEW.user_id = (SELECT approver_user_id FROM scope WHERE scope_id = NEW.scope_id))));
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
        old_status, new_status, old_hours, new_hours, old_scope_id, old_phase_id, old_worked_role_id, old_rate_id, old_work_date)
    VALUES (OLD.entry_id,
        CASE WHEN OLD.status <> NEW.status THEN 'STATUS' ELSE 'UPDATE' END,
        NEW.last_edited_by, NEW.last_edited_at,
        CASE WHEN NEW.status = 'Draft' AND OLD.status = 'Submitted' THEN NEW.reject_reason
             WHEN NEW.status = 'Edited' THEN NEW.last_change_reason END,
        OLD.status, NEW.status, OLD.hours, NEW.hours,
        OLD.scope_id, OLD.phase_id, OLD.worked_role_id, OLD.rate_id, OLD.work_date);
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
        old_status, old_hours, old_scope_id, old_phase_id, old_worked_role_id, old_rate_id, old_work_date)
    VALUES (OLD.entry_id, 'DELETE', OLD.last_edited_by, OLD.last_edited_at, 'Draft cleared',
        OLD.status, OLD.hours, OLD.scope_id, OLD.phase_id, OLD.worked_role_id, OLD.rate_id, OLD.work_date);
END;

-- CTR (Cost Time Resource): per phase, one line per role: hours x sales rate = total.
-- Totals count Approved entries only; pending_hours = Submitted + Edited awaiting approval.
CREATE VIEW v_ctr AS
SELECT c.name AS client, p.project_code, p.name AS project, s.scope_code, s.description AS scope,
       ph.phase_id, ph.name AS phase, r.skill_code, r.name AS role,
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
       c.name AS client, p.name AS project, s.scope_code, ph.name AS phase, r.skill_code, r.name AS role,
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

-- Entries waiting on each scope approver.
CREATE VIEW v_approver_queue AS
SELECT s.approver_user_id, s.scope_code, u.name AS booked_by, t.work_date, t.hours, t.status, t.entry_id
FROM time_entry t JOIN scope s ON s.scope_id = t.scope_id JOIN app_user u ON u.user_id = t.user_id
WHERE t.status IN ('Submitted','Edited');
