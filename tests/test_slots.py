"""Tests for the weekly slot calculation (slots.py). No database needed.

Run from the project folder (venv active):
    python -m unittest discover -s tests -v
"""
import unittest

from slots import weekly_slots


def settings(**changes):
    return {"slot_len": 30, "capacity": 3, "days": [1], "off": [], "custom": [], **changes}


def starts(slots, day=1, kind=None):
    return [s["start"] for s in slots if s["day"] == day and (kind is None or s["kind"] == kind)]


class WeeklySlotTests(unittest.TestCase):
    def test_free_day_is_cut_into_slots_within_working_hours(self):
        slots = weekly_slots(settings(), [])
        # 08:00 to 17:55 is 595 minutes: 19 slots of 30 minutes, the last one 17:00-17:30.
        self.assertEqual(len(slots), 19)
        self.assertEqual((slots[0]["start"], slots[-1]["start"], slots[-1]["end"]), ("08:00", "17:00", "17:30"))
        self.assertEqual(slots[0], {"key": "1|08:00", "day": 1, "start": "08:00", "end": "08:30",
                                    "capacity": 3, "kind": "auto", "off": False})

    def test_classes_are_skipped(self):
        classes = [{"day": 1, "start": "10:00", "end": "11:00"}]
        s = starts(weekly_slots(settings(), classes))
        self.assertIn("09:30", s)          # 09:30-10:00 still fits before the class
        self.assertNotIn("10:00", s)
        self.assertNotIn("10:30", s)
        self.assertEqual(s[s.index("09:30") + 1], "11:00")  # the next slot starts when the class ends

    def test_gap_too_short_for_a_slot(self):
        classes = [{"day": 1, "start": "08:20", "end": "17:55"}]
        self.assertEqual(weekly_slots(settings(), classes), [])  # 08:00-08:20 is shorter than 30 minutes

    def test_overlapping_classes_are_merged(self):
        classes = [{"day": 1, "start": "09:00", "end": "10:30"}, {"day": 1, "start": "10:00", "end": "11:00"},
                   {"day": 1, "start": "09:30", "end": "09:45"}]
        s = starts(weekly_slots(settings(), classes))
        self.assertEqual(s[:3], ["08:00", "08:30", "11:00"])

    def test_only_consultation_days(self):
        slots = weekly_slots(settings(days=[2, 5]), [])
        self.assertEqual({s["day"] for s in slots}, {2, 5})

    def test_classes_on_other_days_do_not_matter(self):
        self.assertEqual(len(weekly_slots(settings(), [{"day": 2, "start": "08:00", "end": "17:55"}])), 19)

    def test_switched_off_slots_are_marked(self):
        slots = weekly_slots(settings(off=["1|09:00"]), [])
        self.assertEqual([s["start"] for s in slots if s["off"]], ["09:00"])

    def test_slot_length_and_capacity(self):
        slots = weekly_slots(settings(slot_len=55, capacity=5), [])
        self.assertEqual(starts(slots)[:3], ["08:00", "08:55", "09:50"])
        self.assertEqual({s["capacity"] for s in slots}, {5})

    def test_extra_slot_replaces_the_free_slots_it_overlaps(self):
        custom = [{"id": "7", "day": 1, "start": "09:15", "end": "10:15", "capacity": 6}]
        slots = weekly_slots(settings(custom=custom), [])
        self.assertNotIn("09:00", starts(slots, kind="auto"))  # 09:00-09:30 overlaps the extra slot
        self.assertNotIn("09:30", starts(slots, kind="auto"))
        self.assertNotIn("10:00", starts(slots, kind="auto"))
        self.assertIn("10:30", starts(slots, kind="auto"))
        extra = [s for s in slots if s["kind"] == "custom"]
        self.assertEqual(extra, [{"key": "c|7", "id": "7", "day": 1, "start": "09:15", "end": "10:15",
                                  "capacity": 6, "kind": "custom", "off": False}])

    def test_extra_slot_on_a_day_without_consultations_is_kept(self):
        custom = [{"id": "8", "day": 6, "start": "10:00", "end": "11:00", "capacity": 2}]
        slots = weekly_slots(settings(custom=custom), [])
        self.assertEqual([s["key"] for s in slots if s["day"] == 6], ["c|8"])

    def test_sorted_by_day_then_time(self):
        custom = [{"id": "9", "day": 1, "start": "17:30", "end": "17:50", "capacity": 1}]
        slots = weekly_slots(settings(days=[3, 1], custom=custom), [])
        keys = [(s["day"], s["start"]) for s in slots]
        self.assertEqual(keys, sorted(keys, key=lambda k: (k[0], int(k[1][:2]) * 60 + int(k[1][3:]))))


if __name__ == "__main__":
    unittest.main()
