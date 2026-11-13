"""Build the mock database: python scripts/build.py [--no-seed] [--extra file.sql ...]

--extra applies more SQL after the seed, for example a local, git-ignored file of real data (docs/p6_spirit_pack.sql).
"""
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build(path=None, seed=True, extra=()):
    """Create a fresh SQLite database from db/schema.sql (+ db/seed.sql). Returns the connection."""
    if path and os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path or ":memory:")
    con.execute("PRAGMA foreign_keys = ON")
    files = ["schema.sql"] + (["seed.sql"] if seed else [])
    for name in files:
        with open(os.path.join(ROOT, "db", name), encoding="utf-8") as f:
            con.executescript(f.read())
    for name in extra:  # local extras, applied after the seed
        with open(name, encoding="utf-8") as f:
            con.executescript(f.read())
    con.commit()
    return con


if __name__ == "__main__":
    target = os.path.join(ROOT, "build", "timewriting.db")
    extra = [sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--extra" and i + 1 < len(sys.argv)]
    con = build(target, seed="--no-seed" not in sys.argv, extra=extra)
    tables = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
    print("Built", target, tables)
