"""Bookings and sessions: the JSON API under /api.

    GET  /api/bookings       the sessions and bookings this user may see, in the shape script.js uses
    GET  /api/profile        the student's saved details (to fill in the booking form)
    POST /api/bookings       a student books a slot (one locked transaction; see book())
"""
import re
from datetime import datetime

import mysql.connector
from flask import Blueprint, abort, current_app, jsonify, make_response

import db
from auth import current_user, error, json_body, login_required
from faculty import sessions_of, week_data
from slots import overlaps, upcoming_slots

bp = Blueprint("bookings", __name__, url_prefix="/api")

COURSES = ("B.Tech CSE", "BCA", "BCA (AI/DS)", "BCA (CS/CL)", "B.Sc IT", "B.Sc CS", "MCA", "Other")  # as on book.html
SEMESTERS = ("I", "II", "III", "IV", "V", "VI", "VII", "VIII")
ID_PATTERN = re.compile(r"[A-Z0-9-]{3,20}")
VIRTUAL_REF = re.compile(r"v:(\d+)\|(\d{4}-\d{2}-\d{2})\|(\d{2}:\d{2})")
SESSION_REF = re.compile(r"s:(\d+)")
ACTIVE = "('Pending', 'Waiting', 'In Session')"  # statuses that hold a seat and are not over
RETRIES = 3  # attempts at a booking transaction that MySQL undid to break a deadlock

SESSION_COLUMNS = """
    SELECT s.id, f.email, f.name, s.topic, s.description, DATE_FORMAT(s.session_date, '%Y-%m-%d'),
           TIME_FORMAT(s.start_time, '%H:%i'), TIME_FORMAT(s.end_time, '%H:%i'), s.capacity, s.source, s.cancelled
    FROM sessions s JOIN users f ON f.id = s.faculty_id"""

BOOKING_COLUMNS = """
    SELECT b.id, b.session_id, u.email, u.name, p.student_id, p.roll_no, p.course, p.semester, p.section, p.group_id,
           b.topic, b.description, b.token, b.status, UNIX_TIMESTAMP(b.created_at) * 1000,
           b.consultation_type, b.base_priority,
           UNIX_TIMESTAMP(b.checked_in_at) * 1000, UNIX_TIMESTAMP(b.called_at) * 1000
    FROM bookings b
    JOIN sessions s ON s.id = b.session_id
    JOIN users u ON u.id = b.student_id
    LEFT JOIN student_profiles p ON p.user_id = b.student_id"""


def ms(value):
    return int(value) if value is not None else None


def session_json(row):
    sid, femail, fname, topic, desc, date, start, end, capacity, source, cancelled = row
    s = {"id": str(sid), "facultyEmail": femail, "facultyName": fname, "topic": topic, "desc": desc, "date": date,
         "start": start, "end": end, "capacity": capacity, "source": source, "cancelled": bool(cancelled)}
    if source == "timetable":
        s["origKey"] = f"{date}|{start}"  # marks the timetable slot this session was made from
    return s


def booking_json(row):
    (bid, sid, email, name, student_id, roll_no, course, semester, section, group_id,
     topic, desc, token, status, created, ctype, priority, checked_in, called) = row
    return {"id": str(bid), "sessionId": str(sid), "studentEmail": email, "studentName": name,
            "student": {"name": name, "studentId": student_id, "rollNo": roll_no, "course": course,
                        "semester": semester, "section": section, "groupId": group_id},
            "topic": topic, "desc": desc, "token": token, "status": status, "createdAt": ms(created),
            "consultationType": ctype, "basePriority": priority,
            "checkedInAt": ms(checked_in), "calledAt": ms(called), "reschedule": None, "history": []}


def visible(user):
    """SQL conditions (fixed text, never user input) and parameters for what this user may see:
    admins everything, faculty their own sessions, students their own bookings and those sessions."""
    if user["role"] == "admin":
        return "TRUE", "TRUE", ()
    if user["role"] == "faculty":
        return "s.faculty_id = %s", "s.faculty_id = %s", (user["id"],)
    return "b.student_id = %s", "s.id IN (SELECT session_id FROM bookings WHERE student_id = %s)", (user["id"],)


