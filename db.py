"""MySQL connection for the CampusSync backend.

Each request gets its own connection the first time it calls get_db(); Flask closes it when
the request ends. Settings come from .env (written by tools/setup_db.py).

Autocommit is OFF: a route that changes data must call get_db().commit() when everything
succeeded. If the request ends with an error, its uncommitted changes are rolled back.
"""
import os
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv
from flask import g

load_dotenv(Path(__file__).resolve().parent / ".env")

DB_SETTINGS = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "database": os.getenv("DB_NAME", "campussync"),
    "user": os.getenv("DB_USER", "campussync_app"),
    "password": os.getenv("DB_PASSWORD", ""),
    "autocommit": False,
}


def get_db():
    """Return this request's database connection, opening it on first use."""
    if "db" not in g:
        g.db = mysql.connector.connect(**DB_SETTINGS)
    return g.db


def close_db(error=None):
    """Called by Flask at the end of every request."""
    db = g.pop("db", None)
    if db is not None:
        if error is not None:
            db.rollback()  # never keep half-finished changes from a failed request
        db.close()


def init_app(app):
    app.teardown_appcontext(close_db)
