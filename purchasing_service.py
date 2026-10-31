"""Purchasing: the Purchase Request (PR) and the Purchase Order (PO).

Both follow the client's paper forms.

PURCHASE REQUEST FORM — PR No. (YYYY-MM-NNN), date requested, required
date, requesting department, purpose / justification, requested by; lines
of item description, purpose / use, qty, unit, est. unit cost, est. total
cost, preferred supplier, budget / category, remark; the total estimated
purchase cost; requester's notes / special instructions; signed off by
Requested by → Checked by → Approved by → Purchasing / Procurement.

PURCHASE ORDER — PO No. (the PR's number), PO date, expected delivery date,
payment terms; supplier information (name, address, contact no. / email,
TIN, contact person, supplier reference); delivery information (deliver to,
address, instructions, contact); lines of description / specification,
brand / model, qty, unit, unit price, amount, requested delivery, VAT / tax,
remarks; subtotal, discount, delivery / freight, VAT / other tax, grand
total; terms / special instructions; Prepared by, Checked by, Approved by,
Supplier acknowledgment.

Flow: a head raises a PR → the Admin checks it (initial approval; may change
quantities or reject) → the Super Admin gives the final approval → an admin
prepares a PO from the approved lines → the Admin checks the PO, the Super
Admin approves it → the PO is issued: printed, sent, and posted to the
supplier's ledger, where payments (advance, partial, full) are recorded
against it → the Receiving Team Head records the delivery against the PO
(each box tied to its PO line) → boxes pass inspection → the PO is marked
delivered once everything ordered has been accepted (or an admin closes it
short).
"""
from datetime import date

import db
import security
import services
from services import _to_float, now_iso, today_iso

COMPANY_NAME = "Amamio Farm Products"
COMPANY_ADDRESS = "Dist. 4, Brgy. Kinabjangan, Nasipit, Agusan del Norte"

DEPARTMENTS = ["Production", "Receiving", "Packing", "Assets & Maintenance",
               "Office / Admin"]
ROLE_DEPARTMENT = {"production_head": "Production",
                   "receiving_head": "Receiving",
                   "packing_head": "Packing", "packing_member": "Packing",
                   "assets_head": "Assets & Maintenance"}
BUDGET_CATEGORIES = ["Raw materials", "Packaging", "Production supplies",
                     "Office supplies", "Equipment / repair", "Other"]
UNITS = ["pc", "set", "box", "pack", "kg", "g", "L", "mL", "roll", "ream",
         "gal", "bottle", "bag"]
ADVANCE_PAYMENT = "Advance payment"
PARTIAL_PAYMENT = "Partial payment"
PAYMENT_TERMS = ["Cash on delivery", "Cash before delivery", ADVANCE_PAYMENT,
                 PARTIAL_PAYMENT, "7 days", "15 days", "30 days"]
# Terms that pay part of the PO up front; the PO records how much.
UPFRONT_TERMS = (ADVANCE_PAYMENT, PARTIAL_PAYMENT)
DEFAULT_TERMS = ("Supplier shall provide goods according to the "
                 "specifications, quantities, prices and delivery requirements "
                 "stated in this PO. Please reference the PO No. on invoices "
                 "and delivery documents.")

# Both forms are approved in two stages: the Admin checks first (initial
# approval, signed "Checked by"), the Super Admin approves last ("Approved by").
PR_PENDING = "Pending Approval"
FOR_FINAL = "For Final Approval"
STAGE_ROLE = {"initial": "admin", "final": "super_admin"}
STAGE_NAME = {"initial": "Admin's check", "final": "Super Admin's final approval"}

PO_PREPARED = "Prepared"
PO_FOR_FINAL = FOR_FINAL
PO_APPROVED = "Approved"
PO_DELIVERED = "Delivered"
PO_CANCELLED = "Cancelled"
PO_BADGE = {PO_PREPARED: "badge-warn", PO_FOR_FINAL: "badge-warn",
            PO_APPROVED: "badge-ok", PO_DELIVERED: "badge-ok",
            PO_CANCELLED: "badge-danger"}
PO_TRACK = ["Prepared", "Checked by Admin", "Approved by Super Admin", "Delivered"]


def approval_stage(record):
    """'initial' while the Admin still has to check a PR or PO, 'final'
    while it waits for the Super Admin, otherwise None."""
    status = (record or {}).get("status")
    if status in (PR_PENDING, PO_PREPARED):
        return "initial"
    if status == FOR_FINAL:
        return "final"
    return None


def can_approve(user, record):
    """True if ``user`` gives the approval ``record`` is waiting for."""
    stage = approval_stage(record)
    return bool(stage) and (security.normalize_role((user or {}).get("role"))
                            == STAGE_ROLE[stage])


def waiting_on(record):
    """'the Admin's check' / 'the Super Admin's final approval', or ''."""
    stage = approval_stage(record)
    return "the " + STAGE_NAME[stage] if stage else ""


def _notify_role(role, message, link):
    services.notify_team([role], message, link=link, admins=False)


def item_choices():
    """The item dropdown on the PR and PO: stocked items, grouped as on the
    month-end reports, each with its unit, last cost and usual supplier."""
    suppliers = {s["id"]: s.get("name") or "" for s in db.read("suppliers")}
    groups = []
    for key, label in services.MATERIAL_CATEGORIES:
        rows = [{"id": m["id"], "name": m.get("name") or "",
                 "unit": m.get("unit") or "pc",
                 "cost": _to_float(m.get("unit_cost")),
                 "supplier": suppliers.get(m.get("supplier_id"), "")}
                for m in services.stocked_materials([key])]
        if rows:
            groups.append((label, rows))
    return groups


