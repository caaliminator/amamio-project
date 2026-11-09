"""Supplier Directory — the client's spreadsheet (No., name, address, mobile
numbers, contact person, items supplied, email, TIN, supplier type) filed
under Raw / Production / Packing / Office.

One supplier per page, added or edited in a short wizard, with the catalog
items it supplies (these narrow the supplier list on purchase requests and
purchase orders), and a printable copy of the whole directory grouped by
category.  An Admin puts a supplier on the Approved Supplier List (SOP
AFP-WH-SOP-001 section 6); only approved suppliers can be ordered from and
received from.  An Admin also imports later versions of the spreadsheet
(upload → preview → confirm).

Only the Admin adds a supplier (by hand or from the spreadsheet), and a new
supplier joins the directory only once the Super Admin approves it
(additions_service).  Editing an existing supplier is unchanged.
"""
import json
import os
import tempfile

from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, url_for)

import additions_service as adds
import db
import services
import supplier_service as sup
import xlsx
from security import current_user, module_required, role_required

suppliers_bp = Blueprint("suppliers", __name__)

CATEGORIES = sup.CATEGORIES
CATEGORY_HINTS = {
    "Raw": "Honey, produce and other raw materials",
    "Production": "Equipment, ingredients and supplies for production",
    "Packing": "Jars, bottles, pouches, labels, boxes, tape",
    "Office": "Paper, ink and office supplies",
}
BUY_FROM = ["Shopee", "Lazada", "TikTok", "Direct", "Other"]
MAX_UPLOAD = 5 * 1024 * 1024


def _norm_category(value):
    for c in CATEGORIES:
        if (value or "").strip().lower() in (c.lower(), c.lower()[:4]):
            return c
    return ""


def _buy_choice(value):
    """Map a stored buy_from onto (chip, other-text)."""
    value = (value or "").strip()
    if not value:
        return "", ""
    for b in BUY_FROM[:-1]:
        if value.lower() == b.lower():
            return b, ""
    return "Other", value


def _contact_line(s):
    """What goes in the paper's Contact# column."""
    parts = [s.get("contact_person"), s.get("phone"), s.get("buy_from")]
    return " · ".join(p for p in parts if p)


def _view(s, everyone=None):
    cat = _norm_category(s.get("category"))
    return dict(s, category=cat or "Other", contact_line=_contact_line(s),
                phones=sup.phone_list(s.get("phone")),
                label=sup.display_name(s, everyone),
                search=" ".join(str(s.get(k) or "") for k in (
                    "name", "tin", "materials_supplied", "contact_person",
                    "phone", "email", "buy_from", "address", "supplier_type",
                    "ref_no")).lower())


def _all():
    everyone = db.read("suppliers")
    return sorted((_view(s, everyone) for s in everyone),
                  key=lambda s: ((s.get("name") or "").lower(),
                                 (s.get("address") or "").lower()))


@suppliers_bp.route("/")
@module_required("suppliers")
def index():
    cat = request.args.get("cat") or "All"
    if cat not in CATEGORIES:
        cat = "All"
    stype = request.args.get("type") or ""
    if stype not in sup.SUPPLIER_TYPES:
        stype = ""
    # Every supplier is on the page (a card table with category and type
    # filters); ?cat= / ?type= / ?q= only preset those filters.
    everyone = _all()
    waiting = [r for r in adds.pending() if r["kind"] == "supplier"]
    return render_template("suppliers/index.html", suppliers=everyone, cat=cat,
                           categories=CATEGORIES,
                           can_add=adds.can_add(current_user()), waiting=waiting,
                           types=sup.SUPPLIER_TYPES, stype=stype,
                           q=request.args.get("q") or "")


def _form_payload(form):
    name = (form.get("name") or "").strip()[:120]
    category = _norm_category(form.get("category"))
    buy = (form.get("buy_from") or "").strip()
    if buy == "Other":
        buy = (form.get("buy_from_other") or "").strip()[:80] or "Other"
    elif buy not in BUY_FROM:
        buy = ""
    try:
        lead = int(form.get("lead_time_days")) if form.get("lead_time_days") else None
        if lead is not None and lead < 0:
            lead = None
    except (TypeError, ValueError):
        lead = None
    payload = {
        "name": name, "category": category,
        "supplier_type": sup.clean_type(form.get("supplier_type")),
        "materials_supplied": (form.get("materials_supplied") or "").strip()[:1000],
        "tin": sup.blank(form.get("tin"))[:30],
        "contact_person": (form.get("contact_person") or "").strip()[:120],
        # One number per line on the form; stored " / "-separated.
        "phone": sup.clean_phone(form.get("phone"))[:120],
        "email": (form.get("email") or "").strip()[:120],
        "address": sup.blank(form.get("address"))[:300],
        "buy_from": buy, "lead_time_days": lead,
        "notes": (form.get("notes") or "").strip()[:500],
    }
    return payload


