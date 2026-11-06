-- Fake seed data for the v1 mock database. All names, rates and codes are examples.

INSERT INTO role (role_id, skill_code, name) VALUES
 (1,'PM-01','Project Manager'),
 (2,'EM-01','Engineering Manager'),
 (3,'PE-01','Project Engineer'),
 (4,'PL-01','Senior Planner'),
 (5,'PL-02','Lead Planner'),
 (6,'CE-01','Cost Engineer');

INSERT INTO client (client_id, name) VALUES (1,'Apache'),(2,'Spirit');

-- Rate cards. Apache rate_id 1-6 (roles 1-6), Spirit rate_id 7-12 (roles 1-6).
-- Seniority order (low to high): Cost Engineer < Senior Planner < Project Engineer < Lead Planner < Engineering Manager < Project Manager.
INSERT INTO rate (rate_id, client_id, role_id, sales_rate) VALUES
 (1,1,1,150.00),(2,1,2,130.00),(3,1,3,105.00),(4,1,4, 95.00),(5,1,5,115.00),(6,1,6, 85.00),
 (7,2,1,165.00),(8,2,2,143.00),(9,2,3,115.50),(10,2,4,104.50),(11,2,5,126.50),(12,2,6, 93.50);

INSERT INTO app_user (user_id, nmc_id, employment_type, name, role_id, permission, start_date, end_date) VALUES
 (1, 'A001','Staff',   'Isla Fraser',       6,'user', '2026-01-01',NULL),
 (2, 'A002','Staff',   'Callum Reid',       6,'user', '2026-01-01',NULL),
 (3, 'A003','Contract','Morag Sinclair',    5,'user', '2026-01-01',NULL),
 (4, 'A004','Staff',   'Fraser Gordon',     4,'user', '2026-01-01',NULL),
 (5, 'A005','Contract','Ewan Mackay',       4,'user', '2026-03-01',NULL),
 (6, 'A006','Staff',   'Rhona Campbell',    1,'user', '2026-01-01',NULL),
 (7, 'A007','Staff',   'Alasdair Murray',   2,'admin','2026-01-01',NULL),
 (8, 'A008','Staff',   'Niall Robertson',   1,'user', '2026-01-01',NULL),
 (9, 'A009','Staff',   'Gregor Munro',      3,'user', '2026-02-01',NULL),
 (10,'A010','Staff',   'Catriona Duff',     4,'user', '2026-02-01',NULL);

-- PAAFs: one per user per client. Rate = client sales rate for the user's own role.
INSERT INTO paaf (paaf_id, user_id, client_id, rate_id, status, sent_date, decided_date, document_ref) VALUES
 (1, 5, 1, 4, 'Approved','2026-03-02','2026-03-09','paaf/apache/A005_Ewan_Mackay.pdf'),
 (2, 3, 1, 5, 'Approved','2026-01-05','2026-01-12','paaf/apache/A003_Morag_Sinclair.pdf'),
 (3, 1, 1, 6, 'Approved','2026-01-05','2026-01-12','paaf/apache/A001_Isla_Fraser.pdf'),
 (4, 4, 1, 4, 'Sent',    '2026-11-04',NULL,        NULL),
 (5, 1, 2, 12,'Approved','2026-01-06','2026-01-14','paaf/spirit/A001_Isla_Fraser.pdf'),
 (6, 3, 2, 11,'Approved','2026-01-06','2026-01-14','paaf/spirit/A003_Morag_Sinclair.pdf'),
 (7, 2, 2, 12,'Draft',   NULL,        NULL,        NULL),
 (8, 9, 1, 3, 'Approved','2026-02-02','2026-02-10','paaf/apache/A009_Gregor_Munro.pdf'),
 (9, 9, 2, 9, 'Approved','2026-02-02','2026-02-11','paaf/spirit/A009_Gregor_Munro.pdf'),
 (10,10,2, 10,'Approved','2026-02-02','2026-02-11','paaf/spirit/A010_Catriona_Duff.pdf');

INSERT INTO project (project_id, project_code, name, client_id) VALUES
 (1,'APJ-01','Wellbay Decommissioning',1),
 (2,'APJ-02','Platform Decommissioning',1),
 (3,'SPJ-01','Morecambe Hub',2);

INSERT INTO scope (scope_id, scope_code, client_id, project_id, description, status, approver_user_id) VALUES
 (1,'BB0001',1,1,'Wellbay Clearance','Open',    6),
 (2,'BB0002',1,2,'Platform Study',   'Open',    7),
 (3,'FB0001',1,2,'EDC Engineering',  'Closed',  7),
 (4,'A1400', 2,3,'Rivers and Calder','Pipeline',8),
 (5,'A1500', 2,3,'PGC & NRU',        'Open',    8),
 (6,'A1600', 2,3,'Pipeline Removal', 'Open',    7);

-- Phases. Hours are booked to a phase. Every scope has at least one.
INSERT INTO phase (phase_id, scope_id, name) VALUES
 (1,1,'Define'),(2,1,'Execute'),(3,2,'Define'),(4,3,'Define'),(5,4,'Define'),(6,5,'Define'),(7,6,'Define');

-- Weekly periods: week of 26 Oct (to be closed later) and week of 2 Nov.
INSERT INTO period (week_start) VALUES ('2026-10-26'),('2026-11-02');

