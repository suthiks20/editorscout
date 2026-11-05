import unittest

from affiliation import analyze_affiliation


class AffiliationTests(unittest.TestCase):
    def test_current_affiliation_from_present_range_and_fragments(self):
        result = analyze_affiliation(
            "Video Editor at Acme",
            "Experience: Acme 2023 - Present | Education: PSG College",
            "Acme",
            current_year=2026,
        )
        self.assertTrue(result["org_match"])
        self.assertEqual(result["status_hint"], "current")
        self.assertEqual(result["years"], {"start": 2023, "end": "Present"})
        self.assertEqual(result["experience"], "Acme 2023 - Present")
        self.assertEqual(result["education"], "PSG College")

    def test_past_affiliation_with_ended_year(self):
        result = analyze_affiliation(
            "Former editor",
            "Acme 2003 - 2005",
            "Acme",
            current_year=2026,
        )
        self.assertEqual(result["status_hint"], "past")
        self.assertEqual(result["years"], {"start": 2003, "end": 2005})
        self.assertEqual(result["years_text"], "2003 - 2005")

    def test_affiliation_without_dates_is_unclear(self):
        result = analyze_affiliation("Editor at Acme", "Creative professional", "Acme")
        self.assertEqual(result["status_hint"], "unclear")
        self.assertIsNone(result["years"])

    def test_mismatched_organisation_does_not_infer_status(self):
        result = analyze_affiliation(
            "Former editor at Another Co",
            "Another Co 2003 - 2005",
            "Acme",
            current_year=2026,
        )
        self.assertFalse(result["org_match"])
        self.assertEqual(result["status_hint"], "unclear")


if __name__ == "__main__":
    unittest.main()
