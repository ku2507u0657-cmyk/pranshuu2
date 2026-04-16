"""
app.py — InvoiceFlow Flask Application Factory
Multi-user: every Client/Invoice/Bill is scoped to its owner Admin.
"""
import os
from flask import Flask
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
    from routes.invoices import invoices_bp
    from routes.bills    import bills_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(clients_bp)
    app.register_blueprint(invoices_bp)
    app.register_blueprint(bills_bp)

    from models import Admin

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(Admin, int(user_id))

    with app.app_context():
        db.create_all()
        _seed_admin(app)

    from scheduler import init_scheduler
    init_scheduler(app)

    @app.shell_context_processor
    def make_shell_context():
        from models import Admin, Client, Invoice, Bill
        return {"db": db, "Admin": Admin, "Client": Client,
                "Invoice": Invoice, "Bill": Bill}

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


app = create_app()

if __name__ == "__main__":
    app.run(debug=app.config.get("DEBUG", False),
            host="0.0.0.0",
            port=int(os.environ.get("PORT", 5000)))
