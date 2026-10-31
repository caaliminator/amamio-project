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


def pur_orderable(req):
    import purchasing_service
    return purchasing_service.orderable_items(req)


def stock(name, kind="material"):
    table = "raw_materials" if kind == "material" else "products"
    item = db.query(table, name=name)[0]
    return services.current_stock(kind, item["id"]), item


def checklist(**fails):
    """Inspection checklist form fields: every point Pass, except those
    given as point=remark, which Fail with that remark."""
    data = {"visual_description": "Free from molds, insects, decay and rotten"}
    for key, _, _ in services.INSPECTION_POINTS:
        data["chk_" + key] = "Fail" if key in fails else "Pass"
        data["chk_%s_remarks" % key] = fails.get(key, "")
    return data


def inspect(c, box_id, **fields):
    """Post an inspection with a full checklist (fields override)."""
    data = checklist()
    data.update(fields)
    return c.post("/receiving/box/%d/inspect" % box_id, data=data,
                  follow_redirects=True)


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

# 1a) the sidebar shows only the purchasing flow, in order
with application.test_client() as c:
    login(c, "superadmin", "super123")
    page = c.get("/approvals/").data.decode("utf-8")
    nav = page.split('class="sidebar-nav"', 1)[1].split("</nav>", 1)[0]
    order = [nav.find(x) for x in ("Purchase Requests", "Approvals",
                                   "Purchase Orders", "Supplier Ledger")]
    check(all(i > 0 for i in order) and order == sorted(order)
          and "Receiving Area" not in nav and "Packing" not in nav,
          "the sidebar shows only Purchase Requests, Approvals, POs, Ledger")

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
    ("admin", "admin123", "/ledger/", 200),
    ("production", "prod123", "/ledger/", 403),
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
        "received_time": "08:30", "delivered_by": "Juan D.",
        "vehicle_plate": "ABC 1234",
        "docs_checked": ["Delivery Receipt"], "docs_other": "Packing list",
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
    check(batch["ref_no"].startswith("MRR-") and batch["received_time"] == "08:30"
          and batch["delivered_by"] == "Juan D."
          and batch["vehicle_plate"] == "ABC 1234"
          and batch["docs_checked"] == ["Delivery Receipt"]
          and batch["docs_other"] == "Packing list",
          "the delivery keeps the MRR header (time, delivered by, plate, documents)")
    page = c.get("/receiving/%d/mrr" % batch["id"]).data.decode("utf-8")
    check("AFP-WH-FRM-001" in page and "ABC 1234" in page and "Juan D." in page,
          "the Material Receiving Report prints")
    check(b"Inspection checklist" not in c.get("/receiving/box/%d" % boxes[0]["id"]).data,
          "a box not yet inspected has no checklist to print")
    inv = c.get("/inventory/").data.decode("utf-8")
    check("Awaiting inspection" in inv and "On hold" in inv,
          "stock on hand shows quantities awaiting inspection / on hold apart")

