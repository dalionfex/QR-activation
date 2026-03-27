from app import app, db
from sqlalchemy import text, inspect

def run_migrations():
    with app.app_context():
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
            conn.commit()
            print("DB Migration applied successfully")

if __name__ == "__main__":
    run_migrations()
