"""QR blueprint: one scanner for every code the floor produces.

These codes circulate in the business, told apart by their prefix:

    RB-<box>             a receiving cage box / crate
    HC-<container>       a honey container awaiting (or carrying) its QC result
    PK-<run>             a packing run
    PR-<request>-<item>  an older purchase-request label (opens the request)

Scanning any of them here resolves what it is and shows the matching action,
so staff only ever need this one screen.
"""
import io
import re

from flask import (Blueprint, Response, abort, flash, jsonify, redirect,
                   render_template, request, url_for)
from markupsafe import escape

import db
import services
from security import can, current_user, module_required

try:
    import qrcode
    _HAVE_QR = True
except ImportError:  # pragma: no cover - optional dependency
    _HAVE_QR = False

qr_bp = Blueprint("qr", __name__)

# Every token shape the scanner accepts.  A printed label carries the code
# first and then its details ("RB-12 | Ginger | 12 kg | …"), so the code is
# searched for anywhere in what the scanner typed.
TOKEN_RE = re.compile(r"^(?:PR-\d+-\d+|RB-\d+|HC-\d+|PK-\d+)$", re.IGNORECASE)
_FIND_RE = re.compile(r"\b(PR-\d+-\d+|RB-\d+|HC-\d+|PK-\d+)\b", re.IGNORECASE)


def _normalize(token):
    """Pull the code out of whatever was scanned or typed."""
    text = (token or "").strip().upper()
    found = _FIND_RE.search(text)
    return found.group(1) if found else text


def resolve_token(token):
    """Identify a scanned code.  Returns a dict describing what it points at."""
    token = _normalize(token)
    if not TOKEN_RE.match(token):
        return {"ok": False, "kind": "unknown",
                "message": "Unrecognised code: %s" % (token or "(empty)")}

    prefix = token.split("-")[0]

    if prefix == "RB":
        box = db.get("receiving_boxes", token.split("-")[1])
        if not box:
            return {"ok": False, "kind": "box",
                    "message": "No receiving box matches %s." % token}
        batch = db.get("receiving_batches", box.get("batch_id")) or {}
        status = services.box_status(box)
        user = current_user()
        url = url_for("receiving.box", box_id=box["id"])
        action = "Open this box"
        if status == services.BOX_AWAITING and can(user, "inspection"):
            url = url_for("receiving.inspect", box_id=box["id"])
            action = "Inspect & clean this box"
        unit = box.get("unit") or ""
        return {
            "ok": True, "kind": "box", "token": token, "record": box,
            "title": "%s — %s" % (box.get("material_name") or "Box",
                                  services.fmt_qty(box.get("declared_qty"), unit)),
            "subtitle": "Box %s · %s · %s" % (token, batch.get("ref_no") or "",
                                             batch.get("supplier_name") or ""),
            "status": status, "url": url, "action": action,
            "details": [
                ("Received", batch.get("received_date")),
                ("Where it is", services.box_stage(box)),
                ("Lot / batch", box.get("lot_no")),
                ("Expiry", box.get("expiry_date")),
                ("Usable", services.fmt_qty(box.get("good_qty"), unit)
                 if status != services.BOX_AWAITING else None),
                ("Can't be used", services.fmt_qty(box.get("unusable_qty"), unit)
                 if status != services.BOX_AWAITING else None),
                ("Damaged", services.fmt_qty(box.get("damaged_qty"), unit)
                 if status != services.BOX_AWAITING else None),
            ],
        }

    if prefix == "HC":
        container = db.get("honey_containers", token.split("-")[1])
        if not container:
            return {"ok": False, "kind": "container",
                    "message": "No honey container matches %s." % token}
        status = container.get("status")
        action = "Open this container"
        if status == "Pending QC":
            action = "Check moisture & appearance"
        elif status in services.QC_RESULTS and services._to_float(
                container.get("remaining_kg",
                              container.get("net_weight_kg"))) > 0:
            action = "Send to a product"
        return {
            "ok": True, "kind": "container", "token": token,
            "record": container,
            "title": "Honey — %s" % services.fmt_qty(
                container.get("net_weight_kg"), "kg"),
            "subtitle": "Container %s · %s" % (token,
                                              container.get("batch_ref") or ""),
            "status": status, "action": action,
            "url": url_for("honey.container", container_id=container["id"]),
            "details": [
                ("Source", container.get("supplier_name") or container.get("source")),
                ("Received", container.get("received_date")),
                ("Moisture", ("%s %%" % container.get("moisture_pct"))
                 if container.get("moisture_pct") is not None else None),
                ("Appearance", container.get("appearance")),
                ("Routed to", container.get("disposition")),
            ],
        }

    if prefix == "PK":
        run = db.get("packing_runs", token.split("-")[1])
        if not run:
            return {"ok": False, "kind": "packing",
                    "message": "No packing run matches %s." % token}
        action = {"In Progress": "Finish packing",
                  "Packed": "Disburse or hand to courier"}.get(
                      run.get("status"), "Open this packing run")
        if not can(current_user(), "packing"):
            # Packing runs are opened by the packing team only.
            return {
                "ok": True, "kind": "packing", "token": token, "record": run,
                "title": "Packing %s — %s item(s)" % (
                    run.get("channel") or "", services.fmt_qty(run.get("total_qty"))),
                "subtitle": "Run %s · packed by %s" % (token,
                                                       run.get("packer_name") or "—"),
                "status": run.get("status"), "url": url_for("qr.scan"),
                "action": "Only the packing team opens packing runs",
                "details": [("Pack date", run.get("pack_date")),
                            ("Courier", run.get("courier")),
                            ("Tracking no.", run.get("tracking_no"))],
            }
        return {
            "ok": True, "kind": "packing", "token": token, "record": run,
            "title": "Packing %s — %s item(s)" % (
                run.get("channel") or "", services.fmt_qty(run.get("total_qty"))),
            "subtitle": "Run %s · packed by %s" % (token,
                                                   run.get("packer_name") or "—"),
            "status": run.get("status"), "action": action,
            "url": url_for("packing.detail", run_id=run["id"]),
            "details": [
                ("Pack date", run.get("pack_date")),
                ("Products", len(run.get("items", []))),
                ("Courier", run.get("courier")),
                ("Tracking no.", run.get("tracking_no")),
                ("Handed to", run.get("handed_to")),
            ],
        }

    # PR-<request>-<item>: labels from before goods were received against
    # purchase orders.  They open the request; receiving happens at the
    # Receiving Area now, so every delivery is inspected first.
    request_id, item_id = services.parse_qr_token(token)
    req = db.get("purchase_requests", request_id) if request_id else None
    if not req:
        return {"ok": False, "kind": "purchase",
                "message": "No purchase request matches %s." % token}
    item = next((i for i in req.get("items", [])
                 if i.get("item_id") == item_id), None)
    if not item:
        return {"ok": False, "kind": "purchase",
                "message": "No item on request #%s matches %s."
                           % (request_id, token)}
    material = db.get("raw_materials", item.get("material_id")) or {}
    return {
        "ok": True, "kind": "purchase", "token": token, "record": item,
        "title": "Purchase request #%s" % request_id,
        "subtitle": material.get("name") or item.get("name") or "",
        "status": item.get("status"),
        "url": url_for("requests.detail", id=request_id),
        "action": "Open the request — receive the goods at the Receiving Area",
        "details": [
            ("Item", material.get("name") or item.get("name")),
            ("Requested", item.get("requested_qty")),
            ("Approved", item.get("approved_qty")),
            ("Status", item.get("status")),
        ],
    }


