"""Authentication & role-based authorization for the Amamio Farm Products
Sales & Inventory Management System.

Ten roles (per the client's team structure):

    super_admin      Super Admin               — full control, incl. roles
    admin            Admin                     — day-to-day control & approvals
    receiving_head   Receiving Team Head       — records every delivery and
                                                 labels it with QR codes
    production_head  Production Team Head      — inspection & cleaning, honey
                                                 QC, batch records, production
    packing_head     Packing Team Head         — packing and dispatch only
    packing_member   Packing Team Member       — records packing and dispatch
    assets_head      Assets Maintenance Head   — equipment, supplies in/out,
                                                 maintenance, sanitation
    finance_admin    Finance Admin             — supplier payments, ledgers,
                                                 payments due (views accounts)
    customer_service_admin  Customer Service Admin — customers, sales orders,
                                                 deliveries
    sales_marketing_admin   Sales Marketing Admin  — sales uploads & reports,
                                                 stock release requests

The three function admins (Finance, Customer Service, Sales Marketing) are
role-based: unlike the Admin they only reach the pages of their own job.

Access is expressed per *module* (a nav destination / blueprint area).  The
PERMISSIONS map is the single source of truth used for both the sidebar
(``can(user, module)``) and the route guards (``module_required``).
"""
from functools import wraps

from flask import abort, redirect, session, url_for

import db

# --- Roles -----------------------------------------------------------------
ROLES = [
    ("super_admin", "Super Admin",
     "Full system control: users, roles, settings and all operations"),
    ("admin", "Admin",
     "Approves requests, oversees operations, reviews reports"),
    ("receiving_head", "Receiving Team Head",
     "Records every delivery at the receiving area and labels it with QR codes"),
    ("production_head", "Production Team Head",
     "Inspection & cleaning, honey QC, batch records and production runs"),
    ("packing_head", "Packing Team Head",
     "Leads packing and dispatch per channel"),
    ("packing_member", "Packing Team Member",
     "Records what was packed, disbursed and handed to the courier"),
    ("assets_head", "Assets Maintenance Head",
     "Equipment, supplies in/out, maintenance, cleaning and pest control"),
    ("finance_admin", "Finance Admin",
     "Supplier payments and ledgers, payments due; views the cash and bank accounts"),
    ("customer_service_admin", "Customer Service Admin",
     "Customers, sales orders (payments and returns) and deliveries"),
    ("sales_marketing_admin", "Sales Marketing Admin",
     "Marketplace sales uploads and reports; stock release requests"),
]
ROLE_KEYS = [r[0] for r in ROLES]
ROLE_LABELS = {r[0]: r[1] for r in ROLES}
ROLE_DESCRIPTIONS = {r[0]: r[2] for r in ROLES}

# Roles that behave as "management" — they see everything and approve requests.
ADMIN_ROLES = {"super_admin", "admin"}
# Admins of one function: role-based, they reach only their own job's pages.
FUNCTION_ADMINS = {"finance_admin", "customer_service_admin", "sales_marketing_admin"}

# Legacy role keys (from the pre-Amamio prototype) mapped onto the new set so
# existing data/accounts keep working after an upgrade.
LEGACY_ROLE_MAP = {
    "manager": "admin",
    "inventory": "production_head",
    "production": "production_head",
    "sales": "packing_head",
    "warehouse": "packing_head",
}

