"""End-to-end smoke test for the Amamio Sales & Inventory Management System.

Runs against a throwaway temp data directory so it never touches your real
data/.  Run with:  python smoke_test.py   (exit code 0 = all passed).
"""
import io
import os
import shutil
import sys
import tempfile

import db
# Point storage at a fresh temp dir BEFORE the app is created / seeded.
db.DATA_DIR = tempfile.mkdtemp(prefix="amamio-smoke-")
# Uploaded sales files go to a temp copy of the sales-platform folder.
_SALES = tempfile.mkdtemp(prefix="amamio-sales-")
_HERE = os.path.dirname(os.path.abspath(__file__))
for _name in os.listdir(os.path.join(_HERE, "sales-platform")):
    shutil.copy(os.path.join(_HERE, "sales-platform", _name), _SALES)
os.environ["AMAMIO_SALES_DIR"] = _SALES

import app as appmod          # noqa: E402  (import after DATA_DIR override)
import platform_service as ps  # noqa: E402
import services               # noqa: E402

application = appmod.create_app()
fails = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)


def login(c, u, p):
    return c.post("/login", data={"username": u, "password": p},
                  follow_redirects=True)


def stock(name, kind="material"):
    table = "raw_materials" if kind == "material" else "products"
    item = db.query(table, name=name)[0]
    return services.current_stock(kind, item["id"]), item


ACCOUNTS = [("superadmin", "super123"), ("admin", "admin123"),
            ("receiving", "recv123"), ("production", "prod123"),
            ("packing", "pack123"), ("packer1", "member123"),
            ("assets", "assets123")]

# 1) every demo account logs in and gets a home screen with its tasks
for u, p in ACCOUNTS:
    with application.test_client() as c:
        check(login(c, u, p).status_code == 200, "login %s" % u)
        r = c.get("/dashboard")
        check(r.status_code == 200 and b"task-card" in r.data,
              "home screen for %s" % u)

# 1b) each user picks light, dark or "match my device"; it is remembered
with application.test_client() as c:
    login(c, "packer1", "member123")
    check(b'data-theme="dark"' in c.get("/dashboard").data, "dark theme by default")
    c.post("/settings/theme", data={"theme": "light"})
    check(b'data-theme="light"' in c.get("/dashboard").data,
          "light theme is saved and applied")
    c.post("/settings/theme", data={"theme": "system"})
    page = c.get("/dashboard").data
    check(b'data-theme-pref="system"' in page and b'data-theme="' not in page,
          "match-my-device leaves the choice to the device")
    check(c.post("/settings/theme", data={"theme": "pink"}).status_code == 400,
          "an unknown theme is refused")
with application.test_client() as c:
    login(c, "packer2", "member123")
    check(b'data-theme="dark"' in c.get("/dashboard").data,
          "one user's theme does not change another's")

# 1c) every page has an "About this page" note, and users can hide them
import page_notes             # noqa: E402

NOT_PAGES = {"static", "auth.root", "auth.login", "auth.logout",
             "honey.suggest", "qr.lookup", "qr.image", "qr.go",
             "platforms.export_csv", "platforms.import_file",
             "migration.download", "requests.bom_calc", "production.new",
             "requests.new_material"}
pages = {r.endpoint for r in application.url_map.iter_rules()
         if "GET" in r.methods and r.endpoint not in NOT_PAGES}
missing = sorted(p for p in pages if not page_notes.for_endpoint(p))
check(not missing, "every page has a note (%s)" % (", ".join(missing) or "all"))
stale = sorted(set(page_notes.NOTES) - pages)
check(not stale, "no note points at a page that no longer exists (%s)"
      % (", ".join(stale) or "none"))
with application.test_client() as c:
    login(c, "receiving", "recv123")
    check(b"About this page" in c.get("/receiving/new").data,
          "notes show by default")
    c.post("/settings/notes", data={"show": "0"})
    check(b'class="page-note' not in c.get("/receiving/new").data,
          "a user can hide the notes")
    c.post("/settings/notes", data={"show": "1"})
    check(b'class="page-note' in c.get("/receiving/new").data,
          "and turn them back on")

# 2) analytics + reports render charts
with application.test_client() as c:
    login(c, "admin", "admin123")
    for route in ("/analytics/", "/analytics/forecast", "/reports/"):
        r = c.get(route)
        check(r.status_code == 200 and b"<svg" in r.data,
              "%s renders chart" % route)

