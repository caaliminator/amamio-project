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
    """Inspection checklist form fields: every point (of both lanes' lists)
    Pass, except those given as point=remark, which Fail with that remark."""
    # Date marking "Pass" needs the expiry date (picked on a calendar).
    data = {"visual_description": "Free from molds, insects, decay and rotten",
            "expiry_date": "2099-12-31"}
    for key, _, _ in services.INSPECTION_POINTS + services.SUPPLY_INSPECTION_POINTS:
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
            ("assets", "assets123"), ("finance", "finance123"),
            ("service", "service123"), ("marketing", "market123")]

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
                                   "Purchase Orders", "Finance", "Supplier Ledger",
                                   "Payments Due", "Cash &amp; Bank Accounts")]
    check(all(i > 0 for i in order) and order == sorted(order)
          and "Receiving Area" not in nav and "Packing" not in nav,
          "the sidebar shows the purchasing flow, then Finance")
with application.test_client() as c:
    login(c, "finance", "finance123")
    page = c.get("/finance/due").data.decode("utf-8")
    nav = page.split('class="sidebar-nav"', 1)[1].split("</nav>", 1)[0]
    check("Supplier Ledger" in nav and "Payments Due" in nav and "Purchase Orders" in nav
          and "Purchase Requests" not in nav and "Approvals" not in nav,
          "the Finance Admin's sidebar is the Finance section and read-only POs")

# 1b) each user picks light, dark or "match my device"; it is remembered
with application.test_client() as c:
    login(c, "packer1", "member123")
    check(b'data-theme="light"' in c.get("/dashboard").data, "light theme by default")
    c.post("/settings/theme", data={"theme": "dark"})
    check(b'data-theme="dark"' in c.get("/dashboard").data,
          "dark theme is saved and applied")
    c.post("/settings/theme", data={"theme": "system"})
    page = c.get("/dashboard").data
    check(b'data-theme-pref="system"' in page and b'data-theme="' not in page,
          "match-my-device leaves the choice to the device")
    check(c.post("/settings/theme", data={"theme": "pink"}).status_code == 400,
          "an unknown theme is refused")
with application.test_client() as c:
    login(c, "packer2", "member123")
    check(b'data-theme="light"' in c.get("/dashboard").data,
          "one user's theme does not change another's")
with application.test_client() as c:
    check(b'data-theme="light"' in c.get("/login").data, "the sign-in page is light too")

# 1c) every page has an "About this page" note, and users can hide them
import page_notes             # noqa: E402

NOT_PAGES = {"static", "auth.root", "auth.login", "auth.logout",
             "honey.suggest", "qr.lookup", "qr.image", "qr.go",
             "platforms.export_csv", "platforms.import_file",
             "migration.download", "requests.bom_calc", "production.new",
             "requests.new_material", "auth.user_image", "ledger.proof",
             "ledger.attachment",
             "receiving.delivery_proof", "receiving.box_photo"}
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
    # Packing Team Head: packing only.
    ("packing", "pack123", "/packing/new", 200),
    ("packing", "pack123", "/orders/", 403),
    ("packing", "pack123", "/customers/", 403),
    ("packing", "pack123", "/deliveries/", 403),
    ("packing", "pack123", "/platforms/", 403),
    ("packing", "pack123", "/sales/", 403),
    ("packing", "pack123", "/products-in-out/", 403),
    # Customer Service Admin: customers, sales orders, deliveries.
    ("service", "service123", "/orders/", 200),
    ("service", "service123", "/orders/new", 200),
    ("service", "service123", "/customers/", 200),
    ("service", "service123", "/deliveries/", 200),
    ("service", "service123", "/inventory/", 200),
    ("service", "service123", "/platforms/", 403),
    ("service", "service123", "/sales/", 403),
    ("service", "service123", "/packing/", 403),
    ("service", "service123", "/ledger/", 403),
    ("service", "service123", "/finance/accounts", 403),
    ("service", "service123", "/requests/", 403),
    ("service", "service123", "/approvals/", 403),
    ("service", "service123", "/receiving/", 403),
    ("service", "service123", "/purchasing/", 403),
    ("service", "service123", "/reports/", 403),
    ("service", "service123", "/qr/scan", 403),
    # Sales Marketing Admin: sales uploads & reports, stock release.
    ("marketing", "market123", "/platforms/", 200),
    ("marketing", "market123", "/platforms/upload", 200),
    ("marketing", "market123", "/sales/", 200),
    ("marketing", "market123", "/sales/new", 200),
    ("marketing", "market123", "/orders/", 403),
    ("marketing", "market123", "/customers/", 403),
    ("marketing", "market123", "/deliveries/", 403),
    ("marketing", "market123", "/packing/", 403),
    ("marketing", "market123", "/ledger/", 403),
    ("marketing", "market123", "/requests/", 403),
    ("marketing", "market123", "/approvals/", 403),
    ("marketing", "market123", "/analytics/", 403),
    # Finance Admin: finance pages only.
    ("finance", "finance123", "/inventory/", 403),
    ("finance", "finance123", "/qr/scan", 403),
    ("finance", "finance123", "/orders/", 403),
    ("finance", "finance123", "/platforms/", 403),
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
    ("production", "prod123", "/finance/accounts", 403),
    ("finance", "finance123", "/ledger/", 200),
    ("finance", "finance123", "/finance/due", 200),
    ("finance", "finance123", "/finance/accounts", 200),
    ("finance", "finance123", "/purchasing/", 200),
    ("finance", "finance123", "/purchasing/new", 403),
    ("finance", "finance123", "/approvals/", 403),
    ("finance", "finance123", "/requests/", 403),
    ("finance", "finance123", "/receiving/", 403),
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

# 3a) The purchase request list is split by stage, urgency in its own colour
with application.test_client() as c:
    login(c, "superadmin", "super123")
    page = c.get("/requests/?show=all").data.decode("utf-8")
    check(page.count('class="prt-card ') >= 1 and 'class="prt-divider ' in page
          and 'class="pr-stages"' in page and 'class="prt-children"' in page
          and "urg-chip urg-" in page and "st-chip tone-" in page,
          "the purchase request table: stage dividers, request cards with their items, coloured status / urgency")

# 3b) Role-based menus: each function admin and the packing team see only
#     their own job's pages
MENUS = {
    ("service", "service123"): (["Sales Orders", "Deliveries", "Customers", "Stock on Hand"],
                                ["Sales Uploads", "Stock Release", "Packing &amp; Dispatch",
                                 "Supplier Ledger", "Purchase Requests", "Approvals"]),
    ("marketing", "market123"): (["Sales Uploads &amp; Reports", "Stock Release", "Stock on Hand"],
                                 ["Sales Orders", "Customers", "Deliveries", "Packing &amp; Dispatch",
                                  "Supplier Ledger", "Purchase Requests", "Approvals"]),
    ("packing", "pack123"): (["Record Packing", "Packing &amp; Dispatch", "Purchase Requests"],
                             ["Sales Orders", "Customers", "Sales Uploads", "Stock Release",
                              "Products In / Out"]),
    ("finance", "finance123"): (["Payments Due", "Supplier Ledger", "Cash &amp; Bank Accounts"],
                                ["Stock on Hand", "Sales Orders", "Sales Uploads", "Scan a QR"]),
}
for (u, pw), (shown, hidden) in MENUS.items():
    with application.test_client() as c:
        login(c, u, pw)
        page = c.get("/dashboard").data.decode("utf-8")
        nav = page[page.find("<nav"):page.find("</nav>")]
        check(all(x in nav for x in shown) and not any(x in nav for x in hidden),
              "%s's menu has only its own pages" % u)
with application.test_client() as c:
    login(c, "superadmin", "super123")
    page = c.get("/users").data.decode("utf-8") if c.get("/users").status_code == 200 \
        else c.get("/auth/users").data.decode("utf-8")
    check("Customer Service Admin" in page and "Sales Marketing Admin" in page,
          "the two new roles can be given to a user")
svc_user = db.query("users", username="service")[0]
mkt_user = db.query("users", username="marketing")[0]
pack_user = db.query("users", username="packing")[0]
n_svc = len(db.query("notifications", user_id=svc_user["id"]))
n_pack = len(db.query("notifications", user_id=pack_user["id"]))
_honey = db.query("products", name="Pure Honey 250 mL")[0]
services.create_sales_order(db.read("customers")[0]["id"],
                            [{"product_id": _honey["id"], "qty": 1, "unit_price": 399}],
                            user_id=svc_user["id"])
check(len(db.query("notifications", user_id=svc_user["id"])) == n_svc + 1
      and len(db.query("notifications", user_id=pack_user["id"])) == n_pack,
      "a new sales order notifies Customer Service, not the Packing Team Head")
with application.test_client() as c:
    login(c, "marketing", "market123")
    c.post("/sales/new", data={"product_id": str(_honey["id"]), "qty": "2", "remarks": "Shopee promo"})
    mine = [r for r in db.read("sales_requests") if r.get("requestor_id") == mkt_user["id"]]
    check(mine and mine[-1]["status"] == "Pending Approval",
          "the Sales Marketing Admin requests a stock release")

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
    ginger_batch_no = boxes[0]["lot_no"]
    ym = services.today_iso()[:7]
    check(ginger_batch_no.startswith("GINGER-%s-" % ym) and len(ginger_batch_no.split("-")[-1]) == 3
          and boxes[1]["lot_no"] == ginger_batch_no and boxes[0]["supplier_lot"] == "LOT-A"
          and boxes[2]["lot_no"].startswith("LEMON-%s-" % ym),
          "each item gets a batch no. ITEM-YEAR-MONTH-NNN (its boxes share it; the supplier lot is kept)")
    payload = services.qr_payload(boxes[0]["box_code"])
    check("Ginger" in payload and "10 kg" in payload and ginger_batch_no in payload,
          "QR label carries the item, quantity and batch no.")
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
    check(services.fmt_mdy(today) in page and "Doc. No." in page
          and "Free from molds, insects, decay and rotten" in page,
          "the report prints MM-DD-YY dates, the document header and the visual description")
    lot_a = [row for row in services.receiving_report_rows(today, today)
             if row["lot_no"] == ginger_batch_no]
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
    check(c.get("/products-in-out/").status_code == 403,
          "the Packing Team Head (packing only) can't open Products In / Out")