# --- Module access ---------------------------------------------------------
# module_key -> set of roles allowed.  super_admin/admin are allowed everywhere
# except where a module is explicitly restricted below.
_ALL = set(ROLE_KEYS)
_HEADS = _ALL - {"packing_member"} - FUNCTION_ADMINS   # the team heads
_FLOOR = _ALL - FUNCTION_ADMINS                          # roles on the floor
PERMISSIONS = {
    "dashboard":     _ALL,
    # Stock on hand: the floor, and the two sales-side admins (what can be
    # sold / promised to a customer).  Not Finance.
    "inventory":     _FLOOR | {"customer_service_admin", "sales_marketing_admin"},
    "qr":            _FLOOR,                     # one scanner for the floor
    "chatbot":       _FLOOR | {"customer_service_admin", "sales_marketing_admin"},
    "notifications": _ALL,
    "requests":      _HEADS,                     # every head can raise requests
    "analytics":     ADMIN_ROLES,
    "approvals":     ADMIN_ROLES,
    # New employees, suppliers, materials and supplies: the Admin adds them,
    # the Super Admin approves them (additions_service.can_add / can_decide).
    "additions":     ADMIN_ROLES,
    "purchasing":    ADMIN_ROLES,                # preparing / approving POs
    # Finance — supplier payments, what is due, and the money accounts.
    "ledger":        ADMIN_ROLES | {"finance_admin"},
    "accounts":      ADMIN_ROLES | {"finance_admin"},   # view balances & statements
    # Transacting on the cash & bank accounts — adding, changing or closing
    # an account, money in / out and transfers — is the Super Admin's alone.
    # (Supplier payments stay with Finance: the "ledger" module.)
    "accounts_transact": {"super_admin"},
    "po_view":       ADMIN_ROLES | {"finance_admin"},   # read-only POs
    "reports":       ADMIN_ROLES,
    "users":         ADMIN_ROLES,
    "migration":     {"super_admin"},
    "profiles":      {"super_admin"},                # staff profiles & e-signatures
    "employees":     {"super_admin"},                # staff without an account

    # Module 1 — receiving area, then inspection & cleaning.
    "receiving":     ADMIN_ROLES | {"receiving_head"},
    "inspection":    ADMIN_ROLES | {"receiving_head", "production_head"},
    # Module 2 — honey containers are logged on arrival, QC'd by production.
    "honey":         ADMIN_ROLES | {"receiving_head", "production_head"},
    # Production records (batch manufacturing, daily report, retention).
    "production":    ADMIN_ROLES | {"production_head"},
    "catalog":       ADMIN_ROLES | {"production_head"},
    # Finished goods taken in / issued out by hand.
    "product_moves": ADMIN_ROLES | {"production_head"},
    # Module 3 — equipment and supplies, every movement in and out.
    "assets":        ADMIN_ROLES | {"assets_head"},
    "supplies":      ADMIN_ROLES | {"assets_head", "receiving_head"},
    "facility":      ADMIN_ROLES | {"assets_head", "production_head"},
    "suppliers":     ADMIN_ROLES | {"receiving_head", "production_head",
                                    "assets_head"},
    # Module 4 — packing, disbursement and courier hand-over.
    # Packing is done only by the packers themselves (see EXACT_ROLES).
    "packing":       {"super_admin", "packing_head", "packing_member"},
    # Module 5 — marketplace uploads and sales reports (Sales Marketing).
    "platforms":     ADMIN_ROLES | {"sales_marketing_admin"},
    # Stock release: Sales Marketing requests it, the Admin approves,
    # production fulfils it.
    "sales":         ADMIN_ROLES | {"sales_marketing_admin", "production_head"},
    # Customer Service: customers, their sales orders and deliveries.
    "orders":        ADMIN_ROLES | {"customer_service_admin"},
    "deliveries":    ADMIN_ROLES | {"customer_service_admin"},
    "customers":     ADMIN_ROLES | {"customer_service_admin"},
}

# Modules only the Super Admin may reach, whatever PERMISSIONS says.
SUPER_ADMIN_ONLY = {"migration", "profiles", "employees", "accounts_transact"}

# Modules where PERMISSIONS is exact — the Admin role does NOT get in by
# default.  Packing is recorded by the packers and the Super Admin only.
EXACT_ROLES = {"packing": PERMISSIONS["packing"]}

# Roles that record packing as themselves ("Packed by" is their own name).
PACKER_ROLES = ("packing_head", "packing_member")