# 3) role guards hold
GUARDS = [
    ("packing", "pack123", "/receiving/", 403),
    ("packing", "pack123", "/approvals/", 403),
    ("packer1", "member123", "/platforms/", 403),
    ("packer1", "member123", "/requests/", 403),
    ("packer1", "member123", "/packing/new", 200),
    ("admin", "admin123", "/packing/", 403),
    ("superadmin", "super123", "/packing/new", 200),
    ("admin", "admin123", "/purchasing/", 200),
    ("production", "prod123", "/receiving/new", 403),
    ("production", "prod123", "/receiving/inspection", 200),
    ("receiving", "recv123", "/receiving/new", 200),
    ("receiving", "recv123", "/packing/", 403),
    ("assets", "assets123", "/honey/", 403),
    ("admin", "admin123", "/migration/", 403),
    ("superadmin", "super123", "/migration/", 200),
]
for u, p, route, expected in GUARDS:
    with application.test_client() as c:
        login(c, u, p)
        check(c.get(route).status_code == expected,
              "%s -> %s is %d" % (u, route, expected))

# 4) Module 1, part 1 — the receiving head records a delivery
with application.test_client() as c:
    login(c, "receiving", "recv123")
    ginger_before, ginger = stock("Ginger")
    _, lemon = stock("Lemon")
    unread_before = len([n for n in db.read("notifications") if not n["is_read"]])
    r = c.post("/receiving/new", data={
        "supplier_id": "", "received_date": services.today_iso(),
        "po_no": "PO-T1", "delivery_ref": "DR-T1",
        "material_id": [ginger["id"], lemon["id"]],
        "qty_per_box": ["10", "100"], "boxes": ["2", "1"],
        "lot_no": ["LOT-A", ""], "expiry_date": ["", ""],
        "packaging_condition": ["Good / intact", ""],
        "temperature_c": ["26", ""]}, follow_redirects=False)
    check(r.status_code == 302 and "/labels" in r.headers.get("Location", ""),
          "receiving wizard saves and goes to the QR labels")
    batch = db.read("receiving_batches")[-1]
    boxes = db.query("receiving_boxes", batch_id=batch["id"])
    check(len(boxes) == 3 and all(b["status"] == "Awaiting Inspection"
                                  for b in boxes),
          "one box per QR label, all awaiting inspection")
    check(services.current_stock("material", ginger["id"]) == ginger_before,
          "receiving alone does not add stock")
    unread_after = len([n for n in db.read("notifications") if not n["is_read"]])
    check(unread_after > unread_before,
          "the team is notified a delivery arrived")
    payload = services.qr_payload(boxes[0]["box_code"])
    check("Ginger" in payload and "10 kg" in payload and "LOT-A" in payload,
          "QR label carries the item, quantity and lot")
    data = c.get("/qr/lookup?token=" + payload).get_json()
    check(data.get("kind") == "box", "scanning the full label text finds the box")
    check(c.get("/receiving/%d/labels" % batch["id"]).status_code == 200,
          "labels page renders")

# 5) Module 1, part 2 — inspection & cleaning: usable / can't use / damaged
with application.test_client() as c:
    login(c, "production", "prod123")
    damage_before = len(db.read("damage_records"))
    r = c.post("/receiving/box/%d/inspect" % boxes[0]["id"], data={
        "cleaned": "1", "usable_qty": "8.5", "unusable_qty": "1",
        "damaged_qty": "0.5", "unusable_reason": "Undersized",
        "damage_reason": "Bruised", "visual_inspection": "Pass",
        "coa_check": "Not required", "sampling_test": "Not required",
        "acceptance": "Accepted", "verified_by_name": "QA"},
        follow_redirects=True)
    after = services.current_stock("material", ginger["id"])
    check(abs(after - ginger_before - 8.5) < 0.001,
          "only the usable quantity joined stock")
    check(len(db.read("damage_records")) == damage_before + 2,
          "can't-be-used and damaged are both logged")
    box = db.get("receiving_boxes", boxes[0]["id"])
    check(box["status"] == "Accepted" and box["stage"] == "Storage Area",
          "accepted box moved to the Storage Area")
    # A missing reason is refused.
    c.post("/receiving/box/%d/inspect" % boxes[1]["id"], data={
        "cleaned": "1", "usable_qty": "9", "damaged_qty": "1",
        "visual_inspection": "Pass", "coa_check": "Complete",
        "sampling_test": "Passed", "acceptance": "Accepted"})
    check(db.get("receiving_boxes", boxes[1]["id"])["status"]
          == "Awaiting Inspection", "damage without a reason is refused")
    # Hold: nothing moves until the decision.
    c.post("/receiving/box/%d/inspect" % boxes[1]["id"], data={
        "cleaned": "1", "usable_qty": "10", "visual_inspection": "Pass",
        "coa_check": "Incomplete", "sampling_test": "Pending result",
        "acceptance": "Hold", "nonconformity": "Waiting for COA"})
    held = services.current_stock("material", ginger["id"])
    check(db.get("receiving_boxes", boxes[1]["id"])["status"] == "On Hold"
          and abs(held - after) < 0.001, "a box on hold adds no stock")
    c.post("/receiving/box/%d/hold" % boxes[1]["id"],
           data={"decision": "Accepted", "note": "COA received"})
    check(abs(services.current_stock("material", ginger["id"]) - held - 10)
          < 0.001, "accepting the held box adds its usable quantity")
    r = c.get("/receiving/report?from=2000-01-01")
    check(r.status_code == 200 and b"RECEIVING AND INSPECTION" in r.data.upper(),
          "receiving & inspection report renders")

