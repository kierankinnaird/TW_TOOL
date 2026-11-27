"""Front end for the v1 mock database. Plain HTML with one inline stylesheet, served by Flask (the pages themselves are built as plain strings).

Needs Flask:   pip install -r requirements.txt
Run from the repo root:   python app/app.py        then open  http://localhost:8000
Uses the PostgreSQL database in DATABASE_URL (fill it with demo data using: python scripts/build.py; docker-compose.yml starts a local server).
There is no login: pick who you are from the "Signed in as" box (mock).
"""
import datetime
import functools
import logging
import os
import re
import sys
from flask import Flask, Response, request
from urllib.parse import parse_qs
from werkzeug.serving import make_server as make_wsgi_server
from nmc.core import ASSETS, REQUIRED_SCHEMA, can_see_reports, db, ensure_current_period, monday, no_access, parse_adds, permission_of
from nmc.timesheet import copy_week, save_timesheet, view_history, view_timesheet
from nmc.monthend import month_bounds, month_sheet, view_monthend
from nmc.approvals import act_approval, reopen_week, view_approvals, view_missing, view_reopen
from nmc.reports import (
    aaa_parse, aaa_sheet, weekly_sheet, ctr_sheet, grid_data, grid_sheet, manhour_data, manhour_sheet,
    period_sheet, report_sheet, report_view, view_ctr, view_grid,
    view_manhours, view_reports, xlsx_bytes)
from nmc.dashboard import view_dashboard
from nmc.team import view_person_timesheet, view_team
from nmc.pdf import manhour_pdf
from nmc import costreports
from nmc.admin import act_allowance_rates, act_period, admin_add, admin_save, view_admin, view_admin_edit, view_audit
from nmc import core, pg
from nmc.core import REQUIRED_SCHEMA, db, fmt, monday  # noqa: F401  (kept importable from here: the tests and scripts use app.db, app.fmt and so on)
from nmc.reports import ctr_tree_rows, xlsx_bytes  # noqa: F401


# ---------- server ----------
# ---------- The web layer (Flask) ----------
# Every page is a function above that takes the database connection and returns HTML. The routes below only read the request,
# open the database, call the right function and send the result. Query strings and forms are read with parse_qs exactly as before
# (dicts of lists), so the page functions did not change.
flask_app = Flask(__name__, static_folder=None)


flask_app.url_map.strict_slashes = False


logging.getLogger("werkzeug").setLevel(logging.ERROR)  # no request log lines on the console


def html_response(text, code=200):
    return Response(text, status=code, mimetype="text/html")


def query():
    return parse_qs(request.query_string.decode("utf-8"))


def posted():
    return parse_qs(request.get_data(as_text=True), keep_blank_values=True)


def user_id(con, q):
    try:
        return int(q["u"][0])
    except (KeyError, ValueError):
        return con.execute("select user_id from app_user order by user_id limit 1").fetchone()[0]


def with_db(fn):
    """Open the database for one request, make sure the current week exists, and close it afterwards."""
    @functools.wraps(fn)
    def wrapper():
        con = db()
        try:
            ensure_current_period(con)
            return fn(con)
        finally:
            con.close()
    return wrapper


def page(rule, *, methods=("GET",)):
    """Register a route whose function receives (con, u, q) for a page, or (con, form) for a posted form."""
    def deco(fn):
        @with_db
        def view(con):
            if request.method == "POST":
                return html_response(fn(con, posted()))
            q = query()
            return html_response(fn(con, user_id(con, q), q))
        view.__name__ = "view_" + fn.__name__
        flask_app.add_url_rule(rule, endpoint=rule, view_func=view, methods=list(methods))
        return fn
    return deco


@flask_app.after_request
def security_headers(resp):
    """Browser protections on every response: no content sniffing, no framing (clickjacking), no referrer leaks, and a policy that only
    lets the page load its own resources (the page's own inline style and script are the only inline code it has)."""
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    # same-origin, not no-referrer: Azure's sign-in layer refuses a form post that arrives with no Referer (it reads that as a forged request), and this still sends nothing to other sites
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                            "img-src 'self' data:; font-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
    resp.headers.setdefault("Cache-Control", "no-store")
    return resp