def units_for(groups):
    """UNITS plus any unit a catalogue item uses that isn't on the list."""
    extra = sorted({r["unit"] for _, rows in groups for r in rows} - set(UNITS))
    return UNITS + extra


def supplier_names():
    return sorted({s.get("name") for s in db.read("suppliers") if s.get("name")},
                  key=str.lower)


def department_for(user):
    role = security.normalize_role((user or {}).get("role"))
    return ROLE_DEPARTMENT.get(role, "Office / Admin")


def pr_number(req):
    """The number printed on the form; older requests show their id."""
    return (req or {}).get("pr_no") or "#%s" % (req or {}).get("id", "")


def next_pr_no(on=None):
    """'2026-09-017': year-month of the request, then a running number for
    the year (as on the client's PR form)."""
    on = on or date.today()
    year = "%04d-" % on.year
    highest = 0
    for r in db.read("purchase_requests"):
        no = str(r.get("pr_no") or "")
        if no.startswith(year):
            try:
                highest = max(highest, int(no.rsplit("-", 1)[1]))
            except (ValueError, IndexError):
                continue
    return "%04d-%02d-%03d" % (on.year, on.month, highest + 1)


def _match_material(name):
    """Link a requested item to a stocked material when the names match."""
    wanted = (name or "").strip().lower()
    if not wanted:
        return None
    for m in db.read("raw_materials"):
        if (m.get("name") or "").strip().lower() == wanted:
            return m["id"]
    return None


def _clean_lines(lines):
    items, problems = [], []
    for raw in lines:
        # An item picked from the dropdown carries its material id; "Other"
        # carries only the typed name.
        try:
            material_id = int(raw.get("material_id"))
        except (TypeError, ValueError):
            material_id = None
        material = db.get("raw_materials", material_id) if material_id else None
        if not material:
            material_id = None
        desc = ((raw.get("description") or "").strip()
                or (material or {}).get("name", ""))
        qty = _to_float(raw.get("qty"))
        if not desc and qty <= 0:
            continue
        if not desc:
            problems.append("Choose an item for every line.")
            continue
        if qty <= 0:
            problems.append("Enter a quantity for %s." % desc)
            continue
        cost = max(0.0, _to_float(raw.get("est_unit_cost")))
        items.append({
            "item_id": len(items) + 1,
            "name": desc,
            "purpose_use": (raw.get("purpose_use") or "").strip(),
            "unit": (raw.get("unit") or "pc").strip(),
            "requested_qty": qty,
            "est_unit_cost": round(cost, 2),
            "est_total": round(cost * qty, 2),
            "preferred_supplier": (raw.get("preferred_supplier") or "").strip(),
            "budget_category": (raw.get("budget_category") or "").strip(),
            "remark": (raw.get("remark") or "").strip(),
            "material_id": material_id or _match_material(desc),
            "category": "",
            "description": "",
            "approved_qty": None,
            "received_qty": None,
            "status": "Pending",
            "note": (raw.get("remark") or "").strip(),
            "qr_token": None,
            "po_id": None,
            "po_no": "",
        })
    return items, problems


def create_purchase_request(user, department, purpose, required_date, lines,
                            notes="", urgency="Normal", pr_no="",
                            date_requested="", requested_by_name=""):
    """Raise a PR from the paper-form fields.  Returns (request, error).

    ``requested_by_name`` is the requestor written on the form — the person
    signed in unless someone else asked for the items."""
    if department not in DEPARTMENTS:
        return None, "Choose the requesting department."
    purpose = (purpose or "").strip()
    if not purpose:
        return None, "Give the purpose / justification — the approver sees it."
    requestor = (requested_by_name or user.get("name") or "").strip()
    if not requestor:
        return None, "Enter the requestor's name."
    items, problems = _clean_lines(lines)
    if problems:
        return None, problems[0]
    if not items:
        return None, "Add at least one item with a quantity."
    requested_on = date_requested or today_iso()
    required_date = (required_date or "").strip()
    if required_date and required_date < requested_on:
        return None, "The expected date can't be before the date requested."
    try:
        number = pr_no or next_pr_no(date.fromisoformat(requested_on))
    except ValueError:
        number = next_pr_no()
    req = db.insert("purchase_requests", {
        "type": "general",
        "pr_no": number,
        "requestor_id": int(user["id"]),
        "requested_by_name": requestor,
        "department": department,
        "purpose": purpose,
        "reason": purpose,
        "date_requested": requested_on,
        "needed_by": required_date,
        "urgency": urgency if urgency in services.URGENCY_LEVELS else "Normal",
        "notes": (notes or "").strip(),
        "remarks": "",
        "check_remarks": "",
        "total_estimated": round(sum(i["est_total"] for i in items), 2),
        "status": PR_PENDING,
        "checked_by_name": "",
        "checked_at": "",
        "approved_by_name": "",
        "purchasing_by_name": "",
        "purchasing_date": "",
        "items": items,
        "created_at": now_iso(),
    })
    _notify_role(
        "admin", "Purchase request %s from %s — %s (est. %s) awaiting your check."
        % (number, department, purpose[:60], services.fmt_peso(req["total_estimated"])),
        "/approvals/%d" % req["id"])
    return req, None


