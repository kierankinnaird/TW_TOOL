-- Fake seed data for the v1 mock database. All names, rates and codes are examples.

-- Job titles come from the resource tables in the Morecambe Hub estimate (NMC-A0100-PM-EST-0001), grouped into job families.
INSERT INTO role (role_id, skill_code, name, family) VALUES
 (1,'PM-01','Project Manager','Project Management'),
 (2,'EM-02','Engineering Manager','Engineering Management'),
 (3,'PE-01','Project Engineer','Project Engineering'),
 (4,'PM-04','Planner','Project Management'),
 (5,'PM-02','Project Controls Manager','Project Management'),
 (6,'PM-06','Cost Engineer','Project Management'),
 (7,'EM-01','Operations Superintendent','Engineering Management'),
 (8,'PM-03','Project Controls Engineer','Project Management'),
 (9,'PM-05','Cost Estimator','Project Management'),
 (10,'PM-07','Document Controller','Project Management'),
 (11,'PE-02','Project Engineer - EDC','Project Engineering'),
 (12,'PE-03','Project Engineer - HLV','Project Engineering'),
 (13,'PE-04','Project Engineer - Integrity','Project Engineering'),
 (14,'PE-05','Project Engineer - Subsea','Project Engineering'),
 (15,'PR-01','Lead Process Engineer','Process Engineering'),
 (16,'PI-01','Lead Piping Engineer','Piping Engineering'),
 (17,'PI-02','Senior Designer','Piping Engineering'),
 (18,'IN-01','Lead Instrument Engineer','Instrument Engineering'),
 (19,'EL-01','Lead Electrical Engineer','Electrical Engineering'),
 (20,'ST-01','Lead Structural Engineer','Structural Engineering'),
 (21,'CN-01','Lead Construction Engineer','Construction Engineering'),
 (22,'PL-01','Subsea Pipeline Engineer','Subsea Pipelines'),
 (23,'TS-01','Lead Technical Safety Engineer','Technical Safety'),
 (24,'HS-01','HSES Advisor','HSES'),
 (25,'ME-01','Lead Mechanical Engineer','Mechanical Engineering'),
 (26,'ME-02','Senior Mechanical Engineer','Mechanical Engineering'),
 (27,'ME-03','Senior Mechanical Designer','Mechanical Engineering'),
 (28,'QC-01','Lead QA/QC Engineer','QA/QC'),
 (29,'QC-02','Senior QA/QC Inspector','QA/QC');

INSERT INTO client (client_id, name) VALUES (1,'Apache'),(2,'Spirit');
-- NMC's own client for internal hours (paid by NMC, not billed). Its codes are scopes under one project; admin can add more.
INSERT INTO client (client_id, name, is_internal) VALUES (3,'Internal',1);

