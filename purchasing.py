"""Purchase orders — the client's Purchase Order form.

An admin prepares a PO from the approved lines of a purchase request
(supplier from the directory → dates, payment and delivery → items and
prices → totals and sign-offs → check); the Admin checks it, the Super Admin
approves it, and it is printed for the supplier, posted to the supplier's
ledger and marked delivered when the goods arrive.  The business rules live
in purchasing_service.py.
"""
from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)

import db
import purchasing_service as pur
import services
import supplier_service as sups
from security import can, current_user, module_required

purchasing_bp = Blueprint("purchasing", __name__)

LINE_KEYS = ("pr_item_id", "include", "brand_model", "qty",
             "unit", "unit_price", "requested_delivery", "remarks")


# The Purchase Orders page: a tab per stage, each a table of PO cards.
# (key, title, what it means, statuses, colour tone, icon)
PO_STAGES = [
    ("check", "Waiting for the Admin's check", "Prepared POs; the Admin checks them first.",
     (pur.PO_PREPARED,), "amber", "requests"),
    ("final", "For final approval", "Checked by the Admin — the Super Admin approves and issues it.",
     (pur.PO_FOR_FINAL,), "green-soft", "approvals"),
    ("issued", "Issued — waiting for delivery", "Approved and sent to the supplier.",
     (pur.PO_APPROVED,), "green", "truck"),
    ("delivered", "Delivered", "The goods arrived.", (pur.PO_DELIVERED,), "done", "box"),
    ("cancelled", "Cancelled", "Called off.", (pur.PO_CANCELLED,), "red", "x-circle"),
]
PO_TONE = {status: tone for _, _, _, statuses, tone, _ in PO_STAGES for status in statuses}
PO_STEPS = ["Prepared", "Checked by the Admin", "Approved & issued", "Delivered"]
PO_PROGRESS = {pur.PO_PREPARED: 1, pur.PO_FOR_FINAL: 2, pur.PO_APPROVED: 3, pur.PO_DELIVERED: 4}


def _days_until(iso):
    try:
        return (services.date.fromisoformat((iso or "")[:10])
                - services.date.fromisoformat(services.today_iso())).days
    except (TypeError, ValueError):
        return None


def po_card_row(po, user):
    """A purchase order as a row-card (purchasing/_cards.html)."""
    total = services._to_float(po.get("grand_total"))
    lines = po.get("items") or []
    subtotal = sum(services._to_float(i.get("amount")) for i in lines) or 0
    items = [{"name": i.get("description") or "Item", "brand": i.get("brand_model") or "",
              "qty": i.get("qty"), "unit": i.get("unit") or "",
              "price": services._to_float(i.get("unit_price")),
              "cost": services._to_float(i.get("amount")),
              "share": round(services._to_float(i.get("amount")) / subtotal * 100, 2) if subtotal else 0,
              "seg": n % 6 + 1, "remarks": i.get("remarks") or ""}
             for n, i in enumerate(lines)]
    pay = pur.po_payment_summary(po)
    status = po.get("status") or ""
    paid_state = ("Paid" if pay["issued"] and pay["balance"] <= 0.005 and pay["paid"] > 0
                  else "Partly paid" if pay["paid"] > 0.005 else "Unpaid")
    return {
        "id": po["id"], "po_no": po.get("po_no") or "PO #%d" % po["id"],
        "supplier": po.get("supplier_name") or "—", "supplier_id": po.get("supplier_id"),
        "status": status, "tone": PO_TONE.get(status, "done"),
        "po_date": (po.get("po_date") or "")[:10], "delivery": (po.get("expected_delivery") or "")[:10],
        "days_left": _days_until(po.get("expected_delivery")) if status in (pur.PO_PREPARED, pur.PO_FOR_FINAL, pur.PO_APPROVED) else None,
        "terms": po.get("payment_terms") or "", "total": total, "items": items,
        "progress": PO_PROGRESS.get(status, 0), "cancelled": status == pur.PO_CANCELLED,
        "pay": pay, "paid_state": paid_state,
        "your_turn": pur.can_approve(user, po), "waiting_on": pur.waiting_on(po),
        "prepared_by": po.get("prepared_by_name") or "", "checked_by": po.get("checked_by_name") or "",
        "pr_no": po.get("pr_no") or "",
    }


