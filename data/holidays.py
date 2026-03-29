"""A-share market holiday calendar.

Maintains a static set of known non-trading dates (holidays + weekends).
Used by the monitor to skip launching on non-trading days.
Update this list annually around December when the next year's schedule is published.
"""

from datetime import date, datetime

# 2026 A-share holidays (confirmed by CSRC / exchange announcements)
# Format: (month, day) for single days, or ranges via _range helper
HOLIDAYS_2026 = {
    # 元旦 New Year
    date(2026, 1, 1), date(2026, 1, 2),
    # 春节 Spring Festival
    date(2026, 2, 14), date(2026, 2, 15), date(2026, 2, 16),
    date(2026, 2, 17), date(2026, 2, 18), date(2026, 2, 19), date(2026, 2, 20),
    # 清明节 Qingming
    date(2026, 4, 4), date(2026, 4, 5), date(2026, 4, 6),
    # 劳动节 Labor Day
    date(2026, 5, 1), date(2026, 5, 2), date(2026, 5, 3),
    date(2026, 5, 4), date(2026, 5, 5),
    # 端午节 Dragon Boat
    date(2026, 5, 31), date(2026, 6, 1),
    # 中秋节 Mid-Autumn + 国庆节 National Day
    date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3),
    date(2026, 10, 4), date(2026, 10, 5), date(2026, 10, 6),
    date(2026, 10, 7), date(2026, 10, 8),
}

ALL_HOLIDAYS = HOLIDAYS_2026


def is_trading_day(d: date | None = None) -> bool:
    """Check if a given date is a trading day (not weekend, not holiday)."""
    if d is None:
        d = date.today()
    if d.weekday() >= 5:
        return False
    return d not in ALL_HOLIDAYS