# 6) Module 2 — honey: PASS to selling, FAILED to other products
with application.test_client() as c:
    login(c, "production", "prod123")
    honey_before, honey = stock("Raw Honey")
    made, _ = services.log_honey_containers(2, 10, 1, material_id=honey["id"])
    good, bad = made
    c.post("/honey/%d/qc" % good["id"], data={"result": "Pass",
           "moisture_pct": "18", "appearance": "Clear"})
    c.post("/honey/%d/qc" % bad["id"], data={"result": "Failed",
           "moisture_pct": "24", "appearance": "Fermented / off-smell"})
    check(abs(services.current_stock("material", honey["id"]) - honey_before - 10)
          < 0.001, "only passed honey joined sellable stock")
    processing = db.query("raw_materials", name=services.PROCESSING_HONEY)
    check(processing and processing[0]["stock_qty"] >= 10,
          "failed honey is kept as honey for processing")
    c.post("/honey/%d/allocate" % bad["id"],
           data={"product": services.HONEY_PASS_PRODUCTS[0], "kg": "5"})
    check(not db.get("honey_containers", bad["id"]).get("allocations"),
          "failed honey cannot go to a PASS product")
    c.post("/honey/%d/allocate" % bad["id"],
           data={"product": services.HONEY_FAIL_PRODUCTS[0], "kg": "4"})
    check(db.get("honey_containers", bad["id"])["remaining_kg"] == 6,
          "allocation records the kg used and what is left")

# 7) Module 4 — packing: packed, then disbursed or handed to the courier
with application.test_client() as c:
    login(c, "packer1", "member123")
    before, product = stock("Pure Honey 250 mL", "product")
    r = c.post("/packing/new", data={
        "channel": "Shopee", "packer_id": str(db.query("users", username="packer1")[0]["id"]),
        "pack_date": services.today_iso(), "order_ref": "SHP-TEST",
        "product_id": [str(product["id"])], "qty": ["3"], "damaged_qty": ["0"],
        "line_notes": [""]})
    run = db.read("packing_runs")[-1]
    check(run["status"] == "Packed" and abs(
        before - services.current_stock("product", product["id"]) - 3) < 0.001,
        "recording packing marks it packed and releases stock")
    c.post("/packing/%d/dispatch" % run["id"], data={
        "dispatch_type": "Handed to Courier", "handed_to": "Rider"})
    check(db.get("packing_runs", run["id"])["status"] == "Packed",
          "courier hand-over needs the courier name")
    c.post("/packing/%d/dispatch" % run["id"], data={
        "dispatch_type": "Handed to Courier", "courier": "J&T Express",
        "handed_to": "Rider Al", "tracking_no": "JT123", "parcel_count": "1"})
    check(db.get("packing_runs", run["id"])["status"] == "Handed to Courier",
          "run handed to the courier")
    summary = services.packing_summary()
    check(summary["courier_qty"] >= 3 and summary["disbursed_qty"] > 0,
          "packing summary counts packed, disbursed and courier separately")
    check(c.post("/packing/%d/cancel" % run["id"]).status_code == 403,
          "a packing member cannot cancel a run")

# 7b) Packing is recorded by the packers (and the Super Admin) as themselves
packer1_id = db.query("users", username="packer1")[0]["id"]
packer2_id = db.query("users", username="packer2")[0]["id"]
with application.test_client() as c:
    login(c, "packer1", "member123")
    page = c.get("/packing/new").data.decode("utf-8")
    check("in stock" in page and 'name="packer_id"' not in page,
          "packers see stock counts and cannot change Packed by")
    _, product = stock("Pure Honey 250 mL", "product")
    c.post("/packing/new", data={
        "channel": "Shopee", "packer_id": str(packer2_id),
        "pack_date": services.today_iso(), "order_ref": "SHP-LOCK",
        "product_id": [str(product["id"])], "qty": ["1"], "damaged_qty": ["0"],
        "line_notes": [""]})
    check(db.read("packing_runs")[-1]["packer_id"] == packer1_id,
          "a packer always records packing under their own account")
    runs_before = len(db.read("packing_runs"))
    have = services.current_stock("product", product["id"])
    c.post("/packing/new", data={
        "channel": "Shopee", "pack_date": services.today_iso(),
        "product_id": [str(product["id"])], "qty": [str(have + 50)],
        "damaged_qty": ["0"], "line_notes": [""]})
    check(len(db.read("packing_runs")) == runs_before
          and services.current_stock("product", product["id"]) == have,
          "packing more than is in stock is refused")
