"""Timetables: every faculty member's classes and busy periods, kept in MySQL.

    GET  /api/timetables                          all timetables with their classes
    POST /api/admin/timetables/<pid>/classes      add a class or busy period
    POST /api/admin/classes/<cid>                 change a class
    POST /api/admin/classes/<cid>/delete          remove a class
    POST /api/admin/timetables/<pid>/restore      go back to the university timetable file
Any change by the admin marks the timetable as edited, so import-timetable keeps it.
"""
import re

from flask import Blueprint, abort, jsonify, make_response

import commands
import db
from auth import error, json_body, login_required
from notifications import timetable_changed
from slots import DAY_NAMES

bp = Blueprint("timetable", __name__, url_prefix="/api")

TIME = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
CLASS_COLUMNS = ("SELECT id, profile_id, day_of_week, TIME_FORMAT(start_time, '%H:%i'), TIME_FORMAT(end_time, '%H:%i'), "
                 "code, subject, section, room FROM timetable_classes")


def class_json(row):
    cid, _, day, start, end, code, subject, section, room = row
    # The id is sent as text because the page compares it with HTML data-attributes.
    return {"id": str(cid), "day": day, "start": start, "end": end, "code": code,
            "subject": subject, "section": section, "room": room}


def class_from_request():
    """Validate a class sent by the admin. Returns the values for the INSERT/UPDATE."""
    data = json_body()
    try:
        day = int(data.get("day"))
    except (TypeError, ValueError):
        day = 0
    start, end = str(data.get("start", "")), str(data.get("end", ""))
    code = str(data.get("code", "")).strip().upper()
    if code == "BUSY":
        code = "Busy"  # the pages show busy periods by this exact spelling
    subject, section, room = (str(data.get(k, "")).strip() for k in ("subject", "section", "room"))
    if not 1 <= day <= 6:
        abort(error("Choose a day from Monday to Saturday.", 400))
    if not (TIME.fullmatch(start) and TIME.fullmatch(end)) or end <= start:
        abort(error("End time must be after start time.", 400))
    if not 1 <= len(code) <= 20:
        abort(error("Enter the subject code.", 400))
    if len(subject) > 150 or len(section) > 255 or len(room) > 100:
        abort(error("Subject, section or room is too long.", 400))
    return day, start, end, code, subject, section, room


def profile_or_404(cur, pid):
    cur.execute("SELECT id FROM timetable_profiles WHERE id = %s FOR UPDATE", (pid,))
    if cur.fetchone() is None:
        abort(error("Timetable not found.", 404))


def class_or_404(cur, cid):
    cur.execute(CLASS_COLUMNS + " WHERE id = %s FOR UPDATE", (cid,))
    row = cur.fetchone()
    if row is None:
        abort(error("Class not found.", 404))
    return row


def mark_edited(cur, pid):
    cur.execute("UPDATE timetable_profiles SET edited = TRUE WHERE id = %s", (pid,))


def tell_faculty(cur, pid, what):
    """Notify the active faculty member who uses this timetable (if any) about the change."""
    cur.execute("SELECT f.faculty_id FROM faculty_settings f JOIN users u ON u.id = f.faculty_id "
                "WHERE f.profile_id = %s AND u.is_active", (pid,))
    row = cur.fetchone()
    if row:
        timetable_changed(cur, row[0], what)


def when(day, start, end):
    return f"{DAY_NAMES[day - 1]}, {start} - {end}"


@bp.get("/timetables")
@login_required()
def list_timetables():
    cur = db.get_db().cursor()
    cur.execute("SELECT id, name, source, edited FROM timetable_profiles ORDER BY id")
    profiles = {pid: {"id": pid, "name": name, "source": source, "edited": bool(edited), "classes": []}
                for pid, name, source, edited in cur.fetchall()}
    cur.execute(CLASS_COLUMNS + " ORDER BY profile_id, day_of_week, start_time, id")
    for row in cur.fetchall():
        profiles[row[1]]["classes"].append(class_json(row))
    return jsonify(ok=True, timetables=list(profiles.values()))


@bp.post("/admin/timetables/<pid>/classes")
@login_required("admin")
def add_class(pid):
    values = class_from_request()
    conn = db.get_db()
    cur = conn.cursor()
    profile_or_404(cur, pid)
    cur.execute("INSERT INTO timetable_classes (profile_id, day_of_week, start_time, end_time, code, subject, section, room) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", (pid, *values))
    cid = cur.lastrowid
    mark_edited(cur, pid)
    day, start, end, code = values[:4]
    tell_faculty(cur, pid, f"added {'a busy period' if code == 'Busy' else 'a class'} on {when(day, start, end)}")
    conn.commit()
    return make_response(jsonify(ok=True, **{"class": class_json(class_or_404(cur, cid))}), 201)


@bp.post("/admin/classes/<int:cid>")
@login_required("admin")
def update_class(cid):
    values = class_from_request()
    conn = db.get_db()
    cur = conn.cursor()
    _, pid, old_day, old_start, _, old_code = class_or_404(cur, cid)[:6]
    cur.execute("UPDATE timetable_classes SET day_of_week = %s, start_time = %s, end_time = %s, code = %s, "
                "subject = %s, section = %s, room = %s WHERE id = %s", (*values, cid))
    mark_edited(cur, pid)
    day, start, end = values[:3]
    tell_faculty(cur, pid, f"changed {old_code} ({DAY_NAMES[old_day - 1][:3]} {old_start}) to {DAY_NAMES[day - 1]} {start} - {end}")
    conn.commit()
    return jsonify(ok=True, **{"class": class_json(class_or_404(cur, cid))})


@bp.post("/admin/classes/<int:cid>/delete")
@login_required("admin")
def delete_class(cid):
    json_body()
    conn = db.get_db()
    cur = conn.cursor()
    _, pid, day, start, end, code = class_or_404(cur, cid)[:6]
    cur.execute("DELETE FROM timetable_classes WHERE id = %s", (cid,))
    mark_edited(cur, pid)
    tell_faculty(cur, pid, f"removed {code} on {when(day, start, end)}. That time is now open for consultations")
    conn.commit()
    return jsonify(ok=True)


@bp.post("/admin/timetables/<pid>/restore")
@login_required("admin")
def restore(pid):
    """Replace the admin's edits with this person's classes from timetable-data.js."""
    json_body()
    original = next((f for f in commands.load_timetable()["faculty"] if f["id"] == pid), None)
    if original is None:
        return error("This timetable is not in the university timetable file.", 400)
    conn = db.get_db()
    cur = conn.cursor()
    profile_or_404(cur, pid)
    cur.execute("DELETE FROM timetable_classes WHERE profile_id = %s", (pid,))
    cur.executemany("INSERT INTO timetable_classes (profile_id, day_of_week, start_time, end_time, code, subject, section, room) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", [(pid, *c[:6], c[6] or "") for c in original["classes"]])
    cur.execute("UPDATE timetable_profiles SET name = %s, edited = FALSE WHERE id = %s", (original["name"], pid))
    tell_faculty(cur, pid, "restored your timetable to the official university timetable")
    conn.commit()
    return jsonify(ok=True)
