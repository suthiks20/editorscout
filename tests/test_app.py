import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import build_query, create_app
from database import connect
from search_provider import (
    SearchAPIError,
    SearchConnectionError,
    SearchResponseError,
    SearchTimeoutError,
)


class EditorScoutTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = str(Path(self.temp_dir.name) / "test.sqlite3")
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-key",
                "DATABASE_PATH": self.database_path,
                "SERPER_API_KEY": "test-api-key",
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_primary_pages_render(self):
        for path in ("/", "/shortlist", "/tracker"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)

    def test_build_query_quotes_role_and_excludes_each_term(self):
        query = build_query(
            {
                "role": "video editor",
                "city": "Coimbatore",
                "college": "PSG College",
                "exclude_words": "recruiter, hiring",
            }
        )
        self.assertEqual(
            query,
            'site:linkedin.com/in "video editor" Coimbatore "PSG College" -recruiter -hiring',
        )

    @patch("app.search_web")
    def test_search_shows_results_and_passes_page_number(self, mock_search):
        mock_search.return_value = [
            {
                "title": "A Person - Video Editor",
                "link": "https://www.linkedin.com/in/a-person",
                "snippet": "Edits short films.",
            }
        ]
        response = self.client.post(
            "/search",
            data={
                "role": "video editor",
                "city": "Coimbatore",
                "exclude_words": "recruiter",
                "per_page": "5",
                "page": "2",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"A Person", response.data)
        self.assertIn(b"Edits short films.", response.data)
        mock_search.assert_called_once_with(
            'site:linkedin.com/in "video editor" Coimbatore -recruiter',
            num=5,
            page=2,
        )

    @patch("app.search_web")
    def test_search_hides_already_saved_profiles(self, mock_search):
        url = "https://www.linkedin.com/in/saved-person"
        mock_search.return_value = [
            {"title": "Saved Person", "link": url, "snippet": "Already saved."}
        ]
        with connect(self.database_path) as connection:
            connection.execute(
                "INSERT INTO people (name, url) VALUES (?, ?)", ("Saved Person", url)
            )
        response = self.client.post(
            "/search", data={"role": "video editor", "city": "Coimbatore"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"Saved Person", response.data)
        self.assertIn(b"No new profiles on this page", response.data)

    @patch("app.search_web", side_effect=SearchAPIError(429))
    def test_rate_limit_error_is_explained(self, _mock_search):
        response = self.client.post(
            "/search", data={"role": "reel editor", "city": "Coimbatore"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"rate limit reached or search credits exhausted", response.data)

    @patch("app.search_web", side_effect=SearchAPIError(403))
    def test_invalid_key_error_is_explained(self, _mock_search):
        response = self.client.post(
            "/search", data={"role": "reel editor", "city": "Coimbatore"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Check SERPER_API_KEY", response.data)

    @patch(
        "app.search_web",
        side_effect=RuntimeError("Add SERPER_API_KEY to your .env file to search."),
    )
    def test_missing_key_error_is_explained(self, _mock_search):
        response = self.client.post(
            "/search", data={"role": "reel editor", "city": "Coimbatore"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Add SERPER_API_KEY", response.data)

    @patch("app.search_web", side_effect=SearchTimeoutError())
    def test_timeout_error_is_explained(self, _mock_search):
        response = self.client.post(
            "/search", data={"role": "reel editor", "city": "Coimbatore"}
        )
        self.assertIn(b"Serper search timed out", response.data)

    @patch("app.search_web", side_effect=SearchConnectionError())
    def test_connection_error_is_explained(self, _mock_search):
        response = self.client.post(
            "/search", data={"role": "reel editor", "city": "Coimbatore"}
        )
        self.assertIn(b"Could not connect to Serper", response.data)

    @patch("app.search_web", side_effect=SearchResponseError())
    def test_unreadable_response_error_is_explained(self, _mock_search):
        response = self.client.post(
            "/search", data={"role": "reel editor", "city": "Coimbatore"}
        )
        self.assertIn(b"Serper returned an unreadable response", response.data)

    @patch("app.search_web")
    def test_regional_linkedin_urls_are_normalized_and_saved(self, mock_search):
        mock_search.return_value = [
            {
                "title": "A Person - Video Editor",
                "link": "https://in.linkedin.com/in/a-person/?trk=public",
                "snippet": "Edits short films.",
            }
        ]
        response = self.client.post(
            "/search", data={"role": "video editor", "city": "Coimbatore"}
        )
        canonical_url = "https://www.linkedin.com/in/a-person"
        self.assertEqual(response.status_code, 200)
        self.assertIn(canonical_url.encode(), response.data)
        profile = {
            "name": "A Person",
            "url": "https://in.linkedin.com/in/a-person/",
        }
        response = self.client.post("/shortlist", data={"profile": json.dumps(profile)})
        self.assertEqual(response.status_code, 302)
        with connect(self.database_path) as connection:
            saved_url = connection.execute(
                "SELECT url FROM people WHERE name = ?", ("A Person",)
            ).fetchone()["url"]
        self.assertEqual(saved_url, canonical_url)

    def test_non_linkedin_urls_are_rejected_from_shortlist(self):
        response = self.client.post(
            "/shortlist",
            data={"profile": json.dumps({"url": "https://example.com/not-linkedin"})},
        )
        self.assertEqual(response.status_code, 400)
        with connect(self.database_path) as connection:
            count = connection.execute("SELECT COUNT(*) FROM people").fetchone()[0]
        self.assertEqual(count, 0)

    def test_shortlist_note_and_draft_workflow(self):
        profile = {
            "name": "A Person",
            "headline": "Video editor",
            "snippet": "Creates reels.",
            "url": "https://www.linkedin.com/in/a-person",
            "college": "PSG",
        }
        response = self.client.post("/shortlist", data={"profile": json.dumps(profile)})
        self.assertEqual(response.status_code, 302)
        with connect(self.database_path) as connection:
            person_id = connection.execute("SELECT id FROM people").fetchone()["id"]
        response = self.client.post(
            f"/people/{person_id}/save",
            data={"personal_note": "the pacing in their travel reel"},
        )
        self.assertEqual(response.status_code, 302)
        response = self.client.post(
            f"/people/{person_id}/draft", data={"channel": "connection"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"pacing in their travel reel", response.data)
        self.assertIn(b"does not send anything", response.data)
        with connect(self.database_path) as connection:
            message = connection.execute(
                "SELECT channel, draft_text FROM messages WHERE person_id = ?",
                (person_id,),
            ).fetchone()
        self.assertEqual(message["channel"], "connection")
        self.assertLess(len(message["draft_text"]), 300)

    def test_tracker_warns_after_more_than_ten_are_marked_sent(self):
        with connect(self.database_path) as connection:
            for index in range(11):
                connection.execute(
                    "INSERT INTO people (name, url) VALUES (?, ?)",
                    (
                        f"Person {index}",
                        f"https://www.linkedin.com/in/person-{index}",
                    ),
                )
            people = connection.execute("SELECT id FROM people").fetchall()
        for person in people:
            response = self.client.post(
                f"/people/{person['id']}/update",
                data={"status": "sent", "personal_note": ""},
            )
            self.assertEqual(response.status_code, 302)
        response = self.client.get("/tracker")
        self.assertIn(b"11", response.data)
        self.assertIn(b"You've passed 10 today", response.data)

    def test_csv_export_escapes_spreadsheet_formulas(self):
        with connect(self.database_path) as connection:
            connection.execute(
                "INSERT INTO people (name, url, personal_note) VALUES (?, ?, ?)",
                ("=1+1", "https://www.linkedin.com/in/csv-test", "@SUM(A1:A2)"),
            )
        response = self.client.get("/export.csv")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"'=1+1", response.data)
        self.assertIn(b"'@SUM(A1:A2)", response.data)


if __name__ == "__main__":
    unittest.main()