@bp.get("/bookings")
@login_required()
def list_bookings():
    booking_where, session_where, params = visible(current_user())
    cur = db.get_db().cursor()
    cur.execute(f"{SESSION_COLUMNS} WHERE {session_where} ORDER BY s.session_date, s.start_time", params)
    sessions = [session_json(r) for r in cur.fetchall()]
    cur.execute(f"{BOOKING_COLUMNS} WHERE {booking_where} ORDER BY b.id", params)
    bookings = {b["id"]: b for b in map(booking_json, cur.fetchall())}
    if bookings:
        ids = [int(i) for i in bookings]
        cur.execute(f"SELECT booking_id, UNIX_TIMESTAMP(at) * 1000, text FROM booking_history "
                    f"WHERE booking_id IN ({', '.join(['%s'] * len(ids))}) ORDER BY id", ids)
        for bid, at, text in cur.fetchall():
            bookings[str(bid)]["history"].append({"at": ms(at), "text": text})
    return jsonify(ok=True, sessions=sessions, bookings=list(bookings.values()))


@bp.get("/profile")
@login_required("student")
def profile():
    cur = db.get_db().cursor()
    cur.execute("SELECT student_id, roll_no, course, semester, section, group_id FROM student_profiles WHERE user_id = %s",
                (current_user()["id"],))
    row = cur.fetchone()
    keys = ("studentId", "rollNo", "course", "semester", "section", "groupId")
    return jsonify(ok=True, profile=dict(zip(keys, row)) if row else None)


def base_priority(semester, consultation_type):
    """Priority scheduling: final-year (semester VII or VIII) Major project consultations go first."""
    return 2 if semester in ("VII", "VIII") and consultation_type == "Major project" else 1


def profile_from_request(data):
    p = data.get("profile") or {}
    profile = {"student_id": str(p.get("studentId", "")).strip().upper(), "roll_no": str(p.get("rollNo", "")).strip().upper(),
               "course": str(p.get("course", "")), "semester": str(p.get("semester", "")),
               "section": str(p.get("section", "")).strip().upper(),
               "group_id": str(p.get("groupId") or "").strip().upper() or None}
    if not ID_PATTERN.fullmatch(profile["student_id"]) or not ID_PATTERN.fullmatch(profile["roll_no"]):
        abort(error("Student ID and roll number need 3 to 20 letters, digits or hyphens.", 400))
    if profile["course"] not in COURSES or profile["semester"] not in SEMESTERS:
        abort(error("Choose your course and semester.", 400))
    if not 1 <= len(profile["section"]) <= 10 or len(profile["group_id"] or "") > 30:
        abort(error("Enter your section (up to 10 characters); the PBL group ID can have up to 30.", 400))
    return profile


def has_started(date, start, now):
    today = now.date().isoformat()
    return date < today or (date == today and start <= now.strftime("%H:%M"))


def save_profile(cur, user_id, profile):
    """Save the student's details on their own row only.

    INSERT first, and UPDATE only when this student already has a row. Two other ways were wrong:
    - INSERT ... ON DUPLICATE KEY UPDATE also fires on ANOTHER student's UNIQUE student ID or roll
      number, and overwrote that student's row.
    - UPDATE first locks the empty "gap" when the row does not exist yet (REPEATABLE READ); two new
      students then each waited for the other's gap lock before their INSERT: a deadlock.
    A clash with another student's student ID or roll number raises the UNIQUE key error (1062)."""
    values = {**profile, "uid": user_id}
    try:
        cur.execute("INSERT INTO student_profiles (user_id, student_id, roll_no, course, semester, section, group_id) "
                    "VALUES (%(uid)s, %(student_id)s, %(roll_no)s, %(course)s, %(semester)s, %(section)s, %(group_id)s)", values)
    except mysql.connector.IntegrityError as e:
        if e.errno != 1062 or "PRIMARY" not in e.msg:
            raise  # e.g. uq_profiles_student_id: someone else's student ID
        cur.execute("UPDATE student_profiles SET student_id = %(student_id)s, roll_no = %(roll_no)s, course = %(course)s, "
                    "semester = %(semester)s, section = %(section)s, group_id = %(group_id)s WHERE user_id = %(uid)s", values)


