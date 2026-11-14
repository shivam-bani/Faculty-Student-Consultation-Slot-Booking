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

All data is on the server (MySQL): accounts, faculty, timetables, consultation settings,
bookings, the queue and notifications, so every user sees the same data from any browser.
The pages keep a copy in the browser only to draw quickly; it is reloaded from the server on
every page load and after every change.

## Logging in
- **Admin:** created with `flask --app app create-admin` (there is no built-in admin). Click
  **Login as Admin**; the password can be changed under **Account**.
- **Faculty:** the admin creates the account under **Faculty** (pick the name from the timetable
  list, click **Create account**, set email + temporary password). The faculty member then uses
  **Login as Faculty**. The admin can reset a forgotten password there.
- **Students:** sign up on the Sign Up page with an `@geu.ac.in` email, then **Login as Student**.
- Passwords are stored only as hashes. Who is logged in (and their role) is decided by the server.
- **Reset all data saved in this browser** on the login page clears the data that is still kept
  in the browser; it does not delete accounts.

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

**Faculty** (Booking Requests, Live Queue, My Timetable, Calendar, Notifications)
- Accept, reject or reschedule requests; reschedule or cancel accepted meetings;
  approve or decline a student's request to change time.
- Live Queue: the students who pressed "I'm here", in calling order, with waiting time and
  priority. **Call next** finishes the current student and calls the next one in.
- My Timetable is read-only; the admin maintains it.

**Student** (Dashboard, Available Slots, Book Consultation, My Appointments, Calendar, Notifications)
- Free time between a faculty member's classes is offered automatically as slots;
  nobody creates sessions by hand. The student requests a slot with their details and the
  consultation type (General or Major project).
- On the day, from `CHECKIN_EARLY_MINUTES` before the slot until it ends, an accepted student
  presses **I'm here** on My Appointments and sees their place in the queue.

### Queue manager (`queue_manager.py`)
A slot is served one student at a time, like one CPU shared by processes.
- Base priority 2 for semester VII/VIII **Major project** consultations, otherwise 1 (set by the
  server when booking; the semester comes from the student's own profile).
- Waiting starts at check-in. **Aging:** priority + 1 for every completed `AGING_MINUTES` of
  waiting, so a long-waiting student overtakes newer high-priority ones (no starvation).
- Call next picks the highest priority, then the earliest check-in, then the lowest token, in
  one locked transaction; at most one student can be "In Session" per slot.
- Both settings are in `.env` (default 10 minutes each). For a classroom demo set
  `AGING_MINUTES=1` and restart `python app.py`.

Status flow: Pending -> Accepted -> (I'm here) -> In Session -> Completed, or Rejected / Cancelled.
When the slot's time is over: never answered -> Expired, accepted but never called in -> Missed.

## Files
- `static/`: the pages, `style.css`, `script.js` and `timetable-data.js`, served by Flask.
- `app.py` (Flask app), `db.py` (MySQL connection), `commands.py` (`flask` commands),
  `schema.sql` (tables and constraints), `tools/setup_db.py` (database setup), `tests/`.
- API: `auth.py` (sign up, login, roles), `faculty.py` (faculty accounts and settings),
  `timetable.py` (timetable editing), `slots.py` (free-slot calculation), `bookings.py`
  (booking in one locked transaction, student and faculty actions), `queue_manager.py`
  (check-in, priority with aging, call next), `notifications.py` (each one is saved in the same
  transaction as the action it describes).
- `static/timetable-data.js`: imported faculty schedules (contact numbers deliberately excluded).
  The admin's edits are stored separately, so this file is only the starting point.
- `tools/build_timetable_data.py`: rebuilds it when a new timetable is published.
- `tools/verify_timetable_data.py`: checks the data against the source files
  (current result: 270/270 PDF subject codes placed correctly, 1526/1526 class periods present).
- When timetable-data.js changes, run `flask --app app import-timetable` again; timetables the
  admin edited by hand are kept.

## Known limitations (demo)
- New notifications and queue changes appear when a page loads or refreshes itself (every
  15 seconds on My Appointments and Live Queue); there is no instant push.
- The student's semester is self-reported in their profile (used for Major project priority).
- Students' own class clashes are not checked yet (only clashes with their other bookings).
