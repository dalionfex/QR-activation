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
from PIL import Image, ImageDraw
from flask import (
    Flask, jsonify, render_template, request,
    session, redirect, url_for, send_file, abort,
)

from models import db, User, QRCode, ScanLog

# ═══════════════════════════════════════════════════════════════════
# App config
# ═══════════════════════════════════════════════════════════════════
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-prod")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///qr_saas.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024  # 4 MB upload limit

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "static", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Base URL for QR codes — set this to your public URL when deployed
# e.g. export BASE_URL=https://yourdomain.com
BASE_URL = os.environ.get("BASE_URL", "")

db.init_app(app)

with app.app_context():
    db.create_all()


# ═══════════════════════════════════════════════════════════════════
# Auth helpers
# ═══════════════════════════════════════════════════════════════════
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"error": "unauthorized"}), 401
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated


def current_user():
    uid = session.get("user_id")
    if uid:
        return db.session.get(User, uid)
    return None


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
    return jsonify({"ok": True, "user": {"id": user.id, "name": user.name, "email": user.email, "avatar": user.avatar_url}})


@app.route("/api/auth/login", methods=["POST"])
def login():
    data = request.get_json(force=True)
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400
    user = User.query.filter_by(email=email).first()
    if not user or not user.check_password(password):
        return jsonify({"error": "Invalid email or password"}), 401
    session["user_id"] = user.id
    return jsonify({"ok": True, "user": {"id": user.id, "name": user.name, "email": user.email, "avatar": user.avatar_url}})


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
    return jsonify({"ok": True, "user": {"id": user.id, "name": user.name, "email": user.email, "avatar": user.avatar_url}})


@app.route("/api/auth/me")
@login_required
def auth_me():
    u = current_user()
    return jsonify({"id": u.id, "name": u.name, "email": u.email, "avatar": u.avatar_url})


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
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else "png"
    fname = f"{uuid.uuid4().hex}.{ext}"
    path = os.path.join(UPLOAD_DIR, fname)
    f.save(path)
    code.logo_path = f"/static/uploads/{fname}"
    db.session.commit()
    return jsonify({"ok": True, "logo_path": code.logo_path})


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
        logo_file = os.path.join(os.path.dirname(__file__), code.logo_path.lstrip("/"))
        if os.path.exists(logo_file):
            logo = Image.open(logo_file).convert("RGBA")
            logo_size = int(img.size[0] * 0.25)
            logo = logo.resize((logo_size, logo_size), Image.LANCZOS)
            pos = ((img.size[0] - logo_size) // 2, (img.size[1] - logo_size) // 2)
            # White background behind logo
            white_bg = Image.new("RGBA", (logo_size + 16, logo_size + 16), bg)
            img.paste(white_bg, (pos[0] - 8, pos[1] - 8))
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
# Page routes
# ═══════════════════════════════════════════════════════════════════
@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard_page"))
    return redirect(url_for("login_page"))


@app.route("/login")
def login_page():
    return render_template("login.html")


@app.route("/dashboard")
@login_required
def dashboard_page():
    return render_template("dashboard.html")


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
    return render_template("landing.html", code=code)


# ═══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5222)
