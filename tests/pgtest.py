"""Throwaway PostgreSQL databases for the tests.

The tests need a PostgreSQL server they may create databases on (the one in docker-compose.yml does: `docker compose up -d`). Point them at
another with TEST_DATABASE_URL (a user that can create databases, connected to the maintenance database, e.g. .../postgres).

Building the schema and seed takes a moment, so each kind of database (with or without the seed, history, users ...) is built once into a
template, and every test gets its own copy of that template: fast, and tests cannot disturb each other. A test's databases are dropped when
the test ends; the templates are dropped when the run ends.
"""
import atexit
import itertools
import os
import sys
import unittest
from urllib.parse import urlsplit, urlunsplit

import psycopg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "app"))
from nmc import pg  # noqa: E402
import build as builder  # noqa: E402

ADMIN_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:nmcdev@localhost:55432/postgres")
_names = itertools.count(1)
_templates = {}          # what was asked for -> the template database's name
_made = []               # databases made for tests, newest last


def url_for(name):
    parts = urlsplit(ADMIN_URL)
    return urlunsplit(parts._replace(path="/" + name))


def _admin(sql):
    with psycopg.connect(ADMIN_URL, autocommit=True) as con:
        con.execute(sql)


def _template(seed, extra, history, users):
    key = (seed, tuple(extra), history, users)
    if key not in _templates:
        name = "nmc_tpl_%d_%d" % (os.getpid(), next(_names))
        _admin("create database %s template template0 encoding 'UTF8' lc_collate 'C' lc_ctype 'C'" % name)
        _templates[key] = name
        builder.build(url_for(name), seed=seed, extra=extra, history=history, users=users).close()
    return _templates[key]


def clone(seed=True, extra=(), history=False, users=None):
    """A new database (its url) holding a copy of the built schema and seed."""
    name = "nmc_t_%d_%d" % (os.getpid(), next(_names))
    _admin("create database %s template %s" % (name, _template(seed, extra, history, users)))
    _made.append(name)
    return url_for(name)


def build(_ignored=None, seed=True, extra=(), history=False, users=None):
    """A connection to a new database holding the schema and seed (what scripts/build.py makes). Stands in for scripts/build.build in tests."""
    return pg.connect(clone(seed, extra, history, users))


def empty():
    """A new database with nothing in it (its url)."""
    name = "nmc_t_%d_%d" % (os.getpid(), next(_names))
    _admin("create database %s template template0 encoding 'UTF8' lc_collate 'C' lc_ctype 'C'" % name)
    _made.append(name)
    return url_for(name)


def drop(name):
    _admin("drop database if exists %s with (force)" % name)


def _drop_from(mark):
    while len(_made) > mark:
        drop(_made.pop())


@atexit.register
def _cleanup():
    _drop_from(0)
    for name in _templates.values():
        drop(name)


# Every test's databases go when the test ends (class-level fixtures, made before the first test runs, last until the run ends).
_run = unittest.TestCase.run


def _run_and_clean_up(self, result=None):
    mark = len(_made)
    try:
        return _run(self, result)
    finally:
        _drop_from(mark)


unittest.TestCase.run = _run_and_clean_up
