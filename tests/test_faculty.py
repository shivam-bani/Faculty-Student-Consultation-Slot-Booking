"""Tests for the faculty API (faculty.py). Needs MySQL running and .env in place.

Run from the project folder (venv active):
    python -m unittest discover -s tests -v
"""
import unittest

from werkzeug.security import generate_password_hash

import db
from app import app

PASSWORD = "Test-Pass-123"


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
    """Remove everything the tests create (users and timetables starting with 'unittest-')."""
    execute("DELETE s FROM faculty_settings s JOIN users u ON u.id = s.faculty_id WHERE u.email LIKE 'unittest-%'")
    execute("DELETE FROM users WHERE email LIKE 'unittest-%'")
    execute("DELETE FROM timetable_profiles WHERE id LIKE 'unittest-%'")


class FacultyTestCase(unittest.TestCase):
    def setUp(self):
        clean_up()
        for email, role in [("unittest-admin@example.com", "admin"), ("unittest-student@geu.ac.in", "student")]:
            execute("INSERT INTO users (name, email, password_hash, role) VALUES (%s, %s, %s, %s)",
                    ("Unit Test", email, generate_password_hash(PASSWORD), role))
        for pid in ("unittest-prof-a", "unittest-prof-b"):
            execute("INSERT INTO timetable_profiles (id, name, source) VALUES (%s, %s, 'imported')", (pid, "Dr. " + pid))
        self.admin = self.client_for("unittest-admin@example.com")

    def tearDown(self):
        clean_up()

    def client_for(self, email, password=PASSWORD):
        client = app.test_client()
        r = client.post("/api/auth/login", json={"email": email, "password": password})
        self.assertEqual(r.status_code, 200, r.get_json())
        return client

    def create(self, client=None, **changes):
        body = {"name": "Unit Faculty", "email": "unittest-fac@geu.ac.in", "password": PASSWORD,
                "profile_id": "unittest-prof-a", **changes}
        return (client or self.admin).post("/api/admin/faculty", json=body)


class CreateFacultyTests(FacultyTestCase):
    def test_create_link_and_log_in(self):
        r = self.create()
        self.assertEqual(r.status_code, 201, r.get_json())
        fac = r.get_json()["faculty"]
        self.assertEqual((fac["email"], fac["profile_id"], fac["profile_name"]),
                         ("unittest-fac@geu.ac.in", "unittest-prof-a", "Dr. unittest-prof-a"))
        self.assertEqual(query_one("SELECT COUNT(*) FROM consult_days WHERE faculty_id = %s", (fac["id"],))[0], 5)
        faculty_client = self.client_for("unittest-fac@geu.ac.in")  # the temporary password works
        self.assertEqual(faculty_client.get("/api/auth/me").get_json()["user"]["role"], "faculty")

    def test_listed_for_every_logged_in_user(self):
        self.create()
        student = self.client_for("unittest-student@geu.ac.in")
        emails = [f["email"] for f in student.get("/api/faculty").get_json()["faculty"]]
        self.assertIn("unittest-fac@geu.ac.in", emails)
        self.assertEqual(app.test_client().get("/api/faculty").status_code, 401)

    def test_one_account_per_timetable(self):
        self.create()
        r = self.create(email="unittest-other@geu.ac.in")
        self.assertEqual(r.status_code, 409)
        self.assertIn("already linked to unittest-fac@geu.ac.in", r.get_json()["error"])

    def test_duplicate_email(self):
        self.create()
        r = self.create(profile_id="unittest-prof-b")
        self.assertEqual(r.status_code, 409)
        self.assertIn("already exists", r.get_json()["error"])

    def test_new_timetable_for_faculty_not_in_the_files(self):
        r = self.create(profile_id="unittest-new-person", profile_name="New Person")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(query_one("SELECT name, source, edited FROM timetable_profiles WHERE id = 'unittest-new-person'"),
                         ("New Person", "admin", 1))

    def test_invalid_input(self):
        for changes in [{"email": "not-an-email"}, {"password": "short"}, {"name": " "}, {"profile_id": "Bad Id!"}]:
            with self.subTest(**changes):
                self.assertEqual(self.create(**changes).status_code, 400)

    def test_only_admins(self):
        student = self.client_for("unittest-student@geu.ac.in")
        self.assertEqual(self.create(client=student).status_code, 403)
        self.assertEqual(self.create(client=app.test_client()).status_code, 401)

    def test_removed_faculty_can_be_added_again(self):
        fid = self.create().get_json()["faculty"]["id"]
        execute("UPDATE users SET is_active = FALSE WHERE id = %s", (fid,))
        execute("UPDATE faculty_settings SET profile_id = NULL WHERE faculty_id = %s", (fid,))
        r = self.create(profile_id="unittest-prof-b", password="Another-Pass-456")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.get_json()["faculty"]["id"], fid)  # same account, history kept
        self.client_for("unittest-fac@geu.ac.in", "Another-Pass-456")


if __name__ == "__main__":
    unittest.main()