# 5) Module 1, part 2 — inspection & cleaning: usable / can't use / damaged
with application.test_client() as c:
    login(c, "production", "prod123")
    damage_before = len(db.read("damage_records"))
    # The checklist must be complete.
    c.post("/receiving/box/%d/inspect" % boxes[0]["id"], data={
        "cleaned": "1", "usable_qty": "10", "acceptance": "Accepted"})
    check(db.get("receiving_boxes", boxes[0]["id"])["status"]
          == "Awaiting Inspection", "an inspection without the checklist is refused")
    # A failed point can't be accepted.
    inspect(c, boxes[0]["id"], cleaned="1", usable_qty="10",
            acceptance="Accepted", **checklist(pest="Weevils seen"))
    check(db.get("receiving_boxes", boxes[0]["id"])["status"]
          == "Awaiting Inspection", "a box that fails a checklist point can't be accepted")
    inspect(c, boxes[0]["id"], cleaned="1", usable_qty="8.5", unusable_qty="1",
            damaged_qty="0.5", unusable_reason="Undersized",
            damage_reason="Bruised", acceptance="Accepted",
            verified_by_name="QA", test_parameter="Moisture",
            test_spec="<= 12%", test_result="10.5%", test_method="Moisture meter")
    after = services.current_stock("material", ginger["id"])
    check(abs(after - ginger_before - 8.5) < 0.001,
          "only the usable quantity joined stock")
    check(len(db.read("damage_records")) == damage_before + 2,
          "can't-be-used and damaged are both logged")
    box = db.get("receiving_boxes", boxes[0]["id"])
    check(box["status"] == "Accepted" and box["stage"] == "Storage Area",
          "accepted box moved to the Storage Area")
    check(box["inspection_no"].startswith("INS-") and len(box["checklist"]) == 11
          and box["test_result"] == "10.5%" and box["visual_inspection"] == "Pass",
          "the inspection keeps its number, the 11 checklist points and the test result")
    page = c.get("/receiving/box/%d/checklist" % box["id"]).data.decode("utf-8")
    check("AFP-WH-FRM-002" in page and "Moisture meter" in page,
          "the Incoming Material Inspection Checklist prints")
    check(db.get("receiving_batches", batch["id"])["inventory_updated_by"],
          "the MRR records who updated the inventory")
    # A missing reason is refused.
    inspect(c, boxes[1]["id"], cleaned="1", usable_qty="9", damaged_qty="1",
            acceptance="Accepted")
    check(db.get("receiving_boxes", boxes[1]["id"])["status"]
          == "Awaiting Inspection", "damage without a reason is refused")
    # Hold without saying what kind of nonconformity is refused.
    inspect(c, boxes[1]["id"], cleaned="1", usable_qty="10", acceptance="Hold",
            nonconformity="Waiting for COA",
            **checklist(coa="COA not included"))
    check(db.get("receiving_boxes", boxes[1]["id"])["status"]
          == "Awaiting Inspection", "hold without the type of nonconformity is refused")
    # Hold: nothing moves until the QA disposition on the hold report.
    unread_before = len([n for n in db.read("notifications") if not n["is_read"]])
    data = checklist(coa="COA not included")
    data.update({"cleaned": "1", "usable_qty": "10", "acceptance": "Hold",
                 "nonconformity": "Waiting for COA",
                 "nc_types": ["Missing / invalid documents"]})
    c.post("/receiving/box/%d/inspect" % boxes[1]["id"], data=data)
    held = services.current_stock("material", ginger["id"])
    check(db.get("receiving_boxes", boxes[1]["id"])["status"] == "On Hold"
          and abs(held - after) < 0.001, "a box on hold adds no stock")
    ncr = db.query("nonconformance_reports", box_id=boxes[1]["id"])
    check(len(ncr) == 1 and ncr[0]["report_no"].startswith("NCR-")
          and ncr[0]["nc_types"] == ["Missing / invalid documents"]
          and abs(ncr[0]["qty_affected"] - 10) < 1e-6
          and "COA not included" in ncr[0]["findings"],
          "a box on hold opens its hold / rejection report")
    ncr = ncr[0]
    check(len([n for n in db.read("notifications") if not n["is_read"]]) > unread_before,
          "QA and Purchasing are notified of the hold")
    check(services.unavailable_stock()[ginger["id"]]["hold"] == 10,
          "the held quantity is counted apart from stock")
    # Disposition needs the containment first.
    c.post("/receiving/ncr/%d/dispose" % ncr["id"],
           data={"disposition": "release", "qa_approved_by_name": "QA"})
    check(db.get("nonconformance_reports", ncr["id"])["status"] == "Open",
          "no disposition before the material is contained")
    c.post("/receiving/ncr/%d/contain" % ncr["id"],
           data={"containment": ["Tagged HOLD / REJECT"], "hold_location": "Q-1"})
    check(not db.get("nonconformance_reports", ncr["id"])["contained_at"],
          "containment must tag, segregate and block the material")
    c.post("/receiving/ncr/%d/contain" % ncr["id"], data={
        "containment": ["Tagged HOLD / REJECT", "Segregated in designated area",
                        "Production use blocked", "Supplier notified"],
        "hold_location": "Quarantine rack Q-1"})
    check(db.get("nonconformance_reports", ncr["id"])["hold_location"]
          == "Quarantine rack Q-1", "containment recorded")
    c.post("/receiving/ncr/%d/dispose" % ncr["id"],
           data={"disposition": "conditional", "qa_approved_by_name": "QA"})
    check(db.get("nonconformance_reports", ncr["id"])["status"] == "Open",
          "conditional acceptance without a justification is refused")
    c.post("/receiving/ncr/%d/dispose" % ncr["id"], data={
        "disposition": "rework", "accepted_qty": "9",
        "justification": "Sorted out the soft roots",
        "disposition_details": "COA received by e-mail",
        "qa_approved_by_name": "QA Officer"})
    check(abs(services.current_stock("material", ginger["id"]) - held - 9)
          < 0.001, "rework / sort releases only what passed")
    box = db.get("receiving_boxes", boxes[1]["id"])
    check(box["status"] == "Accepted" and abs(box["unusable_qty"] - 1) < 1e-6,
          "the rest of the held box is written off as sorted out")
    c.post("/receiving/ncr/%d/close" % ncr["id"],
           data={"closure_verified_by_name": "Warehouse"})
    check(db.get("nonconformance_reports", ncr["id"])["status"] == "Closed",
          "the hold report is closed")
    page = c.get("/receiving/ncr/%d/print" % ncr["id"]).data.decode("utf-8")
    check("AFP-WH-FRM-003" in page and "Quarantine rack Q-1" in page,
          "the Hold / Rejection Report prints")
    # Rejected: its report can't release the box.
    inspect(c, boxes[2]["id"], cleaned="1", usable_qty="0", damaged_qty="100",
            damage_reason="Crushed", acceptance="Rejected",
            nonconformity="Crushed in transit", nc_types="Damaged packaging",
            **checklist(packaging="Carton crushed"))
    ncr2 = db.query("nonconformance_reports", box_id=boxes[2]["id"])[0]
    c.post("/receiving/ncr/%d/contain" % ncr2["id"], data={
        "containment": services.NC_CONTAINMENT[:3], "hold_location": "Reject bay"})
    c.post("/receiving/ncr/%d/dispose" % ncr2["id"],
           data={"disposition": "release", "qa_approved_by_name": "QA"})
    check(db.get("nonconformance_reports", ncr2["id"])["status"] == "Open",
          "a rejected box can't be released")
    c.post("/receiving/ncr/%d/dispose" % ncr2["id"],
           data={"disposition": "return", "qa_approved_by_name": "QA"})
    check(db.get("nonconformance_reports", ncr2["id"])["disposition"] == "return"
          and db.get("receiving_boxes", boxes[2]["id"])["status"] == "Rejected",
          "a rejected box is returned to the supplier")
    r = c.get("/receiving/report?from=2000-01-01")
    check(r.status_code == 200 and b"RECEIVING AND INSPECTION" in r.data.upper(),
          "receiving & inspection report renders")
    page = r.data.decode("utf-8")
    today = services.today_iso()
    check(services.fmt_mdy(today) in page and "Document No.:" in page
          and "Free from molds, insects, decay and rotten" in page,
          "the report prints MM-DD-YY dates, the document header and the visual description")
    lot_a = [row for row in services.receiving_report_rows(today, today)
             if row["lot_no"] == "LOT-A"]
    check(len(lot_a) == 1 and lot_a[0]["qty"] == 20 and lot_a[0]["boxes"] == 2,
          "the report has one row per item and lot, its boxes added up")
    check(db.get("receiving_boxes", boxes[2]["id"])["action_taken"] == "Rejected"
          and "Accepted" in lot_a[0]["action"],
          "Action Taken is filled in with the decision when left blank")
    _, extra_boxes, _ = services.receive_delivery(
        1, None, today, [{"material_id": ginger["id"], "qty_per_box": 5, "boxes": 1}])
    no_desc = checklist()
    no_desc.update({"visual_description": "", "usable_qty": "5",
                    "acceptance": "Accepted", "cleaned": "1"})
    c.post("/receiving/box/%d/inspect" % extra_boxes[0]["id"], data=no_desc)
    check(db.get("receiving_boxes", extra_boxes[0]["id"])["status"]
          == "Awaiting Inspection",
          "an inspection without the visual / organoleptic description is refused")
    inspect(c, extra_boxes[0]["id"], usable_qty="5", acceptance="Accepted",
            cleaned="1")

