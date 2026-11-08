"""Supplier Directory logic shared by the directory, purchasing and the seed.

* Supplier types — the "Reference" column of the client's spreadsheet.
* The spreadsheet clean-up (``clean_sheet``) used by both ``seed_suppliers.py``
  (converted once) and the Import from Excel screen, so both give the same
  values and re-importing the same file changes nothing.
* Item links — which catalog items (``raw_materials``) each supplier supplies,
  kept in the ``supplier_items`` table.  ``raw_materials.supplier_id`` stays
  the item's *usual* supplier and is always one of its linked suppliers.
* Which suppliers a purchase request line or a purchase order may use.
"""
import re

import db
import services

CATEGORIES = ["Raw", "Production", "Packing", "Office"]

DIRECT = "Direct Supplier / Consolidator"
ONLINE = "Online shop (Shopee)"
SUPPLIER_TYPES = [DIRECT, "Cooperative", "Farmer", "Retailer", "Reseller",
                  ONLINE, "Other"]
SUPPLIER_TYPE_HINTS = {
    DIRECT: "Sells to us directly, or gathers produce from several growers",
    "Cooperative": "A farmers' cooperative or association",
    "Farmer": "An individual grower",
    "Retailer": "A store selling at retail",
    "Reseller": "Resells another maker's goods",
    ONLINE: "A shop on Shopee or another marketplace",
    "Other": "Anything else",
}

# The spreadsheet's "Reference" values (lower-cased) -> (type, note).
_TYPE_MAP = {
    "direct supplier/consolidator": (DIRECT, ""),
    "direct supplier": (DIRECT, ""),
    "consolidator": (DIRECT, ""),
    "supplier": (DIRECT, ""),
    "cooperative": ("Cooperative", ""),
    "coop": ("Cooperative", ""),
    "farmer": ("Farmer", ""),
    "retailer": ("Retailer", ""),
    "reseller": ("Reseller", ""),
    "shopee": (ONLINE, ""),
    "online shop": (ONLINE, ""),
    "shopee/direct supplier": (ONLINE, "Also sells direct"),
    "other": ("Other", ""),
}

# Fields filled from the spreadsheet, in its column order.
SHEET_FIELDS = [
    ("ref_no", "No."), ("name", "Name"), ("address", "Address"),
    ("phone", "Mobile#"), ("contact_person", "Contact person"),
    ("materials_supplied", "Items supplied"), ("email", "Email"),
    ("tin", "TIN"), ("supplier_type", "Supplier type"),
]
# What a matched row may fill in or change (the name is how it matched).
MERGE_FIELDS = [f for f in SHEET_FIELDS if f[0] != "name"]
LIMITS = {"ref_no": 10, "name": 120, "address": 300, "phone": 120,
          "contact_person": 120, "materials_supplied": 1000, "email": 120,
          "tin": 30, "supplier_type": 40, "notes": 500}

_BLANKS = {"", "-", "--", "—", "–", "n/a", "na", "none"}


def blank(value):
    """A cell's text, with the placeholders the sheet uses for "none" removed."""
    text = re.sub(r"\s+", " ", str(value if value is not None else "")).strip()
    return "" if text.lower() in _BLANKS else text


def norm(text):
    """Lower-case letters and digits only — for matching names and addresses."""
    return " ".join(re.sub(r"[^0-9a-z]+", " ", (text or "").lower()).split())


def map_supplier_type(text):
    """The spreadsheet's Reference -> (supplier type, note)."""
    key = re.sub(r"\s*/\s*", "/", blank(text).lower())
    if not key:
        return "", ""
    if key in _TYPE_MAP:
        return _TYPE_MAP[key]
    for t in SUPPLIER_TYPES:
        if key == t.lower():
            return t, ""
    return "Other", "Reference: %s" % blank(text)


def clean_type(value):
    """A supplier type from a form: one of SUPPLIER_TYPES, or ''."""
    value = (value or "").strip()
    return value if value in SUPPLIER_TYPES else ""


# --- Phones -------------------------------------------------------------------
def _format_number(part):
    digits = re.sub(r"\D", "", part)
    if len(digits) == 11 and digits.startswith("09"):
        return "%s-%s-%s" % (digits[:4], digits[4:7], digits[7:])
    return part.strip()


