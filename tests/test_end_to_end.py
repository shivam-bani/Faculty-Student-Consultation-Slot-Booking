"""End-to-end test: the whole story from the README, only through the HTTP API, as the pages use it.

admin creates a faculty member -> two students sign up and book the same slot -> the faculty
member accepts -> on the day both press "I'm here" -> the queue calls the final-year Major project
student first -> both are seen -> notifications arrived -> the admin removes the faculty member.

The only shortcut: the slot is moved to "now" in MySQL, so the test does not wait for the real day.
Needs MySQL running and .env in place. Run from the project folder (venv active):
    python -m unittest discover -s tests -v
"""
import unittest
from datetime import datetime, timedelta

from app import app
from test_bookings import PASSWORD, add_user, booking_day, clean_up, execute, login, query


def ok(response, status=200):
    assert response.status_code == status, (response.status_code, response.get_json())
    return response.get_json()


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        clean_up()
        if datetime.now().hour >= 23:
            self.skipTest("the test slot would run past midnight")

    def tearDown(self):
        clean_up()

    def test_full_consultation_day(self):
        # 1. The admin creates a faculty account and sets 2 students per slot.
        add_user("unittest-admin@geu.ac.in", "admin")
        admin = login("unittest-admin@geu.ac.in")
        fac = ok(admin.post("/api/admin/faculty", json={"name": "Dr. Unit Test", "email": "unittest-e2e-fac@geu.ac.in",
                                                         "password": PASSWORD, "profile_id": "unittest-e2e",
                                                         "profile_name": "Dr. Unit Test"}), 201)["faculty"]
        fid = fac["id"]
        ok(admin.post(f"/api/admin/faculty/{fid}/settings", json={"slot_len": 30, "capacity": 2, "days": [1, 2, 3, 4, 5, 6]}))

        # 2. Two students sign up (only @geu.ac.in works) and log in.
        anonymous = app.test_client()
        self.assertEqual(anonymous.post("/api/auth/signup", json={"name": "X", "email": "x@gmail.com", "password": PASSWORD}).status_code, 400)
        students = []
        for n, name in ((1, "Final Year"), (2, "Third Year")):
            ok(anonymous.post("/api/auth/signup", json={"name": name, "email": f"unittest-e2e-s{n}@geu.ac.in", "password": PASSWORD}), 201)
            students.append(login(f"unittest-e2e-s{n}@geu.ac.in"))
        final_year, third_year = students

        # 3. Both book the same free slot; the final-year Major project booking gets priority 2.
        day = booking_day()
        slot = next(s for s in ok(final_year.get(f"/api/faculty/{fid}/slots"))["slots"] if s["date"] == day and s["start"] == "09:00")
        bookings = []
        for client, n, semester, ctype in ((final_year, 1, "VIII", "Major project"), (third_year, 2, "V", "Major project")):
            profile = {"studentId": f"E2E{n}", "rollNo": f"E2EROLL{n}", "course": "B.Tech CSE", "semester": semester,
                       "section": "A", "groupId": ""}
            bookings.append(ok(client.post("/api/bookings", json={"ref": slot["ref"], "topic": "Project review", "desc": "",
                                                                  "consultationType": ctype, "profile": profile}), 201)["booking"])
        self.assertEqual([(b["token"], b["basePriority"]) for b in bookings], [(1, 2), (2, 1)])  # semester V: no priority
        self.assertEqual(bookings[0]["sessionId"], bookings[1]["sessionId"])
        late = {"studentId": "E2E3", "rollNo": "E2EROLL3", "course": "BCA", "semester": "V", "section": "A", "groupId": ""}
        add_user("unittest-e2e-s3@geu.ac.in", "student")
        full = login("unittest-e2e-s3@geu.ac.in").post("/api/bookings", json={"ref": f"s:{bookings[0]['sessionId']}", "topic": "Late",
                                                                               "consultationType": "General", "profile": late})
        self.assertEqual(full.status_code, 409)  # 2 seats, both taken

        # 4. The faculty member sees both requests and accepts them.
        faculty = login("unittest-e2e-fac@geu.ac.in")
        listed = ok(faculty.get("/api/bookings"))["bookings"]
        self.assertEqual(sorted(b["id"] for b in listed), sorted(b["id"] for b in bookings))
        for b in bookings:
            ok(faculty.post(f"/api/faculty/bookings/{b['id']}/accept", json={}))

        # 5. On the day (slot moved to start in 5 minutes): the third-year student arrives first.
        start = datetime.now().replace(second=0, microsecond=0) + timedelta(minutes=5)
        execute("UPDATE sessions SET session_date = %s, start_time = %s, end_time = %s WHERE id = %s",
                (start.date(), start.strftime("%H:%M"), (start + timedelta(minutes=30)).strftime("%H:%M"), bookings[0]["sessionId"]))
        self.assertEqual(ok(third_year.post(f"/api/bookings/{bookings[1]['id']}/check-in", json={}))["position"], 1)
        self.assertEqual(ok(final_year.post(f"/api/bookings/{bookings[0]['id']}/check-in", json={}))["position"], 1)  # priority 2 goes ahead
        self.assertEqual(ok(third_year.get("/api/queue"))["entries"][0]["position"], 2)

        # 6. Call next: priority first, then the other student, then finish.
        sid = bookings[0]["sessionId"]
        called = ok(faculty.post(f"/api/faculty/sessions/{sid}/call-next", json={"current": None}))
        self.assertEqual(called["called"]["id"], bookings[0]["id"])
        called = ok(faculty.post(f"/api/faculty/sessions/{sid}/call-next", json={"current": bookings[0]["id"]}))
        self.assertEqual((called["completed"]["id"], called["called"]["id"]), (bookings[0]["id"], bookings[1]["id"]))
        done = ok(faculty.post(f"/api/faculty/sessions/{sid}/call-next", json={"current": bookings[1]["id"]}))
        self.assertIsNone(done["called"])
        self.assertEqual(query("SELECT status, COUNT(*) FROM bookings WHERE session_id = %s GROUP BY status", (sid,)), [("Completed", 2)])

        # 7. Everyone was told, and each user sees only their own notifications.
        titles = lambda client: [n["title"] for n in ok(client.get("/api/notifications"))["notifications"]][::-1]
        self.assertEqual(titles(final_year), ["Booking request sent", "Request accepted", "It's your turn"])
        self.assertEqual(titles(faculty), ["Welcome to CampusSync", "Consultation hours updated", "New booking request",
                                           "New booking request", "Capacity reached"])

        # 8. Roles are enforced by the server.
        self.assertEqual(final_year.post(f"/api/admin/faculty/{fid}/deactivate", json={}).status_code, 403)
        self.assertEqual(app.test_client().get("/api/bookings").status_code, 401)

        # 9. The admin removes the faculty member: the account stops working, the history stays.
        ok(admin.post(f"/api/admin/faculty/{fid}/deactivate", json={}))
        self.assertEqual(faculty.get("/api/auth/me").status_code, 401)
        r = app.test_client().post("/api/auth/login", json={"email": "unittest-e2e-fac@geu.ac.in", "password": PASSWORD, "role": "faculty"})
        self.assertEqual(r.status_code, 403)
        history = [b["status"] for b in ok(final_year.get("/api/bookings"))["bookings"]]
        self.assertEqual(history, ["Completed"])


if __name__ == "__main__":
    unittest.main()