-- Time entries. Columns: user, scope, phase, paaf, worked role, rate, date, hours, created_by.
INSERT INTO time_entry (user_id, scope_id, phase_id, paaf_id, worked_role_id, rate_id, work_date, hours, created_by) VALUES
 -- Week of 26 Oct
 (5,1,1,1,4,4,  '2026-10-26',8,5),
 (5,1,1,1,4,4,  '2026-10-27',8,5),
 (3,1,1,2,5,5,  '2026-10-28',8,3),
 (1,5,6,5,6,12, '2026-10-28',8,1),
 (9,2,3,8,3,3,  '2026-10-29',8,9),
 (10,5,6,10,4,10,'2026-10-27',8,10),
 -- Week of 2 Nov. Ewan Mackay (Senior Planner): own role Mon-Tue, covering Lead Planner Wed-Thu
 (5,1,1,1,4,4, '2026-11-02',8,5),
 (5,1,1,1,4,4, '2026-11-03',8,5),
 (5,1,1,1,5,5, '2026-11-04',8,5),
 (5,1,1,1,5,5, '2026-11-05',8,5),
 -- Morag Sinclair (Lead Planner): two Apache scopes in one day, then Spirit
 (3,1,1,2,5,5, '2026-11-02',6,3),
 (3,2,3,2,5,5, '2026-11-02',2,3),
 (3,5,6,6,5,11,'2026-11-03',8,3),
 (3,5,6,6,5,11,'2026-11-04',8,3),
 (3,2,3,2,5,5, '2026-11-05',4,3),
 (3,6,7,6,5,11,'2026-11-05',4,3),
 -- Isla Fraser (Cost Engineer): Apache and Spirit
 (1,1,1,3,6,6, '2026-11-02',4,1),
 (1,5,6,5,6,12,'2026-11-02',4,1),
 (1,6,7,5,6,12,'2026-11-03',8,1),
 (1,2,3,3,6,6, '2026-11-04',8,1),
 (1,1,1,3,6,6, '2026-11-05',8,1),
 -- Gregor Munro (Project Engineer): Apache and Spirit
 (9,1,1,8,3,3, '2026-11-02',8,9),
 (9,5,6,9,3,9, '2026-11-03',8,9),
 (9,2,3,8,3,3, '2026-11-04',8,9),
 (9,6,7,9,3,9, '2026-11-05',8,9),
 -- Catriona Duff (Senior Planner): Spirit, covering Lead Planner on Wednesday
 (10,5,6,10,4,10,'2026-11-02',8,10),
 (10,5,6,10,4,10,'2026-11-03',8,10),
 (10,6,7,10,5,11,'2026-11-04',8,10),
 -- Execute phase of BB0001: Gregor Munro (Project Engineer)
 (9,1,2,8,3,3,'2026-11-03',8,9);

-- Approvals are edits, so they name who did it (and are written to time_entry_audit).
-- Week of 26 Oct: approved by each scope's approver.
UPDATE time_entry SET status='Approved', approved_by=6, approved_at='2026-11-02 09:00',
       last_edited_by=6, last_edited_at='2026-11-02 09:00' WHERE scope_id=1 AND work_date < '2026-11-02';
UPDATE time_entry SET status='Approved', approved_by=7, approved_at='2026-11-02 09:05',
       last_edited_by=7, last_edited_at='2026-11-02 09:05' WHERE scope_id=2 AND work_date < '2026-11-02';
UPDATE time_entry SET status='Approved', approved_by=8, approved_at='2026-11-02 09:10',
       last_edited_by=8, last_edited_at='2026-11-02 09:10' WHERE scope_id=5 AND work_date < '2026-11-02';

-- Week of 2 Nov: partly approved.
UPDATE time_entry SET status='Approved', approved_by=6, approved_at='2026-11-06 09:00',
       last_edited_by=6, last_edited_at='2026-11-06 09:00'
 WHERE scope_id=1 AND work_date IN ('2026-11-02','2026-11-03');
UPDATE time_entry SET status='Approved', approved_by=8, approved_at='2026-11-06 09:15',
       last_edited_by=8, last_edited_at='2026-11-06 09:15'
 WHERE scope_id=5 AND work_date >= '2026-11-02';

-- Close the week of 26 Oct.
UPDATE period SET status='Closed', closed_by=7, closed_at='2026-11-02 12:00' WHERE week_start='2026-10-26';

-- Controlled edit after the period closed: Approved -> Edited (reason required), then re-approved.
UPDATE time_entry SET hours=7.5, status='Edited', edit_count=edit_count+1,
       last_change_reason='Hours corrected after Apache query on 27 Oct',
       last_edited_by=7, last_edited_at='2026-11-04 10:00'
 WHERE user_id=5 AND work_date='2026-10-27';
UPDATE time_entry SET status='Approved', approved_by=6, approved_at='2026-11-04 14:00',
       last_edited_by=6, last_edited_at='2026-11-04 14:00'
 WHERE user_id=5 AND work_date='2026-10-27';

-- Owner corrects an approved entry this week: now Edited, waiting for re-approval.
UPDATE time_entry SET hours=7, status='Edited', edit_count=edit_count+1,
       last_change_reason='Corrected hours per site log',
       last_edited_by=3, last_edited_at='2026-11-06 09:30'
 WHERE user_id=3 AND scope_id=1 AND work_date='2026-11-02';

-- Rejected: back to Draft with the reason stored.
UPDATE time_entry SET status='Draft', reject_reason='Lead Planner cover not agreed with client, please confirm',
       last_edited_by=7, last_edited_at='2026-11-06 10:00'
 WHERE user_id=10 AND work_date='2026-11-04';