def decide_request(request_id, user, decisions, remarks, action):
    """The Admin's check (initial approval) or the Super Admin's final
    approval of a PR, whichever it is waiting for.  Returns (request, error).

    ``decisions``: {item_id: {"reject": bool, "approved_qty": str|None}};
    ``action``: 'approve' | 'revise' | 'reject'.  The approver's name fills
    the form's Checked by (Admin) or Approved by (Super Admin).  Items only
    become orderable after the final approval."""
    req = db.get("purchase_requests", request_id)
    if not req:
        return None, "Purchase request not found."
    stage = approval_stage(req)
    if not stage:
        return None, "Request %s has already been decided." % pr_number(req)
    if not can_approve(user, req):
        return None, ("Request %s is waiting for %s."
                      % (pr_number(req), waiting_on(req)))
    name = user.get("name") or ""
    remarks = (remarks or "").strip()

    if stage == "initial" and action != "reject":
        items = req.get("items") or []
        for it in items:
            it["approved_qty"] = services.decided_qty(
                it, decisions.get(it["item_id"], {}), action)
            it["status"] = "Pending" if it["approved_qty"] else "Rejected"
        if any(it["approved_qty"] for it in items):
            req = db.update("purchase_requests", request_id, {
                "items": items, "status": FOR_FINAL, "check_remarks": remarks,
                "checked_by_name": name, "checked_at": now_iso()})
            _notify_role("super_admin",
                         "Purchase request %s was checked by %s — awaiting your "
                         "final approval." % (pr_number(req), name),
                         "/approvals/%d" % request_id)
            services.notify(req.get("requestor_id"),
                            "Your purchase request %s was checked by %s and is "
                            "waiting for the Super Admin's final approval."
                            % (pr_number(req), name),
                            link="/requests/%d" % request_id)
            return req, None
        action = "reject"   # every line was rejected at the check

    services.apply_decision(request_id, decisions, remarks, action,
                            decider_id=user["id"])
    if stage == "initial":
        signoff = {"checked_by_name": name, "checked_at": now_iso(),
                   "check_remarks": remarks}
    else:
        signoff = {"approved_by_name": name}
    return db.update("purchase_requests", request_id, signoff), None


def approvals_for(user):
    """What waits for this user's approval: (requests, purchase orders)."""
    reqs = [r for r in db.read("purchase_requests") if can_approve(user, r)]
    reqs.sort(key=lambda r: r.get("created_at", ""))
    pos = [p for p in db.read("purchase_orders") if can_approve(user, p)]
    pos.sort(key=lambda p: (p.get("po_date") or "", p.get("id")))
    return reqs, pos


def record_signoff(request_id, checked_by_name, approver):
    """Set the Checked by / Approved by names directly (demo data)."""
    return db.update("purchase_requests", request_id, {
        "checked_by_name": (checked_by_name or "").strip(),
        "approved_by_name": (approver or {}).get("name") or "",
    })


# --- Purchase orders -----------------------------------------------------------
def orderable_items(req):
    """Approved lines of a PR not yet on a (live) purchase order."""
    return [it for it in (req.get("items") or [])
            if it.get("status") == "Ordered" and not it.get("po_id")]


def order_lines(req):
    """orderable_items with name, unit and cost filled in — lines of older
    production requests only carry the material id."""
    rows = []
    for it in orderable_items(req):
        m = (db.get("raw_materials", it["material_id"])
             if it.get("material_id") else None) or {}
        rows.append(dict(it, name=it.get("name") or m.get("name")
                         or "Item %s" % it.get("item_id"),
                         unit=it.get("unit") or m.get("unit") or "pc",
                         est_unit_cost=it.get("est_unit_cost")
                         or _to_float(m.get("unit_cost")) or None))
    return rows


def requests_awaiting_po():
    rows = []
    for r in db.read("purchase_requests"):
        if r.get("status") in ("Approved", "Revised") and orderable_items(r):
            rows.append(r)
    rows.sort(key=lambda r: r.get("needed_by") or r.get("created_at") or "")
    return rows


def next_po_no(req):
    base = req.get("pr_no") or "PR%d" % req["id"]
    taken = {p.get("po_no") for p in db.read("purchase_orders")}
    if base not in taken:
        return base
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        candidate = "%s-%s" % (base, letter)
        if candidate not in taken:
            return candidate
    return "%s-%d" % (base, len(taken) + 1)


def _totals(items, discount, freight, tax, labor=0):
    subtotal = round(sum(i["amount"] for i in items), 2)
    discount, freight, tax, labor = (round(max(0.0, _to_float(v)), 2)
                                     for v in (discount, freight, tax, labor))
    return {"subtotal": subtotal, "discount": discount, "freight": freight,
            "labor": labor, "tax": tax,
            "grand_total": round(subtotal - discount + freight + labor + tax, 2)}