# Sidebar groups: (section label, [(module, label, icon name, endpoint), ...])
# Icon names refer to icons.ICONS (monochrome line icons).  Each role only sees
# the entries it may open, so a floor role gets a short, focused menu.
#
# For now the sidebar shows only the purchasing flow, in the order the work
# happens: Purchase Request → Admin check → Super Admin approval → Purchase
# Order, then Finance (supplier ledger, payments due, cash & bank accounts).
# The other menus are parked in NAV_HIDDEN while the flow is reworked; their
# pages still open by URL.  Move a group back into NAV to show it again.
NAV = [
    ("Purchasing", [
        ("requests",   "Purchase Requests", "requests", "requests.index"),
        ("approvals",  "Approvals",         "approvals", "approvals.index"),
        ("purchasing", "Purchase Orders",   "orders", "purchasing.index"),
    ]),
    ("Records", [
        ("additions",  "New Records",       "plus", "additions.index"),
    ]),
    ("Finance", [
        ("ledger",   "Supplier Ledger",       "ledger", "ledger.index"),
        ("ledger",   "Payments Due",          "calendar", "finance.due"),
        ("accounts", "Cash & Bank Accounts",  "wallet", "finance.accounts"),
    ]),
    ("Stock", [
        ("product_moves", "Products In / Out", "swap", "product_moves.index"),
        ("inventory",     "Batches & Expiry",  "calendar", "receiving.batches"),
    ]),
    ("Super Admin", [
        ("profiles",  "Users & Profiles", "users", "auth.users"),
        ("employees", "Employees",        "customers", "employees.index"),
    ]),
]

NAV_HIDDEN = [
    ("Start", [
        ("dashboard", "Home",           "dashboard", "auth.dashboard"),
        ("qr",        "Scan a QR code", "qr", "qr.scan"),
    ]),
    ("Receiving & Inspection", [
        ("receiving",  "Receiving Area",        "receiving", "receiving.index"),
        ("inspection", "Raw Material Inspection",    "inspect", "receiving.inspection"),
        ("inspection", "Packaging & Supplies Check", "clipboard", "receiving.inspection_supplies"),
        ("honey",      "Honey Inspection",           "honey", "honey.index"),
        ("inspection", "Hold & Rejection Reports",   "shield", "receiving.ncr_list"),
    ]),
    ("Production", [
        ("production", "Batch Records",           "production", "production.index"),
        ("production", "Daily Production Report", "reports", "production.daily_report"),
        ("production", "Retention Samples",       "retention", "production.retention"),
        ("catalog",    "Products & Recipes",      "recipes", "catalog.index"),
        ("inventory",  "Stock on Hand",           "inventory", "inventory.index"),
    ]),
    ("Packing & Sales", [
        ("packing",    "Packing & Dispatch",      "packing", "packing.index"),
        ("platforms",  "Sales Uploads & Reports", "platforms", "platforms.index"),
        ("orders",     "Sales Orders",            "orders", "orders.index"),
        ("sales",      "Stock Release",           "release", "sales.index"),
        ("deliveries", "Deliveries",              "deliveries", "deliveries.index"),
        ("customers",  "Customers",               "customers", "customers.index"),
    ]),
    ("Assets & Facility", [
        ("assets",    "Equipment & Assets",    "assets", "assets.index"),
        ("supplies",  "Supplies In / Out",     "swap", "supplies.index"),
        ("facility",  "Cleaning & Sanitation", "sparkle", "facility.sanitation"),
        ("facility",  "Pest Control",          "shield", "facility.pest"),
        ("suppliers", "Supplier Directory",    "suppliers", "suppliers.index"),
    ]),
    ("Management", [
        ("reports",   "Reports",   "reports", "reports.index"),
        ("analytics", "Analytics", "analytics", "analytics.index"),
    ]),
    ("General", [
        ("notifications", "Notifications", "notifications", "notifications.index"),
        ("users",         "Users",         "users", "auth.users"),
        ("migration",     "MySQL Export",  "database", "migration.index"),
    ]),
]

