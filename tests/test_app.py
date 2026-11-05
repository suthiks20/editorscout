import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app import build_query, create_app, make_draft
from database import connect


class EditorScoutTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = str(Path(self.temp_dir.name) / "test.sqlite3")
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-key",
                "DATABASE_PATH": self.database_path,
                "GOOGLE_API_KEY": "test-api-key",
                "GOOGLE_CX": "test-cx",
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

    @patch("app.requests.get")
    def test_search_uses_google_start_index_and_hides_saved_profiles(self, mock_get):
        item = {
            "title": "A Person - Video Editor",
            "link": "https://www.linkedin.com/in/a-person",
            "snippet": "Edits short films.",
        }
        mock_get.return_value = Mock(
            status_code=200,
            ok=True,
            json=lambda: {
                "items": [item],
                "searchInformation": {"totalResults": "30"},
                "queries": {"nextPage": [{"startIndex": 11}]},
            },
        )
        with connect(self.database_path) as connection:
            connection.execute(
                "INSERT INTO people (name, url) VALUES (?, ?)",
                ("A Person", item["link"]),
            )

        response = self.client.post(
            "/search",
            data={
                "role": "video editor",
                "city": "Coimbatore",
                "exclude_words": "recruiter",
                "per_page": "10",
                "page": "2",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No new profiles on this page", response.data)
        self.assertNotIn(b"A Person", response.data)
        self.assertEqual(mock_get.call_args.kwargs["params"]["start"], 11)

    @patch("app.requests.get")
    def test_quota_error_is_explained(self, mock_get):
        mock_get.return_value = Mock(
            status_code=403,
            ok=False,
            json=lambda: {
                "error": {
                    "errors": [{"reason": "dailyLimitExceeded"}],
                }
            },
        )
        response = self.client.post(
            "/search", data={"role": "reel editor", "city": "Coimbatore"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"daily quota is exhausted", response.data)

    def test_shortlist_accepts_only_linkedin_profile_urls(self):
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
        response = self.client.post(
            "/shortlist", data={"profile": json.dumps(profile)}
        )
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

    def test_connection_draft_is_under_300_characters(self):
        draft = make_draft(
            "connection",
            "A Person",
            "a very thoughtful edit " * 40,
            "IncuBrix supports creators.",
            "Let's talk.",
        )
        self.assertLess(len(draft), 300)
        self.assertIn("A Person", draft)

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
