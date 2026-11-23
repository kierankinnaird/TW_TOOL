"""Load the company's people into the database from the tenant's user export (Entra admin centre, Users, Download users).

    python scripts/build.py --users "path/to/users.csv"          # a fresh database with them in
    python scripts/load_users.py "path/to/users.csv" [--apply]   # or into build/timewriting.db (a dry run unless --apply)

Two steps, both from the real people in the export (see import_users.read_people):
  1. people the app already has (the seed's) are linked to their account: work email and Entra object id;
  2. everyone else WITH a job title is added: a standard user, Staff, with their title mapped to a role below. Anyone without a title is
     skipped and listed, because a role cannot be chosen for them: add them in Admin.
Titles with no exact role in the rate card are mapped to the closest one (LOOSE below), the way the seed does, and the people on them are
listed so the cost team can correct them; a title with no billable equivalent (a CEO, HR) gets a new role with no rates, so it can only be
used for internal time. Nothing here invents a rate. Start date is 1 January 2026 for anyone who was here then, otherwise the day their account
was created. NMC ids continue from the last one in use. The export has real names: keep it out of the repo (docs/ is git-ignored).
"""
import csv
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import import_users  # noqa: E402

ALSO = ("Graeme Hogg", "NMC Admin", "Daniel Evans", "Peter McConnachie")   # real people whose address is not firstname.lastname
YEAR_START = "2026-01-01"

# job title in the export -> skill code of the role in the app
TITLE_ROLE = {
    "Lead Construction Engineer (Mechanical)": "CN-01", "Lead Construction (E&I)": "CN-01", "Lead Document Controller": "PM-07", "Cost Engineer": "PM-06",
    "Project Manager": "PM-01", "Lead Piping Engineer": "PI-01", "Principal Piping Engineer & Senior Project Engineer": "PI-01",
    "Lead Structural Engineer": "ST-01", "Lead Instrument Engineer": "IN-01", "Lead Technical Safety Engineer": "TS-01", "Lead Mechanical Engineer": "ME-01",
    "Lead Process Engineer": "PR-01", "Senior Piping Designer": "PI-02",
    # no exact role: closest one (LOOSE)
    "Senior Tech Safety Engineer": "TS-01", "Technical Safety Manager": "TS-01", "Senior Process Engineer": "PR-01", "Senior Project Engineer": "PE-01",
    "Graduate Engineer": "PE-01", "Project Manager/Engineer": "PM-01", "Project Engineer (Regulatory / Compliance)": "PE-01", "Decommissioning Co-ordinator": "PE-01",
    "Offshore Rep": "EM-01", "IRM Manager": "PE-04", "Senior Construction Engineer": "CN-01", "Lead Structural Designer / Project Engineer": "ST-01",
    "Senior Electrical Engineer": "EL-01", "Lead Electrical & Instrument Designer": "EL-01", "E&I Designer": "EL-01", "Senior Instrument Engineer": "IN-01",
    "Senior Piping Engineer": "PI-01", "Senior Draughtsperson": "PI-02", "CAD Operator": "PI-02", "3D Model Support": "ME-03",
    # no billable equivalent: a new role with no rates (internal time only)
    "Business Lead": "BS-04", "Strategy & Growth Consultant": "BS-05", "Head of HSEQ": "BS-06", "BD Manager (International)": "BS-07", "Head of Construction": "BS-08",
    "Procurement Specialist": "BS-09", "IT Placement Student": "BS-10", "Chief Executive Officer": "BS-11", "HR Consultant": "BS-12", "Head of Business Services": "BS-13",
}
NEW_ROLES = {"BS-04": "Business Lead", "BS-05": "Strategy & Growth Consultant", "BS-06": "Head of HSEQ", "BS-07": "BD Manager (International)", "BS-08": "Head of Construction",
             "BS-09": "Procurement Specialist", "BS-10": "IT Placement Student", "BS-11": "Chief Executive Officer", "BS-12": "HR Consultant", "BS-13": "Head of Business Services"}
LOOSE = {"Senior Tech Safety Engineer", "Technical Safety Manager", "Senior Process Engineer", "Senior Project Engineer", "Graduate Engineer", "Project Manager/Engineer",
         "Project Engineer (Regulatory / Compliance)", "Decommissioning Co-ordinator", "Offshore Rep", "IRM Manager", "Senior Construction Engineer",
         "Lead Structural Designer / Project Engineer", "Senior Electrical Engineer", "Lead Electrical & Instrument Designer", "E&I Designer", "Senior Instrument Engineer",
         "Senior Piping Engineer", "Senior Draughtsperson", "CAD Operator", "3D Model Support"}


def read_accounts(path):
    """{(display name key, email): (title, created date)} for every row of the export."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        return {(import_users.key(r["Display name"]), (r["User principal name"] or "").strip().lower()): ((r.get("Title") or "").strip(), (r.get("When created") or "")[:10])
                for r in csv.DictReader(f)}


def load_users(con, path, apply=True, also=ALSO):
    """Link the existing people, then add the new ones with a title. Returns a dict of what happened."""
    people = import_users.read_people(path, also)
    accounts = read_accounts(path)
    linked, already, not_in_app, not_in_export, clashes = import_users.link_people(con, people, apply)
    out = {"linked": len(linked) + len(already), "added": [], "no_title": [], "loose": [], "new_roles": []}
    roles = {r[0]: r[1] for r in con.execute("select skill_code, role_id from role")}
    if apply:
        for code, name in NEW_ROLES.items():
            if code not in roles:
                con.execute("insert into role (skill_code, name, family) values (?,?,'Business Services')", (code, name))
                roles[code] = con.execute("select last_insert_rowid()").fetchone()[0]
                out["new_roles"].append(name)
    last = max(int(r[0][1:]) for r in con.execute("select nmc_id from app_user") if r[0][1:].isdigit())
    by_name = {n: (e, o) for n, e, o in people}
    for name in not_in_app:
        email, oid = by_name[name]
        title, created = accounts.get((import_users.key(name), email), ("", ""))
        if not title:
            out["no_title"].append(name)
            continue
        code = TITLE_ROLE.get(title)
        if not code:
            out["no_title"].append("%s (title not mapped: %s)" % (name, title))
            continue
        if title in LOOSE:
            out["loose"].append("%s: %s -> %s" % (name, title, code))
        last += 1
        out["added"].append(name)
        if apply:
            con.execute("insert into app_user (nmc_id, employment_type, name, role_id, permission, start_date, email, entra_object_id) values (?,?,?,?,?,?,?,?)",
                        ("A%03d" % last, "Staff", name, roles[code], "user", max(YEAR_START, created) if created > YEAR_START else YEAR_START, email, oid or None))
    if apply:
        con.commit()
    return out


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    db = os.environ.get("TW_DB") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "build", "timewriting.db")
    con = sqlite3.connect(db)
    res = load_users(con, args[0], "--apply" in sys.argv)
    print("%s %s" % (db, "(changes written)" if "--apply" in sys.argv else "(dry run: nothing written, add --apply to do it)"))
    print("Linked to their Microsoft account: %d   Added: %d" % (res["linked"], len(res["added"])))
    print("Skipped, no job title (add in Admin): %s" % (", ".join(res["no_title"]) or "-"))
    print("Closest-match roles, to check (%d):\n  %s" % (len(res["loose"]), "\n  ".join(res["loose"])))