def po_stage_groups(rows, show="open"):
    groups = []
    for key, title, sub, statuses, tone, ico in PO_STAGES:
        if show == "open" and key in ("delivered", "cancelled"):
            continue
        items = [r for r in rows if r["status"] in statuses]
        if items:
            groups.append({"key": key, "title": title, "sub": sub, "tone": tone, "icon": ico, "rows": items,
                           "mine": sum(1 for r in items if r["your_turn"])})
    groups.sort(key=lambda g: 0 if g["mine"] else 1)
    return groups


def awaiting_rows(user):
    """Approved purchase requests with lines still to order, as row-cards
    (only the lines still to order count)."""
    import requests_bp
    users = services.users_by_id()
    out = []
    for r in pur.requests_awaiting_po():
        open_items = pur.orderable_items(r)
        part = dict(r, items=open_items, total_estimated=None)
        row = requests_bp.card_row(part, user, users)
        row["lines"] = len(open_items)
        out.append(row)
    return out


@purchasing_bp.route("/")
@module_required("po_view")
def index():
    show = request.args.get("show") or "open"
    orders = db.read("purchase_orders")
    if show == "open":
        orders = [p for p in orders
                  if p.get("status") in (pur.PO_PREPARED, pur.PO_FOR_FINAL,
                                         pur.PO_APPROVED)]
    orders.sort(key=lambda p: (p.get("po_date") or "", p.get("id")), reverse=True)
    user = current_user()
    rows = [po_card_row(p, user) for p in orders]
    awaiting = awaiting_rows(user) if can(user, "purchasing") else []
    import requests_bp
    return render_template("purchasing/index.html", show=show, rows=rows,
                           groups=po_stage_groups(rows, show), awaiting=awaiting,
                           counts=pur.purchasing_counts(), po_steps=PO_STEPS,
                           steps=requests_bp.PROGRESS_STEPS,
                           suppliers=sorted({r["supplier"] for r in rows if r["supplier"] != "—"}))


def _lines_from_form(form):
    cols = {k: form.getlist(k) for k in LINE_KEYS}
    return [{k: (cols[k][i] if i < len(cols[k]) else "") for k in LINE_KEYS}
            for i in range(len(cols["pr_item_id"]))]


def _suggested_supplier(items, suppliers):
    """The directory entry the PR names as preferred supplier, if any (by
    its id; older PRs only carry the name)."""
    by_id = {s["id"]: s for s in suppliers}
    for i in items:
        if i.get("preferred_supplier_id") in by_id:
            return by_id[i["preferred_supplier_id"]]
    wanted = {(i.get("preferred_supplier") or "").strip().lower()
              for i in items if i.get("preferred_supplier")}
    for s in suppliers:
        if (s.get("name") or "").strip().lower() in wanted:
            return s
    return None


def _po_from_form(request_id, user, form, save=True):
    """Build (and, with ``save``, store) a PO from the form's fields."""
    f = form.get
    return pur.create_purchase_order(
        request_id, user,
        {"po_date": f("po_date"), "expected_delivery": f("expected_delivery"),
         "payment_terms": f("payment_terms"),
         "advance_amount": f("advance_amount")},
        {"id": f("supplier_id"),
         "address": f("supplier_address"), "contact": f("supplier_contact"),
         "tin": f("supplier_tin"), "contact_person": f("supplier_contact_person"),
         "type": f("supplier_type"), "reference": f("supplier_reference")},
        {"deliver_to": f("deliver_to"), "address": f("delivery_address"),
         "instructions": f("delivery_instructions"), "contact": f("delivery_contact")},
        _lines_from_form(form),
        {"discount": f("discount"), "freight": f("freight"),
         "vat_rate": f("vat_rate"), "vat_type": f("vat_type"), "labor": f("labor"),
         "other_fees": f("other_fees"), "other_fees_note": f("other_fees_note"),
         "terms": f("terms")},
        save=save,
        save_to_directory=(f("save_to_directory") == "1"
                           and can(user, "suppliers")))


