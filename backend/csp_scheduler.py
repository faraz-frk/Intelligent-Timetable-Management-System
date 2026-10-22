"""
csp_scheduler.py
----------------
CSP timetable scheduler with:
  - Pre-locked Maths teachers per section (Teacher 1 → Section A, Teacher 2 → Section B)
  - Per-section, per-day backtracking for all subjects

Locking rule for Maths
-----------------------
For every class that has exactly 2 Maths teachers in the CSV, the first teacher
(in CSV order) is permanently locked to Section A and the second to Section B.
This applies consistently across the whole week — a student in 1A will always
have Ravi Kumar for Maths, a student in 1B will always have Kiran Babu.

For classes with only 1 Maths teacher the same teacher handles both sections
(load is low enough that backtracking resolves it without "Not Assigned").

Backtracking CSP
----------------
Variables  : subjects to place each day (e.g. 8 subjects for a regular day).
Domains    : valid (period, teacher) pairs per subject.
Constraints:
  * No teacher double-booked at same (day, period) across ALL class-sections.
  * Maths must not occupy two consecutive periods in the same class-section.
  * Sports occupies a fixed consecutive pair once per week.
  * Locked Maths teacher is always preferred (pre-set in class_subject_teacher).

Algorithm  : subject-first backtracking with MRV ordering.
"""

import logging
import random
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DAYS: List[str] = [
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"
]

PERIOD_TIMES: Dict[int, Tuple[str, str]] = {
    1: ("09:00", "09:45"),
    2: ("09:45", "10:30"),
    3: ("10:40", "11:25"),
    4: ("11:25", "12:10"),
    5: ("13:00", "13:45"),
    6: ("13:45", "14:30"),
    7: ("14:30", "15:15"),
    8: ("15:15", "16:00"),
}

DISPLAY_TIMELINE = [
    {"type": "period",  "number": 1},
    {"type": "period",  "number": 2},
    {"type": "break",   "label": "Break",       "time": "10:30 - 10:40"},
    {"type": "period",  "number": 3},
    {"type": "period",  "number": 4},
    {"type": "lunch",   "label": "Lunch Break", "time": "12:10 - 13:00"},
    {"type": "period",  "number": 5},
    {"type": "period",  "number": 6},
    {"type": "period",  "number": 7},
    {"type": "period",  "number": 8},
]

SUBJECTS_PRIMARY: List[str] = [
    "Telugu", "Hindi", "English", "Social",
    "Maths", "Environmental Science", "Computer",
]
SUBJECTS_SECONDARY: List[str] = [
    "Telugu", "Hindi", "English", "Maths",
    "Physics", "Biology", "Social",
]

# Valid sports consecutive pairs (no cross-Break or cross-Lunch pairs)
SPORTS_PAIRS: List[Tuple[int, int]] = [
    (1, 2), (3, 4), (5, 6), (6, 7), (7, 8)
]

_DAY_ABBREV: Dict[str, str] = {
    "Monday":    "Mon",
    "Tuesday":   "Tue",
    "Wednesday": "Wed",
    "Thursday":  "Thu",
    "Friday":    "Fri",
    "Saturday":  "Sat",
}

ALL_PERIODS = list(range(1, 9))  # [1, 2, 3, 4, 5, 6, 7, 8]


def get_period_time(period_num: int) -> str:
    if period_num not in PERIOD_TIMES:
        return "Unknown"
    start, end = PERIOD_TIMES[period_num]
    return f"{start} - {end}"


# ---------------------------------------------------------------------------
# TimetableCSP
# ---------------------------------------------------------------------------

