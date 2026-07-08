"""
QR SaaS — Main Flask Application
==================================
Full-stack dynamic QR code management SaaS.
"""

import io
import os
import uuid
import math
from datetime import datetime, timezone, timedelta
from functools import wraps

import qrcode
import qrcode.image.styledpil
import qrcode.image.styles.moduledrawers
import requests
from PIL import Image, ImageDraw
from flask import (
    Flask, jsonify, render_template, request,
    session, redirect, url_for, send_file, abort,
)

from models import db, User, QRCode, ScanLog, GlobalSettings

# ═══════════════════════════════════════════════════════════════════
# App config
# ═══════════════════════════════════════════════════════════════════
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-prod")
# Database config
# On Vercel the deployment filesystem is read-only except /tmp, so without a real
# DATABASE_URL (e.g. Neon/Supabase Postgres) fall back to an ephemeral SQLite file
# in /tmp — enough to boot and test the UI, but data resets on every cold start.
db_url = os.environ.get("DATABASE_URL", "")
if not db_url:
    db_url = "sqlite:////tmp/qr_saas.db" if os.environ.get("VERCEL") else "sqlite:///qr_saas.db"
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024  # 4 MB upload limit

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "static", "uploads")
try:
    os.makedirs(UPLOAD_DIR, exist_ok=True)
except OSError:
    # Read-only filesystem (e.g. Vercel serverless) — uploads go to Blob storage instead.
    pass
app.config['UPLOAD_FOLDER'] = UPLOAD_DIR # Added for settings_logo route

# Vercel Blob storage — set BLOB_READ_WRITE_TOKEN to store uploads there instead of
# on local disk (required on Vercel, whose serverless filesystem is read-only/ephemeral).
BLOB_TOKEN = os.environ.get("BLOB_READ_WRITE_TOKEN", "")


def save_upload(file_storage, prefix):
    """Save an uploaded file, returning the path/URL to store on the model.

    Uses Vercel Blob when BLOB_READ_WRITE_TOKEN is set, otherwise falls back to
    local disk under static/uploads (used for local dev and non-Vercel hosts).
    """
    ext = file_storage.filename.rsplit(".", 1)[-1].lower() if "." in file_storage.filename else "png"
    fname = f"{prefix}{uuid.uuid4().hex}.{ext}"

    if BLOB_TOKEN:
        resp = requests.put(
            f"https://blob.vercel-storage.com/{fname}",
            data=file_storage.read(),
            headers={
                "Authorization": f"Bearer {BLOB_TOKEN}",
                "x-api-version": "7",
                "content-type": file_storage.mimetype or "application/octet-stream",
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["url"]

    path = os.path.join(UPLOAD_DIR, fname)
    file_storage.save(path)
    return f"/static/uploads/{fname}"


def open_image(path_or_url):
    """Open a PIL image from either a local static path or a remote Blob URL."""
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        resp = requests.get(path_or_url, timeout=15)
        resp.raise_for_status()
        return Image.open(io.BytesIO(resp.content)).convert("RGBA")
    local_path = os.path.join(os.path.dirname(__file__), path_or_url.lstrip("/"))
    return Image.open(local_path).convert("RGBA")

# Base URL for QR codes — set this to your public URL when deployed
# e.g. export BASE_URL=https://yourdomain.com
BASE_URL = os.environ.get("BASE_URL", "")

db.init_app(app)

with app.app_context():
    db.create_all()
    if not GlobalSettings.query.first():
        db.session.add(GlobalSettings())
        db.session.commit()

# Constants
SUPER_ADMIN_EMAIL = "dalionfex@gmail.com"


# ═══════════════════════════════════════════════════════════════════
# Auth helpers
# ═══════════════════════════════════════════════════════════════════
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"error": "unauthorized"}), 401
            return redirect(url_for("login")) # Changed to 'login'
        return f(*args, **kwargs)
    return decorated


def current_user():
    uid = session.get("user_id")
    if uid:
        return db.session.get(User, uid)
    return None

def is_super_admin():
    u = current_user()
    return u is not None and u.email.lower() == SUPER_ADMIN_EMAIL

def super_admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_super_admin():
            abort(403)
        return f(*args, **kwargs)
    return decorated_function