with application.test_client() as c:
    login(c, "superadmin", "super123")
    c.post("/packing/new", data={
        "channel": "Lazada", "packer_id": str(packer2_id),
        "pack_date": services.today_iso(),
        "product_id": [str(product["id"])], "qty": ["1"], "damaged_qty": ["0"],
        "line_notes": [""]})
    check(db.read("packing_runs")[-1]["packer_id"] == packer2_id,
          "the Super Admin can record packing for a chosen packer")

# 7c) Purchase Request Form -> approval -> Purchase Order (the client's forms)
demo = db.query("purchase_requests", pr_no="2026-09-017")
check(demo and demo[0]["status"] == "Ordered"
      and demo[0]["checked_by_name"] == "Jefrey S. Pabillore",
      "the client's PR 2026-09-017 is seeded, approved and ordered")
demo_po = db.query("purchase_orders", po_no="2026-09-017")
check(demo_po and abs(demo_po[0]["grand_total"] - 1100) < 0.01
      and demo_po[0]["supplier_name"] == "Trimix Butuan",
      "its PO to Trimix Butuan totals P1,100.00")
with application.test_client() as c:
    login(c, "production", "prod123")
    check(c.get("/requests/new").status_code == 200, "PR form opens")
    c.post("/requests/new", data={
        "department": "Production", "purpose": "Spare parts for the sealer",
        "needed_by": "", "notes": "Heating element burnt", "urgency": "Urgent",
        "description": ["Heating element", "Teflon tape"],
        "purpose_use": ["Band sealer", "Band sealer"], "qty": ["2", "3"],
        "unit": ["pc", "roll"], "est_unit_cost": ["450", "80"],
        "preferred_supplier": ["Trimix Butuan", ""],
        "budget_category": ["Equipment / repair", ""], "remark": ["", ""]})
    pr = db.read("purchase_requests")[-1]
    check(pr.get("purpose") == "Spare parts for the sealer"
          and len(pr["items"]) == 2 and abs(pr["total_estimated"] - 1140) < 0.01
          and pr["pr_no"].startswith(services.today_iso()[:7]),
          "PR saved with its number, items and estimated total")
    check(c.get("/requests/%d/print" % pr["id"]).status_code == 200,
          "PR prints as the Purchase Request Form")
    check(c.get("/purchasing/").status_code == 403,
          "a team head cannot open purchase orders")
with application.test_client() as c:
    login(c, "admin", "admin123")
    c.post("/approvals/%d" % pr["id"], data={
        "action": "approve", "checked_by_name": "Jefrey S. Pabillore"})
    pr = db.get("purchase_requests", pr["id"])
    check(pr["status"] == "Approved" and pr["checked_by_name"] == "Jefrey S. Pabillore"
          and pr["approved_by_name"], "approval records Checked by and Approved by")
    check(c.get("/purchasing/new?request_id=%d" % pr["id"]).status_code == 200,
          "PO form opens for the approved PR")
    c.post("/purchasing/new?request_id=%d" % pr["id"], data={
        "request_id": pr["id"], "supplier_name": "Trimix Butuan",
        "supplier_address": "Butuan", "po_date": services.today_iso(),
        "payment_terms": "Cash on delivery", "deliver_to": "Amamio Farm Products",
        "delivery_address": "Nasipit", "freight": "60",
        "pr_item_id": ["1", "2"], "include": ["1", "0"],
        "description": ["Heating element", "Teflon tape"], "brand_model": ["", ""],
        "qty": ["2", "3"], "unit": ["pc", "roll"], "unit_price": ["430", "80"],
        "requested_delivery": ["", ""], "vat": ["", ""], "remarks": ["", ""],
        "prepared_by": "Bernadith Frianeza", "checked_by": "Fe Amor Eballar"})
    po = db.read("purchase_orders")[-1]
    pr = db.get("purchase_requests", pr["id"])
    check(po["po_no"] == pr["pr_no"] and len(po["items"]) == 1
          and abs(po["grand_total"] - 920) < 0.01,
          "PO prepared from the approved lines (unticked line left out)")
    check(pr["status"] == "Approved" and pr["items"][0]["po_no"] == po["po_no"]
          and not pr["items"][1].get("po_id"),
          "the PR shows which line is on the PO and which still needs one")
    c.post("/purchasing/%d/approve" % po["id"], data={"approved_by_name": "Janice Nilda A. Mira"})
    check(db.get("purchase_orders", po["id"])["status"] == "Approved",
          "PO approved")
    page = c.get("/purchasing/%d/print" % po["id"]).data.decode("utf-8")
    check("Purchase Order" in page and "Janice Nilda A. Mira" in page,
          "PO prints as the Purchase Order form")
    c.post("/purchasing/%d/delivered" % po["id"])
    check(db.get("purchase_orders", po["id"])["status"] == "Delivered",
          "PO marked delivered")
    for route in ("/purchasing/", "/requests/", "/requests/%d" % pr["id"],
                  "/approvals/", "/purchasing/%d" % po["id"]):
        check(c.get(route).status_code == 200, "%s renders" % route)

