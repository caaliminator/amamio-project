"""New Records — the Admin adds an employee, supplier, raw material or
supply; the Super Admin approves it; only then is it part of the system.

The rules live in ``additions_service``.  The supplier form is the Supplier
Directory's own wizard (suppliers.new), which sends here.
"""
from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, url_for)

import additions_service as adds
import db
import profiles
import purchasing_service as pur
import services
import supplier_service as sups
from security import current_user, module_required

additions_bp = Blueprint("additions", __name__)

SHOW = ("waiting", "approved", "rejected", "all")


def _row(r):
    return dict(r, kind_label=adds.KINDS.get(r.get("kind"), r.get("kind")),
                badge=adds.BADGE.get(r.get("status"), "badge-muted"))


@additions_bp.route("/")
@module_required("additions")
def index():
    user = current_user()
    show = request.args.get("show") or "waiting"
    if show not in SHOW:
        show = "waiting"
    rows = sorted(db.read("creation_requests"),
                  key=lambda r: r.get("created_at") or "", reverse=True)
    counts = {"waiting": sum(1 for r in rows if r.get("status") == adds.PENDING),
              "approved": sum(1 for r in rows if r.get("status") == adds.APPROVED),
              "rejected": sum(1 for r in rows if r.get("status") == adds.REJECTED),
              "all": len(rows)}
    wanted = {"waiting": adds.PENDING, "approved": adds.APPROVED,
              "rejected": adds.REJECTED}.get(show)
    if wanted:
        rows = [r for r in rows if r.get("status") == wanted]
    return render_template("additions/index.html", rows=[_row(r) for r in rows],
                           show=show, counts=counts, can_add=adds.can_add(user),
                           can_decide=adds.can_decide(user))


def _admin_only():
    if not adds.can_add(current_user()):
        abort(403)


@additions_bp.route("/new/employee", methods=["GET", "POST"])
@module_required("additions")
def new_employee():
    _admin_only()
    g.nav_endpoint = "additions.index"
    form = request.form
    if request.method == "POST":
        values, err = profiles.clean_employee(form, pur.DEPARTMENTS)
        req = None
        if not err:
            req, err = adds.submit("employee", values, current_user())
        if req:
            flash("%s sent to the Super Admin for approval. They join the employee "
                  "list once approved." % values["name"], "success")
            return redirect(url_for("additions.detail", req_id=req["id"]))
        flash(err, "error")
    return render_template("additions/employee.html", form=form,
                           departments=pur.DEPARTMENTS, suffixes=profiles.SUFFIXES)


@additions_bp.route("/new/<kind>", methods=["GET", "POST"])
@module_required("additions")
def new_item(kind):
    """Add a raw material, or a supply (packaging / production & cleaning /
    office)."""
    if kind not in ("material", "supply"):
        abort(404)
    _admin_only()
    g.nav_endpoint = "additions.index"
    form = request.form
    if request.method == "POST":
        payload, err = adds.clean_item(form, kind)
        req = None
        if not err:
            req, err = adds.submit(kind, payload, current_user())
        if req:
            flash("'%s' sent to the Super Admin for approval. It joins the "
                  "materials list once approved." % payload["name"], "success")
            return redirect(url_for("additions.detail", req_id=req["id"]))
        flash(err, "error")
    categories = adds.MATERIAL_CATEGORIES if kind == "material" else adds.SUPPLY_CATEGORIES
    groups = {}
    for m in db.read("raw_materials"):
        if m.get("category") in categories and m.get("subcategory"):
            groups.setdefault(m["category"], set()).add(m["subcategory"])
    units = sorted(set(pur.UNITS) | {m.get("unit") for m in db.read("raw_materials")
                                     if m.get("unit")}, key=str.lower)
    return render_template(
        "additions/item.html", kind=kind, form=form,
        kind_label=adds.KINDS[kind], categories=categories,
        category_labels=services.MATERIAL_CATEGORY_LABELS,
        groups={k: sorted(v) for k, v in groups.items()}, units=units,
        track_modes=services.TRACK_MODES, storage=adds.STORAGE,
        suppliers=sorted(db.read("suppliers"), key=lambda s: (s.get("name") or "").lower()),
        supplier_label=sups.display_name)


@additions_bp.route("/<int:req_id>")
@module_required("additions")
def detail(req_id):
    g.nav_endpoint = "additions.index"
    req = db.get("creation_requests", req_id)
    if not req:
        abort(404)
    user = current_user()
    link = adds.record_link(req)
    if link and link[0] == "employees.index" and not adds.can_decide(user):
        link = None                       # the employee list is the Super Admin's
    return render_template(
        "additions/detail.html", r=_row(req), details=adds.details(req),
        can_decide=adds.can_decide(user) and req.get("status") == adds.PENDING,
        can_withdraw=(req.get("status") == adds.PENDING
                      and req.get("requested_by") == user["id"]),
        record_url=url_for(link[0], **link[1]) if link else "",
        problem=adds.problem(req["kind"], adds.payload_of(req))
        if req.get("status") == adds.PENDING else None)


@additions_bp.route("/<int:req_id>/approve", methods=["POST"])
@module_required("additions")
def approve(req_id):
    req, err = adds.approve(req_id, current_user())
    if err:
        flash(err, "error")
    else:
        flash("Approved — %s is now part of the system." % req["title"], "success")
    return redirect(url_for("additions.detail", req_id=req_id))


@additions_bp.route("/<int:req_id>/reject", methods=["POST"])
@module_required("additions")
def reject(req_id):
    req, err = adds.reject(req_id, current_user(), request.form.get("reason"))
    if err:
        flash(err, "error")
    else:
        flash("Rejected — the Admin has been told why.", "success")
    return redirect(url_for("additions.detail", req_id=req_id))


@additions_bp.route("/<int:req_id>/withdraw", methods=["POST"])
@module_required("additions")
def withdraw(req_id):
    req, err = adds.withdraw(req_id, current_user())
    flash(err or "Withdrawn — it will not be added.", "error" if err else "success")
    return redirect(url_for("additions.detail", req_id=req_id))