# 5b) closing the receipt — the SOP's Quick Control Check
with application.test_client() as c:
    login(c, "receiving", "recv123")
    check(db.get("receiving_batches", batch["id"])["status"] == "For Closing",
          "a delivery with every box inspected waits to be closed")
    page = c.get("/receiving/%d/status-labels" % batch["id"]).data.decode("utf-8")
    check("ACCEPTED" in page and "REJECTED" in page,
          "status labels print ACCEPTED / REJECTED")
    checks = {"check_" + k: "Yes" for k, _ in services.CONTROL_CHECKS}
    c.post("/receiving/%d/close" % batch["id"],
           data=dict(checks, check_status_label="No", verified_by_name="Ana"))
    check(db.get("receiving_batches", batch["id"])["status"] == "For Closing",
          "a No on the control check keeps the receipt open")
    c.post("/receiving/%d/close" % batch["id"], data=dict(checks))
    check(db.get("receiving_batches", batch["id"])["status"] == "For Closing",
          "closing needs who verified the receipt")
    c.post("/receiving/%d/close" % batch["id"],
           data=dict(checks, check_lot_dates="N/A", verified_by_name="Ana"))
    closed = db.get("receiving_batches", batch["id"])
    check(closed["status"] == "Completed" and closed["checked_by_name"] == "Ana"
          and closed["control_check"]["lot_dates"] == "N/A",
          "the receipt closes after the Quick Control Check")
    for path in ("/receiving/ncr", "/receiving/ncr?show=all",
                 "/receiving/%d" % batch["id"], "/receiving/box/%d" % boxes[1]["id"],
                 "/dashboard"):
        check(c.get(path).status_code == 200, "%s renders" % path)