-- Rate cards. Spirit rates are the estimate's GBP/hr by job title; Apache rates are example figures (Spirit x 0.95, nearest 0.50).
-- rate_id: roles 1-6 are Apache 1-6 and Spirit 7-12; roles 7-29 are Apache 13-35 and Spirit 36-58.
INSERT INTO rate (rate_id, client_id, role_id, sales_rate) VALUES
 (1,1,1,114.00),
 (2,1,2,104.50),
 (3,1,3,95.00),
 (4,1,4,85.50),
 (5,1,5,100.00),
 (6,1,6,85.50),
 (7,2,1,120.00),
 (8,2,2,110.00),
 (9,2,3,100.00),
 (10,2,4,90.00),
 (11,2,5,105.00),
 (12,2,6,90.00),
 (13,1,7,109.00),
 (14,1,8,66.50),
 (15,1,9,95.00),
 (16,1,10,62.00),
 (17,1,11,95.00),
 (18,1,12,95.00),
 (19,1,13,95.00),
 (20,1,14,95.00),
 (21,1,15,104.50),
 (22,1,16,85.50),
 (23,1,17,64.50),
 (24,1,18,85.50),
 (25,1,19,85.50),
 (26,1,20,85.50),
 (27,1,21,85.50),
 (28,1,22,90.00),
 (29,1,23,114.00),
 (30,1,24,85.50),
 (31,1,25,85.50),
 (32,1,26,76.00),
 (33,1,27,64.50),
 (34,1,28,76.00),
 (35,1,29,62.00),
 (36,2,7,115.00),
 (37,2,8,70.00),
 (38,2,9,100.00),
 (39,2,10,65.00),
 (40,2,11,100.00),
 (41,2,12,100.00),
 (42,2,13,100.00),
 (43,2,14,100.00),
 (44,2,15,110.00),
 (45,2,16,90.00),
 (46,2,17,68.00),
 (47,2,18,90.00),
 (48,2,19,90.00),
 (49,2,20,90.00),
 (50,2,21,90.00),
 (51,2,22,95.00),
 (52,2,23,120.00),
 (53,2,24,90.00),
 (54,2,25,90.00),
 (55,2,26,80.00),
 (56,2,27,68.00),
 (57,2,28,80.00),
 (58,2,29,65.00);

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
 (10,'A010','Staff',   'Catriona Duff',     4,'user', '2026-02-01',NULL),
 (11,'A011','Staff','Hamish Lennox',7,'user','2026-01-01',NULL),
 (12,'A012','Staff','Eilidh Stewart',15,'user','2026-01-01',NULL),
 (13,'A013','Contract','Duncan Baird',16,'user','2026-01-01',NULL),
 (14,'A014','Staff','Kirsty Imrie',17,'user','2026-01-01',NULL),
 (15,'A015','Staff','Struan Fyfe',19,'user','2026-01-01',NULL),
 (16,'A016','Staff','Ailsa Menzies',18,'user','2026-01-01',NULL),
 (17,'A017','Contract','Calum Dunbar',20,'user','2026-01-01',NULL),
 (18,'A018','Staff','Rory Cattanach',21,'user','2026-01-01',NULL),
 (19,'A019','Staff','Mairi Thomson',23,'user','2026-01-01',NULL),
 (20,'A020','Contract','Lachlan Rennie',22,'user','2026-01-01',NULL),
 (21,'A021','Staff','Fiona Gillespie',11,'user','2026-01-01',NULL),
 (22,'A022','Staff','Alan Weir',13,'user','2026-01-01',NULL),
 (23,'A023','Staff','Shona Barclay',10,'user','2026-01-01',NULL),
 (24,'A024','Staff','Euan Drummond',24,'user','2026-01-01',NULL),
 (25,'A025','Staff','Bethany Cruickshank',9,'user','2026-01-01',NULL),
 (26,'A026','Contract','Murdo Ferrier',8,'user','2026-01-01',NULL),
 (27,'A027','Staff','Gavin Ross',29,'user','2026-01-01',NULL),
 (28,'A028','Staff','Isobel Hay',25,'user','2026-01-01',NULL);

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
 (10,10,2, 10,'Approved','2026-02-02','2026-02-11','paaf/spirit/A010_Catriona_Duff.pdf'),
 (11,11,1,13,'Approved','2026-02-02','2026-02-10','paaf/apache/A011.pdf'),
 (12,12,1,21,'Approved','2026-02-02','2026-02-10','paaf/apache/A012.pdf'),
 (13,13,1,22,'Approved','2026-02-02','2026-02-10','paaf/apache/A013.pdf'),
 (14,14,1,23,'Approved','2026-02-02','2026-02-10','paaf/apache/A014.pdf'),
 (15,15,1,25,'Approved','2026-02-02','2026-02-10','paaf/apache/A015.pdf'),
 (16,16,1,24,'Approved','2026-02-02','2026-02-10','paaf/apache/A016.pdf'),
 (17,17,1,26,'Approved','2026-02-02','2026-02-10','paaf/apache/A017.pdf'),
 (18,18,1,27,'Approved','2026-02-02','2026-02-10','paaf/apache/A018.pdf'),
 (19,19,1,29,'Approved','2026-02-02','2026-02-10','paaf/apache/A019.pdf'),
 (20,20,1,28,'Approved','2026-02-02','2026-02-10','paaf/apache/A020.pdf'),
 (21,21,1,17,'Approved','2026-02-02','2026-02-10','paaf/apache/A021.pdf'),
 (22,22,1,19,'Approved','2026-02-02','2026-02-10','paaf/apache/A022.pdf'),
 (23,23,1,16,'Approved','2026-02-02','2026-02-10','paaf/apache/A023.pdf'),
 (24,24,1,30,'Approved','2026-02-02','2026-02-10','paaf/apache/A024.pdf'),
 (25,26,1,14,'Approved','2026-02-02','2026-02-10','paaf/apache/A026.pdf'),
 (26,27,1,35,'Approved','2026-02-02','2026-02-10','paaf/apache/A027.pdf'),
 (27,28,1,31,'Approved','2026-02-02','2026-02-10','paaf/apache/A028.pdf'),
 (28,11,2,36,'Approved','2026-02-02','2026-02-11','paaf/spirit/A011.pdf'),
 (29,12,2,44,'Approved','2026-02-02','2026-02-11','paaf/spirit/A012.pdf'),
 (30,13,2,45,'Approved','2026-02-02','2026-02-11','paaf/spirit/A013.pdf'),
 (31,17,2,49,'Approved','2026-02-02','2026-02-11','paaf/spirit/A017.pdf'),
 (32,19,2,52,'Approved','2026-02-02','2026-02-11','paaf/spirit/A019.pdf'),
 (33,20,2,51,'Approved','2026-02-02','2026-02-11','paaf/spirit/A020.pdf'),
 (34,25,2,38,'Approved','2026-02-02','2026-02-11','paaf/spirit/A025.pdf'),
 (35,26,2,37,'Approved','2026-02-02','2026-02-11','paaf/spirit/A026.pdf');

