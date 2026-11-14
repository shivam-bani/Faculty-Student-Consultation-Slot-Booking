"""Faculty accounts: the list every logged-in user needs, and the admin's management API.

    GET  /api/faculty                          active faculty with their linked timetable
    POST /api/admin/faculty                    create (or re-activate) a faculty account
    POST /api/admin/faculty/<id>/link          link the account to another timetable
    POST /api/admin/faculty/<id>/password      set a new temporary password
    POST /api/admin/faculty/<id>/deactivate    remove from CampusSync (history is kept)
    GET  /api/faculty/<id>/week                classes and weekly consultation slots
    GET  /api/faculty/<id>/slots               what students can book in the next 14 days
    POST /api/admin/faculty/<id>/settings      slot length, students per slot, consultation days
    POST /api/admin/faculty/<id>/slots-off     stop or start offering one weekly free slot
    POST /api/admin/faculty/<id>/slots-off/clear   offer all free slots again
    POST /api/admin/faculty/<id>/custom-slots  add extra consultation hours
    POST /api/admin/custom-slots/<id>/delete   remove extra consultation hours
"""
import re
from datetime import datetime

import mysql.connector
from flask import Blueprint, abort, jsonify, make_response, request
from werkzeug.security import generate_password_hash

import db
from auth import MAX_PASSWORD, MIN_PASSWORD, error, json_body, login_required
from slots import upcoming_slots, weekly_slots

bp = Blueprint("faculty", __name__, url_prefix="/api")

EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
PROFILE_ID = re.compile(r"[a-z0-9-]{1,80}")
TIME = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
DEFAULT_DAYS = (1, 2, 3, 4, 5)  # Monday to Friday, as in the old browser-only version
SLOT_LENGTHS = (15, 20, 30, 45, 55)  # minutes, the choices on the admin Timetables page

FACULTY_QUERY = """
    SELECT u.id, u.name, u.email, s.profile_id, p.name
    FROM users u
    LEFT JOIN faculty_settings s ON s.faculty_id = u.id
    LEFT JOIN timetable_profiles p ON p.id = s.profile_id
    WHERE u.role = 'faculty' AND u.is_active"""


def faculty_json(row):
    fid, name, email, profile_id, profile_name = row
    return {"id": fid, "name": name, "email": email, "profile_id": profile_id, "profile_name": profile_name}


def profile_holder(cur, profile_id):
    """Email of the active account already linked to this timetable, or None."""
    cur.execute("SELECT u.email FROM faculty_settings s JOIN users u ON u.id = s.faculty_id "
                "WHERE s.profile_id = %s AND u.is_active", (profile_id,))
    row = cur.fetchone()
    return row[0] if row else None


def all_settings(cur):
    """Consultation settings of every faculty member, keyed by faculty id. Same shape the pages
    keep in the browser: slot_len, capacity, days, off (["day|HH:MM", ...]) and custom slots."""
    settings = {}
    cur.execute("SELECT faculty_id, slot_len, capacity FROM faculty_settings")
    for fid, slot_len, capacity in cur.fetchall():
        settings[fid] = {"slot_len": slot_len, "capacity": capacity, "days": [], "off": [], "custom": []}
    cur.execute("SELECT faculty_id, day_of_week FROM consult_days ORDER BY day_of_week")
    for fid, day in cur.fetchall():
        settings.get(fid, {}).get("days", []).append(day)
    cur.execute("SELECT faculty_id, day_of_week, TIME_FORMAT(start_time, '%H:%i') FROM slots_off ORDER BY 2, 3")
    for fid, day, start in cur.fetchall():
        settings.get(fid, {}).get("off", []).append(f"{day}|{start}")
    cur.execute("SELECT id, faculty_id, day_of_week, TIME_FORMAT(start_time, '%H:%i'), TIME_FORMAT(end_time, '%H:%i'), "
                "capacity FROM custom_slots ORDER BY day_of_week, start_time, id")
    for cid, fid, day, start, end, capacity in cur.fetchall():
        settings.get(fid, {}).get("custom", []).append(
            {"id": str(cid), "day": day, "start": start, "end": end, "capacity": capacity})
    return settings


@bp.get("/faculty")
@login_required()
def list_faculty():
    cur = db.get_db().cursor()
    cur.execute(FACULTY_QUERY + " ORDER BY u.name")
    rows = cur.fetchall()
    settings = all_settings(cur)
    return jsonify(ok=True, faculty=[{**faculty_json(r), "settings": settings.get(r[0])} for r in rows])