def create_purchase_order(request_id, user, header, supplier, delivery, lines,
                          totals, signoffs=None):
    """Prepare a PO from an approved PR.  Returns (po, error).

    supplier: {id (from the Supplier Directory, required), address, contact,
               tin, contact_person, reference} — the name always comes from
               the directory, the other details can be corrected for this PO.
    header:   {po_date, expected_delivery, payment_terms, advance_amount} —
              the amount is required for advance / partial payment terms.
    lines: [{pr_item_id, description, brand_model, qty, unit, unit_price,
             requested_delivery, vat, remarks}] — only approved PR lines that
    are not on another PO yet.
    Prepared by is the person signed in; Checked by and Approved by are
    filled in by the two approvals."""
    req = db.get("purchase_requests", request_id)
    if not req:
        return None, "Purchase request not found."
    open_items = {it["item_id"]: it for it in order_lines(req)}
    if not open_items:
        return None, "This request has no approved items left to order."
    try:
        directory = db.get("suppliers", int(supplier.get("id")))
    except (TypeError, ValueError):
        directory = None
    if not directory:
        return None, "Choose the supplier from the Supplier Directory."
    if not services.supplier_approved(directory):
        return None, ("%s is not on the Approved Supplier List — approve it in "
                      "the Supplier Directory first." % directory.get("name"))
    supplier_name = directory.get("name") or ""
    card = supplier_card(directory)

    def sup(key, card_key=None):
        value = supplier.get(key)
        return (value if value is not None else card.get(card_key or key) or "").strip()

    items = []
    for raw in lines:
        try:
            pr_item_id = int(raw.get("pr_item_id"))
        except (TypeError, ValueError):
            continue
        if pr_item_id not in open_items or raw.get("include") == "0":
            continue
        qty = _to_float(raw.get("qty"))
        price = _to_float(raw.get("unit_price"))
        if qty <= 0:
            return None, "Enter a quantity for %s." % open_items[pr_item_id]["name"]
        if price < 0:
            return None, "Unit prices can't be negative."
        description = (raw.get("description") or open_items[pr_item_id]["name"]).strip()
        # A different item picked from the dropdown is received as that item.
        material_id = (open_items[pr_item_id].get("material_id")
                       if description == open_items[pr_item_id]["name"]
                       else _match_material(description))
        items.append({
            "line_no": len(items) + 1,
            "pr_item_id": pr_item_id,
            "material_id": material_id,
            "description": description,
            "brand_model": (raw.get("brand_model") or "").strip(),
            "qty": qty,
            "unit": (raw.get("unit") or open_items[pr_item_id].get("unit") or "pc").strip(),
            "unit_price": round(price, 2),
            "amount": round(qty * price, 2),
            "requested_delivery": (raw.get("requested_delivery") or "").strip(),
            "vat": (raw.get("vat") or "").strip(),
            "remarks": (raw.get("remarks") or "").strip(),
        })
    if not items:
        return None, "Choose at least one approved item to order."
    po_date = (header.get("po_date") or today_iso()).strip()
    expected = (header.get("expected_delivery") or "").strip()
    if expected and expected < po_date:
        return None, "The expected delivery date can't be before the PO date."
    money = _totals(items, totals.get("discount"), totals.get("freight"),
                    totals.get("tax"), totals.get("labor"))
    terms = (header.get("payment_terms") or "").strip()
    advance = 0.0
    if terms in UPFRONT_TERMS:
        advance = round(_to_float(header.get("advance_amount")), 2)
        if advance <= 0:
            return None, "Enter how much is paid up front for the %s." % terms.lower()
        if advance > money["grand_total"] + 0.005:
            return None, "The amount paid up front can't be more than the grand total."

    signoffs = signoffs or {}
    po = db.insert("purchase_orders", dict(
        po_no=next_po_no(req),
        purchase_request_id=req["id"],
        pr_no=req.get("pr_no") or "",
        po_date=po_date,
        expected_delivery=expected,
        payment_terms=terms,
        advance_amount=advance,
        supplier_id=directory["id"],
        supplier_name=supplier_name,
        supplier_address=sup("address"),
        supplier_contact=sup("contact"),
        supplier_tin=sup("tin"),
        supplier_contact_person=sup("contact_person"),
        supplier_reference=(supplier.get("reference") or "").strip(),
        deliver_to=(delivery.get("deliver_to") or COMPANY_NAME).strip(),
        delivery_address=(delivery.get("address") or COMPANY_ADDRESS).strip(),
        delivery_instructions=(delivery.get("instructions") or "").strip(),
        delivery_contact=(delivery.get("contact") or "").strip(),
        items=items,
        terms=(totals.get("terms") or DEFAULT_TERMS).strip(),
        prepared_by_name=(signoffs.get("prepared_by") or user.get("name") or "").strip(),
        prepared_at=today_iso(),
        checked_by_name="",
        checked_at="",
        approved_by_name="",
        approved_at="",
        supplier_ack_name="",
        supplier_ack_date="",
        status=PO_PREPARED,
        delivered_at="",
        cancelled_at="",
        created_by=user["id"],
        created_at=now_iso(),
        **money,
    ))
    # The PR lines now point at their PO; the PR's Purchasing / Procurement
    # sign-off is whoever prepared it.
    ordered = {i["pr_item_id"] for i in items}
    for it in req.get("items") or []:
        if it["item_id"] in ordered:
            it["po_id"] = po["id"]
            it["po_no"] = po["po_no"]
    changes = {"items": req["items"],
               "purchasing_by_name": po["prepared_by_name"],
               "purchasing_date": po["prepared_at"]}
    if not orderable_items(req):
        changes["status"] = "Ordered"
    db.update("purchase_requests", req["id"], changes)
    services.notify(req.get("requestor_id"),
                    "PO %s prepared for your request %s (%s)."
                    % (po["po_no"], pr_number(req), supplier_name),
                    link="/requests/%d" % req["id"])
    _notify_role("admin", "PO %s to %s (%s) prepared by %s — awaiting your check."
                 % (po["po_no"], supplier_name, services.fmt_peso(po["grand_total"]),
                    po["prepared_by_name"]), "/purchasing/%d" % po["id"])
    return po, None