INSERT INTO project (project_id, project_code, name, client_id) VALUES
 (1,'APJ-01','Wellbay Decommissioning',1),
 (2,'APJ-02','Platform Decommissioning',1),
 (3,'SPJ-01','Morecambe Hub',2),
 (4,'INT','Internal',3);

INSERT INTO scope (scope_id, scope_code, client_id, project_id, description, status, approver_user_id) VALUES
 (1,'BB0001',1,1,'Wellbay Clearance','Open',    6),
 (2,'BB0002',1,2,'Platform Study',   'Open',    7),
 (3,'FB0001',1,2,'EDC Engineering',  'Closed',  7),
 (4,'A1400', 2,3,'Rivers and Calder','Pipeline',8),
 (5,'A1500', 2,3,'PGC & NRU',        'Open',    8),
 (6,'A1600', 2,3,'Pipeline Removal', 'Open',    7),
 -- Internal codes (no PAAF, no rate)
 (7,'INT01',3,4,'Business Services',   'Open', 7),
 (8,'INT02',3,4,'Training',            'Open', 7),
 (9,'INT03',3,4,'Business Development','Open', 7);

-- Phases. Hours are booked to a phase. Every scope has at least one.
INSERT INTO phase (phase_id, scope_id, name) VALUES
 (1,1,'Define'),(2,1,'Execute'),(3,2,'Define'),(4,3,'Define'),(5,4,'Define'),(6,5,'Define'),(7,6,'Define'),
 (8,7,'General'),(9,8,'General'),(10,9,'General');

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
 -- Week of 2 Nov. Ewan Mackay (Planner): own role Mon-Tue, covering Project Controls Manager Wed-Thu
 (5,1,1,1,4,4, '2026-11-02',8,5),
 (5,1,1,1,4,4, '2026-11-03',8,5),
 (5,1,1,1,5,5, '2026-11-04',8,5),
 (5,1,1,1,5,5, '2026-11-05',8,5),
 -- Morag Sinclair (Project Controls Manager): two Apache scopes in one day, then Spirit
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
 -- Catriona Duff (Planner): Spirit, covering Project Controls Manager on Wednesday
 (10,5,6,10,4,10,'2026-11-02',8,10),
 (10,5,6,10,4,10,'2026-11-03',8,10),
 (10,6,7,10,5,11,'2026-11-04',8,10),
 -- Execute phase of BB0001: Gregor Munro (Project Engineer)
 (9,1,2,8,3,3,'2026-11-03',8,9);

-- More people across job families (booked to their own job title)
INSERT INTO time_entry (user_id, scope_id, phase_id, paaf_id, worked_role_id, rate_id, work_date, hours, created_by) VALUES
 (11,2,3,11,7,13,'2026-10-27',8,11),
 (11,2,3,11,7,13,'2026-10-28',8,11),
 (12,2,3,12,15,21,'2026-10-27',8,12),
 (13,2,3,13,16,22,'2026-10-28',8,13),
 (14,2,3,14,17,23,'2026-10-28',8,14),
 (15,2,3,15,19,25,'2026-10-29',8,15),
 (16,2,3,16,18,24,'2026-10-29',8,16),
 (17,2,3,17,20,26,'2026-10-29',8,17),
 (20,2,3,20,22,28,'2026-10-27',8,20),
 (19,2,3,19,23,29,'2026-11-03',8,19),
 (18,1,1,18,21,27,'2026-10-28',8,18),
 (21,1,2,21,11,17,'2026-11-02',8,21),
 (22,1,2,22,13,19,'2026-11-03',8,22),
 (23,1,1,23,10,16,'2026-10-27',8,23),
 (24,1,1,24,24,30,'2026-10-28',4,24),
 (12,5,6,29,15,44,'2026-10-28',8,12),
 (13,5,6,30,16,45,'2026-11-02',8,13),
 (25,5,6,34,9,38,'2026-10-29',8,25),
 (26,5,6,35,8,37,'2026-11-03',8,26),
 (17,5,6,31,20,49,'2026-11-02',8,17),
 (20,6,7,33,22,51,'2026-11-03',8,20),
 (19,6,7,32,23,52,'2026-11-04',8,19),
 (11,6,7,28,7,36,'2026-11-04',4,11);

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
UPDATE time_entry SET status='Draft', reject_reason='Project Controls Manager cover not agreed with client, please confirm',
       last_edited_by=7, last_edited_at='2026-11-06 10:00'
 WHERE user_id=10 AND work_date='2026-11-04';
