"""Supplier ledger — what is owed each supplier, and what has been paid.

Every supplier in the directory has a ledger.  A purchase order is posted to
it once the Super Admin approves (issues) it; payments — advance, partial or
full — are recorded here, optionally against one PO.  The rules live in
purchasing_service.py (ledger_entries, record_payment).
"""
from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)

import db
import purchasing_service as pur
import services
from security import current_user, module_required

ledger_bp = Blueprint("ledger", __name__)


@ledger_bp.route("/")
@module_required("ledger")
def index():
    show = request.args.get("show") or "active"
    rows = pur.ledger_summary()
    totals = {"purchases": round(sum(r["purchases"] for r in rows), 2),
              "payments": round(sum(r["payments"] for r in rows), 2),
              "balance": round(sum(r["balance"] for r in rows), 2),
              "owing": sum(1 for r in rows if r["balance"] > 0.005)}
    if show == "active":
        rows = [r for r in rows if r["active"]]
    return render_template("ledger/index.html", rows=rows, totals=totals,
                           show=show)


@ledger_bp.route("/<int:supplier_id>")
@module_required("ledger")
def supplier(supplier_id):
    sup = db.get("suppliers", supplier_id)
    if not sup:
        abort(404)
    entries = pur.ledger_entries(supplier_id)
    payable = pur.payable_orders(supplier_id)
    try:
        picked = int(request.args.get("po") or 0)
    except ValueError:
        picked = 0
    chosen = next((p for p in payable if p["id"] == picked), None)
    if chosen:
        default_amount = chosen["advance_due"] or chosen["balance"]
        default_kind = (chosen["payment_terms"] if chosen["advance_due"]
                        and chosen.get("payment_terms") in pur.UPFRONT_TERMS
                        else "Full payment")
    else:
        default_amount, default_kind = "", "Full payment"
    return render_template(
        "ledger/supplier.html", supplier=sup, entries=entries,
        payable=payable, chosen=chosen, default_amount=default_amount,
        default_kind=default_kind,
        purchases=round(sum(e["credit"] for e in entries), 2),
        payments=round(sum(e["debit"] for e in entries), 2),
        balance=entries[-1]["balance"] if entries else 0.0,
        kinds=pur.PAYMENT_KINDS, methods=pur.PAYMENT_METHODS,
        today=services.today_iso())


@ledger_bp.route("/<int:supplier_id>/pay", methods=["POST"])
@module_required("ledger")
def pay(supplier_id):
    payment, err = pur.record_payment(supplier_id, current_user(), request.form)
    if err:
        flash(err, "error")
        return redirect(url_for("ledger.supplier", supplier_id=supplier_id,
                                po=request.form.get("po_id") or None) + "#pay")
    flash("Payment of %s to %s recorded." % (services.fmt_peso(payment["amount"]),
                                            payment["supplier_name"]), "success")
    return redirect(url_for("ledger.supplier", supplier_id=supplier_id))
