"""Faculty accounts: the list every logged-in user needs, and the admin's management API.

    GET  /api/faculty                          active faculty with their linked timetable
    POST /api/admin/faculty                    create (or re-activate) a faculty account
    POST /api/admin/faculty/<id>/link          link the account to another timetable
    POST /api/admin/faculty/<id>/password      set a new temporary password
    POST /api/admin/faculty/<id>/deactivate    remove from CampusSync (history is kept)
"""
import re

import mysql.connector
from flask import Blueprint, abort, jsonify, make_response
from werkzeug.security import generate_password_hash

import db
from auth import MAX_PASSWORD, MIN_PASSWORD, error, json_body, login_required

bp = Blueprint("faculty", __name__, url_prefix="/api")

EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
PROFILE_ID = re.compile(r"[a-z0-9-]{1,80}")
DEFAULT_DAYS = (1, 2, 3, 4, 5)  # Monday to Friday, as in the old browser-only version

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


@bp.get("/faculty")
@login_required()
def list_faculty():
    cur = db.get_db().cursor()
    cur.execute(FACULTY_QUERY + " ORDER BY u.name")
    return jsonify(ok=True, faculty=[faculty_json(r) for r in cur.fetchall()])


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
    sessions and bookings still show who they were with. The timetable is freed for a new account."""
    json_body()
    conn = db.get_db()
    cur = conn.cursor()
    active_faculty_or_404(cur, fid)
    cur.execute("UPDATE users SET is_active = FALSE WHERE id = %s", (fid,))
    cur.execute("UPDATE faculty_settings SET profile_id = NULL WHERE faculty_id = %s", (fid,))
    conn.commit()
    return jsonify(ok=True)
