"""Export the schema as a table (one row per column) to build/schema_tables.csv, which opens in Excel.

Run from the repo root after building:  python scripts/export_schema_tables.py
"""
import csv
import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "build", "timewriting.db")
OUT = os.path.join(ROOT, "build", "schema_tables.csv")

QUERY = """
SELECT m.name, p.name, p.type,
       CASE p."notnull" WHEN 1 THEN 'yes' ELSE '' END,
       CASE p.pk WHEN 1 THEN 'yes' ELSE '' END
FROM sqlite_master m, pragma_table_info(m.name) p
WHERE m.type = 'table'
ORDER BY m.name, p.cid
"""

con = sqlite3.connect(DB)
with open(OUT, "w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig so Excel opens it cleanly
    w = csv.writer(f)
    w.writerow(["table", "column", "type", "required", "key"])
    w.writerows(con.execute(QUERY))
print("Wrote", OUT)