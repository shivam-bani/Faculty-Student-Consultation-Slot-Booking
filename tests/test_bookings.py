"""Tests for booking (bookings.py). Needs MySQL running and .env in place.

Run from the project folder (venv active):
    python -m unittest discover -s tests -v
"""
import unittest
from datetime import date, timedelta

from werkzeug.security import generate_password_hash

import db
from app import app

PASSWORD = "Test-Pass-123"
PASSWORD_HASH = generate_password_hash(PASSWORD)


def execute(sql, params=()):
    with app.app_context():
        conn = db.get_db()
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        return cur.lastrowid


def query(sql, params=()):
    with app.app_context():
        cur = db.get_db().cursor()
        cur.execute(sql, params)
        return cur.fetchall()


def clean_up():
    """Remove everything these tests create. Order matters because of the foreign keys."""
    test_users = "SELECT id FROM users WHERE email LIKE 'unittest-%'"
    execute(f"DELETE FROM bookings WHERE student_id IN ({test_users}) OR session_id IN "
            f"(SELECT id FROM sessions WHERE faculty_id IN ({test_users}))")
    execute(f"DELETE FROM student_profiles WHERE user_id IN ({test_users})")
    execute(f"DELETE FROM sessions WHERE faculty_id IN ({test_users})")
    execute(f"DELETE FROM faculty_settings WHERE faculty_id IN ({test_users})")
    execute("DELETE FROM users WHERE email LIKE 'unittest-%'")
    execute("DELETE FROM timetable_profiles WHERE id LIKE 'unittest-%'")


def add_user(email, role):
    return execute("INSERT INTO users (name, email, password_hash, role) VALUES (%s, %s, %s, %s)",
                   (email.split("@")[0], email, PASSWORD_HASH, role))


def add_faculty(n, slot_len=30, capacity=3):
    """A faculty member with an empty timetable, consulting Monday to Saturday."""
    fid = add_user(f"unittest-fac{n}@geu.ac.in", "faculty")
    execute("INSERT INTO timetable_profiles (id, name, source) VALUES (%s, %s, 'admin')", (f"unittest-tt{n}", f"Dr. Test {n}"))
    execute("INSERT INTO faculty_settings (faculty_id, profile_id, slot_len, capacity) VALUES (%s, %s, %s, %s)",
            (fid, f"unittest-tt{n}", slot_len, capacity))
    for d in range(1, 7):
        execute("INSERT INTO consult_days (faculty_id, day_of_week) VALUES (%s, %s)", (fid, d))
    return fid


def login(email):
    client = app.test_client()
    r = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.get_json()
    return client


def booking_day():
    """A Monday-to-Saturday date two or more days ahead, so no slot on it has started."""
    day = date.today() + timedelta(days=2)
    while day.isoweekday() == 7:
        day += timedelta(days=1)
    return day.isoformat()


def body(ref, n=1, **changes):
    profile = {"studentId": f"UT{n:04d}", "rollNo": f"UTROLL{n:04d}", "course": "B.Tech CSE", "semester": "V",
               "section": "A", "groupId": ""}
    profile.update(changes.pop("profile", {}))
    return {"ref": ref, "topic": "Project review", "desc": "", "consultationType": "General", "profile": profile, **changes}


class BookingTestCase(unittest.TestCase):
    def setUp(self):
        clean_up()
        self.fid = add_faculty(1)
        self.students = [add_user(f"unittest-s{n}@geu.ac.in", "student") for n in range(1, 4)]
        self.clients = [login(f"unittest-s{n}@geu.ac.in") for n in range(1, 4)]
        self.day = booking_day()

    def tearDown(self):
        clean_up()

    def ref(self, start="08:00", fid=None):
        return f"v:{fid or self.fid}|{self.day}|{start}"

    def book(self, n=1, ref=None, **changes):
        return self.clients[n - 1].post("/api/bookings", json=body(self.ref() if ref is None else ref, n, **changes))