def _cancelled_po(req):
    """The latest cancelled PO prepared from this PR, if any."""
    return max((p for p in db.read("purchase_orders")
                if p.get("purchase_request_id") == req["id"]
                and p.get("status") == pur.PO_CANCELLED),
               key=lambda p: p["id"], default=None)


def _po_start_values(po):
    """A cancelled PO's header and totals, as form values to start from."""
    def number(v):
        v = services._to_float(v)
        return ("%g" % v) if v else ""
    values = {k: po.get(k) or "" for k in (
        "supplier_tin", "supplier_address", "supplier_contact_person",
        "supplier_contact", "supplier_type", "supplier_reference",
        "expected_delivery",
        "payment_terms", "deliver_to", "delivery_address", "delivery_contact",
        "delivery_instructions", "terms", "other_fees_note", "vat_type")}
    values.update({k: number(po.get(k)) for k in (
        "advance_amount", "discount", "freight", "labor", "other_fees",
        "vat_rate")})
    return values


@purchasing_bp.route("/new/preview", methods=["POST"])
@module_required("po_prepare")
def preview():
    """The form's last step: the PO exactly as it will print, built from the
    fields typed so far (nothing is saved)."""
    try:
        request_id = int(request.form.get("request_id"))
    except (TypeError, ValueError):
        abort(400)
    po, err = _po_from_form(request_id, current_user(), request.form, save=False)
    if err:
        return render_template("purchasing/_preview_error.html", error=err), 422
    return render_template("purchasing/_document.html", po=po, preview=True)


@purchasing_bp.route("/new", methods=["GET", "POST"])
@module_required("po_prepare")
def new():
    """The Purchase Order form, one part at a time."""
    try:
        request_id = int(request.args.get("request_id") or request.form.get("request_id"))
    except (TypeError, ValueError):
        flash("Choose an approved purchase request to order from.", "error")
        return redirect(url_for("purchasing.index"))
    req = db.get("purchase_requests", request_id)
    if not req:
        abort(404)
    items = pur.order_lines(req)
    if not items:
        flash("PR %s has no approved items left to order." % pur.pr_number(req), "error")
        return redirect(url_for("requests.detail", id=request_id))

    user = current_user()
    form = request.form
    if request.method == "POST":
        po, err = _po_from_form(request_id, user, form)
        if not err:
            flash("Purchase order %s prepared — the Admin has been notified to "
                  "check it." % po["po_no"], "success")
            return redirect(url_for("purchasing.detail", po_id=po["id"]))
        flash(err, "error")

    suppliers = sorted(db.read("suppliers"), key=lambda s: (s.get("name") or "").lower())
    suggested = _suggested_supplier(items, suppliers)
    catalog = pur.item_choices()
    # After a failed save the lines keep what was typed, not the PR values.
    edits = ({str(l["pr_item_id"]): l for l in _lines_from_form(form)}
             if request.method == "POST" else {})
    # A PO for this PR that was cancelled: the new one starts from it.
    prev, start_from, prev_lines = _cancelled_po(req), {}, {}
    if prev:
        start_from = _po_start_values(prev)
        prev_lines = {str(it["pr_item_id"]): it for it in prev.get("items") or []}
        directory = db.get("suppliers", prev.get("supplier_id")) if prev.get("supplier_id") else None
        if directory and services.supplier_approved(directory):
            suggested = directory
    cards = [pur.supplier_card(s) for s in suppliers]
    by_supplier = sups.links_by_supplier(sups.approved_links())
    linked_materials = sorted(sups.links_by_material(sups.approved_links()))
    return render_template(
        "purchasing/new.html", req=req, pr_no=pur.pr_number(req), items=items, edits=edits,
        prev_po=prev, start_from=start_from, prev_lines=prev_lines,
        form=form, me=user, today=services.today_iso(),
        suppliers=cards, supplier_options=[
            {"v": c["id"], "l": c["label"],
             "s": c["type"] or c["contact_person"] or c["address"],
             "d": {"address": c["address"], "contact": c["contact"],
                   "tin": c["tin"], "person": c["contact_person"],
                   "type": c["type"],
                   "items": sorted(by_supplier.get(c["id"], ())),
                   "edit": url_for("suppliers.edit", supplier_id=c["id"])}}
            for c in cards if c["approved"]],
        linked_materials=linked_materials,
        supplier_types=sups.SUPPLIER_TYPES,
        can_edit_suppliers=can(user, "suppliers"),
        suggested=pur.supplier_card(suggested) if suggested else None,
        payment_terms=pur.PAYMENT_TERMS, upfront_terms=pur.UPFRONT_TERMS,
        units=pur.units_for(catalog),
        company_name=pur.COMPANY_NAME, company_address=pur.COMPANY_ADDRESS,
        default_terms=pur.DEFAULT_TERMS, po_no=pur.next_po_no(req),
        start=1 if request.method == "POST" else 0)


