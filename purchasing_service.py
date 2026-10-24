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

Flow: a head raises a PR → an admin approves, revises or rejects it (the
existing approvals) → an admin prepares a PO from the approved lines →
the PO is approved, printed and sent → the Receiving Team Head records the
delivery against the PO (each box tied to its PO line) → boxes pass
inspection → the PO is marked delivered once everything ordered has been
accepted (or an admin closes it short).
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
PAYMENT_TERMS = ["Cash on delivery", "Cash before delivery", "7 days",
                 "15 days", "30 days"]
DEFAULT_TERMS = ("Supplier shall provide goods according to the "
                 "specifications, quantities, prices and delivery requirements "
                 "stated in this PO. Please reference the PO No. on invoices "
                 "and delivery documents.")

PO_PREPARED = "Prepared"
PO_APPROVED = "Approved"
PO_DELIVERED = "Delivered"
PO_CANCELLED = "Cancelled"
PO_BADGE = {PO_PREPARED: "badge-warn", PO_APPROVED: "badge-ok",
            PO_DELIVERED: "badge-ok", PO_CANCELLED: "badge-danger"}
PO_TRACK = ["Prepared", "Approved", "Delivered"]


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
        desc = (raw.get("description") or "").strip()
        qty = _to_float(raw.get("qty"))
        if not desc and qty <= 0:
            continue
        if not desc:
            problems.append("Every line needs an item description.")
            continue
        if qty <= 0:
            problems.append("Enter a quantity for %s." % desc)
            continue
        cost = max(0.0, _to_float(raw.get("est_unit_cost")))
        material_id = raw.get("material_id")
        try:
            material_id = int(material_id) if material_id else None
        except (TypeError, ValueError):
            material_id = None
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
                            date_requested=""):
    """Raise a PR from the paper-form fields.  Returns (request, error)."""
    if department not in DEPARTMENTS:
        return None, "Choose the requesting department."
    purpose = (purpose or "").strip()
    if not purpose:
        return None, "Give the purpose / justification — the approver sees it."
    items, problems = _clean_lines(lines)
    if problems:
        return None, problems[0]
    if not items:
        return None, "Add at least one item with a quantity."
    requested_on = date_requested or today_iso()
    required_date = (required_date or "").strip()
    if required_date and required_date < requested_on:
        return None, "The required date can't be before the date requested."
    try:
        number = pr_no or next_pr_no(date.fromisoformat(requested_on))
    except ValueError:
        number = next_pr_no()
    req = db.insert("purchase_requests", {
        "type": "general",
        "pr_no": number,
        "requestor_id": int(user["id"]),
        "requested_by_name": user.get("name") or "",
        "department": department,
        "purpose": purpose,
        "reason": purpose,
        "date_requested": requested_on,
        "needed_by": required_date,
        "urgency": urgency if urgency in services.URGENCY_LEVELS else "Normal",
        "notes": (notes or "").strip(),
        "remarks": "",
        "total_estimated": round(sum(i["est_total"] for i in items), 2),
        "status": "Pending Approval",
        "checked_by_name": "",
        "approved_by_name": "",
        "purchasing_by_name": "",
        "purchasing_date": "",
        "items": items,
        "created_at": now_iso(),
    })
    services.notify_admins(
        "Purchase request %s from %s — %s (est. %s) awaiting approval."
        % (number, department, purpose[:60], services.fmt_peso(req["total_estimated"])),
        link="/approvals/%d" % req["id"])
    return req, None


def record_signoff(request_id, checked_by_name, approver):
    """After an admin decision: the Checked by / Approved by names."""
    return db.update("purchase_requests", request_id, {
        "checked_by_name": (checked_by_name or "").strip(),
        "approved_by_name": (approver or {}).get("name") or "",
    })


# --- Purchase orders -----------------------------------------------------------
def orderable_items(req):
    """Approved lines of a PR not yet on a (live) purchase order."""
    return [it for it in (req.get("items") or [])
            if it.get("status") == "Ordered" and not it.get("po_id")]


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


def _totals(items, discount, freight, tax):
    subtotal = round(sum(i["amount"] for i in items), 2)
    discount, freight, tax = (round(max(0.0, _to_float(v)), 2)
                              for v in (discount, freight, tax))
    return {"subtotal": subtotal, "discount": discount, "freight": freight,
            "tax": tax, "grand_total": round(subtotal - discount + freight + tax, 2)}


