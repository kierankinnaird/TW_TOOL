"""NMC's job roles, from the job titles in the Microsoft 365 user report (and CMAP's default roles for people the report has no title for).

The job families are NOT changed: a CTR sheet has one tab per job family, so the seed's families (Piping Engineering, Electrical Engineering, Technical Safety
and so on) stay exactly as they are. The seed's roles keep their families, skill codes and rates. A job title from the report that is not on the seed's
rate card is added under the existing family it belongs to, with a new skill code (that family's letters and the next number). Where the title has a nearest
rated title, it gets a copy of that title's rates as a PLACEHOLDER (a Senior Piping Engineer is priced as a Lead Piping Engineer until the cost team sets
the real rate). Titles with no rated equivalent are left unpriced, so the people on them can only book internal time. Everything is listed in the build report.
"""

# The letters of each family's skill codes
PREFIX = {
    "Project Management": "PM", "Engineering Management": "EM", "Project Engineering": "PE", "Process Engineering": "PR", "Piping Engineering": "PI",
    "Instrument Engineering": "IN", "Electrical Engineering": "EL", "Structural Engineering": "ST", "Construction Engineering": "CN", "Subsea Pipelines": "PL",
    "Technical Safety": "TS", "HSES": "HS", "Mechanical Engineering": "ME", "QA/QC": "QC", "Business Services": "BS", "Operations": "OP",
}

# The seed's roles, by title (the seed's HSES Advisor is called HSE Advisor in the Microsoft 365 report, so it is renamed). Their families are left alone.
RENAMED = {"HSES Advisor": "HSE Advisor"}
EXISTING = (
    "Project Manager", "Engineering Manager", "Project Engineer", "Planner", "Project Controls Manager", "Cost Engineer", "Operations Superintendent",
    "Project Controls Engineer", "Cost Estimator", "Document Controller", "Project Engineer - EDC", "Project Engineer - HLV", "Project Engineer - Integrity",
    "Project Engineer - Subsea", "Lead Process Engineer", "Lead Piping Engineer", "Senior Designer", "Lead Instrument Engineer", "Lead Electrical Engineer",
    "Lead Structural Engineer", "Lead Construction Engineer", "Subsea Pipeline Engineer", "Lead Technical Safety Engineer", "HSE Advisor", "Lead Mechanical Engineer",
    "Senior Mechanical Engineer", "Senior Mechanical Designer", "Lead QA/QC Engineer", "Senior QA/QC Inspector", "Head of Projects", "System Administrator",
    "Commercial Lead", "Managing Director",
)

