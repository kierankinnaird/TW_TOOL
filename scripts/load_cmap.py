"""Load the company's people from CMAP's user grid (the system this one replaces), and link each to their Microsoft 365 account.

    python scripts/build.py --cmap "docs/cmap-user-grid.csv" --users "path/to/tenant-users.csv" [--history]

The CMAP grid is the list of people: NMC User ID, Employee or Contractor, start and end dates, line manager and default role. The tenant export
(Entra admin centre, Users, Download users) only supplies the work email and Entra object id each person signs in with, matched by name. People the
seed already has are updated in place (their hours and approvals stay); everyone else is added as a standard user. Nobody is added from the
tenant export alone: guests, shared mailboxes and old accounts are not people here.

Default roles are mapped to the job titles on the rate card (ROLE_MAP below). A title with no billable equivalent gets a new role with no rates, so
it can only book internal time until the cost team prices it; a title mapped to the nearest billable role is listed so it can be checked.
The grid has real names: keep it out of the repo (docs/ is git-ignored).
"""
import csv
import datetime
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import import_users  # noqa: E402
import load_users  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
from nmc.core import audit_user_added, audit_user_changes, person_row  # noqa: E402

# CMAP default role -> (skill code on the rate card, True when it is only the nearest match). Codes in NEW_ROLES are added when missing.
ROLE_MAP = {
    "Technical Safety Lead Engineer": ("TS-01", False), "Technical Safety Senior Engineer": ("TS-01", True), "Technical Safety Manager": ("TS-01", True),
    "Piping Lead Engineer": ("PI-01", False), "Piping Senior Engineer": ("PI-01", True), "Piping Senior Designer": ("PI-02", False),
    "Draughtsman": ("PI-02", True), "Electrical Senior Designer": ("PI-02", True), "Electrical Lead Designer": ("PI-02", True), "Structural Senior Designer": ("PI-02", True),
    "Electrical Lead Engineer": ("EL-01", False), "Electrical Senior Engineer": ("EL-01", True),
    "Instruments Lead Engineer": ("IN-01", False), "Instruments Senior Engineer": ("IN-01", True),
    "Process Lead Engineer": ("PR-01", False), "Process Senior Engineer": ("PR-01", True),
    "Structural Lead Engineer": ("ST-01", False), "Structural Senior Engineer": ("ST-01", True),
    "Mechanical Lead Engineer": ("ME-01", False), "Lead Construction Engineer": ("CN-01", False),
    "Project Manager": ("PM-01", False), "Assistant Project Manager": ("PM-01", True),
    "Project Engineer": ("PE-01", False), "Senior Project Engineer": ("PE-01", True),
    "Engineering Manager": ("EM-02", False), "Engineering Deputy Manager": ("EM-02", True), "Operations Superintendent": ("EM-01", False),
    "Cost Engineer": ("PM-06", False), "Cost Lead Engineer": ("PM-06", True),
    "Planning Engineer": ("PM-04", True), "Planning Lead Engineer": ("PM-04", True),
    "Estimator": ("PM-05", True), "Document Controller": ("PM-07", False), "Document Controller Lead": ("PM-07", True),
    "HSE Lead Engineer": ("HS-01", True),     # no billable equivalent: new roles with no rates (internal time only until the cost team prices them)
    "Site Rep": ("OP-01", False), "Offshore Rep": ("OP-02", False), "OIM": ("OP-03", False), "Production Technician": ("OP-04", False),
    "Project Director": ("BS-16", False), "Project Services Manager": ("BS-17", False), "Non-Chargeable Admin": ("BS-18", False),
    "Project Services": ("BS-19", False), "HSEQ Manager": ("BS-20", False), "Buyer": ("BS-21", False), "Decommissioning Co-ordinator": ("CN-02", False), "Technical Assistant": ("PE-06", False),
}
NEW_ROLES = {   # skill code -> (job title, job family)
    "OP-01": ("Site Rep", "Operations"), "OP-02": ("Offshore Rep", "Operations"), "OP-03": ("OIM", "Operations"), "OP-04": ("Production Technician", "Operations"),
    "BS-16": ("Project Director", "Business Services"), "BS-17": ("Project Services Manager", "Business Services"),
    "BS-18": ("Non-Chargeable Admin", "Business Services"), "BS-19": ("Project Services", "Business Services"),
    "BS-20": ("HSEQ Manager", "Business Services"), "BS-21": ("Buyer", "Business Services"),
    "CN-02": ("Decommissioning Co-ordinator", "Construction Engineering"), "PE-06": ("Technical Assistant", "Project Engineering"),
}
KEEP = ("NMC Admin",)    # not in CMAP, but kept: the shared IT account. Everyone else the seed has who is not in the grid is removed.
NO_TIME = ("Konstantin Nazaruk", "Graeme Hogg", "Craig Matthew", "Arran Bastable")   # directors and the CEO do not write time (the shared IT account is set in load_users)
NAME_ALIASES = {"kimberly smith": "kimberley smith"}                                  # spelled differently in CMAP and Microsoft 365


