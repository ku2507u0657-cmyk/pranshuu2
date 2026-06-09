"""
app.py — InvoiceFlow Flask Application Factory
Multi-user: every Client/Invoice/Bill is scoped to its owner Admin.
"""
import os
from flask import Flask
from sqlalchemy import inspect, text
from config import get_config
from extensions import db, migrate, login_manager


def create_app(config_class=None):
    app = Flask(__name__)
    app.config.from_object(config_class or get_config())

    os.makedirs(app.config.get("PDF_FOLDER", "invoices"), exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)

    from routes.main     import main_bp
    from routes.auth     import auth_bp
    from routes.clients  import clients_bp
    from routes.products import products_bp
    from routes.invoices import invoices_bp
    from routes.bills    import bills_bp
    from routes.reports import reports_bp


    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(clients_bp)
    app.register_blueprint(products_bp)
    app.register_blueprint(invoices_bp)
    app.register_blueprint(bills_bp)
    app.register_blueprint(reports_bp)

    from models import Admin

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(Admin, int(user_id))

    with app.app_context():
        db.create_all()
        _ensure_compatible_schema(app)
        _seed_admin(app)

    from scheduler import init_scheduler
    init_scheduler(app)

    @app.shell_context_processor
    def make_shell_context():
        from models import Admin, Client, Product, Invoice, Bill
        return {"db": db, "Admin": Admin, "Client": Client,
                "Product": Product, "Invoice": Invoice, "Bill": Bill}

    return app


def _seed_admin(app):
    from models import Admin
    username = os.environ.get("ADMIN_USERNAME", "admin")
    password = os.environ.get("ADMIN_PASSWORD", "changeme123")
    if not Admin.query.filter_by(username=username).first():
        admin = Admin(username=username)
        admin.set_password(password)
        db.session.add(admin)
        db.session.commit()
        app.logger.info("Seeded default admin: '%s'", username)


def _ensure_compatible_schema(app):
    """
    Add missing columns to existing databases.

    The project currently relies on db.create_all(), which creates missing
    tables but does not alter existing tables. These additive columns/defaulted
    fields keep old invoice and bill records compatible with the current models.
    """
    table_columns = {
        "clients": {
            "business_name": "VARCHAR(200)",
            "city": "VARCHAR(100)",
            "state": "VARCHAR(100)",
        },
        "invoices": {
            "transaction_type": "VARCHAR(10) NOT NULL DEFAULT 'in'",
            "product_id": "INTEGER",
            "product_quantity": "NUMERIC(12, 3)",
        },
        "bills": {
            "transaction_type": "VARCHAR(10) NOT NULL DEFAULT 'out'",
        },
    }

    try:
        with db.engine.begin() as conn:
            inspector = inspect(conn)
            existing_tables = set(inspector.get_table_names())

            for table_name, columns in table_columns.items():
                if table_name not in existing_tables:
                    continue

                existing_columns = {
                    column["name"] for column in inspector.get_columns(table_name)
                }
                for column_name, ddl_type in columns.items():
                    if column_name not in existing_columns:
                        conn.execute(
                            text(
                                f"ALTER TABLE {table_name} "
                                f"ADD COLUMN {column_name} {ddl_type}"
                            )
                        )
                        app.logger.info("Added %s.%s column", table_name, column_name)
    except Exception as exc:
        app.logger.exception("Could not verify database schema: %s", exc)
        raise


app = create_app()

if __name__ == "__main__":
    app.run(debug=app.config.get("DEBUG", False),
            host="0.0.0.0",
            port=int(os.environ.get("PORT", 5000)))
