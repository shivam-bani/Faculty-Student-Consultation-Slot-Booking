"""Create the CampusSync database and the app's MySQL user, and write .env.

Run from the project folder with the venv active:
    python tools/setup_db.py
It asks for the MySQL root password once (hidden) and never prints any password.

1. Runs schema.sql if the campussync database has no tables yet.
2. Checks the result: table and constraint counts, and three invalid inserts that must be
   rejected (the test rows are rolled back, so nothing is left behind).
3. Writes the settings that are missing from .env (random DB password and SECRET_KEY), then
   creates the MySQL user campussync_app with data-only rights (SELECT, INSERT, UPDATE, DELETE)
   on campussync.* and gives it the password from .env.
4. Logs in as campussync_app with the .env settings, and checks it cannot create tables.
Safe to run again: existing tables and .env values are kept.
"""
import getpass
import re
import secrets
import sys
from pathlib import Path

import mysql.connector
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "schema.sql"
ENV = ROOT / ".env"
DB_NAME = "campussync"
EXPECTED_TABLES = 13
EXPECTED_CONSTRAINTS = {"PRIMARY KEY": 13, "FOREIGN KEY": 13, "UNIQUE": 8, "CHECK": 21}

# Invalid inserts that the constraints must reject: (what, SQL, MySQL error number, constraint).
REJECT_TESTS = [
    ("class ending before it starts",
     "INSERT INTO timetable_classes (profile_id, day_of_week, start_time, end_time, code) "
     "VALUES ('setup-test', 1, '10:00', '09:00', 'TEST')", 3819, "chk_classes_time"),
    ("student without an @geu.ac.in email",
     "INSERT INTO users (name, email, password_hash, role) "
     "VALUES ('Test', 'test@gmail.com', 'x', 'student')", 3819, "chk_users_student_email"),
    ("class for a timetable that does not exist",
     "INSERT INTO timetable_classes (profile_id, day_of_week, start_time, end_time, code) "
     "VALUES ('no-such-profile', 1, '09:00', '10:00', 'TEST')", 1452, "fk_classes_profile"),
]


def statements(sql):
    """Split schema.sql into single statements (it has no ';' or '--' inside strings)."""
    sql = re.sub(r"--[^\n]*", "", sql)
    return [s.strip() for s in sql.split(";") if s.strip()]


def env_settings(path=ENV):
    """Add any missing settings to .env (existing values are kept) and return all of them."""
    current = dotenv_values(path) if path.exists() else {}
    defaults = {
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "3306",
        "DB_NAME": DB_NAME,
        "DB_USER": "campussync_app",
        "DB_PASSWORD": secrets.token_urlsafe(24) + "-Aa9",  # also meets strict MySQL password policies
        "SECRET_KEY": secrets.token_hex(32),
        "AGING_MINUTES": "10",
        "CHECKIN_EARLY_MINUTES": "10",
    }
    missing = {k: v for k, v in defaults.items() if not current.get(k)}
    if missing:
        text = path.read_text(encoding="utf-8") if path.exists() else (
            "# CampusSync settings. Secret: never commit or share this file.\n")
        if text and not text.endswith("\n"):
            text += "\n"
        text += "".join(f"{k}='{v}'\n" for k, v in missing.items())
        path.write_text(text, encoding="utf-8")
    return {**current, **missing}


def check(ok, text):
    print(("  OK    " if ok else "  FAIL  ") + text)
    return ok


def main():
    if not SCHEMA.exists():
        sys.exit("schema.sql not found. Run this from the project folder: python tools/setup_db.py")
    try:
        root = mysql.connector.connect(host="127.0.0.1", port=3306, user="root",
                                       password=getpass.getpass("MySQL root password (hidden): "))
    except mysql.connector.Error as e:
        sys.exit(f"Could not log in as root: {e.msg}")
    cur = root.cursor()
    ok = True

    print("1. Database")
    cur.execute("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s", (DB_NAME,))
    existing = cur.fetchone()[0]
    if existing == 0:
        for stmt in statements(SCHEMA.read_text(encoding="utf-8")):
            try:
                cur.execute(stmt)
            except mysql.connector.Error as e:
                sys.exit(f"  schema.sql failed at:\n{stmt[:200]}\n  -> {e.msg}\n"
                         f"  Fix it, then in MySQL run: DROP DATABASE {DB_NAME};  and run this again.")
            if cur.with_rows:
                cur.fetchall()
        print("  created from schema.sql")
    elif existing != EXPECTED_TABLES:
        sys.exit(f"  {DB_NAME} has {existing} tables, expected {EXPECTED_TABLES}. "
                 f"In MySQL run: DROP DATABASE {DB_NAME};  then run this again.")
    else:
        print("  already exists (schema.sql not run again)")
    cur.execute("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s", (DB_NAME,))
    ok &= check(cur.fetchone()[0] == EXPECTED_TABLES, f"{EXPECTED_TABLES} tables")
    cur.execute("SELECT CONSTRAINT_TYPE, COUNT(*) FROM information_schema.TABLE_CONSTRAINTS "
                "WHERE CONSTRAINT_SCHEMA = %s GROUP BY CONSTRAINT_TYPE", (DB_NAME,))
    counts = dict(cur.fetchall())
    for kind, n in EXPECTED_CONSTRAINTS.items():
        ok &= check(counts.get(kind) == n, f"{kind}: {counts.get(kind, 0)} (expected {n})")

    print("2. Invalid data is rejected (test rows are rolled back)")
    cur.execute(f"USE {DB_NAME}")
    cur.execute("INSERT INTO timetable_profiles (id, name, source) VALUES ('setup-test', 'Setup test', 'admin')")
    for what, sql, errno, name in REJECT_TESTS:
        try:
            cur.execute(sql)
            ok &= check(False, f"{what}: was ACCEPTED")
        except mysql.connector.Error as e:
            ok &= check(e.errno == errno and name in e.msg, f"{what}: rejected ({e.msg})")
    root.rollback()

    print("3. App user and .env")
    env = env_settings()
    user = env["DB_USER"]
    cur.execute("CREATE USER IF NOT EXISTS %s@'localhost' IDENTIFIED BY %s", (user, env["DB_PASSWORD"]))
    cur.execute("ALTER USER %s@'localhost' IDENTIFIED BY %s", (user, env["DB_PASSWORD"]))
    cur.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {DB_NAME}.* TO %s@'localhost'", (user,))
    print(f"  user {user}@localhost has data-only rights on {DB_NAME}.*; settings are in .env")
    root.close()

    print("4. Logging in as the app user with the .env settings")
    try:
        app = mysql.connector.connect(host=env["DB_HOST"], port=int(env["DB_PORT"]), user=user,
                                      password=env["DB_PASSWORD"], database=env["DB_NAME"])
    except mysql.connector.Error as e:
        sys.exit(f"  FAIL  could not log in as {user}: {e.msg}")
    c = app.cursor()
    c.execute("SELECT CURRENT_USER(), COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()")
    who, n = c.fetchone()
    ok &= check(n == EXPECTED_TABLES, f"logged in as {who}, sees {n} tables")
    try:
        c.execute("CREATE TABLE setup_probe (x INT)")
        c.execute("DROP TABLE setup_probe")
        ok &= check(False, "app user can CREATE tables (it should not)")
    except mysql.connector.Error as e:
        ok &= check(e.errno == 1142, f"app user cannot change the schema ({e.msg})")
    app.close()

    print("\nAll checks passed." if ok else "\nSome checks FAILED (see above).")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
