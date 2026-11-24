# CampusSync user guide

## Logging in

- **Admin**: created with `flask --app app create-admin` (there is no built-in admin). Use
  **Login as Admin**. The password can be changed under **Account**.
- **Faculty**: the admin creates the account under **Faculty** (pick the name from the timetable list,
  click **Create account**, set an email and a temporary password). The faculty member then uses
  **Login as Faculty**. The admin can reset a forgotten password there.
- **Students**: sign up on the Sign Up page with an `@geu.ac.in` email, then use **Login as Student**.

Opening the login page logs the current user out. Nothing is stored in the browser: every page loads
what the logged-in user is allowed to see from the server.

## Admin (Faculty, Timetables, Account)

- **Faculty**: all teachers from the university timetable are listed. **Create account** links the
  account to that person's classes. Teachers who are not in the timetable can be added by name.
  **Link timetable** fixes a wrong link, **Reset password** sets a new temporary password, and
  **Remove** deactivates the account (its history is kept), cancels open bookings and notifies the students.
- **Timetables**: add a class, a busy period (duty, meeting, leave) or extra consultation hours; select
  a class to edit or remove it; switch single free slots off; set the slot length (15 to 55 minutes),
  students per slot and consultation days; restore the imported timetable. If a change makes a booked
  meeting clash with a class, the faculty member is notified and the meeting is flagged.

## Faculty (Booking Requests, Live Queue, My Timetable, Calendar, Notifications)

- **Booking Requests**: accept, reject (with a reason) or reschedule requests; reschedule or cancel
  accepted meetings; approve or decline a student's request to change the time.
- **Live Queue**: students who pressed "I'm here", in calling order, with their waiting time and
  priority. **Call next** finishes the current student and calls the next one.
- **My Timetable** is read-only; the admin maintains it.

## Student (Dashboard, Available Slots, Book Consultation, My Appointments, Calendar, Notifications)

- **Available Slots** shows the faculty member's week and the slots that can be booked in the next 14 days.
- **Book Consultation** asks for the student's details (saved once as a profile), the topic and the
  consultation type (General or Major project).
- **My Appointments** shows tokens, queue position and status. A booking can be cancelled or a new time
  requested. On the day, from 10 minutes before the slot until it ends, an accepted student presses
  **I'm here** to join the queue.

## Booking status

```
Pending ──accept──→ Accepted
                      │ "I'm here"   → checked in, waiting starts
                      │ Call next    → In Session (one student at a time)
                      │ Call next    → Completed
Pending ──reject──→ Rejected
Pending / Accepted ──cancel──→ Cancelled
```

When the slot's time is over, a request nobody answered shows as **Expired**, and an accepted student
who was never called in shows as **Missed**.

## The queue

- Base priority is 2 for a Major project consultation by a semester VII or VIII student, otherwise 1.
  The server sets it when the booking is made.
- Effective priority = base + 1 for every full `AGING_MINUTES` of waiting since check-in.
- Call next picks the highest effective priority, then the earliest check-in, then the lowest token.
  It runs in one locked transaction.

Both timings are set in `.env`: `AGING_MINUTES` and `CHECKIN_EARLY_MINUTES` (10 minutes each by
default). `demo.bat` uses 1-minute aging for its run only, so aging is visible during a presentation.

## Demo walk-through

1. Run `demo.bat`. It prepares a slot starting in a few minutes with three accepted students and
   writes the logins to `demo-logins.txt`.
2. As each student, open **My Appointments** and press **I'm here**. The final-year Major project
   student moves to the front.
3. As the faculty member, open **Live Queue** and press **Call next**. Each choice can be explained
   from the Priority column.
4. **Booking Requests** shows a pending request with a reschedule request. **Notifications** shows
   messages such as "Request accepted" and "It's your turn".

## Tests

`python -m unittest discover -s tests -v` (MySQL must be running; the tests create and delete their own
`unittest-...` accounts). Among the 189 tests:

- `test_end_to_end.py`: a whole consultation day through the API.
- `test_concurrency.py`: 10 students book a 3-seat slot at the same moment, five times. Exactly 3
  succeed every time, with tokens 1, 2 and 3. A double click books once, and deadlocks are retried.
- `test_queue.py`: priority, aging (including the exact minute it applies), tie-breaks, the check-in
  window, and two simultaneous Call next clicks calling only one student.

`tools/setup_db.py` also checks that MySQL itself refuses invalid rows (CHECK and FOREIGN KEY constraints).

## Timetable data

- `static/timetable-data.js` holds the imported faculty schedules (contact numbers are left out).
- `tools/build_timetable_data.py` rebuilds it when a new timetable is published, and
  `tools/verify_timetable_data.py` checks it against the source files.
- After it changes, run `flask --app app import-timetable` again. Timetables the admin edited are kept.