def approve_purchase_order(po_id, user):
    """The Admin's check (signed Checked by), then the Super Admin's final
    approval (Approved by).  The final approval issues the PO: it can be
    printed and sent, is posted to the supplier's ledger, and Receiving
    expects the delivery."""
    po = db.get("purchase_orders", po_id)
    if not po:
        return None, "Purchase order not found."
    stage = approval_stage(po)
    if not stage:
        return None, "PO %s is not waiting for an approval." % po["po_no"]
    if not can_approve(user, po):
        return None, "PO %s is waiting for %s." % (po["po_no"], waiting_on(po))
    if po.get("supplier_id") and not services.supplier_approved(
            db.get("suppliers", po["supplier_id"])):
        return None, ("%s is not on the Approved Supplier List — approve it in "
                      "the Supplier Directory first." % po.get("supplier_name"))
    name = user.get("name") or ""
    link = "/purchasing/%d" % po_id
    if stage == "initial":
        po = db.update("purchase_orders", po_id, {
            "status": PO_FOR_FINAL, "checked_by_name": name,
            "checked_at": today_iso()})
        _notify_role("super_admin", "PO %s to %s was checked by %s — awaiting "
                     "your final approval." % (po["po_no"], po["supplier_name"], name),
                     link)
        return po, None
    po = db.update("purchase_orders", po_id, {
        "status": PO_APPROVED, "approved_by_name": name,
        "approved_at": today_iso()})
    services.notify(po.get("created_by"),
                    "PO %s to %s is approved — print it and send it to the supplier."
                    % (po["po_no"], po["supplier_name"]), link=link)
    _notify_role("receiving_head", "PO %s from %s is approved — expect the delivery%s."
                 % (po["po_no"], po["supplier_name"],
                    " on %s" % po["expected_delivery"] if po.get("expected_delivery") else ""),
                 "/receiving/")
    return po, None


def mark_delivered(po_id, user):
    """Close a PO by hand — e.g. the supplier sent less and nothing more is
    coming.  What was accepted at inspection stays as received."""
    po = db.get("purchase_orders", po_id)
    if not po:
        return None, "Purchase order not found."
    if po.get("status") != PO_APPROVED:
        return None, "Only an approved PO can be marked delivered."
    receipts = po_receipts(po)
    if any(r["pending"] for r in receipts.values()):
        return None, ("Some boxes from this PO are still being inspected — "
                      "wait for their decision first.")
    _update_request_lines(po, receipts, closing=True)
    po = db.update("purchase_orders", po_id, {"status": PO_DELIVERED,
                                              "delivered_at": today_iso()})
    services.notify_admins("PO %s from %s marked delivered."
                           % (po["po_no"], po["supplier_name"]),
                           link="/purchasing/%d" % po_id)
    return po, None


def cancel_purchase_order(po_id, user):
    """Cancel a PO that has not been delivered; its PR lines can be ordered
    again on a new PO."""
    po = db.get("purchase_orders", po_id)
    if not po:
        return None, "Purchase order not found."
    if po.get("status") in (PO_DELIVERED, PO_CANCELLED):
        return None, "A delivered or cancelled PO can't be cancelled."
    req = db.get("purchase_requests", po.get("purchase_request_id"))
    if req:
        for it in req.get("items") or []:
            if it.get("po_id") == po["id"] and it.get("status") != "Received":
                it["po_id"] = None
                it["po_no"] = ""
        changes = {"items": req["items"]}
        if req.get("status") == "Ordered" and orderable_items(req):
            changes["status"] = "Approved"
        db.update("purchase_requests", req["id"], changes)
    return db.update("purchase_orders", po_id, {"status": PO_CANCELLED,
                                                "cancelled_at": today_iso()}), None


# --- Receiving against a purchase order ------------------------------------------
# Units on a PO line are taken to be the stock unit of the item it receives.
_BUDGET_TO_CATEGORY = {"Raw materials": "raw", "Packaging": "packaging",
                       "Office supplies": "office"}
_WEIGHT_UNITS = {"kg", "g"}


def po_receipts(po):
    """Per PO line: accepted qty, qty still in inspection, boxes delivered."""
    rows = {it["line_no"]: {"received": 0.0, "pending": 0.0, "boxes": 0}
            for it in po.get("items") or []}
    for box in db.query("receiving_boxes", po_id=po["id"]):
        row = rows.get(box.get("po_line_no"))
        if row is None:
            continue
        row["boxes"] += 1
        status = services.box_status(box)
        if status == services.BOX_ACCEPTED:
            row["received"] += _to_float(box.get("good_qty"))
        elif status in (services.BOX_AWAITING, services.BOX_HOLD):
            row["pending"] += _to_float(box.get("declared_qty"))
    return rows


def lines_to_receive(po):
    """The PO's lines with what is received, in inspection and still to come."""
    receipts = po_receipts(po)
    rows = []
    for it in po.get("items") or []:
        r = receipts[it["line_no"]]
        qty = _to_float(it.get("qty"))
        rows.append(dict(it, received=r["received"], pending=r["pending"],
                         boxes=r["boxes"],
                         remaining=max(0.0, round(qty - r["received"] - r["pending"], 3))))
    return rows