@bp.post("/admin/faculty")
@login_required("admin")
def create_faculty():
    data = json_body()
    name = str(data.get("name", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))
    profile_id = str(data.get("profile_id", "")).strip()
    profile_name = str(data.get("profile_name", "")).strip() or name
    if not 1 <= len(name) <= 100:
        return error("Enter the full name (up to 100 characters).", 400)
    if len(email) > 254 or not EMAIL.fullmatch(email):
        return error("Enter a valid email address.", 400)
    if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        return error(f"The temporary password needs {MIN_PASSWORD} to {MAX_PASSWORD} characters.", 400)
    if not PROFILE_ID.fullmatch(profile_id):
        return error("Choose the timetable to link.", 400)

    conn = db.get_db()
    cur = conn.cursor()
    holder = profile_holder(cur, profile_id)
    if holder:
        return error(f"That timetable is already linked to {holder}.", 409)
    # A faculty member who is not in the university timetable gets a new, empty timetable.
    cur.execute("INSERT IGNORE INTO timetable_profiles (id, name, source, edited) VALUES (%s, %s, 'admin', TRUE)",
                (profile_id, profile_name[:120]))

    cur.execute("SELECT id, role, is_active FROM users WHERE email = %s FOR UPDATE", (email,))
    existing = cur.fetchone()
    password_hash = generate_password_hash(password)
    if existing and (existing[2] or existing[1] != "faculty"):
        conn.rollback()
        return error("An account with this email already exists.", 409)
    try:
        if existing:  # a removed (deactivated) faculty member is added again: keep their history
            fid = existing[0]
            cur.execute("UPDATE users SET name = %s, password_hash = %s, is_active = TRUE WHERE id = %s",
                        (name, password_hash, fid))
        else:
            cur.execute("INSERT INTO users (name, email, password_hash, role) VALUES (%s, %s, %s, 'faculty')",
                        (name, email, password_hash))
            fid = cur.lastrowid
        cur.execute("INSERT INTO faculty_settings (faculty_id, profile_id) VALUES (%s, %s) "
                    "ON DUPLICATE KEY UPDATE profile_id = VALUES(profile_id)", (fid, profile_id))
        cur.executemany("INSERT IGNORE INTO consult_days (faculty_id, day_of_week) VALUES (%s, %s)",
                        [(fid, d) for d in DEFAULT_DAYS])
    except mysql.connector.IntegrityError as e:
        conn.rollback()
        if e.errno == 1062:  # another request linked the timetable or used the email at the same moment
            return error("That email or timetable was just taken. Refresh and try again.", 409)
        raise
    conn.commit()

    cur.execute(FACULTY_QUERY + " AND u.id = %s", (fid,))
    return make_response(jsonify(ok=True, faculty=faculty_json(cur.fetchone())), 201)


def active_faculty_or_404(cur, fid):
    cur.execute(FACULTY_QUERY + " AND u.id = %s FOR UPDATE", (fid,))
    row = cur.fetchone()
    if row is None:
        abort(error("Faculty account not found.", 404))
    return row


@bp.post("/admin/faculty/<int:fid>/link")
@login_required("admin")
def link_timetable(fid):
    profile_id = str(json_body().get("profile_id", "")).strip()
    conn = db.get_db()
    cur = conn.cursor()
    own_email = active_faculty_or_404(cur, fid)[2]
    cur.execute("SELECT 1 FROM timetable_profiles WHERE id = %s", (profile_id,))
    if cur.fetchone() is None:
        return error("Choose a timetable from the list.", 400)
    holder = profile_holder(cur, profile_id)
    if holder and holder != own_email:
        conn.rollback()
        return error(f"That timetable is already linked to {holder}.", 409)
    try:
        cur.execute("SELECT profile_id FROM faculty_settings WHERE faculty_id = %s", (fid,))
        if cur.fetchone()[0] != profile_id:
            # Another timetable has other free slots, so the old "not offered" marks no longer fit.
            cur.execute("DELETE FROM slots_off WHERE faculty_id = %s", (fid,))
        cur.execute("UPDATE faculty_settings SET profile_id = %s WHERE faculty_id = %s", (profile_id, fid))
    except mysql.connector.IntegrityError as e:
        conn.rollback()
        if e.errno == 1062:
            return error("That timetable was just linked to someone else. Refresh and try again.", 409)
        raise
    conn.commit()
    cur.execute(FACULTY_QUERY + " AND u.id = %s", (fid,))
    return jsonify(ok=True, faculty=faculty_json(cur.fetchone()))


