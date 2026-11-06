"""Build the mock database: python scripts/build.py [--no-seed]"""
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build(path=None, seed=True):
    """Create a fresh SQLite database from db/schema.sql (+ db/seed.sql). Returns the connection."""
    if path and os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path or ":memory:")
    con.execute("PRAGMA foreign_keys = ON")
    files = ["schema.sql"] + (["seed.sql"] if seed else [])
    for name in files:
        with open(os.path.join(ROOT, "db", name), encoding="utf-8") as f:
            con.executescript(f.read())
    con.commit()
    return con


if __name__ == "__main__":
    target = os.path.join(ROOT, "build", "timewriting.db")
    con = build(target, seed="--no-seed" not in sys.argv)
    tables = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
    print("Built", target, tables)