with application.test_client() as c:
    login(c, "production", "prod123")
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
    import purchasing_service as _pur  # noqa: E402
    pid = processing[0]["id"]
    check(all(r["id"] != pid for _, rows in _pur.item_choices() for r in rows)
          and all(m["id"] != pid for m in services.purchasable_materials())
          and any(m["id"] == pid for m in services.stocked_materials()),
          "honey for processing is stock, but never offered to buy or receive")
    _, err = _pur.create_purchase_request(
        db.query("users", username="production")[0], "Production", "test", "",
        [{"material_id": str(pid), "qty": "1", "unit": "kg"}], requested_by_name="A B")
    check(bool(err), "a purchase request for honey for processing is refused")
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
check(not db.query("purchase_requests", pr_no="2026-09-017")
      and not db.query("purchase_orders", po_no="2026-09-017"),
      "no sample PR / PO is seeded (the Trimix Butuan example is gone)")
# Computer parts & maintenance: no catalog items linked, so it can be
# preferred / ordered from for items that aren't on the materials list.
parts = db.query("suppliers", ref_no="62")[0]
with application.test_client() as c:
    login(c, "production", "prod123")
    page = c.get("/requests/new").data.decode("utf-8")
    check('name="material_id"' in page and 'data-combo="itemOptions"' in page
          and 'data-combo="supplierOptions"' in page
          and 'name="requested_by_name"' in page and "Expected date" in page
          and 'name="preferred_supplier_id"' in page,
          "PR form has the item and supplier search boxes, expected date and requestor")
    before = len(db.read("purchase_requests"))
    c.post("/requests/new", data={
        "department": "Production", "purpose": "Spare parts for the sealer",
        "requested_by_name": "Aldren Ocate", "material_id": ["other"],
        "description": ["Heating element"], "purpose_use": ["Band sealer"],
        "qty": ["2"], "unit": ["pc"], "est_unit_cost": ["450"],
        "preferred_supplier": ["Trimix Butuan"], "preferred_supplier_id": [""],
        "budget_category": [""], "remark": [""]})
    check(len(db.read("purchase_requests")) == before,
          "a PR refuses a preferred supplier typed instead of picked from the directory")
    c.post("/requests/new", data={
        "department": "Production", "purpose": "Spare parts for the sealer",
        "needed_by": "", "notes": "Heating element burnt", "urgency": "Urgent",
        "requested_by_name": "Aldren Ocate",
        "material_id": ["other", "other"],
        "description": ["Heating element", "Teflon tape"],
        "purpose_use": ["Band sealer", "Band sealer"], "qty": ["2", "3"],
        "unit": ["pc", "roll"], "est_unit_cost": ["450", "80"],
        "preferred_supplier": [parts["name"], ""],
        "preferred_supplier_id": [str(parts["id"]), ""],
        "budget_category": ["Equipment / repair", ""], "remark": ["", ""]})
    pr = db.read("purchase_requests")[-1]
    check(pr.get("purpose") == "Spare parts for the sealer"
          and len(pr["items"]) == 2 and abs(pr["total_estimated"] - 1140) < 0.01
          and pr["pr_no"].startswith(services.today_iso()[:7])
          and pr["requested_by_name"] == "Aldren Ocate"
          and pr["items"][0]["preferred_supplier_id"] == parts["id"]
          and pr["items"][0]["preferred_supplier"] == parts["name"],
          "PR saved with its number, items, estimated total, requestor and picked supplier")
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
with application.test_client() as c:
    login(c, "admin", "admin123")
    page = c.get("/purchasing/new?request_id=%d" % pr["id"]).data.decode("utf-8")
    check('id="supplierOptions"' in page and parts["name"] in page
          and '<select id="supplier_id"' not in page,
          "PO form opens for the approved PR, with a supplier search box")
    pos_before = len(db.read("purchase_orders"))
    c.post("/purchasing/new?request_id=%d" % pr["id"], data={
        "request_id": pr["id"], "supplier_name": "Typed Supplier",
        "po_date": services.today_iso(), "payment_terms": "Cash on delivery",
        "pr_item_id": ["1"], "include": ["1"], "description": ["Heating element"],
        "qty": ["2"], "unit": ["pc"], "unit_price": ["430"]})
    check(len(db.read("purchase_orders")) == pos_before,
          "a PO needs a supplier from the Supplier Directory")
    c.post("/purchasing/new?request_id=%d" % pr["id"], data={
        "request_id": pr["id"], "supplier_id": str(parts["id"]),
        "po_date": services.today_iso(), "payment_terms": "Advance payment",
        "advance_amount": "", "pr_item_id": ["1"], "include": ["1"],
        "description": ["Heating element"], "qty": ["2"], "unit": ["pc"],
        "unit_price": ["430"]})
    check(len(db.read("purchase_orders")) == pos_before,
          "advance payment terms need the amount paid up front")
    c.post("/purchasing/new?request_id=%d" % pr["id"], data={
        "request_id": pr["id"], "supplier_id": str(parts["id"]),
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
          and po["supplier_id"] == parts["id"] and po["advance_amount"] == 500,
          "PO prepared from the approved lines, with labor cost and advance payment")
    check(po["prepared_by_name"] == admin_name and not po["checked_by_name"],
          "Prepared by is the person signed in; Checked by waits for the check")
    check(pr["status"] == "Approved" and pr["items"][0]["po_no"] == po["po_no"]
          and not pr["items"][1].get("po_id"),
          "the PR shows which line is on the PO and which still needs one")
    import purchasing_service as pur_svc  # noqa: E402
    check(not any(e["ref"] == po["po_no"] for e in pur_svc.ledger_entries(parts["id"])),
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
    check("Purchase Order" in page and super_name in page and "Labor cost" in page,
          "PO prints as the Purchase Order form")
    # Supplier ledger: the issued PO is a credit; payments are debits.
    before = pur_svc.ledger_entries(parts["id"])
    check(any(e["ref"] == po["po_no"] and abs(e["credit"] - 1070) < 0.01 for e in before),
          "the issued PO is posted to the supplier's ledger")
    page = c.get("/ledger/%d?po=%d" % (parts["id"], po["id"])).data.decode("utf-8")
    check(po["po_no"] in page and 'value="500.00"' in page,
          "the ledger offers the advance payment that is due")
    c.post("/ledger/%d/pay" % parts["id"], data={
        "po_id": str(po["id"]), "amount": "5000", "kind": "Full payment",
        "method": "Cash"})
    check(pur_svc.po_paid(po["id"]) == 0, "a payment can't be more than the PO's balance")
    c.post("/ledger/%d/pay" % parts["id"], data={
        "po_id": str(po["id"]), "amount": "500", "kind": "Advance payment",
        "method": "GCash", "reference": "GC-1001"})
    c.post("/ledger/%d/pay" % parts["id"], data={
        "po_id": str(po["id"]), "amount": "200", "kind": "Partial payment",
        "method": "Cash"})
    summary = pur_svc.po_payment_summary(db.get("purchase_orders", po["id"]))
    entries = pur_svc.ledger_entries(parts["id"])
    check(summary["paid"] == 700 and abs(summary["balance"] - 370) < 0.01
          and abs(entries[-1]["balance"] - (before[-1]["balance"] - 700)) < 0.01,
          "advance and partial payments lower the PO's and the supplier's balance")
    row = [r for r in pur_svc.ledger_summary() if r["id"] == parts["id"]][0]
    check(abs(row["balance"] - entries[-1]["balance"]) < 0.01,
          "the ledger list shows the same balance as the supplier's ledger")
    for route in ("/ledger/", "/ledger/?show=all", "/ledger/%d" % parts["id"],
                  "/ledger/print", "/ledger/print?show=all"):
        check(c.get(route).status_code == 200, "%s renders" % route)
    page = c.get("/ledger/%d/print" % parts["id"]).data.decode("utf-8")
    check("Supplier Ledger" in page and po["po_no"] in page and "GC-1001" in page,
          "the supplier's ledger prints with its POs and payments")
    page = c.get("/ledger/%d/print?from=2999-01-01" % parts["id"]).data.decode("utf-8")
    check("Balance brought forward" in page and "GC-1001" not in page,
          "a printed ledger from a date brings the earlier balance forward")

# 7c) Finance: payments due, cash & bank accounts, payments from an account
import finance_service as fin_svc  # noqa: E402
with application.test_client() as c:
    login(c, "finance", "finance123")
    due = [r for r in fin_svc.payments_due() if r["id"] == po["id"]]
    check(due and abs(due[0]["balance"] - 370) < 0.01,
          "the issued PO is on Payments Due with its balance")
    page = c.get("/finance/due").data.decode("utf-8")
    check(po["po_no"] in page, "Payments Due lists the PO")
    page = c.get("/purchasing/%d" % po["id"]).data.decode("utf-8")
    check(po["po_no"] in page and "Cancel PO" not in page,
          "Finance sees a PO read-only")
    # Only the Super Admin transacts on the cash & bank accounts.
    r = c.post("/finance/accounts", data={"name": "Cash on hand", "kind": "Cash on hand",
                                          "opening_balance": "10000"})
    check(r.status_code == 403 and not db.read("money_accounts"),
          "Finance Admin can't add a cash / bank account")
    page = c.get("/finance/accounts").data.decode("utf-8")
    check("View only" in page and "Add an account" not in page,
          "Finance Admin sees the accounts page view only")
with application.test_client() as su:
    login(su, "superadmin", "super123")
    su.post("/finance/accounts", data={"name": "Cash on hand", "kind": "Cash on hand",
                                       "opening_balance": "10000"})
    su.post("/finance/accounts", data={"name": "BDO Savings", "kind": "Bank",
                                       "bank": "BDO", "account_no": "0012-3456",
                                       "opening_balance": "50000"})
    su.post("/finance/accounts", data={"name": "cash on hand", "kind": "Cash on hand"})
    accs = {a["name"]: a for a in db.read("money_accounts")}
    check(set(accs) == {"Cash on hand", "BDO Savings"},
          "the Super Admin adds the accounts; a duplicate name is refused")
    cash, bdo = accs["Cash on hand"], accs["BDO Savings"]
    su.post("/finance/accounts/%d/entry" % cash["id"], data={
        "direction": "in", "kind": "Deposit / cash in", "amount": "500"})
    su.post("/finance/accounts/%d/entry" % bdo["id"], data={
        "direction": "Transfer", "to_account_id": str(cash["id"]), "amount": "2000"})
    check(abs(fin_svc.account_balance(db.get("money_accounts", cash["id"])) - 12500) < 0.01
          and abs(fin_svc.account_balance(db.get("money_accounts", bdo["id"])) - 48000) < 0.01,
          "the Super Admin's money in and transfer move the account balances")
    page = su.get("/finance/accounts/%d" % bdo["id"]).data.decode("utf-8")
    check('id="entryForm"' in page and "Close this account" in page,
          "the Super Admin gets the money in / out form")
for who, pw in (("finance", "finance123"), ("admin", "admin123")):
    with application.test_client() as other:
        login(other, who, pw)
        tries = [other.post("/finance/accounts/%d/entry" % cash["id"], data={
                     "direction": "out", "kind": "Withdrawal / cash out", "amount": "100"}),
                 other.post("/finance/accounts/%d/edit" % cash["id"], data={
                     "name": "Petty cash", "kind": "Cash on hand"}),
                 other.post("/finance/accounts/%d/active" % cash["id"])]
        page = other.get("/finance/accounts/%d" % cash["id"]).data.decode("utf-8")
        acc_now = db.get("money_accounts", cash["id"])
        check(all(t.status_code == 403 for t in tries) and acc_now["name"] == "Cash on hand"
              and acc_now["active"]
              and abs(fin_svc.account_balance(acc_now) - 12500) < 0.01
              and 'id="entryForm"' not in page and "View only" in page,
              "%s can't record money in / out, edit or close an account (view only)" % who)
with application.test_client() as c:
    login(c, "finance", "finance123")
    paid_before = pur_svc.po_paid(po["id"])
    c.post("/ledger/%d/pay" % parts["id"], data={
        "po_id": str(po["id"]), "amount": "100", "kind": "Partial payment", "method": "Cash"})
    check(pur_svc.po_paid(po["id"]) == paid_before,
          "once accounts exist, a payment must say which account it came from")
    page = c.get("/ledger/%d?po=%d" % (parts["id"], po["id"])).data.decode("utf-8")
    check('name="account_id"' in page and "BDO Savings" in page,
          "the payment form offers the accounts")
    c.post("/ledger/%d/pay" % parts["id"], data={
        "po_id": str(po["id"]), "amount": "100", "kind": "Partial payment",
        "method": "Bank transfer", "account_id": str(bdo["id"])})
    check(abs(pur_svc.po_paid(po["id"]) - paid_before - 100) < 0.01
          and abs(fin_svc.account_balance(db.get("money_accounts", bdo["id"])) - 47900) < 0.01,
          "a supplier payment comes out of the account it was paid from")
    page = c.get("/finance/accounts/%d" % bdo["id"]).data.decode("utf-8")
    check("Paid " + parts["name"] in page and "Transfer to Cash on hand" in page,
          "the account statement shows the supplier payment and the transfer")
    check(c.get("/finance/accounts/%d/print" % bdo["id"]).status_code == 200,
          "the account statement prints")
    with application.test_client() as su:
        login(su, "superadmin", "super123")
        su.post("/finance/accounts/%d/active" % cash["id"])
        check(not db.get("money_accounts", cash["id"])["active"]
              and all(a["id"] != cash["id"] for a in fin_svc.active_accounts()),
              "a closed account can't be paid from")
        su.post("/finance/accounts/%d/active" % cash["id"])
    r = c.get("/dashboard")
    check(b"Payments due" in r.data, "Finance's home shows Payments due")
    c.get("/logout")
    login(c, "admin", "admin123")     # the checks below carry on as the Admin
    c.post("/purchasing/%d/delivered" % po["id"])
    check(db.get("purchase_orders", po["id"])["status"] == "Delivered",
          "PO marked delivered")
    for route in ("/purchasing/", "/requests/", "/requests/%d" % pr["id"],
                  "/approvals/", "/purchasing/%d" % po["id"]):
        check(c.get(route).status_code == 200, "%s renders" % route)

# 7d) Supplier Directory = the client's spreadsheet; item links; import;
# PR / PO supplier filtering; PO supplier details locked from the directory
import html as _html  # noqa: E402
import json as _json  # noqa: E402
import re as _re      # noqa: E402

import supplier_service as sup_svc  # noqa: E402
import xlsx as _xlsx                # noqa: E402

# (an earlier check added "Not Yet Approved Farm" by hand; it has no No.)
directory = [s for s in db.read("suppliers") if s.get("ref_no")]
by_no = {s["ref_no"]: s for s in directory}
check(len(directory) == 66 and len(by_no) == 66
      and len(db.read("suppliers")) == 67,
      "the directory is the spreadsheet's 66 suppliers (no demo suppliers)")
check(not [s for s in directory if "trimix" in (s.get("name") or "").lower()]
      and not db.query("suppliers", name="Highland Produce Traders"),
      "no demo supplier and no Trimix Butuan in the seeded directory")
metro = [s for s in directory if s["name"] == "Metro Ace Packaging Corporation"]
check(len(metro) == 2 and len({s["address"] for s in metro}) == 2,
      "both Metro Ace branches are kept, each with its own address")
check("clear goddie pouch" in by_no["36"]["materials_supplied"]
      and "white matte pouch 13x21cm" in by_no["36"]["materials_supplied"],
      "EASYHOME 168's extra item rows are merged into its items")
check(by_no["46"]["address"] == "" and by_no["4"]["tin"] == ""
      and by_no["26"]["materials_supplied"] == "",
      "'-' in the spreadsheet is stored as blank")
check(sup_svc.phone_list(by_no["3"]["phone"]) == ["0933-811-2550", "0966-866-1764", "0970-967-6621"],
      "several mobile numbers in one cell are split")
check(by_no["15"]["contact_person"] == "" and "3.7L bot." in by_no["15"]["materials_supplied"],
      "item text typed as Contact Person moved to the items")
check(by_no["7"]["supplier_type"] == "Farmer" and by_no["2"]["supplier_type"] == "Cooperative"
      and by_no["51"]["supplier_type"] == sup_svc.ONLINE and "direct" in by_no["51"]["notes"].lower(),
      "the spreadsheet's Reference is the supplier type")
honey = db.query("raw_materials", name="Raw Honey")[0]
check(by_no["1"]["id"] in sup_svc.links_by_material().get(honey["id"], set())
      and honey["supplier_id"] == by_no["1"]["id"],
      "Raw Honey is linked to VIGILIO ESCLITO, its usual supplier")
check(all(m.get("supplier_id") is None
          or m["supplier_id"] in sup_svc.links_by_material().get(m["id"], set())
          for m in db.read("raw_materials")),
      "every item's usual supplier is one of its linked suppliers")


def _sheet_rows(suppliers, extra=()):
    """The directory written back out in the spreadsheet's layout, with its
    quirks: a title row, EASYHOME's items as extra rows, '-' for blanks."""
    rows = [["SUPPLIER'S DIRECTORY"],
            ["No.", "Name", "Address", "Mobile#", "Contact Person",
             "SUPPLIER CATEGORY", "email add", "TIN", "Reference"]]
    for sp in sorted(suppliers, key=lambda x: int(x.get("ref_no") or 999)):
        items = (sp.get("materials_supplied") or "").split("; ")
        if sp.get("ref_no") != "36":
            items = ["; ".join(items)]
        rows.append([sp.get("ref_no") or "", sp["name"], sp.get("address") or "-",
                     sp.get("phone") or "", sp.get("contact_person") or "",
                     items[0], sp.get("email") or "", sp.get("tin") or "-",
                     sp.get("supplier_type") or ""])
        rows.extend([["", "", "", "", "", more, "", "", ""] for more in items[1:]])
    rows.extend(extra)
    return rows


def _super_approves(kind, title):
    """The Super Admin approves the newest waiting record of this kind and
    title; returns the request (None if there was none)."""
    waiting = [r for r in db.read("creation_requests")
               if r["kind"] == kind and r["title"] == title and r["status"] == "Pending"]
    if not waiting:
        return None
    with application.test_client() as sc:
        login(sc, "superadmin", "super123")
        sc.post("/additions/%d/approve" % waiting[-1]["id"])
    return db.get("creation_requests", waiting[-1]["id"])


def _upload_sheet(c, rows):
    data = _xlsx.write_xlsx([("Suppliers Directory", rows)])
    return c.post("/suppliers/import", data={"workbook": (io.BytesIO(data), "suppliers.xlsx")},
                  content_type="multipart/form-data")


def _confirm(c, page, **extra):
    payload = _html.unescape(_re.search(r'name="payload" value="([^"]*)"', page).group(1))
    form = {"payload": payload}
    for i, default in _re.findall(r'name="match_(\d+)" class="js-match" data-default="([^"]+)"', page):
        form["match_%s" % i] = default
    form.update(extra)
    return c.post("/suppliers/import/confirm", data=form), _json.loads(payload)


with application.test_client() as c:
    login(c, "receiving", "recv123")
    check(c.get("/suppliers/import").status_code == 403,
          "only an Admin imports the spreadsheet")
with application.test_client() as c:
    login(c, "admin", "admin123")
    check(b"Import from Excel" in c.get("/suppliers/").data,
          "the directory offers Import from Excel to the Admin")
    before = _json.dumps(db.read("suppliers"), sort_keys=True)
    r = _upload_sheet(c, _sheet_rows(db.read("suppliers")))
    page = r.data.decode("utf-8")
    check(r.status_code == 200 and "<b>0</b> new" in page and "<b>0</b> with changes" in page
          and "<b>67</b> already the same" in page,
          "re-importing the directory's own spreadsheet finds nothing new or changed")
    _confirm(c, page)
    check(_json.dumps(db.read("suppliers"), sort_keys=True) == before,
          "confirming that import changes nothing")
    lemon_tin = by_no["3"]["tin"]
    count_before = len(db.read("suppliers"))
    extra = [["67", "Agusan Honey Farm", "Cabadbaran City", "09171234567", "Rosa Ty",
              "Honey", "", "-", "farmer"],
             ["", "", "", "", "", "honeycomb", "", "", ""],
             ["4", "Dexter Reblinca", "Buenavista , Agusan del Norte", "0910-1595-699",
              "Dexter Reblinca", "Calamansi", "", "111-222-333-00000",
              "Direct Supplier/Consolidator"]]
    rows = [r for r in _sheet_rows(db.read("suppliers")) if r[0] != "4"] + extra
    page = _upload_sheet(c, rows).data.decode("utf-8")
    check("<b>1</b> new" in page and "Fills in <b>TIN</b>" in page and "Agusan Honey Farm" in page,
          "the preview shows the new supplier and what a match fills in")
    new_i = _re.search(r'name="match_(\d+)" class="js-match" data-default="new"', page).group(1)
    _confirm(c, page, **{"category_%s" % new_i: "Raw",
                         "items_%s" % new_i: [str(honey["id"])]})
    check(not db.query("suppliers", name="Agusan Honey Farm")
          and [r for r in db.read("creation_requests")
               if r["kind"] == "supplier" and r["title"].startswith("Agusan Honey Farm")
               and r["status"] == "Pending"],
          "a new supplier from the import waits for the Super Admin, not in the directory yet")
    _super_approves("supplier", "Agusan Honey Farm (Cabadbaran City)")
    farm = db.query("suppliers", name="Agusan Honey Farm")
    check(farm and not farm[0]["approved"] and farm[0]["supplier_type"] == "Farmer"
          and farm[0]["materials_supplied"] == "Honey; honeycomb"
          and farm[0]["phone"] == "0917-123-4567",
          "once approved it joins the directory off the Approved Supplier List, cleaned up the same way")
    check(farm and farm[0]["id"] in sup_svc.links_by_material().get(honey["id"], set()),
          "the item link ticked on the preview is saved")
    check(db.get("suppliers", by_no["4"]["id"])["tin"] == "111-222-333-00000"
          and len(db.read("suppliers")) == count_before + 1,
          "a matched row fills in a blank field and is not added twice")
    rows = [r for r in _sheet_rows(db.read("suppliers")) if r[0] != "3"]
    rows.append(["3", by_no["3"]["name"], by_no["3"]["address"], by_no["3"]["phone"],
                 by_no["3"]["contact_person"], "Lemon", "", "999-999-999-00000",
                 "Direct Supplier/Consolidator"])
    page = _upload_sheet(c, rows).data.decode("utf-8")
    i3 = _re.search(r'name="match_(\d+)" class="js-match" data-default="%d"' % by_no["3"]["id"], page).group(1)
    check("<b>TIN</b> differs" in page, "a field that differs is shown as a conflict")
    _confirm(c, page)
    check(db.get("suppliers", by_no["3"]["id"])["tin"] == lemon_tin,
          "a conflict keeps the directory's value unless the file's is chosen")
    _confirm(c, page, **{"use_%s_tin" % i3: "file"})
    check(db.get("suppliers", by_no["3"]["id"])["tin"] == "999-999-999-00000",
          "choosing the file's value updates the directory")
    db.update("suppliers", by_no["3"]["id"], {"tin": lemon_tin})
    # The supplier form: a second branch of the same name is allowed.
    before_reqs = len(db.read("creation_requests"))
    c.post("/suppliers/new", data={"name": "Metro Ace Packaging Corporation",
                                   "category": "Packing", "address": "Davao City"})
    check(len(db.read("creation_requests")) == before_reqs,
          "a supplier already in the directory (same name and address) is refused")
    c.post("/suppliers/new", data={"name": "Metro Ace Packaging Corporation",
                                   "category": "Packing", "address": "Butuan City",
                                   "supplier_type": "Retailer",
                                   "phone": "0917 111 2222\n0918-333-4444",
                                   "material_ids": [str(honey["id"])]})
    check(len(db.query("suppliers", name="Metro Ace Packaging Corporation")) == 2,
          "the Admin's new supplier is not in the directory before it is approved")
    _super_approves("supplier", "Metro Ace Packaging Corporation (Butuan City)")
    metro = db.query("suppliers", name="Metro Ace Packaging Corporation")
    check(len(metro) == 3, "the same name is refused at the same address, allowed for a new branch")
    branch = [m for m in metro if m["address"] == "Butuan City"][0]
    check(branch["phone"] == "0917-111-2222 / 0918-333-4444" and branch["supplier_type"] == "Retailer"
          and branch["id"] in sup_svc.links_by_material().get(honey["id"], set()),
          "the supplier form saves the type, several numbers and the catalog items")
    page = c.get("/suppliers/%d" % branch["id"]).data.decode("utf-8")
    check("Retailer" in page and "Raw Honey" in page and "0918-333-4444" in page,
          "the supplier page shows its type, catalog items and numbers")
    c.post("/suppliers/%d/edit" % branch["id"], data={
        "name": branch["name"], "category": "Packing", "address": "Butuan City",
        "supplier_type": "Retailer"})
    check(branch["id"] not in sup_svc.links_by_material().get(honey["id"], set()),
          "unticking an item on the supplier form removes the link")
    page = c.get("/suppliers/?type=Farmer").data.decode("utf-8")
    check("INDAY" in page and "Tengjia" not in page, "the list filters by supplier type")
    page = c.get("/suppliers/print").data.decode("utf-8")
    check("Farmer" in page and "Cooperative" in page, "the printed directory shows the supplier type")

# PR: pick-only, approved suppliers, and only the item's suppliers
blocked = db.insert("suppliers", {"name": "Not On The List Shop", "category": "Raw",
                                  "approved": False, "created_at": services.now_iso()})
sup_svc.set_supplier_items(blocked["id"], [honey["id"]])
tengjia = by_no["13"]
vigilio = by_no["1"]


def _honey_pr(c, supplier_id, typed=""):
    before = len(db.read("purchase_requests"))
    c.post("/requests/new", data={
        "department": "Production", "purpose": "Honey for the next run",
        "requested_by_name": "Aldren Ocate", "material_id": [str(honey["id"])],
        "description": ["Raw Honey"], "purpose_use": [""], "qty": ["20"],
        "unit": ["kg"], "est_unit_cost": ["400"], "preferred_supplier": [typed],
        "preferred_supplier_id": [str(supplier_id) if supplier_id else ""],
        "budget_category": ["Raw materials"], "remark": [""]})
    return len(db.read("purchase_requests")) > before


with application.test_client() as c:
    login(c, "production", "prod123")
    page = c.get("/requests/new").data.decode("utf-8")
    opts = _json.loads(_html.unescape(_re.search(
        r'<script type="application/json" id="supplierOptions">(.*?)</script>', page, _re.S).group(1)))
    vig = [o for o in opts if o["v"] == vigilio["id"]]
    check(vig and honey["id"] in vig[0]["d"]["items"]
          and not [o for o in opts if o["v"] == blocked["id"]],
          "the PR's supplier list has approved suppliers only, each with the items it supplies")
    check(not _honey_pr(c, blocked["id"], "Not On The List Shop"),
          "a PR refuses a supplier that isn't approved")
    check(not _honey_pr(c, tengjia["id"], tengjia["name"]),
          "a PR refuses a supplier that doesn't supply the picked item")
    check(_honey_pr(c, vigilio["id"], vigilio["name"]),
          "a PR accepts a supplier of the picked item")
    honey_pr = db.read("purchase_requests")[-1]
for _u, _p in (("admin", "admin123"), ("superadmin", "super123")):
    with application.test_client() as c:
        login(c, _u, _p)
        c.post("/approvals/%d" % honey_pr["id"], data={"action": "approve"})

# PO: filtered by the ticked items, and the directory's details are locked
db.update("suppliers", vigilio["id"], {"tin": ""})
vigilio = db.get("suppliers", vigilio["id"])
with application.test_client() as c:
    login(c, "admin", "admin123")
    page = c.get("/purchasing/new?request_id=%d" % honey_pr["id"]).data.decode("utf-8")
    check(vigilio["name"] in page and 'data-material="%d"' % honey["id"] in page
          and "Supplier type" in page and "Quotation / invoice no." in page,
          "the PO form suggests the PR's supplier and knows each line's item")
    check('id="supplier_address" name="supplier_address"' in page
          and _re.search(r'id="supplier_address"[^>]*readonly', page)
          and not _re.search(r'id="supplier_tin"[^>]*readonly', page),
          "a detail the directory has is locked; a blank one stays open")

    def _po(supplier_id, **fields):
        data = {"request_id": honey_pr["id"], "supplier_id": str(supplier_id),
                "po_date": services.today_iso(), "payment_terms": "Cash on delivery",
                "pr_item_id": ["1"], "include": ["1"], "qty": ["20"], "unit": ["kg"],
                "unit_price": ["400"]}
        data.update(fields)
        before = len(db.read("purchase_orders"))
        c.post("/purchasing/new?request_id=%d" % honey_pr["id"], data=data)
        return db.read("purchase_orders")[-1] if len(db.read("purchase_orders")) > before else None

    check(_po(tengjia["id"]) is None,
          "the server refuses a PO supplier that supplies none of the items")
    honey_po = _po(vigilio["id"], supplier_address="Fake Street 1",
                   supplier_contact="0000", supplier_contact_person="Somebody Else",
                   supplier_type="Retailer", supplier_tin="123-456-789-00000",
                   supplier_reference="Q-2026-77", save_to_directory="1")
    check(honey_po and honey_po["supplier_address"] == vigilio["address"]
          and honey_po["supplier_contact_person"] == vigilio["contact_person"]
          and honey_po["supplier_type"] == vigilio["supplier_type"]
          and vigilio["phone"] in honey_po["supplier_contact"],
          "the server takes locked supplier details from the directory, not the form")
    check(honey_po and honey_po["supplier_tin"] == "123-456-789-00000"
          and honey_po["supplier_reference"] == "Q-2026-77"
          and db.get("suppliers", vigilio["id"])["tin"] == "123-456-789-00000"
          and db.get("suppliers", vigilio["id"])["address"] == vigilio["address"],
          "a detail typed in a blank field is kept, and saved to the directory when asked")
    page = c.get("/purchasing/%d/print" % honey_po["id"]).data.decode("utf-8")
    check("Supplier type" in page and vigilio["supplier_type"] in page
          and "Quotation / invoice no." in page and "Q-2026-77" in page,
          "the printed PO shows the supplier type and the quotation / invoice no.")
    c.post("/purchasing/%d/cancel" % honey_po["id"])
    db.update("suppliers", vigilio["id"], {"tin": ""})
    page = c.get("/purchasing/new?request_id=%d" % honey_pr["id"]).data.decode("utf-8")
    check(_re.search(r'id="supplier_contact_person"[^>]*readonly', page)
          and 'value="123-456-789-00000"' in page,
          "a PO restarted from a cancelled one is locked too, keeping what was typed in blank fields")
    check('name="other_fees"' in page and 'name="other_fees_note"' in page
          and 'value="NON VAT"' in page and 'id="vat_amount"' in page,
          "the PO totals step has other / misc. fees and VAT / NON VAT with the VAT amount")
    vat_po = _po(vigilio["id"], vat_type="VAT", vat_rate="12", discount="100",
                 other_fees="50", other_fees_note="Handling fee")
    check(vat_po and vat_po["vat_type"] == "VAT" and abs(vat_po["tax"] - 948) < 0.01
          and vat_po["other_fees"] == 50 and vat_po["other_fees_note"] == "Handling fee"
          and abs(vat_po["grand_total"] - (8000 - 100 + 50 + 948)) < 0.01,
          "VAT is worked out on the items after the discount; other fees add to the total")
    page = c.get("/purchasing/%d/print" % vat_po["id"]).data.decode("utf-8")
    check("Other / misc. fees (Handling fee)" in page and "VAT (12%)" in page,
          "the printed PO shows the other fees and the VAT")
    c.post("/purchasing/%d/cancel" % vat_po["id"])
    zero_po = _po(vigilio["id"], vat_type="VAT", vat_rate="0")
    check(zero_po and zero_po["vat_type"] == "NON VAT" and zero_po["tax"] == 0
          and zero_po["grand_total"] == 8000,
          "a VAT rate of 0 is recorded as NON VAT")
    page = c.get("/purchasing/%d/print" % zero_po["id"]).data.decode("utf-8")
    check("NON VAT" in page, "the printed PO says NON VAT")
    c.post("/purchasing/%d/cancel" % zero_po["id"])
    non_po = _po(vigilio["id"], vat_type="NON VAT", vat_rate="12")
    check(non_po and non_po["vat_type"] == "NON VAT" and non_po["tax"] == 0,
          "choosing NON VAT ignores a rate left in the box")
    c.post("/purchasing/%d/cancel" % non_po["id"])

# 7e) New records: only the Admin adds an employee, supplier, material or
# supply; the Super Admin approves; only then is it part of the system.
for _u, _p in (("receiving", "recv123"), ("production", "prod123"), ("superadmin", "super123")):
    with application.test_client() as c:
        login(c, _u, _p)
        check(c.get("/suppliers/new").status_code == 403
              and c.get("/additions/new/employee").status_code in (403,)
              and c.get("/additions/new/material").status_code == 403,
              "%s can't add new records (only the Admin can)" % _u)
with application.test_client() as c:
    login(c, "production", "prod123")
    before = len(db.read("raw_materials"))
    c.post("/catalog/materials", data={"name": "Sneaky Item", "category": "raw",
                                       "unit": "kg"})
    check(len(db.read("raw_materials")) == before
          and not [r for r in db.read("creation_requests") if r["title"] == "Sneaky Item"],
          "the catalog page no longer lets a team head add a material")
with application.test_client() as c:
    login(c, "admin", "admin123")
    page = c.get("/additions/").data.decode("utf-8")
    check("Add new employee" in page and "Add supplier" in page and "Add new material" in page
          and "Add new supply" in page, "the Admin's New Records page offers all four")
    for route in ("/additions/new/employee", "/additions/new/material", "/additions/new/supply"):
        check(c.get(route).status_code == 200, "%s renders" % route)
    c.post("/additions/new/employee", data={
        "first_name": "Rosalie", "middle_name": "Cruz", "last_name": "Batobalonos",
        "position": "Packing Staff", "department": "Packing", "employee_no": "AFP-E-099"})
    emp_req = db.read("creation_requests")[-1]
    check(emp_req["kind"] == "employee" and emp_req["status"] == "Pending"
          and not db.query("employees", name="Rosalie Cruz Batobalonos"),
          "a new employee waits for approval and is not on the list yet")
    _page = c.get("/requests/new").data.decode("utf-8")
    _emps = _re.search(r'id="employeeOptions">(.*?)</script>', _page, _re.S).group(1)
    check("Batobalonos" not in _emps,
          "a waiting employee is not offered on purchase requests")
    c.post("/additions/%d/approve" % emp_req["id"])
    check(db.get("creation_requests", emp_req["id"])["status"] == "Pending",
          "the Admin can't approve their own new record")
    c.post("/additions/new/material", data={
        "name": "Moringa Seeds", "category": "raw", "subcategory": "Raw materials",
        "unit": "kg", "track_mode": "weight", "reorder_level": "2", "unit_cost": "300"})
    mat_req = db.read("creation_requests")[-1]
    tengjia_id = db.query("suppliers", ref_no="13")[0]["id"]
    c.post("/additions/new/supply", data={
        "name": "Square Bottle 500 mL", "category": "packaging", "subcategory": "Jars / Bottles",
        "unit": "pc", "track_mode": "piece", "reorder_level": "100", "unit_cost": "14",
        "supplier_id": str(tengjia_id)})
    sup_req = db.read("creation_requests")[-1]
    before = len(db.read("creation_requests"))
    c.post("/additions/new/supply", data={
        "name": "square bottle 500 ml", "category": "packaging", "unit": "pc"})
    check(len(db.read("creation_requests")) == before,
          "the same new item can't be sent twice while it waits")
    c.post("/additions/new/supply", data={"name": "Hexagonal Jar 250 mL",
                                          "category": "packaging", "unit": "pc"})
    check(len(db.read("creation_requests")) == before,
          "an item already on the materials list is refused")
    c.post("/catalog/materials", data={"name": "Glass Dropper 30 mL", "category": "packaging",
                                       "unit": "pc", "track_mode": "piece"})
    drop_req = db.read("creation_requests")[-1]
    check(drop_req["title"] == "Glass Dropper 30 mL" and drop_req["kind"] == "supply"
          and not db.query("raw_materials", name="Glass Dropper 30 mL"),
          "adding on the catalog page sends the item for approval too")
    c.post("/additions/%d/withdraw" % drop_req["id"])
    check(db.get("creation_requests", drop_req["id"])["status"] == "Withdrawn",
          "the Admin can withdraw a record that is still waiting")
    check(not db.query("raw_materials", name="Moringa Seeds")
          and not db.query("raw_materials", name="Square Bottle 500 mL"),
          "waiting materials and supplies are not on the materials list")
admin_id = db.query("users", username="admin")[0]["id"]
with application.test_client() as c:
    login(c, "superadmin", "super123")
    page = c.get("/approvals/").data.decode("utf-8")
    check("New records waiting for your approval" in page and "Rosalie Cruz Batobalonos" in page
          and "Moringa Seeds" in page, "the Super Admin's approvals list the new records")
    check("Rosalie Cruz Batobalonos" in c.get("/employees/").data.decode("utf-8"),
          "the Employees page shows the employee waiting for approval")
    check(c.get("/additions/%d" % emp_req["id"]).status_code == 200, "a new record's page renders")
    c.post("/additions/%d/approve" % emp_req["id"])
    emp = db.query("employees", name="Rosalie Cruz Batobalonos")
    check(emp and emp[0]["department"] == "Packing"
          and db.get("creation_requests", emp_req["id"])["status"] == "Approved"
          and db.get("creation_requests", emp_req["id"])["record_id"] == emp[0]["id"],
          "approving the employee adds them to the list")
    c.post("/additions/%d/reject" % mat_req["id"], data={"reason": ""})
    check(db.get("creation_requests", mat_req["id"])["status"] == "Pending",
          "a rejection needs a reason")
    c.post("/additions/%d/reject" % mat_req["id"], data={"reason": "Use the existing Moringa item"})
    check(db.get("creation_requests", mat_req["id"])["status"] == "Rejected"
          and not db.query("raw_materials", name="Moringa Seeds"),
          "a rejected material is never added")
    c.post("/additions/%d/approve" % sup_req["id"])
    bottle = db.query("raw_materials", name="Square Bottle 500 mL")
    check(bottle and bottle[0]["category"] == "packaging" and bottle[0]["stock_qty"] == 0
          and bottle[0]["supplier_id"] == tengjia_id
          and tengjia_id in sup_svc.links_by_material().get(bottle[0]["id"], set()),
          "an approved supply joins the materials list, linked to its usual supplier, with no stock")
    c.post("/additions/%d/approve" % sup_req["id"])
    check(len(db.query("raw_materials", name="Square Bottle 500 mL")) == 1,
          "approving twice adds it only once")
    check(c.get("/additions/?show=all").status_code == 200, "/additions/?show=all renders")
mine = [n["message"] for n in db.read("notifications") if n.get("user_id") == admin_id]
check(any("Approved: employee Rosalie" in m for m in mine)
      and any("Rejected: raw material Moringa Seeds" in m and "existing Moringa" in m for m in mine),
      "the Admin is told when a record is approved or rejected")

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
    login(c, "marketing", "market123")
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
               "/suppliers/print"],     # (adding a supplier is the Admin's)
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
        # GROWER'S CHOICE supplies the lemon on this request
        "supplier_id": str(db.query("suppliers", ref_no="3")[0]["id"]),
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
    check("PO line 1" in page and po["supplier_name"].replace("'", "&#39;") in page,
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
        "request_id": old[0]["id"],
        "supplier_id": str(db.query("suppliers", ref_no="3")[0]["id"]),
        "payment_terms": "Cash on delivery",
        "pr_item_id": [str(i["item_id"]) for i in lines],
        "include": ["1"] * len(lines), "description": [""] * len(lines),
        "qty": [str(i["requested_qty"]) for i in lines],
        "unit": ["pc"] * len(lines), "unit_price": ["1"] * len(lines)})
