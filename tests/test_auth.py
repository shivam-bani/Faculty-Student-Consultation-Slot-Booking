"""Tests for login, logout, /me and role checks (auth.py). Needs MySQL running and .env in place.

Run from the project folder (venv active):
    python -m unittest discover -s tests -v
"""
import unittest

from flask import Flask, jsonify
from werkzeug.security import generate_password_hash

import auth
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


def add_user(email, role="student", active=True):
    return execute("INSERT INTO users (name, email, password_hash, role, is_active) VALUES (%s, %s, %s, %s, %s)",
                   ("Unit Test", email, generate_password_hash(PASSWORD), role, active))


class AuthTestCase(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.student_id = add_user("unittest-student@geu.ac.in")

    def tearDown(self):
        execute("DELETE FROM users WHERE email LIKE 'unittest-%'")

    def login(self, email="unittest-student@geu.ac.in", password=PASSWORD, role=None):
        body = {"email": email, "password": password}
        if role:
            body["role"] = role
        return self.client.post("/api/auth/login", json=body)


class LoginTests(AuthTestCase):
    def test_login_sets_a_protected_cookie(self):
        r = self.login(role="student")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["user"]["role"], "student")
        cookie = r.headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)

    def test_me_after_login_and_after_logout(self):
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)
        self.login()
        r = self.client.get("/api/auth/me")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["user"]["email"], "unittest-student@geu.ac.in")
        self.assertEqual(self.client.post("/api/auth/logout", json={}).status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

    def test_email_is_not_case_sensitive(self):
        self.assertEqual(self.login(email="  UNITTEST-Student@GEU.ac.in ").status_code, 200)

    def test_wrong_password_and_unknown_email_give_the_same_answer(self):
        wrong = self.login(password="wrong-password")
        unknown = self.login(email="unittest-nobody@geu.ac.in")
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(unknown.status_code, 401)
        self.assertEqual(wrong.get_json(), unknown.get_json())  # does not reveal which emails exist

    def test_wrong_login_button(self):
        r = self.login(role="admin")
        self.assertEqual(r.status_code, 403)
        self.assertIn("student account", r.get_json()["error"])
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)  # not logged in

    def test_deactivated_account_cannot_log_in(self):
        add_user("unittest-old@geu.ac.in", role="faculty", active=False)
        self.assertEqual(self.login(email="unittest-old@geu.ac.in").status_code, 403)

    def test_non_json_request_is_rejected(self):
        r = self.client.post("/api/auth/login", data={"email": "unittest-student@geu.ac.in", "password": PASSWORD})
        self.assertEqual(r.status_code, 400)


class SignupTests(AuthTestCase):
    EMAIL = "unittest-new@geu.ac.in"

    def signup(self, **changes):
        body = {"name": "New Student", "email": self.EMAIL, "password": PASSWORD, **changes}
        return self.client.post("/api/auth/signup", json=body)

    def test_signup_then_login(self):
        r = self.signup(email="  UNITTEST-New@GEU.ac.in ")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.get_json()["user"]["role"], "student")
        self.assertEqual(r.get_json()["user"]["email"], self.EMAIL)  # stored in lower case
        self.assertEqual(self.login(email=self.EMAIL).status_code, 200)

    def test_password_is_stored_as_a_hash(self):
        self.signup()
        with app.app_context():
            cur = db.get_db().cursor()
            cur.execute("SELECT password_hash FROM users WHERE email = %s", (self.EMAIL,))
            stored = cur.fetchone()[0]
        self.assertTrue(stored.startswith("scrypt:"))
        self.assertNotIn(PASSWORD, stored)

    def test_only_college_emails(self):
        for email in ["someone@gmail.com", "x@geu.ac.in.evil.com", "@geu.ac.in", "a b@geu.ac.in"]:
            with self.subTest(email=email):
                self.assertEqual(self.signup(email=email).status_code, 400)

    def test_duplicate_email(self):
        self.assertEqual(self.signup().status_code, 201)
        r = self.signup(email=self.EMAIL.upper())
        self.assertEqual(r.status_code, 409)
        self.assertIn("already registered", r.get_json()["error"])

    def test_bad_name_or_password(self):
        self.assertEqual(self.signup(name="   ").status_code, 400)
        self.assertEqual(self.signup(password="short").status_code, 400)
        self.assertEqual(self.signup(password="x" * 129).status_code, 400)

    def test_cannot_sign_up_as_admin(self):
        r = self.signup(role="admin")  # extra fields are ignored: sign-up always creates a student
        self.assertEqual(r.get_json()["user"]["role"], "student")


class SessionTests(AuthTestCase):
    def test_role_always_comes_from_the_database(self):
        self.login()
        execute("UPDATE users SET role = 'faculty' WHERE id = %s", (self.student_id,))
        self.assertEqual(self.client.get("/api/auth/me").get_json()["user"]["role"], "faculty")

    def test_deactivating_logs_the_user_out(self):
        self.login()
        execute("UPDATE users SET is_active = FALSE WHERE id = %s", (self.student_id,))
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

    def test_edited_cookie_is_ignored(self):
        self.login()
        self.client.set_cookie("session", "eyJ1c2VyX2lkIjoxfQ.tampered.signature")
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)


class RoleCheckTests(AuthTestCase):
    """login_required() on a small test app with the same settings and database."""

    def setUp(self):
        super().setUp()
        test_app = Flask(__name__)
        test_app.config.update(SECRET_KEY=app.config["SECRET_KEY"], TESTING=True)
        db.init_app(test_app)

        @test_app.get("/admin-only")
        @auth.login_required("admin")
        def admin_only():
            return jsonify(ok=True)

        self.test_client = test_app.test_client()

    def get_as(self, user_id):
        with self.test_client.session_transaction() as s:
            s["user_id"] = user_id
        return self.test_client.get("/admin-only")

    def test_not_logged_in(self):
        self.assertEqual(self.test_client.get("/admin-only").status_code, 401)

    def test_wrong_role(self):
        self.assertEqual(self.get_as(self.student_id).status_code, 403)

    def test_right_role(self):
        admin_id = add_user("unittest-admin@example.com", role="admin")
        self.assertEqual(self.get_as(admin_id).status_code, 200)


if __name__ == "__main__":
    unittest.main()
