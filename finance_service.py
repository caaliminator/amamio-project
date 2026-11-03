"""Finance — the money accounts, and what is due to suppliers.

Finance adds its own cash, bank and e-wallet accounts (nothing is fixed).
An account's balance is its opening balance, plus money put in, less money
taken out — and supplier payments recorded on the ledger come out of the
account they were paid from.  A transfer between two accounts is a money-out
and a money-in sharing one transfer_ref.

What is owed per PO lives in purchasing_service.py (po_payment_summary,
payable_orders); payments_due() lists it across every supplier.
"""
import secrets
from datetime import date

import db
import purchasing_service as pur
from services import _to_float, now_iso, today_iso

ACCOUNT_KINDS = ["Cash on hand", "Bank", "E-wallet"]
MONEY_IN, MONEY_OUT = "in", "out"
ENTRY_KINDS = {
    MONEY_IN: ["Deposit / cash in", "Capital / funding", "Other money in"],
    MONEY_OUT: ["Withdrawal / cash out", "Bank charges", "Other money out"],
}
TRANSFER = "Transfer"
AGE_BUCKETS = [("0–30 days", 0, 30), ("31–60 days", 31, 60), ("Over 60 days", 61, None)]


# --- Accounts ---------------------------------------------------------------
def accounts(active_only=False):
    """Every account with its balance, active ones first, by name."""
    rows = [dict(a, balance=account_balance(a)) for a in db.read("money_accounts")
            if a.get("active", True) or not active_only]
    rows.sort(key=lambda a: (not a.get("active", True), (a.get("name") or "").lower()))
    return rows


def active_accounts():
    return accounts(active_only=True)


def account_balance(account):
    return account_entries(account)[-1]["balance"]


def account_entries(account):
    """The account's statement, oldest first, with a running balance — the
    opening balance, money in / out and the supplier payments made from it."""
    rows = [{"date": account.get("opening_date") or (account.get("created_at") or "")[:10],
             "ref": "", "particulars": "Opening balance",
             "money_in": 0.0, "money_out": 0.0, "opening": True}]
    for e in db.query("money_entries", account_id=account["id"]):
        amount = _to_float(e.get("amount"))
        incoming = e.get("direction") == MONEY_IN
        rows.append({
            "date": e.get("entry_date"), "order": e.get("created_at") or "", "ref": e.get("reference") or "",
            "particulars": e.get("kind") + (" — %s" % e["notes"] if e.get("notes") else ""),
            "money_in": amount if incoming else 0.0,
            "money_out": 0.0 if incoming else amount,
            "recorded_by": e.get("recorded_by_name") or ""})
    po_nos = {p["id"]: p.get("po_no") for p in db.read("purchase_orders")}
    for pay in db.query("supplier_payments", account_id=account["id"]):
        bits = ["Paid %s" % (pay.get("supplier_name") or "supplier"), pay.get("kind")]
        if pay.get("po_id"):
            bits.append("PO %s" % po_nos.get(pay["po_id"], pay["po_id"]))
        rows.append({
            "date": pay.get("paid_on"), "order": pay.get("created_at") or "", "ref": pay.get("reference") or "",
            "particulars": " · ".join(b for b in bits if b),
            "money_in": 0.0, "money_out": _to_float(pay.get("amount")),
            "recorded_by": pay.get("recorded_by_name") or "",
            "supplier_id": pay.get("supplier_id")})
    # The opening balance always heads the statement.
    rows[1:] = sorted(rows[1:], key=lambda r: (r["date"] or "", r["order"]))
    balance = round(_to_float(account.get("opening_balance")), 2)
    rows[0]["balance"] = balance
    for r in rows[1:]:
        balance = round(balance + r["money_in"] - r["money_out"], 2)
        r["balance"] = balance
    return rows