po = db.read("purchase_orders")[-1]
check(po["purchase_request_id"] == old[0]["id"]
      and [i["description"] for i in po["items"]] == ["Lemon", "Ginger"],
      "a PO for an older production request is named from the materials")

# 9b) a PO for switches (not on the materials list yet) received in full
with application.test_client() as c:
    login(c, "production", "prod123")
    c.post("/requests/new", data={
        "department": "Production", "purpose": "Machine accessories",
        "requested_by_name": "Aldren Ocate", "urgency": "Normal",
        "material_id": ["other", "other"],
        "description": ["Timer switch", "Center switch"],
        "purpose_use": ["Dehydrator", "Dehydrator"], "qty": ["1", "1"],
        "unit": ["pc", "pc"], "est_unit_cost": ["700", "150"],
        "preferred_supplier": ["", ""], "preferred_supplier_id": ["", ""],
        "budget_category": ["Equipment / repair", "Equipment / repair"],
        "remark": ["", ""]})
    switch_pr = db.read("purchase_requests")[-1]
for _u, _p in (("admin", "admin123"), ("superadmin", "super123")):
    with application.test_client() as c:
        login(c, _u, _p)
        c.post("/approvals/%d" % switch_pr["id"], data={"action": "approve"})
switch_pr = db.get("purchase_requests", switch_pr["id"])
with application.test_client() as c:
    login(c, "admin", "admin123")
    _its = switch_pr["items"]
    c.post("/purchasing/new?request_id=%d" % switch_pr["id"], data={
        "request_id": switch_pr["id"], "supplier_id": str(parts["id"]),
        "po_date": services.today_iso(), "payment_terms": "Cash on delivery",
        "pr_item_id": [str(i["item_id"]) for i in _its], "include": ["1"] * len(_its),
        "qty": [str(i["approved_qty"] or i["requested_qty"]) for i in _its],
        "unit": ["pc"] * len(_its), "unit_price": ["700", "150"]})
    demo_po = db.read("purchase_orders")[-1]
    c.post("/purchasing/%d/approve" % demo_po["id"])