@qr_bp.route("/scan")
@module_required("qr")
def scan():
    return render_template("qr/scan.html")


@qr_bp.route("/lookup")
@module_required("qr")
def lookup():
    """JSON resolution of a scanned code, used by the scanner page."""
    result = resolve_token(request.args.get("token"))
    payload = {k: v for k, v in result.items() if k != "record"}
    payload["details"] = [d for d in payload.get("details", [])
                          if d[1] not in (None, "", "—")]
    return jsonify(payload), (200 if result.get("ok") else 404)


@qr_bp.route("/go")
@module_required("qr")
def go():
    """Open whatever a scanned code points at."""
    result = resolve_token(request.args.get("token"))
    if not result.get("ok"):
        flash(result.get("message"), "error")
        return redirect(url_for("qr.scan"))
    return redirect(result["url"])


@qr_bp.route("/image/<token>")
@module_required("qr")
def image(token):
    """Render a token as a QR PNG (or a readable placeholder without qrcode)."""
    token = _normalize(token)
    if not TOKEN_RE.match(token):
        abort(404)
    if _HAVE_QR:
        img = qrcode.make(services.qr_payload(token))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Response(buf.getvalue(), mimetype="image/png")
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="160" '
        'viewBox="0 0 160 160"><rect width="160" height="160" fill="#fff" '
        'stroke="#111"/><text x="80" y="80" text-anchor="middle" '
        'font-size="12" font-family="monospace" fill="#111">%s</text></svg>'
        % escape(token))
    return Response(svg, mimetype="image/svg+xml")
