"""Approvals blueprint: the two-stage sign-off of purchase requests and
purchase orders.

Every PR and PO is approved twice: the Admin checks it first (initial
approval, signed "Checked by"), then the Super Admin approves it last
("Approved by").  Each sees only what waits for their own stage.  PR items
only become orderable after the final approval; NO stock moves here (stock
only changes when the goods are received and pass inspection).  POs are
approved on their own page (purchasing.py); they are listed here so each
approver has one queue."""
from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   url_for)

import db
import purchasing_service as pur
import services
from security import current_user, module_required

approvals_bp = Blueprint("approvals", __name__)


def _requestor_name(req):
    return req.get("requested_by_name") or services.user_name(req.get("requestor_id"))


def _queue_row(r):
    r["requestor_name"] = _requestor_name(r)
    r["pr_label"] = pur.pr_number(r)
    r["summary"] = pur.summary_line(r)
    r["total"] = pur.total_estimated(r)
    r["stage"] = pur.approval_stage(r)
    return r


@approvals_bp.route("/")
@module_required("approvals")
def index():
    user = current_user()
    mine, orders = pur.approvals_for(user)
    # What sits at the other stage, so each approver sees the whole queue.
    others = [r for r in db.read("purchase_requests")
              if pur.approval_stage(r) and not pur.can_approve(user, r)]
    others.sort(key=lambda r: r.get("created_at", ""))
    return render_template("approvals/index.html",
                           requests=[_queue_row(r) for r in mine],
                           others=[_queue_row(r) for r in others],
                           orders=orders, waiting_on=pur.waiting_on,
                           urgency_badge=services.URGENCY_BADGE)


@approvals_bp.route("/<int:request_id>", methods=["GET", "POST"])
@module_required("approvals")
def decide(request_id):
    req = db.get("purchase_requests", request_id)
    if not req:
        abort(404)
    user = current_user()
    stage = pur.approval_stage(req)
    if not stage:
        flash("Request %s has already been decided." % pur.pr_number(req), "error")
        return redirect(url_for("requests.detail", id=request_id))
    if not pur.can_approve(user, req):
        flash("Request %s is waiting for %s." % (pur.pr_number(req), pur.waiting_on(req)),
              "error")
        return redirect(url_for("requests.detail", id=request_id))

    materials_by_id = {m["id"]: m for m in db.read("raw_materials")}

    if request.method == "POST":
        action = request.form.get("action") or "approve"
        decisions = {}
        for it in req.get("items", []):
            iid = it["item_id"]
            decisions[iid] = {
                "reject": request.form.get("reject_%d" % iid) == "1",
                "approved_qty": request.form.get("approved_qty_%d" % iid),
            }
        updated, err = pur.decide_request(request_id, user, decisions,
                                          request.form.get("remarks") or "", action)
        if err:
            flash(err, "error")
            return redirect(url_for("requests.detail", id=request_id))
        if updated.get("status") == pur.FOR_FINAL:
            flash("Request %s checked — sent to the Super Admin for final approval."
                  % pur.pr_number(updated), "success")
            return redirect(url_for("approvals.index"))
        flash("Request %s marked %s." % (pur.pr_number(updated),
                                         updated.get("status")), "success")
        if pur.orderable_items(updated):
            return redirect(url_for("requests.detail", id=request_id))
        return redirect(url_for("approvals.index"))

    # GET — enrich items for display
    view = _queue_row(dict(req))
    items = []
    for it in req.get("items", []):
        row = dict(it)
        mat = materials_by_id.get(it.get("material_id"), {})
        row["display_name"] = it.get("name") or mat.get("name", "Material #%s" % it.get("material_id"))
        row["display_unit"] = it.get("unit") or mat.get("unit", "pc")
        items.append(row)
    view["items"] = items
    return render_template("approvals/decide.html", req=view, me=user,
                           stage=stage, urgency_badge=services.URGENCY_BADGE)
