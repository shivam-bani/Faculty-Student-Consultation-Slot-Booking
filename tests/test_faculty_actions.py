"""Tests for the faculty member's actions on bookings: accept, reject, cancel, move, and approve or
decline a student's reschedule request. Needs MySQL running and .env in place.

Run from the project folder (venv active):
    python -m unittest discover -s tests -v
"""
import unittest
from datetime import date, timedelta

from test_bookings import BookingTestCase, add_faculty, execute, login, query


class FacultyActionTestCase(BookingTestCase):
    def setUp(self):
        super().setUp()
        r = self.book(1)
        self.assertEqual(r.status_code, 201)
        self.bid, self.sid = r.get_json()["booking"]["id"], r.get_json()["booking"]["sessionId"]
        self.faculty = login("unittest-fac1@geu.ac.in")

    def act(self, action, json=None, client=None, bid=None):
        return (client or self.faculty).post(f"/api/faculty/bookings/{bid or self.bid}/{action}", json=json or {})

    def row(self, bid=None):
        return query("SELECT status, session_id, token FROM bookings WHERE id = %s", (bid or self.bid,))[0]

    def history(self):
        return [t for (t,) in query("SELECT text FROM booking_history WHERE booking_id = %s ORDER BY id", (self.bid,))]

    def requests(self):
        return query("SELECT COUNT(*) FROM reschedule_requests WHERE booking_id = %s", (self.bid,))[0][0]


class StatusTests(FacultyActionTestCase):
    def test_accept(self):
        self.assertEqual(self.act("accept").status_code, 200)
        self.assertEqual(self.row()[0], "Waiting")  # shown as "Accepted"
        self.assertEqual(self.history()[-1], "Accepted by faculty")
        self.assertEqual(self.act("accept").status_code, 409)  # already accepted

    def test_reject_with_a_reason(self):
        self.assertEqual(self.act("reject", {"note": "See your mentor first"}).status_code, 200)
        self.assertEqual(self.row()[0], "Rejected")
        self.assertEqual(self.history()[-1], "Rejected by faculty: See your mentor first")

    def test_reject_frees_the_seat(self):
        self.act("reject")
        self.assertEqual(self.book(1).status_code, 201)  # the student may book the same slot again

    def test_cancel_only_accepted_bookings(self):
        self.assertEqual(self.act("cancel").status_code, 409)  # still Pending: reject it instead
        self.act("accept")
        self.assertEqual(self.act("cancel", {"note": "On official duty"}).status_code, 200)
        self.assertEqual(self.row()[0], "Cancelled")
        self.assertEqual(self.history()[-1], "Cancelled by faculty: On official duty")
        self.assertEqual(self.act("reject").status_code, 409)

    def test_reject_drops_the_reschedule_request(self):
        self.clients[0].post(f"/api/bookings/{self.bid}/reschedule", json={"ref": self.ref("09:00")})
        self.assertEqual(self.requests(), 1)
        self.act("reject")
        self.assertEqual(self.requests(), 0)

    def test_long_note_refused(self):
        self.assertEqual(self.act("reject", {"note": "x" * 201}).status_code, 400)
        self.assertEqual(self.row()[0], "Pending")

    def test_finished_bookings_cannot_be_changed(self):
        for status in ("Completed", "Cancelled", "In Session"):
            with self.subTest(status=status):
                execute("UPDATE bookings SET status = %s, checked_in_at = NOW(), called_at = NOW() WHERE id = %s", (status, self.bid))
                for action in ("accept", "reject", "cancel", "move"):
                    self.assertEqual(self.act(action, {"ref": self.ref("09:00")}).status_code, 409, action)

    def test_session_that_has_ended(self):
        past = (date.today() - timedelta(days=1)).isoformat()
        execute("UPDATE sessions SET session_date = %s WHERE id = %s", (past, self.sid))
        self.assertEqual(self.act("accept").status_code, 409)


class OwnershipTests(FacultyActionTestCase):
    def test_other_faculty_and_students_cannot_act(self):
        add_faculty(2)
        other = login("unittest-fac2@geu.ac.in")
        for action in ("accept", "reject", "cancel", "move", "approve-reschedule", "decline-reschedule"):
            with self.subTest(action=action):
                self.assertEqual(self.act(action, client=other).status_code, 404)        # not their booking
                self.assertEqual(self.act(action, client=self.clients[0]).status_code, 403)  # students cannot
        self.assertEqual(self.row()[0], "Pending")

    def test_unknown_booking(self):
        self.assertEqual(self.act("accept", bid=999999999).status_code, 404)