def clean_phone(text):
    """Several numbers in one cell ("0919-232-4051/0930-172-2313") -> each
    number on its own, 11-digit mobiles written 09xx-xxx-xxxx, joined " / "."""
    parts = [blank(p) for p in re.split(r"[/,;\r\n]+", str(text or ""))]
    return " / ".join(_format_number(p) for p in parts if p)


def phone_list(phone):
    """A stored phone field as a list of numbers (one per line on screen)."""
    return [p.strip() for p in re.split(r"\s*/\s*|\n", phone or "") if p.strip()]


# --- Category -------------------------------------------------------------------
# Checked in this order: office words win over "sticker", which wins over
# produce words ("turmeric powder sticker" is packing, not raw).
_CATEGORY_WORDS = [
    ("Office", ("office", "ink", "cutting mat", "cuttuing mat", "phototop",
                "photo sticker", "vinyl", "calling card", "comp.", "computer")),
    ("Production", ("tissue", "cleaner", "glove", "steel wool", "gasul", "lpg",
                    "dishwash", "diswahsing", "detergent", "temp", "dipper",
                    "mask", "sponge")),
    ("Packing", ("bottle", "bot.", "jar", "pouch", "box", "packing", "label",
                 "sticker", "seal", "shrink", "vacuum", "rope", "cord",
                 "spray", "lipbalm", "lip balm", "tape", "filler", "kraft",
                 "bag", "liner", "berry", "clabel", "therma")),
    ("Raw", ("honey", "coffee", "lemon", "calamansi", "ginger", "turmeric",
             "cacao", "cocoa", "corn", "banana", "moringa", "vinegar")),
]


def suggest_category(items):
    """Raw / Production / Packing / Office from the items text ('' if no
    word matches).  Words match at the start of a word ("ink" is not in
    "shrink wrap")."""
    text = (items or "").lower()
    for category, words in _CATEGORY_WORDS:
        if any(re.search(r"\b" + re.escape(w), text) for w in words):
            return category
    return ""


# --- The spreadsheet -------------------------------------------------------------
def _header_index(rows):
    """Row index of the header row (the one with Name and Address)."""
    for i, row in enumerate(rows[:12]):
        cells = [norm(c) for c in row]
        if "name" in cells and "address" in cells:
            return i
    return None


def _column(headers, *names):
    for i, h in enumerate(headers):
        if norm(h) in names:
            return i
    return None


def clean_sheet(rows):
    """The client's Suppliers Directory sheet (rows of cell strings) ->
    (suppliers, problems).

    Each supplier is a dict of SHEET_FIELDS plus ``notes`` and the suggested
    ``category``.  Clean-up: rows with no No. and no Name that carry an item
    are more items of the supplier above; "-" and the like are blank; item
    text typed in the Contact Person column (it has digits, a name doesn't)
    moves to the items; several phone numbers are split and tidied."""
    head = _header_index(rows)
    if head is None:
        return [], ["This doesn't look like the Suppliers Directory: no header "
                    "row with Name and Address was found."]
    headers = rows[head]
    col = {
        "ref_no": _column(headers, "no", "no.", "number"),
        "name": _column(headers, "name", "supplier", "supplier name"),
        "address": _column(headers, "address"),
        "phone": _column(headers, "mobile", "mobile#", "mobile no", "phone",
                         "contact no", "contact number"),
        "contact_person": _column(headers, "contact person"),
        "materials_supplied": _column(headers, "supplier category", "items",
                                      "items supplied", "category"),
        "email": _column(headers, "email add", "email", "email address"),
        "tin": _column(headers, "tin"),
        "reference": _column(headers, "reference", "supplier type", "type"),
    }

    def cell(row, key):
        i = col.get(key)
        return blank(row[i]) if i is not None and i < len(row) else ""

    suppliers, problems = [], []
    for n, row in enumerate(rows[head + 1:], start=head + 2):
        name, ref_no = cell(row, "name"), cell(row, "ref_no")
        items = cell(row, "materials_supplied")
        if not name and not ref_no:
            if items and suppliers:
                prev = suppliers[-1]
                prev["materials_supplied"] = "; ".join(
                    p for p in (prev["materials_supplied"], items) if p)
            continue
        if not name:
            problems.append("Row %d has a No. but no name — skipped." % n)
            continue
        contact = cell(row, "contact_person")
        if contact and re.search(r"\d", contact):
            items = "; ".join(p for p in (items, contact) if p)
            contact = ""
        stype, note = map_supplier_type(cell(row, "reference"))
        email = cell(row, "email")
        supplier = {
            "ref_no": ref_no, "name": name, "address": cell(row, "address"),
            "phone": clean_phone(cell(row, "phone")),
            "contact_person": contact, "materials_supplied": items,
            "email": email if "@" in email else "",
            "tin": cell(row, "tin"), "supplier_type": stype, "notes": note,
        }
        supplier["category"] = suggest_category(items)
        suppliers.append(supplier)
    for s in suppliers:
        if not s["category"]:
            s["category"] = suggest_category(s["materials_supplied"]) or "Production"
    return [sanitize(s) for s in suppliers], problems


