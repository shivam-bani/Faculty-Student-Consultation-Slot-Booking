"""Accounts and login for CampusSync: the JSON API under /api/auth.

After login the browser holds a session cookie containing only the user's id. Flask signs the
cookie with SECRET_KEY, so it cannot be changed in the browser, and the user (with their role) is
loaded from MySQL on every request, so the role is never taken from the browser.
"""
from functools import wraps

from flask import Blueprint, abort, g, jsonify, make_response, request, session
from werkzeug.security import check_password_hash, generate_password_hash

import db

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

LOGIN_FAILED = "Email or password is incorrect."
# Checked when the email is unknown, so a wrong email takes as long as a wrong password and the
# response time does not reveal which emails have accounts.
DUMMY_HASH = generate_password_hash("not-a-real-password")


def error(message, status):
    return make_response(jsonify(ok=False, error=message), status)


def json_body():
    """The request's JSON object. Requiring JSON also stops plain HTML forms on other sites
    from submitting to the API with the user's cookie."""
    data = request.get_json(silent=True)
    if not request.is_json or not isinstance(data, dict):
        abort(error("Send the data as JSON.", 400))
    return data


def public_user(user_id, name, email, role):
    return {"id": user_id, "name": name, "email": email, "role": role}


def current_user():
    """The logged-in user (loaded from MySQL once per request), or None."""
    if "user" not in g:
        g.user = None
        user_id = session.get("user_id")
        if user_id is not None:
            cur = db.get_db().cursor()
            cur.execute("SELECT id, name, email, role FROM users WHERE id = %s AND is_active", (user_id,))
            row = cur.fetchone()
            if row:
                g.user = public_user(*row)
            else:
                session.clear()  # account removed or deactivated since login
    return g.user


def login_required(*roles):
    """Route decorator. Not logged in: 401. Logged in with a role not in `roles`: 403.
    Use @login_required() for any logged-in user, @login_required("admin") for admins only."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if user is None:
                return error("Please log in.", 401)
            if roles and user["role"] not in roles:
                return error("You do not have access to this.", 403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


@bp.post("/login")
def login():
    data = json_body()
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))
    wanted_role = data.get("role")  # which login button was used; only for a helpful message

    cur = db.get_db().cursor()
    cur.execute("SELECT id, name, email, role, password_hash, is_active FROM users WHERE email = %s", (email,))
    row = cur.fetchone()
    password_ok = check_password_hash(row[4] if row else DUMMY_HASH, password)
    if row is None or not password_ok:
        return error(LOGIN_FAILED, 401)
    user_id, name, email, role, _, is_active = row
    if not is_active:
        return error("This account has been deactivated. Please contact the admin.", 403)
    if wanted_role and wanted_role != role:
        return error(f'This email belongs to a {role} account. Use the "Login as {role.capitalize()}" button.', 403)

    session.clear()  # start a fresh session on every login
    session["user_id"] = user_id
    return jsonify(ok=True, user=public_user(user_id, name, email, role))


@bp.post("/logout")
def logout():
    json_body()
    session.clear()
    return jsonify(ok=True)


@bp.get("/me")
@login_required()
def me():
    return jsonify(ok=True, user=current_user())