with application.test_client() as c:
    login(c, "superadmin", "super123")
    c.post("/purchasing/%d/approve" % demo_po["id"])
demo_po = db.get("purchase_orders", demo_po["id"])
check(demo_po["purchase_request_id"] == switch_pr["id"] and demo_po["status"] == "Approved"
      and abs(demo_po["grand_total"] - 850) < 0.01,
      "a PO for the switches is prepared and approved")
n = len(demo_po["items"])
with application.test_client() as c:
    login(c, "receiving", "recv123")
    page = c.get("/receiving/new?po=%d" % demo_po["id"]).data.decode("utf-8")
    check("Timer switch" in page and "can't be changed here" in page
          and 'name="material_id" value="new"' in page,
          "an ordered item not on the materials list can be added on receipt")
    check('name="lot_no"' not in page and 'name="temperature_c"' not in page,
          "the receive form has no optional lot / expiry / condition fields")
    # Only the "add an item that isn't on the PO" template still has a picker.
    check(page.count('<select name="material_id"') == 1
          and page.count("can't be changed here") == n,
          "a PO line's item can't be changed when receiving")
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
          and db.get("purchase_requests", switch_pr["id"])["status"] == "Received",
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
    login(c, "service", "service123")
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

# 12b) Files: supplier payment attachments; delivery DR / OR / AR files
from PIL import Image as _Image  # noqa: E402


