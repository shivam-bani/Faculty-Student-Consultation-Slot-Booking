"""Queue manager: the operating-systems part of CampusSync, a priority scheduler with aging.

A consultation slot is served one student at a time, like a single CPU shared by processes:
    - "I'm here" (check-in) puts an accepted booking in the ready queue. Waiting starts here,
      not at booking time, so nobody gains by booking early and arriving late.
    - Priority scheduling: final-year (semester VII/VIII) "Major project" bookings have base
      priority 2, all others 1. The server sets it at booking time (bookings.base_priority).
    - Aging prevents starvation: every completed AGING_MINUTES of waiting adds 1, so
          effective priority = base + FLOOR(seconds waited / (AGING_MINUTES * 60)).
      It is worked out by MySQL whenever it is needed and never stored, so it is always current.
    - Dispatch ("Call next"): the highest effective priority goes first; ties go to the earliest
      check-in (first come, first served), then the lowest token.
    - The whole dispatch is one transaction holding the faculty and session row locks (a mutex),
      and UNIQUE(session_id, in_session_flag) allows at most one student "In Session" per slot.

    POST /api/bookings/<id>/check-in                 a student says "I'm here"
    POST /api/faculty/sessions/<id>/call-next        the faculty member finishes the current student
                                                     (if any) and calls the next one in
    GET  /api/queue                                  queue positions and settings for the pages
"""
from datetime import datetime, timedelta

from flask import Blueprint, abort, current_app, jsonify

import db
from auth import current_user, error, json_body, login_required
from bookings import MONTHS, booking_info, lock_faculty, log, own_active_booking, visible
from notifications import notify

bp = Blueprint("queue", __name__, url_prefix="/api")

# SQL pieces with named parameters: %(aging)s is AGING_MINUTES from the settings, never user input.
EFFECTIVE = "(b.base_priority + FLOOR(TIMESTAMPDIFF(SECOND, b.checked_in_at, NOW()) / (%(aging)s * 60)))"
QUEUE_ORDER = f"{EFFECTIVE} DESC, b.checked_in_at, b.token"
READY = "b.status = 'Waiting' AND b.checked_in_at IS NOT NULL"  # checked in and not called yet


def aging():
    return current_app.config["AGING_MINUTES"]


def opening_hours(date, start, end):
    """When a slot's queue is open: from CHECKIN_EARLY_MINUTES before the start until the end."""
    opens = datetime.strptime(f"{date} {start}", "%Y-%m-%d %H:%M") - timedelta(minutes=current_app.config["CHECKIN_EARLY_MINUTES"])
    return opens, datetime.strptime(f"{date} {end}", "%Y-%m-%d %H:%M")


def positions(cur, session_ids):
    """{booking id: (place in the queue, effective priority)} for the ready bookings of these
    sessions, in exactly the order Call next uses."""
    if not session_ids:
        return {}
    params = {"aging": aging(), **{f"s{i}": sid for i, sid in enumerate(session_ids)}}
    in_list = ", ".join(f"%(s{i})s" for i in range(len(session_ids)))
    cur.execute(f"SELECT b.id, ROW_NUMBER() OVER (PARTITION BY b.session_id ORDER BY {QUEUE_ORDER}), {EFFECTIVE} "
                f"FROM bookings b WHERE {READY} AND b.session_id IN ({in_list})", params)
    return {bid: (int(place), int(eff)) for bid, place, eff in cur.fetchall()}


@bp.get("/queue")
@login_required()
def queue():
    """Places and effective priorities for the bookings this user may see. A student's place is
    counted among everyone in their slot, but only their own bookings are listed."""
    booking_where, _, params = visible(current_user())
    cur = db.get_db().cursor()
    cur.execute(f"SELECT b.id, b.session_id FROM bookings b JOIN sessions s ON s.id = b.session_id "
                f"WHERE {booking_where} AND b.status IN ('Waiting', 'In Session') AND s.session_date >= CURDATE()", params)
    rows = cur.fetchall()
    places = positions(cur, sorted({sid for _, sid in rows}))
    entries = [{"id": str(bid), "position": places[bid][0], "effectivePriority": places[bid][1]}
               for bid, _ in rows if bid in places]
    return jsonify(ok=True, agingMinutes=aging(), checkinEarlyMinutes=current_app.config["CHECKIN_EARLY_MINUTES"],
                   entries=entries)


