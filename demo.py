"""Demo data for presenting CampusSync:  flask --app app seed-demo  (or double-click demo.bat).

Removes the previous demo accounts (emails demo.*@geu.ac.in) and everything linked to them, then
builds a fresh scene around the current time, using the real API so history and notifications
are real too:
  - a faculty member linked to a real timetable from the university file
  - extra consultation hours today, starting a few minutes from now (check-in is already open)
  - three students booked into it and accepted; one is a final-year Major project student
  - two pending requests for later days, one with a reschedule request
Then the presenter only has to press "I'm here" as the students and "Call next" as the faculty.
The real admin account and all non-demo data are never touched.
The demo password is written to demo-logins.txt (git-ignored), never printed in full elsewhere.
"""
import secrets
import threading
from datetime import datetime, timedelta
from pathlib import Path

import click
from flask import current_app
from flask.cli import with_appcontext
from werkzeug.security import generate_password_hash

import db
from notifications import notify

LOGINS_FILE = Path(__file__).resolve().parent / "demo-logins.txt"
FACULTY_EMAIL = "demo.faculty@geu.ac.in"
STUDENTS = [  # name, email, semester, consultation type, course
    ("Aarav Sharma", "demo.aarav@geu.ac.in", "VIII", "Major project", "B.Tech CSE"),
    ("Diya Rawat", "demo.diya@geu.ac.in", "V", "General", "BCA"),
    ("Kabir Negi", "demo.kabir@geu.ac.in", "VI", "General", "B.Tech CSE"),
    ("Ananya Joshi", "demo.ananya@geu.ac.in", "VII", "Major project", "B.Tech CSE"),
]
DEMO_USERS = "SELECT id FROM users WHERE email LIKE 'demo.%@geu.ac.in'"


def remove_demo_data(cur):
    """Delete the demo accounts and what belongs to them (foreign keys decide the order)."""
    cur.execute(f"DELETE FROM notifications WHERE user_id IN ({DEMO_USERS})")
    cur.execute(f"DELETE FROM bookings WHERE student_id IN ({DEMO_USERS}) OR session_id IN "
                f"(SELECT id FROM sessions WHERE faculty_id IN ({DEMO_USERS}))")
    for table, column in (("student_profiles", "user_id"), ("sessions", "faculty_id"), ("custom_slots", "faculty_id"),
                          ("slots_off", "faculty_id"), ("consult_days", "faculty_id"), ("faculty_settings", "faculty_id")):
        cur.execute(f"DELETE FROM {table} WHERE {column} IN ({DEMO_USERS})")
    cur.execute("DELETE FROM users WHERE email LIKE 'demo.%@geu.ac.in'")


def ok(response, what):
    if response.status_code not in (200, 201):
        raise click.ClickException(f"{what} failed: {response.get_json()}")
    return response.get_json()


@click.command("seed-demo")
@with_appcontext
def seed_demo():
    now = datetime.now()
    start = (now + timedelta(minutes=5 - now.minute % 5 + 5)).replace(second=0, microsecond=0)  # 5-10 minutes from now
    end = start + timedelta(minutes=60)
    if now.isoweekday() == 7:
        raise click.ClickException("Consultations run Monday to Saturday. Run the demo setup on a working day.")
    if end.date() != now.date():
        raise click.ClickException("Too late in the day for a one-hour demo slot. Run it before 22:45.")

    conn = db.get_db()
    cur = conn.cursor()
    remove_demo_data(cur)

    # The faculty member: linked to the first timetable from the university file that nobody uses yet.
    cur.execute("SELECT p.id, p.name FROM timetable_profiles p LEFT JOIN faculty_settings f ON f.profile_id = p.id "
                "WHERE p.source = 'imported' AND f.faculty_id IS NULL ORDER BY p.name LIMIT 1")
    row = cur.fetchone()
    if row is None:
        raise click.ClickException("Every timetable is already linked to a faculty account.")
    profile_id, faculty_name = row
    password = secrets.token_urlsafe(9)
    cur.execute("INSERT INTO users (name, email, password_hash, role) VALUES (%s, %s, %s, 'faculty')",
                (faculty_name, FACULTY_EMAIL, generate_password_hash(password)))
    fid = cur.lastrowid
    cur.execute("INSERT INTO faculty_settings (faculty_id, profile_id, slot_len, capacity) VALUES (%s, %s, 30, 3)", (fid, profile_id))
    cur.executemany("INSERT INTO consult_days (faculty_id, day_of_week) VALUES (%s, %s)", [(fid, d) for d in range(1, 7)])
    cur.execute("INSERT INTO custom_slots (faculty_id, day_of_week, start_time, end_time, capacity) VALUES (%s, %s, %s, %s, 4)",
                (fid, now.isoweekday(), start.strftime("%H:%M"), end.strftime("%H:%M")))
    notify(cur, fid, "Welcome to CampusSync", "Your faculty account was created by the admin. "
                                              "Student requests for your free periods appear under Booking Requests.")
    conn.commit()  # the API calls below use their own connections

    # The flask command runs inside an app context, and requests made from it would share that
    # context (and its cached logged-in user). A separate thread has no app context, so every API
    # call below gets its own, exactly like a real request from a browser.
    app, errors = current_app._get_current_object(), []

    def run():
        try:
            build_scene(app, password, fid, faculty_name, now, start, end)
        except Exception as e:  # reported by the main thread
            errors.append(e)

    worker = threading.Thread(target=run)
    worker.start()
    worker.join()
    if errors:
        raise errors[0]
    click.echo(f"Demo ready: slot today {start:%H:%M}-{end:%H:%M}, 4 students, 1 faculty. Logins in {LOGINS_FILE.name}.")