# 8) Module 5 — sales uploads and reports
# A made-up Shopify CSV goes through the whole flow (upload → preview → save
# → re-upload), so this runs anywhere.  The checks against the real
# marketplace exports run only where those files are present: they hold
# customer data and are kept out of the repository.
SHOPIFY_CSV = (
    "Name,Created at,Financial Status,Lineitem quantity,Lineitem name,"
    "Lineitem price,Lineitem sku,Lineitem discount,Billing Name,"
    "Shipping Province\n"
    "#1001,2026-10-12 10:00:00 +0800,paid,2,Moringa Powder 100g,150,,0,"
    "Ana,Agusan del Norte\n"
    "#1002,2026-10-13 11:00:00 +0800,refunded,1,Pure Honey 250ml,399,,0,"
    "Ben,Cebu\n")
HAVE_EXPORTS = all(os.path.exists(os.path.join(_SALES, n))
                   for n in ("shopee.xlsx", "lazada.xlsx", "tiktok.xlsx"))


def upload(client, platform, name, content):
    """Upload a file and confirm its preview; returns (redirect, preview page)."""
    r = client.post("/platforms/upload", data={
        "platform": platform, "source": "upload",
        "workbook": (io.BytesIO(content), name)},
        content_type="multipart/form-data")
    loc = r.headers.get("Location", "")
    page = client.get(loc) if loc else None
    if loc:
        filename = loc.split("file=")[1].split("&")[0]
        template = [t for t in ps.all_templates() if t["platform"] == platform][0]
        client.post("/platforms/upload/preview", data={
            "file": filename, "platform": platform,
            "template_id": template["id"]})
    return r, page


with application.test_client() as c:
    login(c, "packing", "pack123")
    r, preview = upload(c, "Shopify", "shopify-orders.csv", SHOPIFY_CSV.encode())
    check(r.status_code == 302 and preview is not None
          and preview.status_code == 200 and b"Shopify" in preview.data,
          "upload goes to a preview that recognises the platform")
    rows = [x for x in db.read("platform_sales") if x["platform"] == "Shopify"]
    check(len(rows) == 2 and rows[0]["net_amount"] == 300
          and rows[0]["product_id"], "Shopify CSV saved and matched to the catalog")
    check(rows[1]["status_group"] == "Returned", "a refunded order is not a sale")
    before = len(db.read("platform_sales"))
    r, preview = upload(c, "Shopify", "shopify-orders.csv", SHOPIFY_CSV.encode())
    check(b"already uploaded" in preview.data
          and len(db.read("platform_sales")) == before,
          "re-uploading the same export adds nothing twice")
    report = ps.sales_report(grain="month")
    check(report["kpis"]["orders"] > 0 and report["periods"],
          "monthly sales report has periods and orders")
    r = c.get("/platforms/export.csv?grain=week")
    check(r.status_code == 200 and b"Total" in r.data,
          "weekly report downloads as CSV")
    for view in ("overview", "periods", "products", "orders", "uploads"):
        check(c.get("/platforms/?view=%s&range=all" % view).status_code == 200,
              "sales report view: %s" % view)

    if HAVE_EXPORTS:
        check(any(x["platform"] == "Shopee" for x in db.read("platform_sales")),
              "sample exports were imported on first run")
        shopee = [x for x in db.read("platform_sales") if x["platform"] == "Shopee"]
        check(abs(sum(x["net_amount"] for x in shopee) - 46424.0) < 0.01,
              "Shopee lines use the line subtotal, not the repeated order total")
        tiktok = [x for x in db.read("platform_sales") if x["platform"] == "TikTok"]
        check(any(x["status_group"] == "Adjustment" for x in tiktok),
              "TikTok tax lines count as adjustments, not sales")
    else:
        print("SKIP checks on the sample exports (sales-platform/*.xlsx not "
              "present — they are kept out of git)")

