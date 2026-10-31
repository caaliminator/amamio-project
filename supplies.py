"""Module 3 — supplies in / out and the month-end count.

Raw materials, jars / bottles / packaging, production (cleaning) supplies and
office supplies.  Every IN and OUT is recorded in a short step-by-step flow
and moves stock through the inventory ledger; the paper "Month-End Report"
per category is rebuilt from that ledger plus the physical count.  All logic
lives in ``assets_service``.
"""
from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   url_for)

import assets_service as svc
import db
import services
import xlsx
from security import current_user, module_required

supplies_bp = Blueprint("supplies", __name__)

CATEGORY_KEYS = [c[0] for c in services.MATERIAL_CATEGORIES]


def _category(value, default="raw"):
    return value if value in CATEGORY_KEYS else default


def _material_or_404(material_id):
    m = db.get("raw_materials", material_id)
    if not m or services.material_category(m) not in CATEGORY_KEYS:
        abort(404)
    return m


@supplies_bp.route("/")
@module_required("supplies")
def index():
    cat = _category(request.args.get("cat"))
    all_rows = svc.supplies()
    counts = {k: {"items": 0, "low": 0} for k in CATEGORY_KEYS}
    for m in all_rows:
        c = counts[services.material_category(m)]
        c["items"] += 1
        c["low"] += 1 if services.is_low(m) else 0
    rows = [m for m in all_rows if services.material_category(m) == cat]
    return render_template(
        "supplies/index.html", cat=cat, counts=counts,
        groups=svc.group_supplies(rows), is_low=services.is_low,
        categories=services.MATERIAL_CATEGORIES,
        low_total=sum(c["low"] for c in counts.values()))


@supplies_bp.route("/<int:material_id>")
@module_required("supplies")
def item(material_id):
    m = _material_or_404(material_id)
    return render_template(
        "supplies/item.html", m=m, low=services.is_low(m),
        cat=services.material_category(m),
        cat_label=services.MATERIAL_CATEGORY_LABELS.get(services.material_category(m)),
        moves=svc.supply_movement_rows(material_id=material_id, limit=12))


def _movement_filters():
    """Category and date range of the IN / OUT log (this month by default)."""
    cat = request.args.get("cat")
    cat = cat if cat in CATEGORY_KEYS else ""
    date_from = request.args.get("from") or services.month_start_iso()
    date_to = request.args.get("to") or services.today_iso()
    if date_to < date_from:
        date_from, date_to = date_to, date_from
    moves = svc.supply_movement_rows(category=cat or None, date_from=date_from,
                                     date_to=date_to)
    return cat, date_from, date_to, moves


@supplies_bp.route("/movements")
@module_required("supplies")
def movements():
    cat, date_from, date_to, moves = _movement_filters()
    return render_template(
        "supplies/movements.html", cat=cat, date_from=date_from,
        date_to=date_to, categories=services.MATERIAL_CATEGORIES,
        cat_label=services.MATERIAL_CATEGORY_LABELS.get(cat, "All supplies"),
        moves=moves, totals=svc.supply_movement_totals(moves))


@supplies_bp.route("/movements.<fmt>")
@module_required("supplies")
def movements_download(fmt):
    if fmt not in ("xlsx", "csv"):
        abort(404)
    cat, date_from, date_to, moves = _movement_filters()
    summary = [["Item", "Category", "Unit", "IN", "OUT"]]
    for t in svc.supply_movement_totals(moves):
        summary.append([t["material_name"],
                        services.MATERIAL_CATEGORY_LABELS.get(t["category"], ""),
                        t["unit"], t["in"], t["out"]])
    rows = [["Date", "Item", "IN / OUT", "Qty", "Unit", "Reason", "Person",
             "Reference", "Balance after", "Notes", "Recorded by"]]
    for m in sorted(moves, key=lambda m: m.get("moved_at") or ""):
        rows.append([m["when"], m.get("material_name"), m.get("direction"),
                     m.get("qty"), m.get("unit"), m.get("reason"),
                     m.get("person"), m.get("reference"), m.get("balance_after"),
                     m.get("notes"), m["recorded_by_name"]])
    return xlsx.download(
        [("Summary", summary), ("Movements", rows)],
        "amamio-supplies-in-out-%s%s-to-%s" % (cat + "-" if cat else "",
                                               date_from, date_to), fmt)


