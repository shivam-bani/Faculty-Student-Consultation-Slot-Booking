"""Tests for the queue manager (queue_manager.py): check-in, priority, aging, tie-breaks, Call next.
Needs MySQL running and .env in place.

Run from the project folder (venv active):
    python -m unittest discover -s tests -v
"""
import threading
import unittest
from collections import Counter
from datetime import datetime, timedelta

from app import app
from test_bookings import BookingTestCase, add_faculty, execute, login, query


class QueueTestCase(BookingTestCase):
    """Three students (1, 2, 3) booked into one 3-seat slot that is moved to "now"."""

    def setUp(self):
        super().setUp()
        if datetime.now().hour >= 23:
            self.skipTest("the test slot would run past midnight")
        self.settings = (app.config["AGING_MINUTES"], app.config["CHECKIN_EARLY_MINUTES"])
        app.config.update(AGING_MINUTES=10, CHECKIN_EARLY_MINUTES=10)
        self.bids = {}
        for n in (1, 2, 3):
            final_year = {"profile": {"semester": "VII"}, "consultationType": "Major project"} if n == 2 else {}
            r = self.book(n, **final_year)
            self.assertEqual(r.status_code, 201, r.get_json())
            self.bids[n] = r.get_json()["booking"]["id"]
        self.sid = r.get_json()["booking"]["sessionId"]
        self.faculty = login("unittest-fac1@geu.ac.in")

    def tearDown(self):
        app.config.update(AGING_MINUTES=self.settings[0], CHECKIN_EARLY_MINUTES=self.settings[1])
        super().tearDown()

    def move_slot(self, starts_in, length=30):
        """Put the slot at today's time: it starts `starts_in` minutes from now (negative = already started)."""
        start = datetime.now().replace(second=0, microsecond=0) + timedelta(minutes=starts_in)
        end = start + timedelta(minutes=length)
        execute("UPDATE sessions SET session_date = %s, start_time = %s, end_time = %s WHERE id = %s",
                (start.date(), start.strftime("%H:%M"), end.strftime("%H:%M"), self.sid))

    def accept(self, *students):
        for n in students:
            execute("UPDATE bookings SET status = 'Waiting' WHERE id = %s", (self.bids[n],))

    def arrived(self, n, minutes_ago=0, seconds_ago=0):
        """Mark student n accepted and checked in some time ago (as if they pressed "I'm here" then)."""
        execute("UPDATE bookings SET status = 'Waiting', checked_in_at = NOW() - INTERVAL %s SECOND WHERE id = %s",
                (minutes_ago * 60 + seconds_ago, self.bids[n]))

    def check_in(self, n):
        return self.clients[n - 1].post(f"/api/bookings/{self.bids[n]}/check-in", json={})

    def call_next(self, client=None, **body):
        return (client or self.faculty).post(f"/api/faculty/sessions/{self.sid}/call-next", json=body)

    def called(self, r):
        self.assertEqual(r.status_code, 200, r.get_json())
        person = r.get_json()["called"]
        return next(n for n, bid in self.bids.items() if bid == person["id"]) if person else None

    def status(self, n):
        return query("SELECT status FROM bookings WHERE id = %s", (self.bids[n],))[0][0]


