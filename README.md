# CampusSync

## Setup (Windows, once)
Needs Python 3.14 and MySQL Server 8.4 (running as a Windows service). In PowerShell 7, in this folder:

```
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python tools/setup_db.py                   # asks for the MySQL root password; creates the database, the app user and .env
flask --app app import-timetable           # loads the 101 faculty timetables into MySQL
flask --app app create-admin               # asks for the admin's name, email and password
```

`.env` holds the database password and the secret key. It is created by `tools/setup_db.py`
and must never be committed or shared.

## Run
```
.\.venv\Scripts\Activate.ps1
python app.py
```
Then open http://127.0.0.1:5000/. Tests: `python -m unittest discover -s tests -v` (needs MySQL running).

The backend is being connected step by step. Until login moves to the server, the pages still
keep their data in the browser and use the demo logins below.

## Logging in
- **Admin:** `admin@campussync.edu` / `Admin@1234` (created automatically). Click **Login as Admin**,
  then change the password under **Account**.
- **Faculty:** the admin creates the account under **Faculty** (pick the name from the timetable
  list, click **Create account**, set email + temporary password). The faculty member then uses
  **Login as Faculty**. The admin can reset a forgotten password there.
- **Students:** sign up on the Sign Up page, then **Login as Student**.
- Data lives in one browser. If something looks stale after an update, use
  **Reset all data saved in this browser** on the login page.

## Roles
**Admin** (Faculty, Timetables, Account)
- Faculty: all 101 teachers from the timetable files are listed; **Create account** links the
  account to that person's classes. Teachers not in the files can be added by name (a name that
  matches the timetable is linked automatically). **Link timetable** fixes a wrong link,
  **Reset password** sets a new temporary password, **Remove** cancels their open bookings
  and notifies the students.
- Timetables: add a class, a busy period (duty, meeting, leave) or extra consultation hours;
  select a class to edit or remove it; switch individual free slots off; set slot length,
  students per slot and consultation days; restore the imported timetable.
  If a change makes an already booked meeting clash with a class, the faculty member is
  notified and the meeting is flagged on their Booking Requests page.

**Faculty** (Booking Requests, My Timetable, Calendar, Notifications)
- Accept, reject or reschedule requests; reschedule or cancel accepted meetings;
  approve or decline a student's request to change time.
- My Timetable is read-only; the admin maintains it.

**Student** (Dashboard, Available Slots, Book Consultation, My Appointments, Calendar, Notifications)
- Free time between a faculty member's classes is offered automatically as slots;
  nobody creates sessions by hand. The student requests a slot with their details.

Status flow: Pending -> Accepted -> Completed (automatically after the meeting time),
or Rejected / Cancelled, or Expired (never answered before the meeting time).

## Files
- `static/`: the pages, `style.css`, `script.js` and `timetable-data.js`, served by Flask.
- `app.py` (Flask app and API), `db.py` (MySQL connection), `commands.py` (`flask` commands),
  `schema.sql` (tables and constraints), `tools/setup_db.py` (database setup), `tests/`.
- `static/timetable-data.js`: imported faculty schedules (contact numbers deliberately excluded).
  The admin's edits are stored separately, so this file is only the starting point.
- `tools/build_timetable_data.py`: rebuilds it when a new timetable is published.
- `tools/verify_timetable_data.py`: checks the data against the source files
  (current result: 270/270 PDF subject codes placed correctly, 1526/1526 class periods present).
- When timetable-data.js changes, browsers refresh their copy on the next page load; timetables
  the admin edited by hand are kept.

## Known limitations (demo)
- localStorage only (one browser); passwords are stored in plain text. Replace the
  DATA LAYER in script.js with a backend before real use.
- Students' own class clashes are not checked yet.