# Menus written for one team, in its own words (SOP AFP-WH-SOP-001 for
# receiving).  A role listed here sees this menu instead of NAV, with its own
# sidebar subtitle; every entry is still checked with can().
ROLE_NAV = {
    "receiving_head": [
        ("Receiving", [
            ("dashboard", "Receiving Home",     "dashboard", "auth.dashboard"),
            ("receiving", "Receiving Log",      "clipboard", "receiving.index"),
            ("receiving", "Receive a Delivery", "in", "receiving.new"),
            ("inspection", "Raw Material Inspection",    "inspect", "receiving.inspection"),
            ("inspection", "Packaging & Supplies Check", "clipboard", "receiving.inspection_supplies"),
            ("honey",     "Honey Inspection",   "honey", "honey.index"),
            ("supplies",  "Supplies In / Out",  "swap", "supplies.index"),
            ("qr",        "Scan a QR Label",    "qr", "qr.scan"),
        ]),
        # The records the receiving and inspection work produces.
        ("Receiving Reports", [
            ("receiving",  "Receiving & Inspection Report", "reports", "receiving.report"),
            ("inspection", "Hold & Rejection Reports",      "shield", "receiving.ncr_list"),
            ("receiving",  "Damage Report",                 "alert", "receiving.damage"),
            ("inventory",  "Batches & Expiry",              "calendar", "receiving.batches"),
        ]),
        ("Suppliers & Requests", [
            ("suppliers", "Supplier Directory", "suppliers", "suppliers.index"),
            ("requests",  "Purchase Requests",  "requests", "requests.index"),
        ]),
        ("General", [
            ("notifications", "Notifications", "notifications", "notifications.index"),
        ]),
    ],
}
ROLE_NAV["finance_admin"] = [
    ("Finance", [
        ("dashboard", "Finance Home",         "dashboard", "auth.dashboard"),
        ("ledger",    "Payments Due",         "calendar", "finance.due"),
        ("ledger",    "Supplier Ledger",      "ledger", "ledger.index"),
        ("accounts",  "Cash & Bank Accounts", "wallet", "finance.accounts"),
    ]),
    ("Purchasing", [
        ("po_view",   "Purchase Orders",      "orders", "purchasing.index"),
    ]),
    ("General", [
        ("notifications", "Notifications", "notifications", "notifications.index"),
    ]),
]
ROLE_NAV["customer_service_admin"] = [
    ("Customer Service", [
        ("dashboard",  "Customer Service Home", "dashboard", "auth.dashboard"),
        ("orders",     "Sales Orders",          "orders", "orders.index"),
        ("orders",     "New Sales Order",       "plus", "orders.new"),
        ("deliveries", "Deliveries",            "deliveries", "deliveries.index"),
        ("customers",  "Customers",             "customers", "customers.index"),
    ]),
    ("Stock", [
        ("inventory",  "Stock on Hand",         "inventory", "inventory.index"),
    ]),
    ("General", [
        ("notifications", "Notifications", "notifications", "notifications.index"),
    ]),
]
ROLE_NAV["sales_marketing_admin"] = [
    ("Sales & Marketing", [
        ("dashboard", "Sales Home",              "dashboard", "auth.dashboard"),
        ("platforms", "Sales Uploads & Reports", "platforms", "platforms.index"),
        ("platforms", "Upload a Sales File",     "upload", "platforms.upload"),
        ("sales",     "Stock Release",           "release", "sales.index"),
    ]),
    ("Stock", [
        ("inventory", "Stock on Hand",           "inventory", "inventory.index"),
    ]),
    ("General", [
        ("notifications", "Notifications", "notifications", "notifications.index"),
    ]),
]
# Packing: packing and dispatch only (plus purchase requests for the head,
# e.g. packaging supplies).
ROLE_NAV["packing_head"] = [
    ("Packing", [
        ("dashboard", "Packing Home",        "dashboard", "auth.dashboard"),
        ("packing",   "Record Packing",      "plus", "packing.new"),
        ("packing",   "Packing & Dispatch",  "packing", "packing.index"),
        ("packing",   "Packing History",     "reports", "packing.history"),
        ("qr",        "Scan a QR Label",     "qr", "qr.scan"),
        ("inventory", "Stock on Hand",       "inventory", "inventory.index"),
    ]),
    ("Requests", [
        ("requests",  "Purchase Requests",   "requests", "requests.index"),
    ]),
    ("General", [
        ("notifications", "Notifications", "notifications", "notifications.index"),
    ]),
]
ROLE_NAV["packing_member"] = [
    ("Packing", [
        ("dashboard", "Packing Home",        "dashboard", "auth.dashboard"),
        ("packing",   "Record Packing",      "plus", "packing.new"),
        ("packing",   "Packing & Dispatch",  "packing", "packing.index"),
        ("qr",        "Scan a QR Label",     "qr", "qr.scan"),
    ]),
    ("General", [
        ("notifications", "Notifications", "notifications", "notifications.index"),
    ]),
]
ROLE_NAV_SUBTITLE = {"receiving_head": "Receiving", "finance_admin": "Finance",
                     "customer_service_admin": "Customer Service",
                     "sales_marketing_admin": "Sales & Marketing",
                     "packing_head": "Packing", "packing_member": "Packing"}


