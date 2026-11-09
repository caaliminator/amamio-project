"""Module 1 — the receiving area, then inspection & cleaning.

Follows SOP AFP-WH-SOP-001 "Receiving of Materials" and its three forms.

Part 1 (Receiving Team Head): a delivery is recorded once, in a short
step-by-step flow — which purchase order it fills (if any), who delivered it
and how, the documents checked, what arrived and in how many boxes.  Against
a PO the supplier and the ordered items come pre-filled, each box stays tied
to its PO line, and shortages / overages are noted.  This is the Material
Receiving Report (AFP-WH-FRM-001).  Every box gets a QR label carrying its
code, item, quantity and delivery details, and the production team is
notified that boxes are waiting.

Part 2 (inspection & cleaning): a box is scanned, cleaned and checked on the
Incoming Material Inspection Checklist (AFP-WH-FRM-002), its content split
into usable / can't be used / damaged, then Accepted, Rejected or put on
Hold.  Only the usable part of an accepted box joins stock.  A box on Hold or
Rejected opens a Nonconforming Material Hold / Rejection Report
(AFP-WH-FRM-003): containment, then the QA disposition, then closure.  The
receipt is closed with the SOP's Quick Control Check.  The printable "Raw
Materials Receiving and Inspection Report" is built from these records.
"""
import os
from datetime import datetime
from urllib.parse import urlparse

from flask import (Blueprint, abort, flash, g, jsonify, redirect, render_template,
                   request, send_file, url_for)

import db
import profiles
import purchasing_service as pur
import services
import uploads
from security import can, current_user, login_required, module_required

receiving_bp = Blueprint("receiving", __name__)


def _int(value):
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _batch_rows(batches):
    rows = []
    for b in sorted(batches, key=lambda x: x.get("created_at", ""), reverse=True):
        rows.append({
            "id": b["id"], "ref_no": b.get("ref_no"),
            "supplier_name": b.get("supplier_name"),
            "po_no": b.get("po_no"), "delivery_ref": b.get("delivery_ref"),
            "received_date": b.get("received_date"),
            "received_by": services.user_name(b.get("received_by")),
            "status": b.get("status"),
            "badge": services.RECEIVING_BADGE.get(b.get("status"), "badge-muted"),
            "box_count": b.get("box_count") or 0,
            "created_at": services.fmt_dt(b.get("created_at")),
        })
    return rows


def _lines_from_form(form):
    """The repeating item cards of the receive wizard -> list of dicts."""
    keys = ("material_id", "qty_per_box", "boxes", "lot_no", "expiry_date",
            "packaging_condition", "temperature_c", "po_line_no")
    columns = {k: form.getlist(k) for k in keys}
    lines = []
    for i in range(len(columns["material_id"])):
        line = {k: (columns[k][i] if i < len(columns[k]) else "") for k in keys}
        if line["material_id"]:
            lines.append(line)
    return lines


def _lane_endpoint(row):
    """The inspection lane page a box belongs to (for the menu and links)."""
    return services.INSPECTION_LANES[row.get("lane") or services.LANE_RAW]["endpoint"]


def _checklist_from_form(form):
    """The inspection checklist radios + remarks -> {point: {result, remarks}}
    (points of either lane; the service keeps those of the box's lane)."""
    return {key: {"result": form.get("chk_" + key) or "",
                  "remarks": form.get("chk_%s_remarks" % key) or ""}
            for key, _, _ in services.INSPECTION_POINTS + services.SUPPLY_INSPECTION_POINTS}


def _ncr_or_404(ncr_id):
    user = current_user()
    if not (can(user, "receiving") or can(user, "inspection")):
        abort(403)
    ncr = db.get("nonconformance_reports", ncr_id)
    if not ncr:
        abort(404)
    return ncr


# --- Part 1: receiving --------------------------------------------------------
@receiving_bp.route("/")
@module_required("receiving")
def index():
    # Every delivery is listed; the page shows a tab per status.
    batches = db.read("receiving_batches")
    still_open = (services.RECEIVING_OPEN, services.RECEIVING_FOR_CLOSING)
    listed = batches
    show = "all"
    boxes = db.read("receiving_boxes")
    stats = {
        "open": len([b for b in batches if b.get("status") in still_open]),
        "ncr_open": len(services.ncr_rows("open")),
        "awaiting": len([b for b in boxes if services.box_status(b)
                         == services.BOX_AWAITING]),
        "today": len([b for b in batches
                      if b.get("received_date") == services.today_iso()]),
    }
    return render_template("receiving/index.html", batches=_batch_rows(listed),
                           stats=stats, show=show,
                           expected=pur.open_pos_for_receiving())