# New titles: title -> (existing family, the rated title whose rates are copied as a placeholder, or None to leave unpriced)
NEW = {
    # Business Services: the directors and the company's support roles
    "Chief Executive Officer": ("Business Services", None), "Operations & Projects Director": ("Business Services", None), "Technical Director": ("Business Services", None),
    "Project Director": ("Business Services", None), "Head of Business Services": ("Business Services", None), "Head of Construction": ("Business Services", None),
    "Head of HSEQ": ("Business Services", None), "BD Manager (International)": ("Business Services", None), "Strategy & Growth Consultant": ("Business Services", None),
    "Business Lead": ("Business Services", None), "Project Services Manager": ("Business Services", None), "Project Services Support": ("Business Services", None),
    "HR Consultant": ("Business Services", None), "IT Placement Student": ("Business Services", None), "Non-Chargeable Admin": ("Business Services", None),
    "Procurement Specialist": ("Business Services", None), "Buyer": ("Business Services", None),
    # Project Management: project controls and managers
    "Planning Engineer": ("Project Management", "Planner"), "Estimator": ("Project Management", "Cost Estimator"),
    "Lead Document Controller": ("Project Management", "Document Controller"),
    "Assistant Project Manager": ("Project Management", "Project Manager"), "PM Consultant": ("Project Management", "Project Manager"),
    # Project Engineering
    "Senior Project Engineer": ("Project Engineering", "Project Engineer"), "Project Engineer (Regulatory / Compliance)": ("Project Engineering", "Project Engineer"),
    "Project Coordinator": ("Project Engineering", None), "Graduate Engineer": ("Project Engineering", None), "Technical Assistant": ("Project Engineering", None),
    # Engineering Management
    "Engineering Deputy Manager": ("Engineering Management", "Engineering Manager"), "IRM Manager": ("Engineering Management", "Engineering Manager"),
    # Process Engineering
    "Senior Process Engineer": ("Process Engineering", "Lead Process Engineer"), "Process Engineer": ("Process Engineering", "Lead Process Engineer"),
    "Lead Pigging Engineer": ("Process Engineering", "Lead Process Engineer"),
    # Piping Engineering (the piping engineers and the designers and draughtspeople)
    "Senior Piping Engineer": ("Piping Engineering", "Lead Piping Engineer"), "Principal Piping Engineer": ("Piping Engineering", "Lead Piping Engineer"),
    "Senior Piping Designer": ("Piping Engineering", "Senior Designer"), "Senior Draughtsperson": ("Piping Engineering", "Senior Designer"),
    "CAD Operator": ("Piping Engineering", None), "3D Model Support": ("Piping Engineering", None),
    # Electrical and Instrument Engineering
    "E&I Designer": ("Electrical Engineering", "Senior Designer"), "Lead Electrical & Instrument Designer": ("Electrical Engineering", "Senior Designer"),
    "Senior Electrical Engineer": ("Electrical Engineering", "Lead Electrical Engineer"), "Senior Instrument Engineer": ("Instrument Engineering", "Lead Instrument Engineer"),
    # Structural Engineering
    "Senior Structural Designer": ("Structural Engineering", "Senior Designer"), "Lead Structural Designer": ("Structural Engineering", "Lead Structural Engineer"),
    "Senior Structural Engineer": ("Structural Engineering", "Lead Structural Engineer"),
    # Technical Safety
    "Senior Technical Safety Engineer": ("Technical Safety", "Lead Technical Safety Engineer"), "Technical Safety Manager": ("Technical Safety", "Lead Technical Safety Engineer"),
    # Construction Engineering
    "Lead Construction Engineer (E&I)": ("Construction Engineering", "Lead Construction Engineer"), "Senior Construction Engineer": ("Construction Engineering", "Lead Construction Engineer"),
    "Decommissioning Co-ordinator": ("Construction Engineering", None),
    # HSES
    "HSEQ Manager": ("HSES", "HSE Advisor"),
    # Operations (the offshore and site representatives, who are not an engineering discipline)
    "OIM": ("Operations", "Operations Superintendent"), "Site Rep": ("Operations", None), "Offshore Rep": ("Operations", None), "Production Technician": ("Operations", None),
}

# Microsoft 365 titles that are not written the way the role table has them
TITLE_365 = {
    "Ops Superintendent": "Operations Superintendent", "Senior Tech Safety Engineer": "Senior Technical Safety Engineer",
    "Project Manager/Engineer": "Project Manager", "Lead Construction Engineer (Mechanical)": "Lead Construction Engineer",
    "Lead Construction (E&I)": "Lead Construction Engineer (E&I)", "Principal Piping Engineer & Senior Project Engineer": "Principal Piping Engineer",
    "Lead Structural Designer / Project Engineer": "Lead Structural Designer",
}

