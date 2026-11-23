"""PostgreSQL access. The rest of the app was written against sqlite3's way of working (con.execute(sql, params) with ? placeholders, rows read
by name or by position, a failed statement leaving the transaction usable), so this module gives psycopg the same feel and keeps that code unchanged.

What it does:
  - ? placeholders work (and a literal % in a query needs no doubling);
  - rows read by name (row["name"]) or by position (row[0]);
  - a failing INSERT / UPDATE / DELETE is undone on its own and the transaction carries on, as it did on SQLite (a trigger refusing a booking
    must not poison the rest of the request);
  - NUMERIC columns come back as int or float (never Decimal), and dates go in as ISO text;
  - error_text() gives just the message a trigger raised, without PostgreSQL's CONTEXT lines.
"""
import datetime
import os
import re

import psycopg
from psycopg.adapt import Dumper, Loader

# Where the database is. A local development server is the default; Azure sets DATABASE_URL.
DEFAULT_URL = "postgresql://postgres:nmcdev@localhost:55432/nmc"


def database_url():
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


DatabaseError = psycopg.DatabaseError
IntegrityError = psycopg.IntegrityError
UniqueViolation = psycopg.errors.UniqueViolation
Error = psycopg.Error


class Row(tuple):
    """A result row: a tuple that can also be read by column name."""

    def __new__(cls, values, index):
        row = super().__new__(cls, values)
        row._index = index
        return row

    def __getitem__(self, key):
        if isinstance(key, str):
            return tuple.__getitem__(self, self._index[key])
        return tuple.__getitem__(self, key)

    def keys(self):
        return list(self._index)

    def get(self, key, default=None):
        return tuple.__getitem__(self, self._index[key]) if key in self._index else default


def row_factory(cursor):
    index = {c.name: i for i, c in enumerate(cursor.description or ())}
    return lambda values: Row(values, index)


class _NumberLoader(Loader):
    """NUMERIC as a plain number (int when it has no decimal point, else float), which is what the screens and totals were written for."""

    def load(self, data):
        text = bytes(data).decode()
        return float(text) if "." in text or "e" in text.lower() else int(text)


class _DateAsText(Dumper):
    """A date goes to the database as yyyy-mm-dd text: dates are held as text, so they compare with the stored values."""

    def dump(self, obj):
        return obj.isoformat().encode()


psycopg.adapters.register_loader("numeric", _NumberLoader)
psycopg.adapters.register_dumper(datetime.date, _DateAsText)

_WRITES = re.compile(r"\s*(insert|update|delete)\b", re.I)


def translate(sql):
    """? placeholders become %s, and a literal % is doubled so psycopg leaves it alone."""
    return sql.replace("%", "%%").replace("?", "%s")


class Conn(psycopg.Connection):
    """A psycopg connection that behaves like the sqlite3 one the app was written for."""

    def execute(self, query, params=None, **kw):
        sql, params = translate(query), tuple(params) if params is not None else ()
        if not _WRITES.match(sql):
            return super().execute(sql, params, **kw)
        # A write that fails (a trigger refusing it, a duplicate) is undone by itself, so the transaction stays usable.
        super().execute("savepoint nmc_statement")
        try:
            cur = super().execute(sql, params, **kw)
        except psycopg.Error:
            super().execute("rollback to savepoint nmc_statement")
            super().execute("release savepoint nmc_statement")
            raise
        super().execute("release savepoint nmc_statement")
        return cur

    def executemany(self, query, seq_of_params):
        """The same statement for each set of values."""
        for params in seq_of_params:
            self.execute(query, params)

    def executescript(self, script):
        """Run a file of SQL as it stands (no placeholders), for the schema, seed and extras."""
        return super().execute(script)


def connect(url=None):
    # Parameters are filled in by the driver (not sent separately), as sqlite did: a query like  (? is null or x = ?)  works with a NULL.
    return Conn.connect(url or database_url(), row_factory=row_factory, cursor_factory=psycopg.ClientCursor)


def error_text(e):
    """The message of a database error without PostgreSQL's CONTEXT lines: what a trigger said, for showing to people."""
    diag = getattr(e, "diag", None)
    if diag is not None and diag.message_primary:
        return diag.message_primary
    return str(e).split("\n")[0]


def reset_schema(con):
    """Empty the database (its public schema) ready for a fresh build. Works without the right to create databases."""
    con.execute("drop schema if exists public cascade")
    con.execute("create schema public")
    con.commit()


def schema_version(con):
    try:
        return con.execute("select version from schema_info").fetchone()[0]
    except psycopg.Error:
        con.rollback()
        return 0