@bp.post("/bookings/<int:bid>/check-in")
@login_required("student")
def check_in(bid):
    json_body()
    conn = db.get_db()
    cur = conn.cursor()
    own_active_booking(cur, bid)  # locks student, faculty, then the booking; 404 / 409 if not allowed
    cur.execute("SELECT b.status, b.checked_in_at, b.session_id, DATE_FORMAT(s.session_date, '%Y-%m-%d'), "
                "TIME_FORMAT(s.start_time, '%H:%i'), TIME_FORMAT(s.end_time, '%H:%i') "
                "FROM bookings b JOIN sessions s ON s.id = b.session_id WHERE b.id = %s", (bid,))
    status, checked_in, sid, date, start, end = cur.fetchone()
    if status != "Waiting":
        return error("You can check in once the faculty member has accepted your request.", 409)
    if checked_in is not None:
        return error("You have already checked in.", 409)
    opens, closes = opening_hours(date, start, end)
    if datetime.now() < opens:
        return error(f"Check-in opens at {opens:%H:%M} on {opens.day} {MONTHS[opens.month - 1]}, "
                     f"{current_app.config['CHECKIN_EARLY_MINUTES']} minutes before your slot.", 409)
    cur.execute("UPDATE bookings SET checked_in_at = NOW() WHERE id = %s", (bid,))
    log(cur, bid, "Checked in (I'm here)")
    conn.commit()
    place, eff = positions(cur, [sid])[bid]
    return jsonify(ok=True, position=place, effectivePriority=eff)


def student_json(row):
    return {"id": str(row[0]), "token": row[1], "name": row[2], "email": row[3]} if row else None


@bp.post("/faculty/sessions/<int:sid>/call-next")
@login_required("faculty")
def call_next(sid):
    """Body (optional): {"current": "<booking id the page shows In Session>" or null}. If the queue
    changed since the page was drawn (e.g. a double click already called someone), nothing is
    done, so a student is never finished without being seen."""
    data = json_body()
    me = current_user()["id"]
    conn = db.get_db()
    cur = conn.cursor()
    lock_faculty(cur, me)
    cur.execute("SELECT DATE_FORMAT(session_date, '%Y-%m-%d'), TIME_FORMAT(start_time, '%H:%i'), TIME_FORMAT(end_time, '%H:%i'), "
                "cancelled FROM sessions WHERE id = %s AND faculty_id = %s FOR UPDATE", (sid, me))
    row = cur.fetchone()
    if row is None:
        return error("Session not found.", 404)
    date, start, end, cancelled = row
    opens, closes = opening_hours(date, start, end)
    now = datetime.now()
    if cancelled:
        return error("This session was cancelled.", 409)
    if now < opens:
        return error(f"The queue opens at {opens:%H:%M}.", 409)
    if now >= closes:
        return error("This session has ended.", 409)

    cur.execute("SELECT b.id, b.token, u.name, u.email FROM bookings b JOIN users u ON u.id = b.student_id "
                "WHERE b.session_id = %s AND b.status = 'In Session' FOR UPDATE", (sid,))
    current = cur.fetchone()
    if "current" in data and str(data["current"] or "") != (str(current[0]) if current else ""):
        return error("The queue has changed since the page was loaded. Check it and try again.", 409)
    if current:
        cur.execute("UPDATE bookings SET status = 'Completed', completed_at = NOW() WHERE id = %s", (current[0],))
        log(cur, current[0], "Consultation completed")

    cur.execute(f"SELECT b.id, b.token, u.name, u.email, {EFFECTIVE} FROM bookings b JOIN users u ON u.id = b.student_id "
                f"WHERE b.session_id = %(sid)s AND {READY} ORDER BY {QUEUE_ORDER} LIMIT 1 FOR UPDATE",
                {"sid": sid, "aging": aging()})
    nxt = cur.fetchone()
    if nxt:
        cur.execute("UPDATE bookings SET status = 'In Session', called_at = NOW() WHERE id = %s", (nxt[0],))
        log(cur, nxt[0], f"Called in by faculty (priority {int(nxt[4])})")
        info = booking_info(cur, nxt[0])
        notify(cur, info["student_id"], "It's your turn",
               f"{info['faculty']} is ready for you now ({info['when']}, token #{info['token']}).")
    conn.commit()
    return jsonify(ok=True, completed=student_json(current), called=student_json(nxt[:4] if nxt else None),
                   priority=int(nxt[4]) if nxt else None)
