"""Link the people in the app to their Microsoft 365 accounts, from the tenant's user export (Entra admin centre, Users, Download users).

    python scripts/import_users.py "path/to/users.csv"            # dry run: shows what would change, writes nothing
    python scripts/import_users.py "path/to/users.csv" --apply    # does it
    python scripts/import_users.py "path/to/users.csv" --also "Graeme Hogg" --also "NMC Admin"   # people whose address is not firstname.lastname

Only real people are read from the export: an @nmcenergy.com address of the form firstname.lastname, not blocked from signing in. Each is matched
to an existing person in the app by name and gets their work email and their Entra object id (the stable key Microsoft sign-in uses). Nobody is
added here: a person the app does not know about is listed so they can be added in Admin with a role, and anyone in the app who is not in the
export is listed too. The export has real names, so keep it out of the repo (docs/ is git-ignored).
"""
import csv
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOMAIN = "nmcenergy.com"
FIRST_DOT_LAST = re.compile(r"^[a-z][a-z'\-]*\.[a-z][a-z'\-]*$")


def key(name):
    """A name as compared between the export and the app: trailing dots (old accounts), spacing and case ignored."""
    return " ".join((name or "").replace(".", " ").split()).casefold()


def read_people(path, also=()):
    """The real people in a tenant export: [(display name, email, object id)], one per account that passes the filter. also names people to
    include although their address is not firstname.lastname (a short address such as graeme@). They must still be on the company domain and not blocked."""
    extra = {key(n) for n in also}
    out = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            email = (r.get("User principal name") or "").strip().lower()
            local, _, domain = email.partition("@")
            if domain == DOMAIN and r.get("Block credential") != "True" and (FIRST_DOT_LAST.match(local) or key(r["Display name"]) in extra):
                out.append((" ".join((r["Display name"] or "").split()).rstrip("."), email, (r.get("Object Id") or "").strip().lower()))
    return out


def link_people(con, people, apply=False):
    """Set email and object id on the people the app already has. Returns (linked, already, not_in_app, not_in_export, clashes)."""
    app_users = {}
    for r in con.execute("select user_id, name, email, entra_object_id from app_user"):
        app_users.setdefault(key(r[1]), []).append(r)
    linked, already, not_in_app, clashes, seen = [], [], [], [], set()
    for name, email, oid in people:
        rows = app_users.get(key(name), [])
        if len(rows) != 1:
            (clashes if rows else not_in_app).append(name)
            continue
        uid, _, cur_email, cur_oid = rows[0]
        seen.add(uid)
        if cur_email == email and cur_oid == oid:
            already.append(name)
            continue
        linked.append(name)
        if apply:
            con.execute("update app_user set email=?, entra_object_id=? where user_id=?", (email, oid or None, uid))
    if apply:
        con.commit()
    not_in_export = [r[1] for rows in app_users.values() for r in rows if r[0] not in seen]
    return linked, already, sorted(not_in_app), sorted(not_in_export), sorted(clashes)


if __name__ == "__main__":
    argv = sys.argv[1:]
    also = [argv[i + 1] for i, a in enumerate(argv) if a == "--also" and i + 1 < len(argv)]
    args = [a for i, a in enumerate(argv) if not a.startswith("--") and not (i and argv[i - 1] == "--also")]
    if not args:
        sys.exit(__doc__)
    db = os.environ.get("TW_DB") or os.path.join(ROOT, "build", "timewriting.db")
    con = sqlite3.connect(db)
    linked, already, not_in_app, not_in_export, clashes = link_people(con, read_people(args[0], also), "--apply" in sys.argv)
    print("%s %s" % (db, "(changes written)" if "--apply" in sys.argv else "(dry run: nothing written, add --apply to do it)"))
    print("Linked: %d   Already linked: %d" % (len(linked), len(already)))
    print("In the export but not in the app (%d): %s" % (len(not_in_app), ", ".join(not_in_app) or "-"))
    print("In the app but not in the export (%d): %s" % (len(not_in_export), ", ".join(not_in_export) or "-"))
    if clashes:
        print("Matched more than one person in the app (%d), left alone: %s" % (len(clashes), ", ".join(clashes)))
