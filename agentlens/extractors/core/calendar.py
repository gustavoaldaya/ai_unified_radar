"""Business-day calendar used by the fixtures harness.

Last business day (LBD) = the most recent business day strictly *before*
``run_date``. Weekends (and optional holidays) are skipped. Rationale and the
algorithm are documented in ``architecture/Fixtures Strategy``.
"""

from __future__ import annotations

from collections.abc import Container
from datetime import date, timedelta

_SATURDAY = 5


def _is_non_business(
    day: date,
    holidays: Container[date],
    *,
    mon_fri: bool,
) -> bool:
    if day in holidays:
        return True
    return mon_fri and day.weekday() >= _SATURDAY


def last_business_day(
    run_date: date,
    *,
    holidays: Container[date] = frozenset(),
    business_week_mon_fri: bool = True,
) -> date:
    """Return the LBD relative to ``run_date``.

    Examples (Mon-Fri week, no holidays):
        Tue..Fri run -> previous day
        Mon run      -> previous Friday
        Sat/Sun run  -> previous Friday
    """
    day = run_date - timedelta(days=1)
    while _is_non_business(day, holidays, mon_fri=business_week_mon_fri):
        day -= timedelta(days=1)
    return day
