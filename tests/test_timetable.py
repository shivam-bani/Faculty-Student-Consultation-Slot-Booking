"""Tests for the timetable API (timetable.py). Needs MySQL running and .env in place.

Run from the project folder (venv active):
    python -m unittest discover -s tests -v
"""
import unittest

from werkzeug.security import generate_password_hash

import commands
import db
from app import app

PASSWORD = "Test-Pass-123"
PID = "unittest-tt"
CLASS = {"day": 2, "start": "10:10", "end": "11:05", "code": "tcs-502", "subject": "Operating Systems",
         "section": "B.Tech CSE V-A", "room": "LT-6"}


def execute(sql, params=()):
    with app.app_context():
        conn = db.get_db()
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        return cur.lastrowid


def query_one(sql, params=()):
    with app.app_context():
        cur = db.get_db().cursor()
        cur.execute(sql, params)
        return cur.fetchone()


def clean_up():
    execute("DELETE s FROM faculty_settings s JOIN users u ON u.id = s.faculty_id WHERE u.email LIKE 'unittest-%'")
    execute("DELETE FROM users WHERE email LIKE 'unittest-%'")
    execute("DELETE FROM timetable_profiles WHERE id LIKE 'unittest-%'")


class TimetableTestCase(unittest.TestCase):
    def setUp(self):
        clean_up()
        for email, role in [("unittest-admin@example.com", "admin"), ("unittest-student@geu.ac.in", "student")]:
            execute("INSERT INTO users (name, email, password_hash, role) VALUES (%s, %s, %s, %s)",
                    ("Unit Test", email, generate_password_hash(PASSWORD), role))
        execute("INSERT INTO timetable_profiles (id, name, source) VALUES (%s, 'Dr. Unit Test', 'admin')", (PID,))
        self.admin = self.client_for("unittest-admin@example.com")
        self.student = self.client_for("unittest-student@geu.ac.in")

    def tearDown(self):
        clean_up()

    def client_for(self, email):
        client = app.test_client()
        self.assertEqual(client.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code, 200)
        return client

    def timetable(self, pid=PID, client=None):
        data = (client or self.admin).get("/api/timetables").get_json()["timetables"]
        return next(t for t in data if t["id"] == pid)

    def add(self, **changes):
        return self.admin.post(f"/api/admin/timetables/{PID}/classes", json={**CLASS, **changes})


class ReadTests(TimetableTestCase):
    def test_every_logged_in_user_can_read_all_timetables(self):
        data = self.student.get("/api/timetables").get_json()["timetables"]
        imported = [t for t in data if t["source"] == "imported"]
        self.assertEqual(len(imported), len(commands.load_timetable()["faculty"]))
        self.assertEqual(sum(len(t["classes"]) for t in imported),
                         sum(len(f["classes"]) for f in commands.load_timetable()["faculty"]))
        self.assertEqual(app.test_client().get("/api/timetables").status_code, 401)

    def test_class_format_matches_the_page(self):
        self.add()
        cls = self.timetable()["classes"][0]
        self.assertEqual(set(cls), {"id", "day", "start", "end", "code", "subject", "section", "room"})
        self.assertIsInstance(cls["id"], str)
        self.assertEqual((cls["start"], cls["end"], cls["code"]), ("10:10", "11:05", "TCS-502"))


class EditTests(TimetableTestCase):
    def test_add_marks_the_timetable_edited(self):
        self.assertEqual(self.add().status_code, 201)
        tt = self.timetable()
        self.assertEqual(len(tt["classes"]), 1)
        self.assertTrue(tt["edited"])

    def test_invalid_classes_are_rejected(self):
        for changes in [{"end": "10:00"}, {"end": "10:10"}, {"day": 7}, {"day": "x"}, {"start": "25:00"},
                        {"code": "  "}, {"room": "x" * 101}]:
            with self.subTest(**changes):
                self.assertEqual(self.add(**changes).status_code, 400)
        self.assertEqual(self.timetable()["classes"], [])

    def test_update_and_delete(self):
        cid = self.add().get_json()["class"]["id"]
        r = self.admin.post(f"/api/admin/classes/{cid}", json={**CLASS, "day": 4, "start": "14:05", "end": "15:00"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.get_json()["class"]["day"], r.get_json()["class"]["start"]), (4, "14:05"))
        self.assertEqual(self.admin.post(f"/api/admin/classes/{cid}/delete", json={}).status_code, 200)
        self.assertEqual(self.timetable()["classes"], [])
        self.assertEqual(self.admin.post(f"/api/admin/classes/{cid}/delete", json={}).status_code, 404)

    def test_busy_period_keeps_the_spelling_the_pages_use(self):
        for code in ("Busy", "busy", "BUSY"):
            with self.subTest(code=code):
                self.assertEqual(self.add(code=code, subject="Department meeting", section="", room="").status_code, 201)
        self.assertEqual({c["code"] for c in self.timetable()["classes"]}, {"Busy"})  # script.js checks code === "Busy"

    def test_only_admins_can_edit(self):
        cid = self.add().get_json()["class"]["id"]
        for path in (f"/api/admin/timetables/{PID}/classes", f"/api/admin/classes/{cid}",
                     f"/api/admin/classes/{cid}/delete", f"/api/admin/timetables/{PID}/restore"):
            with self.subTest(path=path):
                self.assertEqual(self.student.post(path, json=CLASS).status_code, 403)

    def test_unknown_timetable(self):
        r = self.admin.post("/api/admin/timetables/unittest-missing/classes", json=CLASS)
        self.assertEqual(r.status_code, 404)


class RestoreTests(TimetableTestCase):
    def test_restore_brings_back_the_file_version(self):
        # Use an imported timetable the admin has not edited, so restoring loses nothing real.
        pid = query_one("SELECT id FROM timetable_profiles WHERE source = 'imported' AND NOT edited ORDER BY id LIMIT 1")[0]
        before = self.timetable(pid)["classes"]
        self.admin.post(f"/api/admin/timetables/{pid}/classes", json=CLASS)
        self.assertTrue(self.timetable(pid)["edited"])
        self.assertEqual(self.admin.post(f"/api/admin/timetables/{pid}/restore", json={}).status_code, 200)
        after = self.timetable(pid)
        self.assertFalse(after["edited"])
        strip = lambda classes: [{k: v for k, v in c.items() if k != "id"} for c in classes]
        self.assertEqual(strip(after["classes"]), strip(before))

    def test_cannot_restore_a_timetable_that_is_not_in_the_file(self):
        self.assertEqual(self.admin.post(f"/api/admin/timetables/{PID}/restore", json={}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
