"""New records: the Admin adds, the Super Admin approves.

Four kinds of record join the system only this way:

* an employee   (the staff list, ``employees``)
* a supplier    (the Supplier Directory, ``suppliers`` + its item links)
* a material    (a raw material, ``raw_materials`` category "raw")
* a supply      (packaging, production / cleaning or office supply —
                 also ``raw_materials``, the other categories)

Only the Admin role fills in the form.  What was filled in waits in
``creation_requests`` — it is *not* part of the system yet, so no list,
dropdown or report shows it.  The Super Admin approves it (the record is
created then, after its details are checked once more) or rejects it with a
reason; either way the Admin is told.
"""
import json

import db
import profiles
import purchasing_service as pur
import security
import services
import supplier_service as sups

KINDS = {
    "employee": "Employee",
    "supplier": "Supplier",
    "material": "Raw material",
    "supply": "Supply",
}
PENDING, APPROVED, REJECTED, WITHDRAWN = ("Pending", "Approved", "Rejected",
                                          "Withdrawn")
BADGE = {PENDING: "badge-warn", APPROVED: "badge-ok", REJECTED: "badge-danger",
         WITHDRAWN: "badge-muted"}

# Material categories a "material" / a "supply" may be filed under.
MATERIAL_CATEGORIES = ["raw"]
SUPPLY_CATEGORIES = ["packaging", "cleaning", "office"]
STORAGE = {"raw": "Storage Area", "packaging": "Packing Area",
           "cleaning": "Utility Room", "office": "Office"}


def can_add(user):
    """Only the Admin role fills in a new record (the Super Admin approves)."""
    return security.normalize_role((user or {}).get("role")) == "admin"


def can_decide(user):
    return security.is_super_admin(user)


def payload_of(req):
    data = req.get("payload")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            data = {}
    return data if isinstance(data, dict) else {}


def pending():
    rows = [r for r in db.read("creation_requests") if r.get("status") == PENDING]
    rows.sort(key=lambda r: r.get("created_at") or "")
    return rows


def pending_count():
    return len(pending())


# --- Checking a new record -------------------------------------------------------
def _key(kind, payload):
    """What makes two records of a kind the same (for duplicate checks)."""
    if kind == "supplier":
        return sups.norm(payload.get("name")) + "|" + sups.norm(payload.get("address"))
    return sups.norm(payload.get("name"))


def _already_waiting(kind, payload, exclude_id=None):
    same = "supply" if kind == "material" else kind
    for r in pending():
        other = "supply" if r["kind"] == "material" else r["kind"]
        if other == same and r["id"] != exclude_id \
                and _key(r["kind"], payload_of(r)) == _key(kind, payload):
            return r
    return None


def problem(kind, payload):
    """Why this record can't be added (it already exists), or None."""
    if kind == "employee":
        values, err = profiles.clean_employee(payload, pur.DEPARTMENTS)
        return err
    if kind == "supplier":
        return sups.duplicate_problem(payload.get("name"), payload.get("address"))
    if kind in ("material", "supply"):
        name = sups.norm(payload.get("name"))
        for m in db.read("raw_materials"):
            if sups.norm(m.get("name")) == name:
                return "'%s' is already on the materials list." % m.get("name")
        return None
    return "Unknown kind of record."


def clean_item(form, kind):
    """A raw material / supply from the form.  Returns (payload, error)."""
    name = " ".join((form.get("name") or "").split())[:120]
    allowed = MATERIAL_CATEGORIES if kind == "material" else SUPPLY_CATEGORIES
    category = form.get("category") if form.get("category") in allowed else allowed[0]
    if kind == "supply" and form.get("category") not in allowed:
        return None, "Choose what kind of supply it is."
    if not name:
        return None, "Enter the item's name."
    unit = (form.get("unit") or "").strip()[:20]
    if not unit:
        return None, "Choose the unit it is counted in."
    track = form.get("track_mode") if form.get("track_mode") in services.TRACK_MODE_LABELS else "piece"
    reorder = services._to_float(form.get("reorder_level"))
    cost = services._to_float(form.get("unit_cost"))
    if reorder < 0 or cost < 0:
        return None, "The reorder level and unit cost can't be negative."
    supplier_id = None
    if (form.get("supplier_id") or "").strip():
        try:
            supplier = db.get("suppliers", int(form.get("supplier_id")))
        except (TypeError, ValueError):
            supplier = None
        if not supplier:
            return None, "Choose the usual supplier from the list, or leave it blank."
        supplier_id = supplier["id"]
    payload = {
        "name": name, "category": category,
        "subcategory": " ".join((form.get("subcategory") or "").split())[:60],
        "unit": unit, "track_mode": track, "reorder_level": round(reorder, 3),
        "unit_cost": round(cost, 2), "supplier_id": supplier_id,
        "storage_location": " ".join((form.get("storage_location") or "").split())[:80]
                            or STORAGE.get(category, ""),
    }
    return payload, None