@receiving_bp.route("/new", methods=["GET", "POST"])
@module_required("receiving")
def new():
    """Which purchase order is this? (skipped when none are open) → the
    delivery wizard, pre-filled from the PO when there is one."""
    form = request.form
    po_arg = _po_arg()
    open_pos = pur.open_pos_for_receiving()
    if po_arg is None and open_pos and request.method == "GET":
        return render_template("receiving/choose_po.html", orders=open_pos)
    po = None
    if po_arg and po_arg != "none":
        po = db.get("purchase_orders", _int(po_arg))
        if not po or po.get("status") != pur.PO_APPROVED:
            flash("That purchase order isn't open for receiving.", "error")
            return redirect(url_for("receiving.new"))

    if request.method == "POST":
        # The DR / OR / AR files are checked first, so a bad file saves nothing.
        doc_files, err = _read_doc_files(request.files)
        if not err:
            batch, boxes, err = _receive(form, po)
        if not err:
            for key, att in doc_files.items():
                _save_doc(batch, key, att)
            honey = services.delivery_honey(batch["id"])
            parts = []
            if boxes:
                parts.append("%d box(es)" % len(boxes))
            if honey:
                parts.append("%d honey container(s)" % len(honey))
            flash("Delivery %s recorded — %s labelled. Print the QR labels and "
                  "stick one on each.%s" % (
                      batch.get("ref_no"), " and ".join(parts),
                      " The honey goes to Honey Inspection for its moisture check; "
                      "its labels print from the delivery's page." if honey and boxes else ""),
                  "success")
            if not boxes and honey:
                return redirect(url_for("honey.labels", fresh=1,
                                        only=",".join(str(c["id"]) for c in honey)))
            return redirect(url_for("receiving.labels", batch_id=batch["id"],
                                    fresh=1))
        flash(err, "error")

    return _new_form(form, po)


# The delivery's documents: (key, label, number field, file field on the
# record, file input on the receive form).  Each file is a picture or a PDF.
DELIVERY_DOCS = [
    ("dr", "Delivery receipt (DR)", "delivery_ref", "proof", "proof"),
    ("or", "Official receipt (OR)", "or_no", "or_proof", "or_file"),
    ("ar", "Acknowledgement receipt (AR)", "ar_no", "ar_proof", "ar_file"),
]
DOC_BY_KEY = {d[0]: d for d in DELIVERY_DOCS}


def _po_arg():
    """The PO being received: picked on the form by typing its number (the
    hidden "po" field), else from the address (?po=…, "none" for none)."""
    if request.method == "POST" and (request.form.get("po") or "").strip():
        return request.form.get("po").strip()
    return request.args.get("po")


def _read_doc_files(files):
    """The DR / OR / AR files chosen on a form.  Returns ({key: attachment},
    error); a bad file saves nothing."""
    out = {}
    for key, label, _, _, field in DELIVERY_DOCS:
        att, err = uploads.read_attachment(files.get(field))
        if err:
            return None, "%s file: %s" % (label, err)
        if att:
            out[key] = att
    return out, None


def _save_doc(batch, key, att):
    """Store (or replace) one delivery document's picture / PDF."""
    _, _, _, field, _ = DOC_BY_KEY[key]
    old = uploads.picture_path("deliveries", batch.get(field))
    db.update("receiving_batches", batch["id"], {
        field: uploads.save_attachment("deliveries", "delivery-%d-%s" % (batch["id"], key), att)})
    if old:
        try:
            os.remove(old)
        except OSError:
            pass


def _delivery_fields(form, po):
    """The receive form's fields, as receive_delivery / preview_delivery
    take them."""
    return dict(
        supplier_id=_int(form.get("supplier_id")),
        received_date=form.get("received_date") or services.today_iso(),
        lines=_lines_from_form(form), po_no=form.get("po_no") or "",
        delivery_ref=form.get("delivery_ref") or "",
        or_no=form.get("or_no") or "", ar_no=form.get("ar_no") or "",
        remarks=form.get("remarks") or "", po_id=po["id"] if po else None,
        received_time=form.get("received_time") or "",
        delivered_by=form.get("delivered_by") or "",
        vehicle_plate=form.get("vehicle_plate") or "",
        receiving_area=form.get("receiving_area") or "",
        docs_checked=form.getlist("docs_checked"),
        docs_other=form.get("docs_other") or "")


def _receive(form, po):
    """Save the delivery from the wizard's fields."""
    return services.receive_delivery(current_user()["id"], **_delivery_fields(form, po))


@receiving_bp.route("/new/preview", methods=["POST"])
@module_required("receiving")
def new_preview():
    """The form's last step: the Material Receiving Report this delivery
    makes, built from the fields so far (nothing is saved)."""
    po = None
    if _po_arg() not in (None, "", "none"):
        po = db.get("purchase_orders", _int(_po_arg()))
    batch, lines, err = services.preview_delivery(current_user(), **_delivery_fields(request.form, po))
    if err:
        return render_template("receiving/_preview_error.html", error=err), 422
    return render_template("receiving/_mrr_document.html", batch=batch, lines=lines,
                           received_by=current_user().get("name") or "", inventory_by="",
                           docs=services.DOCS_CHECKED, preview=True,
                           doc_names=[(label, request.form.get(field + "_name"))
                                      for _, label, _, _, field in DELIVERY_DOCS
                                      if request.form.get(field + "_name")])