# 8b) Module 3, production records, facility records — pages and rules
import assets_service      # noqa: E402
import facility_service    # noqa: E402
import production_service  # noqa: E402

PAGES = {
    "assets": ["/assets/", "/assets/move", "/assets/new", "/assets/movements",
               "/assets/print/inventory", "/assets/1", "/assets/1/log",
               "/supplies/", "/supplies/move", "/supplies/count",
               "/supplies/report", "/supplies/movements",
               "/facility/sanitation", "/facility/sanitation/new",
               "/facility/sanitation/print", "/facility/pest",
               "/facility/pest/print", "/suppliers/",
               "/suppliers/new", "/suppliers/print"],
    "production": ["/production/", "/production/batch/new", "/production/batch/1",
                   "/production/batch/1/print", "/production/daily-report",
                   "/production/daily-report?view=day&all=1",
                   "/production/retention", "/production/retention/new",
                   "/production/retention/print"],
}
for user, pw in (("assets", "assets123"), ("production", "prod123")):
    with application.test_client() as c:
        login(c, user, pw)
        bad = [u for u in PAGES[user] if c.get(u).status_code != 200]
        if user == "assets":   # this month is done in the demo -> opens it
            r = c.get("/facility/pest/new")
            bad += [] if r.status_code in (200, 302) else ["/facility/pest/new"]
        check(not bad, "%s pages render (%s)" % (user, ", ".join(bad) or "all"))

# Equipment OUT then back IN, every move recorded.
heat_gun = db.query("assets", item_no="AFP-023")[0]
out_before = heat_gun.get("qty_out") or 0
move, err = assets_service.record_movement(
    heat_gun["id"], "OUT", assets_service.OUT_BORROWED, 1, person="Packing team",
    destination="Packing Area", user_id=1, notify=False)
check(not err and db.get("assets", heat_gun["id"])["qty_out"] == out_before + 1,
      "equipment OUT is recorded and counted as out")
move, err = assets_service.record_movement(
    heat_gun["id"], "IN", assets_service.IN_RETURNED, 1, person="Packing team",
    user_id=1, notify=False)
check(not err and db.get("assets", heat_gun["id"])["qty_out"] == out_before,
      "equipment IN brings it back")
check(len(db.query("asset_movements", asset_id=heat_gun["id"])) >= 2,
      "every equipment movement is logged")

# Supplies: an OUT can never exceed what is on hand.
tape_before, tape = stock("Packaging Tape")
_, err = assets_service.record_supply_movement(
    tape["id"], "OUT", tape_before + 1, assets_service.SUPPLY_OUT_REASONS[1],
    person="Packing", user_id=1, notify=False)
check(bool(err), "supply OUT larger than stock is refused")
_, err = assets_service.record_supply_movement(
    tape["id"], "OUT", 2, assets_service.SUPPLY_OUT_REASONS[1], person="Packing",
    user_id=1, notify=False)
check(not err and abs(services.current_stock("material", tape["id"])
                      - (tape_before - 2)) < 0.001, "supply OUT reduces stock")
report = assets_service.month_end_report("packaging", services.this_month())
check(bool(report), "month-end report builds")

# Batch Manufacturing Record: stock moves once, on APPROVED.
mp_before, moringa_p = stock("Moringa Powder 100 g", "product")
lines = production_service.default_dispensing(moringa_p["id"], 2)
rec, err = production_service.start_batch(
    1, moringa_p["id"], 2, services.today_iso(), "",
    services.today_iso() + "T08:00", lines,
    production_service.procedure_for(moringa_p))
check(not err and rec, "batch record started")
production_service.sign_dispensing(rec["id"], [("Ana", "Ben")] * len(lines), 1)
for i in range(len(rec["steps"])):
    production_service.sign_step(rec["id"], i, "Ana", "Ben", 1)
done, err = production_service.finish_batch(
    rec["id"], 1, services.today_iso() + "T15:00", 2,
    production_service.APPROVED, "QA Officer")
check(not err and abs(services.current_stock("product", moringa_p["id"])
                      - mp_before - 2) < 0.001,
      "an approved batch adds its yield to finished goods")
_, err = production_service.finish_batch(
    rec["id"], 1, services.today_iso() + "T15:00", 2,
    production_service.APPROVED, "QA Officer")
check(bool(err) and abs(services.current_stock("product", moringa_p["id"])
                        - mp_before - 2) < 0.001, "a batch never posts twice")

# Facility: today's pre-production check; an open repair shows up as open.
areas = [{"current_status": facility_service.CLEAN, "action": "",
          "date_undertaken": "", "remarks": ""} for _ in facility_service.LOCATIONS]
areas[2] = {"current_status": facility_service.NEEDS_REPAIR,
            "action": "Fix the leaking faucet", "date_undertaken": "",
            "remarks": ""}