# CMAP default roles, for people the Microsoft 365 report has no title for
TITLE_CMAP = {
    "Technical Safety Lead Engineer": "Lead Technical Safety Engineer", "Technical Safety Senior Engineer": "Senior Technical Safety Engineer",
    "Technical Safety Manager": "Technical Safety Manager", "Piping Lead Engineer": "Lead Piping Engineer", "Piping Senior Engineer": "Senior Piping Engineer",
    "Piping Senior Designer": "Senior Piping Designer", "Draughtsman": "Senior Draughtsperson", "Electrical Senior Designer": "E&I Designer",
    "Electrical Lead Designer": "Lead Electrical & Instrument Designer", "Structural Senior Designer": "Senior Structural Designer",
    "Electrical Lead Engineer": "Lead Electrical Engineer", "Electrical Senior Engineer": "Senior Electrical Engineer",
    "Instruments Lead Engineer": "Lead Instrument Engineer", "Instruments Senior Engineer": "Senior Instrument Engineer",
    "Process Lead Engineer": "Lead Process Engineer", "Process Senior Engineer": "Senior Process Engineer",
    "Structural Lead Engineer": "Lead Structural Engineer", "Structural Senior Engineer": "Senior Structural Engineer",
    "Mechanical Lead Engineer": "Lead Mechanical Engineer", "Lead Construction Engineer": "Lead Construction Engineer",
    "Project Manager": "Project Manager", "Assistant Project Manager": "Assistant Project Manager", "Project Engineer": "Project Engineer",
    "Senior Project Engineer": "Senior Project Engineer", "Engineering Manager": "Engineering Manager", "Engineering Deputy Manager": "Engineering Deputy Manager",
    "Operations Superintendent": "Operations Superintendent", "Cost Engineer": "Cost Engineer", "Cost Lead Engineer": "Cost Engineer",
    "Planning Engineer": "Planning Engineer", "Planning Lead Engineer": "Planning Engineer", "Estimator": "Estimator",
    "Document Controller": "Document Controller", "Document Controller Lead": "Lead Document Controller", "HSE Lead Engineer": "HSE Advisor",
    "HSEQ Manager": "HSEQ Manager", "Buyer": "Buyer", "Site Rep": "Site Rep", "Offshore Rep": "Offshore Rep", "OIM": "OIM",
    "Production Technician": "Production Technician", "Project Director": "Project Director", "Project Services Manager": "Project Services Manager",
    "Non-Chargeable Admin": "Non-Chargeable Admin", "Project Services": "Project Services Support", "Decommissioning Co-ordinator": "Decommissioning Co-ordinator",
    "Technical Assistant": "Technical Assistant",
}


def key(title):
    return " ".join((title or "").replace("-Ordinator", "-ordinator").split()).casefold()


KNOWN = {key(t): t for t in list(EXISTING) + list(NEW)}
FROM_365 = {key(k): v for k, v in TITLE_365.items()}
FROM_CMAP = {key(k): v for k, v in TITLE_CMAP.items()}


def role_title(title_365, cmap_role):
    """The role-table title for a person: their Microsoft 365 title when it is one we know, else their CMAP default role. Returns (title, source)
    where source is '365', 'cmap', or (None, None) when neither is recognised."""
    t = key(title_365)
    if t:
        for cand in (FROM_365.get(t), t):
            if cand and key(cand) in KNOWN:
                return KNOWN[key(cand)], "365"
    mapped = FROM_CMAP.get(key(cmap_role))
    if mapped and key(mapped) in KNOWN:
        return KNOWN[key(mapped)], "cmap"
    return None, None


def apply_job_roles(con):
    """Add the new titles under their existing families (with placeholder rates where a nearest rated title is named); the seed's roles and families are left
    as they are. Returns (role ids by title, report dict)."""
    report = {"added": [], "placeholder": [], "unpriced": []}
    for old, new in RENAMED.items():
        con.execute("update role set name=? where name=?", (new, old))
    by_title = {r[1]: r[0] for r in con.execute("select role_id, name from role")}
    families = {r[0] for r in con.execute("select family from role")}
    nxt = {}                                                                     # the highest skill code number so far under each set of letters
    for code, in con.execute("select skill_code from role"):
        prefix, _, num = code.partition("-")
        if num.isdigit():
            nxt[prefix] = max(nxt.get(prefix, 0), int(num))
    for title, (family, like) in NEW.items():
        if title in by_title:
            continue
        prefix = PREFIX[family]
        nxt[prefix] = nxt.get(prefix, 0) + 1
        by_title[title] = con.execute("insert into role (skill_code, name, family) values (?,?,?) returning role_id", ("%s-%02d" % (prefix, nxt[prefix]), title, family)).fetchone()[0]
        report["added"].append(title)
        if family not in families:
            report.setdefault("new_families", []).append(family)
            families.add(family)
        if like and like in by_title:
            con.execute("insert into rate (client_id, role_id, sales_rate, effective_from) select client_id, ?, sales_rate, effective_from from rate where role_id=?",
                        (by_title[title], by_title[like]))
            report["placeholder"].append("%s (as %s)" % (title, like))
        else:
            report["unpriced"].append(title)
    return by_title, report