def sanitize(row):
    """Clamp a supplier dict (e.g. posted back from the import preview)."""
    out = {}
    for key, limit in LIMITS.items():
        out[key] = blank(row.get(key))[:limit]
    out["supplier_type"] = clean_type(out["supplier_type"])
    category = (row.get("category") or "").strip()
    out["category"] = category if category in CATEGORIES else ""
    if out["email"] and "@" not in out["email"]:
        out["email"] = ""
    return out


# --- Item links -------------------------------------------------------------------
def links():
    return db.read("supplier_items")


def approved_links():
    """Links of suppliers on the Approved Supplier List — the only ones that
    narrow what a purchase request or purchase order may pick."""
    approved = {s["id"] for s in db.read("suppliers") if services.supplier_approved(s)}
    return [l for l in links() if l.get("supplier_id") in approved]


def links_by_material(rows=None):
    out = {}
    for link in rows if rows is not None else links():
        out.setdefault(link.get("material_id"), set()).add(link.get("supplier_id"))
    return out


def links_by_supplier(rows=None):
    out = {}
    for link in rows if rows is not None else links():
        out.setdefault(link.get("supplier_id"), set()).add(link.get("material_id"))
    return out


def material_ids_of(supplier_id):
    return links_by_supplier().get(supplier_id, set())


def set_supplier_items(supplier_id, material_ids):
    """Replace the catalog items a supplier supplies.  An item whose usual
    supplier is no longer linked moves to another linked supplier (or none)."""
    known = {m["id"] for m in db.read("raw_materials")}
    wanted = set()
    for m in material_ids:
        try:
            m = int(m)
        except (TypeError, ValueError):
            continue
        if m in known:
            wanted.add(m)
    rows = links()
    keep = [l for l in rows if l.get("supplier_id") != supplier_id]
    have = {l.get("material_id") for l in rows if l.get("supplier_id") == supplier_id}
    now = services.now_iso()
    next_id = max([l.get("id") or 0 for l in rows] or [0]) + 1
    mine = [l for l in rows if l.get("supplier_id") == supplier_id
            and l.get("material_id") in wanted]
    for mid in sorted(wanted - have):
        mine.append({"id": next_id, "supplier_id": supplier_id,
                     "material_id": mid, "created_at": now})
        next_id += 1
    db.write("supplier_items", sorted(keep + mine, key=lambda l: l["id"]))
    by_material = links_by_material(keep + mine)
    for mid in have - wanted:
        material = db.get("raw_materials", mid)
        if material and material.get("supplier_id") == supplier_id:
            others = sorted(by_material.get(mid, set()))
            db.update("raw_materials", mid,
                      {"supplier_id": others[0] if others else None})
    return wanted


def ensure_link(supplier_id, material_id):
    """Link a supplier to an item (e.g. when it is made the usual supplier)."""
    if not supplier_id or not material_id:
        return
    if any(l.get("supplier_id") == supplier_id and l.get("material_id") == material_id
           for l in links()):
        return
    db.insert("supplier_items", {"supplier_id": supplier_id,
                                 "material_id": material_id,
                                 "created_at": services.now_iso()})


def remove_supplier_links(supplier_id):
    set_supplier_items(supplier_id, [])