open_before = facility_service.open_sanitation_count()
try:
    facility_service.save_sanitation(1, services.today_iso(), facility_service.PRE,
                                     areas, "Cleaner", "Supervisor", notify=False)
    saved = True
except facility_service.RecordError:
    saved = False
check(saved and facility_service.open_sanitation_count() == open_before + 1,
      "sanitation record saved; the open repair is tracked")

# 9) approved PR -> PO -> received against the PO -> inspection -> stock
with application.test_client() as c:
    login(c, "admin", "admin123")
    req = [r for r in db.read("purchase_requests")
           if r["status"] == "Pending Approval"
           and r["items"][0].get("material_id")][0]
    item = req["items"][0]
    c.post("/approvals/%d" % req["id"], data={"action": "approve"})
    check(not db.get("purchase_requests", req["id"])["items"][0].get("qr_token"),
          "approval no longer mints a scan-to-stock QR code")
    check(c.post("/qr/receive", json={"token": "PR-%d-1" % req["id"]}).status_code
          in (404, 405), "the scan-to-stock shortcut is gone")
    lines = [i for i in db.get("purchase_requests", req["id"])["items"]
             if i["status"] == "Ordered"]
    c.post("/purchasing/new?request_id=%d" % req["id"], data={
        "request_id": req["id"], "supplier_name": "Highland Produce Traders",
        "po_date": services.today_iso(), "payment_terms": "Cash on delivery",
        "pr_item_id": [str(i["item_id"]) for i in lines],
        "include": ["1"] * len(lines),
        "description": [i["name"] for i in lines], "brand_model": [""] * len(lines),
        "qty": [str(i["approved_qty"]) for i in lines],
        "unit": [i["unit"] for i in lines], "unit_price": ["10"] * len(lines),
        "requested_delivery": [""] * len(lines), "vat": [""] * len(lines),
        "remarks": [""] * len(lines)})
    po = db.read("purchase_orders")[-1]
    c.post("/purchasing/%d/approve" % po["id"])
with application.test_client() as c:
    login(c, "receiving", "recv123")
    page = c.get("/receiving/new").data.decode("utf-8")
    check(po["po_no"] in page and "No purchase order" in page,
          "receiving starts by choosing the purchase order")
    page = c.get("/receiving/new?po=%d" % po["id"]).data.decode("utf-8")
    check("PO line 1" in page and po["supplier_name"] in page,
          "the PO's supplier and items are filled in")
    first, second = po["items"][0], po["items"][1]
    c.post("/receiving/new?po=%d" % po["id"], data={
        "supplier_id": "", "received_date": services.today_iso(),
        "delivery_ref": "DR-PO-1",
        "po_line_no": [str(first["line_no"]), str(second["line_no"])],
        "material_id": [str(first["material_id"]), str(second["material_id"])],
        "qty_per_box": [str(first["qty"]), str(second["qty"] / 2)],
        "boxes": ["1", "1"], "lot_no": ["", ""], "expiry_date": ["", ""],
        "packaging_condition": ["", ""], "temperature_c": ["", ""]})
    batch = db.read("receiving_batches")[-1]
    boxes = db.query("receiving_boxes", batch_id=batch["id"])
    check(batch.get("po_id") == po["id"] and len(boxes) == 2
          and all(b.get("po_id") == po["id"] for b in boxes)
          and batch["supplier_name"] == po["supplier_name"],
          "the delivery and its boxes are tied to the PO lines")
with application.test_client() as c:
    login(c, "production", "prod123")
    before = services.current_stock("material", first["material_id"])
    for b in boxes:
        c.post("/receiving/box/%d/inspect" % b["id"], data={
            "usable_qty": str(b["declared_qty"]), "unusable_qty": "0",
            "damaged_qty": "0", "acceptance": "Accepted", "cleaned": "1"})
    check(services.current_stock("material", first["material_id"])
          >= before + first["qty"] - 1e-6,
          "accepted boxes add the PO items to stock")
    po_now = db.get("purchase_orders", po["id"])
    check(po_now["status"] == "Approved",
          "a PO with a line still short stays open")
    pr_now = db.get("purchase_requests", req["id"])
    check(pr_now["items"][0]["status"] == "Received"
          and abs(pr_now["items"][1]["received_qty"] - second["qty"] / 2) < 1e-6,
          "the PR shows what was received per line")
with application.test_client() as c:
    login(c, "admin", "admin123")
    page = c.get("/purchasing/%d" % po["id"]).data.decode("utf-8")
    check("being inspected" not in page and " of " in page,
          "the PO page shows received against ordered")
    c.post("/purchasing/%d/delivered" % po["id"])
    check(db.get("purchase_orders", po["id"])["status"] == "Delivered",
          "a short PO can be closed as delivered")

