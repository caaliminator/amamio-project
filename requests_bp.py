"""Purchase requests — the client's Purchase Request Form.

A head raises a PR in a short step-by-step flow (what it's for → the items,
picked from dropdowns → expected date, requestor and notes → check); the
Admin checks it and the Super Admin gives the final approval under
Approvals; an admin then prepares the Purchase Order from it (purchasing.py).  Also kept: the recipe-shortfall
request for a production run, and the BOM auto-calc it uses.
"""
from flask import (Blueprint, abort, flash, jsonify, redirect, render_template,
                   request, url_for)

import db
import profiles
import purchasing_service as pur
import services
import supplier_service as sups
from security import (current_user, is_admin, module_required,
                      role_required)

requests_bp = Blueprint("requests", __name__)

OPEN_STATUSES = (pur.PR_PENDING, pur.FOR_FINAL, "Approved", "Revised", "Ordered")

# The list is split by where each request stands, one section per stage:
# (key, title, what it means, statuses, colour tone).
STAGE_GROUPS = [
    ("final", "For final approval", "Checked by the Admin — the Super Admin gives the last approval.",
     (pur.FOR_FINAL,), "green-soft"),
    ("check", "Waiting for the Admin's check", "New requests; the Admin checks them first.",
     (pur.PR_PENDING,), "amber"),
    ("approved", "Approved — ready for a purchase order", "Fully approved; the Admin prepares the PO.",
     ("Approved", "Revised"), "green"),
    ("ordered", "Ordered — waiting for delivery", "A PO was issued; the items are on their way.",
     ("Ordered",), "blue"),
    ("received", "Received", "Everything on the request was delivered.", ("Received",), "done"),
    ("rejected", "Rejected", "Not approved.", ("Rejected",), "red"),
]
STATUS_TONE = {status: tone for _, _, _, statuses, tone in STAGE_GROUPS for status in statuses}
# How far along a request is, out of PROGRESS_STEPS (the ring on the list).
PROGRESS_STEPS = ["Submitted", "Checked by the Admin", "Approved", "Ordered", "Received"]
PROGRESS = {pur.PR_PENDING: 1, pur.FOR_FINAL: 2, "Approved": 3, "Revised": 3,
            "Ordered": 4, "Received": 5}
ITEM_TONE = {"Ordered": "blue", "Received": "done", "Rejected": "red"}


def _item_rows(r, total):
    """A request's lines for the list: name, quantity, estimated cost (and
    its share of the total, for the cost bar), supplier and where it stands."""
    out = []
    for i, it in enumerate(r.get("items") or []):
        cost = services._to_float(it.get("est_total"))
        if not cost:
            cost = services._to_float(it.get("est_unit_cost")) * services._to_float(it.get("requested_qty"))
        status = it.get("status") or ""
        out.append({
            "name": it.get("name") or "Item", "qty": it.get("approved_qty") or it.get("requested_qty"),
            "unit": it.get("unit") or "", "cost": round(cost, 2),
            "share": round(cost / total * 100, 2) if total else 0,
            "seg": i % 6 + 1, "supplier": it.get("preferred_supplier") or "",
            "status": status if status not in ("", "Pending") else "Requested",
            "tone": ITEM_TONE.get(status, "done"), "po_no": it.get("po_no") or "",
        })
    return out


def _days_left(iso):
    try:
        return (services.date.fromisoformat(iso[:10]) - services.date.fromisoformat(services.today_iso())).days
    except (TypeError, ValueError, AttributeError):
        return None
URGENCY_RANK = {"Critical": 0, "Urgent": 1, "Normal": 2}


def _groups(rows, user):
    """The rows in stage sections, most urgent first in each, newest next.
    The section waiting on this user (their turn to approve) comes first."""
    out = []
    for key, title, sub, statuses, tone in STAGE_GROUPS:
        items = [r for r in rows if r["status"] in statuses]
        if not items:
            continue
        items.sort(key=lambda r: r["created_at"], reverse=True)
        items.sort(key=lambda r: URGENCY_RANK.get(r["urgency"], 2))
        out.append({"key": key, "title": title, "sub": sub, "tone": tone, "rows": items,
                    "mine": sum(1 for r in items if r["your_turn"])})
    known = {s for g in STAGE_GROUPS for s in g[3]}
    other = [r for r in rows if r["status"] not in known]
    if other:
        out.append({"key": "other", "title": "Other", "sub": "", "tone": "done", "rows": other, "mine": 0})
    out.sort(key=lambda g: 0 if g["mine"] else 1)       # stable: keeps the stage order
    return out