@bp.post("/admin/faculty/<int:fid>/password")
@login_required("admin")
def reset_password(fid):
    password = str(json_body().get("password", ""))
    if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        return error(f"Use {MIN_PASSWORD} to {MAX_PASSWORD} characters.", 400)
    conn = db.get_db()
    cur = conn.cursor()
    active_faculty_or_404(cur, fid)
    cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (generate_password_hash(password), fid))
    conn.commit()
    return jsonify(ok=True)


@bp.post("/admin/faculty/<int:fid>/deactivate")
@login_required("admin")
def deactivate(fid):
    """Removes the faculty member from CampusSync but keeps the account row, so their past
    sessions and bookings still show who they were with. The timetable is freed for a new account.
    Their open bookings (sessions not yet over) are cancelled in the same transaction; the reply
    lists them so the page can tell those students."""
    json_body()
    conn = db.get_db()
    cur = conn.cursor()
    active_faculty_or_404(cur, fid)  # locks the faculty row, so no booking for them can start meanwhile
    cur.execute("SELECT b.id FROM bookings b JOIN sessions s ON s.id = b.session_id "
                "WHERE s.faculty_id = %s AND b.status IN ('Pending', 'Waiting', 'In Session') "
                "AND TIMESTAMP(s.session_date, s.end_time) > NOW() FOR UPDATE", (fid,))
    cancelled = [bid for (bid,) in cur.fetchall()]
    for bid in cancelled:
        cur.execute("UPDATE bookings SET status = 'Cancelled' WHERE id = %s", (bid,))
        cur.execute("DELETE FROM reschedule_requests WHERE booking_id = %s", (bid,))
        cur.execute("INSERT INTO booking_history (booking_id, text) VALUES (%s, 'Cancelled: faculty account removed by admin')", (bid,))
    cur.execute("UPDATE users SET is_active = FALSE WHERE id = %s", (fid,))
    cur.execute("UPDATE faculty_settings SET profile_id = NULL WHERE faculty_id = %s", (fid,))
    conn.commit()
    return jsonify(ok=True, cancelled=[str(bid) for bid in cancelled])


def day_and_time(data, *keys):
    """Read and check a day (1-6) and one or more HH:MM times from the request body."""
    try:
        day = int(data.get("day"))
    except (TypeError, ValueError):
        day = 0
    times = [str(data.get(k, "")) for k in keys]
    if not 1 <= day <= 6:
        abort(error("Choose a day from Monday to Saturday.", 400))
    if not all(TIME.fullmatch(t) for t in times):
        abort(error("Enter times as HH:MM.", 400))
    return day, *times


@bp.post("/admin/faculty/<int:fid>/settings")
@login_required("admin")
def save_settings(fid):
    data = json_body()
    try:
        slot_len, capacity = int(data.get("slot_len")), int(data.get("capacity"))
        days = sorted({int(d) for d in data.get("days", [])})
    except (TypeError, ValueError):
        return error("Send the slot length, students per slot and days as numbers.", 400)
    if slot_len not in SLOT_LENGTHS:
        return error("Choose a slot length from the list.", 400)
    if not 1 <= capacity <= 50:
        return error("Students per slot must be between 1 and 50.", 400)
    if not days or not all(1 <= d <= 6 for d in days):
        return error("Choose at least one consultation day.", 400)
    conn = db.get_db()
    cur = conn.cursor()
    active_faculty_or_404(cur, fid)
    cur.execute("SELECT slot_len FROM faculty_settings WHERE faculty_id = %s", (fid,))
    if cur.fetchone()[0] != slot_len:
        # The weekly slots start at different times now, so the old "not offered" marks no longer fit.
        cur.execute("DELETE FROM slots_off WHERE faculty_id = %s", (fid,))
    cur.execute("UPDATE faculty_settings SET slot_len = %s, capacity = %s WHERE faculty_id = %s", (slot_len, capacity, fid))
    cur.execute("DELETE FROM consult_days WHERE faculty_id = %s", (fid,))
    cur.executemany("INSERT INTO consult_days (faculty_id, day_of_week) VALUES (%s, %s)", [(fid, d) for d in days])
    conn.commit()
    return jsonify(ok=True)


@bp.post("/admin/faculty/<int:fid>/slots-off")
@login_required("admin")
def toggle_slot(fid):
    data = json_body()
    day, start = day_and_time(data, "start")
    conn = db.get_db()
    cur = conn.cursor()
    active_faculty_or_404(cur, fid)
    if data.get("off"):
        cur.execute("INSERT IGNORE INTO slots_off (faculty_id, day_of_week, start_time) VALUES (%s, %s, %s)", (fid, day, start))
    else:
        cur.execute("DELETE FROM slots_off WHERE faculty_id = %s AND day_of_week = %s AND start_time = %s", (fid, day, start))
    conn.commit()
    return jsonify(ok=True)