class CheckInTests(QueueTestCase):
    def test_check_in_inside_the_window(self):
        self.move_slot(starts_in=5)  # check-in opened 5 minutes ago
        self.accept(1)
        r = self.check_in(1)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual((r.get_json()["position"], r.get_json()["effectivePriority"]), (1, 1))
        self.assertIsNotNone(query("SELECT checked_in_at FROM bookings WHERE id = %s", (self.bids[1],))[0][0])
        self.assertEqual(query("SELECT text FROM booking_history WHERE booking_id = %s ORDER BY id DESC LIMIT 1",
                               (self.bids[1],))[0][0], "Checked in (I'm here)")
        self.assertEqual(self.check_in(1).status_code, 409)  # only once

    def test_too_early(self):
        self.move_slot(starts_in=30)
        self.accept(1)
        r = self.check_in(1)
        self.assertEqual(r.status_code, 409)
        self.assertIn("Check-in opens at", r.get_json()["error"])

    def test_early_window_is_configurable(self):
        self.move_slot(starts_in=30)
        self.accept(1)
        app.config["CHECKIN_EARLY_MINUTES"] = 45
        self.assertEqual(self.check_in(1).status_code, 200)

    def test_during_the_slot_but_not_after_it(self):
        self.move_slot(starts_in=-10)
        self.accept(1, 2)
        self.assertEqual(self.check_in(1).status_code, 200)  # late arrival, slot still running
        self.move_slot(starts_in=-40)                         # the slot has ended
        self.assertEqual(self.check_in(2).status_code, 409)

    def test_only_accepted_bookings_of_the_student(self):
        self.move_slot(starts_in=5)
        r = self.check_in(1)  # still Pending
        self.assertEqual(r.status_code, 409)
        self.assertIn("accepted", r.get_json()["error"])
        self.accept(1)
        self.assertEqual(self.clients[1].post(f"/api/bookings/{self.bids[1]}/check-in", json={}).status_code, 404)
        self.assertEqual(self.faculty.post(f"/api/bookings/{self.bids[1]}/check-in", json={}).status_code, 403)


class PriorityTests(QueueTestCase):
    def setUp(self):
        super().setUp()
        self.move_slot(starts_in=-5)

    def test_major_project_final_year_goes_first(self):
        self.arrived(1, minutes_ago=3)   # General, priority 1, arrived first
        self.arrived(2, minutes_ago=2)   # Major project in semester VII, priority 2
        self.arrived(3, minutes_ago=1)   # General, priority 1
        self.assertEqual(self.called(self.call_next()), 2)
        self.assertEqual(self.called(self.call_next()), 1)  # same priority: earlier check-in first
        self.assertEqual(self.called(self.call_next()), 3)
        self.assertIsNone(self.called(self.call_next()))     # queue empty: student 3 finished, nobody called
        self.assertEqual([self.status(n) for n in (1, 2, 3)], ["Completed"] * 3)

    def test_only_checked_in_students_are_called(self):
        self.accept(1, 2)    # accepted but not here yet
        self.arrived(3)
        self.assertEqual(self.called(self.call_next()), 3)
        self.assertIsNone(self.called(self.call_next()))
        self.assertEqual((self.status(1), self.status(2)), ("Waiting", "Waiting"))

    def test_tie_goes_to_the_lower_token(self):
        execute("UPDATE bookings SET status = 'Waiting', checked_in_at = NOW() - INTERVAL 1 MINUTE WHERE id IN (%s, %s)",
                (self.bids[1], self.bids[3]))  # same priority, same check-in time
        self.assertEqual(self.called(self.call_next()), 1)

    def test_one_student_in_session_and_history(self):
        self.arrived(1)
        self.arrived(3)
        self.call_next()
        self.assertEqual(self.status(1), "In Session")
        self.call_next()
        self.assertEqual(query("SELECT COUNT(*) FROM bookings WHERE session_id = %s AND status = 'In Session'", (self.sid,))[0][0], 1)
        texts = [t for (t,) in query("SELECT text FROM booking_history WHERE booking_id = %s ORDER BY id", (self.bids[1],))]
        self.assertEqual(texts[-2:], ["Called in by faculty (priority 1)", "Consultation completed"])
        self.assertEqual(query("SELECT completed_at IS NOT NULL, called_at >= checked_in_at FROM bookings WHERE id = %s",
                               (self.bids[1],)), [(1, 1)])