@flask_app.errorhandler(404)
@flask_app.errorhandler(405)
def not_found(_):
    return html_response(core.missing_page(), 404)


def _serve_asset(path):
    def view():
        try:
            with open(ASSETS[path][0], "rb") as f:
                data = f.read()
        except OSError:
            return html_response(core.missing_page("That file"), 404)
        return Response(data, mimetype=ASSETS[path][1], headers={"Cache-Control": "public, max-age=86400"})
    view.__name__ = "asset_" + path.strip("/").replace("/", "_").replace(".", "_")
    return view


for _path in ASSETS:
    flask_app.add_url_rule(_path, endpoint=_path, view_func=_serve_asset(_path))


@flask_app.route("/")
@flask_app.route("/timesheet")
@with_db
def timesheet_page(con):
    q = query()
    u = user_id(con, q)
    try:
        w = monday(datetime.date.fromisoformat(q["w"][0])) if "w" in q else monday(datetime.date.today())
    except ValueError:
        w = monday(datetime.date.today())
    adds = parse_adds(q.get("add", []))
    if "add_phase" in q and "add_role" in q:
        adds += parse_adds(["%s:%s:%s:%s" % (q["add_phase"][0], q["add_role"][0], q.get("add_loc", ["1"])[0], "1" if "add_ot" in q else "0")])
    return html_response(view_timesheet(con, u, w, adds=adds))


page("/history")(lambda con, u, q: view_history(con, u, q))


page("/approvals")(lambda con, u, q: view_approvals(con, u, q=q))


page("/missing")(lambda con, u, q: view_missing(con, u, q))


page("/reopen")(lambda con, u, q: view_reopen(con, u, q))


page("/monthend")(lambda con, u, q: view_monthend(con, u, q))


page("/dashboard")(lambda con, u, q: view_dashboard(con, u, q))


page("/reports")(lambda con, u, q: view_reports(con, u, q))


page("/reports/grid")(lambda con, u, q: view_grid(con, u, q))


page("/reports/manhours")(lambda con, u, q: view_manhours(con, u, q))


page("/ctr")(lambda con, u, q: view_ctr(con, u, q))


page("/admin")(lambda con, u, q: view_admin(con, u, q.get("t", [""])[0], q=q))


page("/admin/edit")(lambda con, u, q: view_admin_edit(con, u, q.get("t", [""])[0], int(q.get("id", ["0"])[0])))


page("/audit")(lambda con, u, q: view_audit(con, u, q))
page("/team")(lambda con, u, q: view_team(con, u, q))
page("/team/timesheet")(lambda con, u, q: view_person_timesheet(con, u, q))