def lock_faculty(cur, fid):
    """Deadlock prevention. Every booking takes its locks in the same order: the student's row, the
    faculty member's row, then the session row. With one fixed order no two bookings can each hold a
    lock the other is waiting for (no circular wait). It also means only one booking at a time can
    create a faculty member's session, so two "first" bookings cannot fight over the same new row."""
    cur.execute("SELECT id FROM users WHERE id = %s AND role = 'faculty' FOR UPDATE", (fid,))
    cur.fetchone()


def find_session(cur, ref, now):
    """Turn the slot reference from the booking form into a locked session row.

        "s:<id>"                      an existing session
        "v:<faculty>|<date>|<start>"  a free timetable slot; it becomes a session on its first booking
    Returns (session id, faculty id, date, start, end, capacity), or aborts if the slot cannot be booked."""
    m = VIRTUAL_REF.fullmatch(ref)
    if m:
        fid, date, start = int(m.group(1)), m.group(2), m.group(3)
        lock_faculty(cur, fid)
        offered = upcoming_slots(week_data(cur, fid)[3], sessions_of(cur, fid), now)  # the 14 days the page offers
        slot = next((s for s in offered if s["date"] == date and s["start"] == start), None)
        if slot is None:
            abort(error("This slot is no longer available. Please choose another one.", 409))
        if slot["session_id"] is None:
            # UNIQUE(faculty_id, session_date, start_time): if another student creates this session at the
            # same moment, this INSERT waits for them and then reuses their row instead of making a second one.
            cur.execute("INSERT INTO sessions (faculty_id, session_date, start_time, end_time, capacity, source, description) "
                        "VALUES (%s, %s, %s, %s, %s, 'timetable', %s) ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)",
                        (fid, date, start, slot["end"], slot["capacity"], "From the faculty member's weekly timetable"))
            sid = cur.lastrowid
        else:
            sid = slot["session_id"]
    else:
        m = SESSION_REF.fullmatch(ref)
        if not m:
            abort(error("Please choose a time slot.", 400))
        sid = int(m.group(1))
        cur.execute("SELECT faculty_id FROM sessions WHERE id = %s", (sid,))
        row = cur.fetchone()
        if row is None:
            abort(error("This slot is no longer available. Please choose another one.", 409))
        lock_faculty(cur, row[0])
    # The lock (a mutex on this session): every other booking for it waits here until this transaction ends.
    cur.execute("SELECT s.id, s.faculty_id, DATE_FORMAT(s.session_date, '%Y-%m-%d'), TIME_FORMAT(s.start_time, '%H:%i'), "
                "TIME_FORMAT(s.end_time, '%H:%i'), s.capacity, s.cancelled, f.is_active "
                "FROM sessions s JOIN users f ON f.id = s.faculty_id WHERE s.id = %s FOR UPDATE", (sid,))
    row = cur.fetchone()
    if row is None or row[6] or not row[7]:
        abort(error("This slot is no longer available. Please choose another one.", 409))
    if has_started(row[2], row[3], now):
        abort(error("This slot has already started. Please choose another one.", 409))
    return row[:6]