def nav_for(user):
    """The sidebar sections for this user: their team's menu, or NAV."""
    role = normalize_role(user.get("role")) if user else None
    return ROLE_NAV.get(role, NAV)


def nav_subtitle(user):
    """The line under the brand in the sidebar."""
    role = normalize_role(user.get("role")) if user else None
    return ROLE_NAV_SUBTITLE.get(role, "Sales & Inventory")

def normalize_role(role):
    """Map a legacy role key onto the current role set."""
    if role in ROLE_KEYS:
        return role
    return LEGACY_ROLE_MAP.get(role, role)


def is_admin(user):
    """True for Super Admin / Admin — the two management roles."""
    if not user:
        return False
    return normalize_role(user.get("role")) in ADMIN_ROLES


def is_super_admin(user):
    return bool(user) and normalize_role(user.get("role")) == "super_admin"


def can(user, module):
    """True if the given user dict may access the module."""
    if not user:
        return False
    role = normalize_role(user.get("role"))
    if module in SUPER_ADMIN_ONLY:
        return role == "super_admin"
    if module in EXACT_ROLES:
        return role in EXACT_ROLES[module]
    if role in ADMIN_ROLES:
        return True
    allowed = PERMISSIONS.get(module)
    return bool(allowed) and role in allowed


def current_user():
    """Return the logged-in user dict from session, or None."""
    uid = session.get("user_id")
    if uid is None:
        return None
    user = db.get("users", uid)
    if user and not user.get("active", True):
        return None
    return user


def login_required(f):
    """Redirect to the login page if the request is not authenticated."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return wrapper


def role_required(*roles):
    """403 unless the user's role is in ``roles``.

    Super Admin and Admin always pass, except when the decorator names
    ``super_admin`` alone — that restricts the route to the Super Admin."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            user = current_user()
            if user is None:
                return redirect(url_for("auth.login"))
            role = normalize_role(user.get("role"))
            if set(roles) == {"super_admin"}:
                if role != "super_admin":
                    abort(403)
                return f(*args, **kwargs)
            if role not in ADMIN_ROLES and role not in roles:
                abort(403)
            return f(*args, **kwargs)
        return wrapper
    return decorator


def module_required(module):
    """403 unless the current user may access ``module`` (per PERMISSIONS)."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            user = current_user()
            if user is None:
                return redirect(url_for("auth.login"))
            if not can(user, module):
                abort(403)
            return f(*args, **kwargs)
        return wrapper
    return decorator
