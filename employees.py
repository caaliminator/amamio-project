"""Employees — the staff list kept by the Super Admin.

Most of the workforce never signs in.  Their names live here (not in Users),
so a team head can pick who is asking for items as the requesting employee
on a purchase request instead of typing the name each time.
"""
from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, url_for)

import db
import profiles
import purchasing_service as pur
import services
from security import module_required

employees_bp = Blueprint("employees", __name__)

FIELDS = ("first_name", "middle_name", "last_name", "suffix", "position",
          "department", "employee_no", "phone")


def _clean(form, exclude_id=None):
    """Check the form.  Returns (values to store, error)."""
    values = {k: " ".join((form.get(k) or "").split()) for k in FIELDS}
    if not values["first_name"] or not values["last_name"]:
        return None, "First name and last name are required."
    if values["department"] not in pur.DEPARTMENTS:
        return None, "Choose the employee's department."
    if values["suffix"] not in profiles.SUFFIXES:
        values["suffix"] = values["suffix"][:10]
    values["name"] = profiles.full_name(values["first_name"], values["middle_name"],
                                        values["last_name"], values["suffix"])
    same = profiles.find_employee(values["name"])
    if same and same["id"] != exclude_id:
        return None, "%s is already on the list." % values["name"]
    if profiles.find_user(name=values["name"]):
        return None, ("%s has a system account — the list is for staff without "
                      "one." % values["name"])
    return values, None


def _rows():
    rows = sorted(db.read("employees"),
                  key=lambda e: (pur.DEPARTMENTS.index(e["department"])
                                 if e.get("department") in pur.DEPARTMENTS else 99,
                                 (e.get("last_name") or "").lower(),
                                 (e.get("first_name") or "").lower()))
    return rows


@employees_bp.route("/", methods=["GET", "POST"])
@module_required("employees")
def index():
    form = request.form
    if request.method == "POST":
        values, err = _clean(form)
        if not err:
            values["created_at"] = services.now_iso()
            db.insert("employees", values)
            flash("%s added to the employee list." % values["name"], "success")
            return redirect(url_for("employees.index"))
        flash(err, "error")
    rows = _rows()
    counts = {d: sum(1 for e in rows if e.get("department") == d)
              for d in pur.DEPARTMENTS}
    return render_template("employees/index.html", rows=rows, form=form,
                           departments=pur.DEPARTMENTS, counts=counts,
                           suffixes=profiles.SUFFIXES)


@employees_bp.route("/<int:id>/edit", methods=["GET", "POST"])
@module_required("employees")
def edit(id):
    emp = db.get("employees", id)
    if not emp:
        abort(404)
    g.nav_endpoint = "employees.index"
    form = request.form
    if request.method == "POST":
        values, err = _clean(form, exclude_id=id)
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