def build_scene(app, password, fid, faculty_name, now, start, end):
    def client_for(email):
        client = app.test_client()
        ok(client.post("/api/auth/login", json={"email": email, "password": password}), f"Login as {email}")
        return client

    signup = app.test_client()
    students = []
    for n, (name, email, semester, ctype, course) in enumerate(STUDENTS, 1):
        ok(signup.post("/api/auth/signup", json={"name": name, "email": email, "password": password}), f"Sign-up of {name}")
        students.append((client_for(email), semester, ctype, course, n))

    slots = ok(students[0][0].get(f"/api/faculty/{fid}/slots"), "Listing slots")["slots"]
    today_ref = next(s["ref"] for s in slots if s["date"] == now.date().isoformat() and s["start"] == start.strftime("%H:%M"))
    later = [s["ref"] for s in slots if s["date"] > now.date().isoformat()]

    def book(student, ref, topic):
        client, semester, ctype, course, n = student
        profile = {"studentId": f"DEMO{n:03d}", "rollNo": f"2201{n:03d}", "course": course, "semester": semester,
                   "section": "A", "groupId": f"PBL-CSE-{semester}-{n:02d}"}
        return ok(client.post("/api/bookings", json={"ref": ref, "topic": topic, "desc": "", "consultationType": ctype,
                                                     "profile": profile}), "Booking")["booking"]

    today = [book(students[0], today_ref, "Final project report review"),
             book(students[1], today_ref, "Database schema doubts"),
             book(students[2], today_ref, "OS scheduling assignment")]
    pending = book(students[3], later[0], "Major project progress review")
    book(students[1], later[3], "Viva preparation")
    ok(students[3][0].post(f"/api/bookings/{pending['id']}/reschedule", json={"ref": later[6], "reason": "Lab exam clash"}),
       "Reschedule request")

    faculty = client_for(FACULTY_EMAIL)
    for b in today:
        ok(faculty.post(f"/api/faculty/bookings/{b['id']}/accept", json={}), "Accepting a booking")

    lines = [
        "CampusSync demo logins (made by demo.bat; run it again for fresh data)",
        f"Password for every demo account below: {password}",
        "",
        f"Faculty:  {FACULTY_EMAIL}   ({faculty_name})   -> Login as Faculty",
        *[f"Student:  {email}   ({name}, semester {sem}, {ctype})   -> Login as Student"
          for name, email, sem, ctype, _ in STUDENTS],
        "Admin:    your own admin account (not changed by the demo)",
        "",
        f"Today's demo slot: {start:%H:%M} - {end:%H:%M} with {faculty_name}; Aarav, Diya and Kabir are accepted.",
        "Check-in (\"I'm here\") is open from now until the slot ends. Aging: +1 priority per minute (demo setting).",
        "",
        "Suggested order:",
        "1. Student Kabir: My Appointments -> I'm here (position 1).",
        "2. Student Diya (private window): I'm here (position 2).",
        "3. Student Aarav: I'm here -> jumps to position 1 (final-year Major project = priority 2).",
        "   Aging: every minute of waiting adds 1. If Kabir has waited a minute or more longer than Aarav,",
        "   Kabir catches up and goes first (equal priority -> earlier check-in). The Live Queue shows the numbers.",
        "4. Faculty: Live Queue -> Call next, and again; explain each choice from the Priority column.",
        "5. Faculty: Booking Requests shows Ananya's pending request with a reschedule request; Notifications.",
        "6. Students: Notifications show 'Request accepted' and 'It's your turn'.",
    ]
    LOGINS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def init_app(app):
    app.cli.add_command(seed_demo)