def create_purchase_order(request_id, user, header, supplier, delivery, lines,
                          totals, signoffs):
    """Prepare a PO from an approved PR.  Returns (po, error).

    lines: [{pr_item_id, description, brand_model, qty, unit, unit_price,
             requested_delivery, vat, remarks}] — only approved PR lines that
    are not on another PO yet."""
    req = db.get("purchase_requests", request_id)
    if not req:
        return None, "Purchase request not found."
    open_items = {it["item_id"]: it for it in orderable_items(req)}
    if not open_items:
        return None, "This request has no approved items left to order."
    supplier_name = (supplier.get("name") or "").strip()
    if not supplier_name:
        return None, "Choose or type the supplier."
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
        items.append({
            "line_no": len(items) + 1,
            "pr_item_id": pr_item_id,
            "material_id": open_items[pr_item_id].get("material_id"),
            "description": (raw.get("description") or
                            open_items[pr_item_id]["name"]).strip(),
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

    supplier_id = supplier.get("id")
    try:
        supplier_id = int(supplier_id) if supplier_id else None
    except (TypeError, ValueError):
        supplier_id = None
    po = db.insert("purchase_orders", dict(
        po_no=next_po_no(req),
        purchase_request_id=req["id"],
        pr_no=req.get("pr_no") or "",
        po_date=po_date,
        expected_delivery=expected,
        payment_terms=(header.get("payment_terms") or "").strip(),
        supplier_id=supplier_id,
        supplier_name=supplier_name,
        supplier_address=(supplier.get("address") or "").strip(),
        supplier_contact=(supplier.get("contact") or "").strip(),
        supplier_tin=(supplier.get("tin") or "").strip(),
        supplier_contact_person=(supplier.get("contact_person") or "").strip(),
        supplier_reference=(supplier.get("reference") or "").strip(),
        deliver_to=(delivery.get("deliver_to") or COMPANY_NAME).strip(),
        delivery_address=(delivery.get("address") or COMPANY_ADDRESS).strip(),
        delivery_instructions=(delivery.get("instructions") or "").strip(),
        delivery_contact=(delivery.get("contact") or "").strip(),
        items=items,
        terms=(totals.get("terms") or DEFAULT_TERMS).strip(),
        prepared_by_name=(signoffs.get("prepared_by") or user.get("name") or "").strip(),
        prepared_at=today_iso(),
        checked_by_name=(signoffs.get("checked_by") or "").strip(),
        approved_by_name="",
        approved_at="",
        supplier_ack_name="",
        supplier_ack_date="",
        status=PO_PREPARED,
        delivered_at="",
        created_by=user["id"],
        created_at=now_iso(),
        **_totals(items, totals.get("discount"), totals.get("freight"),
                  totals.get("tax")),
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
    return po, None


def approve_purchase_order(po_id, user, approved_by_name=""):
    po = db.get("purchase_orders", po_id)
    if not po:
        return None, "Purchase order not found."
    if po.get("status") != PO_PREPARED:
        return None, "Only a prepared PO can be approved."
    return db.update("purchase_orders", po_id, {
        "status": PO_APPROVED,
        "approved_by_name": (approved_by_name or user.get("name") or "").strip(),
        "approved_at": today_iso(),
    }), None


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
    return db.update("purchase_orders", po_id, {"status": PO_CANCELLED}), None


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
    reached = {PO_PREPARED: 0, PO_APPROVED: 1, PO_DELIVERED: 2}.get(status, 0)
    return [(label, "done" if i < reached else ("now" if i == reached else ""))
            for i, label in enumerate(PO_TRACK)]


def supplier_card(supplier):
    """What the PO's supplier block shows, from the Supplier Directory."""
    contact = ", ".join(p for p in (supplier.get("phone"), supplier.get("email")) if p)
    return {"id": supplier["id"], "name": supplier.get("name") or "",
            "address": supplier.get("address") or "",
            "contact": contact or supplier.get("buy_from") or "",
            "tin": supplier.get("tin") or "",
            "contact_person": supplier.get("contact_person") or ""}


def purchasing_counts():
    orders = db.read("purchase_orders")
    return {
        "awaiting_po": len(requests_awaiting_po()),
        "prepared": sum(1 for p in orders if p.get("status") == PO_PREPARED),
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
    services.apply_decision(req["id"], {}, "Approved for repair of the dehydrator.",
                            "approve", decider_id=admin["id"])
    record_signoff(req["id"], "Jefrey S. Pabillore", {"name": "Bernadith M. Frianeza"})
    trimix = next((s for s in suppliers.values() if s.get("name") == "Trimix Butuan"), None)
    supplier = supplier_card(trimix) if trimix else {"name": "Trimix Butuan",
                                                     "address": "Butuan"}
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
        {"prepared_by": "Bernadith Frianeza", "checked_by": "Fe Amor Eballar"})
    if po:
        approve_purchase_order(po["id"], admin, "Janice Nilda A. Mira")
        db.update("purchase_orders", po["id"], {"approved_at": "2026-09-21",
                                                "prepared_at": "2026-09-21"})
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
