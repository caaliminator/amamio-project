"""Staff profiles: personal details, photo and e-signature for each user.

The Super Admin keeps these up to date (Users & Profiles).  The full name and
position print on the system's forms, and the e-signature is drawn on the
sign-off line of whoever signed — matched by user id where a record keeps
one, otherwise by the name written on it.

Photos and signatures are stored under data/uploads/users/ (never in the
public repository) and served only to signed-in users.
"""
import os
import re
import secrets
from io import BytesIO

from flask import url_for
from PIL import Image, ImageOps, UnidentifiedImageError

import db
import security
from services import today_iso

GENDERS = ["Male", "Female", "Prefer not to say"]
SUFFIXES = ["", "Jr.", "Sr.", "II", "III", "IV"]
# Plain text fields the profile form edits, as stored on the user record.
TEXT_FIELDS = ("first_name", "middle_name", "last_name", "suffix", "position",
               "gender", "birthdate", "employee_no", "date_hired", "phone",
               "email", "address", "emergency_contact")
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
IMAGE_FORMATS = {"PNG", "JPEG", "WEBP", "GIF", "BMP"}
KINDS = ("photo", "signature")


def upload_dir():
    return os.path.join(db.DATA_DIR, "uploads", "users")


# --- Names ---------------------------------------------------------------------
def full_name(first="", middle="", last="", suffix=""):
    """'Juan', 'Santos', 'Dela Cruz', 'Jr.' -> 'Juan Santos Dela Cruz Jr.'"""
    parts = [(p or "").strip() for p in (first, middle, last, suffix)]
    return " ".join(p for p in parts if p)


def name_parts(user):
    """First / middle / last / suffix for the form.  Accounts made before
    profiles only have ``name``: its last word is taken as the surname."""
    user = user or {}
    if user.get("first_name") or user.get("last_name"):
        return {k: user.get(k) or "" for k in
                ("first_name", "middle_name", "last_name", "suffix")}
    words = (user.get("name") or "").split()
    return {"first_name": " ".join(words[:-1]) if len(words) > 1 else " ".join(words),
            "middle_name": "",
            "last_name": words[-1] if len(words) > 1 else "",
            "suffix": ""}


def _norm(name):
    return " ".join((name or "").split()).lower()


def find_user(user_id=None, name=None):
    """The user who signed: by id when known, else by the name on the form."""
    if user_id:
        try:
            user = db.get("users", int(user_id))
        except (TypeError, ValueError):
            user = None
        if user:
            return user
    wanted = _norm(name)
    if not wanted:
        return None
    for user in db.read("users"):
        if wanted in (_norm(user.get("name")), _norm(full_name(
                user.get("first_name"), user.get("middle_name"),
                user.get("last_name"), user.get("suffix")))):
            return user
    return None


# --- Images --------------------------------------------------------------------
def image_path(user, kind):
    """Absolute path of the user's photo / signature, or None."""
    filename = (user or {}).get(kind)
    if kind not in KINDS or not filename:
        return None
    path = os.path.join(upload_dir(), os.path.basename(filename))
    return path if os.path.isfile(path) else None


def image_url(user, kind):
    if not image_path(user, kind):
        return ""
    # The file name changes with every upload, so it doubles as a cache key.
    return url_for("auth.user_image", id=user["id"], kind=kind, v=user[kind])


def signature_url(user_id=None, name=None):
    return image_url(find_user(user_id, name), "signature")


def position_label(user):
    """The profile's position, else the system role's name."""
    if not user:
        return ""
    return (user.get("position")
            or security.ROLE_LABELS.get(security.normalize_role(user.get("role")), ""))


def position_of(user_id=None, name=None):
    return position_label(find_user(user_id, name))


def find_employee(name):
    """An entry of the employee list (staff without an account) by name."""
    wanted = _norm(name)
    for emp in db.read("employees"):
        if wanted and _norm(emp.get("name")) == wanted:
            return emp
    return None


def employee_label(emp):
    """'Juan Santos Reyes (Machine Operator)' — how the list shows a name."""
    position = (emp or {}).get("position") or ""
    return "%s (%s)" % (emp.get("name"), position) if position else emp.get("name") or ""