class TimetableCSP:
    """
    CSP-based timetable scheduler.

    Key feature: Maths teachers are pre-locked per section before any
    scheduling begins, guaranteeing consistency across the whole week.
    """

    def __init__(self, teachers_data: List[dict]) -> None:
        self.teachers: List[dict] = teachers_data

        # Global teacher booking: (day, period) → set of booked teacher names
        self.teacher_usage: Dict[Tuple[str, int], Set[str]] = {}

        # Locked teacher per (class_num, section, subject).
        # Pre-populated for Maths; other subjects get locked on first assignment.
        self.class_subject_teacher: Dict[Tuple[int, str, str], str] = {}

        # Build the Maths locks immediately on construction
        self._lock_maths_teachers()

    # ------------------------------------------------------------------
    # Maths teacher locking (called once in __init__)
    # ------------------------------------------------------------------

    def _lock_maths_teachers(self) -> None:
        """
        For every class that has 2 Maths teachers in the CSV:
          • Teacher 1 (first in CSV order) → permanently locked to Section A
          • Teacher 2                      → permanently locked to Section B

        This ensures a student always sees the SAME Maths teacher all week.
        """
        # class_num → ordered list of Maths teacher names (CSV order preserved)
        class_maths: Dict[int, List[str]] = {}
        for t in self.teachers:
            if t["subject"] != "Maths":
                continue
            for cls in t["classes"]:
                class_maths.setdefault(cls, [])
                if t["name"] not in class_maths[cls]:
                    class_maths[cls].append(t["name"])

        for cls, teachers in class_maths.items():
            if len(teachers) >= 2:
                # Lock: Teacher 1 → A, Teacher 2 → B
                self.class_subject_teacher[(cls, "A", "Maths")] = teachers[0]
                self.class_subject_teacher[(cls, "B", "Maths")] = teachers[1]
                logger.info(
                    "Maths lock: Class %d-A → %s | Class %d-B → %s",
                    cls, teachers[0], cls, teachers[1],
                )
            # If only 1 Maths teacher: no lock needed; backtracking handles it.

    # ------------------------------------------------------------------
    # Subject helpers
    # ------------------------------------------------------------------

    def get_subjects(self, class_num: int) -> List[str]:
        if 1 <= class_num <= 5:
            return list(SUBJECTS_PRIMARY)
        return list(SUBJECTS_SECONDARY)

    # ------------------------------------------------------------------
    # Teacher lookup helpers
    # ------------------------------------------------------------------

    def _free_teachers(
        self,
        subject: str,
        class_num: int,
        day: str,
        period: int,
        relaxed: bool = False,
    ) -> List[str]:
        """
        Return teachers eligible to teach subject/class at (day, period).

        Strict (relaxed=False): checks day availability, period availability,
                                and not already double-booked.
        Relaxed (relaxed=True): only checks not double-booked.
        """
        day_abbrev = _DAY_ABBREV.get(day, day)
        booked = self.teacher_usage.get((day, period), set())
        result: List[str] = []
        for t in self.teachers:
            if t["subject"] != subject:
                continue
            if class_num not in t["classes"]:
                continue
            if not relaxed:
                if day_abbrev not in t["available_days"]:
                    continue
                if period not in t["available_periods"]:
                    continue
            if t["name"] in booked:
                continue
            result.append(t["name"])
        return result

    def _get_teachers(
        self, subject: str, class_num: int, section: str, day: str, period: int
    ) -> List[str]:
        """
        Return ordered candidate teachers.
        - Locked teacher (from class_subject_teacher) is always placed first.
        - Others sorted by ascending daily load (least-loaded first).
        - Falls back to relaxed mode if no strict candidates exist.
        """
        teachers = self._free_teachers(subject, class_num, day, period, relaxed=False)
        if not teachers:
            teachers = self._free_teachers(subject, class_num, day, period, relaxed=True)

        # Sort by daily load (ascending)
        teachers.sort(key=lambda name: sum(
            1 for (d, _), bk in self.teacher_usage.items() if d == day and name in bk
        ))

        # Promote locked teacher to front
        lock_key = (class_num, section, subject)
        locked = self.class_subject_teacher.get(lock_key)
        if locked and locked in teachers:
            teachers = [locked] + [t for t in teachers if t != locked]

        return teachers

    # ------------------------------------------------------------------
    # Backtracking CSP (subject-first)
    # ------------------------------------------------------------------

    def _count_options(
        self,
        subject: str,
        assignment: Dict[int, Tuple[str, str]],
        blocked: Set[int],
        class_num: int,
        day: str,
    ) -> int:
        """Count valid (period, teacher) pairs for MRV subject ordering."""
        count = 0
        for p in ALL_PERIODS:
            if p in assignment or p in blocked:
                continue
            if subject == "Maths":
                if assignment.get(p - 1, (None,))[0] == "Maths":
                    continue
                if assignment.get(p + 1, (None,))[0] == "Maths":
                    continue
            strict = self._free_teachers(subject, class_num, day, p, relaxed=False)
            relaxed = self._free_teachers(subject, class_num, day, p, relaxed=True)
            count += len(strict) or len(relaxed)
        return count

    def _backtrack(
        self,
        subjects: List[str],
        assignment: Dict[int, Tuple[str, str]],
        blocked: Set[int],
        class_num: int,
        section: str,
        day: str,
    ) -> bool:
        """
        Recursively assign each subject in `subjects` to a free (period, teacher).

        `blocked` = periods already taken by pre-fixed slots (Sports).
        Modifies `assignment` in-place; undoes changes on failure.
        Returns True on full success.
        """
        if not subjects:
            return True

        # MRV: pick the subject with fewest valid options first
        subjects.sort(
            key=lambda s: self._count_options(s, assignment, blocked, class_num, day)
        )
        subject = subjects[0]
        remaining = subjects[1:]

        # Find all valid periods for this subject
        candidate_periods: List[int] = []
        for p in ALL_PERIODS:
            if p in assignment or p in blocked:
                continue
            if subject == "Maths":
                # Enforce non-consecutive Maths rule
                if assignment.get(p - 1, (None,))[0] == "Maths":
                    continue
                if assignment.get(p + 1, (None,))[0] == "Maths":
                    continue
            candidate_periods.append(p)

        random.shuffle(candidate_periods)  # variety between runs

        for period in candidate_periods:
            teachers = self._get_teachers(subject, class_num, section, day, period)
            for teacher in teachers:
                # Tentatively assign
                assignment[period] = (subject, teacher)
                self.teacher_usage.setdefault((day, period), set()).add(teacher)

                if self._backtrack(remaining[:], assignment, blocked, class_num, section, day):
                    # Commit: lock teacher for future days (non-Maths subjects)
                    lock_key = (class_num, section, subject)
                    if lock_key not in self.class_subject_teacher:
                        self.class_subject_teacher[lock_key] = teacher
                    return True

                # Undo
                del assignment[period]
                self.teacher_usage[(day, period)].discard(teacher)

        return False  # no valid assignment found — caller must backtrack

    # ------------------------------------------------------------------
    # Fixed-slot helper (Sports)
    # ------------------------------------------------------------------

    def _assign_fixed(
        self,
        subject: str,
        period: int,
        class_num: int,
        section: str,
        day: str,
        assignment: Dict[int, Tuple[str, str]],
    ) -> None:
        """Assign a teacher to a fixed period (Sports). Committed immediately."""
        teachers = self._get_teachers(subject, class_num, section, day, period)
        teacher = teachers[0] if teachers else (
            "Sports Teacher" if subject == "Sports" else "Not Assigned"
        )
        assignment[period] = (subject, teacher)
        if teacher not in ("Sports Teacher", "Not Assigned"):
            self.teacher_usage.setdefault((day, period), set()).add(teacher)

    # ------------------------------------------------------------------
    # Public: generate one timetable
    # ------------------------------------------------------------------

    def assign_teacher_slot(self, teacher_name: str, day: str, period: int,
                            class_num: int, section: str) -> None:
        """External helper to record a booking (used by app.py if needed)."""
        self.teacher_usage.setdefault((day, period), set()).add(teacher_name)

    def generate_timetable(self, class_num: int, section: str) -> dict:
        """
        Generate a full weekly timetable for one class-section.

        For each day:
          1. Fix Sports slots (sports day only).
          2. Run subject-first backtracking to fill remaining slots.
             Maths locked teacher is always preferred via class_subject_teacher.
        """
        subjects = self.get_subjects(class_num)
        sports_day  = random.choice(DAYS)
        sports_pair = random.choice(SPORTS_PAIRS)

        schedule: Dict[str, List[dict]] = {}

        for day in DAYS:
            is_sports = (day == sports_day)
            assignment: Dict[int, Tuple[str, str]] = {}
            blocked: Set[int] = set()

            # ── 1. Fix Sports slots (same teacher for both periods) ──
            if is_sports:
                sp1, sp2 = sports_pair
                # Pick one sports teacher who is free at BOTH periods
                t1_candidates = self._get_teachers("Sports", class_num, section, day, sp1)
                sports_teacher = None
                for candidate in t1_candidates:
                    # Check also free at sp2
                    booked_sp2 = self.teacher_usage.get((day, sp2), set())
                    if candidate not in booked_sp2:
                        sports_teacher = candidate
                        break
                if sports_teacher is None:
                    sports_teacher = t1_candidates[0] if t1_candidates else "Sports Teacher"

                for sp in (sp1, sp2):
                    assignment[sp] = ("Sports", sports_teacher)
                    if sports_teacher != "Sports Teacher":
                        self.teacher_usage.setdefault((day, sp), set()).add(sports_teacher)
                blocked.update({sp1, sp2})


            # ── 2. Build subject list for backtracking ────────────────
            if is_sports:
                non_maths = [s for s in subjects if s != "Maths"]
                random.shuffle(non_maths)
                # Sports day: 1 Maths + 5 other subjects in 6 free slots
                to_place = ["Maths"] + non_maths[:5]
            else:
                # Regular day: all 7 subjects + extra Maths = 8 slots
                to_place = list(subjects) + ["Maths"]
                random.shuffle(to_place)

            # ── 3. Backtrack to assign subjects to free periods ───────
            success = self._backtrack(to_place[:], assignment, blocked, class_num, section, day)

            if not success:
                logger.warning(
                    "Backtracking incomplete for Class %s%s on %s", class_num, section, day
                )

            # ── 4. Build the day's slot list ──────────────────────────
            day_slots: List[dict] = []
            for p in ALL_PERIODS:
                start, end = PERIOD_TIMES[p]
                if p in assignment:
                    subj, teacher = assignment[p]
                    teacher = teacher or "Not Assigned"
                else:
                    subj, teacher = "Free", "—"

                day_slots.append({
                    "period":  p,
                    "subject": subj,
                    "teacher": teacher,
                    "time":    f"{start} - {end}",
                })

            schedule[day] = day_slots

        return {
            "class_num":      class_num,
            "section":        section,
            "sports_day":     sports_day,
            "sports_periods": sports_pair,
            "schedule":       schedule,
        }

    # ------------------------------------------------------------------
    # Public: generate all 20 timetables
    # ------------------------------------------------------------------

    def generate_all_timetables(self) -> Dict[str, dict]:
        """Generate timetables for all 20 class-sections (Classes 1-10, A & B)."""
        all_timetables: Dict[str, dict] = {}
        for class_num in range(1, 11):
            for section in ("A", "B"):
                key = f"Class_{class_num}{section}"
                logger.info("Generating timetable for %s", key)
                tt = self.generate_timetable(class_num, section)
                all_timetables[key] = tt
        return all_timetables

    # ------------------------------------------------------------------
    # Display helper
    # ------------------------------------------------------------------

    def get_display_schedule(self, timetable: dict) -> Dict[str, List[dict]]:
        """Return schedule with Break and Lunch entries inserted per DISPLAY_TIMELINE."""
        schedule = timetable.get("schedule", {})
        display: Dict[str, List[dict]] = {}

        for day, periods in schedule.items():
            period_map = {slot["period"]: slot for slot in periods}
            display_slots: List[dict] = []

            for entry in DISPLAY_TIMELINE:
                if entry["type"] == "period":
                    p = entry["number"]
                    slot = period_map.get(p)
                    if slot:
                        display_slots.append(dict(slot))
                    else:
                        start, end = PERIOD_TIMES.get(p, ("", ""))
                        display_slots.append({
                            "period":  p,
                            "subject": "—",
                            "teacher": "—",
                            "time":    f"{start} - {end}",
                        })
                else:
                    display_slots.append({
                        "type":  entry["type"],
                        "label": entry["label"],
                        "time":  entry["time"],
                    })

            display[day] = display_slots

        return display
