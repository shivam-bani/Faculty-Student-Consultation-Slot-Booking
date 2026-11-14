"""Tests for the student's actions on a booking: cancel, reschedule request, withdraw.
Needs MySQL running and .env in place.

Run from the project folder (venv active):
    python -m unittest discover -s tests -v
"""
import unittest

from test_bookings import BookingTestCase, execute, login, query


class StudentActionTestCase(BookingTestCase):
    def setUp(self):
        super().setUp()
        r = self.book(1)
        self.assertEqual(r.status_code, 201)
        self.bid, self.sid = r.get_json()["booking"]["id"], r.get_json()["booking"]["sessionId"]

    def act(self, action, json=None, n=1):
        return self.clients[n - 1].post(f"/api/bookings/{self.bid}/{action}", json=json or {})

    def mine(self, n=1):
        bookings = self.clients[n - 1].get("/api/bookings").get_json()["bookings"]
        return next(b for b in bookings if b["id"] == self.bid)

    def history(self):
        return [t for (t,) in query("SELECT text FROM booking_history WHERE booking_id = %s ORDER BY id", (self.bid,))]

    def seats_at_8(self):
        slots = self.clients[1].get(f"/api/faculty/{self.fid}/slots").get_json()["slots"]
        return [s["seats"] for s in slots if s["date"] == self.day and s["start"] == "08:00"]


class CancelTests(StudentActionTestCase):
    def test_cancel_frees_the_seat(self):
        self.assertEqual(self.seats_at_8(), [2])
        self.assertEqual(self.act("cancel").status_code, 200)
        self.assertEqual(self.mine()["status"], "Cancelled")
        self.assertEqual(self.seats_at_8(), [3])
        self.assertEqual(self.history()[-1], "Cancelled by student")

    def test_cannot_cancel_twice(self):
        self.act("cancel")
        self.assertEqual(self.act("cancel").status_code, 409)

    def test_accepted_booking_can_be_cancelled(self):
        execute("UPDATE bookings SET status = 'Waiting' WHERE id = %s", (self.bid,))
        self.assertEqual(self.act("cancel").status_code, 200)

    def test_finished_bookings_cannot_be_changed(self):
        for status in ("Rejected", "Completed", "In Session"):
            with self.subTest(status=status):
                execute("UPDATE bookings SET status = %s, checked_in_at = NOW(), called_at = NOW() WHERE id = %s", (status, self.bid))
                self.assertEqual(self.act("cancel").status_code, 409)

    def test_only_the_owner(self):
        self.assertEqual(self.act("cancel", n=2).status_code, 404)  # another student: not found
        self.assertEqual(login("unittest-fac1@geu.ac.in").post(f"/api/bookings/{self.bid}/cancel", json={}).status_code, 403)
        self.assertEqual(self.mine()["status"], "Pending")


class RescheduleTests(StudentActionTestCase):
    def test_request_shows_for_student_and_faculty(self):
        r = self.act("reschedule", {"ref": self.ref("09:00"), "reason": "Lab exam"})
        self.assertEqual(r.status_code, 200, r.get_json())
        request = self.mine()["reschedule"]
        self.assertEqual((request["ref"], request["reason"]), (self.ref("09:00"), "Lab exam"))
        self.assertRegex(request["label"], r"^[A-Z][a-z]+day, \d{1,2} [A-Z][a-z]{2} \d{4}, 09:00 - 09:30$")
        faculty_view = login("unittest-fac1@geu.ac.in").get("/api/bookings").get_json()["bookings"]
        self.assertEqual(next(b for b in faculty_view if b["id"] == self.bid)["reschedule"]["label"], request["label"])
        self.assertTrue(self.history()[-1].startswith("Student asked to move to "))

    def test_new_request_replaces_the_old_one(self):
        self.act("reschedule", {"ref": self.ref("09:00")})
        self.act("reschedule", {"ref": self.ref("10:00")})
        self.assertIn("10:00 - 10:30", self.mine()["reschedule"]["label"])
        self.assertEqual(query("SELECT COUNT(*) FROM reschedule_requests WHERE booking_id = %s", (self.bid,))[0][0], 1)

    def test_request_to_a_session_that_has_bookings(self):
        other = self.book(2, ref=self.ref("11:00")).get_json()["booking"]["sessionId"]
        r = self.act("reschedule", {"ref": f"s:{other}"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.mine()["reschedule"]["ref"], f"s:{other}")

    def test_bad_targets(self):
        self.assertEqual(self.act("reschedule", {"ref": f"s:{self.sid}"}).status_code, 400)        # the current slot
        self.assertEqual(self.act("reschedule", {"ref": self.ref("09:10")}).status_code, 409)      # not a real slot
        other_faculty = self.ref("09:00").replace(f"v:{self.fid}|", "v:999999999|")
        self.assertEqual(self.act("reschedule", {"ref": other_faculty}).status_code, 400)         # another faculty member
        self.assertEqual(self.act("reschedule", {"ref": "nonsense"}).status_code, 400)
        self.assertIsNone(self.mine()["reschedule"])

    def test_withdraw(self):
        self.act("reschedule", {"ref": self.ref("09:00")})
        self.assertEqual(self.act("reschedule/withdraw").status_code, 200)
        self.assertIsNone(self.mine()["reschedule"])
        self.assertEqual(self.history()[-1], "Student withdrew the reschedule request")
        self.assertEqual(self.act("reschedule/withdraw").status_code, 409)  # nothing left to withdraw

    def test_cancelling_also_drops_the_request(self):
        self.act("reschedule", {"ref": self.ref("09:00")})
        self.act("cancel")
        self.assertEqual(query("SELECT COUNT(*) FROM reschedule_requests WHERE booking_id = %s", (self.bid,))[0][0], 0)

    def test_only_the_owner(self):
        self.assertEqual(self.act("reschedule", {"ref": self.ref("09:00")}, n=2).status_code, 404)
        self.assertEqual(self.act("reschedule/withdraw", n=2).status_code, 404)


if __name__ == "__main__":
    unittest.main()