def employee_options(departments=(), first=None):
    """The employee list for a type-to-search box (app.js "combo"), grouped
    by department in the order given, the ``first`` department on top."""
    order = [first] + [d for d in departments if d != first] if first else list(departments)
    rows = sorted(db.read("employees"),
                  key=lambda e: (order.index(e.get("department"))
                                 if e.get("department") in order else len(order),
                                 (e.get("last_name") or e.get("name") or "").lower()))
    return [{"v": e["id"], "l": employee_label(e), "g": e.get("department") or "Other"}
            for e in rows if e.get("name")]


def with_position(user_id=None, name=None):
    """'Maria Santos Dela Cruz (Production Team Head)' for text fields; the
    name as given when it isn't a user's."""
    user = find_user(user_id, name)
    if not user:
        return name or ""
    position = position_label(user)
    return "%s (%s)" % (user.get("name"), position) if position else user.get("name") or ""


def strip_position(text):
    """Undo with_position() on a submitted name, so records keep the bare
    name — what prints on the forms and what the e-signature lookup
    matches.  Text that isn't "<a user's or employee's name> (…)" is kept
    as typed."""
    text = " ".join((text or "").split())
    m = re.match(r"^(.*\S)\s*\([^()]*\)$", text)
    person = m and (find_user(name=m.group(1)) or find_employee(m.group(1)))
    return person.get("name") if person else text


def _clear_paper(img):
    """Make the paper behind a photographed signature transparent, so the
    ink sits on the form's sign-off line.  Pixels close to the paper's
    brightness (the median of the image) disappear; ink stays."""
    if img.mode == "RGBA" and img.getextrema()[3][0] < 250:
        return img                     # already has a transparent background
    rgba = img.convert("RGBA")
    grey = rgba.convert("L")
    hist = grey.histogram()
    half, seen, paper = grey.width * grey.height / 2, 0, 255
    for level, count in enumerate(hist):
        seen += count
        if seen >= half:
            paper = level
            break
    cut = max(90, min(225, paper - 45))
    alpha = grey.point(lambda v: 0 if v >= cut else min(255, (cut - v) * 6))
    rgba.putalpha(alpha)
    return rgba


def save_image(user, kind, upload):
    """Store an uploaded photo / signature for the user.
    Returns (filename, error)."""
    if kind not in KINDS:
        return None, "Unknown picture type."
    data = upload.read(MAX_UPLOAD_BYTES + 1)
    if not data:
        return None, "The file is empty."
    if len(data) > MAX_UPLOAD_BYTES:
        return None, "The picture is too large — use one under 5 MB."
    try:
        img = Image.open(BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError):
        return None, "That file isn't a picture. Use a PNG or JPG."
    if img.format not in IMAGE_FORMATS:
        return None, "Use a PNG or JPG picture."
    img = ImageOps.exif_transpose(img)
    if kind == "photo":
        img = img.convert("RGB")
        img.thumbnail((600, 600))
        ext, fmt, opts = "jpg", "JPEG", {"quality": 88}
    else:
        img.thumbnail((1200, 600))
        img = _clear_paper(img)
        box = img.getchannel("A").getbbox()
        if not box:
            return None, "No signature could be seen in that picture."
        img = img.crop(box)
        img.thumbnail((900, 300))
        ext, fmt, opts = "png", "PNG", {"optimize": True}
    os.makedirs(upload_dir(), exist_ok=True)
    filename = "%d-%s-%s.%s" % (user["id"], kind, secrets.token_hex(4), ext)
    img.save(os.path.join(upload_dir(), filename), fmt, **opts)
    remove_image(user, kind)
    db.update("users", user["id"], {kind: filename})
    return filename, None


def remove_image(user, kind):
    path = image_path(user, kind)
    if path:
        try:
            os.remove(path)
        except OSError:
            pass
    if (user or {}).get(kind):
        db.update("users", user["id"], {kind: ""})


# --- The profile form ----------------------------------------------------------
def clean_profile(form):
    """Check the profile fields.  Returns (values to store, error)."""
    values = {k: (form.get(k) or "").strip() for k in TEXT_FIELDS}
    if not values["first_name"] or not values["last_name"]:
        return None, "First name and last name are required."
    if values["gender"] and values["gender"] not in GENDERS:
        return None, "Choose a gender from the list."
    if values["suffix"] not in SUFFIXES:
        values["suffix"] = values["suffix"][:10]
    if values["birthdate"] and values["birthdate"] >= today_iso():
        return None, "The birthdate must be in the past."
    values["name"] = full_name(values["first_name"], values["middle_name"],
                               values["last_name"], values["suffix"])
    return values, None