def _png(color="red"):
    buf = io.BytesIO()
    _Image.new("RGB", (40, 30), color).save(buf, "PNG")
    return buf.getvalue()


_PDF = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"
chona = db.query("suppliers", ref_no="5")[0]
with application.test_client() as c:
    login(c, "admin", "admin123")
    page = c.get("/ledger/%d" % chona["id"]).data.decode("utf-8")
    check('name="attachments"' in page and "multiple" in page and "application/pdf" in page,
          "the payment form takes more attachments (pictures or PDFs)")
    acct = [a for a in db.read("money_accounts") if a.get("active", True)]
    base = {"amount": "250", "kind": "Partial payment", "method": "Cash",
            "account_id": str(acct[0]["id"]) if acct else ""}
    before = len(db.read("supplier_payments"))
    c.post("/ledger/%d/pay" % chona["id"], data=dict(base, attachments=[
        (io.BytesIO(b"not a picture"), "notes.txt")]), content_type="multipart/form-data")
    check(len(db.read("supplier_payments")) == before,
          "a file that is neither a picture nor a PDF is refused, nothing saved")
    c.post("/ledger/%d/pay" % chona["id"], data=dict(
        base, attachments_label="Official receipt and invoice",
        proof=(io.BytesIO(_png("blue")), "receipt.png"),
        attachments=[(io.BytesIO(_png()), "or.png"), (io.BytesIO(_PDF), "invoice.pdf")]),
        content_type="multipart/form-data")
    pay = db.read("supplier_payments")[-1]
    atts = pay.get("attachments") or []
    check(pay["supplier_id"] == chona["id"] and pay["proof"] and len(atts) == 2
          and [a["kind"] for a in atts] == ["image", "pdf"]
          and atts[1]["name"] == "invoice.pdf" and atts[0]["label"] == "Official receipt and invoice",
          "a payment on account saves its proof and both attachments")
    r = c.get("/ledger/payments/%d/files/2" % pay["id"])
    check(r.status_code == 200 and r.mimetype == "application/pdf" and r.data.startswith(b"%PDF"),
          "a PDF attachment opens as a PDF")
    check(c.get("/ledger/payments/%d/files/1" % pay["id"]).status_code == 200
          and c.get("/ledger/payments/%d/files/9" % pay["id"]).status_code == 404,
          "a picture attachment opens; a missing one is 404")
    page = c.get("/ledger/payments/%d" % pay["id"]).data.decode("utf-8")
    check("Attachments · 2" in page and "invoice.pdf" in page and "Attach more files" in page,
          "the payment's page lists its attachments and offers to attach more")
    c.post("/ledger/payments/%d/attachments" % pay["id"], data={
        "attachments_label": "Deposit slip",
        "attachments": [(io.BytesIO(_PDF), "deposit.pdf")]}, content_type="multipart/form-data")
    atts = db.get("supplier_payments", pay["id"])["attachments"]
    check(len(atts) == 3 and atts[2]["n"] == 3 and atts[2]["label"] == "Deposit slip",
          "more files can be attached to a payment later")
    check("3 attachments" in c.get("/ledger/%d" % chona["id"]).data.decode("utf-8"),
          "the ledger links each payment to its attachments")
