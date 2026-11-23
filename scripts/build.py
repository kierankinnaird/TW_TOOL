"""Build the database: python scripts/build.py [--no-seed] [--users export.csv] [--history] [--extra file.sql ...]

Builds into the PostgreSQL database in DATABASE_URL (default: the local development server, see docker-compose.yml). Anything already in that
database's public schema is dropped first, so this is for a development or demo database, never for one holding real hours.

--users loads the company's people from a tenant user export (scripts/load_users.py). --history adds twelve weeks of approved demo timesheets before the seed's own weeks (scripts/seed_history.py).

--extra applies more SQL after the seed, for example a local, git-ignored file of real data (docs/p6_spirit_pack.sql).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
from nmc import pg  # noqa: E402


def build(url=None, seed=True, extra=(), history=False, users=None):
    """Create a fresh database from db/schema.sql (+ db/seed.sql) in the PostgreSQL database at url. Returns the connection."""
    con = pg.connect(url)
    pg.reset_schema(con)
    files = ["schema.sql"] + (["seed.sql"] if seed else [])
    for name in files:
        with open(os.path.join(ROOT, "db", name), encoding="utf-8") as f:
            con.executescript(f.read())
    con.execute("select reset_sequences()")  # the seed gives some rows fixed ids: the next new row must not clash with them
    for name in extra:  # local extras, applied after the seed
        with open(name, encoding="utf-8") as f:
            con.executescript(f.read())
        con.execute("select reset_sequences()")
    if users:  # the company's people from the tenant's user export (scripts/load_users.py), before the history so it covers them
        from load_users import load_users
        load_users(con, users)
    if history:  # twelve weeks of demo timesheets before the seed's own weeks (scripts/seed_history.py)
        from seed_history import add_history
        add_history(con)
    con.commit()
    return con


def where(url):
    """The database's address without its password, for showing."""
    from urllib.parse import urlsplit
    p = urlsplit(url or pg.database_url())
    return "%s:%s%s" % (p.hostname, p.port or 5432, p.path)


if __name__ == "__main__":
    extra = [sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--extra" and i + 1 < len(sys.argv)]
    con = build(seed="--no-seed" not in sys.argv, extra=extra, history="--history" in sys.argv,
                users=next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--users" and i + 1 < len(sys.argv)), None))
    tables = [r[0] for r in con.execute("select table_name from information_schema.tables where table_schema='public' and table_type='BASE TABLE' order by 1")]
    print("Built", where(None), tables)
