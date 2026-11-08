"""Supplier ledger — what is owed each supplier, and what has been paid.

Every supplier in the directory has a ledger.  A purchase order is posted to
it once the Super Admin approves (issues) it; payments — advance, partial or
full — are recorded here, optionally against one PO.  The rules live in
purchasing_service.py (ledger_entries, record_payment).
"""
from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, send_file, url_for)

import os

import db
import finance_service as fin
import purchasing_service as pur
import services
import uploads
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
        accounts=fin.active_accounts(), today=services.today_iso())


@ledger_bp.route("/print")
@module_required("ledger")
def print_summary():
    """Every supplier's balance on one page, for printing."""
    show = request.args.get("show") or "active"
    rows = pur.ledger_summary()
    if show == "active":
        rows = [r for r in rows if r["active"]]
    totals = {k: round(sum(r[k] for r in rows), 2)
              for k in ("purchases", "payments", "balance")}
    return render_template("ledger/print_summary.html", rows=rows,
                           totals=totals, show=show, today=services.today_iso())


@ledger_bp.route("/<int:supplier_id>/print")
@module_required("ledger")
def print_statement(supplier_id):
    """A supplier's ledger as a printable statement.  With a date range, the
    balance before it is brought forward."""
    sup = db.get("suppliers", supplier_id)
    if not sup:
        abort(404)
    date_from = request.args.get("from") or ""
    date_to = request.args.get("to") or services.today_iso()
    entries = pur.ledger_entries(supplier_id)
    before = [e for e in entries if date_from and (e["date"] or "")[:10] < date_from]
    shown = [e for e in entries if e not in before and (e["date"] or "")[:10] <= date_to]
    opening = before[-1]["balance"] if before else 0.0
    return render_template(
        "ledger/print.html", supplier=sup, entries=shown, opening=opening,
        date_from=date_from, date_to=date_to,
        purchases=round(sum(e["credit"] for e in shown), 2),
        payments=round(sum(e["debit"] for e in shown), 2),
        balance=shown[-1]["balance"] if shown else opening)


@ledger_bp.route("/<int:supplier_id>/pay", methods=["POST"])
@module_required("ledger")
def pay(supplier_id):
    payment, err = pur.record_payment(supplier_id, current_user(), request.form,
                                      proof=request.files.get("proof"),
                                      attachments=request.files.getlist("attachments"))
    if err:
        flash(err, "error")
        return redirect(url_for("ledger.supplier", supplier_id=supplier_id,
                                po=request.form.get("po_id") or None) + "#pay")
    flash("Payment of %s to %s recorded." % (services.fmt_peso(payment["amount"]),
                                            payment["supplier_name"]), "success")
    return redirect(url_for("ledger.supplier", supplier_id=supplier_id))


def _payment_or_404(payment_id):
    payment = db.get("supplier_payments", payment_id)
    if not payment:
        abort(404)
    return payment


@ledger_bp.route("/payments/<int:payment_id>")
@module_required("ledger")
def payment(payment_id):
    """One payment: its details, proof of payment and attachments, and a form
    to attach more (e.g. the official receipt that came later)."""
    pay = _payment_or_404(payment_id)
    g.nav_endpoint = "ledger.index"
    po = db.get("purchase_orders", pay.get("po_id")) if pay.get("po_id") else None
    return render_template("ledger/payment.html", p=pay, po=po,
                           attachments=pay.get("attachments") or [],
                           max_attachments=pur.MAX_ATTACHMENTS)


@ledger_bp.route("/payments/<int:payment_id>/attachments", methods=["POST"])
@module_required("ledger")
def add_attachments(payment_id):
    pay, err = pur.add_payment_attachments(payment_id, current_user(),
                                           request.files.getlist("attachments"),
                                           request.form.get("attachments_label"))
    if err:
        flash(err, "error")
    else:
        flash("Attached to the payment of %s." % services.fmt_peso(pay["amount"]), "success")
    return redirect(url_for("ledger.payment", payment_id=payment_id))


@ledger_bp.route("/payments/<int:payment_id>/files/<int:n>")
@module_required("ledger")
def attachment(payment_id, n):
    """One attachment of a payment (picture or PDF) — ledger users only."""
    pay = _payment_or_404(payment_id)
    att = next((a for a in pay.get("attachments") or [] if a.get("n") == n), None)
    path = uploads.picture_path("payments", (att or {}).get("file"))
    if not path:
        abort(404)
    is_pdf = att.get("kind") == "pdf"
    return send_file(path, max_age=3600,
                     mimetype="application/pdf" if is_pdf else "image/jpeg",
                     download_name=att.get("name") or os.path.basename(path))


@ledger_bp.route("/payments/<int:payment_id>/proof")
@module_required("ledger")
def proof(payment_id):
    """The picture of a payment's receipt — ledger users only."""
    payment = db.get("supplier_payments", payment_id)
    path = uploads.picture_path("payments", (payment or {}).get("proof"))
    if not path:
        abort(404)
    return send_file(path, max_age=3600)