def _po_not_receivable():
    """{PO no.: why it can't be received} for the POs not open for
    receiving — said on the form when one of them is typed."""
    open_ids = {o["id"] for o in pur.open_pos_for_receiving()}
    out = {}
    for p in db.read("purchase_orders"):
        if p["id"] in open_ids or not p.get("po_no"):
            continue
        status = p.get("status")
        if status == pur.PO_PREPARED:
            why = ("isn't approved yet — it waits for the Admin's check, then the Super "
                   "Admin's final approval. It can be received once it is approved.")
        elif status == pur.PO_FOR_FINAL:
            why = ("isn't approved yet — it waits for the Super Admin's final approval. "
                   "It can be received once it is approved.")
        elif status == pur.PO_CANCELLED:
            why = "was cancelled — nothing can be received against it."
        elif status == pur.PO_DELIVERED:
            why = "is already delivered — everything on it was received."
        else:
            refs = [b.get("ref_no") for b in db.query("receiving_batches", po_id=p["id"]) if b.get("ref_no")]
            why = ("has nothing left to receive — everything on it is already in "
                   + (", ".join(refs) if refs else "an earlier delivery")
                   + " (received or still being inspected).")
        out[p["po_no"].strip().lower()] = "%s %s" % (p["po_no"], why)
    return out


def _new_form(form, po):
    return render_template(
        "receiving/new.html", delivery_docs=DELIVERY_DOCS,
        suppliers=sorted(db.read("suppliers"),
                                               key=lambda s: s.get("name") or ""),
        materials=services.purchasable_materials(),
        categories=services.MATERIAL_CATEGORIES,
        category_of=services.material_category,
        track_labels=services.TRACK_MODE_LABELS,
        conditions=services.PACKAGING_CONDITIONS,
        is_honey=services.is_honey_material,
        open_pos=[] if po else pur.open_pos_for_receiving(),
        po_options=[] if po else [
            {"v": o["id"], "l": o["po_no"] or "", "s": "%s · %s" % (o["supplier_name"] or "—", o["summary"])}
            for o in pur.open_pos_for_receiving()],
        po_closed={} if po else _po_not_receivable(),
        docs=services.DOCS_CHECKED,
        now_time=datetime.now().strftime("%H:%M"),
        today=services.today_iso(), form=form, po=po,
        po_lines=[l for l in pur.lines_to_receive(po) if l["remaining"] > 0] if po else [],
        lines=_lines_from_form(form) if request.method == "POST" else [],
        start=1 if request.method == "POST" else 0)


@receiving_bp.route("/new/po/<int:po_id>")
@module_required("receiving")
def new_po_lines(po_id):
    """A PO typed into the receive form: its supplier and its items still to
    come, as the form's item cards (honey as containers)."""
    po = db.get("purchase_orders", po_id)
    if not po or po.get("status") != pur.PO_APPROVED:
        return jsonify({"error": "That purchase order isn't open for receiving."}), 404
    lines = [l for l in pur.lines_to_receive(po) if l["remaining"] > 0]
    if not lines:
        return jsonify({"error": "Everything on %s has already been received." % po.get("po_no")}), 404
    html = render_template(
        "receiving/_po_lines.html", po_lines=lines, submitted={},
        materials=services.purchasable_materials(),
        categories=services.MATERIAL_CATEGORIES,
        category_of=services.material_category,
        track_labels=services.TRACK_MODE_LABELS,
        conditions=services.PACKAGING_CONDITIONS,
        is_honey=services.is_honey_material)
    return jsonify({"id": po["id"], "po_no": po.get("po_no"),
                    "supplier_id": po.get("supplier_id"),
                    "supplier_name": po.get("supplier_name") or "",
                    "html": html})


@receiving_bp.route("/<int:batch_id>")
@module_required("receiving")
def batch(batch_id):
    record = db.get("receiving_batches", batch_id)
    if not record:
        abort(404)
    boxes = services.receiving_box_rows(batch_id=batch_id)
    po = db.get("purchase_orders", record.get("po_id")) if record.get("po_id") else None
    return render_template(
        "receiving/batch.html", batch=record, boxes=boxes, po=po,
        delivery_docs=DELIVERY_DOCS, has_doc=services.has_delivery_document(record),
        honey=services.delivery_honey(batch_id),
        badge=services.RECEIVING_BADGE.get(record.get("status"), "badge-muted"),
        received_by=services.user_name(record.get("received_by")),
        awaiting=[b for b in boxes if b["status"] == services.BOX_AWAITING],
        open_ncrs=[b for b in boxes
                   if b["ncr_id"] and b["ncr_status"] != services.NCR_CLOSED])


@receiving_bp.route("/<int:batch_id>/mrr")
@module_required("receiving")
def mrr(batch_id):
    """The Material Receiving Report (AFP-WH-FRM-001), filled in."""
    record = db.get("receiving_batches", batch_id)
    if not record:
        abort(404)
    return render_template(
        "receiving/mrr.html", batch=record, lines=services.mrr_lines(record),
        received_by=services.user_name(record.get("received_by")),
        inventory_by=services.user_name(record.get("inventory_updated_by"))
        if record.get("inventory_updated_by") else "",
        docs=services.DOCS_CHECKED)


