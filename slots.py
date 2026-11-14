"""Consultation slots: the free time between a faculty member's classes, and what can be booked.

weekly_slots() is the server's copy of weeklySlots() in static/script.js (which draws the week
grid), line for line; upcoming_slots() was ported from the page's former upcomingSlots(), which the
pages no longer have because they now ask the server. tests/test_slots.py checks the rules, and both
were compared with the JavaScript versions on real timetables.
Also the date texts used in messages, the same as the pages show them.
"""
from datetime import datetime, timedelta

DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def when_text(date, start, end):
    """The same text the page shows for a session, e.g. "16 Nov 2026, 09:00 - 09:30"."""
    d = datetime.strptime(date, "%Y-%m-%d").date()
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}, {start} - {end}"

DAY_START, DAY_END = "08:00", "17:55"  # college working hours used to find free time


def to_min(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def to_hm(m):
    return f"{m // 60:02d}:{m % 60:02d}"


def overlaps(a1, a2, b1, b2):
    return to_min(a1) < to_min(b2) and to_min(b1) < to_min(a2)


def weekly_slots(settings, classes):
    """Free gaps between classes, cut into slots of slot_len minutes, plus extra (custom) slots.

    settings: {"slot_len", "capacity", "days", "off": ["day|HH:MM", ...], "custom": [{id, day, start, end, capacity}]}
    classes:  [{"day", "start", "end", ...}, ...]
    Returns slots in the same shape and order as script.js: key, day, start, end, capacity, kind, off.
    """
    slot_len, off = settings["slot_len"], set(settings["off"])
    custom = [{"key": f"c|{c['id']}", "id": c["id"], "day": c["day"], "start": c["start"], "end": c["end"],
               "capacity": c["capacity"], "kind": "custom", "off": False} for c in settings["custom"]]
    out = []
    for d in settings["days"]:  # no timetable yet = no classes, so working hours are free
        busy = sorted((to_min(c["start"]), to_min(c["end"])) for c in classes if c["day"] == d)
        t, gaps = to_min(DAY_START), []
        for s, e in busy:
            if s > t:
                gaps.append((t, s))
            t = max(t, e)
        if to_min(DAY_END) > t:
            gaps.append((t, to_min(DAY_END)))
        for gs, ge in gaps:
            s = gs
            while s + slot_len <= ge:
                start, end, key = to_hm(s), to_hm(s + slot_len), f"{d}|{to_hm(s)}"
                if not any(c["day"] == d and overlaps(c["start"], c["end"], start, end) for c in custom):
                    out.append({"key": key, "day": d, "start": start, "end": end,
                                "capacity": settings["capacity"], "kind": "auto", "off": key in off})
                s += slot_len
    return sorted(out + custom, key=lambda x: (x["day"], to_min(x["start"])))  # stable, like JS sort


def is_past(day, start, now):
    """True when a slot on `day` (YYYY-MM-DD) starting at `start` (HH:MM) has begun or is over."""
    today = now.date().isoformat()
    return day < today or (day == today and start <= now.strftime("%H:%M"))


def upcoming_slots(weekly, sessions, now, days=14):
    """What a student can book with one faculty member, as in upcomingSlots() in script.js:
    weekly slots for the next `days` days that are not switched off, in the past, or already turned
    into a session, plus every future session that is not cancelled and still has seats.

    weekly:   weekly_slots(...) for this faculty member
    sessions: [{"id", "date", "start", "end", "capacity", "booked", "source", "cancelled", "topic"}, ...]
              where booked = bookings that hold a seat (not Rejected or Cancelled)
    now:      the current local datetime (passed in so tests can fix the time)
    """
    live = [s for s in sessions if not s["cancelled"]]
    # A timetable slot that became a session is not offered again as a free slot, even if that
    # session was later cancelled (same as script.js).
    used = {f"{s['date']}|{s['start']}" for s in sessions if s["source"] == "timetable"}
    open_weekly = [w for w in weekly if not w["off"]]
    out = []
    for i in range(days):
        day = now.date() + timedelta(days=i)
        date, weekday = day.isoformat(), day.isoweekday()  # 1 = Monday ... 7 = Sunday (never a consultation day)
        for w in open_weekly:
            if w["day"] != weekday or is_past(date, w["start"], now) or f"{date}|{w['start']}" in used:
                continue
            if any(s["date"] == date and overlaps(s["start"], s["end"], w["start"], w["end"]) for s in live):
                continue
            out.append({"date": date, "start": w["start"], "end": w["end"], "capacity": w["capacity"],
                        "seats": w["capacity"], "topic": "Consultation hours", "session_id": None})
    for s in live:
        seats = s["capacity"] - s["booked"]
        if not is_past(s["date"], s["start"], now) and seats > 0:
            out.append({"date": s["date"], "start": s["start"], "end": s["end"], "capacity": s["capacity"],
                        "seats": seats, "topic": s["topic"], "session_id": s["id"]})
    return sorted(out, key=lambda x: x["date"] + x["start"])