def _check(payload, supplier_id=None):
    """Return (message, wizard step) or None.  The same name is allowed for
    another branch, i.e. at a different address."""
    if not payload["name"]:
        return "Enter the supplier's name.", 0
    if not payload["category"]:
        return "Choose what the supplier is filed under.", 0
    dup = sup.duplicate_problem(payload["name"], payload["address"], supplier_id)
    if dup:
        return dup, 0
    if payload["email"] and "@" not in payload["email"]:
        return "That email address doesn't look right.", 4
    return None


def _material_groups():
    """Catalog items for the "Which catalog items do they supply?" step."""
    groups = []
    for key, label in services.MATERIAL_CATEGORIES:
        rows = sorted((m for m in db.read("raw_materials")
                       if m.get("category") == key and services.is_purchasable(m)),
                      key=lambda m: (m.get("name") or "").lower())
        if rows:
            groups.append((label, rows))
    return groups


def _picked_items(form):
    return form.getlist("material_ids")


def _wizard(supplier, form, start, title, submit):
    source = form if form else supplier or {}
    chip, other = _buy_choice(source.get("buy_from"))
    if form:
        chip, other = form.get("buy_from") or "", form.get("buy_from_other") or ""
        linked = {int(m) for m in _picked_items(form) if str(m).isdigit()}
    else:
        linked = sup.material_ids_of(supplier["id"]) if supplier and supplier.get("id") else set()
    usual = {m["id"] for m in db.read("raw_materials")
             if supplier and supplier.get("id") and m.get("supplier_id") == supplier["id"]}
    return render_template(
        "suppliers/form.html", supplier=supplier, f=source, start=start,
        categories=CATEGORIES, hints=CATEGORY_HINTS, buy_options=BUY_FROM,
        types=sup.SUPPLIER_TYPES, type_hints=sup.SUPPLIER_TYPE_HINTS,
        buy_chip=chip, buy_other=other, page_title=title, submit_label=submit,
        material_groups=_material_groups(), linked=linked, usual=usual,
        phones="\n".join(sup.phone_list(source.get("phone"))),
        category=_norm_category(source.get("category")) if not form else form.get("category"))


def _admin_adds():
    """403 unless the signed-in user is the Admin (who adds new records)."""
    if not adds.can_add(current_user()):
        abort(403)


@suppliers_bp.route("/new", methods=["GET", "POST"])
@module_required("suppliers")
def new():
    """Add a supplier — sent to the Super Admin, who approves it before it
    joins the directory."""
    _admin_adds()
    g.nav_endpoint = "additions.index"
    start = 0
    if request.method == "POST":
        payload = _form_payload(request.form)
        problem = _check(payload)
        if not problem:
            payload["material_ids"] = sorted(
                {int(m) for m in _picked_items(request.form) if str(m).isdigit()})
            req, err = adds.submit("supplier", payload, current_user())
            if req:
                flash("Supplier '%s' sent to the Super Admin for approval. It joins "
                      "the directory once approved." % payload["name"], "success")
                return redirect(url_for("additions.detail", req_id=req["id"]))
            problem = (err, 0)
        flash(problem[0], "error")
        start = problem[1]
    preset = {"category": request.args.get("cat")} if request.method == "GET" else None
    return _wizard(preset, request.form, start, "Add a supplier", "Send for approval")


@suppliers_bp.route("/<int:supplier_id>")
@module_required("suppliers")
def detail(supplier_id):
    g.nav_endpoint = "suppliers.index"
    s = db.get("suppliers", supplier_id)
    if not s:
        abort(404)
    linked = sup.material_ids_of(supplier_id)
    materials = sorted((m for m in db.read("raw_materials") if m["id"] in linked),
                       key=lambda m: (m.get("name") or "").lower())
    deliveries = [b for b in db.read("receiving_batches")
                  if b.get("supplier_id") == supplier_id]
    deliveries.sort(key=lambda b: b.get("received_date") or "", reverse=True)
    return render_template("suppliers/detail.html", s=_view(s),
                           materials=materials, deliveries=deliveries[:5],
                           delivery_count=len(deliveries),
                           hints=CATEGORY_HINTS,
                           approved_by=services.user_name(s.get("approved_by"))
                           if s.get("approved_by") else "")


@suppliers_bp.route("/<int:supplier_id>/approval", methods=["POST"])
@role_required("admin")
def approval(supplier_id):
    """Add the supplier to, or remove it from, the Approved Supplier List."""
    approve = request.form.get("approved") == "1"
    s, err = services.set_supplier_approval(supplier_id, approve,
                                            current_user()["id"])
    if err:
        abort(404)
    flash("%s is %s the Approved Supplier List." % (
        s["name"], "now on" if approve else "no longer on"), "success")
    return redirect(url_for("suppliers.detail", supplier_id=supplier_id))