def _row(r, users):
    return {
        "id": r.get("id"), "pr_no": pur.pr_number(r),
        "department": r.get("department") or ("Production" if r.get("type") == "production" else ""),
        "purpose": r.get("purpose") or r.get("reason") or "",
        "summary": pur.summary_line(r),
        "requested_by": r.get("requested_by_name")
                        or (users.get(r.get("requestor_id")) or {}).get("name", "—"),
        "date_requested": r.get("date_requested") or (r.get("created_at") or "")[:10],
        "needed_by": r.get("needed_by") or "",
        "total": pur.total_estimated(r),
        "status": r.get("status"),
        "badge": services.REQUEST_STATUS_BADGE.get(r.get("status"), "badge-muted"),
        "urgency": r.get("urgency") or "Normal",
        "po_nos": sorted({i.get("po_no") for i in r.get("items") or [] if i.get("po_no")}),
        "tone": STATUS_TONE.get(r.get("status"), "done"),
        "created_at": r.get("created_at") or "",
        "progress": PROGRESS.get(r.get("status"), 0),
        "rejected": r.get("status") == "Rejected",
        "days_left": _days_left(r.get("needed_by") or ""),
    }


@requests_bp.route("/")
@module_required("requests")
def index():
    user = current_user()
    show = request.args.get("show") or "open"
    reqs = db.read("purchase_requests")
    if not is_admin(user):
        reqs = [r for r in reqs if r.get("requestor_id") == user["id"]]
    if show == "open":
        reqs = [r for r in reqs if r.get("status") in OPEN_STATUSES]
    reqs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    users = services.users_by_id()
    rows = []
    for r in reqs:
        row = _row(r, users)
        row["your_turn"] = pur.can_approve(user, r)
        row["items"] = _item_rows(r, row["total"])
        rows.append(row)
    return render_template("requests/index.html", show=show, rows=rows,
                           groups=_groups(rows, user), steps=PROGRESS_STEPS)


def _lines_from_form(form):
    keys = ("material_id", "description", "purpose_use", "qty", "unit",
            "est_unit_cost", "preferred_supplier", "preferred_supplier_id",
            "budget_category", "remark")
    cols = {k: form.getlist(k) for k in keys}
    return [{k: (cols[k][i] if i < len(cols[k]) else "") for k in keys}
            for i in range(len(cols["description"]))]


def _pr_from_form(user, form, save=True):
    """Build (and, with ``save``, store) a PR from the form's fields."""
    return pur.create_purchase_request(
        user, form.get("department") or "", form.get("purpose") or "",
        form.get("needed_by") or "", _lines_from_form(form),
        notes=form.get("notes") or "", urgency=form.get("urgency") or "Normal",
        requested_by_name=form.get("requested_by_name") or "", save=save)


@requests_bp.route("/new/preview", methods=["POST"])
@module_required("requests")
def preview():
    """The form's last step: the PR exactly as it will print, built from the
    fields typed so far (nothing is saved)."""
    req, err = _pr_from_form(current_user(), request.form, save=False)
    if err:
        return render_template("requests/_preview_error.html", error=err), 422
    return render_template("requests/_document.html", req=req,
                           row=_row(req, services.users_by_id()),
                           items=_items_view(req), decided_on="", preview=True)


@requests_bp.route("/new", methods=["GET", "POST"])
@module_required("requests")
def new():
    """The Purchase Request Form, one part at a time."""
    user = current_user()
    form = request.form
    lines = []
    if request.method == "POST":
        lines = _lines_from_form(form)
        req, err = _pr_from_form(user, form)
        if not err:
            flash("Purchase request %s submitted for approval — the Admin has "
                  "been notified to check it." % req["pr_no"], "success")
            return redirect(url_for("requests.detail", id=req["id"]))
        flash(err, "error")
    catalog = pur.item_choices()
    # Options for the type-to-search boxes (app.js "combo").
    item_options = [{"v": r["id"], "l": r["name"], "g": group, "s": r["unit"],
                     "d": {"unit": r["unit"], "sid": r["supplier_id"]}}
                    for group, rows in catalog for r in rows]
    supplier_options = _supplier_options()
    # Requesting employee: the staff list (people without an account), the
    # head's own department first.
    employee_options = profiles.employee_options(pur.DEPARTMENTS,
                                                 first=pur.department_for(user))
    return render_template(
        "requests/new.html", form=form, lines=lines, me=user,
        item_options=item_options, supplier_options=supplier_options,
        employee_options=employee_options,
        departments=pur.DEPARTMENTS, my_department=pur.department_for(user),
        catalog=catalog, units=pur.units_for(catalog),
        categories=pur.BUDGET_CATEGORIES,
        urgencies=services.URGENCY_LEVELS, today=services.today_iso(),
        next_no=pur.next_pr_no(), start=1 if request.method == "POST" else 0)


