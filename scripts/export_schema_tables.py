"""Export the database's tables for a walkthrough: build/schema_tables.xlsx (needs openpyxl) and build/schema_tables.csv (always).

Sheets: Tables (one row per table with its number of columns and what it links to), Columns (one row per column: type, required, key, and the table a
column points to), Links (every foreign key). Run from the repo root against a built database:  python scripts/export_schema_tables.py
"""
import csv
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
from nmc import pg  # noqa: E402

OUT = os.path.join(ROOT, "build", "schema_tables")

COLUMNS = """
SELECT c.table_name, c.column_name, c.data_type,
       CASE c.is_nullable WHEN 'NO' THEN 'yes' ELSE '' END AS required,
       CASE WHEN pk.column_name IS NOT NULL THEN 'yes' ELSE '' END AS pkey,
       coalesce(fk.ref, '') AS points_to
FROM information_schema.columns c
JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name AND t.table_type = 'BASE TABLE'
LEFT JOIN (SELECT kcu.table_name, kcu.column_name FROM information_schema.table_constraints tc
           JOIN information_schema.key_column_usage kcu ON kcu.constraint_name = tc.constraint_name AND kcu.table_schema = tc.table_schema
           WHERE tc.constraint_type = 'PRIMARY KEY') pk ON pk.table_name = c.table_name AND pk.column_name = c.column_name
LEFT JOIN (SELECT cl.relname AS table_name, a.attname AS column_name, rf.relname || '.' || af.attname AS ref
           FROM pg_constraint co JOIN pg_class cl ON cl.oid = co.conrelid JOIN pg_class rf ON rf.oid = co.confrelid
           JOIN pg_attribute a ON a.attrelid = co.conrelid AND a.attnum = co.conkey[1] JOIN pg_attribute af ON af.attrelid = co.confrelid AND af.attnum = co.confkey[1]
           WHERE co.contype = 'f' AND cl.relnamespace = 'public'::regnamespace) fk ON fk.table_name = c.table_name AND fk.column_name = c.column_name
WHERE c.table_schema = 'public'
ORDER BY c.table_name, c.ordinal_position
"""

con = pg.connect()
columns = [list(r) for r in con.execute(COLUMNS)]
tables = sorted({r[0] for r in columns})
links = {}
for t, col, _, _, _, ref in columns:
    if ref:
        links.setdefault(t, []).append("%s -> %s" % (col, ref))
rows = []
for t in tables:
    rows.append([t, sum(1 for c in columns if c[0] == t), "; ".join(links.get(t, []))])
head_c = ["table", "column", "type", "required", "key", "points to"]
head_t = ["table", "columns", "links to (column -> table.column)"]
head_l = ["table", "column", "points to"]
link_rows = [[c[0], c[1], c[5]] for c in columns if c[5]]

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT + ".csv", "w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig so Excel opens it cleanly
    w = csv.writer(f)
    w.writerow(head_c)
    w.writerows(columns)
print("Wrote", OUT + ".csv")
try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
except ImportError:
    print("openpyxl is not installed (pip install openpyxl) so only the CSV was written.")
else:
    wb = Workbook()
    wb.remove(wb.active)
    for name, head, data in (("Tables", head_t, rows), ("Columns", head_c, columns), ("Links", head_l, link_rows)):
        ws = wb.create_sheet(name)
        ws.append(head)
        for r in data:
            ws.append(r)
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="FE6700")
        for i, h in enumerate(head, 1):
            ws.column_dimensions[get_column_letter(i)].width = min(max([len(str(h))] + [len(str(r[i - 1])) for r in data]) + 2, 70)
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
    wb.save(OUT + ".xlsx")
    print("Wrote", OUT + ".xlsx", "(%d tables, %d columns, %d links)" % (len(rows), len(columns), len(link_rows)))
