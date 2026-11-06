import unittest

from messaging import extract_first_name, render_message


class MessagingTests(unittest.TestCase):
    def test_first_name_removes_titles_initials_emoji_and_punctuation(self):
        examples = {
            "Dr. A. 🎬 Priya!": "Priya",
            "Mr 🎥 Kumar": "Kumar",
            "Ms. Maya,": "Maya",
            "🎞️ Ravi": "Ravi",
        }
        for name, expected in examples.items():
            with self.subTest(name=name):
                self.assertEqual(extract_first_name(name), expected)

    def test_empty_values_are_removed_without_dangling_phrases_or_braces(self):
        person = {
            "name": "A Person",
            "org_name": "",
            "headline": "",
            "personal_note": "",
        }
        rendered, unknown, empty = render_message(
            "Hi {first_name}, I liked your work on {specific_note}. "
            "I saw you at {organisation}.",
            person,
            {},
        )
        self.assertEqual(rendered, "Hi Person. I saw you.")
        self.assertEqual(unknown, [])
        self.assertEqual(empty, ["organisation", "specific_note"])
        self.assertNotIn("{", rendered)
        self.assertNotIn(" at ", rendered)

    def test_unknown_placeholders_are_reported_and_removed(self):
        rendered, unknown, _empty = render_message(
            "Hello {first_name}, {unknown_token} {not a token}.",
            {
                "name": "Jane Doe",
                "org_name": "",
                "headline": "",
                "personal_note": "",
            },
            {},
        )
        self.assertEqual(unknown, ["not a token", "unknown_token"])
        self.assertNotIn("{", rendered)
        self.assertNotIn("}", rendered)

    def test_rendered_message_uses_available_person_fields(self):
        rendered, unknown, _empty = render_message(
            "Hi {first_name} ({name}), {role} at {organisation}: {specific_note}. "
            "{my_name} {link}",
            {
                "name": "Jane Doe",
                "org_name": "Acme Studio",
                "headline": "Video editor",
                "personal_note": "the pacing",
            },
            {"my_name": "Sam", "link": "https://example.test"},
        )
        self.assertEqual(
            rendered,
            "Hi Jane (Jane Doe), Video editor at Acme Studio: the pacing. "
            "Sam https://example.test",
        )
        self.assertEqual(unknown, [])


if __name__ == "__main__":
    unittest.main()