class AgingTests(QueueTestCase):
    def setUp(self):
        super().setUp()
        self.move_slot(starts_in=-5, length=60)

    def effective(self, n):
        entries = self.faculty.get("/api/queue").get_json()["entries"]
        return next(e["effectivePriority"] for e in entries if e["id"] == self.bids[n])

    def test_priority_rises_once_per_completed_interval(self):
        for waited, expected in ((0, 1), (9 * 60 + 59, 1), (10 * 60, 2), (25 * 60, 3)):
            with self.subTest(seconds=waited):
                self.arrived(1, seconds_ago=waited)
                self.assertEqual(self.effective(1), expected)

    def test_long_wait_overtakes_higher_base_priority(self):
        self.arrived(1, minutes_ago=25)  # base 1 + 2 levels of aging = 3
        self.arrived(2)                  # base 2, just arrived
        self.assertEqual(self.called(self.call_next()), 1)
        self.assertEqual(self.call_next().get_json()["priority"], 2)

    def test_aging_interval_is_configurable(self):
        app.config["AGING_MINUTES"] = 1  # demo setting
        self.arrived(1, minutes_ago=3)   # 1 + 3 = 4
        self.arrived(2, minutes_ago=1)   # 2 + 1 = 3
        self.assertEqual(self.effective(1), 4)
        self.assertEqual(self.called(self.call_next()), 1)

    def test_queue_positions_match_call_order(self):
        self.arrived(1, minutes_ago=3)
        self.arrived(2, minutes_ago=2)
        self.arrived(3, minutes_ago=1)
        own = self.clients[2].get("/api/queue").get_json()
        self.assertEqual([(e["id"], e["position"]) for e in own["entries"]], [(self.bids[3], 3)])  # only their own booking
        self.assertEqual((own["agingMinutes"], own["checkinEarlyMinutes"]), (10, 10))
        self.call_next()  # student 2 is called: the others move up
        self.assertEqual(self.clients[2].get("/api/queue").get_json()["entries"][0]["position"], 2)


class CallNextRuleTests(QueueTestCase):
    def test_before_and_after_the_slot(self):
        self.move_slot(starts_in=30)
        self.assertEqual(self.call_next().status_code, 409)
        self.move_slot(starts_in=-40)
        self.assertEqual(self.call_next().status_code, 409)

    def test_only_the_slots_own_faculty_member(self):
        self.move_slot(starts_in=-5)
        self.arrived(1)
        add_faculty(2)
        self.assertEqual(self.call_next(login("unittest-fac2@geu.ac.in")).status_code, 404)
        self.assertEqual(self.call_next(self.clients[0]).status_code, 403)
        self.assertEqual(self.status(1), "Waiting")

    def test_stale_page_does_nothing(self):
        self.move_slot(starts_in=-5)
        self.arrived(1)
        self.arrived(3)
        self.call_next(current=None)                       # student 1 called
        r = self.call_next(current=None)                   # an old page still thinks nobody is in session
        self.assertEqual(r.status_code, 409)
        self.assertEqual((self.status(1), self.status(3)), ("In Session", "Waiting"))
        self.assertEqual(self.called(self.call_next(current=self.bids[1])), 3)

    def test_double_click_at_the_same_moment_calls_one_student(self):
        self.move_slot(starts_in=-5)
        self.arrived(1, minutes_ago=2)
        self.arrived(3, minutes_ago=1)
        clients = [self.faculty, login("unittest-fac1@geu.ac.in")]
        barrier, results = threading.Barrier(2), [None, None]

        def click(i):
            barrier.wait()
            results[i] = self.call_next(clients[i], current=None).status_code

        threads = [threading.Thread(target=click, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(Counter(results), Counter({200: 1, 409: 1}))
        self.assertEqual((self.status(1), self.status(3)), ("In Session", "Waiting"))  # nobody skipped


class SettingsTests(unittest.TestCase):
    def test_bad_values_stop_the_server_from_starting(self):
        import os
        from unittest import mock

        from app import minutes_setting
        for bad in ("0", "abc", "500"):
            with self.subTest(value=bad), mock.patch.dict(os.environ, {"AGING_MINUTES": bad}):
                with self.assertRaises(RuntimeError):
                    minutes_setting("AGING_MINUTES", 10, 1, 120)
        with mock.patch.dict(os.environ, {"AGING_MINUTES": "1"}):
            self.assertEqual(minutes_setting("AGING_MINUTES", 10, 1, 120), 1)


if __name__ == "__main__":
    unittest.main()
