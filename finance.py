"""Finance — payments due to suppliers, and the cash & bank accounts.

The Supplier Ledger itself is ledger.py; the rules are in finance_service.py.
"""
from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)

import db
import finance_service as fin
import services
from security import can, current_user, module_required

finance_bp = Blueprint("finance", __name__)


def _account_or_404(account_id):
    account = db.get("money_accounts", account_id)
    if not account:
        abort(404)
    return account


@finance_bp.route("/due")
@module_required("ledger")
def due():
    rows = fin.payments_due()
    return render_template(
        "finance/due.html", rows=rows, aging=fin.aging(rows),
        total=round(sum(r["balance"] for r in rows), 2),
        due_now=round(sum(r["advance_due"] if r["advance_due"] > 0.005 else r["balance"]
                          for r in rows if r["due_now"]), 2))


@finance_bp.route("/accounts", methods=["GET", "POST"])
@module_required("accounts")
def accounts():
    form = request.form
    if request.method == "POST":
        if not can(current_user(), "accounts_transact"):
            abort(403)          # adding an account is the Super Admin's
        account, err = fin.save_account(form, current_user())
        if not err:
            flash("Account %s added." % account["name"], "success")
            return redirect(url_for("finance.account", account_id=account["id"]))
        flash(err, "error")
    rows = fin.accounts()
    return render_template(
        "finance/accounts.html", rows=rows, form=form, kinds=fin.ACCOUNT_KINDS,
        total=round(sum(a["balance"] for a in rows if a.get("active", True)), 2),
        today=services.today_iso(),
        adding=can(current_user(), "accounts_transact") and (request.method == "POST" or not rows))


@finance_bp.route("/accounts/<int:account_id>")
@module_required("accounts")
def account(account_id):
    acc = _account_or_404(account_id)
    entries = fin.account_entries(acc)
    return render_template(
        "finance/account.html", account=acc, entries=entries,
        balance=entries[-1]["balance"], kinds=fin.ACCOUNT_KINDS,
        entry_kinds=fin.ENTRY_KINDS, transfer=fin.TRANSFER,
        others=[a for a in fin.active_accounts() if a["id"] != acc["id"]],
        today=services.today_iso())


@finance_bp.route("/accounts/<int:account_id>/edit", methods=["POST"])
@module_required("accounts_transact")
def edit_account(account_id):
    _account_or_404(account_id)
    account, err = fin.save_account(request.form, current_user(), account_id)
    flash(err or "Account %s saved." % account["name"], "error" if err else "success")
    return redirect(url_for("finance.account", account_id=account_id))


@finance_bp.route("/accounts/<int:account_id>/entry", methods=["POST"])
@module_required("accounts_transact")
def entry(account_id):
    acc = _account_or_404(account_id)
    entries, err = fin.record_entry(acc, current_user(), request.form)
    if err:
        flash(err, "error")
    else:
        flash("%s of %s recorded on %s." % (
            entries[0]["kind"], services.fmt_peso(entries[0]["amount"]), acc["name"]),
            "success")
    return redirect(url_for("finance.account", account_id=account_id))


@finance_bp.route("/accounts/<int:account_id>/active", methods=["POST"])
@module_required("accounts_transact")
def toggle_account(account_id):
    acc = _account_or_404(account_id)
    active = not acc.get("active", True)
    fin.set_active(account_id, active)
    flash("%s %s." % (acc["name"], "reopened" if active else
                      "closed — it stays on record but can't be paid from"), "success")
    return redirect(url_for("finance.account", account_id=account_id))


@finance_bp.route("/accounts/<int:account_id>/print")
@module_required("accounts")
def print_account(account_id):
    acc = _account_or_404(account_id)
    entries = fin.account_entries(acc)
    return render_template("finance/account_print.html", account=acc, entries=entries,
                           balance=entries[-1]["balance"], today=services.today_iso())
