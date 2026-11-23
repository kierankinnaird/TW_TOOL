"""Export the schema as a table (one row per column) to build/schema_tables.csv, which opens in Excel.

Run from the repo root after building:  python scripts/export_schema_tables.py
"""
import csv
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
from nmc import pg  # noqa: E402

OUT = os.path.join(ROOT, "build", "schema_tables.csv")

QUERY = """
SELECT c.table_name, c.column_name, c.data_type,
       CASE c.is_nullable WHEN 'NO' THEN 'yes' ELSE '' END,
       CASE WHEN k.column_name IS NOT NULL THEN 'yes' ELSE '' END
FROM information_schema.columns c
JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name AND t.table_type = 'BASE TABLE'
LEFT JOIN (SELECT kcu.table_name, kcu.column_name FROM information_schema.table_constraints tc
           JOIN information_schema.key_column_usage kcu ON kcu.constraint_name = tc.constraint_name AND kcu.table_schema = tc.table_schema
           WHERE tc.constraint_type = 'PRIMARY KEY') k ON k.table_name = c.table_name AND k.column_name = c.column_name
WHERE c.table_schema = 'public'
ORDER BY c.table_name, c.ordinal_position
"""

con = pg.connect()
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig so Excel opens it cleanly
    w = csv.writer(f)
    w.writerow(["table", "column", "type", "required", "key"])
    w.writerows(con.execute(QUERY))
print("Wrote", OUT)