_STOP = {"ml", "mm", "cm", "in", "liter", "litre", "l", "g", "kg", "pc", "pcs",
         "the", "and", "with", "for", "raw", "big", "small", "medium", "large",
         "black", "white", "brown", "clear", "round", "square", "hexagonal",
         "matte", "c", "cc", "of"}


def _words(text):
    out = []
    for w in re.findall(r"[a-z]+", (text or "").lower()):
        if len(w) < 3 or w in _STOP:
            continue
        out.append(w[:-1] if w.endswith("s") and len(w) > 4 else w)
    return out


def _sizes(text):
    t = (text or "").lower().replace(" ", "")
    return set(re.findall(r"\d+(?:\.\d+)?x\d+(?:\.\d+)?|\d+(?:\.\d+)?(?:ml|mm|l)\b", t))


def suggest_items(items_text, materials=None):
    """Catalog items whose name matches a supplier's free-text items — a
    suggestion only; someone confirms each link."""
    text = (items_text or "").lower()
    if not text.strip():
        return []
    sizes = _sizes(text)
    out = []
    for m in materials if materials is not None else db.read("raw_materials"):
        if not services.is_purchasable(m):
            continue                      # made in the plant, never supplied
        words = _words(m.get("name"))
        if not words:
            continue
        own = _sizes(m.get("name"))
        if sizes and own and not (sizes & own):
            continue                      # "jar 300ml" is not the 100 mL jar
        hits = [w for w in words if re.search(r"\b" + w, text)]
        if len(hits) == len(words) or (hits and sizes & own):
            out.append(m["id"])
    return out


def duplicate_problem(name, address, exclude_id=None):
    """Why a supplier with this name and address can't be added, or None.
    The same name is allowed for another branch (a different address)."""
    for s in db.read("suppliers"):
        if s.get("id") == exclude_id:
            continue
        if norm(s.get("name")) == norm(name) and norm(s.get("address")) == norm(address):
            return ("'%s'%s is already in the directory. For another branch, "
                    "enter its address so the two can be told apart."
                    % (s.get("name"), (" at %s" % s["address"]) if s.get("address") else ""))
    return None


# --- Who can be picked ---------------------------------------------------------------
def approved_suppliers():
    return sorted((s for s in db.read("suppliers") if services.supplier_approved(s)),
                  key=lambda s: ((s.get("name") or "").lower(), (s.get("address") or "").lower()))


def display_name(supplier, everyone=None):
    """The name, with the address added when another supplier has the same
    name (the two Metro Ace branches)."""
    name = supplier.get("name") or ""
    everyone = everyone if everyone is not None else db.read("suppliers")
    twins = [s for s in everyone if norm(s.get("name")) == norm(name)]
    if len(twins) > 1 and supplier.get("address"):
        return "%s (%s)" % (name, supplier["address"])
    return name


def pr_supplier_problem(supplier, material_id, by_material=None):
    """Why ``supplier`` can't be a PR line's preferred supplier, or None."""
    if not supplier:
        return "Pick a supplier from the list, or leave it blank."
    if not services.supplier_approved(supplier):
        return ("%s is not on the Approved Supplier List — pick another, or "
                "leave it blank." % supplier.get("name"))
    if material_id:
        by_material = by_material if by_material is not None else links_by_material(approved_links())
        linked = by_material.get(material_id) or set()
        if linked and supplier["id"] not in linked:
            material = db.get("raw_materials", material_id) or {}
            return ("%s doesn't supply %s in the Supplier Directory — pick one "
                    "of its suppliers, or leave it blank."
                    % (supplier.get("name"), material.get("name") or "this item"))
    return None


def relevant_materials(material_ids, by_material=None):
    """The items that narrow the supplier list: catalog items that have at
    least one linked supplier."""
    by_material = by_material if by_material is not None else links_by_material(approved_links())
    return {m for m in material_ids if m and by_material.get(m)}


