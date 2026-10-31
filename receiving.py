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
from datetime import datetime

from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, url_for)

import db
import profiles
import purchasing_service as pur
import services
from security import can, current_user, module_required

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


def _checklist_from_form(form):
    """The inspection checklist radios + remarks -> {point: {result, remarks}}."""
    return {key: {"result": form.get("chk_" + key) or "",
                  "remarks": form.get("chk_%s_remarks" % key) or ""}
            for key, _, _ in services.INSPECTION_POINTS}


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
    show = request.args.get("show") or "open"
    batches = db.read("receiving_batches")
    still_open = (services.RECEIVING_OPEN, services.RECEIVING_FOR_CLOSING)
    if show == "open":
        listed = [b for b in batches if b.get("status") in still_open]
    else:
        listed = batches
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
    po_arg = request.values.get("po")
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
        batch, boxes, err = services.receive_delivery(
            current_user()["id"], _int(form.get("supplier_id")),
            form.get("received_date") or services.today_iso(),
            _lines_from_form(form), po_no=form.get("po_no") or "",
            delivery_ref=form.get("delivery_ref") or "",
            remarks=form.get("remarks") or "", po_id=po["id"] if po else None,
            received_time=form.get("received_time") or "",
            delivered_by=form.get("delivered_by") or "",
            vehicle_plate=form.get("vehicle_plate") or "",
            receiving_area=form.get("receiving_area") or "",
            docs_checked=form.getlist("docs_checked"),
            docs_other=form.get("docs_other") or "")
        if not err:
            flash("Delivery %s recorded — %d box(es) labelled. Print the QR "
                  "labels and stick one on each box." % (batch.get("ref_no"),
                                                         len(boxes)), "success")
            return redirect(url_for("receiving.labels", batch_id=batch["id"],
                                    fresh=1))
        flash(err, "error")

    return render_template(
        "receiving/new.html", suppliers=sorted(db.read("suppliers"),
                                               key=lambda s: s.get("name") or ""),
        materials=services.stocked_materials(),
        categories=services.MATERIAL_CATEGORIES,
        category_of=services.material_category,
        track_labels=services.TRACK_MODE_LABELS,
        conditions=services.PACKAGING_CONDITIONS,
        docs=services.DOCS_CHECKED,
        now_time=datetime.now().strftime("%H:%M"),
        today=services.today_iso(), form=form, po=po,
        po_lines=[l for l in pur.lines_to_receive(po) if l["remaining"] > 0] if po else [],
        lines=_lines_from_form(form) if request.method == "POST" else [],
        start=1 if request.method == "POST" else 0)


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
        materials=services.stocked_materials(),
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
    return render_template(
        "receiving/close.html", batch=record, form=form,
        checks=services.CONTROL_CHECKS, answers=services.CONTROL_ANSWERS,
        awaiting=[b for b in boxes if b["status"] == services.BOX_AWAITING],
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


@receiving_bp.route("/box/<int:box_id>")
def box(box_id):
    row = _box_or_404(box_id)
    g.nav_endpoint = ("receiving.inspection"
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
        fefo_first=first,
        can_inspect=can(current_user(), "inspection"))


@receiving_bp.route("/box/<int:box_id>/checklist")
def checklist(box_id):
    """The Incoming Material Inspection Checklist (AFP-WH-FRM-002), filled in."""
    row = _box_or_404(box_id)
    if row["status"] == services.BOX_AWAITING:
        flash("Box %s hasn't been inspected yet." % row["box_code"], "error")
        return redirect(url_for("receiving.box", box_id=box_id))
    return render_template("receiving/checklist.html", box=row,
                           points=services.INSPECTION_POINTS)


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
    waiting = services.receiving_box_rows(status=services.BOX_AWAITING)
    held = services.receiving_box_rows(status=services.BOX_HOLD)
    done = [r for r in services.receiving_box_rows(
        status=(services.BOX_ACCEPTED, services.BOX_REJECTED))
        if r["checked_at"]]
    done.sort(key=lambda r: r["checked_at"], reverse=True)
    return render_template("receiving/inspection.html", waiting=waiting,
                           held=held, done=done[:8])


@receiving_bp.route("/box/<int:box_id>/inspect", methods=["GET", "POST"])
@module_required("inspection")
def inspect(box_id):
    g.nav_endpoint = "receiving.inspection"
    row = _box_or_404(box_id)
    if row["status"] != services.BOX_AWAITING:
        flash("Box %s was already inspected." % row["box_code"], "error")
        return redirect(url_for("receiving.box", box_id=box_id))
    form = request.form
    if request.method == "POST":
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
            visual_description=form.get("visual_description") or "")
        if not err:
            flash("Box %s inspected — %s." % (box.get("box_code"),
                                              box.get("acceptance_status")),
                  "success" if box.get("acceptance_status") == "Accepted"
                  else "error")
            nxt = services.receiving_box_rows(status=services.BOX_AWAITING)
            if nxt and request.form.get("then") == "next":
                return redirect(url_for("receiving.inspect", box_id=nxt[0]["id"]))
            return redirect(url_for("receiving.box", box_id=box_id))
        flash(err, "error")

    return render_template(
        "receiving/inspect.html", box=row, form=form,
        points=services.INSPECTION_POINTS, check_results=services.CHECK_RESULTS,
        point_types=services.NC_TYPE_FOR_POINT, nc_types=services.NC_TYPES,
        waiting_count=len(services.receiving_box_rows(
            status=services.BOX_AWAITING)),
        start=1 if request.method == "POST" else 0)


# --- Nonconforming Material Hold / Rejection Report (AFP-WH-FRM-003) ---------
@receiving_bp.route("/ncr")
def ncr_list():
    user = current_user()
    if not (can(user, "receiving") or can(user, "inspection")):
        abort(403)
    show = request.args.get("show") or "open"
    return render_template("receiving/ncr_list.html", show=show,
                           rows=services.ncr_rows("open" if show == "open" else None))


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
