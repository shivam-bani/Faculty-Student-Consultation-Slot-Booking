"""Tests for notifications (notifications.py and the actions that create them).
Needs MySQL running and .env in place.

Run from the project folder (venv active):
    python -m unittest discover -s tests -v
"""
import unittest
from datetime import datetime, timedelta

from test_bookings import BookingTestCase, add_user, execute, login, query


def notes(email):
    """(title, message) of a user's notifications, oldest first."""
    return query("SELECT n.title, n.message FROM notifications n JOIN users u ON u.id = n.user_id "
                 "WHERE u.email = %s ORDER BY n.id", (email,))


def titles(email):
    return [t for t, _ in notes(email)]


S1, S2, FAC = "unittest-s1@geu.ac.in", "unittest-s2@geu.ac.in", "unittest-fac1@geu.ac.in"


class NotificationTestCase(BookingTestCase):
    def setUp(self):
        super().setUp()
        self.faculty = login(FAC)

    def booked(self, n=1, start="08:00"):
        r = self.book(n, ref=self.ref(start))
        self.assertEqual(r.status_code, 201, r.get_json())
        return r.get_json()["booking"]["id"]

    def fac(self, bid, action, json=None):
        r = self.faculty.post(f"/api/faculty/bookings/{bid}/{action}", json=json or {})
        self.assertEqual(r.status_code, 200, r.get_json())
        return r


class BookingNotificationTests(NotificationTestCase):
    def test_booking_tells_the_student_and_the_faculty_member(self):
        self.booked(1)
        (title, message), = notes(S1)
        self.assertEqual(title, "Booking request sent")
        self.assertRegex(message, r"^Your request to unittest-fac1 for \d{1,2} [A-Z][a-z]{2} \d{4}, 08:00 - 08:30 is waiting "
                                  r"for approval\. Your token is #1\.$")
        (title, message), = notes(FAC)
        self.assertEqual((title, message.split(" requested")[0]), ("New booking request", "unittest-s1 (UTROLL0001, B.Tech CSE)"))
        self.assertIn('"Project review"', message)

    def test_last_seat_also_says_capacity_reached(self):
        for n in (1, 2, 3):
            self.booked(n)
        self.assertEqual(titles(FAC), ["New booking request"] * 3 + ["Capacity reached"])

    def test_refused_booking_creates_nothing(self):
        self.booked(1)
        self.assertEqual(self.book(1, ref=self.ref("08:00")).status_code, 409)  # already booked
        self.assertEqual(len(notes(S1)), 1)
        self.assertEqual(len(notes(FAC)), 1)


class StudentActionNotificationTests(NotificationTestCase):
    def test_cancel_and_reschedule_request_tell_the_faculty_member(self):
        bid = self.booked(1)
        self.clients[0].post(f"/api/bookings/{bid}/reschedule", json={"ref": self.ref("09:00"), "reason": "Lab exam"})
        title, message = notes(FAC)[-1]
        self.assertEqual(title, "Reschedule request")
        self.assertTrue(message.startswith("unittest-s1 (token #1) asked to move from "))
        self.assertTrue(message.endswith("09:00 - 09:30. Reason: Lab exam"))
        self.clients[0].post(f"/api/bookings/{bid}/cancel", json={})
        self.assertEqual(notes(FAC)[-1][0], "Booking cancelled")
        self.assertRegex(notes(FAC)[-1][1], r"^unittest-s1 cancelled token #1 \(.*08:00 - 08:30\)\.$")


