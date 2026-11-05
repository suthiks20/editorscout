import unittest

from profile_urls import normalize_linkedin_url, parse_person_input


class ProfileUrlTests(unittest.TestCase):
    def test_normalizes_regional_urls_tracking_case_and_trailing_slash(self):
        self.assertEqual(
            normalize_linkedin_url(
                "https://in.linkedin.com/in/Jane%2DEditor-123/?trk=public_profile"
            ),
            "https://www.linkedin.com/in/jane-editor-123",
        )

    def test_specific_person_input_recognizes_urls_and_encoded_slugs(self):
        parsed = parse_person_input(
            "in.linkedin.com/in/Jane%2DEditor-123/?utm_source=search"
        )
        self.assertEqual(parsed["kind"], "profile")
        self.assertEqual(parsed["slug"], "jane-editor-123")
        self.assertEqual(
            parsed["url"], "https://www.linkedin.com/in/jane-editor-123"
        )

    def test_specific_person_input_recognizes_bare_linkedin_ids(self):
        parsed = parse_person_input("Editor-ID-123")
        self.assertEqual(parsed["kind"], "profile")
        self.assertEqual(parsed["slug"], "editor-id-123")

    def test_specific_person_input_keeps_plain_names_as_names(self):
        self.assertEqual(
            parse_person_input("Jane Editor")["kind"],
            "name",
        )

    def test_invalid_linkedin_profile_url_is_not_treated_as_a_name(self):
        self.assertEqual(
            parse_person_input("https://www.linkedin.com/company/acme")["kind"],
            "invalid",
        )


if __name__ == "__main__":
    unittest.main()
