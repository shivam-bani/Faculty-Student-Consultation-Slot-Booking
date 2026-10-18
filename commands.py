"""Command-line tasks for CampusSync. Run from the project folder with the venv active:

    flask --app app import-timetable    load static/timetable-data.js into MySQL
"""
import json
import re
from pathlib import Path

import click

import db

TIMETABLE_FILE = Path(__file__).resolve().parent / "static" / "timetable-data.js"


def load_timetable(path=TIMETABLE_FILE):
    """Read the TIMETABLE object out of timetable-data.js (it is plain JSON after the '=')."""
    match = re.search(r"const TIMETABLE = (.*);", path.read_text(encoding="utf-8"))
    if not match:
        raise click.ClickException(f"No TIMETABLE data found in {path}")
    return json.loads(match.group(1))


@click.command("import-timetable")
def import_timetable():
    """Load the university timetable into MySQL.

    New profiles are added; imported profiles get their classes replaced by the file's;
    profiles the admin has edited are left alone. Everything is one transaction: if any
    row fails, nothing is saved.
    """
    data = load_timetable()
    conn = db.get_db()
    cur = conn.cursor()
    added = updated = kept = 0
    for f in data["faculty"]:
        cur.execute("SELECT edited FROM timetable_profiles WHERE id = %s", (f["id"],))
        row = cur.fetchone()
        if row and row[0]:
            kept += 1
            continue
        if row:
            cur.execute("UPDATE timetable_profiles SET name = %s WHERE id = %s", (f["name"], f["id"]))
            cur.execute("DELETE FROM timetable_classes WHERE profile_id = %s", (f["id"],))
            updated += 1
        else:
            cur.execute("INSERT INTO timetable_profiles (id, name, source) VALUES (%s, %s, 'imported')",
                        (f["id"], f["name"]))
            added += 1
        # Each class in the file is [day, start, end, code, subject, section, room].
        cur.executemany(
            "INSERT INTO timetable_classes (profile_id, day_of_week, start_time, end_time, code, subject, section, room) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            [(f["id"], *c[:6], c[6] or "") for c in f["classes"]])
    conn.commit()

    cur.execute("SELECT (SELECT COUNT(*) FROM timetable_profiles), (SELECT COUNT(*) FROM timetable_classes)")
    profiles, classes = cur.fetchone()
    click.echo(f"Timetable version {data.get('version', '?')}: {added} added, {updated} updated, "
               f"{kept} kept (edited by the admin).")
    click.echo(f"Database now has {profiles} timetable profiles and {classes} classes.")


def init_app(app):
    app.cli.add_command(import_timetable)