# ═══════════════════════════════════════════════════════════════════
# Auth routes
# ═══════════════════════════════════════════════════════════════════
@app.route("/api/auth/register", methods=["POST"])
def register():
    data = request.get_json(force=True)
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    name = (data.get("name") or "").strip()
    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({"error": "This email is already registered"}), 409
    user = User(email=email, name=name or email.split("@")[0])
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    session["user_id"] = user.id
    return jsonify({"ok": True, "user": {"id": user.id, "name": user.name, "email": user.email, "avatar": user.avatar_url, "is_admin": user.email.lower() == SUPER_ADMIN_EMAIL}})


@app.route("/api/auth/login", methods=["POST"])
def login_api(): # Renamed to avoid conflict with login page route
    data = request.get_json(force=True)
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400
    user = User.query.filter_by(email=email).first()
    if not user or not user.check_password(password):
        return jsonify({"error": "Invalid email or password"}), 401
    session["user_id"] = user.id
    return jsonify({"ok": True, "user": {"id": user.id, "name": user.name, "email": user.email, "avatar": user.avatar_url, "is_admin": user.email.lower() == SUPER_ADMIN_EMAIL}})


@app.route("/api/auth/demo", methods=["POST"])
def demo_auth():
    """Demo login — creates a demo user."""
    user = User.query.filter_by(email="demo@qrsaas.local").first()
    if not user:
        user = User(email="demo@qrsaas.local", name="Demo User", avatar_url="")
        user.set_password("demo123")
        db.session.add(user)
        db.session.commit()
    session["user_id"] = user.id
    return jsonify({"ok": True, "user": {"id": user.id, "name": user.name, "email": user.email, "avatar": user.avatar_url, "is_admin": False}})


@app.route("/api/auth/me")
@login_required
def auth_me():
    u = current_user()
    return jsonify({"id": u.id, "name": u.name, "email": u.email, "avatar": u.avatar_url, "is_admin": u.email.lower() == SUPER_ADMIN_EMAIL})


@app.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    session.clear()
    return jsonify({"ok": True})


# ═══════════════════════════════════════════════════════════════════
# QR CRUD
# ═══════════════════════════════════════════════════════════════════
@app.route("/api/qr", methods=["GET"])
@login_required
def list_qr():
    codes = QRCode.query.filter_by(user_id=session["user_id"]).order_by(QRCode.created_at.desc()).all()
    return jsonify([c.to_dict() for c in codes])


@app.route("/api/qr", methods=["POST"])
@login_required
def create_qr():
    data = request.get_json(force=True) if request.is_json else {}
    code = QRCode(user_id=session["user_id"])
    _apply_qr_data(code, data)
    db.session.add(code)
    db.session.commit()
    return jsonify(code.to_dict()), 201


@app.route("/api/qr/<int:qr_id>", methods=["GET"])
@login_required
def get_qr(qr_id):
    code = QRCode.query.get_or_404(qr_id)
    if code.user_id != session["user_id"]:
        abort(403)
    return jsonify(code.to_dict())


@app.route("/api/qr/<int:qr_id>", methods=["PUT"])
@login_required
def update_qr(qr_id):
    code = QRCode.query.get_or_404(qr_id)
    if code.user_id != session["user_id"]:
        abort(403)
    data = request.get_json(force=True)
    _apply_qr_data(code, data)
    db.session.commit()
    return jsonify(code.to_dict())


@app.route("/api/qr/<int:qr_id>", methods=["DELETE"])
@login_required
def delete_qr(qr_id):
    code = QRCode.query.get_or_404(qr_id)
    if code.user_id != session["user_id"]:
        abort(403)
    db.session.delete(code)
    db.session.commit()
    return jsonify({"ok": True})


def _apply_qr_data(code, data):
    for field in [
        "title", "qr_type", "redirect_url",
        "fg_color", "bg_color", "dot_shape", "error_correction",
        "page_bg_color", "page_bg_image", "page_title", "page_description", "promo_code",
        "button_bg_color", "button_text_color",
        "logo_transparent", "logo_rounded", "landing_logo_rounded", "hide_landing_logo",
    ]:
        if field in data:
            setattr(code, field, data[field])