def save_account(form, user, account_id=None):
    """Add an account, or change one.  Returns (account, error)."""
    name = (form.get("name") or "").strip()
    if not name:
        return None, "Give the account a name, e.g. Cash on hand or BDO Savings."
    taken = [a for a in db.read("money_accounts")
             if (a.get("name") or "").strip().lower() == name.lower() and a["id"] != account_id]
    if taken:
        return None, "There is already an account called %s." % name
    kind = form.get("kind") if form.get("kind") in ACCOUNT_KINDS else ACCOUNT_KINDS[0]
    opening = round(_to_float(form.get("opening_balance")), 2)
    if opening < 0:
        return None, "The opening balance can't be below zero."
    opening_date = (form.get("opening_date") or today_iso()).strip()
    if opening_date > today_iso():
        return None, "The opening balance date can't be in the future."
    fields = {"name": name, "kind": kind,
              "bank": (form.get("bank") or "").strip() if kind != "Cash on hand" else "",
              "account_no": (form.get("account_no") or "").strip() if kind != "Cash on hand" else "",
              "opening_balance": opening, "opening_date": opening_date,
              "notes": (form.get("notes") or "").strip()}
    if account_id:
        return db.update("money_accounts", account_id, fields), None
    return db.insert("money_accounts", dict(fields, active=True, created_by=user["id"],
                                            created_at=now_iso())), None


def set_active(account_id, active):
    return db.update("money_accounts", account_id, {"active": bool(active)})


def record_entry(account, user, form):
    """Money in, money out, or a transfer to another account.
    Returns (entries, error)."""
    if not account.get("active", True):
        return None, "%s is closed — reopen it first." % account["name"]
    direction = form.get("direction")
    amount = round(_to_float(form.get("amount")), 2)
    if amount <= 0:
        return None, "Enter the amount."
    on = (form.get("entry_date") or today_iso()).strip()
    if on > today_iso():
        return None, "The date can't be in the future."
    base = {"entry_date": on, "amount": amount,
            "reference": (form.get("reference") or "").strip(),
            "notes": (form.get("notes") or "").strip(),
            "recorded_by": user["id"], "recorded_by_name": user.get("name") or "",
            "created_at": now_iso()}
    if direction == TRANSFER:
        try:
            other = db.get("money_accounts", int(form.get("to_account_id") or 0))
        except (TypeError, ValueError):
            other = None
        if not other or other["id"] == account["id"] or not other.get("active", True):
            return None, "Choose the account the money goes to."
        ref = "TR-%s" % secrets.token_hex(3).upper()
        out = db.insert("money_entries", dict(
            base, account_id=account["id"], direction=MONEY_OUT, transfer_ref=ref,
            kind="Transfer to %s" % other["name"]))
        into = db.insert("money_entries", dict(
            base, account_id=other["id"], direction=MONEY_IN, transfer_ref=ref,
            kind="Transfer from %s" % account["name"]))
        return [out, into], None
    if direction not in ENTRY_KINDS:
        return None, "Choose money in, money out or a transfer."
    kind = form.get("kind") if form.get("kind") in ENTRY_KINDS[direction] else ENTRY_KINDS[direction][0]
    return [db.insert("money_entries", dict(base, account_id=account["id"],
                                            direction=direction, kind=kind,
                                            transfer_ref=""))], None


# --- What is due to suppliers ---------------------------------------------------
def _days_since(iso):
    try:
        return (date.today() - date.fromisoformat((iso or "")[:10])).days
    except ValueError:
        return 0


def payments_due():
    """Every issued PO with something left to pay, oldest first: how old it
    is, and whether an up-front payment is due now."""
    suppliers, by_name = pur._supplier_index()
    names = {s["id"]: s.get("name") or "" for s in suppliers}
    rows = []
    for po in pur.issued_orders():
        summary = pur.po_payment_summary(po)
        if not summary["can_pay"]:
            continue
        supplier_id = pur._po_supplier_id(po, by_name)
        age = _days_since(po.get("approved_at"))
        if summary["advance_due"] > 0.005:
            why = "%s due now" % (po.get("payment_terms") or "Up-front payment")
        elif po.get("status") == pur.PO_DELIVERED:
            why = "Delivered — balance due"
        else:
            why = "Waiting for delivery"
        rows.append(dict(po, **summary, supplier_id=supplier_id,
                         supplier=names.get(supplier_id) or po.get("supplier_name") or "",
                         age=age, why=why,
                         due_now=summary["advance_due"] > 0.005
                                 or po.get("status") == pur.PO_DELIVERED))
    rows.sort(key=lambda r: (not r["due_now"], -r["age"], r.get("po_no") or ""))
    return rows


def aging(rows):
    """Balances of payments_due() rows grouped by how old the PO is."""
    out = []
    for label, low, high in AGE_BUCKETS:
        hit = [r for r in rows if r["age"] >= low and (high is None or r["age"] <= high)]
        out.append({"label": label, "count": len(hit),
                    "amount": round(sum(r["balance"] for r in hit), 2)})
    return out