def role_key(title):
    return " ".join(title.replace("-Ordinator", "-ordinator").split()).casefold()


ROLE_LOOKUP = {role_key(k): v for k, v in ROLE_MAP.items()}


def read_grid(path):
    """The people in the grid: [{name, nmc_id, type, start, end, role, manager, status}]. A repeated NMC User ID is kept by the Active person; the other gets -2."""
    out = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if not (r.get("Last Name") or "").strip():          # the "Total Users" line
                continue
            m = re.match(r"[A-Za-z]\d+", (r["NMC User ID"] or "").strip())
            out.append({
                "name": " ".join(("%s %s" % (r["First Name"], r["Last Name"])).split()),
                "nmc_id": m.group(0).upper() if m else None,
                "type": "Staff" if r["Employment Status"].strip() == "Employee" else "Contract",
                "start": datetime.datetime.strptime(r["Start Date"].strip(), "%d-%b-%y").date().isoformat(),
                "end": datetime.datetime.strptime(r["End Date"].strip(), "%d-%b-%y").date().isoformat() if r["End Date"].strip() else None,
                "role": r["Default Role"].strip(), "manager": (r["Line Manager"] or "").strip(), "status": r["Profile Status"].strip(),
            })
    seen = {}
    for p in sorted(out, key=lambda p: p["status"] != "Active"):    # Active people first, so they keep a repeated id
        if p["nmc_id"] in seen:
            p["nmc_id"] += "-2"
            p["duplicate"] = True
        seen[p["nmc_id"]] = p
    return out


def ms_accounts(path):
    """The tenant's usable accounts by name: key -> (email, object id). Company domain, not blocked; found by display name or by first and last name."""
    found = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            email = (r.get("User principal name") or "").strip().lower()
            if not email.endswith("@" + import_users.DOMAIN) or r.get("Block credential") == "True":
                continue
            oid = (r.get("Object Id") or "").strip().lower()
            licensed = r.get("Licenses", "") != "Unlicensed"
            for name in (r.get("Display name"), "%s %s" % (r.get("First name"), r.get("Last name"))):
                k = import_users.key(name)
                if k and (k not in found or (licensed and not found[k][2])):
                    found[k] = (email, oid, licensed)
    return {k: (v[0], v[1]) for k, v in found.items()}


def remove_people(con, user_ids):
    """Take people out of a demo database completely: their hours and audit rows, and every other mention of them. The database's own rules
    (append-only audit, only draft hours can be deleted) are switched off while this runs, so it is for demo data only, never for real hours."""
    if not user_ids:
        return
    ids = list(user_ids)
    tables = [r[0] for r in con.execute("select tablename from pg_tables where schemaname='public'")]
    for t in tables:
        con.execute("alter table %s disable trigger user" % t)
    try:
        con.execute("delete from time_entry_audit where entry_id in (select entry_id from time_entry where user_id = any(?))", (ids,))
        con.execute("delete from time_entry where user_id = any(?)", (ids,))
        for table, col, required in con.execute(
                "select c.conrelid::regclass::text, a.attname, a.attnotnull from pg_constraint c join pg_attribute a on a.attrelid = c.conrelid and a.attnum = any(c.conkey) "
                "where c.contype = 'f' and c.confrelid = 'app_user'::regclass").fetchall():
            if required:
                con.execute("delete from %s where %s = any(?)" % (table, col), (ids,))
            else:
                con.execute("update %s set %s = NULL where %s = any(?)" % (table, col, col), (ids,))
        con.execute("delete from app_user where user_id = any(?)", (ids,))
    finally:
        for t in tables:
            con.execute("alter table %s enable trigger user" % t)