def po_supplier_problem(supplier, material_ids, by_supplier=None, by_material=None):
    """Why ``supplier`` can't be the supplier of a PO for these items, or None.
    Only items that an approved supplier is linked to narrow the choice, and
    a supplier with no linked items at all is not ruled out."""
    by_material = by_material if by_material is not None else links_by_material(approved_links())
    by_supplier = by_supplier if by_supplier is not None else links_by_supplier()
    wanted = relevant_materials(material_ids, by_material)
    mine = by_supplier.get(supplier["id"]) or set()
    if wanted and mine and not (mine & wanted):
        return ("%s doesn't supply any of the items on this PO in the Supplier "
                "Directory — pick another supplier." % supplier.get("name"))
    return None


# --- Import ----------------------------------------------------------------------------
def match_supplier(row, suppliers):
    """(existing supplier, reason) for a cleaned row, matched by name and
    address; (None, '') when it is new."""
    name = norm(row.get("name"))
    same = [s for s in suppliers if norm(s.get("name")) == name]
    if not same:
        return None, ""
    address = norm(row.get("address"))
    exact = [s for s in same if norm(s.get("address")) == address]
    if exact:
        return exact[0], "same name and address"
    if len(same) == 1 and (not address or not norm(same[0].get("address"))):
        return same[0], "same name"
    return None, ""


def compare(row, supplier):
    """(fields the row would fill in, conflicts) against a directory entry."""
    fills, conflicts = [], []
    for key, label in MERGE_FIELDS:
        incoming, current = row.get(key) or "", (supplier.get(key) or "")
        if current in (None, ""):
            current = ""
        current = str(current)
        if not incoming or blank(incoming) == blank(current):
            continue
        if not current:
            fills.append({"field": key, "label": label, "value": incoming})
        else:
            conflicts.append({"field": key, "label": label,
                              "current": current, "incoming": incoming})
    return fills, conflicts


def preview_rows(cleaned):
    """What the import would do with each cleaned row."""
    suppliers = db.read("suppliers")
    materials = db.read("raw_materials")
    out = []
    for i, row in enumerate(cleaned):
        match, reason = match_supplier(row, suppliers)
        entry = {"i": i, "row": row, "match_id": match["id"] if match else None,
                 "reason": reason, "fills": [], "conflicts": [],
                 "suggested_items": []}
        if match:
            entry["fills"], entry["conflicts"] = compare(row, match)
        else:
            entry["suggested_items"] = suggest_items(row.get("materials_supplied"),
                                                     materials)
        entry["unchanged"] = bool(match) and not entry["fills"] and not entry["conflicts"]
        out.append(entry)
    return out


def apply_import(cleaned, decisions, approve_new, add_new):
    """Apply the confirmed import.  ``decisions[i]``:
    {"match": supplier id or "new", "use_file": {field, ...},
     "category": str, "items": [material ids]}.

    A matched row updates its supplier straight away.  A new supplier is
    not created here: ``add_new(payload)`` sends it for the Super Admin's
    approval and returns (request, error).
    Returns counts {"sent", "not_sent", "updated", "unchanged"} and the
    reasons a new row was not sent."""
    counts = {"sent": 0, "not_sent": 0, "updated": 0, "unchanged": 0}
    reasons = []
    for i, row in enumerate(cleaned):
        choice = decisions.get(i) or {}
        target = choice.get("match")
        supplier = None
        if target not in (None, "", "new"):
            try:
                supplier = db.get("suppliers", int(target))
            except (TypeError, ValueError):
                supplier = None
        if supplier:
            fills, conflicts = compare(row, supplier)
            changes = {f["field"]: f["value"] for f in fills}
            for c in conflicts:
                if c["field"] in choice.get("use_file", set()):
                    changes[c["field"]] = c["incoming"]
            if changes:
                db.update("suppliers", supplier["id"], changes)
                counts["updated"] += 1
            else:
                counts["unchanged"] += 1
            continue
        category = choice.get("category") or row.get("category") or "Production"
        payload = dict(row, category=category if category in CATEGORIES else "Production",
                       buy_from="Shopee" if row.get("supplier_type") == ONLINE else "",
                       lead_time_days=None, approved=bool(approve_new),
                       material_ids=[int(m) for m in choice.get("items") or []
                                     if str(m).isdigit()])
        _, err = add_new(payload)
        if err:
            counts["not_sent"] += 1
            reasons.append("%s: %s" % (row.get("name"), err))
        else:
            counts["sent"] += 1
    return counts, reasons
