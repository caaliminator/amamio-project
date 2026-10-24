"""Approvals blueprint: admin decisions on purchase requests.

Approving items sets them to 'Ordered' and mints a QR token; NO stock moves here
(stock only changes when materials are received via QR scan).  The decision
also fills the PR form's "Checked by" and "Approved by" sign-offs."""
from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   url_for)

import db
import purchasing_service as pur
import services
from security import current_user, module_required

approvals_bp = Blueprint("approvals", __name__)


def _requestor_name(req):
    return req.get("requested_by_name") or services.user_name(req.get("requestor_id"))


@approvals_bp.route("/")
@module_required("approvals")
def index():
    pending = [r for r in db.read("purchase_requests")
               if r.get("status") == "Pending Approval"]
    pending = sorted(pending, key=lambda r: r.get("created_at", ""))
    for r in pending:
        r["requestor_name"] = _requestor_name(r)
        r["pr_label"] = pur.pr_number(r)
        r["summary"] = pur.summary_line(r)
        r["total"] = pur.total_estimated(r)
    return render_template("approvals/index.html", requests=pending,
                           urgency_badge=services.URGENCY_BADGE)


@approvals_bp.route("/<int:request_id>", methods=["GET", "POST"])
@module_required("approvals")
def decide(request_id):
    req = db.get("purchase_requests", request_id)
    if not req:
        abort(404)
    if req.get("status") != "Pending Approval":
        flash("Request %s has already been decided." % pur.pr_number(req), "error")
        return redirect(url_for("requests.detail", id=request_id))

    materials_by_id = {m["id"]: m for m in db.read("raw_materials")}

    if request.method == "POST":
        action = request.form.get("action") or "approve"
        remarks = request.form.get("remarks") or ""
        decisions = {}
        for it in req.get("items", []):
            iid = it["item_id"]
            decisions[iid] = {
                "reject": request.form.get("reject_%d" % iid) == "1",
                "approved_qty": request.form.get("approved_qty_%d" % iid),
            }
        updated = services.apply_decision(request_id, decisions, remarks, action,
                                          decider_id=current_user()["id"])
        pur.record_signoff(request_id, request.form.get("checked_by_name"),
                           current_user())
        flash("Request %s marked %s." % (pur.pr_number(updated),
                                         updated.get("status")), "success")
        if pur.orderable_items(db.get("purchase_requests", request_id)):
            return redirect(url_for("requests.detail", id=request_id))
        return redirect(url_for("approvals.index"))

    # GET — enrich items for display
    view = dict(req)
    view["requestor_name"] = _requestor_name(req)
    view["pr_label"] = pur.pr_number(req)
    view["total"] = pur.total_estimated(req)
    items = []
    for it in req.get("items", []):
        row = dict(it)
        mat = materials_by_id.get(it.get("material_id"), {})
        row["display_name"] = it.get("name") or mat.get("name", "Material #%s" % it.get("material_id"))
        row["display_unit"] = it.get("unit") or mat.get("unit", "pc")
        items.append(row)
    view["items"] = items
    return render_template("approvals/decide.html", req=view,
                           me=current_user(),
                           urgency_badge=services.URGENCY_BADGE)