# 5c) the Approved Supplier List (SOP section 6)
unapproved = db.insert("suppliers", {"name": "Not Yet Approved Farm",
                                     "category": "Raw", "approved": False})
_, ginger = stock("Ginger")
with application.test_client() as c:
    login(c, "receiving", "recv123")
    before = len(db.read("receiving_batches"))
    c.post("/receiving/new", data={
        "supplier_id": str(unapproved["id"]), "received_date": services.today_iso(),
        "material_id": [ginger["id"]], "qty_per_box": ["5"], "boxes": ["1"]})
    check(len(db.read("receiving_batches")) == before,
          "a delivery from a supplier not on the approved list is refused")
    check(c.post("/suppliers/%d/approval" % unapproved["id"],
                 data={"approved": "1"}).status_code == 403,
          "only an Admin approves a supplier")
with application.test_client() as c:
    login(c, "admin", "admin123")
    c.post("/suppliers/%d/approval" % unapproved["id"], data={"approved": "1"})
    check(services.supplier_approved(db.get("suppliers", unapproved["id"])),
          "an Admin puts the supplier on the approved list")
    check(b"Approved supplier" in c.get("/suppliers/%d" % unapproved["id"]).data,
          "the supplier page shows it is approved")
with application.test_client() as c:
    login(c, "receiving", "recv123")
    c.post("/receiving/new", data={
        "supplier_id": str(unapproved["id"]), "received_date": services.today_iso(),
        "material_id": [ginger["id"]], "qty_per_box": ["5"], "boxes": ["1"]})
    check(len(db.read("receiving_batches")) == before + 1,
          "once approved, its deliveries are received")

# 5f) Products In / Out — finished goods taken in or issued out by hand
with application.test_client() as c:
    login(c, "receiving", "recv123")
    check(c.get("/products-in-out/").status_code == 403,
          "Receiving can't open Products In / Out")
