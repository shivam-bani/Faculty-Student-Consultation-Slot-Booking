"""Tests for the flask commands in commands.py. Needs MySQL running and .env in place.

Run from the project folder (venv active):
    python -m unittest discover -s tests -v
"""
import unittest

from werkzeug.security import check_password_hash

import commands
import db
from app import app


def query_one(sql, params=()):
    with app.app_context():
        cur = db.get_db().cursor()
        cur.execute(sql, params)
        return cur.fetchone()


def execute(sql, params=()):
    with app.app_context():
        conn = db.get_db()
        conn.cursor().execute(sql, params)
        conn.commit()


class ImportTimetableTests(unittest.TestCase):
    def setUp(self):
        self.runner = app.test_cli_runner()
        self.data = commands.load_timetable()

    def run_import(self):
        result = self.runner.invoke(args=["import-timetable"])
        self.assertEqual(result.exit_code, 0, result.output)
        return result

    def test_import_matches_the_file(self):
        self.run_import()
        expected_classes = sum(len(f["classes"]) for f in self.data["faculty"])
        self.assertEqual(query_one("SELECT COUNT(*) FROM timetable_profiles WHERE source = 'imported'")[0],
                         len(self.data["faculty"]))
        self.assertEqual(query_one("SELECT COUNT(*) FROM timetable_classes c JOIN timetable_profiles p "
                                   "ON p.id = c.profile_id WHERE p.source = 'imported'")[0], expected_classes)

    def test_running_twice_does_not_duplicate(self):
        self.run_import()
        before = query_one("SELECT COUNT(*) FROM timetable_classes")[0]
        self.run_import()
        self.assertEqual(query_one("SELECT COUNT(*) FROM timetable_classes")[0], before)

    def test_profile_edited_by_admin_is_kept(self):
        pid = self.data["faculty"][0]["id"]
        self.run_import()
        try:
            execute("UPDATE timetable_profiles SET edited = TRUE WHERE id = %s", (pid,))
            execute("DELETE FROM timetable_classes WHERE profile_id = %s", (pid,))  # the admin's "edit"
            self.assertIn("1 kept", self.run_import().output)
            self.assertEqual(query_one("SELECT COUNT(*) FROM timetable_classes WHERE profile_id = %s", (pid,))[0], 0)
        finally:
            execute("UPDATE timetable_profiles SET edited = FALSE WHERE id = %s", (pid,))
            self.run_import()  # restores the classes from the file
        self.assertEqual(query_one("SELECT COUNT(*) FROM timetable_classes WHERE profile_id = %s", (pid,))[0],
                         len(self.data["faculty"][0]["classes"]))


class CreateAdminTests(unittest.TestCase):
    EMAIL = "unittest-admin@example.com"

    def setUp(self):
        self.runner = app.test_cli_runner()

    def tearDown(self):
        execute("DELETE FROM users WHERE email = %s", (self.EMAIL,))

    def create(self, password, repeat=None):
        return self.runner.invoke(args=["create-admin", "--name", "Unit Test", "--email", self.EMAIL],
                                  input=f"{password}\n{repeat or password}\n")

    def test_stores_only_a_hash(self):
        self.assertEqual(self.create("Correct-Horse-1").exit_code, 0)
        role, password_hash = query_one("SELECT role, password_hash FROM users WHERE email = %s", (self.EMAIL,))
        self.assertEqual(role, "admin")
        self.assertNotIn("Correct-Horse-1", password_hash)
        self.assertTrue(check_password_hash(password_hash, "Correct-Horse-1"))
        self.assertFalse(check_password_hash(password_hash, "wrong-password"))

    def test_duplicate_email_is_rejected(self):
        self.create("Correct-Horse-1")
        result = self.create("Another-Pass-2")
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("already exists", result.output)

    def test_short_password_is_rejected(self):
        result = self.create("short")
        self.assertNotEqual(result.exit_code, 0)
        self.assertIsNone(query_one("SELECT id FROM users WHERE email = %s", (self.EMAIL,)))


if __name__ == "__main__":
    unittest.main()