# 9b) the client's PO 2026-09-017 (switches, not stocked yet) received in full
demo_po = db.query("purchase_orders", po_no="2026-09-017")[0]
n = len(demo_po["items"])
with application.test_client() as c:
    login(c, "receiving", "recv123")
    page = c.get("/receiving/new?po=%d" % demo_po["id"]).data.decode("utf-8")
    check("as a new stock item" in page,
          "an ordered item not on the materials list can be added on receipt")
    c.post("/receiving/new?po=%d" % demo_po["id"], data={
        "supplier_id": "", "received_date": services.today_iso(),
        "po_line_no": [str(i["line_no"]) for i in demo_po["items"]],
        "material_id": ["new"] * n,
        "qty_per_box": [str(i["qty"]) for i in demo_po["items"]],
        "boxes": ["1"] * n, "lot_no": [""] * n, "expiry_date": [""] * n,
        "packaging_condition": [""] * n, "temperature_c": [""] * n})
    batch = db.read("receiving_batches")[-1]
    check(batch.get("po_id") == demo_po["id"]
          and db.query("raw_materials", name="Timer switch"),
          "the switches join the materials list when received")
with application.test_client() as c:
    login(c, "production", "prod123")
    for b in db.query("receiving_boxes", batch_id=batch["id"]):
        c.post("/receiving/box/%d/inspect" % b["id"], data={
            "usable_qty": str(b["declared_qty"]), "unusable_qty": "0",
            "damaged_qty": "0", "acceptance": "Accepted", "cleaned": "1"})
    check(db.get("purchase_orders", demo_po["id"])["status"] == "Delivered"
          and db.query("purchase_requests", pr_no="2026-09-017")[0]["status"] == "Received",
          "the PO is delivered and the PR received once everything passes inspection")

# 10) a request without a reason is refused
with application.test_client() as c:
    login(c, "production", "prod123")
    before = len(db.read("purchase_requests"))
    c.post("/requests/new/production",
           data={"product_id": 1, "qty": 5, "reason": "",
                 "material_id": 1, "requested_qty": 2}, follow_redirects=True)
    check(len(db.read("purchase_requests")) == before,
          "a request without a reason is refused")

# 11) customer order -> confirm deducts stock -> pay -> Paid
with application.test_client() as c:
    login(c, "packing", "pack123")
    _, product = stock("Pure Honey 250 mL", "product")
    c.post("/orders/new", data={"customer_id": 1, "discount": 0,
           "product_id": product["id"], "qty": 3, "unit_price": 399},
           follow_redirects=True)
    o = [o for o in db.read("sales_orders") if o["status"] == "Pending"][-1]
    before = services.current_stock("product", product["id"])
    c.post("/orders/%d/confirm" % o["id"], follow_redirects=True)
    check(services.current_stock("product", product["id"]) == before - 3,
          "order confirm deducted stock")
    c.post("/orders/%d/pay" % o["id"], data={"amount": o["total"]},
           follow_redirects=True)
    check(db.get("sales_orders", o["id"])["payment_status"] == "Paid",
          "order fully paid")

# 12) QR codes resolve to the right kind of record
with application.test_client() as c:
    login(c, "admin", "admin123")
    for token, kind in (("RB-1", "box"), ("HC-1", "container"),
                        ("PK-1", "packing")):
        data = c.get("/qr/lookup?token=%s" % token).get_json()
        check(bool(data) and data.get("kind") == kind,
              "%s resolves as a %s" % (token, kind))
    check(c.get("/qr/image/RB-1").status_code == 200, "QR image renders")

# 13) the MySQL export builds, including the new tables
import mysql_export            # noqa: E402

schema = mysql_export.build_schema_sql()
data_sql = mysql_export.build_data_sql()
for table in ("receiving_boxes", "packing_items", "honey_allocations",
              "assets", "asset_movements", "stock_movements",
              "month_end_count_lines", "batch_dispensing", "batch_steps",
              "retention_samples", "sanitation_areas", "pest_control_areas",
              "purchase_orders", "purchase_order_items"):
    check("CREATE TABLE `%s`" % table in schema,
          "MySQL schema has %s" % table)
check("INSERT INTO `users`" in data_sql, "MySQL data dump includes rows")

# 14) chatbot answers
with application.test_client() as c:
    login(c, "admin", "admin123")
    j = c.post("/chatbot/ask",
               json={"question": "what is low in stock?"}).get_json()
    check(bool(j and j.get("answer")), "chatbot answers")

print("\n=== %d failure(s) ===" % len(fails))
sys.exit(1 if fails else 0)
