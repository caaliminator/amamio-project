"""Employees — the staff list kept by the Super Admin.

Most of the workforce never signs in.  Their names live here (not in Users),
so a team head can pick who is asking for items as the requesting employee
on a purchase request instead of typing the name each time.

New employees are added by the Admin (New Records → Add new employee) and
join the list once the Super Admin approves them (additions.py).  The Super
Admin keeps the list itself: corrects or removes an entry.
"""
from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, url_for)

import additions_service as adds
import db
import profiles
import purchasing_service as pur
from security import module_required

employees_bp = Blueprint("employees", __name__)


def _rows():
    rows = sorted(db.read("employees"),
                  key=lambda e: (pur.DEPARTMENTS.index(e["department"])
                                 if e.get("department") in pur.DEPARTMENTS else 99,
                                 (e.get("last_name") or "").lower(),
                                 (e.get("first_name") or "").lower()))
    return rows


@employees_bp.route("/")
@module_required("employees")
def index():
    rows = _rows()
    counts = {d: sum(1 for e in rows if e.get("department") == d)
              for d in pur.DEPARTMENTS}
    pending = [r for r in adds.pending() if r["kind"] == "employee"]
    return render_template("employees/index.html", rows=rows,
                           departments=pur.DEPARTMENTS, counts=counts,
                           pending=pending)


@employees_bp.route("/<int:id>/edit", methods=["GET", "POST"])
@module_required("employees")
def edit(id):
    emp = db.get("employees", id)
    if not emp:
        abort(404)
    g.nav_endpoint = "employees.index"
    form = request.form
    if request.method == "POST":
        values, err = profiles.clean_employee(form, pur.DEPARTMENTS, exclude_id=id)
        if not err:
            db.update("employees", id, values)
            flash("%s saved." % values["name"], "success")
            return redirect(url_for("employees.index"))
        flash(err, "error")
    return render_template("employees/edit.html", emp=emp,
                           form=form if request.method == "POST" else emp,
                           departments=pur.DEPARTMENTS, suffixes=profiles.SUFFIXES)


@employees_bp.route("/<int:id>/delete", methods=["POST"])
@module_required("employees")
def delete(id):
    emp = db.get("employees", id)
    if emp:
        db.delete("employees", id)
        flash("%s removed from the list. Purchase requests that name them keep "
              "the name." % emp.get("name"), "success")
    return redirect(url_for("employees.index"))