with application.test_client() as c:
    login(c, "packing", "pack123")
    check(b"Products In / Out" in c.get("/dashboard").data,
          "Products In / Out shows in the sidebar")
    p_before, prod = stock("Pure Honey 250 mL", "product")
    c.post("/products-in-out/move", data={
        "direction": "IN", "product_id": prod["id"], "qty": "4",
        "reason": "Returned unused", "person": "Sales team"})
    check(services.current_stock("product", prod["id"]) == p_before + 4,
          "products IN adds to stock")
    c.post("/products-in-out/move", data={
        "direction": "OUT", "product_id": prod["id"], "qty": "999999",
        "reason": "Samples / giveaway"})
    check(services.current_stock("product", prod["id"]) == p_before + 4,
          "products OUT can't issue more than is in stock")
    damage_before = len(db.read("damage_records"))
    c.post("/products-in-out/move", data={
        "direction": "OUT", "product_id": prod["id"], "qty": "1",
        "reason": "Damaged / expired", "notes": "Cracked jar"})
    check(services.current_stock("product", prod["id"]) == p_before + 3
          and len(db.read("damage_records")) == damage_before + 1,
          "products OUT damaged lowers stock and goes on the damage report")
    moves = db.query("product_movements", product_id=prod["id"])
    check(len(moves) == 2 and any(t.get("txn_type") == "stock_out"
                                  for t in db.query("inventory_transactions",
                                                    reference_id="PM-%d" % moves[-1]["id"])),
          "each product movement is on the stock ledger")
    for path in ("/products-in-out/", "/products-in-out/log",
                 "/products-in-out/move?product_id=%d&direction=OUT" % prod["id"]):
        check(c.get(path).status_code == 200, "%s renders" % path)
    # The Products IN / OUT report: opening + IN - OUT = closing, by reason.
    rep = services.product_in_out_report(services.today_iso(), services.today_iso())
    row = [x for x in rep["rows"] if x["product_name"] == "Pure Honey 250 mL"][0]
    check(abs(row["opening"] + row["total_in"] - row["total_out"] - row["closing"]) < 1e-6
          and abs(row["closing"] - services.current_stock("product", prod["id"])) < 1e-6
          and dict(row["in_detail"]).get("Returned unused") == 4
          and dict(row["out_detail"]).get("Damaged / expired") == 1,
          "products report: opening + IN - OUT = closing, split by reason")
    page = c.get("/products-in-out/report").data.decode("utf-8")
    check("Products IN / OUT Report" in page and "Returned unused" in page,
          "the Products IN / OUT report prints")
    import zipfile as _zip
    r = c.get("/products-in-out/report.xlsx")
    book = _zip.ZipFile(io.BytesIO(r.data))
    check(r.status_code == 200 and "xl/worksheets/sheet3.xml" in book.namelist()
          and b"Returned unused" in book.read("xl/worksheets/sheet3.xml"),
          "the Products IN / OUT report downloads as Excel")
    r = c.get("/products-in-out/report.csv")
    check(r.status_code == 200 and "Pure Honey 250 mL" in r.data.decode("utf-8-sig"),
          "the Products IN / OUT report downloads as CSV")
with application.test_client() as c:
    login(c, "assets", "assets123")
    page = c.get("/supplies/movements?from=2000-01-01").data.decode("utf-8")
    check("Supplies IN / OUT" in page and "Balance after" in page,
          "the supplies IN / OUT log has a date range and prints")
    r = c.get("/supplies/movements.xlsx?from=2000-01-01")
    check(r.status_code == 200 and r.data[:2] == b"PK",
          "the supplies IN / OUT log downloads as Excel")
    check(c.get("/supplies/movements.csv?from=2000-01-01").status_code == 200,
          "the supplies IN / OUT log downloads as CSV")

# 5d) every box on hold or rejected has its report — even older ones
legacy = db.query("receiving_boxes", batch_id=batch["id"])[2]
db.write("nonconformance_reports", [r for r in db.read("nonconformance_reports")
                                    if r.get("box_id") != legacy["id"]])
check(services.backfill_ncrs() >= 1
      and len(db.query("nonconformance_reports", box_id=legacy["id"])) == 1,
      "a held / rejected box without a report gets one")
check(services.backfill_ncrs() == 0, "the backfill never makes a second report")

# 5e) FEFO / FIFO: the box that expires first leaves storage first
qa = db.query("users", username="production")[0]["id"]
fefo_batch, fefo_boxes, _ = services.receive_delivery(
    qa, None, services.today_iso(), [
        {"material_id": ginger["id"], "qty_per_box": 5, "boxes": 1,
         "expiry_date": "2099-12-20"},
        {"material_id": ginger["id"], "qty_per_box": 5, "boxes": 1,
         "expiry_date": "2099-12-05"}])
for b in fefo_boxes:
    services.inspect_receiving_box(b["id"], qa, 5, acceptance="Accepted")
