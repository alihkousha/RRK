"""Date windows that keep each grid query under the server's 999-row cap.

A search returns at most 999 rows (``totalRows: 999``, ``moreData: true``).
A capped window is split by letter date (never empty in the data): ranges
of years, then months, then days.  Windows overlap by one day (a month runs
to the next month's 1st), so no invalid Jalali date such as ``1402/07/31`` is
ever sent; the overlap only re-stores some rows.  A capped "29 to the next
1st" is split into its real days (months 1-6 have 31, 7-11 have 30; Esfand's
29 or 30 stay together).  A single day that is still capped (43% of ads have
the placeholder ``1300/02/01``) is split the same way by newspaper date: from
the letter's month to 13 months on (97% are printed then), and before and
after that.  A window still capped on one day of both dates is split by
newspaper type (``PICKS``: Tehran, provinces, ...; 4 queries), then by city
(716; a Tehran window stays whole), both exact matches, from the dropdowns
(main.py fetches them each run); a window still capped after both, or listed
without the choices, can't be split.  A capped "last day to the next 1st" with
both dates fixed is split into its two days first.

The newspaper-date windows miss the ads without a newspaper date on a capped
letter day: :func:`undated_windows` lists by status those that hold them
(``UNDATED``), whatever the letter date, beside a full listing and the gaps
(``--missing-only``: also ads a run before 2026-11-01 never listed).

``--years`` limits a run to letter dates in some years: :func:`within` splits
windows that cross the limit, free of queries, into parts inside and outside
it; a window by newspaper date alone gets ``bounds``, its letter dates
clamped to them, and the parts it splits into outside them are dropped.

:func:`new_windows` lists by newspaper date instead, from a week before the
newest one stored: the notices printed since, whatever their letter date.  A
crowded newspaper day is split by letter date: the 13 months up to it first.

:func:`gap_windows` builds windows from the database instead: only the
dates whose ads still lack a detail page, for a run that resumes the details
without listing everything again (detail links are session-bound).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import date

CAP = 999
GAP_BUDGET = 900  # rows per gap window by the stored counts; new ads since fill the rest
NEW_MARGIN = 7  # days listed again before the newest stored newspaper date: printed late, or listed mid-day

log = logging.getLogger("rrk.windows")

# (from item, to item) per date, in the order they are split.  Both are
# among the grid's items to submit, so a window is just a grid request.
DIMENSIONS = (
    ("P199_SABTNODATE_AZ", "P199_SABTNODATE_TA"),  # letter date: never empty
    ("P199_NEWSPAPERDATE_AZ", "P199_NEWSPAPER_TA"),  # newspaper date: empty on ~4%
)
FIRST_YEAR = 1300  # the data's placeholder date is 1300/02/01
# Single-choice grid items that split a window capped on one day of both
# dates, in this order; each value is matched exactly (checked live 2026-11-01:
# a type-2 day of 1,299 rows was Tehran 556 + provinces 743; city 124, Tehran,
# also 556, a code's prefix 0).
PICKS = ("P199_NEWSPAPERTYPE", "P199_CITYCODE")
STATUS = "P199_NEWSSTATUS"
# Ready to print, being published unsigned, published unsigned: 99.8% of the 5,927 stored ads without
# a newspaper date, and rare (8,461 of 6M ads; checked live 2026-11-01: a letter day + status 2 gave its
# 19 undated ads): listing each status costs a type ~40 queries, not 3 more per crowded letter day.
UNDATED = ("2", "3", "4")


def _end_year() -> int:
    """A Jalali year after today's (Gregorian year - 621 from Nowruz on)."""
    return date.today().year - 620


