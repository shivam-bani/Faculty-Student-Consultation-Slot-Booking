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


class ManageFacultyTests(FacultyTestCase):
    def setUp(self):
        super().setUp()
        self.fid = self.create().get_json()["faculty"]["id"]

    def post(self, action, body=None, client=None):
        return (client or self.admin).post(f"/api/admin/faculty/{self.fid}/{action}", json=body or {})

    def test_link_another_timetable(self):
        r = self.post("link", {"profile_id": "unittest-prof-b"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["faculty"]["profile_id"], "unittest-prof-b")

    def test_cannot_link_a_timetable_that_is_taken(self):
        self.create(email="unittest-other@geu.ac.in", profile_id="unittest-prof-b")
        r = self.post("link", {"profile_id": "unittest-prof-b"})
        self.assertEqual(r.status_code, 409)
        self.assertIn("unittest-other@geu.ac.in", r.get_json()["error"])

    def test_cannot_link_a_missing_timetable(self):
        self.assertEqual(self.post("link", {"profile_id": "unittest-does-not-exist"}).status_code, 400)

    def test_reset_password(self):
        self.assertEqual(self.post("password", {"password": "Reset-Pass-789"}).status_code, 200)
        self.client_for("unittest-fac@geu.ac.in", "Reset-Pass-789")
        self.assertEqual(self.post("password", {"password": "short"}).status_code, 400)

    def test_deactivate(self):
        faculty_client = self.client_for("unittest-fac@geu.ac.in")
        self.assertEqual(self.post("deactivate").status_code, 200)
        self.assertEqual(faculty_client.get("/api/auth/me").status_code, 401)  # logged out at once
        listed = [f["id"] for f in self.admin.get("/api/faculty").get_json()["faculty"]]
        self.assertNotIn(self.fid, listed)
        self.assertEqual(query_one("SELECT is_active FROM users WHERE id = %s", (self.fid,))[0], 0)  # row kept
        self.assertIsNone(query_one("SELECT profile_id FROM faculty_settings WHERE faculty_id = %s", (self.fid,))[0])
        self.assertEqual(self.create(email="unittest-new@geu.ac.in").status_code, 201)  # timetable is free again

    def test_unknown_or_removed_faculty(self):
        self.post("deactivate")
        for action in ("link", "password", "deactivate"):
            with self.subTest(action=action):
                self.assertEqual(self.post(action, {"profile_id": "unittest-prof-b", "password": PASSWORD}).status_code, 404)

    def test_only_admins(self):
        student = self.client_for("unittest-student@geu.ac.in")
        for action in ("link", "password", "deactivate"):
            with self.subTest(action=action):
                self.assertEqual(self.post(action, client=student).status_code, 403)


class SettingsTests(FacultyTestCase):
    def setUp(self):
        super().setUp()
        self.fid = self.create().get_json()["faculty"]["id"]

    def settings(self):
        faculty = self.admin.get("/api/faculty").get_json()["faculty"]
        return next(f["settings"] for f in faculty if f["id"] == self.fid)

    def post(self, path, body=None, client=None):
        return (client or self.admin).post(f"/api/admin/faculty/{self.fid}/{path}", json=body or {})

    def test_defaults(self):
        self.assertEqual(self.settings(), {"slot_len": 30, "capacity": 3, "days": [1, 2, 3, 4, 5], "off": [], "custom": []})

    def test_save_settings(self):
        self.assertEqual(self.post("settings", {"slot_len": 45, "capacity": 5, "days": [6, 2, 2]}).status_code, 200)
        s = self.settings()
        self.assertEqual((s["slot_len"], s["capacity"], s["days"]), (45, 5, [2, 6]))

    def test_invalid_settings(self):
        for body in [{"slot_len": 25, "capacity": 3, "days": [1]}, {"slot_len": 30, "capacity": 0, "days": [1]},
                     {"slot_len": 30, "capacity": 51, "days": [1]}, {"slot_len": 30, "capacity": 3, "days": []},
                     {"slot_len": 30, "capacity": 3, "days": [7]}, {"slot_len": "x", "capacity": 3, "days": [1]}]:
            with self.subTest(**body):
                self.assertEqual(self.post("settings", body).status_code, 400)
        self.assertEqual(self.settings()["slot_len"], 30)

    def test_switch_slots_off_and_on(self):
        self.post("slots-off", {"day": 2, "start": "09:00", "off": True})
        self.post("slots-off", {"day": 3, "start": "12:30", "off": True})
        self.assertEqual(self.settings()["off"], ["2|09:00", "3|12:30"])
        self.post("slots-off", {"day": 2, "start": "09:00", "off": False})
        self.assertEqual(self.settings()["off"], ["3|12:30"])
        self.post("slots-off/clear")
        self.assertEqual(self.settings()["off"], [])

    def test_new_slot_length_clears_switched_off_slots(self):
        self.post("slots-off", {"day": 2, "start": "09:00", "off": True})
        self.post("settings", {"slot_len": 30, "capacity": 4, "days": [1, 2]})  # same length: kept
        self.assertEqual(self.settings()["off"], ["2|09:00"])
        self.post("settings", {"slot_len": 20, "capacity": 4, "days": [1, 2]})  # new length: cleared
        self.assertEqual(self.settings()["off"], [])

    def test_extra_slots(self):
        r = self.post("custom-slots", {"day": 6, "start": "10:00", "end": "11:00", "capacity": 5})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.settings()["custom"],
                         [{"id": r.get_json()["id"], "day": 6, "start": "10:00", "end": "11:00", "capacity": 5}])
        self.assertEqual(self.post("custom-slots", {"day": 6, "start": "11:00", "end": "10:00", "capacity": 5}).status_code, 400)
        self.assertEqual(self.admin.post(f"/api/admin/custom-slots/{r.get_json()['id']}/delete", json={}).status_code, 200)
        self.assertEqual(self.settings()["custom"], [])

    def test_week_uses_the_timetable_and_settings(self):
        execute("INSERT INTO timetable_classes (profile_id, day_of_week, start_time, end_time, code) "
                "VALUES ('unittest-prof-a', 1, '10:00', '11:00', 'TCS-502')")
        self.post("settings", {"slot_len": 30, "capacity": 4, "days": [1]})
        self.post("slots-off", {"day": 1, "start": "08:00", "off": True})
        student = self.client_for("unittest-student@geu.ac.in")  # every logged-in user may look
        r = student.get(f"/api/faculty/{self.fid}/week")
        self.assertEqual(r.status_code, 200)
        week = r.get_json()
        self.assertEqual([c["code"] for c in week["classes"]], ["TCS-502"])
        starts = [s["start"] for s in week["slots"]]
        self.assertIn("09:30", starts)
        self.assertNotIn("10:00", starts)
        self.assertEqual([s["start"] for s in week["slots"] if s["off"]], ["08:00"])
        self.assertEqual({s["capacity"] for s in week["slots"]}, {4})

    def test_week_for_unknown_or_logged_out(self):
        self.assertEqual(self.admin.get("/api/faculty/999999999/week").status_code, 404)
        self.assertEqual(app.test_client().get(f"/api/faculty/{self.fid}/week").status_code, 401)

    def test_only_admins(self):
        student = self.client_for("unittest-student@geu.ac.in")
        for path in ("settings", "slots-off", "slots-off/clear", "custom-slots"):
            with self.subTest(path=path):
                self.assertEqual(self.post(path, client=student).status_code, 403)


if __name__ == "__main__":
    unittest.main()