@supplies_bp.route("/move", methods=["GET", "POST"])
@module_required("supplies")
def move():
    form = request.form if request.method == "POST" else request.args
    start = 0
    preset = svc._int(form.get("material_id"))
    preset_cat = form.get("category")
    if preset and not preset_cat:
        m = db.get("raw_materials", preset)
        preset_cat = services.material_category(m) if m else ""
    if request.method == "POST":
        movement, err = svc.record_supply_movement(
            form.get("material_id"), form.get("direction"), form.get("qty"),
            form.get("reason"), person=form.get("person") or "",
            reference=form.get("reference") or "", notes=form.get("notes") or "",
            user_id=current_user()["id"])
        if not err:
            flash("%s recorded: %s %s (%s). Now %s in stock." % (
                movement["direction"], services.fmt_qty(movement["qty"], movement["unit"]),
                movement["material_name"], movement["reason"],
                services.fmt_qty(movement["balance_after"], movement["unit"])), "success")
            return redirect(url_for("supplies.item", material_id=movement["material_id"]))
        flash(err, "error")
        start = 3
    elif preset and form.get("direction") in ("IN", "OUT"):
        start = 3       # from an item page: straight to "how much"
    return render_template(
        "supplies/move.html", form=form, start=start, preset_cat=preset_cat or "",
        materials=svc.supplies(), categories=services.MATERIAL_CATEGORIES,
        category_of=services.material_category,
        in_reasons=svc.SUPPLY_IN_REASONS, out_reasons=svc.SUPPLY_OUT_REASONS)


@supplies_bp.route("/count", methods=["GET", "POST"])
@module_required("supplies")
def count():
    form = request.form if request.method == "POST" else request.args
    periods = svc.recent_periods(6)
    this_month = services.this_month()
    start = 0
    if request.method == "POST":
        cat = form.get("category")
        period = form.get("period")
        counts = {k[6:]: v for k, v in form.items() if k.startswith("count_")}
        record, err = svc.save_month_end_count(
            cat, period, counts, user_id=current_user()["id"],
            notes=form.get("notes") or "",
            adjust=form.get("adjust") == "1" and period == this_month)
        if not err:
            flash("Month-end count saved for %s — %s (%d item(s))%s." % (
                services.MATERIAL_CATEGORY_LABELS.get(cat, cat),
                svc.period_label(period), len(record["lines"]),
                "; stock adjusted to the physical count" if record.get("adjusted") else ""),
                "success")
            return redirect(url_for("supplies.report", category=cat, period=period))
        flash(err, "error")
        start = 1
    materials = svc.supplies()
    return render_template(
        "supplies/count.html", form=form, start=start, periods=periods,
        period_label=svc.period_label, this_month=this_month,
        categories=services.MATERIAL_CATEGORIES,
        groups_by_cat={k: svc.group_supplies([m for m in materials
                                              if services.material_category(m) == k])
                       for k in CATEGORY_KEYS},
        system=svc.system_closing(materials, periods),
        recent=svc.count_rows(limit=4))


@supplies_bp.route("/report")
@module_required("supplies")
def report():
    cat = _category(request.args.get("category"))
    period = request.args.get("period") or services.this_month()
    if not svc.valid_period(period) or period > services.this_month():
        period = services.this_month()
    periods = svc.recent_periods(12)
    if period not in periods:
        periods.append(period)
    return render_template(
        "supplies/report.html", r=svc.month_end_report(cat, period),
        categories=services.MATERIAL_CATEGORIES, cat=cat, period=period,
        periods=periods, period_label=svc.period_label,
        this_month=services.this_month())
