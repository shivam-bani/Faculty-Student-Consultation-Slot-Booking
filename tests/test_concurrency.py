"""Concurrency tests: many booking requests for the same slot at the same instant.

Each request runs in its own thread with its own database connection, and a barrier releases
them all together, so they really race inside MySQL. Needs MySQL running and .env in place.

Run from the project folder (venv active):
    python -m unittest tests.test_concurrency -v
"""
import threading
import unittest
from collections import Counter
from unittest import mock

import mysql.connector

import bookings
from app import app
from test_bookings import add_faculty, add_user, body, booking_day, clean_up, login, query

ROUNDS = 5  # repeat the race to make a lucky pass unlikely


def race(clients_and_bodies):
    """Send all requests at the same moment; return the HTTP status codes."""
    barrier = threading.Barrier(len(clients_and_bodies))
    results = [None] * len(clients_and_bodies)

    def run(i, client, json):
        barrier.wait()  # every thread waits here, then all are released together
        results[i] = client.post("/api/bookings", json=json).status_code

    threads = [threading.Thread(target=run, args=(i, c, b)) for i, (c, b) in enumerate(clients_and_bodies)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


class SimultaneousBookingTests(unittest.TestCase):
    def setUp(self):
        clean_up()
        self.fid = add_faculty(1, capacity=3)
        self.day = booking_day()
        for n in range(1, 11):
            add_user(f"unittest-s{n}@geu.ac.in", "student")
        self.clients = [login(f"unittest-s{n}@geu.ac.in") for n in range(1, 11)]

    def tearDown(self):
        clean_up()

    def test_ten_students_one_three_seat_slot(self):
        for round_ in range(ROUNDS):
            start = f"{8 + round_:02d}:00"  # a different free slot each round
            ref = f"v:{self.fid}|{self.day}|{start}"
            with self.subTest(round=round_ + 1, slot=start):
                statuses = race([(c, body(ref, n + 1)) for n, c in enumerate(self.clients)])
                self.assertEqual(Counter(statuses), Counter({201: 3, 409: 7}), statuses)
                sessions = query("SELECT id FROM sessions WHERE faculty_id = %s AND session_date = %s AND start_time = %s",
                                 (self.fid, self.day, start))
                self.assertEqual(len(sessions), 1)  # never two sessions for one slot
                tokens = query("SELECT token FROM bookings WHERE session_id = %s ORDER BY token", (sessions[0][0],))
                self.assertEqual([t[0] for t in tokens], [1, 2, 3])  # never over capacity, no shared token

    def test_double_click_books_once(self):
        ref = f"v:{self.fid}|{self.day}|14:00"
        statuses = race([(self.clients[0], body(ref, 1))] * 5)  # one student, five identical requests
        self.assertEqual(statuses.count(201), 1, statuses)
        self.assertEqual(query("SELECT COUNT(*) FROM bookings b JOIN sessions s ON s.id = b.session_id "
                               "WHERE s.faculty_id = %s", (self.fid,))[0][0], 1)


class DeadlockRecoveryTests(unittest.TestCase):
    """If MySQL breaks a deadlock by undoing a booking transaction, the booking is tried again."""

    def setUp(self):
        clean_up()
        self.fid = add_faculty(1)
        add_user("unittest-s1@geu.ac.in", "student")
        self.client = login("unittest-s1@geu.ac.in")
        self.ref = f"v:{self.fid}|{booking_day()}|08:00"

    def tearDown(self):
        clean_up()

    def deadlock(self):
        return mysql.connector.errors.InternalError(msg="Deadlock found when trying to get lock", errno=1213)

    def test_booking_is_retried_after_a_deadlock(self):
        real, calls = bookings.find_session, []

        def first_attempt_deadlocks(*args):
            calls.append(1)
            if len(calls) == 1:
                raise self.deadlock()
            return real(*args)

        with mock.patch.object(bookings, "find_session", side_effect=first_attempt_deadlocks), \
             mock.patch.object(app.logger, "disabled", True):
            r = self.client.post("/api/bookings", json=body(self.ref))
        self.assertEqual(r.status_code, 201, r.get_json())
        self.assertEqual(len(calls), 2)  # the first attempt was undone, the second succeeded
        self.assertEqual(query("SELECT COUNT(*) FROM bookings b JOIN sessions s ON s.id = b.session_id "
                               "WHERE s.faculty_id = %s", (self.fid,))[0][0], 1)

    def test_gives_up_after_three_attempts_without_saving_anything(self):
        with mock.patch.object(bookings, "find_session", side_effect=self.deadlock()) as fake, \
             mock.patch.object(app.logger, "disabled", True):
            r = self.client.post("/api/bookings", json=body(self.ref))
        self.assertEqual(r.status_code, 500)
        self.assertEqual(fake.call_count, bookings.RETRIES)
        self.assertEqual(query("SELECT COUNT(*) FROM student_profiles p JOIN users u ON u.id = p.user_id "
                               "WHERE u.email = 'unittest-s1@geu.ac.in'")[0][0], 0)  # rolled back each time


if __name__ == "__main__":
    unittest.main()