@bp.post("/bookings")
@login_required("student")
def book():
    """Book a slot. Everything happens in one transaction with row locks, so two students booking
    at the same moment are handled one after the other: no double booking, no going over capacity."""
    data = json_body()
    topic, desc = str(data.get("topic", "")).strip(), str(data.get("desc", "")).strip()
    ctype = data.get("consultationType", "General")
    if not 1 <= len(topic) <= 150 or len(desc) > 1000:
        return error("Enter the consultation topic (up to 150 characters).", 400)
    if ctype not in ("General", "Major project"):
        return error("Choose the consultation type.", 400)
    profile = profile_from_request(data)
    student = current_user()
    conn = db.get_db()
    for attempt in range(1, RETRIES + 1):
        try:
            return book_once(conn, student, profile, str(data.get("ref", "")), topic, desc, ctype, datetime.now())
        except mysql.connector.Error as e:
            conn.rollback()
            # Deadlock recovery: MySQL broke a deadlock by undoing this whole transaction (1213), or a lock
            # wait timed out (1205). Nothing was saved, so the booking can safely be tried again.
            if e.errno not in (1213, 1205) or attempt == RETRIES:
                raise
            current_app.logger.warning("Booking attempt %s hit MySQL error %s; trying again", attempt, e.errno)


def book_once(conn, student, profile, ref, topic, desc, ctype, now):
    """One attempt at the booking transaction (see book())."""
    cur = conn.cursor()
    try:
        # Lock order is always: the student's row, the faculty member's row, then the session row
        # (see lock_faculty). The student lock also stops a double click from booking twice.
        cur.execute("SELECT id FROM users WHERE id = %s FOR UPDATE", (student["id"],))
        cur.fetchone()
        save_profile(cur, student["id"], profile)
        sid, fid, date, start, end, capacity = find_session(cur, ref, now)

        # Inside the lock: capacity works like a counting semaphore for this session.
        # These reads end in FOR UPDATE on purpose. Under MySQL's default REPEATABLE READ a plain SELECT
        # reads an older snapshot of the data; a locking read always sees the latest committed bookings,
        # including those just made by the students who held the lock before us.
        cur.execute("SELECT COUNT(*) FROM bookings WHERE session_id = %s AND status NOT IN ('Rejected', 'Cancelled') "
                    "FOR UPDATE", (sid,))
        if cur.fetchone()[0] >= capacity:
            conn.rollback()
            return error("This session is full.", 409)
        cur.execute(f"SELECT COUNT(*) FROM bookings WHERE session_id = %s AND student_id = %s AND status IN {ACTIVE} FOR UPDATE",
                    (sid, student["id"]))
        if cur.fetchone()[0]:
            conn.rollback()
            return error("You have already booked this session.", 409)
        cur.execute(f"SELECT b.token, TIME_FORMAT(s.start_time, '%H:%i'), TIME_FORMAT(s.end_time, '%H:%i') "
                    f"FROM bookings b JOIN sessions s ON s.id = b.session_id "
                    f"WHERE b.student_id = %s AND b.status IN {ACTIVE} AND s.session_date = %s", (student["id"], date))
        clash = next((r for r in cur.fetchall() if overlaps(r[1], r[2], start, end)), None)
        if clash:
            conn.rollback()
            return error(f"You already have a consultation at that time (token #{clash[0]}). Choose a different slot.", 409)

        cur.execute("SELECT COALESCE(MAX(token), 0) + 1 FROM bookings WHERE session_id = %s FOR UPDATE", (sid,))
        token = cur.fetchone()[0]  # tokens are never reused, even after a cancellation
        priority = base_priority(profile["semester"], ctype)
        cur.execute("INSERT INTO bookings (session_id, student_id, token, topic, description, consultation_type, base_priority) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)", (sid, student["id"], token, topic, desc, ctype, priority))
        bid = cur.lastrowid
        cur.execute("INSERT INTO booking_history (booking_id, text) VALUES (%s, 'Requested by student')", (bid,))
        conn.commit()
    except mysql.connector.IntegrityError as e:
        conn.rollback()
        if e.errno == 1062:  # a UNIQUE key caught something the checks above did not (the report's promise)
            if "uq_profiles" in e.msg:
                return error("This student ID or roll number is already used by another account.", 409)
            return error("Someone booked at the same moment. Please choose the slot again.", 409)
        raise
    return make_response(jsonify(ok=True, booking={"id": str(bid), "sessionId": str(sid), "token": token,
                                                   "basePriority": priority, "status": "Pending"}), 201)