# ═══════════════════════════════════════════════════════════════════
# Logo upload
# ═══════════════════════════════════════════════════════════════════
@app.route("/api/qr/<int:qr_id>/logo", methods=["POST"])
@login_required
def upload_logo(qr_id):
    code = QRCode.query.get_or_404(qr_id)
    if code.user_id != session["user_id"]:
        abort(403)
    f = request.files.get("logo")
    if not f:
        return jsonify({"error": "no file"}), 400
    code.logo_path = save_upload(f, "logo_")
    db.session.commit()
    return jsonify({"ok": True, "logo_path": code.logo_path})

@app.route("/api/qr/<int:qr_id>/landing_logo", methods=["POST"])
@login_required
def upload_landing_logo(qr_id):
    code = QRCode.query.get_or_404(qr_id)
    if code.user_id != session["user_id"]:
        abort(403)
    f = request.files.get("logo")
    if not f:
        return jsonify({"error": "no file"}), 400
    code.landing_logo_path = save_upload(f, "landing_")
    db.session.commit()
    return jsonify({"ok": True, "landing_logo_path": code.landing_logo_path})


# ═══════════════════════════════════════════════════════════════════
# QR Image generation
# ═══════════════════════════════════════════════════════════════════
@app.route("/api/qr/<int:qr_id>/image")
def qr_image(qr_id):
    code = QRCode.query.get_or_404(qr_id)
    host = BASE_URL or request.host_url.rstrip("/")
    if code.qr_type == "redirect" and code.redirect_url:
        url = code.redirect_url
    else:
        url = f"{host}/go/{code.slug}"

    ec_map = {"L": qrcode.constants.ERROR_CORRECT_L, "M": qrcode.constants.ERROR_CORRECT_M,
              "Q": qrcode.constants.ERROR_CORRECT_Q, "H": qrcode.constants.ERROR_CORRECT_H}
    ec = ec_map.get(code.error_correction, qrcode.constants.ERROR_CORRECT_H)

    qr = qrcode.QRCode(version=None, error_correction=ec, box_size=10, border=4)
    qr.add_data(url)
    qr.make(fit=True)

    fg = code.fg_color or "#000000"
    bg = code.bg_color or "#FFFFFF"

    img = qr.make_image(fill_color=fg, back_color=bg).convert("RGBA")

    # Apply dot shape
    if code.dot_shape in ("rounded", "diamond"):
        img = _apply_dot_shape(qr, fg, bg, code.dot_shape)

    # Overlay logo
    if code.logo_path:
        try:
            logo = open_image(code.logo_path)
        except (requests.RequestException, OSError):
            logo = None
        if logo is not None:
            logo_size = int(img.size[0] * 0.25)
            logo = logo.resize((logo_size, logo_size), Image.LANCZOS)
            
            if code.logo_rounded:
                mask = Image.new("L", logo.size, 0)
                draw = ImageDraw.Draw(mask)
                draw.ellipse((0, 0, logo_size, logo_size), fill=255)
                if "A" in logo.getbands():
                    alpha = logo.split()[3]
                    mask = Image.composite(alpha, Image.new("L", logo.size, 0), mask)
                logo.putalpha(mask)

            pos = ((img.size[0] - logo_size) // 2, (img.size[1] - logo_size) // 2)
            
            if not code.logo_transparent:
                bg_size = logo_size + 16
                white_bg = Image.new("RGBA", (bg_size, bg_size), bg)
                bg_pos = (pos[0] - 8, pos[1] - 8)
                if code.logo_rounded:
                    bg_mask = Image.new("L", (bg_size, bg_size), 0)
                    draw_bg = ImageDraw.Draw(bg_mask)
                    draw_bg.ellipse((0, 0, bg_size, bg_size), fill=255)
                    img.paste(white_bg, bg_pos, mask=bg_mask)
                else:
                    img.paste(white_bg, bg_pos)
            
            img.paste(logo, pos, logo)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png", download_name=f"qr-{code.slug}.png")


def _apply_dot_shape(qr, fg, bg, shape):
    """Redraw QR with rounded or diamond dots."""
    box = 10
    border = 4
    matrix = qr.get_matrix()
    rows = len(matrix)
    cols = len(matrix[0]) if rows else 0
    w = (cols + border * 2) * box
    h = (rows + border * 2) * box
    img = Image.new("RGBA", (w, h), bg)
    draw = ImageDraw.Draw(img)

    for r, row in enumerate(matrix):
        for c, val in enumerate(row):
            if not val:
                continue
            x = (c + border) * box
            y = (r + border) * box
            if shape == "rounded":
                draw.rounded_rectangle([x, y, x + box, y + box], radius=box // 3, fill=fg)
            elif shape == "diamond":
                cx, cy = x + box // 2, y + box // 2
                half = box // 2
                draw.polygon([(cx, cy - half), (cx + half, cy), (cx, cy + half), (cx - half, cy)], fill=fg)
    return img


# ═══════════════════════════════════════════════════════════════════
# Analytics
# ═══════════════════════════════════════════════════════════════════
@app.route("/api/qr/<int:qr_id>/stats")
@login_required
def qr_stats(qr_id):
    code = QRCode.query.get_or_404(qr_id)
    if code.user_id != session["user_id"]:
        abort(403)

    scans = ScanLog.query.filter_by(qr_id=qr_id, event_type="scan").all()
    copies = ScanLog.query.filter_by(qr_id=qr_id, event_type="copy_promo").all()
    unique_sessions = len(set(s.session_id for s in scans if s.session_id))

    total_scans = len(scans)
    total_copies = len(copies)
    ctr = round(total_copies / total_scans * 100, 1) if total_scans > 0 else 0

    # Daily scans for last 30 days
    now = datetime.now(timezone.utc)
    daily = {}
    for i in range(30):
        day = (now - timedelta(days=29 - i)).strftime("%Y-%m-%d")
        daily[day] = 0
    for s in scans:
        day = s.timestamp.strftime("%Y-%m-%d")
        if day in daily:
            daily[day] += 1

    return jsonify({
        "total_scans": total_scans,
        "unique_visitors": unique_sessions,
        "total_copies": total_copies,
        "ctr": ctr,
        "daily_scans": daily,
    })


# ═══════════════════════════════════════════════════════════════════
# Event tracking (public)
# ═══════════════════════════════════════════════════════════════════
@app.route("/api/qr/<int:qr_id>/event", methods=["POST"])
def log_event(qr_id):
    code = QRCode.query.get_or_404(qr_id)
    data = request.get_json(force=True) if request.is_json else {}
    event_type = data.get("event", "scan")
    if event_type not in ("scan", "copy_promo"):
        event_type = "scan"
    sid = data.get("session_id", "")
    log = ScanLog(qr_id=code.id, event_type=event_type, session_id=sid)
    db.session.add(log)
    db.session.commit()
    return jsonify({"ok": True})


# ═══════════════════════════════════════════════════════════════════
# Super Admin & Settings
# ═══════════════════════════════════════════════════════════════════
@app.route("/api/settings", methods=["GET"])
def get_settings():
    s = GlobalSettings.query.first()
    return jsonify(s.to_dict())


@app.route('/api/settings', methods=['PUT'])
@login_required
@super_admin_required
def update_settings():
    data = request.json
    s = GlobalSettings.query.first()
    for field in ["platform_name", "admin_message_show", "admin_message_text", "admin_message_btn_text", "admin_message_btn_url"]:
        if field in data:
            setattr(s, field, data[field])
    db.session.commit()
    return jsonify({"ok": True})

@app.route('/api/settings/logo', methods=['POST', 'DELETE'])
@login_required
@super_admin_required
def settings_logo():
    s = GlobalSettings.query.first()
    if request.method == 'DELETE':
        s.platform_logo_path = ""
        db.session.commit()
        return jsonify({"ok": True})
    
    if 'logo' not in request.files:
        return jsonify({"error": "No file"}), 400
    file = request.files['logo']
    if file.filename == '':
        return jsonify({"error": "No file"}), 400

    s.platform_logo_path = save_upload(file, "platform_logo_")
    db.session.commit()
    return jsonify({"ok": True, "logo_url": s.platform_logo_path})


@app.route("/api/settings/icon", methods=["POST"])
@login_required
@super_admin_required # Added decorator
def upload_platform_icon():
    # if not is_super_admin(): # Removed check, replaced by decorator
    #     abort(403)
    f = request.files.get("icon")
    if not f:
        return jsonify({"error": "no file"}), 400
    s = GlobalSettings.query.first()
    s.admin_message_icon = save_upload(f, "icon_")
    db.session.commit()
    return jsonify({"ok": True, "icon_path": s.admin_message_icon})


@app.route("/api/admin/stats", methods=["GET"])
@login_required
@super_admin_required # Added decorator
def admin_stats():
    # if not is_super_admin(): # Removed check, replaced by decorator
    #     abort(403)
    
    now = datetime.utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week = today - timedelta(days=today.weekday())
    month = today.replace(day=1)
    year = today.replace(month=1, day=1)

    all_qrs = QRCode.query.all()
    qrs_today = sum(1 for q in all_qrs if q.created_at >= today)
    qrs_week = sum(1 for q in all_qrs if q.created_at >= week)
    qrs_month = sum(1 for q in all_qrs if q.created_at >= month)
    qrs_year = sum(1 for q in all_qrs if q.created_at >= year)

    active_users = User.query.count()

    users = User.query.order_by(User.created_at.desc()).all()
    users_data = [{
        "id": u.id,
        "name": u.name,
        "email": u.email,
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "qrs_count": len(u.qr_codes)
    } for u in users]

    return jsonify({
        "qrs": {
            "today": qrs_today,
            "week": qrs_week,
            "month": qrs_month,
            "year": qrs_year,
            "all_time": len(all_qrs)
        },
        "active_users": active_users,
        "users_list": users_data
    })


# ═══════════════════════════════════════════════════════════════════
# Page routes
# ═══════════════════════════════════════════════════════════════════
@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard_page"))
    return redirect(url_for("login"))


@app.route('/login')
def login():
    settings = GlobalSettings.query.first()
    return render_template('login.html', settings=settings)


@app.route("/dashboard")
@login_required
def dashboard_page():
    return render_template("dashboard.html", settings=GlobalSettings.query.first(), is_admin=is_super_admin())


@app.route("/go/<slug>")
def landing_page(slug):
    code = QRCode.query.filter_by(slug=slug).first_or_404()
    # For redirect type, do a 302 redirect (and log scan via JS on landing page)
    if code.qr_type == "redirect" and code.redirect_url:
        # Log scan before redirect
        sid = request.args.get("sid", "")
        log = ScanLog(qr_id=code.id, event_type="scan", session_id=sid)
        db.session.add(log)
        db.session.commit()
        return redirect(code.redirect_url)
    return render_template("landing.html", code=code, settings=GlobalSettings.query.first())


# ═══════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        # Auto-migrate existing DB 
        from sqlalchemy import text, inspect
        with db.engine.connect() as conn:
            inspector = inspect(db.engine)
            if inspector.has_table("qr_codes"):
                cols = [c["name"] for c in inspector.get_columns("qr_codes")]
                if "logo_transparent" not in cols:
                    conn.execute(text("ALTER TABLE qr_codes ADD COLUMN logo_transparent BOOLEAN DEFAULT 0"))
                if "logo_rounded" not in cols:
                    conn.execute(text("ALTER TABLE qr_codes ADD COLUMN logo_rounded BOOLEAN DEFAULT 0"))
                if "landing_logo_rounded" not in cols:
                    conn.execute(text("ALTER TABLE qr_codes ADD COLUMN landing_logo_rounded BOOLEAN DEFAULT 0"))
                if "hide_landing_logo" not in cols:
                    conn.execute(text("ALTER TABLE qr_codes ADD COLUMN hide_landing_logo BOOLEAN DEFAULT 0"))
            
            if inspector.has_table("global_settings"):
                cols = [c["name"] for c in inspector.get_columns("global_settings")]
                if "admin_message_btn_text" not in cols:
                    conn.execute(text("ALTER TABLE global_settings ADD COLUMN admin_message_btn_text VARCHAR(64) DEFAULT 'View'"))
                if "platform_logo_path" not in cols:
                    conn.execute(text("ALTER TABLE global_settings ADD COLUMN platform_logo_path VARCHAR(512) DEFAULT ''"))
            conn.commit()
    app.run(host='0.0.0.0', port=5222, debug=True)