later, sooner = fefo_boxes
with application.test_client() as c:
    login(c, "production", "prod123")
    c.post("/receiving/box/%d/move" % later["id"], data={"stage": "Production Area"})
    check(db.get("receiving_boxes", later["id"])["stage"] == "Storage Area",
          "a box can't go to production while one expiring earlier is in storage")
    check(b"Use %s first" % sooner["box_code"].encode()
          in c.get("/receiving/box/%d" % later["id"]).data,
          "the box page says which box to use first")
    c.post("/receiving/box/%d/move" % sooner["id"], data={"stage": "Production Area"})
    c.post("/receiving/box/%d/move" % later["id"], data={"stage": "Production Area"})
    check(db.get("receiving_boxes", sooner["id"])["stage"] == "Production Area"
          and db.get("receiving_boxes", later["id"])["stage"] == "Production Area",
          "after the earlier box, the later one can go")

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
    page = c.get("/requests/new").data.decode("utf-8")
    check('name="material_id"' in page and '<select name="preferred_supplier"' in page
          and 'name="requested_by_name"' in page and "Expected date" in page,
          "PR form has the item and supplier dropdowns, expected date and requestor")
    c.post("/requests/new", data={
        "department": "Production", "purpose": "Spare parts for the sealer",
        "needed_by": "", "notes": "Heating element burnt", "urgency": "Urgent",
        "requested_by_name": "Aldren Ocate",
        "material_id": ["other", "other"],
        "description": ["Heating element", "Teflon tape"],
        "purpose_use": ["Band sealer", "Band sealer"], "qty": ["2", "3"],
        "unit": ["pc", "roll"], "est_unit_cost": ["450", "80"],
        "preferred_supplier": ["Trimix Butuan", ""],
        "budget_category": ["Equipment / repair", ""], "remark": ["", ""]})
    pr = db.read("purchase_requests")[-1]
    check(pr.get("purpose") == "Spare parts for the sealer"
          and len(pr["items"]) == 2 and abs(pr["total_estimated"] - 1140) < 0.01
          and pr["pr_no"].startswith(services.today_iso()[:7])
          and pr["requested_by_name"] == "Aldren Ocate",
          "PR saved with its number, items, estimated total and requestor")
    sugar = db.query("raw_materials", name="Sugar") or db.read("raw_materials")[:1]
    c.post("/requests/new", data={
        "department": "Production", "purpose": "Restock", "requested_by_name": "",
        "material_id": [str(sugar[0]["id"])], "description": [""], "qty": ["1"],
        "unit": ["kg"], "est_unit_cost": [""], "preferred_supplier": [""],
        "budget_category": [""], "remark": [""], "purpose_use": [""]})
    picked = db.read("purchase_requests")[-1]
    check(picked["items"][0]["name"] == sugar[0]["name"]
          and picked["items"][0]["material_id"] == sugar[0]["id"]
          and picked["requested_by_name"],
          "an item picked from the dropdown is saved as that stocked item")
    check(c.get("/requests/%d/print" % pr["id"]).status_code == 200,
          "PR prints as the Purchase Request Form")
    check(c.get("/purchasing/").status_code == 403,
          "a team head cannot open purchase orders")
admin_name = db.query("users", username="admin")[0]["name"]
super_name = db.query("users", username="superadmin")[0]["name"]
with application.test_client() as c:
    login(c, "superadmin", "super123")
    c.post("/approvals/%d" % pr["id"], data={"action": "approve"})
    check(db.get("purchase_requests", pr["id"])["status"] == "Pending Approval",
          "the Super Admin can't approve before the Admin has checked it")
with application.test_client() as c:
    login(c, "admin", "admin123")
    c.post("/approvals/%d" % pr["id"], data={"action": "approve"})
    pr = db.get("purchase_requests", pr["id"])
    check(pr["status"] == "For Final Approval" and pr["checked_by_name"] == admin_name
          and not pr["approved_by_name"] and not pur_orderable(pr),
          "the Admin's check signs Checked by and sends it for final approval")
    c.post("/approvals/%d" % pr["id"], data={"action": "approve"})
    check(db.get("purchase_requests", pr["id"])["status"] == "For Final Approval",
          "the Admin can't give the final approval")
with application.test_client() as c:
    login(c, "superadmin", "super123")
    check(pr["pr_no"] in c.get("/approvals/").data.decode("utf-8"),
          "the checked PR is in the Super Admin's queue")
    c.post("/approvals/%d" % pr["id"], data={"action": "approve"})
    pr = db.get("purchase_requests", pr["id"])
    check(pr["status"] == "Approved" and pr["approved_by_name"] == super_name
          and pr["checked_by_name"] == admin_name,
          "the Super Admin's final approval signs Approved by")