def open_pos_for_receiving():
    """Approved POs with something still to come — what Receiving expects."""
    rows = []
    for po in db.read("purchase_orders"):
        if po.get("status") != PO_APPROVED:
            continue
        lines = lines_to_receive(po)
        waiting = [l for l in lines if l["remaining"] > 0]
        if not waiting:
            continue
        rows.append({"id": po["id"], "po_no": po.get("po_no"),
                     "supplier_name": po.get("supplier_name"),
                     "expected_delivery": po.get("expected_delivery") or "",
                     "lines": waiting,
                     "summary": ", ".join(
                         "%s %s" % (l["description"], services.fmt_qty(l["remaining"], l.get("unit") or ""))
                         for l in waiting[:3]) + (" + %d more" % (len(waiting) - 3)
                                                  if len(waiting) > 3 else "")})
    rows.sort(key=lambda r: (r["expected_delivery"] or "9999", r["po_no"] or ""))
    return rows


def material_for_po_line(po, line):
    """The stock item a PO line is received into, created (once) for items
    ordered that aren't on the materials list yet."""
    if line.get("material_id"):
        material = db.get("raw_materials", line["material_id"])
        if material:
            return material
    material = _match_material(line.get("description"))
    material = db.get("raw_materials", material) if material else None
    req = db.get("purchase_requests", po.get("purchase_request_id")) or {}
    pr_item = next((i for i in req.get("items") or []
                    if i.get("item_id") == line.get("pr_item_id")), {})
    if not material:
        unit = (line.get("unit") or "pc").strip()
        material = db.insert("raw_materials", {
            "name": line.get("description") or "Received item",
            "category": _BUDGET_TO_CATEGORY.get(pr_item.get("budget_category"), "cleaning"),
            "subcategory": "",
            "unit": unit,
            "track_mode": "weight" if unit in _WEIGHT_UNITS else "piece",
            "stock_qty": 0.0,
            "reorder_level": 0,
            "supplier_id": po.get("supplier_id"),
            "unit_cost": _to_float(line.get("unit_price")),
            "created_at": now_iso(),
        })
    # Remember the link on the PO line and the PR line it came from.
    for it in po.get("items") or []:
        if it["line_no"] == line["line_no"]:
            it["material_id"] = material["id"]
    db.update("purchase_orders", po["id"], {"items": po["items"]})
    if pr_item:
        pr_item["material_id"] = material["id"]
        db.update("purchase_requests", req["id"], {"items": req["items"]})
    return material


def _update_request_lines(po, receipts, closing=False):
    """Copy what came in onto the PR lines of this PO."""
    req = db.get("purchase_requests", po.get("purchase_request_id"))
    if not req:
        return
    by_pr_item = {it.get("pr_item_id"): it for it in po.get("items") or []}
    for it in req.get("items") or []:
        line = by_pr_item.get(it.get("item_id"))
        if it.get("po_id") != po["id"] or not line:
            continue
        r = receipts[line["line_no"]]
        it["received_qty"] = round(r["received"], 3)
        full = r["received"] >= _to_float(line.get("qty")) - 1e-9
        if (full or closing) and not r["pending"]:
            it["status"] = "Received"
    changes = {"items": req["items"]}
    if all(it.get("status") in ("Received", "Rejected") for it in req["items"]):
        changes["status"] = "Received"
    db.update("purchase_requests", req["id"], changes)


def sync_po_receipts(po_id):
    """After a box from this PO is decided: update the PR lines, and mark
    the PO delivered once every line has been accepted in full."""
    po = db.get("purchase_orders", po_id)
    if not po or po.get("status") != PO_APPROVED:
        return po
    receipts = po_receipts(po)
    _update_request_lines(po, receipts)
    complete = all(not receipts[it["line_no"]]["pending"]
                   and receipts[it["line_no"]]["received"]
                   >= _to_float(it.get("qty")) - 1e-9
                   for it in po.get("items") or [])
    if complete:
        po = db.update("purchase_orders", po_id, {"status": PO_DELIVERED,
                                                  "delivered_at": today_iso()})
        services.notify_admins(
            "PO %s from %s is delivered — everything ordered passed inspection."
            % (po["po_no"], po["supplier_name"]), link="/purchasing/%d" % po_id)
    return po


def po_track(po):
    status = po.get("status")
    if status == PO_CANCELLED:
        return [("Prepared", "done"), ("Cancelled", "now")]
    reached = {PO_PREPARED: 0, PO_FOR_FINAL: 1, PO_APPROVED: 2,
               PO_DELIVERED: 3}.get(status, 0)
    return [(label, "done" if i < reached else ("now" if i == reached else ""))
            for i, label in enumerate(PO_TRACK)]


def supplier_card(supplier):
    """What the PO's supplier block shows, from the Supplier Directory."""
    contact = ", ".join(p for p in (supplier.get("phone"), supplier.get("email")) if p)
    return {"id": supplier["id"], "name": supplier.get("name") or "",
            "address": supplier.get("address") or "",
            "contact": contact or supplier.get("buy_from") or "",
            "tin": supplier.get("tin") or "",
            "contact_person": supplier.get("contact_person") or "",
            "approved": services.supplier_approved(supplier)}


def purchasing_counts():
    orders = db.read("purchase_orders")
    return {
        "awaiting_po": len(requests_awaiting_po()),
        "prepared": sum(1 for p in orders if p.get("status") == PO_PREPARED),
        "for_final": sum(1 for p in orders if p.get("status") == PO_FOR_FINAL),
        "approved": sum(1 for p in orders if p.get("status") == PO_APPROVED),
    }