def jalali_today() -> str:
    """Today's Jalali date, give or take a day (Nowruz taken as 21 March)."""
    today = date.today()
    nowruz = date(today.year if today >= date(today.year, 3, 21) else today.year - 1, 3, 21)
    days = (today - nowruz).days
    month, day = divmod(days, 31) if days < 186 else (6 + (days - 186) // 30, (days - 186) % 30)
    return _day((nowruz.year - 621) * 12 + month, day + 1)


@dataclass(frozen=True)
class Window:
    """Part of one ad type's listing.  ``level`` is ``all`` (no date
    filter), ``years`` [lo, hi), ``months`` [lo, hi) as ``year*12 + month-1``,
    ``days`` [lo, hi] of month ``month`` (day 29 stands for 29 up to the
    next month's 1st, so no month needs its length), ``day`` [lo, hi] of
    month ``month`` exactly (hi 32: up to the next month's 1st), or ``dates``:
    from the first of ``days`` (stored dates) to the last."""

    level: str = "all"
    lo: int = 0
    hi: int = 0
    month: int = 0
    dim: int = 0
    fixed: tuple[tuple[str, str], ...] = field(default=())  # an earlier dimension's day
    days: tuple[str, ...] = ()
    bounds: tuple[str, ...] = ()  # (from, to): this run's letter dates (--years), to exclusive

    def filters(self) -> dict[str, str]:
        found = self._filters()
        if self.bounds:
            az, ta = DIMENSIONS[0]
            found[az] = max(found.get(az, self.bounds[0]), self.bounds[0])
            found[ta] = min(found.get(ta, self.bounds[1]), self.bounds[1])
        return found

    def _filters(self) -> dict[str, str]:
        found = dict(self.fixed)
        if self.level == "all":
            return found
        az, ta = DIMENSIONS[self.dim]
        if self.level == "years":
            found[az], found[ta] = f"{self.lo}/01/01", f"{self.hi}/01/01"
        elif self.level == "months":
            found[az], found[ta] = _month_start(self.lo), _month_start(self.hi)
        elif self.level == "dates":
            found[az], found[ta] = self.days[0], self.days[-1]
        elif self.level == "day":
            found[az] = _day(self.month, self.lo)
            found[ta] = _day(self.month, self.hi) if self.hi < 32 else _month_start(self.month + 1)
        else:
            found[az] = _day(self.month, self.lo)
            found[ta] = _day(self.month, self.hi) if self.hi < 29 else _month_start(self.month + 1)
        return found

    def split(self, choices: Mapping[str, Sequence[str]] | None = None) -> list[Window]:
        """Smaller windows that together cover this one; [] if it can't be
        split.  A step that only changes the level (a year to its months, a
        month to its days) is taken at once: it would repeat the same query.
        *choices*: the codes of each of ``PICKS``, for a window capped on one
        day of both dates."""
        parts = self._split(choices)
        while len(parts) == 1 and replace(parts[0], bounds=self.bounds).filters() == self.filters():
            parts = parts[0]._split(choices)
        return within(parts, self.bounds)[0] if self.bounds else parts

    def _split(self, choices: Mapping[str, Sequence[str]] | None) -> list[Window]:
        if self.level == "all":
            return [Window("years", FIRST_YEAR, _end_year(), dim=self.dim, fixed=self.fixed)]
        if self.level == "dates":  # capped by ads added since the counts
            if len(self.days) > 1:
                half = len(self.days) // 2
                return [Window("dates", dim=self.dim, fixed=self.fixed, days=self.days[:half]),
                        Window("dates", dim=self.dim, fixed=self.fixed, days=self.days[half:])]
        elif self.level != "day" and self.hi - self.lo > (0 if self.level == "days" else 1):
            mid = (self.lo + self.hi + (1 if self.level == "days" else 0)) // 2
            first_hi = mid - 1 if self.level == "days" else mid
            return [Window(self.level, self.lo, first_hi, self.month, self.dim, self.fixed),
                    Window(self.level, mid, self.hi, self.month, self.dim, self.fixed)]
        elif self.level == "years":
            return [Window("months", self.lo * 12, self.lo * 12 + 12, dim=self.dim, fixed=self.fixed)]
        elif self.level == "months":
            return [Window("days", 1, 29, self.lo, self.dim, self.fixed)]
        elif self.level == "days" and self.lo == 29 and self.month % 12 < 11:  # "29 to the next 1st": its days
            last = 31 if self.month % 12 < 6 else 30
            return [*(Window("day", day, day, self.month, self.dim, self.fixed) for day in range(29, last)),
                    Window("day", last, 32, self.month, self.dim, self.fixed)]
        other = next((d for d, (az, _) in enumerate(DIMENSIONS) if d != self.dim and az not in dict(self.fixed)), None)
        if other is not None:  # one day, still capped: split by the other date
            fixed = tuple(self.filters().items())
            start = re.match(r"(\d{4})/(\d{1,2})/", dict(fixed)[DIMENSIONS[self.dim][0]])
            if not start or int(start[1]) == FIRST_YEAR:  # the placeholder letter date: printed in any year
                return Window(dim=other, fixed=fixed).split()
            year, month = int(start[1]), int(start[2])
            # Printed after the letter: 97% within 13 months, 0.2% before it (measured on 280k ads).
            at, end = year * 12 + month - 1, _end_year() * 12
            lo, hi = (at, at + 13) if other > self.dim else (at - 12, at + 1)  # by letter date: before the print
            lo, hi = max(lo, FIRST_YEAR * 12), min(hi, end)
            return [Window("months", a, b, dim=other, fixed=fixed)
                    for a, b in ((FIRST_YEAR * 12, lo), (lo, hi), (hi, end)) if a < b]
        # Both dates fixed.  The last day to the next 1st: each day (the 1st also has its own month's).
        if self.level == "day" and self.hi == 32:
            return [replace(self, hi=self.lo), Window("day", 1, 1, self.month + 1, self.dim, self.fixed)]
        fixed = dict(self.fixed)
        for az, ta in DIMENSIONS:  # the same for an earlier date's (windows queued before 2026-11-01)
            if az in fixed and _two_days(fixed[az], fixed[ta]):
                return [replace(self, fixed=tuple({**fixed, az: day, ta: day}.items()))
                        for day in (fixed[az], fixed[ta])]
        pick = next((item for item in PICKS if item not in fixed), None)
        if pick and choices and choices.get(pick):
            return [replace(self, fixed=(*self.fixed, (pick, code))) for code in choices[pick]]
        return []

    def __str__(self) -> str:
        return ", ".join(f"{k}={v}" for k, v in self.filters().items()) or "no date filter"

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> Window:
        fields = json.loads(text)
        return cls(**{**fields, "fixed": tuple(map(tuple, fields["fixed"])), "days": tuple(fields["days"]),
                      "bounds": tuple(fields.get("bounds", ()))})


def year_bounds(text: str) -> tuple[str, str]:
    """``--years FROM-TO`` (letter-date years, both included) as ``bounds``."""
    match = re.fullmatch(r"(\d{4})-(\d{4})", text)
    if not match or not FIRST_YEAR <= int(match[1]) <= int(match[2]):
        raise ValueError(f"years: FROM-TO from {FIRST_YEAR} on, such as 1395-1405, not {text!r}")
    return f"{match[1]}/01/01", f"{int(match[2]) + 1}/01/01"


def within(windows: Iterable[Window], bounds: tuple[str, ...]) -> tuple[list[Window], list[Window]]:
    """The parts of *windows* inside the letter dates *bounds* and outside
    them (a window ending on their first day is outside: the next one has that
    day too), split without queries.  A window by newspaper date alone, or one
    whose letter dates are fixed, gets the bounds (its letter dates clamped)."""
    az, ta = DIMENSIONS[0]
    inside, outside, todo = [], [], list(windows)[::-1]
    while todo:
        window = replace(todo.pop(), bounds=())
        found = window._filters()
        if az not in found and window.dim == 0:  # the whole listing (of a status): its years
            todo += window.split()[::-1]
        elif az in found and (found[ta] <= bounds[0] or found[az] >= bounds[1]):
            outside.append(window)
        elif az in found and found[az] >= bounds[0] and found[ta] <= bounds[1]:
            inside.append(replace(window, bounds=bounds))
        elif az in found and window.dim == 0 and (parts := window.split()):
            todo += parts[::-1]
        else:
            inside.append(replace(window, bounds=bounds))
    return inside, outside


def _month_start(index: int) -> str:
    return f"{index // 12}/{index % 12 + 1:02d}/01"


def _day(index: int, day: int) -> str:
    return f"{index // 12}/{index % 12 + 1:02d}/{day:02d}"


def _two_days(az: str, ta: str) -> bool:
    """*az* to *ta* is a month's last day to the next 1st.  shortcut: Esfand's
    29 to Farvardin 1st (2 or 3 days) is left whole: no leap-year table."""
    match = re.fullmatch(r"(\d{4})/(\d\d)/(\d\d)", az)
    if not match:
        return False
    year, month, day = map(int, match.groups())
    return month < 12 and day == (31 if month <= 6 else 30) and ta == _month_start(year * 12 + month)


def undated_windows() -> list[Window]:
    """The listing of each status in ``UNDATED``: the ads without a newspaper
    date that the newspaper-date windows of a capped letter day leave out."""
    return [Window(fixed=((STATUS, status),)) for status in UNDATED]


def new_windows(newest: str | None) -> list[Window]:
    """Newspaper-date windows from NEW_MARGIN days before *newest* (the
    newest newspaper date stored) on; the whole listing if nothing is stored."""
    if not newest:
        return [Window()]
    year, month, day = map(int, newest.split("/"))
    at, day = year * 12 + month - 1, day - NEW_MARGIN
    if day < 1:  # the month before, taken as 30 days long
        at, day = at - 1, day + 30
    found = [Window("days", min(day, 29), 29, at, dim=1)]  # 29: up to the next month's 1st
    if at + 1 < _end_year() * 12:
        found.append(Window("months", at + 1, _end_year() * 12, dim=1))
    return found


def gap_windows(counts: Iterable[tuple[str, str | None, int, int]],
                budget: int = GAP_BUDGET) -> list[Window]:
    """Windows that cover every ad still missing its detail page, from the
    stored ``(letter date, newspaper date, ads, missing)`` counts.  Neighbouring
    letter dates are joined while the window's stored ads (all of them: the
    grid returns them all) stay within *budget*.  A crowded letter date such
    as the placeholder ``1300/02/01`` is never inside a range: its missing ads
    are reached by newspaper date within that day, like the full listing; its
    missing ads without a newspaper date by :func:`undated_windows`, which a
    run with these windows lists too."""
    letters: dict[str, list[int]] = {}
    papers: dict[str, list[tuple[str, int, int]]] = {}
    for letter, paper, total, missing in counts:
        sums = letters.setdefault(letter, [0, 0])
        sums[0], sums[1] = sums[0] + total, sums[1] + missing
        if paper is not None:
            papers.setdefault(letter, []).append((paper, total, missing))
    windows, undated = [], 0
    for days in _pack(sorted((d, t, m) for d, (t, m) in letters.items()), budget):
        if letters[days[0]][0] <= budget:
            windows.append(Window("dates", days=days))
            continue
        fixed = tuple(Window("dates", days=days).filters().items())
        by_paper = sorted(papers.get(days[0], []))
        windows += [Window("dates", dim=1, fixed=fixed, days=d) for d in _pack(by_paper, budget)]
        undated += letters[days[0]][1] - sum(m for _, _, m in by_paper)
    if undated:
        log.info("%d ads without detail on crowded letter dates have no newspaper date:"
                 " the statuses that hold them reach them", undated)
    return windows


def _pack(keys: list[tuple[str, int, int]], budget: int) -> list[tuple[str, ...]]:
    """Groups of the sorted *keys* (date, ads, missing) that have missing ads,
    each spanning at most *budget* ads; a date over budget is a group alone."""
    groups: list[tuple[str, ...]] = []
    days: list[str] = []
    size = gap = 0  # ads in the group; ads on later dates with nothing missing
    for key, total, missing in keys:
        if total > budget or (missing and days and size + gap + total > budget):
            if days:
                groups.append(tuple(days))
            days, size, gap = [], 0, 0
            if total > budget:
                if missing:
                    groups.append((key,))
                continue
        if missing:
            days.append(key)
            size, gap = size + gap + total, 0
        elif days:
            gap += total
    if days:
        groups.append(tuple(days))
    return groups
