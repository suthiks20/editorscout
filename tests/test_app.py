import json
import sqlite3
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from app import build_query, create_app, normalize_filters
from database import connect, init_db
from openpyxl import load_workbook
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
                "org_type": "college",
                "org_name": "PSG College",
                "filter_status": "any",
                "batch_year": "",
                "exclude_words": "recruiter, hiring",
            }
        )
        self.assertEqual(
            query,
            'site:linkedin.com/in "video editor" Coimbatore "PSG College" -recruiter -hiring',
        )

    def test_build_query_adds_status_and_type_specific_year_hints(self):
        base = {
            "role": "video editor",
            "city": "Coimbatore",
            "org_name": "Acme Studio",
            "exclude_words": "",
        }
        college_query = build_query(
            {
                **base,
                "org_type": "college",
                "filter_status": "current",
                "batch_year": "2027",
            }
        )
        self.assertIn('"Acme Studio" Present 2027', college_query)
        company_query = build_query(
            {
                **base,
                "org_type": "company",
                "filter_status": "current",
                "batch_year": "2027",
            }
        )
        self.assertIn('"Acme Studio" Present', company_query)
        self.assertNotIn("2027", company_query)
        school_query = build_query(
            {
                **base,
                "org_type": "school",
                "filter_status": "past",
                "batch_year": "2027",
            }
        )
        self.assertIn('"Acme Studio" -Present', school_query)
        self.assertNotIn("2027", school_query)

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
    def test_search_posts_and_preserves_organisation_filter_values(self, mock_search):
        mock_search.return_value = [
            {
                "title": "A Person - Video Editor",
                "link": "https://www.linkedin.com/in/a-person",
                "snippet": "Experience: PSG College of Technology 2023 - Present",
            }
        ]
        response = self.client.post(
            "/search",
            data={
                "role": "video editor",
                "city": "Coimbatore",
                "org_type": "college",
                "org_name": "PSG College of Technology",
                "filter_status": "current",
                "batch_year": "2027",
                "exclude_words": "recruiter",
                "per_page": "5",
                "page": "1",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'name="org_type" id="org-type"', response.data)
        self.assertIn(b'value="PSG College of Technology"', response.data)
        self.assertIn(b'name="filter_status" id="filter-status"', response.data)
        self.assertIn(b'name="batch_year"', response.data)
        self.assertIn(b'value="2027"', response.data)
        self.assertIn(b"Current per snippet", response.data)
        self.assertIn(b"Search results can't confirm identity or dates", response.data)
        self.assertEqual(
            mock_search.call_args.args[0],
            'site:linkedin.com/in "video editor" Coimbatore '
            '"PSG College of Technology" Present 2027 -recruiter',
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
            "org_type": "college",
            "org_name": "PSG",
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
        self.assertIn(b"not verified yet", response.data.lower())

    def test_unverified_connection_draft_shows_warning_without_blocking(self):
        with connect(self.database_path) as connection:
            connection.execute(
                """INSERT INTO people (name, url, status, personal_note)
                   VALUES (?, ?, 'shortlisted', ?)""",
                (
                    "A Person",
                    "https://www.linkedin.com/in/a-person",
                    "their color grading",
                ),
            )
            person_id = connection.execute(
                "SELECT id FROM people"
            ).fetchone()["id"]
        response = self.client.post(
            f"/people/{person_id}/draft", data={"channel": "connection"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"not verified yet", response.data.lower())
        with connect(self.database_path) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM messages WHERE person_id = ?", (person_id,)
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_verified_status_is_saved_filtered_and_exported(self):
        profile = {
            "name": "A Person",
            "url": "https://www.linkedin.com/in/a-person",
            "org_type": "company",
            "org_name": "Acme Studio",
            "status_hint": "current",
            "years_text": "2023 - Present",
        }
        self.client.post("/shortlist", data={"profile": json.dumps(profile)})
        with connect(self.database_path) as connection:
            person_id = connection.execute("SELECT id FROM people").fetchone()["id"]
        response = self.client.post(
            f"/people/{person_id}/save",
            data={
                "personal_note": "their recent reel",
                "verified_status": "current",
                "verified_note": "Profile says current editor",
            },
        )
        self.assertEqual(response.status_code, 302)
        tracker = self.client.get("/tracker?verified_current=1")
        self.assertIn(b"Acme Studio", tracker.data)
        self.assertIn(b"Company", tracker.data)
        self.assertIn(b"Manually verified: Current", tracker.data)
        export = self.client.get("/export.csv")
        self.assertIn(b"Organisation type", export.data)
        self.assertIn(b"Organisation name", export.data)
        self.assertIn(b"Verified status", export.data)
        self.assertIn(b"Acme Studio", export.data)
        self.assertIn(b"Profile says current editor", export.data)

    def test_excel_export_workbook_and_filtering(self):
        with connect(self.database_path) as connection:
            connection.execute(
                """INSERT INTO people
                    (name, headline, snippet, url, org_type, org_name, status_hint,
                     years_text, verified_status, verified_note, status, personal_note,
                     created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "=1+1",
                    "Video editor",
                    "+SUM(A1:A2)",
                    "https://www.linkedin.com/in/excel-person",
                    "company",
                    "Acme Studio",
                    "current",
                    "2023 - Present",
                    "current",
                    "@verified note",
                    "sent",
                    "-personal note",
                    "2026-11-06T09:30:00Z",
                    "2026-11-06T10:30:00Z",
                ),
            )
            person_id = connection.execute(
                "SELECT id FROM people WHERE name = ?", ("=1+1",)
            ).fetchone()["id"]
            connection.execute(
                "INSERT INTO messages (person_id, channel, draft_text) VALUES (?, ?, ?)",
                (person_id, "connection", "=HYPERLINK(\"bad\")"),
            )
            connection.execute(
                """INSERT INTO people
                    (name, url, org_type, org_name, verified_status, status)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    "Unverified person",
                    "https://www.linkedin.com/in/unverified-person",
                    "company",
                    "Acme Studio",
                    "unverified",
                    "sent",
                ),
            )

        response = self.client.get("/export.xlsx?status=sent&verified_current=1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.mimetype,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertIn(b"editorscout_", response.headers["Content-Disposition"].encode())
        workbook = load_workbook(BytesIO(response.data), data_only=False)
        self.assertEqual(workbook.sheetnames, ["Outreach", "Summary"])
        outreach = workbook["Outreach"]
        expected_headers = [
            "Name",
            "Headline",
            "Profile URL",
            "Organisation type",
            "Organisation name",
            "Status hint",
            "Years",
            "Verified status",
            "Verified note",
            "Outreach status",
            "Personal note",
            "Draft",
            "Created",
            "Updated",
            "Batch",
        ]
        self.assertEqual(
            [cell.value for cell in outreach[1]], expected_headers
        )
        self.assertEqual(outreach.max_row, 2)
        self.assertEqual(outreach["A2"].value, "'=1+1")
        self.assertEqual(outreach["I2"].value, "'@verified note")
        self.assertEqual(outreach["K2"].value, "'-personal note")
        self.assertEqual(outreach["L2"].value, "'=HYPERLINK(\"bad\")")
        self.assertEqual(outreach["F2"].value, "current")
        self.assertEqual(
            outreach["C2"].hyperlink.target,
            "https://www.linkedin.com/in/excel-person",
        )
        self.assertTrue(outreach.freeze_panes == "A2")
        self.assertTrue(outreach.auto_filter.ref.startswith("A1:O"))
        validations = list(outreach.data_validations.dataValidation)
        self.assertEqual(len(validations), 2)
        self.assertTrue(any("J2:J2" in validation.sqref for validation in validations))
        self.assertTrue(any("H2:H2" in validation.sqref for validation in validations))
        summary = workbook["Summary"]
        formulas = [
            cell.value
            for row in summary.iter_rows()
            for cell in row
            if isinstance(cell.value, str) and cell.value.startswith("=")
        ]
        self.assertTrue(any("COUNTIF(Outreach!J:J,\"sent\")" in f for f in formulas))
        self.assertTrue(any("COUNTIF(Outreach!H:H,\"current\")" in f for f in formulas))
        self.assertEqual(sum(f.startswith("=IFERROR(") for f in formulas), 2)
        self.assertTrue(any("replied" in f and "sent" in f for f in formulas))
        self.assertTrue(any("agreed" in f and "sent" in f for f in formulas))

    def test_csv_export_respects_active_tracker_filters(self):
        with connect(self.database_path) as connection:
            connection.execute(
                """INSERT INTO people (name, url, status, verified_status)
                   VALUES ('Current sent', 'https://www.linkedin.com/in/current-sent',
                           'sent', 'current')"""
            )
            connection.execute(
                """INSERT INTO people (name, url, status, verified_status)
                   VALUES ('Unverified sent', 'https://www.linkedin.com/in/unverified-sent',
                           'sent', 'unverified')"""
            )
            connection.execute(
                """INSERT INTO people (name, url, status, verified_status)
                   VALUES ('Current replied', 'https://www.linkedin.com/in/current-replied',
                           'replied', 'current')"""
            )
        response = self.client.get("/export.csv?status=sent&verified_current=1")
        self.assertIn(b"Current sent", response.data)
        self.assertNotIn(b"Unverified sent", response.data)
        self.assertNotIn(b"Current replied", response.data)

    def test_init_db_migrates_existing_college_column_safely(self):
        legacy_path = str(Path(self.temp_dir.name) / "legacy.sqlite3")
        connection = sqlite3.connect(legacy_path)
        connection.executescript(
            """CREATE TABLE people (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                headline TEXT NOT NULL DEFAULT '',
                snippet TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL UNIQUE,
                college TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                personal_note TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'new',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                sent_at TEXT
            );
            INSERT INTO people (name, url, college)
            VALUES ('Legacy Person', 'https://www.linkedin.com/in/legacy', 'PSG College');"""
        )
        connection.commit()
        connection.close()

        init_db(legacy_path)
        init_db(legacy_path)

        with connect(legacy_path) as migrated:
            person = migrated.execute(
                "SELECT * FROM people WHERE name = 'Legacy Person'"
            ).fetchone()
            columns = {
                row["name"] for row in migrated.execute("PRAGMA table_info(people)")
            }
        self.assertEqual(person["org_name"], "PSG College")
        self.assertEqual(person["org_type"], "College")
        self.assertEqual(person["verified_status"], "unverified")
        self.assertTrue(
            {
                "org_type",
                "org_name",
                "status_hint",
                "years_text",
                "verified_status",
                "verified_note",
            }.issubset(columns)
        )
        self.assertIsNotNone(person["batch_id"])
        with connect(legacy_path) as migrated:
            batch = migrated.execute(
                "SELECT name, is_current FROM batches WHERE id = ?",
                (person["batch_id"],),
            ).fetchone()
        self.assertEqual(batch["name"], "Earlier outreach")
        self.assertEqual(batch["is_current"], 0)

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
                "INSERT INTO people (name, url, snippet, personal_note) VALUES (?, ?, ?, ?)",
                (
                    "=1+1",
                    "https://www.linkedin.com/in/csv-test",
                    "+SUM(A1:A2)",
                    "@SUM(A1:A2)",
                ),
            )
        response = self.client.get("/export.csv")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"'=1+1", response.data)
        self.assertIn(b"'+SUM(A1:A2)", response.data)
        self.assertIn(b"'@SUM(A1:A2)", response.data)

    def test_shortlist_creates_batch_and_only_current_batch_is_shown(self):
        first = {"name": "First", "url": "https://www.linkedin.com/in/first-123"}
        self.client.post("/shortlist", data={"profile": json.dumps(first)})
        with connect(self.database_path) as connection:
            first_batch = connection.execute(
                "SELECT batch_id FROM people WHERE name = 'First'"
            ).fetchone()["batch_id"]
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM batches WHERE is_current = 1"
                ).fetchone()[0],
                1,
            )
        self.assertIn(b"First", self.client.get("/shortlist").data)

        self.client.post("/batches/new")
        second = {"name": "Second", "url": "https://www.linkedin.com/in/second-123"}
        self.client.post("/shortlist", data={"profile": json.dumps(second)})
        shortlist = self.client.get("/shortlist")
        self.assertIn(b"Second", shortlist.data)
        self.assertNotIn(b">First<", shortlist.data)
        tracker = self.client.get("/tracker")
        self.assertIn(b"First", tracker.data)
        self.assertIn(b"Second", tracker.data)
        only_first = self.client.get(f"/tracker?batch={first_batch}")
        self.assertIn(b"First", only_first.data)
        self.assertNotIn(b"Second", only_first.data)
        current_only = self.client.get("/tracker?batch=current")
        self.assertIn(b"Second", current_only.data)
        self.assertNotIn(b"First", current_only.data)

    def test_remove_from_shortlist_keeps_tracker_history(self):
        profile = {"name": "Keep history", "url": "https://www.linkedin.com/in/keep-history-123"}
        self.client.post("/shortlist", data={"profile": json.dumps(profile)})
        with connect(self.database_path) as connection:
            person_id = connection.execute(
                "SELECT id FROM people WHERE name = 'Keep history'"
            ).fetchone()["id"]
        response = self.client.post(f"/people/{person_id}/remove")
        self.assertEqual(response.status_code, 302)
        self.assertNotIn(b"Keep history", self.client.get("/shortlist").data)
        tracker = self.client.get("/tracker")
        self.assertIn(b"Keep history", tracker.data)
        self.assertIn(b"Removed from shortlist", tracker.data)
        with connect(self.database_path) as connection:
            person = connection.execute(
                "SELECT batch_id, removed_from_shortlist FROM people WHERE id = ?",
                (person_id,),
            ).fetchone()
        self.assertIsNotNone(person["batch_id"])
        self.assertEqual(person["removed_from_shortlist"], 1)

    @patch("app.search_web")
    def test_browse_mode_can_show_saved_profiles_with_shortlist_disabled(self, mock_search):
        url = "https://www.linkedin.com/in/saved-visible-123"
        mock_search.return_value = [{
            "title": "Saved Visible",
            "link": "https://in.linkedin.com/in/Saved-Visible-123/?trk=x",
            "snippet": "Video editor",
        }]
        with connect(self.database_path) as connection:
            connection.execute(
                "INSERT INTO people (name, url, status) VALUES (?, ?, ?)",
                ("Saved Visible", url, "sent"),
            )
        response = self.client.post(
            "/search",
            data={
                "role": "video editor",
                "city": "Coimbatore",
                "show_saved": "1",
            },
        )
        self.assertIn(b"Saved Visible", response.data)
        self.assertIn(b"1 already saved shown", response.data)
        self.assertIn(b"Already in tracker:", response.data)
        self.assertIn(b'class="profile-check" type="checkbox" name="profile"', response.data)
        self.assertIn(b"disabled", response.data)

    def test_shortlist_deduplicates_normalized_profile_urls(self):
        first = {
            "name": "First saved",
            "url": "https://in.linkedin.com/in/Dupe-Person-123/?trk=first",
        }
        second = {
            "name": "Duplicate saved",
            "url": "https://www.linkedin.com/in/dupe-person-123?utm_source=second",
        }
        self.client.post("/shortlist", data={"profile": json.dumps(first)})
        self.client.post("/shortlist", data={"profile": json.dumps(second)})
        with connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM people").fetchone()[0],
                1,
            )

    @patch("app.search_web")
    def test_find_person_url_query_and_saved_badge(self, mock_search):
        mock_search.return_value = [{
            "title": "Already Saved - Video Editor",
            "link": "https://www.linkedin.com/in/already-saved-123",
            "snippet": "Experience: Acme 2023 - Present",
        }]
        with connect(self.database_path) as connection:
            connection.execute(
                """INSERT INTO people (name, url, status)
                   VALUES ('Already Saved', 'https://in.linkedin.com/in/Already-Saved-123?trk=x', 'sent')"""
            )
        response = self.client.post(
            "/search",
            data={
                "mode": "person",
                "person_input": "https://in.linkedin.com/in/Already-Saved-123/?trk=foo",
                "role": "",
                "city": "",
                "org_type": "",
                "filter_status": "any",
                "exclude_words": "",
                "per_page": "5",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Already in tracker: Earlier outreach, status sent", response.data)
        self.assertIn(b"disabled", response.data)
        mock_search.assert_called_once_with("linkedin.com/in/already-saved-123", num=5, page=1)

    @patch("app.search_web")
    def test_find_person_name_escapes_quotes_and_preserves_input(self, mock_search):
        mock_search.return_value = []
        response = self.client.post(
            "/search",
            data={
                "mode": "person",
                "person_input": 'Jane "J" Doe',
                "role": "",
                "city": "Coimbatore",
                "org_type": "company",
                "org_name": "Acme Studio",
                "filter_status": "current",
                "exclude_words": "",
                "per_page": "10",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Jane &#34;J&#34; Doe', response.data)
        self.assertIn(b"Check the photo, name and organisation", response.data)
        mock_search.assert_called_once_with(
            'site:linkedin.com/in "Jane \\"J\\" Doe" Coimbatore '
            '"Acme Studio" Present',
            num=10,
            page=1,
        )

    def test_search_rejects_overlong_fields_and_empty_specific_person(self):
        too_long = self.client.post(
            "/search",
            data={
                "mode": "person",
                "person_input": "x" * 101,
                "role": "",
                "city": "",
            },
        )
        self.assertEqual(too_long.status_code, 400)
        self.assertIn(b"100 characters or fewer", too_long.data)
        empty = self.client.post(
            "/search",
            data={"mode": "person", "person_input": "", "role": "", "city": ""},
        )
        self.assertEqual(empty.status_code, 400)
        self.assertIn(b"Enter a name", empty.data)

    @patch("app.search_web")
    def test_search_results_are_cached_and_refresh_bypasses_cache(self, mock_search):
        mock_search.return_value = [{
            "title": "Cache Person",
            "link": "https://www.linkedin.com/in/cache-person-123",
            "snippet": "Video editor",
        }]
        data = {"role": "video editor", "city": "Coimbatore", "per_page": "5"}
        first = self.client.post("/search", data=data)
        second = self.client.post("/search", data=data)
        refreshed = self.client.post("/search", data={**data, "refresh": "1"})
        self.assertEqual(first.status_code, 200)
        self.assertIn(b"Cached result", second.data)
        self.assertEqual(mock_search.call_count, 2)
        self.assertEqual(refreshed.status_code, 200)
        with connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM search_cache").fetchone()[0],
                1,
            )

    def test_status_changes_are_logged_and_sent_timestamp_uses_ist(self):
        with connect(self.database_path) as connection:
            connection.execute(
                "INSERT INTO people (name, url) VALUES (?, ?)",
                ("IST person", "https://www.linkedin.com/in/ist-person-123"),
            )
            person_id = connection.execute(
                "SELECT id FROM people WHERE name = 'IST person'"
            ).fetchone()["id"]
        self.client.post(
            f"/people/{person_id}/update",
            data={"status": "sent", "personal_note": ""},
        )
        with connect(self.database_path) as connection:
            person = connection.execute(
                "SELECT sent_at, status_changed_at FROM people WHERE id = ?",
                (person_id,),
            ).fetchone()
            history = connection.execute(
                "SELECT old_status, new_status, changed_at FROM status_history "
                "WHERE person_id = ? ORDER BY id DESC LIMIT 1",
                (person_id,),
            ).fetchone()
        self.assertTrue(person["sent_at"].endswith("+05:30"))
        self.assertEqual(person["sent_at"], person["status_changed_at"])
        self.assertEqual(history["old_status"], "new")
        self.assertEqual(history["new_status"], "sent")
        self.assertEqual(history["changed_at"], person["status_changed_at"])

    def test_repeat_connection_draft_requires_confirmation(self):
        with connect(self.database_path) as connection:
            connection.execute(
                """INSERT INTO people (name, url, status, personal_note)
                   VALUES ('Contacted', 'https://www.linkedin.com/in/contacted-123',
                           'sent', 'their editing style')"""
            )
            person_id = connection.execute(
                "SELECT id FROM people WHERE name = 'Contacted'"
            ).fetchone()["id"]
        blocked = self.client.post(
            f"/people/{person_id}/draft", data={"channel": "connection"}
        )
        self.assertEqual(blocked.status_code, 302)
        with connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM messages WHERE person_id = ?", (person_id,)
                ).fetchone()[0],
                0,
            )
        confirmed = self.client.post(
            f"/people/{person_id}/draft",
            data={"channel": "connection", "confirm_repeat": "1"},
        )
        self.assertEqual(confirmed.status_code, 200)
        self.assertIn(b"already marked sent", confirmed.data)

    def test_csv_and_excel_exports_include_batch_filter_and_column(self):
        old = {"name": "Old batch", "url": "https://www.linkedin.com/in/old-batch-123"}
        self.client.post("/shortlist", data={"profile": json.dumps(old)})
        with connect(self.database_path) as connection:
            old_batch = connection.execute(
                "SELECT batch_id FROM people WHERE name = 'Old batch'"
            ).fetchone()["batch_id"]
        self.client.post("/batches/new")
        new = {"name": "New batch", "url": "https://www.linkedin.com/in/new-batch-123"}
        self.client.post("/shortlist", data={"profile": json.dumps(new)})
        csv_response = self.client.get(f"/export.csv?batch={old_batch}")
        self.assertIn(b"Batch", csv_response.data)
        self.assertIn(b"Old batch", csv_response.data)
        self.assertNotIn(b"New batch", csv_response.data)
        workbook = load_workbook(
            BytesIO(self.client.get(f"/export.xlsx?batch={old_batch}").data),
            data_only=False,
        )
        outreach = workbook["Outreach"]
        headers = [cell.value for cell in outreach[1]]
        self.assertIn("Batch", headers)
        self.assertEqual(outreach.max_row, 2)
        self.assertEqual(outreach.cell(row=2, column=headers.index("Name") + 1).value, "Old batch")


if __name__ == "__main__":
    unittest.main()