with application.test_client() as c:
    login(c, "receiving", "recv123")
    check(c.get("/ledger/payments/%d/files/1" % pay["id"]).status_code == 403,
          "payment attachments are for ledger users only")
# A PO payment shows its attachments on the PO too.
paid_po = [p for p in db.read("supplier_payments") if p.get("po_id")]
if paid_po:
    with application.test_client() as c:
        login(c, "admin", "admin123")
        c.post("/ledger/payments/%d/attachments" % paid_po[0]["id"], data={
            "attachments": [(io.BytesIO(_png()), "or.png")]}, content_type="multipart/form-data")
        page = c.get("/purchasing/%d" % paid_po[0]["po_id"]).data.decode("utf-8")
        check("1 attachment" in page, "a PO's payments show their attachments")

# An issued PO (the honey request's) for the Delivered by check.
with application.test_client() as c:
    login(c, "admin", "admin123")
    c.post("/purchasing/new?request_id=%d" % honey_pr["id"], data={
        "request_id": honey_pr["id"], "supplier_id": str(vigilio["id"]),
        "po_date": services.today_iso(), "payment_terms": "Cash on delivery",
        "pr_item_id": ["1"], "include": ["1"], "qty": ["20"], "unit": ["kg"],
        "unit_price": ["400"]})
    honey_po2 = db.read("purchase_orders")[-1]
    c.post("/purchasing/%d/approve" % honey_po2["id"])
