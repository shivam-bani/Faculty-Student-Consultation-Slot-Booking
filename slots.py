"""Weekly consultation slots: the free time between a faculty member's classes.

This is the server's copy of weeklySlots() in static/script.js, line for line, so the server can
check that a slot a student books really exists. Both must give the same answer: tests/test_slots.py
checks the rules, and the browser test compares the two for real timetables.
"""
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
