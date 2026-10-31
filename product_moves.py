"""Products In / Out — finished goods taken in or issued out by hand.

Production runs, packing, sales and customer returns move product stock by
themselves; this page records everything else (samples, items issued to a
team, transfers, damaged or expired goods, count corrections) in a short
step-by-step flow.  Every movement goes through the inventory ledger.
"""
from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   url_for)

import db
import services
import xlsx
from security import current_user, module_required

product_moves_bp = Blueprint("product_moves", __name__)


def _products():
    return sorted(db.read("products"),
                  key=lambda p: (p.get("product_group") or "",
                                 p.get("sort_order") or 0, p.get("name") or ""))


@product_moves_bp.route("/")
@module_required("product_moves")
def index():
    products = _products()
    groups, index = [], {}
    for p in products:
        g = p.get("product_group") or "Others"
        if g not in index:
            index[g] = len(groups)
            groups.append((g, []))
        groups[index[g]][1].append(p)
    return render_template("product_moves/index.html", groups=groups,
                           moves=services.product_movement_rows(limit=10),
                           is_low=services.is_low)


@product_moves_bp.route("/log")
@module_required("product_moves")
def log():
    return render_template("product_moves/log.html",
                           moves=services.product_movement_rows(limit=300))


@product_moves_bp.route("/move", methods=["GET", "POST"])
@module_required("product_moves")
def move():
    form = request.form if request.method == "POST" else request.args
    start = 0
    if request.method == "POST":
        movement, err = services.record_product_movement(
            form.get("product_id"), form.get("direction"), form.get("qty"),
            form.get("reason"), person=form.get("person") or "",
            reference=form.get("reference") or "", notes=form.get("notes") or "",
            user_id=current_user()["id"])
        if not err:
            flash("%s recorded: %s %s (%s). Now %s in stock." % (
                movement["direction"],
                services.fmt_qty(movement["qty"], movement["unit"]),
                movement["product_name"], movement["reason"],
                services.fmt_qty(movement["balance_after"], movement["unit"])),
                "success")
            return redirect(url_for("product_moves.index"))
        flash(err, "error")
        start = 2
    elif form.get("product_id") and form.get("direction") in ("IN", "OUT"):
        start = 2       # from the list: straight to "how much"
    return render_template(
        "product_moves/move.html", form=form, start=start,
        products=_products(),
        in_reasons=services.PRODUCT_IN_REASONS,
        out_reasons=services.PRODUCT_OUT_REASONS)


def _range():
    """The report's date range: ?from / ?to, this month by default."""
    date_from = request.args.get("from") or services.month_start_iso()
    date_to = request.args.get("to") or services.today_iso()
    if date_to < date_from:
        date_from, date_to = date_to, date_from
    return date_from, date_to


@product_moves_bp.route("/report")
@module_required("product_moves")
def report():
    """Printable Products IN / OUT report for a date range."""
    date_from, date_to = _range()
    return render_template("product_moves/report.html",
                           r=services.product_in_out_report(date_from, date_to),
                           date_from=date_from, date_to=date_to)


@product_moves_bp.route("/report.<fmt>")
@module_required("product_moves")
def report_download(fmt):
    if fmt not in ("xlsx", "csv"):
        abort(404)
    date_from, date_to = _range()
    r = services.product_in_out_report(date_from, date_to)
    summary = [["Product", "Group", "Unit", "Opening", "IN", "OUT", "Closing",
                "IN detail", "OUT detail", "Check"]]
    for row in r["rows"]:
        summary.append([
            row["product_name"], row["group"], row["unit"], row["opening"],
            row["total_in"], row["total_out"], row["closing"],
            "; ".join("%s %g" % kv for kv in row["in_detail"]),
            "; ".join("%s %g" % kv for kv in row["out_detail"]),
            "Ledger doesn't add up to stock" if row["mismatch"] else ""])
    moves = [["Date", "Product", "IN / OUT", "Qty", "Unit", "Reason",
              "Reference", "Person", "Recorded by"]]
    for m in r["movements"]:
        moves.append([m["when"], m["product_name"], m["direction"], m["qty"],
                      m["unit"], m["reason"], m["reference"], m["person"],
                      m["recorded_by"]])
    reasons = [["Direction", "Reason", "Qty"]]
    reasons += [["IN", k, v] for k, v in r["in_totals"]]
    reasons += [["OUT", k, v] for k, v in r["out_totals"]]
    return xlsx.download(
        [("Summary", summary), ("By reason", reasons), ("Movements", moves)],
        "amamio-products-in-out-%s-to-%s" % (date_from, date_to), fmt)