def title_of(kind, payload):
    if kind == "employee":
        return payload.get("name") or profiles.full_name(
            payload.get("first_name"), payload.get("middle_name"),
            payload.get("last_name"), payload.get("suffix"))
    if kind == "supplier":
        name = payload.get("name") or ""
        return "%s (%s)" % (name, payload["address"]) if payload.get("address") else name
    return payload.get("name") or ""


def details(req):
    """(label, value) rows describing what was filled in, for the pages."""
    kind, p = req.get("kind"), payload_of(req)
    if kind == "employee":
        rows = [("Name", p.get("name")), ("Position", p.get("position")),
                ("Department", p.get("department")),
                ("Employee no.", p.get("employee_no")), ("Phone", p.get("phone"))]
    elif kind == "supplier":
        names = {m["id"]: m.get("name") for m in db.read("raw_materials")}
        items = [names.get(int(m)) for m in p.get("material_ids") or []
                 if str(m).isdigit() and int(m) in names]
        rows = [("Name", p.get("name")), ("Filed under", p.get("category")),
                ("Supplier type", p.get("supplier_type")),
                ("Items supplied", p.get("materials_supplied")),
                ("Catalog items", ", ".join(items)),
                ("TIN", p.get("tin")), ("Contact person", p.get("contact_person")),
                ("Phone", p.get("phone")), ("Email", p.get("email")),
                ("Address", p.get("address")), ("Buy from", p.get("buy_from")),
                ("Usual lead time", ("%s days" % p["lead_time_days"])
                 if p.get("lead_time_days") not in (None, "") else ""),
                ("Notes", p.get("notes")),
                ("No. in the spreadsheet", p.get("ref_no")),
                ("Approved Supplier List", "Put on the list when created"
                 if p.get("approved") else "")]
    else:
        supplier = db.get("suppliers", p.get("supplier_id")) if p.get("supplier_id") else None
        rows = [("Name", p.get("name")),
                ("Category", services.MATERIAL_CATEGORY_LABELS.get(p.get("category"),
                                                                   p.get("category"))),
                ("Group", p.get("subcategory")), ("Unit", p.get("unit")),
                ("Counted", services.TRACK_MODE_LABELS.get(p.get("track_mode"))),
                ("Reorder level", services.fmt_qty(p.get("reorder_level"))
                 if p.get("reorder_level") else ""),
                ("Unit cost", services.fmt_peso(p.get("unit_cost"))
                 if p.get("unit_cost") else ""),
                ("Usual supplier", supplier.get("name") if supplier else ""),
                ("Storage", p.get("storage_location")),
                ("Opening stock", "0 — stock comes in through receiving or a count")]
    return [(label, value) for label, value in rows if value not in (None, "")]


# --- The flow ----------------------------------------------------------------------
def submit(kind, payload, user):
    """The Admin sends a new record for approval.  Returns (request, error)."""
    if kind not in KINDS:
        return None, "Unknown kind of record."
    if not can_add(user):
        return None, "Only the Admin adds new records; the Super Admin approves them."
    err = problem(kind, payload)
    if err:
        return None, err
    waiting = _already_waiting(kind, payload)
    if waiting:
        return None, ("'%s' is already waiting for the Super Admin's approval."
                      % waiting.get("title"))
    now = services.now_iso()
    req = db.insert("creation_requests", {
        "kind": kind, "title": title_of(kind, payload)[:200],
        "payload": payload, "status": PENDING,
        "requested_by": user["id"], "requested_by_name": user.get("name") or "",
        "decided_by": None, "decided_by_name": "", "decided_at": None,
        "remarks": "", "record_id": None, "created_at": now,
    })
    services.notify_team(["super_admin"], "New %s to approve: %s (added by %s)."
                         % (KINDS[kind].lower(), req["title"], req["requested_by_name"]),
                         link="/additions/%d" % req["id"], admins=False)
    return req, None


