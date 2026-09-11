"""
Season/week numbering for the stats sheet.

Staff set the current season number and its week 1 start date in the
'Season Settings' tab. The bot counts weeks 1-4 from that date and never
starts a new season on its own.
"""
from datetime import date, datetime, timedelta

WEEKS_PER_SEASON = 4
SETTINGS_SHEET_NAME = "Season Settings"
SETTINGS_RANGE = f"'{SETTINGS_SHEET_NAME}'!B1:B2"  # B1: season number, B2: week 1 start date
SHEETS_EPOCH = date(1899, 12, 30)  # Google Sheets date serial 0
DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y")


def week_start_of(day):
    """Snap a date back to its Wednesday, the bot's week boundary."""
    return day - timedelta(days=(day.weekday() - 2) % 7)


def week_number(week_start, season_start):
    """Return the 1-based week of the season for week_start, or None if it falls outside the season."""
    index = (week_start - season_start).days // 7
    return index + 1 if 0 <= index < WEEKS_PER_SEASON else None


def _parse_season(raw):
    if isinstance(raw, float) and raw.is_integer():
        raw = int(raw)
    text = str(raw).strip()
    if not text.isdigit() or int(text) < 1:
        raise ValueError(f"'{SETTINGS_SHEET_NAME}'!B1 must be the season number, got {raw!r}")
    return int(text)


def _parse_date(raw):
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return SHEETS_EPOCH + timedelta(days=int(raw))
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(str(raw).strip(), fmt).date()
        except ValueError:
            pass
    raise ValueError(f"'{SETTINGS_SHEET_NAME}'!B2 must be the season's week 1 start date, got {raw!r}")


def parse_settings(values):
    """
    Validate the raw [[B1], [B2]] values read from SETTINGS_RANGE.
    Returns (season, season_start) with season_start snapped to its Wednesday.
    """
    cells = [row[0] if row else None for row in values[:2]]
    cells += [None] * (2 - len(cells))
    return _parse_season(cells[0]), week_start_of(_parse_date(cells[1]))