def _po_or_404(po_id):
    po = db.get("purchase_orders", po_id)
    if not po:
        abort(404)
    return po


@purchasing_bp.route("/<int:po_id>")
@module_required("po_view")
def detail(po_id):
    po = _po_or_404(po_id)
    req = db.get("purchase_requests", po.get("purchase_request_id")) or {}
    deliveries = sorted(db.query("receiving_batches", po_id=po["id"]),
                        key=lambda b: b.get("created_at") or "")
    payments = sorted(db.query("supplier_payments", po_id=po["id"]),
                      key=lambda p: (p.get("paid_on") or "", p["id"]))
    supplier_id = po.get("supplier_id") or next(
        (s["id"] for s in db.read("suppliers")
         if (s.get("name") or "").strip().lower()
         == (po.get("supplier_name") or "").strip().lower()), None)
    return render_template("purchasing/detail.html", po=po, req=req,
                           lines=pur.lines_to_receive(po), deliveries=deliveries,
                           track=pur.po_track(po), me=current_user(),
                           stage=pur.approval_stage(po),
                           can_approve=pur.can_approve(current_user(), po),
                           waiting_on=pur.waiting_on(po),
                           pay=pur.po_payment_summary(po), payments=payments,
                           supplier_id=supplier_id,
                           badge=pur.PO_BADGE.get(po.get("status"), "badge-muted"))


@purchasing_bp.route("/<int:po_id>/approve", methods=["POST"])
@module_required("purchasing")
def approve(po_id):
    po, err = pur.approve_purchase_order(po_id, current_user())
    if err:
        flash(err, "error")
    elif po["status"] == pur.PO_FOR_FINAL:
        flash("PO %s checked — sent to the Super Admin for final approval."
              % po["po_no"], "success")
    else:
        flash("PO %s approved and issued — print it and send it to the "
              "supplier. It is now on the supplier's ledger." % po["po_no"], "success")
    return redirect(url_for("purchasing.detail", po_id=po_id))


@purchasing_bp.route("/<int:po_id>/delivered", methods=["POST"])
@module_required("po_prepare")
def delivered(po_id):
    po, err = pur.mark_delivered(po_id, current_user())
    flash(err or "PO %s marked delivered." % po["po_no"], "error" if err else "success")
    return redirect(url_for("purchasing.detail", po_id=po_id))


@purchasing_bp.route("/<int:po_id>/cancel", methods=["POST"])
@module_required("po_prepare")
def cancel(po_id):
    po, err = pur.cancel_purchase_order(po_id, current_user())
    flash(err or "PO %s cancelled — its items can be ordered again." % po["po_no"],
          "error" if err else "success")
    return redirect(url_for("purchasing.detail", po_id=po_id))


@purchasing_bp.route("/<int:po_id>/print")
@module_required("po_view")
def print_po(po_id):
    return render_template("purchasing/print.html", po=_po_or_404(po_id))
