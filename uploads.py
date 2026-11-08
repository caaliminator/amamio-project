"""Pictures attached to records — e.g. the receipt of a supplier payment.

Stored under data/uploads/<folder>/ (git-ignored, like staff photos) and
served only through routes that check the reader's access.  Every upload is
checked to really be a picture, turned upright and saved as a JPEG no larger
than MAX_SIDE pixels, so a phone photo of a receipt stays small and readable.
"""
import os
import secrets
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

import db

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_SIDE = 1800
FORMATS = {"PNG", "JPEG", "MPO", "WEBP", "GIF", "BMP"}   # MPO: some phone cameras


def folder_path(folder):
    return os.path.join(db.DATA_DIR, "uploads", folder)


def read_picture(upload):
    """Check an uploaded file is a picture.  Returns (image, error); both
    None when nothing was chosen."""
    if not upload or not getattr(upload, "filename", ""):
        return None, None
    data = upload.read(MAX_UPLOAD_BYTES + 1)
    if not data:
        return None, "The picture is empty."
    if len(data) > MAX_UPLOAD_BYTES:
        return None, "The picture is too large — use one under 10 MB."
    try:
        img = Image.open(BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError):
        return None, "That file isn't a picture. Use a JPG or PNG photo or screenshot."
    if img.format not in FORMATS:
        return None, "Use a JPG or PNG photo or screenshot."
    return ImageOps.exif_transpose(img), None


def save_picture(folder, prefix, img):
    """Store a picture from read_picture().  Returns the file name."""
    img = img.convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    os.makedirs(folder_path(folder), exist_ok=True)
    filename = "%s-%s.jpg" % (prefix, secrets.token_hex(6))
    img.save(os.path.join(folder_path(folder), filename), "JPEG", quality=85)
    return filename


def picture_path(folder, filename):
    """Absolute path of a stored picture, or None."""
    if not filename:
        return None
    path = os.path.join(folder_path(folder), os.path.basename(filename))
    return path if os.path.isfile(path) else None


# --- Attachments: a picture or a PDF ------------------------------------------
# e.g. the official receipt, invoice, deposit slip or check copy of a supplier
# payment.  Pictures are stored like read_picture()/save_picture(); a PDF is
# kept as it is (checked to really be a PDF).
def _safe_name(filename):
    name = os.path.basename((filename or "").replace("\\", "/")).strip()
    return name[:120] or "attachment"


def read_attachment(upload):
    """Check an uploaded picture or PDF.  Returns (attachment, error); both
    None when nothing was chosen.  attachment: {kind, name, image | data}."""
    if not upload or not getattr(upload, "filename", ""):
        return None, None
    data = upload.read(MAX_UPLOAD_BYTES + 1)
    name = _safe_name(upload.filename)
    if not data:
        return None, "%s is empty." % name
    if len(data) > MAX_UPLOAD_BYTES:
        return None, "%s is too large — use a file under 10 MB." % name
    if data[:5] == b"%PDF-":
        return {"kind": "pdf", "name": name, "data": data}, None
    try:
        img = Image.open(BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError):
        return None, "%s isn't a picture or a PDF." % name
    if img.format not in FORMATS:
        return None, "%s: use a JPG or PNG picture, or a PDF." % name
    return {"kind": "image", "name": name, "image": ImageOps.exif_transpose(img)}, None


def save_attachment(folder, prefix, attachment):
    """Store an attachment from read_attachment().  Returns the file name."""
    if attachment["kind"] == "image":
        return save_picture(folder, prefix, attachment["image"])
    os.makedirs(folder_path(folder), exist_ok=True)
    filename = "%s-%s.pdf" % (prefix, secrets.token_hex(6))
    with open(os.path.join(folder_path(folder), filename), "wb") as f:
        f.write(attachment["data"])
    return filename
