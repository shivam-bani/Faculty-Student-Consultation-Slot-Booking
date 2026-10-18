"""Tests for the Flask app. Needs the MySQL service running and .env in place.

Run from the project folder (venv active):
    python -m unittest discover -s tests -v
"""
import unittest
from pathlib import Path
from unittest import mock

import db
from app import app

STATIC = Path(__file__).resolve().parent.parent / "static"


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_health(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json(), {"ok": True})

    def test_health_db_when_database_is_up(self):
        r = self.client.get("/api/health/db")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json(), {"ok": True, "database": "campussync"})

    def test_health_db_when_login_fails(self):
        with mock.patch.dict(db.DB_SETTINGS, {"password": "wrong-password"}), \
             mock.patch.object(app.logger, "disabled", True):
            r = self.client.get("/api/health/db")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.get_json(), {"ok": False, "error": "Database unavailable"})


class StaticFileTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_every_page_and_asset_is_served(self):
        files = sorted(p.name for p in STATIC.iterdir())
        self.assertEqual(len([f for f in files if f.endswith(".html")]), 15)
        for name in ["", *files]:
            with self.subTest(file=name), self.client.get("/" + name) as r:
                self.assertEqual(r.status_code, 200)

    def test_project_files_are_not_served(self):
        for path in ["/app.py", "/db.py", "/.env", "/schema.sql", "/requirements.txt",
                     "/tools/setup_db.py", "/../app.py", "/%2e%2e/app.py"]:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)


class TransactionTests(unittest.TestCase):
    """A request that fails must not leave half-finished changes in the database."""

    def count_rows(self):
        with app.app_context():
            cur = db.get_db().cursor()
            cur.execute("SELECT COUNT(*) FROM timetable_profiles WHERE id = 'unittest-row'")
            return cur.fetchone()[0]

    def test_failed_request_is_rolled_back(self):
        with self.assertRaises(RuntimeError):
            with app.app_context():
                db.get_db().cursor().execute(
                    "INSERT INTO timetable_profiles (id, name, source) VALUES ('unittest-row', 'x', 'admin')")
                raise RuntimeError("simulated failure inside a route")
        self.assertEqual(self.count_rows(), 0)

    def test_committed_change_is_kept(self):
        try:
            with app.app_context():
                conn = db.get_db()
                conn.cursor().execute(
                    "INSERT INTO timetable_profiles (id, name, source) VALUES ('unittest-row', 'x', 'admin')")
                conn.commit()
            self.assertEqual(self.count_rows(), 1)
        finally:
            with app.app_context():
                conn = db.get_db()
                conn.cursor().execute("DELETE FROM timetable_profiles WHERE id = 'unittest-row'")
                conn.commit()


if __name__ == "__main__":
    unittest.main()