@bp.post("/admin/faculty/<int:fid>/slots-off/clear")
@login_required("admin")
def offer_all(fid):
    json_body()
    conn = db.get_db()
    cur = conn.cursor()
    active_faculty_or_404(cur, fid)
    cur.execute("DELETE FROM slots_off WHERE faculty_id = %s", (fid,))
    conn.commit()
    return jsonify(ok=True)


@bp.post("/admin/faculty/<int:fid>/custom-slots")
@login_required("admin")
def add_custom_slot(fid):
    data = json_body()
    day, start, end = day_and_time(data, "start", "end")
    try:
        capacity = int(data.get("capacity"))
    except (TypeError, ValueError):
        capacity = 0
    if end <= start:
        return error("End time must be after start time.", 400)
    if not 1 <= capacity <= 50:
        return error("Students per slot must be between 1 and 50.", 400)
    conn = db.get_db()
    cur = conn.cursor()
    active_faculty_or_404(cur, fid)
    cur.execute("INSERT INTO custom_slots (faculty_id, day_of_week, start_time, end_time, capacity) VALUES (%s, %s, %s, %s, %s)",
                (fid, day, start, end, capacity))
    conn.commit()
    return make_response(jsonify(ok=True, id=str(cur.lastrowid)), 201)


@bp.post("/admin/custom-slots/<int:cid>/delete")
@login_required("admin")
def delete_custom_slot(cid):
    json_body()
    conn = db.get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM custom_slots WHERE id = %s", (cid,))
    if cur.rowcount == 0:
        conn.rollback()
        return error("Extra slot not found.", 404)
    conn.commit()
    return jsonify(ok=True)


def week_data(cur, fid):
    """A faculty member's row, settings, classes and weekly slots (404 if not an active faculty member)."""
    cur.execute(FACULTY_QUERY + " AND u.id = %s", (fid,))
    row = cur.fetchone()
    if row is None:
        abort(error("Faculty account not found.", 404))
    settings = all_settings(cur)[fid]
    cur.execute("SELECT day_of_week, TIME_FORMAT(start_time, '%H:%i'), TIME_FORMAT(end_time, '%H:%i'), code, subject "
                "FROM timetable_classes WHERE profile_id = %s ORDER BY day_of_week, start_time, id", (row[3],))
    classes = [{"day": d, "start": s, "end": e, "code": code, "subject": subject} for d, s, e, code, subject in cur.fetchall()]
    return row, settings, classes, weekly_slots(settings, classes)


def sessions_of(cur, fid):
    """The faculty member's sessions with the number of bookings holding a seat (not Rejected or Cancelled)."""
    cur.execute("""
        SELECT s.id, DATE_FORMAT(s.session_date, '%Y-%m-%d'), TIME_FORMAT(s.start_time, '%H:%i'),
               TIME_FORMAT(s.end_time, '%H:%i'), s.capacity, s.source, s.cancelled, s.topic,
               (SELECT COUNT(*) FROM bookings b WHERE b.session_id = s.id AND b.status NOT IN ('Rejected', 'Cancelled'))
        FROM sessions s WHERE s.faculty_id = %s""", (fid,))
    keys = ("id", "date", "start", "end", "capacity", "source", "cancelled", "topic", "booked")
    return [dict(zip(keys, r)) for r in cur.fetchall()]


@bp.get("/faculty/<int:fid>/week")
@login_required()
def week(fid):
    """A faculty member's classes and the weekly consultation slots worked out from them."""
    row, settings, classes, weekly = week_data(db.get_db().cursor(), fid)
    return jsonify(ok=True, faculty=faculty_json(row), settings=settings, classes=classes, slots=weekly)


@bp.get("/faculty/<int:fid>/slots")
@login_required()
def bookable_slots(fid):
    """What a student can book with this faculty member in the next ?days= days (default 14).
    Each slot has a ref: "v:<faculty id>|<date>|<start>" for a free timetable slot, or "s:<session id>"
    for a session that already has bookings."""
    days = request.args.get("days", "14")
    if not days.isdigit() or not 1 <= int(days) <= 60:
        return error("days must be a number from 1 to 60.", 400)
    cur = db.get_db().cursor()
    weekly = week_data(cur, fid)[3]
    slots = upcoming_slots(weekly, sessions_of(cur, fid), datetime.now(), int(days))
    for slot in slots:
        slot["ref"] = f"s:{slot['session_id']}" if slot["session_id"] else f"v:{fid}|{slot['date']}|{slot['start']}"
    return jsonify(ok=True, slots=slots)