def seed_demo(users, suppliers):
    """The client's own example: PR 2026-09-017 (machine accessories for the
    dehydrator), approved, and PO 2026-09-017 to Trimix Butuan."""
    production = users.get("production_head")
    admin = users.get("admin")
    if not production or not admin:
        return
    req, err = create_purchase_request(
        production, "Production", "Machine accessories", "2026-09-22",
        [{"description": "Momentary push button switch", "purpose_use": "Heater switch",
          "qty": 1, "unit": "pc", "est_unit_cost": 250,
          "preferred_supplier": "Trimix Butuan", "budget_category": "Equipment / repair"},
         {"description": "Timer switch", "purpose_use": "Dehydrator", "qty": 1,
          "unit": "pc", "est_unit_cost": 700, "preferred_supplier": "Trimix Butuan",
          "budget_category": "Equipment / repair"},
         {"description": "Center switch", "purpose_use": "Dehydrator", "qty": 1,
          "unit": "pc", "est_unit_cost": 150, "preferred_supplier": "Trimix Butuan",
          "budget_category": "Equipment / repair"}],
        notes="Melted momentary push button switch and damaged timer switch",
        pr_no="2026-09-017", date_requested="2026-09-21")
    if err:
        return
    # Checked by the Admin, approved by the Super Admin (names as signed).
    services.apply_decision(req["id"], {}, "Approved for repair of the dehydrator.",
                            "approve", decider_id=admin["id"])
    record_signoff(req["id"], "Jefrey S. Pabillore", {"name": "Bernadith M. Frianeza"})
    trimix = next((s for s in suppliers.values() if s.get("name") == "Trimix Butuan"), None)
    if not trimix:
        return
    supplier = supplier_card(trimix)
    req = db.get("purchase_requests", req["id"])
    po, err = create_purchase_order(
        req["id"], admin,
        {"po_date": "2026-09-21", "expected_delivery": "2026-09-21",
         "payment_terms": "Cash on delivery"},
        supplier,
        {"deliver_to": COMPANY_NAME, "address": COMPANY_ADDRESS,
         "instructions": "Door to door", "contact": "Aldren"},
        [{"pr_item_id": it["item_id"], "description": it["name"], "qty": it["approved_qty"],
          "unit": it["unit"], "unit_price": it["est_unit_cost"]}
         for it in req["items"]],
        {"terms": DEFAULT_TERMS},
        {"prepared_by": "Bernadith Frianeza"})
    if po:
        db.update("purchase_orders", po["id"], {
            "status": PO_APPROVED, "prepared_at": "2026-09-21",
            "checked_by_name": "Fe Amor Eballar", "checked_at": "2026-09-21",
            "approved_by_name": "Janice Nilda A. Mira", "approved_at": "2026-09-21"})
    # As written on the paper form: requested and handled by Aldren Ocate.
    db.update("purchase_requests", req["id"], {
        "requested_by_name": "Aldren Ocate", "purchasing_by_name": "Aldren Ocate",
        "purchasing_date": "2026-09-21", "decided_at": "2026-09-21T10:00:00",
        "created_at": "2026-09-21T08:00:00"})


def summary_line(req):
    items = req.get("items") or []
    first = items[0].get("name") if items and items[0].get("name") else None
    if req.get("type") == "production":
        first = "Ingredients for %s" % (req.get("product_name") or "production")
    more = len(items) - 1
    return "%s%s" % (first or "Request", (" + %d more" % more) if more > 0 else "")


def total_estimated(req):
    if req.get("total_estimated") is not None:
        return _to_float(req.get("total_estimated"))
    return round(sum(_to_float(i.get("est_total")) for i in req.get("items") or []), 2)


# --- Supplier ledger ---------------------------------------------------------------
# One ledger per supplier in the directory.  An issued PO (approved by the
# Super Admin) is a credit — what is owed the supplier; each payment is a
# debit; a PO cancelled after it was issued is reversed with a debit.  The
# balance is what is still owed.  Nothing is stored twice: the ledger is read
# from the POs and the supplier_payments table.
PAYMENT_KINDS = [ADVANCE_PAYMENT, PARTIAL_PAYMENT, "Full payment"]
PAYMENT_METHODS = ["Cash", "Bank transfer", "Check", "GCash"]


def _supplier_index():
    suppliers = db.read("suppliers")
    by_name = {(s.get("name") or "").strip().lower(): s["id"] for s in suppliers}
    return suppliers, by_name


def _po_supplier_id(po, by_name):
    """The directory supplier of a PO (older POs only carry the name)."""
    return po.get("supplier_id") or by_name.get(
        (po.get("supplier_name") or "").strip().lower())


def issued_orders(supplier_id=None):
    """POs the Super Admin approved — whatever happened to them after."""
    _, by_name = _supplier_index()
    rows = [p for p in db.read("purchase_orders") if p.get("approved_at")]
    if supplier_id is not None:
        rows = [p for p in rows if _po_supplier_id(p, by_name) == supplier_id]
    return rows


def po_paid(po_id):
    return round(sum(_to_float(p.get("amount"))
                     for p in db.query("supplier_payments", po_id=po_id)), 2)


def po_payment_summary(po):
    """Paid, still owed, and how much of the agreed up-front amount is due."""
    paid = po_paid(po["id"])
    issued = bool(po.get("approved_at"))
    owed = 0.0 if po.get("status") == PO_CANCELLED else _to_float(po.get("grand_total"))
    return {"issued": issued, "paid": paid,
            "balance": round(owed - paid, 2),
            "advance": _to_float(po.get("advance_amount")),
            "advance_due": round(max(0.0, _to_float(po.get("advance_amount")) - paid), 2),
            "can_pay": issued and po.get("status") in (PO_APPROVED, PO_DELIVERED)
                       and owed - paid > 0.005}


