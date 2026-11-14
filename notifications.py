"""Notifications: short messages for one user, stored in MySQL.

Each one is written by the action it describes, in the same transaction (notify() gets that
action's cursor). So a notification exists exactly when its action was saved: if the action is
rolled back, its notification is rolled back too. Every user sees only their own.

    GET  /api/notifications         the logged-in user's notifications, newest first, and the unread count
    POST /api/notifications/read    mark them all as read (done when the Notifications page is opened)
"""
from flask import Blueprint, jsonify

import db
from auth import current_user, json_body, login_required
from slots import overlaps

bp = Blueprint("notifications", __name__, url_prefix="/api")

LIMIT = 200  # newest notifications sent to the page


def notify(cur, user_id, title, message):
    cur.execute("INSERT INTO notifications (user_id, title, message) VALUES (%s, %s, %s)",
                (user_id, title[:100], message[:1000]))


def clashing_bookings(cur, fid):
    """How many of the faculty member's open bookings now overlap one of their classes
    (after the admin changed the timetable). Same rule as clashingBookings() on the page."""
    cur.execute("SELECT day_of_week, TIME_FORMAT(c.start_time, '%H:%i'), TIME_FORMAT(c.end_time, '%H:%i') "
                "FROM timetable_classes c JOIN faculty_settings f ON f.profile_id = c.profile_id WHERE f.faculty_id = %s", (fid,))
    classes = cur.fetchall()
    cur.execute("SELECT s.session_date, TIME_FORMAT(s.start_time, '%H:%i'), TIME_FORMAT(s.end_time, '%H:%i') "
                "FROM bookings b JOIN sessions s ON s.id = b.session_id "
                "WHERE s.faculty_id = %s AND NOT s.cancelled AND b.status IN ('Pending', 'Waiting', 'In Session') "
                "AND TIMESTAMP(s.session_date, s.end_time) > NOW()", (fid,))
    return sum(1 for session_date, start, end in cur.fetchall()
               if any(day == session_date.isoweekday() and overlaps(cs, ce, start, end) for day, cs, ce in classes))


def timetable_changed(cur, fid, what):
    """Tell a faculty member that the admin changed their timetable, and about any clashes."""
    clashes = clashing_bookings(cur, fid)
    notify(cur, fid, "Timetable updated", f"The admin {what}." + (
        f" {clashes} booked meeting(s) now clash with a class. Reschedule them under Booking Requests." if clashes else ""))
    return clashes


@bp.get("/notifications")
@login_required()
def list_notifications():
    cur = db.get_db().cursor()
    me = current_user()["id"]
    cur.execute("SELECT id, title, message, UNIX_TIMESTAMP(created_at) * 1000, is_read FROM notifications "
                "WHERE user_id = %s ORDER BY id DESC LIMIT %s", (me, LIMIT))
    rows = [{"id": str(i), "title": t, "message": m, "at": int(at), "read": bool(r)} for i, t, m, at, r in cur.fetchall()]
    cur.execute("SELECT COUNT(*) FROM notifications WHERE user_id = %s AND NOT is_read", (me,))
    return jsonify(ok=True, notifications=rows, unread=cur.fetchone()[0])


@bp.post("/notifications/read")
@login_required()
def mark_read():
    json_body()
    conn = db.get_db()
    cur = conn.cursor()
    cur.execute("UPDATE notifications SET is_read = TRUE WHERE user_id = %s AND NOT is_read", (current_user()["id"],))
    conn.commit()
    return jsonify(ok=True)