class BookTests(BookingTestCase):
    def test_book_a_free_timetable_slot(self):
        r = self.book()
        self.assertEqual(r.status_code, 201, r.get_json())
        b = r.get_json()["booking"]
        self.assertEqual((b["token"], b["status"], b["basePriority"]), (1, "Pending", 1))
        self.assertEqual(query("SELECT source, capacity FROM sessions WHERE id = %s", (b["sessionId"],)), [("timetable", 3)])
        self.assertEqual(query("SELECT text FROM booking_history WHERE booking_id = %s", (b["id"],)), [("Requested by student",)])
        self.assertEqual(query("SELECT student_id, semester FROM student_profiles WHERE user_id = %s", (self.students[0],)),
                         [("UT0001", "V")])
        slot = [s for s in self.clients[0].get(f"/api/faculty/{self.fid}/slots").get_json()["slots"]
                if s["date"] == self.day and s["start"] == "08:00"]
        self.assertEqual([(s["ref"], s["seats"]) for s in slot], [(f"s:{b['sessionId']}", 2)])

    def test_two_students_share_one_session_with_tokens_1_and_2(self):
        first = self.book(1).get_json()["booking"]
        second = self.book(2).get_json()["booking"]  # same free-slot ref: joins the session made by the first
        self.assertEqual(first["sessionId"], second["sessionId"])
        self.assertEqual((first["token"], second["token"]), (1, 2))
        self.assertEqual(query("SELECT COUNT(*) FROM sessions WHERE faculty_id = %s", (self.fid,))[0][0], 1)

    def test_tokens_are_never_reused(self):
        sid = self.book(1).get_json()["booking"]["sessionId"]
        self.book(2)
        execute("UPDATE bookings SET status = 'Cancelled' WHERE session_id = %s AND token = 1", (sid,))
        self.assertEqual(self.book(3, ref=f"s:{sid}").get_json()["booking"]["token"], 3)

    def test_full_session(self):
        for n in (1, 2, 3):
            self.assertEqual(self.book(n).status_code, 201)
        extra = add_user("unittest-s4@geu.ac.in", "student")
        r = login("unittest-s4@geu.ac.in").post("/api/bookings", json=body(self.ref(), 4))
        self.assertEqual(r.status_code, 409)
        self.assertIn("no longer available", r.get_json()["error"])  # a full session is not offered any more
        sid = query("SELECT id FROM sessions WHERE faculty_id = %s", (self.fid,))[0][0]
        r = login("unittest-s4@geu.ac.in").post("/api/bookings", json=body(f"s:{sid}", 4))
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "This session is full."))
        self.assertTrue(extra)

    def test_same_student_cannot_book_the_same_session_twice(self):
        sid = self.book().get_json()["booking"]["sessionId"]
        r = self.book(ref=f"s:{sid}")
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "You have already booked this session."))
        execute("UPDATE bookings SET status = 'Cancelled' WHERE session_id = %s", (sid,))
        self.assertEqual(self.book(ref=f"s:{sid}").status_code, 201)  # allowed again after cancelling

    def test_student_cannot_be_in_two_places_at_once(self):
        other = add_faculty(2, slot_len=45)  # 08:00-08:45 overlaps faculty 1's 08:30-09:00
        self.assertEqual(self.book(ref=self.ref("08:30")).status_code, 201)
        r = self.book(ref=self.ref("08:00", fid=other))
        self.assertEqual(r.status_code, 409)
        self.assertIn("already have a consultation at that time (token #1)", r.get_json()["error"])

    def test_slots_that_cannot_be_booked(self):
        self.clients[0]  # logged in student
        execute("INSERT INTO slots_off (faculty_id, day_of_week, start_time) VALUES (%s, %s, '09:00')",
                (self.fid, date.fromisoformat(self.day).isoweekday()))
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        for ref in (self.ref("09:00"), self.ref("08:10"), f"v:{self.fid}|{yesterday}|08:00", "s:999999999"):
            with self.subTest(ref=ref):
                self.assertEqual(self.book(ref=ref).status_code, 409)
        for ref in ("", "x:1", "v:abc"):
            with self.subTest(ref=ref):
                self.assertEqual(self.book(ref=ref).status_code, 400)

    def test_invalid_details(self):
        for changes in [{"topic": " "}, {"consultationType": "Urgent"}, {"profile": {"studentId": "a!"}},
                        {"profile": {"course": "PhD"}}, {"profile": {"semester": "IX"}}, {"profile": {"section": ""}}]:
            with self.subTest(**{k: str(v) for k, v in changes.items()}):
                self.assertEqual(self.book(**changes).status_code, 400)
        self.assertEqual(query("SELECT COUNT(*) FROM bookings b JOIN sessions s ON s.id = b.session_id "
                               "WHERE s.faculty_id = %s", (self.fid,))[0][0], 0)

    def test_student_id_belongs_to_one_account(self):
        self.book(1)
        for clash in ({"studentId": "UT0001"}, {"rollNo": "UTROLL0001"}):
            with self.subTest(**clash):
                r = self.book(2, ref=self.ref("09:00"), profile=clash)
                self.assertEqual(r.status_code, 409)
                self.assertIn("already used by another account", r.get_json()["error"])
        # Student 1's details must be untouched (a wrong upsert once overwrote them).
        self.assertEqual(query("SELECT student_id, roll_no FROM student_profiles WHERE user_id = %s", (self.students[0],)),
                         [("UT0001", "UTROLL0001")])
        self.assertEqual(query("SELECT COUNT(*) FROM student_profiles WHERE user_id = %s", (self.students[1],))[0][0], 0)

    def test_profile_is_updated_on_the_next_booking(self):
        self.book(1)
        self.book(1, ref=self.ref("09:00"), profile={"semester": "VI", "section": "B"})
        self.assertEqual(query("SELECT semester, section FROM student_profiles WHERE user_id = %s", (self.students[0],)),
                         [("VI", "B")])

    def test_only_students_can_book(self):
        self.assertEqual(login("unittest-fac1@geu.ac.in").post("/api/bookings", json=body(self.ref())).status_code, 403)
        self.assertEqual(app.test_client().post("/api/bookings", json=body(self.ref())).status_code, 401)