class MoveTests(FacultyActionTestCase):
    def test_move_to_a_free_slot_accepts_the_request(self):
        r = self.act("move", {"ref": self.ref("10:00"), "note": "Clash with a meeting"})
        self.assertEqual(r.status_code, 200, r.get_json())
        moved = r.get_json()["booking"]
        status, sid, token = self.row()
        self.assertEqual((status, str(sid), token), ("Waiting", moved["sessionId"], 1))
        self.assertNotEqual(moved["sessionId"], self.sid)
        self.assertEqual(query("SELECT TIME_FORMAT(start_time, '%H:%i'), source FROM sessions WHERE id = %s", (sid,)),
                         [("10:00", "timetable")])
        self.assertTrue(self.history()[-1].startswith("Moved to ") and self.history()[-1].endswith("10:00 - 10:30 by faculty"))

    def test_move_into_a_session_gives_the_next_token(self):
        other = self.book(2, ref=self.ref("11:00")).get_json()["booking"]["sessionId"]
        r = self.act("move", {"ref": f"s:{other}"})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["booking"]["token"], 2)

    def test_full_slot_refused(self):
        execute("UPDATE faculty_settings SET capacity = 1 WHERE faculty_id = %s", (self.fid,))
        full = self.book(2, ref=self.ref("11:00")).get_json()["booking"]["sessionId"]
        r = self.act("move", {"ref": f"s:{full}"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["error"], "That slot is full. Choose another one.")
        self.assertEqual(str(self.row()[1]), self.sid)  # not moved

    def test_same_slot_refused(self):
        self.assertEqual(self.act("move", {"ref": f"s:{self.sid}"}).status_code, 409)

    def test_student_clash_refused(self):
        self.book(1, ref=self.ref("12:00"))  # the same student's other booking at 12:00
        r = self.act("move", {"date": self.day, "start": "12:15", "end": "12:45"})
        self.assertEqual(r.status_code, 409)
        self.assertIn("another consultation", r.get_json()["error"])
        self.assertEqual(str(self.row()[1]), self.sid)
        # the session made for that time was undone with the rest of the transaction
        self.assertEqual(query("SELECT COUNT(*) FROM sessions WHERE source = 'created' AND faculty_id = %s", (self.fid,))[0][0], 0)

    def test_move_to_a_new_date_and_time(self):
        r = self.act("move", {"date": self.day, "start": "18:30", "end": "18:50"})
        self.assertEqual(r.status_code, 200, r.get_json())
        sid = self.row()[1]
        self.assertEqual(query("SELECT capacity, source, topic FROM sessions WHERE id = %s", (sid,)),
                         [(1, "created", "Rescheduled consultation")])
        # a second student moved to the same new time joins that session, and it holds only one
        second = self.book(2, ref=self.ref("13:00")).get_json()["booking"]["id"]
        r = self.act("move", {"date": self.day, "start": "18:30", "end": "18:50"}, bid=second)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["error"], "That slot is full. Choose another one.")

    def test_bad_new_times(self):
        cases = [{"date": self.day, "start": "10:00", "end": "09:00"},             # ends before it starts
                 {"date": "2020-01-01", "start": "10:00", "end": "10:30"},         # in the past
                 {"date": "soon", "start": "10:00", "end": "10:30"},
                 {"ref": "nonsense"}]
        for case in cases:
            with self.subTest(case=case):
                self.assertEqual(self.act("move", case).status_code, 400)

    def test_cannot_move_into_another_faculty_members_slot(self):
        fid2 = add_faculty(2)
        self.assertEqual(self.act("move", {"ref": self.ref("09:00", fid=fid2)}).status_code, 409)
        other = self.book(2, ref=self.ref("09:00", fid=fid2)).get_json()["booking"]["sessionId"]
        self.assertEqual(self.act("move", {"ref": f"s:{other}"}).status_code, 409)
        self.assertEqual(str(self.row()[1]), self.sid)

    def test_move_clears_check_in(self):
        self.act("accept")
        execute("UPDATE bookings SET checked_in_at = NOW() WHERE id = %s", (self.bid,))
        self.act("move", {"ref": self.ref("10:00")})
        self.assertEqual(query("SELECT checked_in_at FROM bookings WHERE id = %s", (self.bid,)), [(None,)])


class RescheduleRequestTests(FacultyActionTestCase):
    def ask(self, ref):
        r = self.clients[0].post(f"/api/bookings/{self.bid}/reschedule", json={"ref": ref, "reason": "Lab exam"})
        self.assertEqual(r.status_code, 200, r.get_json())

    def test_approve_moves_to_the_requested_free_slot(self):
        self.ask(self.ref("09:00"))
        r = self.act("approve-reschedule")
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(self.row()[0], "Waiting")
        self.assertEqual(query("SELECT TIME_FORMAT(start_time, '%H:%i') FROM sessions WHERE id = %s", (self.row()[1],)), [("09:00",)])
        self.assertEqual(self.requests(), 0)

    def test_approve_into_a_session_with_bookings(self):
        other = self.book(2, ref=self.ref("11:00")).get_json()["booking"]["sessionId"]
        self.ask(f"s:{other}")
        self.assertEqual(self.act("approve-reschedule").get_json()["booking"]["token"], 2)
        self.assertEqual(str(self.row()[1]), other)

    def test_approve_when_the_requested_slot_filled_up(self):
        execute("UPDATE faculty_settings SET capacity = 1 WHERE faculty_id = %s", (self.fid,))
        self.ask(self.ref("09:00"))
        self.book(2, ref=self.ref("09:00"))  # someone else takes the only seat first
        self.assertEqual(self.act("approve-reschedule").status_code, 409)
        self.assertEqual(self.requests(), 1)  # nothing changed; the faculty member can pick another slot

    def test_decline_keeps_the_old_time(self):
        self.ask(self.ref("09:00"))
        self.assertEqual(self.act("decline-reschedule").status_code, 200)
        self.assertEqual((self.requests(), str(self.row()[1])), (0, self.sid))
        self.assertEqual(self.history()[-1], "Reschedule request declined by faculty")

    def test_nothing_to_approve_or_decline(self):
        self.assertEqual(self.act("approve-reschedule").status_code, 409)
        self.assertEqual(self.act("decline-reschedule").status_code, 409)


if __name__ == "__main__":
    unittest.main()