def load_cmap(con, grid_path, ms_path=None):
    """Apply the grid (and, when given, the tenant export). Returns a dict of what happened, for printing."""
    people = read_grid(grid_path)
    out = {"people": len(people), "updated": [], "added": [], "loose": [], "no_account": [], "duplicate_ids": [p["name"] for p in people if p.get("duplicate")],
           "removed": [], "new_roles": [], "unmapped": []}
    roles = {r[0]: r[1] for r in con.execute("select skill_code, role_id from role")}
    for code, (title, family) in NEW_ROLES.items():
        if code not in roles:
            roles[code] = con.execute("insert into role (skill_code, name, family) values (?,?,?) returning role_id", (code, title, family)).fetchone()[0]
            out["new_roles"].append(title)
    by_name = {}
    for r in con.execute("select user_id, name from app_user"):
        by_name.setdefault(import_users.key(r[1]), []).append(r[0])

    # people the seed already has are updated in place (their hours and approvals stay); everyone else is added
    for p in people:
        p["key"] = NAME_ALIASES.get(import_users.key(p["name"]), import_users.key(p["name"]))
        p["uid"] = (by_name.get(p["key"]) or [None])[0]
        code = ROLE_LOOKUP.get(role_key(p["role"]))
        if code is None:
            out["unmapped"].append("%s: %s" % (p["name"], p["role"]))
            code = ("BS-19", True)
        p["role_id"] = roles[code[0]]
        if code[1]:
            out["loose"].append("%s: %s -> %s" % (p["name"], p["role"], code[0]))
    for p in people:
        if p["uid"]:
            old = person_row(con, p["uid"])
            con.execute("update app_user set nmc_id=?, employment_type=?, start_date=?, end_date=?, role_id=? where user_id=?",
                        (p["nmc_id"], p["type"], p["start"], p["end"], p["role_id"], p["uid"]))
            audit_user_changes(con, None, p["uid"], old)
            out["updated"].append(p["name"])
        else:
            p["uid"] = con.execute("insert into app_user (nmc_id, employment_type, name, role_id, permission, start_date, end_date, books_time) "
                                   "values (?,?,?,?,'user',?,?,1) returning user_id", (p["nmc_id"], p["type"], p["name"], p["role_id"], p["start"], p["end"])).fetchone()[0]
            audit_user_added(con, None, p["uid"])
            out["added"].append(p["name"])
    ids = {p["key"]: p["uid"] for p in people}
    for p in people:                                                 # line managers, once everyone exists
        manager = ids.get(import_users.key(p["manager"])) or (by_name.get(import_users.key(p["manager"])) or [None])[0]
        if manager and manager != p["uid"]:
            old = person_row(con, p["uid"])
            con.execute("update app_user set line_manager_id=? where user_id=?", (manager, p["uid"]))
            audit_user_changes(con, None, p["uid"], old)
    grid_ids = {p["uid"] for p in people}
    # anyone else in the database who is not in the grid is not a person here: removed, apart from the shared IT account
    leftover = [(uid, name) for uid, name in con.execute("select user_id, name from app_user order by user_id").fetchall()
                if uid not in grid_ids and name not in KEEP]
    remove_people(con, [uid for uid, _ in leftover])
    out["removed"] = [name for _, name in leftover]

    if ms_path:                                                      # the Microsoft account each person signs in with
        accounts = ms_accounts(ms_path)
        for uid, name in con.execute("select user_id, name from app_user order by user_id").fetchall():
            k = NAME_ALIASES.get(import_users.key(name), import_users.key(name))
            email, oid = accounts.get(k, (None, None))
            if not email and name in load_users.ALSO:
                email, oid = accounts.get(import_users.key(name), (None, None))
            if email:
                old = person_row(con, uid)
                con.execute("update app_user set email=?, entra_object_id=? where user_id=?", (email, oid or None, uid))
                audit_user_changes(con, None, uid, old)
            elif uid in grid_ids:
                out["no_account"].append(name)

    for name in NO_TIME:                                             # directors and the CEO do not write time
        row = con.execute("select user_id from app_user where name=?", (name,)).fetchone()
        if row:
            old = person_row(con, row[0])
            con.execute("update app_user set books_time=0 where user_id=?", (row[0],))
            audit_user_changes(con, None, row[0], old)
    load_users.set_permissions(con, out)                            # cost controllers and IT admins
    con.commit()
    return out


def report(out):
    lines = ["%d people in the grid: %d updated (already in the seed), %d added." % (out["people"], len(out["updated"]), len(out["added"]))]
    if out["new_roles"]:
        lines.append("New roles with no rates yet (%d): %s" % (len(out["new_roles"]), ", ".join(out["new_roles"])))
    if out["duplicate_ids"]:
        lines.append("A repeated NMC User ID in CMAP, the second person given -2 (fix in CMAP or Admin): %s" % ", ".join(out["duplicate_ids"]))
    if out["unmapped"]:
        lines.append("Roles not recognised, set to Project Services (%d): %s" % (len(out["unmapped"]), "; ".join(out["unmapped"])))
    lines.append("Mapped to the nearest rate-card role, to check (%d):\n  %s" % (len(out["loose"]), "\n  ".join(out["loose"])))
    lines.append("In CMAP but no Microsoft account found (%d, cannot sign in until one is set in Admin): %s" % (len(out["no_account"]), ", ".join(out["no_account"]) or "-"))
    lines.append("Removed, not in CMAP (%d): %s   (kept: %s)" % (len(out["removed"]), ", ".join(out["removed"]) or "-", ", ".join(KEEP)))
    return "\n".join(lines)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    from nmc import pg
    con = pg.connect()
    print(report(load_cmap(con, args[0], args[1] if len(args) > 1 else None)))