with application.test_client() as c:
    login(c, "superadmin", "super123")
    c.post("/purchasing/%d/approve" % honey_po2["id"])
with application.test_client() as c:
    login(c, "receiving", "recv123")
    check(db.get("purchase_orders", honey_po2["id"])["status"] == "Approved",
          "an issued PO is open for receiving")
    page = c.get("/receiving/new?po=%d" % honey_po2["id"]).data.decode("utf-8")
    check('id="delivered_by"' in page and 'value="VIGILIO ESCLITO"' in page,
          "Delivered by starts as the PO's supplier name")
    page = c.get("/receiving/new?po=none").data.decode("utf-8")
    check('name="or_no"' in page and 'name="ar_no"' in page and 'name="or_file"' in page
          and 'name="ar_file"' in page and 'name="delivery_ref"' in page,
          "the receive form has DR, OR and acknowledgement receipt numbers, each with a file")
    _, ginger = stock("Ginger")
    r = c.post("/receiving/new?po=none", data={
        "supplier_id": str(chona["id"]), "received_date": services.today_iso(),
        "received_time": "09:00", "delivered_by": chona["name"],
        "delivery_ref": "DR-9001", "or_no": "OR-9001", "ar_no": "AR-9001",
        "material_id": [str(ginger["id"])], "qty_per_box": ["5"], "boxes": ["1"],
        "lot_no": [""], "expiry_date": [""], "packaging_condition": [""], "temperature_c": [""],
        "proof": (io.BytesIO(_png()), "dr.png"), "or_file": (io.BytesIO(_PDF), "or.pdf")},
        content_type="multipart/form-data")
    rb = db.read("receiving_batches")[-1]
    check(r.status_code == 302 and rb["delivery_ref"] == "DR-9001" and rb["or_no"] == "OR-9001"
          and rb["ar_no"] == "AR-9001" and rb["delivered_by"] == chona["name"]
          and rb["proof"] and rb["or_proof"].endswith(".pdf") and not rb.get("ar_proof"),
          "a delivery saves its DR, OR and AR numbers and their files")
    r = c.get("/receiving/%d/proof?doc=or" % rb["id"])
    check(r.status_code == 200 and r.mimetype == "application/pdf", "the OR file opens as a PDF")
    c.post("/receiving/%d/proof?doc=ar" % rb["id"], data={
        "proof": (io.BytesIO(_png("green")), "ar.png")}, content_type="multipart/form-data")
    check(db.get("receiving_batches", rb["id"]).get("ar_proof"),
          "the AR file can be added later on the delivery's page")
    page = c.get("/receiving/%d" % rb["id"]).data.decode("utf-8")
    check("Delivery documents" in page and "Official receipt (OR)" in page and "OR-9001" in page,
          "the delivery's page shows its DR, OR and AR")
    page = c.get("/receiving/%d/mrr" % rb["id"]).data.decode("utf-8")
    check("OR-9001" in page and "AR-9001" in page, "the receiving report shows the OR and AR numbers")
    before = len(db.read("receiving_batches"))
    c.post("/receiving/new?po=none", data={
        "supplier_id": str(chona["id"]), "received_date": services.today_iso(),
        "received_time": "09:00", "material_id": [str(ginger["id"])], "qty_per_box": ["5"],
        "boxes": ["1"], "lot_no": [""], "expiry_date": [""], "packaging_condition": [""],
        "temperature_c": [""], "ar_file": (io.BytesIO(b"junk"), "ar.txt")},
        content_type="multipart/form-data")
    check(len(db.read("receiving_batches")) == before,
          "a bad document file stops the delivery from saving")

# 12c) Inspection lanes: Raw Material Inspection, Packaging & Supplies Check,
# Honey Inspection — each with its own way of checking
with application.test_client() as c:
    login(c, "receiving", "recv123")
    nav = c.get("/receiving/").data.decode("utf-8")
    check("Raw Material Inspection" in nav and "Packaging &amp; Supplies Check" in nav
          and "Honey Inspection" in nav and "Boxes for Inspection" not in nav
          and "Honey Containers" not in nav,
          "the menu shows the three inspection lanes (no more 'Boxes' / 'Honey Containers')")
    tengjia_s = db.query("suppliers", ref_no="13")[0]
    bottle_m = db.query("raw_materials", name="Square Bottle 300 mL")[0]
    _, ginger_m = stock("Ginger")
    c.post("/receiving/new?po=none", data={
        "supplier_id": str(tengjia_s["id"]), "received_date": services.today_iso(),
        "received_time": "10:00", "material_id": [str(bottle_m["id"]), str(ginger_m["id"])],
        "qty_per_box": ["100", "5"], "boxes": ["1", "1"], "lot_no": ["", ""],
        "expiry_date": ["", ""], "packaging_condition": ["", ""], "temperature_c": ["", ""]})
    lane_batch = db.read("receiving_batches")[-1]
    lane_boxes = {b["material_name"]: b for b in db.query("receiving_boxes", batch_id=lane_batch["id"])}
    bottle_box, ginger_box = lane_boxes["Square Bottle 300 mL"], lane_boxes["Ginger"]
with application.test_client() as c:
    login(c, "production", "prod123")
    raw_page = c.get("/receiving/inspection").data.decode("utf-8")
    sup_page = c.get("/receiving/inspection/supplies").data.decode("utf-8")
    check("Raw Material Inspection" in raw_page and ginger_box["box_code"] in raw_page
          and bottle_box["box_code"] not in raw_page,
          "a raw material box waits in Raw Material Inspection")
    check("Packaging &amp; Supplies Check" in sup_page and bottle_box["box_code"] in sup_page
          and ginger_box["box_code"] not in sup_page,
          "a bottle box waits in Packaging & Supplies Check")
    page = c.get("/receiving/box/%d/inspect" % bottle_box["id"]).data.decode("utf-8")
    check("Clean the content" not in page and "Test results" not in page
          and "Defects" in page and "Odor / appearance" not in page
          and "Food-grade certificate" in page,
          "the packaging & supplies check skips cleaning and tests and uses its own checklist")
    page = c.get("/receiving/box/%d/inspect" % ginger_box["id"]).data.decode("utf-8")
    check("Clean the content" in page and "Odor / appearance" in page and "Test results" in page,
          "the raw material inspection keeps cleaning, the full checklist and tests")
    supply_answers = {"visual_description": "Clean, no cracks", "usable_qty": "100",
                      "unusable_qty": "0", "damaged_qty": "0", "cleaned": "0"}
    for key, _, _ in services.SUPPLY_INSPECTION_POINTS:
        supply_answers["chk_" + key] = "Fail" if key == "defects" else "Pass"
        supply_answers["chk_%s_remarks" % key] = "3 cracked necks" if key == "defects" else ""
    supply_answers["chk_dates"] = "N/A"          # bottles carry no expiry
    c.post("/receiving/box/%d/inspect" % bottle_box["id"], data=dict(supply_answers, acceptance="Accepted"))
    check(services.box_status(db.get("receiving_boxes", bottle_box["id"])) == services.BOX_AWAITING,
          "a box that failed the defects check can't be accepted")
    c.post("/receiving/box/%d/inspect" % bottle_box["id"], data=dict(
        supply_answers, acceptance="Hold", nonconformity="Cracked necks",
        nc_types=["Damaged packaging"]))
    held_box = db.get("receiving_boxes", bottle_box["id"])
    check(services.box_status(held_box) == services.BOX_HOLD
          and held_box["checklist_lane"] == "supplies"
          and set(held_box["checklist"]) == {k for k, _, _ in services.SUPPLY_INSPECTION_POINTS},
          "the supplies checklist is saved with its own points, without odor or test")
    page = c.get("/receiving/box/%d/checklist" % bottle_box["id"]).data.decode("utf-8")
    check("Packaging &amp; Supplies Inspection Checklist" in page and "Defects" in page
          and "Odor / appearance" not in page, "the printed checklist is the packaging & supplies one")
    check("Damaged packaging" in services.nc_types_for(held_box),
          "a failed defects check suggests 'Damaged packaging' on the hold report")
    home = c.get("/dashboard").data.decode("utf-8")
    check("Raw material inspection" in home and "Packaging &amp; supplies check" in home
          and "Honey inspection" in home, "the production head's home has a task per lane")
    check("Honey Inspection" in c.get("/honey/").data.decode("utf-8"), "the honey page is Honey Inspection")

# 12d) Batch numbers and expiry: given on receipt, dates picked at inspection
import production_service as _prod_svc  # noqa: E402
ym = services.today_iso()[:7]
with application.test_client() as c:
    login(c, "receiving", "recv123")
    cal = db.query("raw_materials", name="Calamansi")[0]
    dexter = db.query("suppliers", ref_no="4")[0]
    used = services._batch_numbers_used("CALAMANSI", ym)
    page = c.post("/receiving/new/preview?po=none", data={
        "supplier_id": str(dexter["id"]), "received_date": services.today_iso(),
        "material_id": [str(cal["id"])], "qty_per_box": ["8"], "boxes": ["2"],
        "lot_no": [""], "expiry_date": [""], "packaging_condition": [""],
        "temperature_c": [""]}).data.decode("utf-8")
    expected_no = "CALAMANSI-%s-%03d" % (ym, max(used or [0]) + 1)
    check(expected_no in page, "the receiving report preview shows the batch no. it will give")
    for _ in range(2):
        c.post("/receiving/new?po=none", data={
            "supplier_id": str(dexter["id"]), "received_date": services.today_iso(),
            "received_time": "11:00", "material_id": [str(cal["id"])], "qty_per_box": ["8"],
            "boxes": ["2"], "lot_no": [""], "expiry_date": [""], "packaging_condition": [""],
            "temperature_c": [""]})
    cal_boxes = [b for b in db.read("receiving_boxes") if b["material_id"] == cal["id"]][-4:]
    first_no, second_no = cal_boxes[0]["lot_no"], cal_boxes[2]["lot_no"]
    check(first_no == expected_no and cal_boxes[1]["lot_no"] == first_no
          and int(second_no[-3:]) == int(first_no[-3:]) + 1,
          "the next delivery of the same item this month gets the next number in the series")
