import json
import re
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo
from unittest.mock import patch

from app import build_query, create_app, detect_role, normalize_filters
from database import connect, init_db
from openpyxl import load_workbook
from search_provider import (
    SearchAPIError,
    SearchConnectionError,
    SearchResponseError,
    SearchTimeoutError,
)


class RoleDetectionTests(unittest.TestCase):
    def test_role_priority_and_cleaned_headline(self):
        self.assertEqual(
            detect_role("Video Editor | Stayin PSG ...", "")[0],
            "Video Editor",
        )
        self.assertEqual(
            detect_role("Content Creator & YouTuber", "")[0],
            "Content Creator",
        )

    def test_study_text_is_removed_and_empty_headline_has_fallback(self):
        role, cleaned = detect_role("Electrical & Electronics Engineering Student", "")
        self.assertEqual(role, "Other")
        self.assertNotIn("Engineering", cleaned)
        self.assertNotIn("Student", cleaned)

        _, cleaned = detect_role("CSE Student at PSG iTech | NextWork Learner", "")
        self.assertNotIn("CSE", cleaned)
        self.assertNotIn("Student", cleaned)
        self.assertEqual(detect_role("", "")[1], "Other")


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

    def shortlist_profile(self, name, slug, status=None):
        response = self.client.post(
            "/shortlist",
            data={
                "profile": json.dumps(
                    {
                        "name": name,
                        "url": f"https://www.linkedin.com/in/{slug}",
                    }
                )
            },
        )
        self.assertEqual(response.status_code, 302)
        with connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT id FROM people WHERE url = ?",
                (f"https://www.linkedin.com/in/{slug}",),
            ).fetchone()
            if status:
                connection.execute(
                    "UPDATE people SET status = ? WHERE id = ?", (status, row["id"])
                )
        return row["id"]

    def today_shortlist_url(self):
        today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
        return f"/shortlist?date={today}"

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

    def test_empty_personal_note_does_not_block_connection_draft(self):
        person_id = self.shortlist_profile("No Note Person", "no-note-person-123")
        connection = self.client.post(
            f"/people/{person_id}/draft", data={"channel": "connection"}
        )
        self.assertEqual(connection.status_code, 200)
        self.assertIn(b"I&#39;d love to connect", connection.data)
        self.assertNotIn(b"work on .", connection.data)
        follow_up = self.client.post(
            f"/people/{person_id}/draft", data={"channel": "follow_up"}
        )
        self.assertEqual(follow_up.status_code, 302)
        self.client.post(
            f"/people/{person_id}/update",
            data={"status": "connected", "personal_note": ""},
        )
        follow_up = self.client.post(
            f"/people/{person_id}/draft", data={"channel": "follow_up"}
        )
        self.assertEqual(follow_up.status_code, 200)

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
            "Instagram handle",
            "Confirmed Instagram link",
            "Confirmed YouTube link",
        ]
        expected_headers.extend(
            ["Source", "Referred by", "Touch count", "Follow-up date", "Sent at", "Last sent message"]
        )
        self.assertEqual([cell.value for cell in outreach[1]], expected_headers)
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
        self.assertTrue(outreach.auto_filter.ref.startswith("A1:X"))
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
        self.assertTrue(any("COUNTIF(Outreach!J:J,\"connected\")" in f for f in formulas))
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
                "instagram_handle",
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
        with connect(legacy_path) as migrated:
            self.assertEqual(
                migrated.execute("SELECT COUNT(*) FROM person_links").fetchone()[0],
                1,
            )

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
        date_list = self.client.get("/shortlist")
        self.assertIn(b"First", date_list.data)
        self.assertIn(b"Today", date_list.data)

        self.client.post("/batches/new")
        second = {"name": "Second", "url": "https://www.linkedin.com/in/second-123"}
        self.client.post("/shortlist", data={"profile": json.dumps(second)})
        shortlist = self.client.get(self.today_shortlist_url())
        self.assertIn(b"Second", shortlist.data)
        self.assertIn(b"First", shortlist.data)
        tracker = self.client.get("/tracker")
        self.assertIn(b"First", tracker.data)
        self.assertIn(b"Second", tracker.data)
        only_first = self.client.get(f"/tracker?batch={first_batch}")
        self.assertIn(b"First", only_first.data)
        self.assertNotIn(b"Second", only_first.data)
        current_only = self.client.get("/tracker?batch=current")
        self.assertIn(b"Second", current_only.data)
        self.assertNotIn(b"First", current_only.data)

    def test_shortlist_is_listed_by_date_and_filtered_on_click(self):
        backdated_id = self.shortlist_profile("Backdated Person", "backdated-person-123")
        today_id = self.shortlist_profile("Today Person", "today-person-123")
        with connect(self.database_path) as connection:
            connection.execute(
                "UPDATE people SET shortlisted_at = ? WHERE id = ?",
                ("2026-11-03T10:15:00+05:30", backdated_id),
            )
        date_list = self.client.get("/shortlist")
        self.assertEqual(date_list.status_code, 200)
        self.assertIn(b"03 Nov 2026", date_list.data)
        self.assertIn(b"10:15 AM", date_list.data)
        self.assertNotIn(b'class="person-card shortlist-person-card"', date_list.data)
        self.assertIn(b"Pick a date", date_list.data)

        selected = self.client.get("/shortlist?date=2026-11-03")
        self.assertEqual(selected.status_code, 200)
        self.assertIn(b"Backdated Person", selected.data)
        self.assertNotIn(b"Today Person", selected.data)
        self.assertIn(b"All dates", selected.data)
        self.assertIn(b"2 shortlisted in total", selected.data)
        self.assertIn(b"showing 1 on Tue, 03 Nov 2026", selected.data)
        self.assertNotEqual(backdated_id, today_id)

        invalid = self.client.get("/shortlist?date=garbage")
        self.assertEqual(invalid.status_code, 200)
        self.assertIn(b"Pick a date", invalid.data)
        self.assertNotIn(b'id="common-message-form"', invalid.data)

    def test_shortlist_rows_show_role_first_without_college_label(self):
        person_id = self.shortlist_profile("Role First", "role-first-123")
        with connect(self.database_path) as connection:
            connection.execute(
                """UPDATE people SET headline = ?, snippet = ?, org_type = ?,
                   org_name = ?, verified_status = ? WHERE id = ?""",
                (
                    "Video Editor | PSG iTech",
                    "Video editing and motion graphics",
                    "College",
                    "PSG iTech",
                    "current",
                    person_id,
                ),
            )
            connection.execute(
                """INSERT INTO messages
                   (person_id, channel, draft_text, is_edited)
                   VALUES (?, 'connection', 'Hello', 0)""",
                (person_id,),
            )
        response = self.client.get(self.today_shortlist_url())
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'data-role="Video Editor"', response.data)
        self.assertIn(b'class="sl-role sl-role-video-editor"', response.data)
        row_html = response.data.decode().split(
            '<div class="shortlist-person-row sl-row"', 1
        )[1].split('<div class="shortlist-expanded-panel"', 1)[0]
        self.assertIn("Video Editor", row_html)
        self.assertIn("✓", row_html)
        self.assertNotIn("College", row_html)

    def test_send_controls_open_platform_links_without_posting_or_changing_status(self):
        instagram_id = self.shortlist_profile("Instagram Person", "instagram-person-123")
        linkedin_id = self.shortlist_profile("LinkedIn Person", "linkedin-person-123")
        with connect(self.database_path) as connection:
            connection.execute(
                "UPDATE people SET instagram_handle = ? WHERE id = ?",
                ("jane.editor", instagram_id),
            )
            for person_id in (instagram_id, linkedin_id):
                connection.execute(
                    """INSERT INTO messages
                       (person_id, channel, draft_text, is_edited)
                       VALUES (?, 'connection', 'Hello there', 0)""",
                    (person_id,),
                )
        response = self.client.get(self.today_shortlist_url())
        self.assertEqual(response.status_code, 200)
        html = response.data.decode()
        self.assertIn('data-send-target="https://ig.me/m/jane.editor"', html)
        self.assertIn(
            'data-send-target="https://www.linkedin.com/in/linkedin-person-123"',
            html,
        )
        self.assertIn("Send · IG", html)
        self.assertIn("Send · LinkedIn", html)
        self.assertIn("Open LinkedIn profile", html)
        self.assertIn("Open Instagram DM", html)
        send_controls = re.findall(r"<button\b[^>]*data-send-open[^>]*>", html)
        self.assertTrue(send_controls)
        self.assertTrue(all('type="button"' in control for control in send_controls))
        self.assertTrue(all("form=" not in control for control in send_controls))
        with connect(self.database_path) as connection:
            statuses = connection.execute(
                "SELECT status FROM people WHERE id IN (?, ?)",
                (instagram_id, linkedin_id),
            ).fetchall()
        self.assertEqual({row["status"] for row in statuses}, {"shortlisted"})

    def test_shortlist_date_survives_actions_and_scopes_generate_all(self):
        selected_id = self.shortlist_profile("Selected Day", "selected-day-123")
        other_id = self.shortlist_profile("Other Day", "other-day-123")
        with connect(self.database_path) as connection:
            connection.execute(
                "UPDATE people SET shortlisted_at = ? WHERE id = ?",
                ("2026-11-03T10:15:00+05:30", selected_id),
            )
            connection.execute(
                "UPDATE people SET shortlisted_at = ? WHERE id = ?",
                ("2026-11-04T10:15:00+05:30", other_id),
            )
        date_value = "2026-11-03"
        selected_page = self.client.get(f"/shortlist?date={date_value}")
        self.assertIn(b'name="date" value="2026-11-03"', selected_page.data)
        response = self.client.post(
            "/shortlist/messages",
            data={
                "date": date_value,
                "kind": "connection",
                "action": "generate",
                "common_message": "Hello {first_name}",
                "apply_to": "all",
            },
        )
        self.assertIn(f"date={date_value}", response.location)
        with connect(self.database_path) as connection:
            drafts = connection.execute(
                "SELECT person_id FROM messages WHERE channel = 'connection'"
            ).fetchall()
        self.assertEqual([row["person_id"] for row in drafts], [selected_id])

        save_response = self.client.post(
            f"/people/{selected_id}/save",
            data={
                "date": date_value,
                "personal_note": "A detail",
                "verified_status": "unverified",
                "verified_note": "",
                "instagram_handle": "",
            },
        )
        self.assertIn(f"date={date_value}", save_response.location)
        sent_response = self.client.post(
            f"/people/{selected_id}/sent",
            data={
                "date": date_value,
                "kind": "connection",
                "sent_text": "Manual send",
            },
        )
        self.assertIn(f"date={date_value}", sent_response.location)
        remove_response = self.client.post(
            f"/people/{selected_id}/remove", data={"date": date_value}
        )
        self.assertIn(f"date={date_value}", remove_response.location)

    def test_remove_from_shortlist_keeps_tracker_history(self):
        profile = {"name": "Keep history", "url": "https://www.linkedin.com/in/keep-history-123"}
        self.client.post("/shortlist", data={"profile": json.dumps(profile)})
        with connect(self.database_path) as connection:
            person_id = connection.execute(
                "SELECT id FROM people WHERE name = 'Keep history'"
            ).fetchone()["id"]
        response = self.client.post(f"/people/{person_id}/remove")
        self.assertEqual(response.status_code, 302)
        self.assertNotIn(b"Keep history", self.client.get(self.today_shortlist_url()).data)
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
            'site:linkedin.com/in "Jane \\"J\\" Doe" "Acme Studio" '
            '"Coimbatore" Present',
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

    def test_find_person_search_keeps_other_platform_results_when_one_fails(self):
        from search_provider import SearchAPIError

        def web_search(query, num, page):
            if "site:instagram.com" in query:
                raise SearchAPIError(429)
            return [{
                "title": "Jane Doe - Video Editor",
                "link": "https://linkedin.com/in/jane-doe-123",
                "snippet": "Jane Doe video editor",
            }]

        with patch("app.search_web", side_effect=web_search), patch(
            "app.search_videos",
            return_value=[{
                "title": "Jane Doe editing",
                "link": "https://youtube.com/watch?v=abc123",
                "snippet": "Jane Doe's video channel",
                "date": "2 days ago",
            }],
        ):
            response = self.client.post(
                "/search",
                data={
                    "mode": "person",
                    "person_input": "Jane Doe",
                    "role": "",
                    "city": "",
                    "filter_status": "any",
                    "org_type": "",
                    "org_name": "",
                    "exclude_words": "",
                    "platforms_enabled": "1",
                    "platforms": ["linkedin", "instagram", "youtube"],
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Instagram search failed", response.data)
        self.assertIn(b"Jane Doe - Video Editor", response.data)
        self.assertIn(b"Jane Doe editing", response.data)
        self.assertIn(b"Unverified suggestion", response.data)
        self.assertIn(b"3 searches will use 3 Serper credits", response.data)

    def test_platform_search_cache_reuses_each_platform_results(self):
        web_calls = []

        def web_search(query, num, page):
            web_calls.append(query)
            if "instagram.com" in query:
                return [{
                    "title": "Jane (@jane.editor) • Instagram",
                    "link": "https://instagram.com/jane.editor/",
                    "snippet": "Jane Doe creator",
                }]
            return [{
                "title": "Jane Doe - Video Editor",
                "link": "https://linkedin.com/in/jane-doe-123",
                "snippet": "Jane Doe editor",
            }]

        data = {
            "mode": "person",
            "person_input": "Jane Doe",
            "role": "",
            "city": "",
            "filter_status": "any",
            "org_type": "",
            "org_name": "",
            "exclude_words": "",
            "platforms_enabled": "1",
            "platforms": ["linkedin", "instagram"],
        }
        with patch("app.search_web", side_effect=web_search):
            first = self.client.post("/search", data=data)
            second = self.client.post("/search", data=data)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(web_calls.__len__(), 2)
        self.assertIn(b"Cached", second.data)

    def test_find_more_links_confirm_dismiss_and_unique_url(self):
        profile = {
            "name": "Jane Doe",
            "url": "https://www.linkedin.com/in/jane-doe-123",
            "org_name": "Acme Studio",
        }
        self.client.post("/shortlist", data={"profile": json.dumps(profile)})
        with connect(self.database_path) as connection:
            person_id = connection.execute(
                "SELECT id FROM people WHERE name = 'Jane Doe'"
            ).fetchone()["id"]

        def web_search(query, num, page):
            if "instagram.com" in query:
                return [
                    {
                        "title": "Jane Doe (@jane.editor) • Instagram",
                        "link": "https://www.instagram.com/jane.editor/?igsh=abc",
                        "snippet": "Jane Doe video editor",
                    },
                    {
                        "title": "Jane Doe (@jane.editor) • Instagram",
                        "link": "https://instagram.com/jane.editor/",
                        "snippet": "Duplicate URL",
                    },
                ]
            return [{
                "title": "Jane Doe channel",
                "link": "https://youtube.com/@janedoe",
                "snippet": "Jane Doe video editor",
            }]

        with patch("app.search_web", side_effect=web_search), patch(
            "app.search_videos", return_value=[]
        ):
            response = self.client.post(f"/people/{person_id}/find-links")
        self.assertEqual(response.status_code, 302)
        with connect(self.database_path) as connection:
            links = connection.execute(
                "SELECT * FROM person_links WHERE person_id = ? AND platform != 'linkedin'",
                (person_id,),
            ).fetchall()
        self.assertEqual(len(links), 2)
        instagram = next(link for link in links if link["platform"] == "instagram")
        youtube = next(link for link in links if link["platform"] == "youtube")
        self.client.post(
            f"/people/{person_id}/links/{instagram['id']}/confirm"
        )
        self.client.post(
            f"/people/{person_id}/links/{youtube['id']}/dismiss"
        )
        with connect(self.database_path) as connection:
            person = connection.execute(
                "SELECT instagram_handle FROM people WHERE id = ?", (person_id,)
            ).fetchone()
            instagram = connection.execute(
                "SELECT confirmed, handle FROM person_links WHERE id = ?",
                (instagram["id"],),
            ).fetchone()
            youtube = connection.execute(
                "SELECT dismissed, confirmed FROM person_links WHERE id = ?",
                (youtube["id"],),
            ).fetchone()
        self.assertEqual(person["instagram_handle"], "jane.editor")
        self.assertEqual(instagram["confirmed"], 1)
        self.assertEqual(instagram["handle"], "jane.editor")
        self.assertEqual(youtube["dismissed"], 1)
        self.assertEqual(youtube["confirmed"], 0)
        shortlist = self.client.get(self.today_shortlist_url())
        self.assertIn(b"Open Instagram DM", shortlist.data)
        self.assertIn(b"ig.me/m/jane.editor", shortlist.data)

    def test_manual_instagram_handle_is_normalized_and_rejects_invalid_values(self):
        profile = {
            "name": "Manual Instagram",
            "url": "https://www.linkedin.com/in/manual-instagram-123",
        }
        self.client.post("/shortlist", data={"profile": json.dumps(profile)})
        with connect(self.database_path) as connection:
            person_id = connection.execute(
                "SELECT id FROM people WHERE name = 'Manual Instagram'"
            ).fetchone()["id"]
        response = self.client.post(
            f"/people/{person_id}/save",
            data={
                "personal_note": "their editing",
                "verified_status": "unverified",
                "instagram_handle": "https://www.instagram.com/Manual.Handle/?utm=one",
            },
        )
        self.assertEqual(response.status_code, 302)
        with connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT instagram_handle FROM people WHERE id = ?", (person_id,)
                ).fetchone()["instagram_handle"],
                "Manual.Handle",
            )
        rejected = self.client.post(
            f"/people/{person_id}/save",
            data={
                "personal_note": "their editing",
                "verified_status": "unverified",
                "instagram_handle": "https://instagram.com/p/postid/",
            },
        )
        self.assertEqual(rejected.status_code, 302)
        with connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT instagram_handle FROM people WHERE id = ?", (person_id,)
                ).fetchone()["instagram_handle"],
                "Manual.Handle",
            )

    def test_instagram_and_youtube_export_columns_are_in_csv_and_excel(self):
        profile = {
            "name": "Export links",
            "url": "https://www.linkedin.com/in/export-links-123",
        }
        self.client.post("/shortlist", data={"profile": json.dumps(profile)})
        with connect(self.database_path) as connection:
            person_id = connection.execute(
                "SELECT id FROM people WHERE name = 'Export links'"
            ).fetchone()["id"]
            connection.execute(
                """INSERT INTO person_links
                   (person_id, platform, kind, url, handle, confidence, confirmed)
                   VALUES (?, 'instagram', 'profile', ?, 'export.handle', 'likely', 1)""",
                (person_id, "https://www.instagram.com/export.handle/"),
            )
            connection.execute(
                """INSERT INTO person_links
                   (person_id, platform, kind, url, confidence, confirmed)
                   VALUES (?, 'youtube', 'channel', ?, 'likely', 1)""",
                (person_id, "https://www.youtube.com/@export"),
            )
            connection.execute(
                "UPDATE people SET instagram_handle = '+formula' WHERE id = ?",
                (person_id,),
            )
        csv_response = self.client.get("/export.csv")
        self.assertIn(b"Instagram handle", csv_response.data)
        self.assertIn(b"Confirmed Instagram link", csv_response.data)
        self.assertIn(b"Confirmed YouTube link", csv_response.data)
        self.assertIn(b"'+formula", csv_response.data)
        workbook = load_workbook(
            BytesIO(self.client.get("/export.xlsx").data), data_only=False
        )
        headers = [cell.value for cell in workbook["Outreach"][1]]
        self.assertIn("Instagram handle", headers)
        self.assertIn("Confirmed Instagram link", headers)
        self.assertIn("Confirmed YouTube link", headers)
        self.assertEqual(workbook["Outreach"].cell(2, headers.index("Instagram handle") + 1).value, "'+formula")

    def test_common_messages_are_saved_per_kind_with_personal_details(self):
        response = self.client.post(
            "/shortlist/messages",
            data={
                "action": "save",
                "kind": "connection",
                "common_message": "Hi {first_name}, {specific_note}",
                "my_name": "Sam",
                "message_link": "https://example.test/work",
            },
        )
        self.assertEqual(response.status_code, 302)
        with connect(self.database_path) as connection:
            values = {
                row["key"]: row["value"]
                for row in connection.execute("SELECT key, value FROM settings")
            }
        self.assertEqual(values["common_connection"], "Hi {first_name}, {specific_note}")
        self.assertEqual(values["my_name"], "Sam")
        self.assertEqual(values["link"], "https://example.test/work")
        self.client.post(
            "/shortlist/messages",
            data={
                "action": "save",
                "kind": "follow_up",
                "common_message": "Thanks {first_name}",
                "my_name": "Sam",
                "message_link": "https://example.test/work",
            },
        )
        with connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT value FROM settings WHERE key = 'common_follow_up'"
                ).fetchone()["value"],
                "Thanks {first_name}",
            )
        page = self.client.get(self.today_shortlist_url())
        self.assertIn(b"data-connection=", page.data)
        self.assertIn(b"Only the people I tick", page.data)
        self.assertIn(b"This only prepares text and links", page.data)

    def test_generate_drafts_for_selected_or_all_shortlisted_people(self):
        first_id = self.shortlist_profile("Asha Rao", "asha-rao-123")
        second_id = self.shortlist_profile("Bala Kumar", "bala-kumar-123")
        data = {
            "action": "generate",
            "kind": "connection",
            "common_message": "Hello {first_name}, {specific_note}",
            "apply_to": "selected",
            "person_ids": str(first_id),
        }
        self.client.post("/shortlist/messages", data=data)
        with connect(self.database_path) as connection:
            drafts = connection.execute(
                "SELECT person_id, draft_text FROM messages WHERE channel = 'connection'"
            ).fetchall()
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0]["person_id"], first_id)
        self.assertEqual(drafts[0]["draft_text"], "Hello Asha")

        self.client.post(
            "/shortlist/messages",
            data={**data, "apply_to": "all", "person_ids": ""},
        )
        with connect(self.database_path) as connection:
            drafts = connection.execute(
                "SELECT person_id FROM messages WHERE channel = 'connection'"
            ).fetchall()
        self.assertEqual({row["person_id"] for row in drafts}, {first_id, second_id})

    def test_generation_skips_edited_drafts_and_overwrites_only_when_requested(self):
        person_id = self.shortlist_profile("Edited Person", "edited-person-123")
        with connect(self.database_path) as connection:
            connection.execute(
                """INSERT INTO messages
                   (person_id, channel, draft_text, is_edited)
                   VALUES (?, 'connection', 'Hand-edited', 1)""",
                (person_id,),
            )
        self.client.post(
            "/shortlist/messages",
            data={
                "action": "generate",
                "kind": "connection",
                "common_message": "Common for {first_name}",
                "apply_to": "all",
            },
        )
        with connect(self.database_path) as connection:
            draft = connection.execute(
                "SELECT draft_text, is_edited FROM messages WHERE person_id = ? "
                "AND channel = 'connection' ORDER BY id DESC LIMIT 1",
                (person_id,),
            ).fetchone()
        self.assertEqual(draft["draft_text"], "Hand-edited")
        self.assertEqual(draft["is_edited"], 1)

        self.client.post(
            "/shortlist/messages",
            data={
                "action": "generate",
                "kind": "connection",
                "common_message": "Common for {first_name}",
                "apply_to": "all",
                "overwrite_edited": "1",
            },
        )
        with connect(self.database_path) as connection:
            draft = connection.execute(
                "SELECT draft_text, is_edited FROM messages WHERE person_id = ? "
                "AND channel = 'connection' ORDER BY id DESC LIMIT 1",
                (person_id,),
            ).fetchone()
        self.assertEqual(draft["draft_text"], "Common for Edited")
        self.assertEqual(draft["is_edited"], 0)
        self.assertIn(b"Edited", self.client.get(self.today_shortlist_url()).data)

    def test_generation_skips_contacted_people_and_enforces_followup_status(self):
        sent_id = self.shortlist_profile("Sent Person", "sent-person-123", "sent")
        connected_id = self.shortlist_profile(
            "Connected Person", "connected-person-123", "connected"
        )
        new_id = self.shortlist_profile("New Person", "new-person-123", "new")
        connection_data = {
            "action": "generate",
            "kind": "connection",
            "common_message": "Hi {first_name}",
            "apply_to": "all",
        }
        self.client.post("/shortlist/messages", data=connection_data)
        with connect(self.database_path) as connection:
            connection_ids = {
                row["person_id"]
                for row in connection.execute(
                    "SELECT person_id FROM messages WHERE channel = 'connection'"
                )
            }
        self.assertNotIn(sent_id, connection_ids)
        self.assertNotIn(connected_id, connection_ids)

        self.client.post(
            "/shortlist/messages",
            data={
                "action": "generate",
                "kind": "connection",
                "common_message": "Hi {first_name}",
                "apply_to": "all",
                "include_contacted": "1",
            },
        )
        follow_data = {
            "action": "generate",
            "kind": "follow_up",
            "common_message": "Follow-up {first_name}",
            "apply_to": "all",
        }
        self.client.post("/shortlist/messages", data=follow_data)
        with connect(self.database_path) as connection:
            follow_ids = {
                row["person_id"]
                for row in connection.execute(
                    "SELECT person_id FROM messages WHERE channel = 'follow_up'"
                )
            }
        self.assertEqual(follow_ids, {connected_id})
        self.assertNotIn(new_id, follow_ids)

    def test_connection_draft_limit_is_299_characters(self):
        person_id = self.shortlist_profile("Long Draft", "long-draft-123")
        accepted = self.client.post(
            f"/people/{person_id}/draft/save",
            data={"channel": "connection", "draft_text": "x" * 299},
        )
        self.assertEqual(accepted.status_code, 302)
        rejected = self.client.post(
            f"/people/{person_id}/draft/save",
            data={"channel": "connection", "draft_text": "x" * 300},
        )
        self.assertEqual(rejected.status_code, 302)
        with connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT draft_text FROM messages WHERE person_id = ?",
                (person_id,),
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]["draft_text"]), 299)

    def test_mark_sent_logs_text_status_and_four_day_followup_in_ist(self):
        person_id = self.shortlist_profile("Sent Log", "sent-log-123")
        response = self.client.post(
            f"/people/{person_id}/sent",
            data={
                "kind": "connection",
                "sent_text": "Exact message I sent",
            },
        )
        self.assertEqual(response.status_code, 302)
        with connect(self.database_path) as connection:
            person = connection.execute(
                "SELECT status, sent_at, status_changed_at, touch_count, follow_up_date "
                "FROM people WHERE id = ?",
                (person_id,),
            ).fetchone()
            sent_message = connection.execute(
                "SELECT kind, text, sent_at FROM sent_messages WHERE person_id = ?",
                (person_id,),
            ).fetchone()
            history = connection.execute(
                "SELECT old_status, new_status, changed_at FROM status_history "
                "WHERE person_id = ? ORDER BY id DESC LIMIT 1",
                (person_id,),
            ).fetchone()
        sent_at = datetime.fromisoformat(person["sent_at"])
        self.assertEqual(sent_at.utcoffset(), timedelta(hours=5, minutes=30))
        self.assertEqual(person["status"], "sent")
        self.assertEqual(person["status_changed_at"], person["sent_at"])
        self.assertEqual(person["touch_count"], 1)
        self.assertEqual(person["follow_up_date"], (sent_at.date() + timedelta(days=4)).isoformat())
        self.assertEqual(sent_message["kind"], "connection")
        self.assertEqual(sent_message["text"], "Exact message I sent")
        self.assertEqual(sent_message["sent_at"], person["sent_at"])
        self.assertEqual(history["old_status"], "shortlisted")
        self.assertEqual(history["new_status"], "sent")
        self.assertEqual(history["changed_at"], person["sent_at"])
        self.assertIn(b"of 10 sent today", self.client.get(self.today_shortlist_url()).data)

    def test_marking_same_kind_sent_requires_confirmation(self):
        person_id = self.shortlist_profile("Duplicate Send", "duplicate-send-123")
        send_data = {"kind": "connection", "sent_text": "Same kind"}
        self.client.post(f"/people/{person_id}/sent", data=send_data)
        response = self.client.post(f"/people/{person_id}/sent", data=send_data)
        self.assertEqual(response.status_code, 302)
        with connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM sent_messages WHERE person_id = ?",
                    (person_id,),
                ).fetchone()[0],
                1,
            )
        self.client.post(
            f"/people/{person_id}/sent",
            data={**send_data, "confirm_duplicate": "1"},
        )
        with connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM sent_messages WHERE person_id = ?",
                    (person_id,),
                ).fetchone()[0],
                2,
            )

    def test_tracker_due_filter_and_new_export_fields(self):
        today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
        with connect(self.database_path) as connection:
            batch_id = connection.execute(
                "INSERT INTO batches (name, is_current) VALUES ('Due batch', 1)"
            ).lastrowid
            for name, follow_date in (
                ("Overdue", (today - timedelta(days=1)).isoformat()),
                ("Due today", today.isoformat()),
                ("Future", (today + timedelta(days=2)).isoformat()),
            ):
                cursor = connection.execute(
                    """INSERT INTO people
                       (name, url, batch_id, follow_up_date, source, referred_by, touch_count)
                       VALUES (?, ?, ?, ?, 'Referral', 'Alex', 2)""",
                    (
                        name,
                        f"https://www.linkedin.com/in/{name.lower()}-due-123",
                        batch_id,
                        follow_date,
                    ),
                )
                if name == "Overdue":
                    connection.execute(
                        """INSERT INTO sent_messages
                           (person_id, kind, text, sent_at)
                           VALUES (?, 'connection', '+sent message', ?)""",
                        (cursor.lastrowid, datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()),
                    )
        due = self.client.get("/tracker?due=1")
        self.assertIn(b"Overdue", due.data)
        self.assertIn(b"Due today", due.data)
        self.assertNotIn(b"Future", due.data)
        csv_response = self.client.get("/export.csv?due=1")
        self.assertIn(b"Referred by", csv_response.data)
        self.assertIn(b"Touch count", csv_response.data)
        self.assertIn(b"Follow-up date", csv_response.data)
        self.assertIn(b"Last sent message", csv_response.data)
        self.assertIn(b"'+sent message", csv_response.data)
        workbook = load_workbook(
            BytesIO(self.client.get("/export.xlsx?due=1").data), data_only=False
        )
        headers = [cell.value for cell in workbook["Outreach"][1]]
        for header in (
            "Source", "Referred by", "Touch count", "Follow-up date",
            "Sent at", "Last sent message",
        ):
            self.assertIn(header, headers)
        name_index = headers.index("Name") + 1
        sent_index = headers.index("Last sent message") + 1
        overdue_row = next(
            row
            for row in range(2, workbook["Outreach"].max_row + 1)
            if workbook["Outreach"].cell(row, name_index).value == "Overdue"
        )
        self.assertEqual(
            workbook["Outreach"].cell(overdue_row, sent_index).value,
            "'+sent message",
        )
        self.assertEqual(
            workbook["Outreach"].cell(2, headers.index("Source") + 1).value,
            "Referral",
        )
        self.assertEqual(
            workbook["Outreach"].cell(2, headers.index("Referred by") + 1).value,
            "Alex",
        )
        self.assertEqual(
            workbook["Outreach"].cell(2, headers.index("Touch count") + 1).value,
            2,
        )

    def test_connected_status_check_rebuild_preserves_rows_and_foreign_keys(self):
        legacy_path = str(Path(self.temp_dir.name) / "checked-legacy.sqlite3")
        legacy = sqlite3.connect(legacy_path)
        legacy.executescript(
            """PRAGMA foreign_keys = ON;
            CREATE TABLE batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT '', is_current INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE people (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                headline TEXT NOT NULL DEFAULT '', snippet TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL UNIQUE, college TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'Serper.dev',
                personal_note TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'new'
                    CHECK (status IN ('new', 'shortlisted', 'sent', 'replied', 'agreed', 'rejected')),
                created_at TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '',
                sent_at TEXT, batch_id INTEGER REFERENCES batches(id),
                shortlisted_at TEXT, status_changed_at TEXT
            );
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
                channel TEXT NOT NULL, draft_text TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT ''
            );
            INSERT INTO batches (name, is_current) VALUES ('Old batch', 1);
            INSERT INTO people (name, url, source, status, batch_id)
                VALUES ('Legacy connected', 'https://www.linkedin.com/in/legacy-checked',
                        'Serper.dev', 'sent', 1);
            INSERT INTO messages (person_id, channel, draft_text) VALUES (1, 'connection', 'Keep this draft');
            """
        )
        legacy.commit()
        legacy.close()
        init_db(legacy_path)
        init_db(legacy_path)
        with connect(legacy_path) as connection:
            connection.execute("UPDATE people SET status = 'connected' WHERE id = 1")
            person = connection.execute(
                "SELECT id, name, source, status, batch_id, touch_count FROM people"
            ).fetchone()
            draft = connection.execute("SELECT draft_text FROM messages").fetchone()
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        self.assertEqual(person["id"], 1)
        self.assertEqual(person["name"], "Legacy connected")
        self.assertEqual(person["source"], "Serper.dev")
        self.assertEqual(person["status"], "connected")
        self.assertEqual(person["batch_id"], 1)
        self.assertEqual(person["touch_count"], 0)
        self.assertEqual(draft["draft_text"], "Keep this draft")
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