def ledger_entries(supplier_id):
    """The supplier's ledger, oldest first, with a running balance."""
    entries = []
    for po in issued_orders(supplier_id):
        items = len(po.get("items") or [])
        entries.append({
            "date": po.get("approved_at"), "order": 0, "ref": po.get("po_no"),
            "po_id": po["id"],
            "particulars": "Purchase order issued — %d item(s)%s" % (
                items, ", %s" % po["payment_terms"] if po.get("payment_terms") else ""),
            "debit": 0.0, "credit": _to_float(po.get("grand_total"))})
        if po.get("status") == PO_CANCELLED:
            entries.append({
                "date": po.get("cancelled_at") or po.get("approved_at"), "order": 2,
                "ref": po.get("po_no"), "po_id": po["id"],
                "particulars": "Purchase order cancelled",
                "debit": _to_float(po.get("grand_total")), "credit": 0.0})
    po_nos = {p["id"]: p.get("po_no") for p in db.read("purchase_orders")}
    for pay in db.query("supplier_payments", supplier_id=supplier_id):
        bits = [pay.get("kind"), pay.get("method")]
        if pay.get("po_id"):
            bits.append("PO %s" % po_nos.get(pay["po_id"], pay["po_id"]))
        entries.append({
            "date": pay.get("paid_on"), "order": 1,
            "ref": pay.get("reference") or "Payment", "po_id": pay.get("po_id"),
            "particulars": " · ".join(b for b in bits if b)
                           + (" — %s" % pay["notes"] if pay.get("notes") else ""),
            "debit": _to_float(pay.get("amount")), "credit": 0.0,
            "recorded_by": pay.get("recorded_by_name") or ""})
    entries.sort(key=lambda e: (e["date"] or "", e["order"]))
    balance = 0.0
    for e in entries:
        balance = round(balance + e["credit"] - e["debit"], 2)
        e["balance"] = balance
    return entries


def ledger_summary():
    """Every supplier in the directory with purchases, payments and balance."""
    suppliers, by_name = _supplier_index()
    totals = {s["id"]: {"credit": 0.0, "debit": 0.0, "open": 0, "last": ""}
              for s in suppliers}
    for po in db.read("purchase_orders"):
        if not po.get("approved_at"):
            continue
        row = totals.get(_po_supplier_id(po, by_name))
        if row is None:
            continue
        row["credit"] += _to_float(po.get("grand_total"))
        if po.get("status") == PO_CANCELLED:
            row["debit"] += _to_float(po.get("grand_total"))
        row["last"] = max(row["last"], po.get("approved_at") or "")
    for pay in db.read("supplier_payments"):
        row = totals.get(pay.get("supplier_id"))
        if row is None:
            continue
        row["debit"] += _to_float(pay.get("amount"))
        row["last"] = max(row["last"], pay.get("paid_on") or "")
    rows = []
    for s in suppliers:
        t = totals[s["id"]]
        rows.append({"id": s["id"], "name": s.get("name") or "",
                     "category": s.get("category") or "",
                     "purchases": round(t["credit"], 2), "payments": round(t["debit"], 2),
                     "balance": round(t["credit"] - t["debit"], 2),
                     "last": t["last"], "active": bool(t["last"])})
    rows.sort(key=lambda r: (not r["active"], -r["balance"], r["name"].lower()))
    return rows


def payable_orders(supplier_id):
    """Issued POs of this supplier that still have something to pay."""
    rows = []
    for po in issued_orders(supplier_id):
        summary = po_payment_summary(po)
        if summary["can_pay"]:
            rows.append(dict(po, **summary))
    rows.sort(key=lambda p: (p.get("approved_at") or "", p["id"]))
    return rows


def record_payment(supplier_id, user, form):
    """Record a payment to a supplier.  Returns (payment, error).

    form: {po_id (optional), amount, paid_on, kind, method, reference, notes}.
    A payment against a PO can't be more than what is still owed on it."""
    supplier = db.get("suppliers", supplier_id)
    if not supplier:
        return None, "Supplier not found."
    amount = round(_to_float(form.get("amount")), 2)
    if amount <= 0:
        return None, "Enter the amount paid."
    paid_on = (form.get("paid_on") or today_iso()).strip()
    if paid_on > today_iso():
        return None, "The payment date can't be in the future."
    kind = form.get("kind") if form.get("kind") in PAYMENT_KINDS else PAYMENT_KINDS[-1]
    method = form.get("method") if form.get("method") in PAYMENT_METHODS else PAYMENT_METHODS[0]
    po_id = None
    if form.get("po_id"):
        try:
            po_id = int(form.get("po_id"))
        except (TypeError, ValueError):
            po_id = None
        po = next((p for p in payable_orders(supplier["id"]) if p["id"] == po_id), None)
        if not po:
            return None, "That purchase order has nothing left to pay."
        if amount > po["balance"] + 0.005:
            return None, ("PO %s only has %s left to pay."
                          % (po["po_no"], services.fmt_peso(po["balance"])))
    payment = db.insert("supplier_payments", {
        "supplier_id": supplier["id"], "supplier_name": supplier.get("name") or "",
        "po_id": po_id, "amount": amount, "paid_on": paid_on, "kind": kind,
        "method": method, "reference": (form.get("reference") or "").strip(),
        "notes": (form.get("notes") or "").strip(),
        "recorded_by": user["id"], "recorded_by_name": user.get("name") or "",
        "created_at": now_iso()})
    return payment, None