@flask_app.route("/monthend.xlsx")
@with_db
def monthend_download(con):
    q = query()
    if permission_of(con, user_id(con, q)) != "controller":
        return html_response(no_access(con, user_id(con, q), "Month end"), 403)
    rows, sheet = month_sheet(con, q)
    return Response(xlsx_bytes(sheet, rows, (1, len(rows)) if len(rows) > 1 else (1,)), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="month-%s.xlsx"' % month_bounds(q)[0].strftime("%Y-%m")})


@flask_app.route("/reports/aaa.xlsx")
@with_db
def aaa_download(con):
    """The hours export for AAA Consulting (dates, and staff / contractors / all). Cost controllers only: it covers everyone's hours."""
    q = query()
    u = user_id(con, q)
    if permission_of(con, u) != "controller":
        return html_response(no_access(con, u, "Reports"), 403)
    rows, sheet, title, sub = aaa_sheet(con, q)
    first, last, who = aaa_parse(q)
    return Response(xlsx_bytes(sheet, rows, (1, len(rows)) if len(rows) > 1 else (1,), title, sub),
                    mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="aaa-consulting-%s-%s-to-%s.xlsx"' % (who, first.isoformat(), last.isoformat())})


@flask_app.route("/reports/weekly.xlsx")
@with_db
def weekly_download(con):
    """Each person's hours per week against their contracted hours. Cost controllers only: it covers everyone."""
    q = query()
    u = user_id(con, q)
    if permission_of(con, u) != "controller":
        return html_response(no_access(con, u, "Reports"), 403)
    rows, sheet, title, sub = weekly_sheet(con, q)
    first, last, _ = aaa_parse(q)
    return Response(xlsx_bytes(sheet, rows, (1, len(rows)) if len(rows) > 1 else (1,), title, sub),
                    mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="weekly-hours-%s-to-%s.xlsx"' % (first.isoformat(), last.isoformat())})


@flask_app.route("/reports/cost.xlsx")
@with_db
def cost_download(con):
    """Excel download of one of the cost team's reports (k = ctr_week, budget_hours, cost_week, invoice), for the client chosen. Cost controllers only."""
    q = query()
    u = user_id(con, q)
    kind = q.get("k", [""])[0]
    if permission_of(con, u) != "controller" or kind not in dict(costreports.COST_REPORTS):
        return html_response(no_access(con, u, "Reports"), 403)
    rep = costreports.report(con, kind, q)
    name = "%s - %s_%s" % (rep["title"], rep["params"]["client_name"], datetime.date.today().strftime("%d %b %Y"))
    name = re.sub(r"[^A-Za-z0-9 _&-]+", "", name).strip()
    return Response(costreports.workbook(rep, kind), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="%s.xlsx"' % name})


@flask_app.route("/reports/export.xlsx")
@with_db
def report_export(con):
    """Excel download of any one report on the Reports page (k = ctr, period, internal, allow, loc, budget, month)."""
    q = query()
    u = user_id(con, q)
    key = q.get("k", [""])[0]
    if key == "month":
        return monthend_download()
    if not can_see_reports(con, u):
        return html_response(no_access(con, u, "Reports"), 403)
    got = report_sheet(con, u, q, key)
    if got is None:
        return html_response(no_access(con, u, "Reports"), 404)
    rows, sheet = got
    bold = (1, len(rows)) if len(rows) > 1 else (1,)
    return Response(xlsx_bytes(sheet, rows, bold), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="report-%s.xlsx"' % key})


@flask_app.route("/export/table.xlsx", methods=("POST",))
def table_download():
    """Excel file from a table the browser sends as rows of text (the 'Download table as Excel' link above each table). Nothing is read from the database."""
    data = request.get_json(silent=True) or {}
    rows = data.get("rows")
    if not isinstance(rows, list) or len(rows) > 50000:
        return html_response("<p>Nothing to export.</p>", 400)
    num = re.compile(r"^-?(0|[1-9]\d*)(\.\d+)?$")
    out = []
    for r in rows:
        if not isinstance(r, list):
            return html_response("<p>Nothing to export.</p>", 400)
        out.append([float(c.replace(",", "")) if isinstance(c, str) and num.match(c.replace(",", "")) else c for c in r])
    name = str(data.get("name") or "Table")
    return Response(xlsx_bytes(name, out, (1,)), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="%s.xlsx"' % (re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-") or "table")})


@flask_app.route("/ctr.xlsx")
@with_db
def ctr_download(con):
    q = query()
    u = user_id(con, q)
    if not can_see_reports(con, u):
        return html_response(no_access(con, u, "CTR"), 403)
    got = ctr_sheet(con, u, q)
    if got is None:
        return html_response(no_access(con, u, "CTR"), 404)
    rows, sheet = got
    return Response(xlsx_bytes(sheet, rows, (1,)), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="ctr-%s.xlsx"' % re.sub(r"[^A-Za-z0-9]+", "-", sheet).strip("-")})


@flask_app.route("/reports/manhours.pdf")
@with_db
def manhours_pdf(con):
    """The man-hour report for a client as a PDF with approval boxes, to sign or to send for signature."""
    q = query()
    u = user_id(con, q)
    if not can_see_reports(con, u):
        return html_response(no_access(con, u, "Reports"), 403)
    data = manhour_data(con, u, q)
    if data is None:
        return html_response(no_access(con, u, "Reports"), 403)
    me = con.execute("select name from app_user where user_id=?", (u,)).fetchone()
    name = "%s-%s-to-%s" % (re.sub(r"[^A-Za-z0-9]+", "-", data["client"]).strip("-"), data["from"], data["to"])
    return Response(manhour_pdf(data, me[0] if me else ""), mimetype="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="manhours-%s.pdf"' % name})


@flask_app.route("/reports/grid.xlsx")
@flask_app.route("/reports/period.xlsx")
@flask_app.route("/reports/manhours.xlsx")
@with_db
def excel_download(con):
    q = query()
    u = user_id(con, q)
    if not can_see_reports(con, u):
        return html_response(no_access(con, u, "Reports"), 403)
    if request.path.endswith("manhours.xlsx"):
        data = manhour_data(con, u, q)
        if data is None:
            return html_response(no_access(con, u, "Reports"), 403)
        rows, bold, sheet = manhour_sheet(data)
        name = re.sub(r"[^A-Za-z0-9]+", "-", data["client"]).strip("-")
        return Response(xlsx_bytes(sheet, rows, bold), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": 'attachment; filename="manhours-%s.xlsx"' % name})
    rv = report_view(con, u)
    grid = request.path.endswith("grid.xlsx")
    if grid:
        rows, sheet = grid_sheet(grid_data(con, u, q, rv)), "Timesheet grid"
    else:
        rows, sheet = period_sheet(con, u, q, rv)
    bold = (1, len(rows)) if len(rows) > 1 else (1,)
    return Response(xlsx_bytes(sheet, rows, bold), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="timewriting-%s.xlsx"' % ("grid" if grid else "period")})


page("/timesheet/save", methods=("POST",))(lambda con, form: save_timesheet(con, form))


page("/timesheet/copy", methods=("POST",))(lambda con, form: copy_week(con, form))


page("/reopen/act", methods=("POST",))(lambda con, form: reopen_week(con, form))


page("/approvals/act", methods=("POST",))(lambda con, form: act_approval(con, form))


page("/admin/period", methods=("POST",))(lambda con, form: act_period(con, form))


page("/admin/add", methods=("POST",))(lambda con, form: admin_add(con, form))


page("/admin/save", methods=("POST",))(lambda con, form: admin_save(con, form))




page("/admin/allowance_rates", methods=("POST",))(lambda con, form: act_allowance_rates(con, form))




def make_server(port=8000):
    """A threaded development server on this machine (Werkzeug's, the one Flask itself uses). It has serve_forever, shutdown and server_address."""
    return make_wsgi_server("127.0.0.1", port, flask_app, threaded=True)


def schema_problem(url=None):
    """A message if the database at url (default: DATABASE_URL) cannot be reached, or was built with an older schema than this app needs, otherwise None."""
    try:
        con = pg.connect(url)
    except pg.Error as e:
        return ("Cannot reach the database: %s\nSet DATABASE_URL, or start the local server (docker compose up -d) and build it with: python scripts/build.py"
                % pg.error_text(e))
    try:
        have = pg.schema_version(con)
    finally:
        con.close()
    if have >= REQUIRED_SCHEMA:
        return None
    return ("The database was built with an older schema (version %d, this app needs %d), so pages would fail.\n"
            "Build a fresh demo database with:  python scripts/build.py  (this empties the database named in DATABASE_URL)." % (have, REQUIRED_SCHEMA))


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    problem = schema_problem(core.DB_URL)
    if problem:
        sys.exit(problem)
    print("Timewriting mock running at http://localhost:%d  (Ctrl+C to stop)" % port)
    make_server(port).serve_forever()