@receiving_bp.route("/<int:batch_id>/proof", methods=["GET", "POST"])
@login_required
def delivery_proof(batch_id):
    """A delivery document's picture / PDF (?doc=dr|or|ar, DR by default):
    anyone on receiving or inspection can view it; the receiving team can add
    or replace it, and fill in its number if it was left blank."""
    user = current_user()
    record = db.get("receiving_batches", batch_id)
    if not record:
        abort(404)
    key = request.values.get("doc") or "dr"
    if key not in DOC_BY_KEY:
        abort(404)
    _, label, number_field, file_field, _ = DOC_BY_KEY[key]
    if request.method == "POST":
        if not can(user, "receiving"):
            abort(403)
        att, err = uploads.read_attachment(request.files.get("proof"))
        number = (request.form.get("number") or "").strip()[:40]
        if err:
            flash("%s file: %s" % (label, err), "error")
        elif not att and not number:
            flash("Enter the %s no. or choose its file." % label, "error")
        else:
            if number:
                db.update("receiving_batches", batch_id, {number_field: number})
            if att:
                _save_doc(record, key, att)
            flash("%s saved." % label, "success")
        return redirect(url_for("receiving.batch", batch_id=batch_id))
    if not (can(user, "receiving") or can(user, "inspection")):
        abort(403)
    path = uploads.picture_path("deliveries", record.get(file_field))
    if not path:
        abort(404)
    is_pdf = path.lower().endswith(".pdf")
    return send_file(path, max_age=3600,
                     mimetype="application/pdf" if is_pdf else "image/jpeg")


@receiving_bp.route("/<int:batch_id>/add", methods=["GET", "POST"])
@module_required("receiving")
def add_boxes(batch_id):
    """Add boxes that were missed to a delivery that is still open."""
    record = db.get("receiving_batches", batch_id)
    if not record:
        abort(404)
    if request.method == "POST":
        boxes, err = services.add_receiving_box(
            batch_id, request.form.get("material_id"),
            request.form.get("qty_per_box"), current_user()["id"],
            count=request.form.get("boxes") or 1,
            lot_no=request.form.get("lot_no") or "",
            expiry_date=request.form.get("expiry_date") or "",
            packaging_condition=request.form.get("packaging_condition") or "",
            temperature_c=request.form.get("temperature_c"))
        if err:
            flash(err, "error")
            return redirect(url_for("receiving.add_boxes", batch_id=batch_id))
        flash("%d box(es) added to %s." % (len(boxes), record.get("ref_no")),
              "success")
        return redirect(url_for("receiving.labels", batch_id=batch_id,
                                only=",".join(str(b["id"]) for b in boxes)))
    return render_template(
        "receiving/add.html", batch=record,
        materials=[m for m in services.purchasable_materials()
                   if not services.is_honey_material(m)],
        categories=services.MATERIAL_CATEGORIES,
        category_of=services.material_category,
        track_labels=services.TRACK_MODE_LABELS,
        conditions=services.PACKAGING_CONDITIONS)


@receiving_bp.route("/<int:batch_id>/labels")
@module_required("receiving")
def labels(batch_id):
    record = db.get("receiving_batches", batch_id)
    if not record:
        abort(404)
    boxes = services.receiving_box_rows(batch_id=batch_id)
    only = request.args.get("only")
    if only:
        wanted = {_int(x) for x in only.split(",")}
        boxes = [b for b in boxes if b["id"] in wanted]
    return render_template("receiving/labels.html", batch=record, boxes=boxes,
                           fresh=request.args.get("fresh"))


@receiving_bp.route("/<int:batch_id>/status-labels")
@module_required("receiving")
def status_labels(batch_id):
    """SOP 5.7: identification / status labels — ACCEPTED, HOLD or REJECTED."""
    record = db.get("receiving_batches", batch_id)
    if not record:
        abort(404)
    boxes = [b for b in services.receiving_box_rows(batch_id=batch_id)
             if b["status"] != services.BOX_AWAITING]
    only = request.args.get("only")
    if only:
        wanted = {_int(x) for x in only.split(",")}
        boxes = [b for b in boxes if b["id"] in wanted]
    return render_template("receiving/status_labels.html", batch=record,
                           boxes=boxes)


@receiving_bp.route("/<int:batch_id>/close", methods=["GET", "POST"])
@module_required("receiving")
def close(batch_id):
    """Close the receipt with the SOP's Quick Control Check."""
    record = db.get("receiving_batches", batch_id)
    if not record:
        abort(404)
    g.nav_endpoint = "receiving.index"
    form = request.form
    if request.method == "POST":
        record, err = services.close_receiving_batch(
            batch_id, current_user()["id"],
            checks={key: form.get("check_" + key)
                    for key, _ in services.CONTROL_CHECKS},
            verified_by_name=form.get("verified_by_name") or "",
            qa_approved_by_name=profiles.strip_position(form.get("qa_approved_by_name")))
        if not err:
            flash("Receipt %s closed." % record.get("ref_no"), "success")
            return redirect(url_for("receiving.batch", batch_id=batch_id))
        flash(err, "error")
        record = db.get("receiving_batches", batch_id)
    boxes = services.receiving_box_rows(batch_id=batch_id)
    counts = {s: sum(1 for b in boxes if b["status"] == s)
              for s in (services.BOX_ACCEPTED, services.BOX_HOLD, services.BOX_REJECTED)}
    return render_template(
        "receiving/close.html", batch=record, form=form, boxes=boxes, counts=counts,
        has_doc=services.has_delivery_document(record),
        checks=services.CONTROL_CHECKS, answers=services.CONTROL_ANSWERS,
        awaiting=[b for b in boxes if b["status"] == services.BOX_AWAITING],
        honey_unchecked=[c for c in services.delivery_honey(batch_id)
                         if c.get("status") not in services.QC_RESULTS],
        held=[b for b in boxes if b["status"] == services.BOX_HOLD])


