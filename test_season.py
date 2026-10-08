import unittest
from datetime import date, datetime

from season import parse_settings, waiting_status, week_number, wrote_status


class WeekNumberTest(unittest.TestCase):
    START = date(2026, 9, 2)  # Season 45 week 1 (a Wednesday)

    def test_counts_weeks_from_season_start(self):
        self.assertEqual(week_number(date(2026, 9, 2), self.START), 1)
        # Regression: the week of 2026-09-09 is S45W2, not S46W1.
        self.assertEqual(week_number(date(2026, 9, 9), self.START), 2)
        self.assertEqual(week_number(date(2026, 9, 23), self.START), 4)

    def test_no_week_outside_the_season(self):
        # Before week 1 or after week 4: never roll into a season staff haven't started.
        self.assertIsNone(week_number(date(2026, 8, 26), self.START))
        self.assertIsNone(week_number(date(2026, 9, 30), self.START))


class ParseSettingsTest(unittest.TestCase):
    def test_sheets_serial_date(self):
        # Sheets serial 36526 is 2000-01-01 (a Saturday), snapped back to its Wednesday.
        self.assertEqual(parse_settings([[45], [36526]]), (45, date(1999, 12, 29)))

    def test_text_values(self):
        self.assertEqual(parse_settings([["45"], ["2026-09-02"]]), (45, date(2026, 9, 2)))
        self.assertEqual(parse_settings([[45.0], ["9/3/2026"]]), (45, date(2026, 9, 2)))

    def test_rejects_missing_or_bad_values(self):
        bad = ([], [[45]], [[], ["2026-09-02"]], [["S45"], ["2026-09-02"]],
               [[0], ["2026-09-02"]], [[45], ["next week"]])
        for values in bad:
            with self.subTest(values=values), self.assertRaises(ValueError):
                parse_settings(values)


if __name__ == "__main__":
    unittest.main()


class StatusTextTest(unittest.TestCase):
    NOW = datetime(2026, 10, 7, 23, 0)

    def test_waiting_names_the_cells_and_the_gap(self):
        text = waiting_status(date(2026, 9, 30), 45, date(2026, 9, 2), self.NOW)
        self.assertIn("2026-09-30", text)
        self.assertIn("Season 45", text)
        self.assertIn("B1 and B2", text)
        self.assertIn("2026-10-07 23:00 ET", text)

    def test_ok_names_the_sheet_and_rows(self):
        text = wrote_status("S46W1", 157, self.NOW)
        self.assertIn("S46W1", text)
        self.assertIn("157", text)
        self.assertIn("2026-10-07 23:00 ET", text)