@suppliers_bp.route("/<int:supplier_id>/edit", methods=["GET", "POST"])
@module_required("suppliers")
def edit(supplier_id):
    g.nav_endpoint = "suppliers.index"
    s = db.get("suppliers", supplier_id)
    if not s:
        abort(404)
    start = 0
    if request.method == "POST":
        payload = _form_payload(request.form)
        problem = _check(payload, supplier_id)
        if not problem:
            db.update("suppliers", supplier_id, payload)
            sup.set_supplier_items(supplier_id, _picked_items(request.form))
            flash("Supplier '%s' updated." % payload["name"], "success")
            return redirect(url_for("suppliers.detail", supplier_id=supplier_id))
        flash(problem[0], "error")
        start = problem[1]
    return _wizard(s, request.form, start, "Edit %s" % s.get("name"), "Save changes")


@suppliers_bp.route("/print")
@module_required("suppliers")
def print_directory():
    g.nav_endpoint = "suppliers.index"
    everyone = _all()
    groups = []
    for c in CATEGORIES + ["Other"]:
        rows = [s for s in everyone if s["category"] == c]
        if rows:
            groups.append((c, rows))
    return render_template("suppliers/print.html", groups=groups,
                           total=len(everyone), today=services.today_iso())


# --- Import from Excel (Admin) ----------------------------------------------------
def _read_upload(upload):
    """The uploaded workbook's first sheet as rows, or (None, error)."""
    if not upload or not upload.filename:
        return None, "Choose the Suppliers Directory spreadsheet to upload."
    if not upload.filename.lower().endswith(".xlsx"):
        return None, "Only an Excel workbook (.xlsx) can be imported."
    data = upload.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        return None, "That file is too large for a supplier list (5 MB at most)."
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        return xlsx.read_sheet(path, 0), None
    except (xlsx.XlsxError, ValueError, KeyError, OSError) as exc:
        return None, "The file could not be read as a workbook (%s)." % exc
    except Exception:                      # a corrupt zip, bad XML …
        return None, "The file could not be read as a workbook."
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _preview_context(cleaned, problems, filename):
    everyone = db.read("suppliers")
    by_id = {s["id"]: s for s in everyone}
    rows = sup.preview_rows(cleaned)
    for r in rows:
        r["match"] = _view(by_id[r["match_id"]], everyone) if r["match_id"] else None
    options = sorted(({"id": s["id"], "label": sup.display_name(s, everyone)}
                      for s in everyone), key=lambda o: o["label"].lower())
    materials = {m["id"]: m for m in db.read("raw_materials")}
    return dict(rows=rows, problems=problems, filename=filename,
                payload=json.dumps(cleaned), supplier_options=options,
                materials=materials, categories=CATEGORIES,
                counts={"new": sum(1 for r in rows if not r["match_id"]),
                        "changes": sum(1 for r in rows if r["match_id"] and not r["unchanged"]),
                        "same": sum(1 for r in rows if r["unchanged"])})


@suppliers_bp.route("/import", methods=["GET", "POST"])
@module_required("suppliers")
def import_file():
    """Upload the spreadsheet; the preview shows what would change."""
    _admin_adds()
    g.nav_endpoint = "suppliers.index"
    if request.method == "POST":
        upload = request.files.get("workbook")
        rows, err = _read_upload(upload)
        if err is None:
            cleaned, problems = sup.clean_sheet(rows)
            if cleaned:
                return render_template("suppliers/import_preview.html",
                                       **_preview_context(cleaned, problems,
                                                          upload.filename))
            err = problems[0] if problems else "No suppliers were found in that file."
        flash(err, "error")
    return render_template("suppliers/import.html")


@suppliers_bp.route("/import/confirm", methods=["POST"])
@module_required("suppliers")
def import_confirm():
    """Apply the import as confirmed on the preview: matched suppliers are
    updated, new ones are sent to the Super Admin for approval."""
    _admin_adds()
    try:
        cleaned = [sup.sanitize(r) for r in json.loads(request.form.get("payload") or "[]")
                   if isinstance(r, dict)]
    except (TypeError, ValueError):
        cleaned = []
    cleaned = [r for r in cleaned if r.get("name")]
    if not cleaned:
        flash("Nothing to import — upload the spreadsheet again.", "error")
        return redirect(url_for("suppliers.import_file"))
    f = request.form
    decisions = {}
    for i in range(len(cleaned)):
        decisions[i] = {
            "match": f.get("match_%d" % i) or "new",
            "use_file": {k[len("use_%d_" % i):] for k in f
                         if k.startswith("use_%d_" % i) and f.get(k) == "file"},
            "category": _norm_category(f.get("category_%d" % i)),
            "items": f.getlist("items_%d" % i),
        }
    user = current_user()
    counts, reasons = sup.apply_import(
        cleaned, decisions, f.get("approve_new") == "1",
        lambda payload: adds.submit("supplier", payload, user))
    flash("Import done: %d new supplier(s) sent to the Super Admin for approval, "
          "%d updated, %d unchanged." % (counts["sent"], counts["updated"],
                                         counts["unchanged"]), "success")
    for reason in reasons[:5]:
        flash("Not sent — %s" % reason, "error")
    return redirect(url_for("suppliers.index"))
