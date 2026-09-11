import unittest
from datetime import date

from season import parse_settings, week_number


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