def _create(kind, payload, req, user):
    """Make the approved record part of the system.  Returns (record, error)."""
    now = services.now_iso()
    if kind == "employee":
        values, err = profiles.clean_employee(payload, pur.DEPARTMENTS)
        if err:
            return None, err
        return db.insert("employees", dict(values, created_at=now)), None
    if kind == "supplier":
        err = sups.duplicate_problem(payload.get("name"), payload.get("address"))
        if err:
            return None, err
        record = {k: v for k, v in payload.items() if k not in ("material_ids", "approved")}
        on_list = bool(payload.get("approved"))
        record.update(created_at=now, approved=on_list,
                      approved_by=req.get("requested_by") if on_list else None,
                      approved_at=now if on_list else None)
        row = db.insert("suppliers", record)
        sups.set_supplier_items(row["id"], payload.get("material_ids") or [])
        return row, None
    err = problem(kind, payload)
    if err:
        return None, err
    row = db.insert("raw_materials", dict(payload, stock_qty=0.0, expiry_date="",
                                          created_at=now))
    sups.ensure_link(payload.get("supplier_id"), row["id"])
    return row, None


def approve(req_id, user):
    """The Super Admin approves: the record is created.  (request, error)"""
    req = db.get("creation_requests", req_id)
    if not req:
        return None, "Not found."
    if not can_decide(user):
        return None, "Only the Super Admin approves new records."
    if req.get("status") != PENDING:
        return None, "This was already %s." % (req.get("status") or "decided").lower()
    record, err = _create(req["kind"], payload_of(req), req, user)
    if err:
        return None, "It can't be added: %s Reject it, or ask the Admin to correct it." % err
    req = db.update("creation_requests", req_id, {
        "status": APPROVED, "decided_by": user["id"],
        "decided_by_name": user.get("name") or "", "decided_at": services.now_iso(),
        "record_id": record["id"]})
    services.notify(req.get("requested_by"), "Approved: %s %s is now in the system."
                    % (KINDS[req["kind"]].lower(), req["title"]),
                    link="/additions/%d" % req_id)
    return req, None


def reject(req_id, user, reason):
    req = db.get("creation_requests", req_id)
    if not req:
        return None, "Not found."
    if not can_decide(user):
        return None, "Only the Super Admin rejects new records."
    if req.get("status") != PENDING:
        return None, "This was already %s." % (req.get("status") or "decided").lower()
    reason = " ".join((reason or "").split())[:500]
    if not reason:
        return None, "Say why it is rejected — the Admin sees the reason."
    req = db.update("creation_requests", req_id, {
        "status": REJECTED, "decided_by": user["id"],
        "decided_by_name": user.get("name") or "", "decided_at": services.now_iso(),
        "remarks": reason})
    services.notify(req.get("requested_by"), "Rejected: %s %s — %s"
                    % (KINDS[req["kind"]].lower(), req["title"], reason),
                    link="/additions/%d" % req_id)
    return req, None


def withdraw(req_id, user):
    """The Admin who sent it takes it back while it is still waiting."""
    req = db.get("creation_requests", req_id)
    if not req or req.get("status") != PENDING:
        return None, "Only a record still waiting for approval can be withdrawn."
    if req.get("requested_by") != (user or {}).get("id"):
        return None, "Only the Admin who sent it can withdraw it."
    return db.update("creation_requests", req_id, {
        "status": WITHDRAWN, "decided_at": services.now_iso()}), None


def record_link(req):
    """(endpoint, kwargs) of the created record's page, or None."""
    rid = req.get("record_id")
    if not rid or req.get("status") != APPROVED:
        return None
    if req["kind"] == "supplier":
        return "suppliers.detail", {"supplier_id": rid}
    if req["kind"] in ("material", "supply"):
        return "supplies.item", {"material_id": rid}
    return "employees.index", {}