class PriorityTests(BookingTestCase):
    def priority(self, n, semester, ctype, start):
        r = self.book(n, ref=self.ref(start), consultationType=ctype, profile={"semester": semester})
        self.assertEqual(r.status_code, 201, r.get_json())
        return r.get_json()["booking"]["basePriority"]

    def test_final_year_major_project_gets_priority_2(self):
        self.assertEqual(self.priority(1, "VII", "Major project", "08:00"), 2)
        self.assertEqual(self.priority(2, "VIII", "Major project", "08:00"), 2)

    def test_everyone_else_gets_priority_1(self):
        self.assertEqual(self.priority(1, "VII", "General", "08:00"), 1)
        self.assertEqual(self.priority(2, "V", "Major project", "08:00"), 1)

    def test_priority_comes_from_the_server(self):
        r = self.book(ref=self.ref(), basePriority=2)  # an extra field from the browser is ignored
        self.assertEqual(r.get_json()["booking"]["basePriority"], 1)


class ListTests(BookingTestCase):
    def test_each_role_sees_only_its_bookings(self):
        self.book(1)
        self.book(2, ref=self.ref("09:00"))
        add_faculty(2)
        admin_id = add_user("unittest-admin@example.com", "admin")
        mine = self.clients[0].get("/api/bookings").get_json()
        self.assertEqual([b["studentEmail"] for b in mine["bookings"]], ["unittest-s1@geu.ac.in"])
        self.assertEqual(len(mine["sessions"]), 1)
        self.assertEqual(len(login("unittest-fac1@geu.ac.in").get("/api/bookings").get_json()["bookings"]), 2)
        self.assertEqual(login("unittest-fac2@geu.ac.in").get("/api/bookings").get_json()["bookings"], [])
        everything = login("unittest-admin@example.com").get("/api/bookings").get_json()["bookings"]
        self.assertTrue({"unittest-s1@geu.ac.in", "unittest-s2@geu.ac.in"} <= {b["studentEmail"] for b in everything})
        self.assertTrue(admin_id)

    def test_shape_matches_script_js(self):
        self.book(1)
        data = self.clients[0].get("/api/bookings").get_json()
        b, s = data["bookings"][0], data["sessions"][0]
        self.assertEqual((b["sessionId"], b["token"], b["status"], b["topic"]), (s["id"], 1, "Pending", "Project review"))
        self.assertEqual(b["student"]["rollNo"], "UTROLL0001")
        self.assertEqual([h["text"] for h in b["history"]], ["Requested by student"])
        self.assertEqual((s["facultyEmail"], s["date"], s["start"], s["end"], s["origKey"]),
                         ("unittest-fac1@geu.ac.in", self.day, "08:00", "08:30", f"{self.day}|08:00"))
        self.assertIsInstance(b["id"], str)

    def test_profile_for_the_booking_form(self):
        self.assertIsNone(self.clients[0].get("/api/profile").get_json()["profile"])
        self.book(1)
        self.assertEqual(self.clients[0].get("/api/profile").get_json()["profile"]["studentId"], "UT0001")


if __name__ == "__main__":
    unittest.main()
