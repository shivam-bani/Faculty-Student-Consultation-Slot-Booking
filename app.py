"""CampusSync backend (Flask).

Serves the frontend from static/ and the JSON API under /api/.
Run with:  python app.py   then open http://127.0.0.1:5000/
"""
import os

import mysql.connector
from flask import Flask, jsonify

import auth
import bookings
import commands
import db  # also loads .env
import demo
import faculty
import notifications
import queue_manager
import timetable

# static_url_path="" serves static/index.html at /index.html (not /static/index.html),
# so the pages' relative links (style.css, script.js, book.html, ...) keep working unchanged.
# Only files inside static/ are served: app.py, .env and the rest of the project are not reachable.
app = Flask(__name__, static_folder="static", static_url_path="")

if not os.getenv("SECRET_KEY"):
    raise RuntimeError("SECRET_KEY is missing from .env. Run: python tools/setup_db.py")
app.config.update(
    SECRET_KEY=os.environ["SECRET_KEY"],  # signs the login cookie so the browser cannot change it
    SESSION_COOKIE_HTTPONLY=True,         # page scripts cannot read the cookie
    SESSION_COOKIE_SAMESITE="Lax",        # other sites cannot send it with their requests
)


def minutes_setting(name, default, lowest, highest):
    """A whole number of minutes from .env, checked once at start-up."""
    try:
        value = int(os.getenv(name, default))
    except ValueError:
        value = None
    if value is None or not lowest <= value <= highest:
        raise RuntimeError(f"{name} in .env must be a whole number of minutes from {lowest} to {highest}.")
    return value


# Queue manager (queue_manager.py): one priority level is added per AGING_MINUTES of waiting, and
# students can press "I'm here" from CHECKIN_EARLY_MINUTES before their slot starts.
app.config["AGING_MINUTES"] = minutes_setting("AGING_MINUTES", 10, 1, 120)
app.config["CHECKIN_EARLY_MINUTES"] = minutes_setting("CHECKIN_EARLY_MINUTES", 10, 0, 120)

db.init_app(app)
commands.init_app(app)
demo.init_app(app)
app.register_blueprint(auth.bp)
app.register_blueprint(bookings.bp)
app.register_blueprint(faculty.bp)
app.register_blueprint(notifications.bp)
app.register_blueprint(queue_manager.bp)
app.register_blueprint(timetable.bp)


@app.get("/")
def home():
    # The login page is the entry point, exactly as when index.html was opened directly.
    return app.send_static_file("index.html")


@app.get("/api/health")
def health():
    # A tiny endpoint that proves the server is running and returns JSON.
    return jsonify(ok=True)


@app.get("/api/health/db")
def health_db():
    # Proves the app can log in to MySQL with the .env settings and run a query.
    try:
        cur = db.get_db().cursor()
        cur.execute("SELECT DATABASE()")
        database = cur.fetchone()[0]
    except mysql.connector.Error:
        # The details go to the server console only; the browser gets a short message.
        app.logger.exception("Database health check failed")
        return jsonify(ok=False, error="Database unavailable"), 503
    return jsonify(ok=True, database=database)


if __name__ == "__main__":
    # host="127.0.0.1": reachable only from this laptop.
    # debug=True: auto-reload on code changes and show detailed errors. Never expose a
    # debug server to a network, because its error page can run Python code.
    app.run(host="127.0.0.1", port=5000, debug=True)