# --- One box -----------------------------------------------------------------
def _box_or_404(box_id):
    user = current_user()
    if not (can(user, "receiving") or can(user, "inspection")):
        abort(403)
    rows = [r for r in services.receiving_box_rows()
            if r["id"] == int(box_id)]
    if not rows:
        abort(404)
    return rows[0]


def _back_batch(row):
    """The delivery page to return to after an inspection: ?back=<id> (the
    delivery page's links, the inspection form's hidden field), else the
    delivery page this was opened from.  Only the box's own delivery counts."""
    wanted = _int(request.values.get("back"))
    if wanted is None and request.referrer:
        own = url_for("receiving.batch", batch_id=row["batch_id"])
        if urlparse(request.referrer).path.rstrip("/") == own.rstrip("/"):
            wanted = row["batch_id"]
    return row["batch_id"] if wanted == row["batch_id"] else None


@receiving_bp.route("/box/<int:box_id>")
def box(box_id):
    row = _box_or_404(box_id)
    g.nav_endpoint = (_lane_endpoint(row)
                      if not can(current_user(), "receiving")
                      else "receiving.index")
    record = db.get("receiving_boxes", box_id)
    first = (services.fefo_first(record)
             if row["status"] == services.BOX_ACCEPTED
             and row["stage"] == "Storage Area" else None)
    return render_template(
        "receiving/box.html", box=row, track=services.box_track(record),
        qr_text=services.box_qr_text(record),
        stages=services.BOX_STAGES[1:],
        failed=services.failed_points(record),
        fefo_first=first, back=_back_batch(row),
        can_inspect=can(current_user(), "inspection"))


@receiving_bp.route("/box/<int:box_id>/checklist")
def checklist(box_id):
    """The Incoming Material Inspection Checklist (AFP-WH-FRM-002), filled in."""
    row = _box_or_404(box_id)
    if row["status"] == services.BOX_AWAITING:
        flash("Box %s hasn't been inspected yet." % row["box_code"], "error")
        return redirect(url_for("receiving.box", box_id=box_id))
    return render_template("receiving/checklist.html", box=row,
                           points=services.points_for_box(row),
                           lane=services.INSPECTION_LANES[row["lane"]])


@receiving_bp.route("/box/<int:box_id>/ncr")
def box_ncr(box_id):
    """Open the hold / rejection report of a box (opening one for a box held
    or rejected before these reports existed)."""
    row = _box_or_404(box_id)
    ncr = services.ncr_for_box(box_id)
    if not ncr:
        flash("Box %s is not on hold or rejected." % row["box_code"], "error")
        return redirect(url_for("receiving.box", box_id=box_id))
    return redirect(url_for("receiving.ncr", ncr_id=ncr["id"]))


@receiving_bp.route("/box/<int:box_id>/move", methods=["POST"])
def move_box(box_id):
    _box_or_404(box_id)
    box, err = services.move_receiving_box(
        box_id, request.form.get("stage") or "",
        user_id=current_user()["id"],
        released_to=request.form.get("released_to") or "")
    if err:
        flash(err, "error")
    else:
        flash("Box %s is now in the %s." % (box.get("box_code"),
                                            box.get("stage")), "success")
    return redirect(url_for("receiving.box", box_id=box_id))


# --- Part 2: inspection & cleaning -------------------------------------------
@receiving_bp.route("/inspection")
@module_required("inspection")
def inspection():
    """Raw Material Inspection: ginger, lemon, calamansi, cacao, coffee …"""
    return _lane_page(services.LANE_RAW)


@receiving_bp.route("/inspection/supplies")
@module_required("inspection")
def inspection_supplies():
    """Packaging & Supplies Check: jars, bottles, seals, pouches, labels,
    boxes; cleaning, production and office supplies."""
    return _lane_page(services.LANE_SUPPLIES)


def _batch_dates(row):
    """(mfg, expiry) to start the date pickers with: the box's own, else
    those already recorded on another box of the same batch."""
    mfg, exp = row.get("mfg_date") or "", row.get("expiry_date") or ""
    if not (mfg and exp) and row.get("lot_no"):
        for b in db.query("receiving_boxes", lot_no=row["lot_no"]):
            mfg = mfg or b.get("mfg_date") or ""
            exp = exp or b.get("expiry_date") or ""
    return mfg, exp


@receiving_bp.route("/batches")
@module_required("inventory")
def batches():
    """Batches & Expiry: every batch no., what is left of it and when it
    expires — the expired and expiring ones first to see."""
    show = request.args.get("show") or "stock"
    rows = services.batch_rows()
    counts = {"expiring": sum(1 for r in rows if r["status"] in ("Expired", "Expiring soon")),
              "stock": sum(1 for r in rows if r["remaining"] > 0.0005 or r["waiting"]),
              "all": len(rows)}
    if show == "expiring":
        rows = sorted((r for r in rows if r["status"] in ("Expired", "Expiring soon")),
                      key=lambda r: r["expiry_date"])
    elif show == "stock":
        rows = [r for r in rows if r["remaining"] > 0.0005 or r["waiting"]]
    else:
        show = "all"
    q = (request.args.get("q") or "").strip()
    return render_template("receiving/batches.html", rows=rows, show=show,
                           counts=counts, q=q, soon=services.EXPIRING_SOON_DAYS)


