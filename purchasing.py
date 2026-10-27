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
from security import current_user, module_required

purchasing_bp = Blueprint("purchasing", __name__)

LINE_KEYS = ("pr_item_id", "include", "description", "brand_model", "qty",
             "unit", "unit_price", "requested_delivery", "vat", "remarks")


@purchasing_bp.route("/")
@module_required("purchasing")
def index():
    show = request.args.get("show") or "open"
    orders = db.read("purchase_orders")
    if show == "open":
        orders = [p for p in orders
                  if p.get("status") in (pur.PO_PREPARED, pur.PO_FOR_FINAL,
                                         pur.PO_APPROVED)]
    orders.sort(key=lambda p: (p.get("po_date") or "", p.get("id")), reverse=True)
    awaiting = []
    for r in pur.requests_awaiting_po():
        awaiting.append({"id": r["id"], "pr_no": pur.pr_number(r),
                         "purpose": r.get("purpose") or r.get("reason") or "",
                         "summary": pur.summary_line(r),
                         "department": r.get("department") or "",
                         "lines": len(pur.orderable_items(r)),
                         "needed_by": r.get("needed_by") or ""})
    return render_template("purchasing/index.html", show=show, orders=orders,
                           awaiting=awaiting, counts=pur.purchasing_counts(),
                           po_badge=pur.PO_BADGE)


def _lines_from_form(form):
    cols = {k: form.getlist(k) for k in LINE_KEYS}
    return [{k: (cols[k][i] if i < len(cols[k]) else "") for k in LINE_KEYS}
            for i in range(len(cols["pr_item_id"]))]


def _suggested_supplier(items, suppliers):
    """The directory entry matching the PR's preferred supplier, if any."""
    wanted = {(i.get("preferred_supplier") or "").strip().lower()
              for i in items if i.get("preferred_supplier")}
    for s in suppliers:
        if (s.get("name") or "").strip().lower() in wanted:
            return s
    return None


@purchasing_bp.route("/new", methods=["GET", "POST"])
@module_required("purchasing")
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
        f = form.get
        po, err = pur.create_purchase_order(
            request_id, user,
            {"po_date": f("po_date"), "expected_delivery": f("expected_delivery"),
             "payment_terms": f("payment_terms"),
             "advance_amount": f("advance_amount")},
            {"id": f("supplier_id"),
             "address": f("supplier_address"), "contact": f("supplier_contact"),
             "tin": f("supplier_tin"), "contact_person": f("supplier_contact_person"),
             "reference": f("supplier_reference")},
            {"deliver_to": f("deliver_to"), "address": f("delivery_address"),
             "instructions": f("delivery_instructions"), "contact": f("delivery_contact")},
            _lines_from_form(form),
            {"discount": f("discount"), "freight": f("freight"), "tax": f("tax"),
             "labor": f("labor"), "terms": f("terms")})
        if not err:
            flash("Purchase order %s prepared — the Admin has been notified to "
                  "check it." % po["po_no"], "success")
            return redirect(url_for("purchasing.detail", po_id=po["id"]))
        flash(err, "error")

    suppliers = sorted(db.read("suppliers"), key=lambda s: (s.get("name") or "").lower())
    suggested = _suggested_supplier(items, suppliers)
    catalog = pur.item_choices()
    known = {r["name"] for _, rows in catalog for r in rows}
    return render_template(
        "purchasing/new.html", req=req, pr_no=pur.pr_number(req), items=items,
        form=form, me=user, today=services.today_iso(),
        suppliers=[pur.supplier_card(s) for s in suppliers],
        suggested=pur.supplier_card(suggested) if suggested else None,
        payment_terms=pur.PAYMENT_TERMS, upfront_terms=pur.UPFRONT_TERMS,
        catalog=catalog, known_items=known, units=pur.units_for(catalog),
        company_name=pur.COMPANY_NAME, company_address=pur.COMPANY_ADDRESS,
        default_terms=pur.DEFAULT_TERMS, po_no=pur.next_po_no(req),
        start=1 if request.method == "POST" else 0)


def _po_or_404(po_id):
    po = db.get("purchase_orders", po_id)
    if not po:
        abort(404)
    return po


@purchasing_bp.route("/<int:po_id>")
@module_required("purchasing")
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
@module_required("purchasing")
def delivered(po_id):
    po, err = pur.mark_delivered(po_id, current_user())
    flash(err or "PO %s marked delivered." % po["po_no"], "error" if err else "success")
    return redirect(url_for("purchasing.detail", po_id=po_id))


@purchasing_bp.route("/<int:po_id>/cancel", methods=["POST"])
@module_required("purchasing")
def cancel(po_id):
    po, err = pur.cancel_purchase_order(po_id, current_user())
    flash(err or "PO %s cancelled — its items can be ordered again." % po["po_no"],
          "error" if err else "success")
    return redirect(url_for("purchasing.detail", po_id=po_id))


@purchasing_bp.route("/<int:po_id>/print")
@module_required("purchasing")
def print_po(po_id):
    return render_template("purchasing/print.html", po=_po_or_404(po_id))