def _supplier_options():
    """Approved suppliers for the "Preferred supplier" search box, each with
    the catalog items it supplies (the page narrows the list per item)."""
    everyone = db.read("suppliers")
    by_supplier = sups.links_by_supplier()
    out = []
    for s in sups.approved_suppliers():
        phones = sups.phone_list(s.get("phone"))
        out.append({"v": s["id"], "l": sups.display_name(s, everyone),
                    "s": s.get("supplier_type") or s.get("category") or "",
                    "d": {"type": s.get("supplier_type") or "",
                          "person": s.get("contact_person") or "",
                          "phone": ", ".join(phones),
                          "items": sorted(by_supplier.get(s["id"], ()))}})
    return out


@requests_bp.route("/bom_calc")
@module_required("requests")
def bom_calc():
    product_id = request.args.get("product_id")
    qty = request.args.get("qty")
    try:
        rows = services.requirements_for(int(product_id), float(qty))
    except (TypeError, ValueError):
        return jsonify({"rows": []})
    return jsonify({"rows": rows})

@requests_bp.route("/new/production", methods=["GET", "POST"])
@role_required("production_head")
def new_production():
    if request.method == "POST":
        product_id = request.form.get("product_id")
        qty = request.form.get("qty")
        remarks = request.form.get("remarks") or ""
        reason = (request.form.get("reason") or "").strip()
        needed_by = request.form.get("needed_by") or ""
        urgency = request.form.get("urgency") or "Normal"
        material_ids = request.form.getlist("material_id")
        requested = request.form.getlist("requested_qty")
        notes = request.form.getlist("note")
        try:
            product_id = int(product_id)
            qty = float(qty)
        except (TypeError, ValueError):
            flash("Choose a product and quantity.", "error")
            return redirect(url_for("requests.new_production"))
        if not reason:
            flash("Say why this request is needed — the approver sees it.",
                  "error")
            return redirect(url_for("requests.new_production"))

        lines = []
        for i, mid in enumerate(material_ids):
            try:
                rq = float(requested[i]) if i < len(requested) else 0
            except ValueError:
                rq = 0
            if rq > 0:
                lines.append({"material_id": int(mid), "requested_qty": rq,
                              "note": notes[i] if i < len(notes) else ""})
        if not lines:
            flash("At least one material must have a request quantity above zero.",
                  "error")
            return redirect(url_for("requests.new_production"))

        req = services.create_production_request(
            current_user()["id"], product_id, qty, lines, reason=reason,
            remarks=remarks, needed_by=needed_by, urgency=urgency)
        flash("Purchase request %s submitted for approval." % req["pr_no"], "success")
        return redirect(url_for("requests.detail", id=req["id"]))

    return render_template("requests/new_production.html",
                           products=db.read("products"),
                           reasons=services.REQUEST_REASONS,
                           urgencies=services.URGENCY_LEVELS,
                           today=services.today_iso())


@requests_bp.route("/new/material")
@module_required("requests")
def new_material():
    """Older link: supplies / equipment are requested on the PR form."""
    return redirect(url_for("requests.new"))


def _request_or_404(id):
    user = current_user()
    req = db.get("purchase_requests", id)
    if not req:
        abort(404)
    if not is_admin(user) and req.get("requestor_id") != user["id"]:
        abort(403)
    return req


def _items_view(req):
    """PR lines with names and units filled in for older requests too."""
    materials = {m["id"]: m for m in db.read("raw_materials")}
    rows = []
    for it in req.get("items") or []:
        row = dict(it)
        mat = materials.get(it.get("material_id")) or {}
        row["name"] = it.get("name") or mat.get("name") or "Item %s" % it.get("item_id")
        row["unit"] = it.get("unit") or mat.get("unit") or "pc"
        row["est_unit_cost"] = it.get("est_unit_cost")
        row["est_total"] = it.get("est_total")
        rows.append(row)
    return rows


@requests_bp.route("/<int:id>")
@module_required("requests")
def detail(id):
    req = _request_or_404(id)
    users = services.users_by_id()
    orders = [p for p in db.read("purchase_orders")
              if p.get("purchase_request_id") == req["id"]]
    return render_template(
        "requests/detail.html", req=req, row=_row(req, users),
        items=_items_view(req), orders=orders, po_badge=pur.PO_BADGE,
        status_badge=services.REQUEST_STATUS_BADGE,
        urgency_badge=services.URGENCY_BADGE,
        can_order=is_admin(current_user()) and bool(pur.orderable_items(req)),
        can_decide=pur.can_approve(current_user(), req),
        waiting_on=pur.waiting_on(req),
        decided_on=(req.get("decided_at") or "")[:10])


@requests_bp.route("/<int:id>/print")
@module_required("requests")
def print_form(id):
    req = _request_or_404(id)
    users = services.users_by_id()
    return render_template("requests/print.html", req=req,
                           row=_row(req, users), items=_items_view(req),
                           decided_on=(req.get("decided_at") or "")[:10])