def _lane_page(lane):
    rows = [r for r in services.receiving_box_rows(
        status=(services.BOX_AWAITING, services.BOX_HOLD, services.BOX_ACCEPTED,
                services.BOX_REJECTED))]
    mine = [r for r in rows if r["lane"] == lane]
    waiting = [r for r in mine if r["status"] == services.BOX_AWAITING]
    held = [r for r in mine if r["status"] == services.BOX_HOLD]
    done = [r for r in mine if r["status"] in (services.BOX_ACCEPTED, services.BOX_REJECTED)
            and r["checked_at"]]
    done.sort(key=lambda r: r["checked_at"], reverse=True)
    other = services.LANE_SUPPLIES if lane == services.LANE_RAW else services.LANE_RAW
    other_waiting = sum(1 for r in rows if r["lane"] == other
                        and r["status"] in (services.BOX_AWAITING, services.BOX_HOLD))
    return render_template("receiving/inspection.html", waiting=waiting,
                           held=held, done=done, lane_key=lane,
                           lane=services.INSPECTION_LANES[lane],
                           other=services.INSPECTION_LANES[other],
                           other_waiting=other_waiting)


@receiving_bp.route("/box/<int:box_id>/inspect", methods=["GET", "POST"])
@module_required("inspection")
def inspect(box_id):
    row = _box_or_404(box_id)
    g.nav_endpoint = _lane_endpoint(row)
    back = _back_batch(row)
    if row["status"] != services.BOX_AWAITING:
        flash("Box %s was already inspected." % row["box_code"], "error")
        if back:
            return redirect(url_for("receiving.batch", batch_id=back))
        return redirect(url_for("receiving.box", box_id=box_id))
    form = request.form
    if request.method == "POST":
        # The photos are checked first, so a bad file saves nothing.
        pictures, err = _read_photos(request.files.getlist("photos"))
        if not err:
            box, err = services.inspect_receiving_box(
                box_id, current_user()["id"],
                usable_qty=form.get("usable_qty"),
                unusable_qty=form.get("unusable_qty") or 0,
                damaged_qty=form.get("damaged_qty") or 0,
                unusable_reason=form.get("unusable_reason") or "",
                damage_reason=form.get("damage_reason") or "",
                cleaned=form.get("cleaned") == "1",
                acceptance=form.get("acceptance") or "",
                nonconformity=form.get("nonconformity") or "",
                action_taken=form.get("action_taken") or "",
                verified_by_name=form.get("verified_by_name") or "",
                checklist=_checklist_from_form(form),
                test_parameter=form.get("test_parameter") or "",
                test_spec=form.get("test_spec") or "",
                test_result=form.get("test_result") or "",
                test_method=form.get("test_method") or "",
                nc_types=form.getlist("nc_types"),
                nc_other=form.get("nc_other") or "",
                visual_description=form.get("visual_description") or "",
                mfg_date=form.get("mfg_date") or "",
                expiry_date=form.get("expiry_date") or "")
        if not err:
            if pictures:
                _add_photos(box_id, pictures)
            flash("Box %s inspected — %s." % (box.get("box_code"),
                                              box.get("acceptance_status")),
                  "success" if box.get("acceptance_status") == "Accepted"
                  else "error")
            if back:
                # Started from the delivery page: the next box of this
                # delivery, else back to the delivery.
                nxt = services.receiving_box_rows(batch_id=back, status=services.BOX_AWAITING)
                if nxt and request.form.get("then") == "next":
                    return redirect(url_for("receiving.inspect", box_id=nxt[0]["id"], back=back))
                return redirect(url_for("receiving.batch", batch_id=back))
            nxt = [r for r in services.receiving_box_rows(status=services.BOX_AWAITING)
                   if r["lane"] == row["lane"]]
            if nxt and request.form.get("then") == "next":
                return redirect(url_for("receiving.inspect", box_id=nxt[0]["id"]))
            return redirect(url_for("receiving.box", box_id=box_id))
        flash(err, "error")

    return render_template(
        "receiving/inspect.html", box=row, form=form,
        points=services.points_for_box(row), check_results=services.CHECK_RESULTS,
        point_types=services.NC_TYPE_FOR_POINT, nc_types=services.NC_TYPES,
        lane_key=row["lane"], lane=services.INSPECTION_LANES[row["lane"]],
        batch_dates=_batch_dates(row), back=back, today=services.today_iso(),
        waiting_count=len(services.receiving_box_rows(batch_id=back, status=services.BOX_AWAITING))
        if back else len([r for r in services.receiving_box_rows(
            status=services.BOX_AWAITING) if r["lane"] == row["lane"]]),
        start=1 if request.method == "POST" else 0)