trimix = db.query("suppliers", name="Trimix Butuan")[0]
with application.test_client() as c:
    login(c, "admin", "admin123")
    check(c.get("/purchasing/new?request_id=%d" % pr["id"]).status_code == 200,
          "PO form opens for the approved PR")
    pos_before = len(db.read("purchase_orders"))
    c.post("/purchasing/new?request_id=%d" % pr["id"], data={
        "request_id": pr["id"], "supplier_name": "Typed Supplier",
        "po_date": services.today_iso(), "payment_terms": "Cash on delivery",
        "pr_item_id": ["1"], "include": ["1"], "description": ["Heating element"],
        "qty": ["2"], "unit": ["pc"], "unit_price": ["430"]})
    check(len(db.read("purchase_orders")) == pos_before,
          "a PO needs a supplier from the Supplier Directory")
    c.post("/purchasing/new?request_id=%d" % pr["id"], data={
        "request_id": pr["id"], "supplier_id": str(trimix["id"]),
        "po_date": services.today_iso(), "payment_terms": "Advance payment",
        "advance_amount": "", "pr_item_id": ["1"], "include": ["1"],
        "description": ["Heating element"], "qty": ["2"], "unit": ["pc"],
        "unit_price": ["430"]})
    check(len(db.read("purchase_orders")) == pos_before,
          "advance payment terms need the amount paid up front")
    c.post("/purchasing/new?request_id=%d" % pr["id"], data={
        "request_id": pr["id"], "supplier_id": str(trimix["id"]),
        "supplier_address": "Butuan", "po_date": services.today_iso(),
        "payment_terms": "Advance payment", "advance_amount": "500",
        "deliver_to": "Amamio Farm Products",
        "delivery_address": "Nasipit", "freight": "60", "labor": "150",
        "pr_item_id": ["1", "2"], "include": ["1", "0"],
        "description": ["Heating element", "Teflon tape"], "brand_model": ["", ""],
        "qty": ["2", "3"], "unit": ["pc", "roll"], "unit_price": ["430", "80"],
        "requested_delivery": ["", ""], "vat": ["", ""], "remarks": ["", ""],
        "prepared_by": "Someone Else", "checked_by": "Someone Else"})
    po = db.read("purchase_orders")[-1]
    pr = db.get("purchase_requests", pr["id"])
    check(po["po_no"] == pr["pr_no"] and len(po["items"]) == 1
          and abs(po["grand_total"] - 1070) < 0.01 and po["labor"] == 150
          and po["supplier_id"] == trimix["id"] and po["advance_amount"] == 500,
          "PO prepared from the approved lines, with labor cost and advance payment")
    check(po["prepared_by_name"] == admin_name and not po["checked_by_name"],
          "Prepared by is the person signed in; Checked by waits for the check")
    check(pr["status"] == "Approved" and pr["items"][0]["po_no"] == po["po_no"]
          and not pr["items"][1].get("po_id"),
          "the PR shows which line is on the PO and which still needs one")
    import purchasing_service as pur_svc  # noqa: E402
    check(not any(e["ref"] == po["po_no"] for e in pur_svc.ledger_entries(trimix["id"])),
          "a PO is not on the ledger before it is approved")
    c.post("/purchasing/%d/approve" % po["id"])
    po = db.get("purchase_orders", po["id"])
    check(po["status"] == "For Final Approval" and po["checked_by_name"] == admin_name,
          "the Admin's check of the PO signs Checked by")
    c.post("/purchasing/%d/approve" % po["id"])
    check(db.get("purchase_orders", po["id"])["status"] == "For Final Approval",
          "the Admin can't give the PO its final approval")
with application.test_client() as c:
    login(c, "superadmin", "super123")
    check(po["po_no"] in c.get("/approvals/").data.decode("utf-8"),
          "the checked PO is in the Super Admin's queue")
    c.post("/purchasing/%d/approve" % po["id"])
    po = db.get("purchase_orders", po["id"])
    check(po["status"] == "Approved" and po["approved_by_name"] == super_name,
          "the Super Admin's final approval signs Approved by and issues the PO")