with application.test_client() as c:
    login(c, "production", "prod123")
    page = c.get("/receiving/box/%d/inspect" % cal_boxes[0]["id"]).data.decode("utf-8")
    check('type="date" name="mfg_date"' in page and 'type="date" name="expiry_date"' in page
          and first_no in page, "the checklist has calendars for date marking and shows the batch no.")
    answers = checklist()
    answers.update({"usable_qty": "8", "unusable_qty": "0", "damaged_qty": "0", "cleaned": "1",
                    "acceptance": "Accepted", "expiry_date": "", "mfg_date": ""})
    c.post("/receiving/box/%d/inspect" % cal_boxes[0]["id"], data=answers)
    check(services.box_status(db.get("receiving_boxes", cal_boxes[0]["id"])) == services.BOX_AWAITING,
          "date marking 'Pass' without an expiry date is refused")
    answers.update({"mfg_date": "2099-01-02", "expiry_date": "2099-01-01"})
    c.post("/receiving/box/%d/inspect" % cal_boxes[0]["id"], data=answers)
    check(services.box_status(db.get("receiving_boxes", cal_boxes[0]["id"])) == services.BOX_AWAITING,
          "an expiry before the manufacturing date is refused")
    from datetime import date as _d, timedelta as _td  # noqa: E402
    soon = (_d.fromisoformat(services.today_iso()) + _td(days=10)).isoformat()
    answers.update({"mfg_date": services.today_iso(), "expiry_date": soon})
    c.post("/receiving/box/%d/inspect" % cal_boxes[0]["id"], data=answers)
    done_box = db.get("receiving_boxes", cal_boxes[0]["id"])
    check(services.box_status(done_box) == services.BOX_ACCEPTED and done_box["expiry_date"] == soon
          and done_box["mfg_date"] == services.today_iso(),
          "the dates picked on the calendar are saved on the box")
    page = c.get("/receiving/box/%d/inspect" % cal_boxes[1]["id"]).data.decode("utf-8")
    check('value="%s"' % soon in page, "the next box of the same batch starts with the batch's dates")
    page = c.get("/receiving/box/%d/checklist" % cal_boxes[0]["id"]).data.decode("utf-8")
    check("EXP" in page and first_no in page, "the printed checklist shows the batch no. and dates")
    row = [r for r in services.batch_rows() if r["batch_no"] == first_no][0]
    check(row["accepted_qty"] == 8 and row["in_storage"] == 8 and row["expiry_date"] == soon
          and row["status"] == "Waiting for inspection",
          "Batches & Expiry adds up a batch (one box still waiting)")
    answers.update({"usable_qty": "8"})
    c.post("/receiving/box/%d/inspect" % cal_boxes[1]["id"], data=answers)
    row = [r for r in services.batch_rows() if r["batch_no"] == first_no][0]
    check(row["remaining"] == 16 and row["status"] == "Expiring soon" and row["days_left"] == 10,
          "a batch with stock that expires within 30 days is flagged")
    page = c.get("/receiving/batches?show=expiring").data.decode("utf-8")
    check(first_no in page and "Expiring soon" in page and "10 days left" in page,
          "the Batches & Expiry page lists it")
    for show in ("stock", "all"):
        check(c.get("/receiving/batches?show=" + show).status_code == 200,
              "/receiving/batches?show=%s renders" % show)
    check("expired or expiring within 30 days" in c.get("/dashboard").data.decode("utf-8"),
          "the home page warns about expiring batches")
    check(services.fefo_batch(cal["id"])["batch_no"] == first_no,
          "the batch to use first is the one that expires first (FEFO)")
    hlgc = db.query("products", name="Honey Calamansi Concentrate 300 mL")[0]
    line = [l for l in _prod_svc.default_dispensing(hlgc["id"], 10) if l["material_id"] == cal["id"]][0]
    check(line["lot_no"] == first_no and line["expiry_date"] == soon,
          "production dispensing fills in the batch no. and expiry to use first")
with application.test_client() as c:
    login(c, "receiving", "recv123")
    nav = c.get("/receiving/").data.decode("utf-8")
    check("Batches &amp; Expiry" in nav, "the receiving menu has Batches & Expiry")

# 12e) Supplies In / Out names the batch: OUT from a batch, new stock IN
#      starts one, returns go back to theirs
def _batch(no):
    return [r for r in services.batch_rows(cal["id"]) if r["batch_no"] == no][0]


def _cal_stock():
    return services.current_stock("material", cal["id"])


with application.test_client() as c:
    login(c, "admin", "admin123")
    page = c.get("/supplies/move?material_id=%d&direction=OUT" % cal["id"]).data.decode("utf-8")
    check(first_no in page and 'id="stepBatch"' in page,
          "the IN / OUT form has a batch step listing the item's batches")
    before = _cal_stock()
    move = {"direction": "OUT", "category": "raw", "material_id": str(cal["id"]),
            "reason": "Used in production", "person": "Production team"}
    c.post("/supplies/move", data=dict(move, qty="20", batch_no=first_no))
    check(abs(_cal_stock() - before) < 0.001, "an OUT can't take more than the batch has left")
    c.post("/supplies/move", data=dict(move, qty="5", batch_no=first_no))
    last = sorted(db.read("stock_movements"), key=lambda m: m["id"])[-1]
    check(last["batch_no"] == first_no and abs(_cal_stock() - (before - 5)) < 0.001
          and _batch(first_no)["remaining"] == 11,
          "an OUT from a batch is recorded with its batch no. and comes off what the batch has left")
    _, loose = services.batch_choices(cal["id"])
    _, err = assets_service.record_supply_movement(
        cal["id"], "OUT", loose + 1, "Used in production", user_id=1, notify=False, batch_no="")
    check(bool(err) and "batch" in err, "an OUT with no batch no. is limited to stock from before batch numbers")
    _, err = assets_service.record_supply_movement(
        cal["id"], "IN", 2, "Returned unused", user_id=1, notify=False, batch_no=first_no)
    check(not err and _batch(first_no)["remaining"] == 13, "a return goes back to its batch")
    _, err = assets_service.record_supply_movement(
        cal["id"], "IN", 10, "Returned unused", user_id=1, notify=False, batch_no=first_no)
    check(bool(err), "a return can't put back more than went out of the batch")
    _, err = assets_service.record_supply_movement(
        cal["id"], "IN", 4, services.NEW_STOCK_REASON, user_id=1, notify=False,
        expiry_date="2000-01-01")
    check(bool(err), "new stock with an expiry date already passed is refused")
    later = (_d.fromisoformat(services.today_iso()) + _td(days=200)).isoformat()
    expect_no = services.next_batch_no("Calamansi")
    c.post("/supplies/move", data={"direction": "IN", "category": "raw", "material_id": str(cal["id"]),
                                   "reason": services.NEW_STOCK_REASON, "qty": "4",
                                   "expiry_date": later, "person": "Market"})
    hand = sorted(db.read("stock_movements"), key=lambda m: m["id"])[-1]
    check(hand["batch_no"] == expect_no and hand["batch_new"] and hand["expiry_date"] == later,
          "a delivery / purchase IN gets a new batch no. of its own, with its expiry")
    hand_row = _batch(expect_no)
    check(hand_row["remaining"] == 4 and hand_row["source"] == "Supplies IN",
          "Batches & Expiry lists a batch taken in by hand")
    check(int(services.next_batch_no("Calamansi")[-3:]) == int(expect_no[-3:]) + 1,
          "receiving carries on the same series after it")
    bmr = db.insert("batch_records", {"batch_no": "BMR-TEST-BATCH", "status": "Completed",
                                      "stock_posted": True,
                                      "dispensing": [{"material_id": cal["id"], "lot_no": expect_no.lower(),
                                                      "theoretical_qty": 1.5}]})
    check(_batch(expect_no)["remaining"] == 2.5,
          "a finished production batch comes off the batch its dispensing line names")
    db.delete("batch_records", bmr["id"])
    page = c.get("/supplies/%d" % cal["id"]).data.decode("utf-8")
    check(first_no in page and expect_no in page,
          "the item page lists its batches")
    page = c.get("/supplies/movements").data.decode("utf-8")
    check("Batch %s" % first_no in page, "the movements log shows the batch no.")
    for b in cal_boxes[:2]:
        db.update("receiving_boxes", b["id"], {"expiry_date": "2000-01-01"})
    _, err = assets_service.record_supply_movement(
        cal["id"], "OUT", 1, "Used in production", user_id=1, notify=False, batch_no=first_no)
    check(bool(err) and "expired" in err, "an expired batch can't go out for use")
    _, err = assets_service.record_supply_movement(
        cal["id"], "OUT", 1, "Damaged / expired", user_id=1, notify=False, batch_no=first_no)
    check(not err, "an expired batch goes out as Damaged / expired")

# 13) the MySQL export builds, including the new tables
import mysql_export            # noqa: E402

schema = mysql_export.build_schema_sql()
data_sql = mysql_export.build_data_sql()
for table in ("receiving_boxes", "packing_items", "honey_allocations",
              "assets", "asset_movements", "stock_movements",
              "month_end_count_lines", "batch_dispensing", "batch_steps",
              "retention_samples", "sanitation_areas", "pest_control_areas",
              "purchase_orders", "purchase_order_items",
              "nonconformance_reports", "creation_requests", "supplier_items",
              "supplier_payment_attachments"):
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