class FacultyActionNotificationTests(NotificationTestCase):
    def test_accept(self):
        bid = self.booked(1)
        self.fac(bid, "accept")
        title, message = notes(S1)[-1]
        self.assertEqual(title, "Request accepted")
        self.assertRegex(message, r"^unittest-fac1 accepted your consultation on .*08:00 - 08:30\. Token #1\.$")

    def test_reject_and_cancel_carry_the_reason(self):
        first, second = self.booked(1), self.booked(2)
        self.fac(first, "reject", {"note": "See your mentor"})
        self.assertEqual(notes(S1)[-1][0], "Request rejected")
        self.assertTrue(notes(S1)[-1][1].endswith("(token #1). Reason: See your mentor You can book another slot."))
        self.fac(second, "accept")
        self.fac(second, "cancel")
        self.assertEqual(notes(S2)[-1][0], "Booking cancelled")
        self.assertTrue(notes(S2)[-1][1].endswith("(token #2). You can book another slot."))

    def test_move_and_approve_and_decline(self):
        bid = self.booked(1)
        self.fac(bid, "move", {"ref": self.ref("10:00"), "note": "Room change"})
        title, message = notes(S1)[-1]
        self.assertEqual(title, "Consultation rescheduled")
        self.assertRegex(message, r"^Your consultation with unittest-fac1 is now on .*10:00 - 10:30\. New token #1\. Note: Room change$")
        self.clients[0].post(f"/api/bookings/{bid}/reschedule", json={"ref": self.ref("11:00")})
        self.fac(bid, "decline-reschedule")
        self.assertEqual(notes(S1)[-1][0], "Reschedule declined")
        self.clients[0].post(f"/api/bookings/{bid}/reschedule", json={"ref": self.ref("11:00")})
        self.fac(bid, "approve-reschedule")
        self.assertIn("11:00 - 11:30. New token #1.", notes(S1)[-1][1])

    def test_failed_move_creates_nothing(self):
        bid = self.booked(1)
        before = len(notes(S1))
        r = self.faculty.post(f"/api/faculty/bookings/{bid}/move", json={"ref": "nonsense"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(len(notes(S1)), before)

    def test_call_next_says_its_your_turn(self):
        bid = self.booked(1)
        if datetime.now().hour >= 23:
            self.skipTest("the test slot would run past midnight")
        start = datetime.now().replace(second=0, microsecond=0) - timedelta(minutes=5)
        execute("UPDATE sessions SET session_date = %s, start_time = %s, end_time = %s WHERE id = "
                "(SELECT session_id FROM (SELECT session_id FROM bookings WHERE id = %s) x)",
                (start.date(), start.strftime("%H:%M"), (start + timedelta(minutes=30)).strftime("%H:%M"), bid))
        execute("UPDATE bookings SET status = 'Waiting', checked_in_at = NOW() WHERE id = %s", (bid,))
        sid = query("SELECT session_id FROM bookings WHERE id = %s", (bid,))[0][0]
        self.assertEqual(self.faculty.post(f"/api/faculty/sessions/{sid}/call-next", json={}).status_code, 200)
        title, message = notes(S1)[-1]
        self.assertEqual(title, "It's your turn")
        self.assertRegex(message, r"^unittest-fac1 is ready for you now \(.*, token #1\)\.$")


class AdminNotificationTests(NotificationTestCase):
    def setUp(self):
        super().setUp()
        add_user("unittest-admin@geu.ac.in", "admin")
        self.admin = login("unittest-admin@geu.ac.in")

    def test_removing_the_faculty_member_tells_the_students(self):
        self.booked(1)
        self.admin.post(f"/api/admin/faculty/{self.fid}/deactivate", json={})
        title, message = notes(S1)[-1]
        self.assertEqual(title, "Booking cancelled")
        self.assertRegex(message, r"^unittest-fac1 is no longer available on CampusSync, so your meeting on .*08:00 - 08:30 "
                                  r"was cancelled\. Please book another faculty member\.$")

    def test_timetable_change_counts_clashing_bookings(self):
        self.booked(1)  # 08:00 - 08:30 on self.day
        day = datetime.strptime(self.day, "%Y-%m-%d").isoweekday()
        r = self.admin.post("/api/admin/timetables/unittest-tt1/classes",
                            json={"day": day, "start": "08:00", "end": "09:00", "code": "TCS-101"})
        self.assertEqual(r.status_code, 201, r.get_json())
        title, message = notes(FAC)[-1]
        self.assertEqual(title, "Timetable updated")
        days = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")
        self.assertEqual(message, f"The admin added a class on {days[day - 1]}, 08:00 - 09:00. 1 booked meeting(s) now clash "
                                  f"with a class. Reschedule them under Booking Requests.")
        cid = r.get_json()["class"]["id"]
        self.admin.post(f"/api/admin/classes/{cid}/delete", json={})
        self.assertTrue(notes(FAC)[-1][1].startswith("The admin removed TCS-101 on "))
        self.assertTrue(notes(FAC)[-1][1].endswith("That time is now open for consultations."))  # no clash any more

    def test_settings_and_extra_hours(self):
        self.admin.post(f"/api/admin/faculty/{self.fid}/settings", json={"slot_len": 20, "capacity": 4, "days": [1, 2]})
        self.assertEqual(notes(FAC)[-1], ("Consultation hours updated",
                                          "The admin set your consultation slots to 20 minutes for up to 4 students."))
        self.admin.post(f"/api/admin/faculty/{self.fid}/custom-slots", json={"day": 3, "start": "18:00", "end": "18:30", "capacity": 2})
        self.assertEqual(notes(FAC)[-1][1], "The admin added consultation hours on Wednesday, 18:00 - 18:30.")


class ReadingTests(NotificationTestCase):
    def test_list_is_own_newest_first_and_mark_read(self):
        self.booked(1)
        self.booked(2, start="09:00")
        listing = self.clients[0].get("/api/notifications").get_json()
        self.assertEqual(([n["title"] for n in listing["notifications"]], listing["unread"]), (["Booking request sent"], 1))
        faculty_list = self.faculty.get("/api/notifications").get_json()
        self.assertEqual([n["title"] for n in faculty_list["notifications"]], ["New booking request"] * 2)
        self.assertGreater(int(faculty_list["notifications"][0]["id"]), int(faculty_list["notifications"][1]["id"]))
        self.assertEqual(self.clients[0].post("/api/notifications/read", json={}).status_code, 200)
        self.assertEqual(self.clients[0].get("/api/notifications").get_json()["unread"], 0)
        self.assertEqual(self.faculty.get("/api/notifications").get_json()["unread"], 2)  # other users untouched

    def test_login_required(self):
        from app import app
        self.assertEqual(app.test_client().get("/api/notifications").status_code, 401)


if __name__ == "__main__":
    unittest.main()