with application.test_client() as c:
    login(c, "admin", "admin123")
    page = c.get("/purchasing/%d/print" % po["id"]).data.decode("utf-8")
    check("Purchase Order" in page and super_name in page and "Labor Cost" in page,
          "PO prints as the Purchase Order form")
    # Supplier ledger: the issued PO is a credit; payments are debits.
    before = pur_svc.ledger_entries(trimix["id"])
    check(any(e["ref"] == po["po_no"] and abs(e["credit"] - 1070) < 0.01 for e in before),
          "the issued PO is posted to the supplier's ledger")
    page = c.get("/ledger/%d?po=%d" % (trimix["id"], po["id"])).data.decode("utf-8")
    check(po["po_no"] in page and 'value="500.00"' in page,
          "the ledger offers the advance payment that is due")
    c.post("/ledger/%d/pay" % trimix["id"], data={
        "po_id": str(po["id"]), "amount": "5000", "kind": "Full payment",
        "method": "Cash"})
    check(pur_svc.po_paid(po["id"]) == 0, "a payment can't be more than the PO's balance")
    c.post("/ledger/%d/pay" % trimix["id"], data={
        "po_id": str(po["id"]), "amount": "500", "kind": "Advance payment",
        "method": "GCash", "reference": "GC-1001"})
    c.post("/ledger/%d/pay" % trimix["id"], data={
        "po_id": str(po["id"]), "amount": "200", "kind": "Partial payment",
        "method": "Cash"})
    summary = pur_svc.po_payment_summary(db.get("purchase_orders", po["id"]))
    entries = pur_svc.ledger_entries(trimix["id"])
    check(summary["paid"] == 700 and abs(summary["balance"] - 370) < 0.01
          and abs(entries[-1]["balance"] - (before[-1]["balance"] - 700)) < 0.01,
          "advance and partial payments lower the PO's and the supplier's balance")
    row = [r for r in pur_svc.ledger_summary() if r["id"] == trimix["id"]][0]
    check(abs(row["balance"] - entries[-1]["balance"]) < 0.01,
          "the ledger list shows the same balance as the supplier's ledger")
    for route in ("/ledger/", "/ledger/?show=all", "/ledger/%d" % trimix["id"]):
        check(c.get(route).status_code == 200, "%s renders" % route)
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
with application.test_client() as c:
    login(c, "superadmin", "super123")
    c.post("/approvals/%d" % req["id"], data={"action": "approve"})
with application.test_client() as c:
    login(c, "admin", "admin123")
    check(not db.get("purchase_requests", req["id"])["items"][0].get("qr_token"),
          "approval no longer mints a scan-to-stock QR code")
    check(c.post("/qr/receive", json={"token": "PR-%d-1" % req["id"]}).status_code
          in (404, 405), "the scan-to-stock shortcut is gone")
    lines = [i for i in db.get("purchase_requests", req["id"])["items"]
             if i["status"] == "Ordered"]
    c.post("/purchasing/new?request_id=%d" % req["id"], data={
        "request_id": req["id"],
        "supplier_id": str(db.query("suppliers", name="Highland Produce Traders")[0]["id"]),
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
    login(c, "superadmin", "super123")
    c.post("/purchasing/%d/approve" % po["id"])
    po = db.get("purchase_orders", po["id"])
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
    check(any("PO line %d" % second["line_no"] in d and "short by" in d
              for d in batch.get("discrepancies") or []),
          "a short delivery against the PO is noted on the MRR")
with application.test_client() as c:
    login(c, "production", "prod123")
    before = services.current_stock("material", first["material_id"])
    for b in boxes:
        inspect(c, b["id"], **{
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

# 9a) a PO for an older production request, whose lines only carry the
# material id, gets the item names from the materials list
_lemon, _ginger = (db.query("raw_materials", name=n)[0] for n in ("Lemon", "Ginger"))
lines = [{"item_id": n, "material_id": m["id"], "requested_qty": 5.0,
          "approved_qty": 5, "status": "Ordered", "note": ""}
         for n, m in ((1, _lemon), (2, _ginger))]
old = [db.insert("purchase_requests", {
    "type": "production", "status": "Approved", "items": lines,
    "requestor_id": db.query("users", username="production")[0]["id"],
    "created_at": services.now_iso()})]
with application.test_client() as c:
    login(c, "admin", "admin123")
    check(c.get("/purchasing/new?request_id=%d" % old[0]["id"]).status_code == 200,
          "the PO form opens for an older production request")
    c.post("/purchasing/new?request_id=%d" % old[0]["id"], data={
        "request_id": old[0]["id"], "supplier_id": str(db.read("suppliers")[0]["id"]),
        "payment_terms": "Cash on delivery",
        "pr_item_id": [str(i["item_id"]) for i in lines],
        "include": ["1"] * len(lines), "description": [""] * len(lines),
        "qty": [str(i["requested_qty"]) for i in lines],
        "unit": ["pc"] * len(lines), "unit_price": ["1"] * len(lines)})
po = db.read("purchase_orders")[-1]
check(po["purchase_request_id"] == old[0]["id"]
      and [i["description"] for i in po["items"]] == ["Lemon", "Ginger"],
      "a PO for an older production request is named from the materials")

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
        inspect(c, b["id"], **{
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
              "purchase_orders", "purchase_order_items",
              "nonconformance_reports"):
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
