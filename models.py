"""
Database models for QR SaaS
"""
import uuid
from datetime import datetime, timezone
from werkzeug.security import generate_password_hash, check_password_hash

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def gen_slug():
    return uuid.uuid4().hex[:8]


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    google_id = db.Column(db.String(128), unique=True, nullable=True)
    email = db.Column(db.String(256), unique=True, nullable=False)
    name = db.Column(db.String(256), nullable=False)
    password_hash = db.Column(db.String(256), nullable=True)
    avatar_url = db.Column(db.String(512), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    qr_codes = db.relationship("QRCode", backref="owner", lazy=True, cascade="all, delete-orphan")


class QRCode(db.Model):
    __tablename__ = "qr_codes"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    slug = db.Column(db.String(16), unique=True, default=gen_slug)

    # QR style
    # QR type: "landing" = show landing page, "redirect" = 302 to URL
    qr_type = db.Column(db.String(16), default="landing")
    redirect_url = db.Column(db.String(1024), default="")

    title = db.Column(db.String(256), default="My QR Code")
    fg_color = db.Column(db.String(9), default="#000000")
    bg_color = db.Column(db.String(9), default="#FFFFFF")
    dot_shape = db.Column(db.String(16), default="square")  # square | rounded | diamond
    logo_path = db.Column(db.String(512), default="")
    logo_transparent = db.Column(db.Boolean, default=False)
    logo_rounded = db.Column(db.Boolean, default=False)
    error_correction = db.Column(db.String(1), default="H")

    # Landing page
    landing_logo_path = db.Column(db.String(512), default="")
    landing_logo_rounded = db.Column(db.Boolean, default=False)
    hide_landing_logo = db.Column(db.Boolean, default=False)
    page_bg_color = db.Column(db.String(9), default="#0f0c29")
    page_bg_image = db.Column(db.String(512), default="")
    page_title = db.Column(db.String(256), default="Welcome!")
    page_description = db.Column(db.Text, default="Thanks for scanning!")
    promo_code = db.Column(db.String(64), default="PROMO2026")
    button_bg_color = db.Column(db.String(9), default="#8b5cf6")
    button_text_color = db.Column(db.String(9), default="#ffffff")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    scans = db.relationship("ScanLog", backref="qr_code", lazy=True, cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "slug": self.slug,
            "qr_type": self.qr_type,
            "redirect_url": self.redirect_url,
            "title": self.title,
            "fg_color": self.fg_color,
            "bg_color": self.bg_color,
            "dot_shape": self.dot_shape,
            "logo_path": self.logo_path,
            "logo_transparent": self.logo_transparent,
            "logo_rounded": self.logo_rounded,
            "error_correction": self.error_correction,
            "landing_logo_path": self.landing_logo_path,
            "landing_logo_rounded": self.landing_logo_rounded,
            "hide_landing_logo": self.hide_landing_logo,
            "page_bg_color": self.page_bg_color,
            "page_bg_image": self.page_bg_image,
            "page_title": self.page_title,
            "page_description": self.page_description,
            "promo_code": self.promo_code,
            "button_bg_color": self.button_bg_color,
            "button_text_color": self.button_text_color,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "total_scans": len([s for s in self.scans if s.event_type == "scan"]),
        }


class ScanLog(db.Model):
    __tablename__ = "scan_logs"

    id = db.Column(db.Integer, primary_key=True)
    qr_id = db.Column(db.Integer, db.ForeignKey("qr_codes.id"), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    session_id = db.Column(db.String(64), default="")
    event_type = db.Column(db.String(16), default="scan")  # scan | copy_promo


class GlobalSettings(db.Model):
    __tablename__ = "global_settings"

    id = db.Column(db.Integer, primary_key=True)
    platform_name = db.Column(db.String(128), default="QR SaaS")
    platform_logo_path = db.Column(db.String(512), default="")
    admin_message_show = db.Column(db.Boolean, default=False)
    admin_message_show = db.Column(db.Boolean, default=False)
    admin_message_text = db.Column(db.String(512), default="Welcome to QR SaaS!")
    admin_message_icon = db.Column(db.String(512), default="")
    admin_message_btn_text = db.Column(db.String(64), default="View")
    admin_message_btn_url = db.Column(db.String(1024), default="")

    def to_dict(self):
        return {
            "platform_name": self.platform_name,
            "platform_logo_path": self.platform_logo_path,
            "admin_message_show": self.admin_message_show,
            "admin_message_text": self.admin_message_text,
            "admin_message_icon": self.admin_message_icon,
            "admin_message_btn_text": self.admin_message_btn_text,
            "admin_message_btn_url": self.admin_message_btn_url,
        }

