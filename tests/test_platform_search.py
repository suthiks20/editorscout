import unittest

from platform_search import (
    build_platform_queries,
    classify_link,
    extract_instagram_handle,
    match_confidence,
    normalize_instagram_handle,
)


class PlatformSearchTests(unittest.TestCase):
    def test_build_platform_queries_quotes_and_escapes_values(self):
        queries = build_platform_queries(
            'Jane "J" Doe',
            "Acme Studio",
            "Coimbatore",
            "video editor",
            ("linkedin", "instagram", "youtube"),
        )
        self.assertEqual(
            queries["linkedin"],
            'site:linkedin.com/in "Jane \\"J\\" Doe" "Acme Studio" "Coimbatore" "video editor"',
        )
        self.assertIn('site:instagram.com "Jane \\"J\\" Doe" "Acme Studio"', queries["instagram"])
        self.assertIn("(video editor OR reel)", queries["youtube"])

    def test_build_platform_queries_removes_controls_and_limits_inputs(self):
        query = build_platform_queries(
            "Jane\nDoe", "", "", "", ("linkedin",)
        )["linkedin"]
        self.assertIn('"Jane Doe"', query)
        with self.assertRaises(ValueError):
            build_platform_queries("x" * 101, "", "", "", ("youtube",))

    def test_classifies_platform_link_kinds(self):
        cases = {
            "https://www.instagram.com/editor.name/": ("instagram", "profile"),
            "https://www.instagram.com/p/ABC123/": ("instagram", "post"),
            "https://instagram.com/reel/ABC123/": ("instagram", "reel"),
            "https://instagram.com/explore/tags/video/": ("instagram", "other"),
            "https://www.youtube.com/@editor": ("youtube", "channel"),
            "https://www.youtube.com/channel/UCabc": ("youtube", "channel"),
            "https://www.youtube.com/c/editor": ("youtube", "channel"),
            "https://www.youtube.com/watch?v=abc": ("youtube", "video"),
            "https://youtu.be/abc": ("youtube", "video"),
            "https://youtube.com/shorts/abc": ("youtube", "video"),
            "https://in.linkedin.com/in/Jane-123?trk=x": ("linkedin", "profile"),
            "https://www.instagram.com/accounts/login/": (None, None),
            "https://www.youtube.com/": (None, None),
            "https://notyoutube.com/watch?v=abc": (None, None),
            "https://example.com/@editor": (None, None),
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(classify_link(url), expected)

    def test_extracts_and_validates_instagram_handles(self):
        self.assertEqual(
            extract_instagram_handle("Jane Doe (@jane.editor_1) • Instagram"),
            "jane.editor_1",
        )
        self.assertEqual(
            extract_instagram_handle("jane_editor on Instagram: photos"),
            "jane_editor",
        )
        self.assertEqual(
            extract_instagram_handle("", "https://instagram.com/Jane.Editor/"),
            "Jane.Editor",
        )
        self.assertIsNone(normalize_instagram_handle("@bad/handle"))
        self.assertIsNone(normalize_instagram_handle("x" * 31))
        self.assertIsNone(extract_instagram_handle("No Instagram handle"))

    def test_match_confidence_handles_accents_missing_org_and_partial_names(self):
        self.assertEqual(
            match_confidence("José Álvarez", "Acme Studio", "Jose Alvarez", "Acme Studio"),
            "likely",
        )
        self.assertEqual(
            match_confidence("Jane Doe", "Acme", "Jane Doe - Editor", "video"),
            "possible",
        )
        self.assertEqual(
            match_confidence("Jane Doe", "Acme", "Jane Editor", "Acme Studio"),
            "unsure",
        )
        self.assertEqual(
            match_confidence("Jane Doe", "", "Jane Doe", ""),
            "likely",
        )


if __name__ == "__main__":
    unittest.main()