@receiving_bp.route("/box/<int:box_id>/inspect/preview", methods=["POST"])
@module_required("inspection")
def inspect_preview(box_id):
    """The inspection's last step: the Incoming Material Inspection Checklist
    this inspection makes, from the answers so far (nothing is saved)."""
    row = _box_or_404(box_id)
    form = request.form
    num = lambda name: services._to_float(form.get(name))
    box = dict(row,
               inspection_no=row.get("inspection_no")
               or services._next_ref("INS", "receiving_boxes", "inspection_no"),
               checked_by=current_user().get("name") or "",
               checked_at=services.today_iso(),
               checklist=_checklist_from_form(form),
               visual_description=form.get("visual_description") or "",
               mfg_date=form.get("mfg_date") or "",
               expiry_date=form.get("expiry_date") or row.get("expiry_date") or "",
               test_parameter=form.get("test_parameter") or "",
               test_spec=form.get("test_spec") or "",
               test_result=form.get("test_result") or "",
               test_method=form.get("test_method") or "",
               acceptance_status=form.get("acceptance") or "",
               nonconformity=form.get("nonconformity") or "",
               unusable_reason=form.get("unusable_reason") or "",
               damage_reason=form.get("damage_reason") or "",
               good_qty=num("usable_qty"), unusable_qty=num("unusable_qty"),
               damaged_qty=num("damaged_qty"),
               verified_by_name=profiles.strip_position(form.get("verified_by_name")))
    # Hold / Rejected: the hold / rejection report this will open, too.
    ncr = None
    held = {"Hold": services.BOX_HOLD, "Rejected": services.BOX_REJECTED}.get(box["acceptance_status"])
    if held:
        ncr = services.ncr_row(services.raise_ncr(
            dict(box, status=held), current_user()["id"], form.getlist("nc_types"),
            form.get("nc_other") or "", save=False))
    box["photo_names"] = request.form.getlist("photo_names")
    return render_template("receiving/_inspect_preview.html", box=box, ncr=ncr,
                           points=services.points_for_box(row), preview=True,
                           lane=services.INSPECTION_LANES[row["lane"]],
                           nc_types=services.NC_TYPES, containment=services.NC_CONTAINMENT,
                           dispositions=services.NC_DISPOSITIONS)


MAX_PHOTOS = 6


def _read_photos(files):
    """Check the inspection photos chosen.  Returns (pictures, error)."""
    files = [f for f in files if f and f.filename]
    if len(files) > MAX_PHOTOS:
        return [], "Choose up to %d photos." % MAX_PHOTOS
    pictures = []
    for n, f in enumerate(files, 1):
        img, err = uploads.read_picture(f)
        if err:
            return [], "Photo %d (%s): %s" % (n, f.filename, err)
        pictures.append(img)
    return pictures, None


def _add_photos(box_id, pictures):
    """Store inspection photos on a box, after the ones it already has."""
    box = db.get("receiving_boxes", box_id)
    names = list(box.get("photos") or [])
    for img in pictures:
        names.append(uploads.save_picture("inspections", "box-%d" % box_id, img))
    db.update("receiving_boxes", box_id, {"photos": names})


@receiving_bp.route("/box/<int:box_id>/photos/<int:n>")
@login_required
def box_photo(box_id, n):
    """One of a box's inspection photos — receiving and inspection only."""
    user = current_user()
    if not (can(user, "receiving") or can(user, "inspection")):
        abort(403)
    photos = (db.get("receiving_boxes", box_id) or {}).get("photos") or []
    path = uploads.picture_path("inspections", photos[n] if 0 <= n < len(photos) else "")
    if not path:
        abort(404)
    return send_file(path, max_age=3600)


@receiving_bp.route("/box/<int:box_id>/photos", methods=["POST"])
@login_required
def box_add_photos(box_id):
    """Add photos to a box after its inspection."""
    user = current_user()
    if not (can(user, "receiving") or can(user, "inspection")):
        abort(403)
    box = db.get("receiving_boxes", box_id)
    if not box:
        abort(404)
    room = MAX_PHOTOS - len(box.get("photos") or [])
    files = [f for f in request.files.getlist("photos") if f and f.filename]
    if not files:
        flash("Choose the photos to add.", "error")
    elif len(files) > room:
        flash("A box keeps up to %d photos — there is room for %d more." % (MAX_PHOTOS, max(room, 0)), "error")
    else:
        pictures, err = _read_photos(files)
        if err:
            flash(err, "error")
        else:
            _add_photos(box_id, pictures)
            flash("%d photo(s) added to %s." % (len(pictures), box.get("box_code")), "success")
    return redirect(url_for("receiving.box", box_id=box_id) + "#photos")


# --- Nonconforming Material Hold / Rejection Report (AFP-WH-FRM-003) ---------
@receiving_bp.route("/ncr")
def ncr_list():
    user = current_user()
    if not (can(user, "receiving") or can(user, "inspection")):
        abort(403)
    # Every report; the page's tabs split them by status (an old ?show=
    # link still works — it is simply ignored).
    return render_template("receiving/ncr_list.html", rows=services.ncr_rows())


@receiving_bp.route("/ncr/<int:ncr_id>")
def ncr(ncr_id):
    record = _ncr_or_404(ncr_id)
    g.nav_endpoint = "receiving.ncr_list"
    box = db.get("receiving_boxes", record.get("box_id")) or {}
    me = current_user()
    return render_template(
        "receiving/ncr.html", ncr=services.ncr_row(record),
        box_status=services.box_status(box) if box else "",
        good_qty=box.get("good_qty"),
        containment=services.NC_CONTAINMENT,
        dispositions=services.NC_DISPOSITIONS,
        needs_justification=services.NC_NEEDS_JUSTIFICATION,
        can_decide=can(me, "inspection"), me_name=me.get("name") or "")


@receiving_bp.route("/ncr/<int:ncr_id>/contain", methods=["POST"])
def ncr_contain(ncr_id):
    _ncr_or_404(ncr_id)
    form = request.form
    record, err = services.record_ncr_containment(
        ncr_id, current_user()["id"], form.getlist("containment"),
        hold_location=form.get("hold_location") or "",
        qty_segregated=form.get("qty_segregated"),
        tag_no=form.get("tag_no") or "")
    if err:
        flash(err, "error")
    else:
        flash("Containment recorded on %s." % record.get("report_no"), "success")
    return redirect(url_for("receiving.ncr", ncr_id=ncr_id))


@receiving_bp.route("/ncr/<int:ncr_id>/dispose", methods=["POST"])
@module_required("inspection")
def ncr_dispose(ncr_id):
    _ncr_or_404(ncr_id)
    form = request.form
    record, err = services.decide_ncr(
        ncr_id, current_user()["id"], form.get("disposition") or "",
        details=form.get("disposition_details") or "",
        justification=form.get("justification") or "",
        supplier_response=form.get("supplier_response") or "",
        accepted_qty=form.get("accepted_qty"),
        qa_approved_by_name=profiles.strip_position(form.get("qa_approved_by_name")))
    if err:
        flash(err, "error")
    else:
        flash("%s: %s." % (record.get("report_no"),
                           services.NC_DISPOSITION_LABELS[record["disposition"]]),
              "success")
    return redirect(url_for("receiving.ncr", ncr_id=ncr_id))


@receiving_bp.route("/ncr/<int:ncr_id>/close", methods=["POST"])
def ncr_close(ncr_id):
    _ncr_or_404(ncr_id)
    record, err = services.close_ncr(
        ncr_id, current_user()["id"],
        profiles.strip_position(request.form.get("closure_verified_by_name")),
        supplier_response=request.form.get("supplier_response") or "")
    if err:
        flash(err, "error")
    else:
        flash("%s closed." % record.get("report_no"), "success")
    return redirect(url_for("receiving.ncr", ncr_id=ncr_id))


@receiving_bp.route("/ncr/<int:ncr_id>/print")
def ncr_print(ncr_id):
    record = _ncr_or_404(ncr_id)
    return render_template(
        "receiving/ncr_print.html", ncr=services.ncr_row(record),
        nc_types=services.NC_TYPES, containment=services.NC_CONTAINMENT,
        dispositions=services.NC_DISPOSITIONS)


# --- Reports -------------------------------------------------------------------
@receiving_bp.route("/report")
def report():
    """The paper Raw Materials Receiving and Inspection Report, filled in."""
    user = current_user()
    if not (can(user, "receiving") or can(user, "inspection")):
        abort(403)
    date_from = request.args.get("from") or services.month_start_iso()
    date_to = request.args.get("to") or services.today_iso()
    return render_template(
        "receiving/report.html",
        rows=services.receiving_report_rows(date_from, date_to),
        date_from=date_from, date_to=date_to,
        doc_no=services.RIR_DOC_NO, revision=services.RIR_REVISION,
        effective=services.RIR_EFFECTIVE_DATE)


@receiving_bp.route("/report/line")
def report_line():
    """One line of the report — an item and lot of a delivery — in detail,
    as a document: what arrived, how it was inspected, box by box."""
    user = current_user()
    if not (can(user, "receiving") or can(user, "inspection")):
        abort(403)
    row = services.receiving_report_line(
        _int(request.args.get("batch")), _int(request.args.get("material")),
        request.args.get("lot") or "", request.args.get("expiry") or "")
    if not row:
        abort(404)
    g.nav_endpoint = "receiving.report"
    # Back to the report with the same dates — only ever a report address.
    back = request.args.get("back") or ""
    if not back.startswith("/receiving/report?"):
        back = url_for("receiving.report")
    return render_template("receiving/report_line.html", r=row, back=back)


@receiving_bp.route("/damage")
def damage():
    user = current_user()
    if not (can(user, "receiving") or can(user, "inspection")):
        abort(403)
    records = sorted(db.read("damage_records"),
                     key=lambda d: d.get("recorded_at", ""), reverse=True)
    rows = [{
        "recorded_at": services.fmt_dt(d.get("recorded_at")),
        "source": d.get("source"), "reference": d.get("reference"),
        "item_name": d.get("item_name"), "qty": d.get("qty"),
        "unit": d.get("unit"), "stage": d.get("stage"),
        "reason": d.get("reason"),
        "recorded_by": services.user_name(d.get("recorded_by")),
    } for d in records]
    totals, by_source = services.damage_summary()
    return render_template("receiving/damage.html", rows=rows, totals=totals,
                           by_source=by_source)
