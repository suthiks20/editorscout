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
from werkzeug.datastructures import MultiDict

from app import (
    build_query,
    build_search_key,
    create_app,
    detect_role,
    location_cache_key,
    normalize_filters,
)
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

    def test_primary_pages_render_accessible_active_navigation(self):
        expected = {
            "/": rb'href="/" aria-current="page"',
            "/shortlist": rb'href="/shortlist" aria-current="page"',
            "/tracker": rb'href="/tracker" aria-current="page"',
        }
        for path, active_link in expected.items():
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertIn(b'<nav class="ui-tabs" aria-label="Main navigation">', response.data)
                self.assertEqual(
                    len(re.findall(rb'<a class="ui-tab(?: is-active)?"', response.data)),
                    3,
                )
                self.assertIn(active_link, response.data)
                self.assertIn(b'Skip to content', response.data)
                self.assertIn(b'rel="icon" href="data:image/svg+xml,', response.data)

    def test_incubrix_logo_link_and_static_asset(self):
        response = self.client.get("/")
        self.assertRegex(
            response.data,
            rb'<a class="ui-incubrix-link" href="https://incubrix\.com/" target="_blank" rel="noopener noreferrer">\s*'
            rb'<img class="ui-incubrix-logo" src="[^"]*/static/img/incubrix-logo\.svg" alt="Incubrix"',
        )
        self.assertIn(b'width="180" height="24" loading="eager"', response.data)
        self.assertIn(b'width="135" height="18" loading="eager"', response.data)

        logo_path = Path(__file__).resolve().parents[1] / "static" / "img" / "incubrix-logo.svg"
        created_directory = not logo_path.parent.exists()
        created_file = not logo_path.exists()
        if created_file:
            logo_path.parent.mkdir(parents=True, exist_ok=True)
            logo_path.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg"><title>Test placeholder</title></svg>',
                encoding="utf-8",
            )

        def cleanup_placeholder():
            if created_file and logo_path.exists():
                logo_path.unlink()
            if created_directory and logo_path.parent.exists():
                logo_path.parent.rmdir()

        self.addCleanup(cleanup_placeholder)
        asset_response = self.client.get("/static/img/incubrix-logo.svg")
        self.assertEqual(asset_response.status_code, 200)

    def test_theme_controls_and_css_tokens(self):
        response = self.client.get("/")
        self.assertIn(b'data-theme="dark"', response.data)
        self.assertIn(b'class="ui-theme-select"', response.data)

        css_path = Path(__file__).resolve().parents[1] / "static" / "style.css"
        css = css_path.read_text(encoding="utf-8")
        for token in ("--bg: #07090f", "--primary: #2563ff", "--accent: #2dd4bf", "--gradient:"):
            self.assertIn(token, css)

        depth = 0
        root_scopes = []
        outside_root_colors = []
        for line_number, line in enumerate(css.splitlines(), start=1):
            if ":root" in line and "{" in line:
                root_scopes.append(depth + line.count("{") - line.count("}"))
            in_root_scope = bool(root_scopes and depth >= root_scopes[-1])
            if re.search(r"#[0-9a-fA-F]{3,8}\b", line) and not in_root_scope:
                outside_root_colors.append((line_number, line.strip()))
            depth += line.count("{") - line.count("}")
            while root_scopes and depth < root_scopes[-1]:
                root_scopes.pop()
        self.assertEqual(outside_root_colors, [])

    def test_follow_up_navigation_badge_appears_only_when_due(self):
        response = self.client.get("/tracker")
        self.assertNotIn(b'aria-label="1 follow-ups due"', response.data)
        person_id = self.shortlist_profile("Due Creator", "due-creator")
        due_date = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
        with connect(self.database_path) as connection:
            connection.execute(
                "UPDATE people SET follow_up_date = ? WHERE id = ?",
                (due_date, person_id),
            )
        response = self.client.get("/tracker")
        self.assertIn(b'aria-label="1 follow-ups due"', response.data)
        self.assertIn(b'class="ui-stat-card ui-stat-card-warm"', response.data)

    def test_tracker_renders_sticky_table_with_existing_edit_fields(self):
        self.shortlist_profile("Table Creator", "table-creator")
        response = self.client.get("/tracker")
        self.assertIn(b'<table class="ui-tracker-table">', response.data)
        self.assertIn(b'<th scope="col">Creator</th>', response.data)
        self.assertIn(b'name="status"', response.data)
        self.assertIn(b'name="personal_note"', response.data)
        self.assertIn(b'name="follow_up_date"', response.data)

    def test_tracker_sent_stat_includes_people_contacted_in_other_statuses(self):
        self.shortlist_profile("Sent Stat", "sent-stat", "sent")
        self.shortlist_profile("Connected Stat", "connected-stat", "connected")
        self.shortlist_profile("Replied Stat", "replied-stat", "replied")
        self.shortlist_profile("Agreed Stat", "agreed-stat", "agreed")
        touched_id = self.shortlist_profile("Touched Stat", "touched-stat", "new")
        with connect(self.database_path) as connection:
            connection.execute(
                "UPDATE people SET touch_count = 1 WHERE id = ?", (touched_id,)
            )

        response = self.client.get("/tracker")
        self.assertIn(
            b'<span title="People you have messaged (includes connected and replied)">Sent</span><strong>5</strong>',
            response.data,
        )
        self.assertIn(b"<span>Connected</span><strong>1</strong>", response.data)
        self.assertIn(b"<span>Replied</span><strong>1</strong>", response.data)

    def test_tracker_renders_multiple_people_and_nonsticky_table_header(self):
        self.shortlist_profile("First Tracker Creator", "first-tracker-creator")
        self.shortlist_profile("Second Tracker Creator", "second-tracker-creator")
        response = self.client.get("/tracker")
        self.assertEqual(response.data.count(b"<thead>"), 1)
        self.assertIn(b"First Tracker Creator", response.data)
        self.assertIn(b"Second Tracker Creator", response.data)

        css_path = Path(__file__).resolve().parents[1] / "static" / "style.css"
        css = css_path.read_text(encoding="utf-8")
        tracker_styles = css[css.index(".ui-tracker-table th"):css.index(".tracker-edit")]
        self.assertNotIn("position: sticky", tracker_styles)

    def test_flash_messages_render_in_live_toast_container(self):
        response = self.client.post(
            "/shortlist",
            data={
                "profile": json.dumps(
                    {
                        "name": "Toast Creator",
                        "url": "https://www.linkedin.com/in/toast-creator",
                    }
                )
            },
            follow_redirects=True,
        )
        self.assertIn(b'class="flash-list ui-toast-container" aria-live="polite"', response.data)
        self.assertIn(b'class="flash flash-success ui-toast" role="status"', response.data)
        self.assertIn(b'aria-label="Dismiss notification"', response.data)
        self.assertIn(b'aria-label="1 shortlisted today"', response.data)

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

    def test_build_query_uses_the_most_specific_location_and_disambiguates(self):
        base = {
            "mode": "browse",
            "role": "video editor",
            "org_name": "",
            "filter_status": "any",
            "exclude_words": "",
            "widen_search": False,
        }
        self.assertIn("Coimbatore", build_query({**base, "city": "Coimbatore"}))
        self.assertIn('"Tamil Nadu"', build_query({**base, "state": "Tamil Nadu"}))
        self.assertIn('"United States"', build_query({**base, "country": "US"}))
        self.assertEqual(
            build_query({**base, "role": ""}),
            "site:linkedin.com/in",
        )
        custom_city = normalize_filters(MultiDict({
            "role": "video editor",
            "city": "Coimbatore",
            "city_custom": "Pollachi",
        }))["city"]
        self.assertIn("Pollachi", build_query({**base, "city": custom_city}))
        self.assertIn(
            'Springfield "Illinois"',
            build_query({
                **base,
                "city": "Springfield",
                "state": "Illinois",
                "disambiguate": True,
            }),
        )
        self.assertNotIn(
            '"Illinois"',
            build_query({
                **base,
                "city": "Springfield",
                "state": "Illinois",
                "disambiguate": False,
            }),
        )

    def test_normalize_location_filters_validates_and_prefers_custom_city(self):
        with self.assertRaises(ValueError):
            normalize_filters(MultiDict({"country": "ZZ"}))
        empty = normalize_filters(MultiDict({"country": "", "role": ""}))
        self.assertEqual(empty["city"], "")
        custom = normalize_filters(MultiDict({
            "country": "IN",
            "state": "Tamil Nadu",
            "city": "Coimbatore",
            "city_custom": "Pollachi",
            "role": "",
        }))
        self.assertEqual(custom["city"], "Pollachi")
        unsafe = normalize_filters(MultiDict({
            "city": 'Springfield" (IL) \\',
            "role": "",
        }))
        self.assertEqual(unsafe["city"], "Springfield IL")
        safe_query = build_query({
            "mode": "browse",
            "role": "",
            "city": unsafe["city"],
            "org_name": "",
            "filter_status": "any",
            "exclude_words": "",
        })
        self.assertIn('"Springfield IL"', safe_query)
        self.assertNotIn('\\"', safe_query)
        with self.assertRaises(ValueError):
            normalize_filters(MultiDict({"city": "x" * 81}))

    def test_location_cache_key_includes_country_code(self):
        base = {
            "role": "video editor",
            "city": "Springfield",
            "state": "",
            "org_name": "",
            "filter_status": "any",
            "exclude_words": "",
        }
        from app import location_cache_key
        self.assertNotEqual(
            location_cache_key({**base, "country": "US"}),
            location_cache_key({**base, "country": "CA"}),
        )

    def test_search_page_has_world_countries_and_default_location(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'option value="IN" selected>India', response.data)
        self.assertIn(b'data-initial-state="Tamil Nadu"', response.data)
        self.assertIn(b'data-initial-city="Coimbatore"', response.data)
        self.assertIn(b'option value="">Anywhere (worldwide)</option>', response.data)

    def test_india_location_file_is_served_and_has_tamil_nadu_cities(self):
        response = self.client.get("/static/data/locations/IN.json")
        self.assertEqual(response.status_code, 200)
        locations = json.loads(response.data)
        tamil_nadu = next(
            state for state in locations["states"] if state["name"] == "Tamil Nadu"
        )
        self.assertIn("Coimbatore", tamil_nadu["cities"])
        indian_states = {
            "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
            "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
            "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram",
            "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu",
            "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
        }
        by_name = {state["name"]: state for state in locations["states"]}
        self.assertTrue(indian_states.issubset(by_name))
        self.assertTrue(
            all(len(by_name[name]["cities"]) >= 15 for name in indian_states)
        )

    @patch("app.search_web")
    def test_search_location_sets_serper_country_and_rerenders_filters(self, mock_search):
        mock_search.return_value = []
        response = self.client.post(
            "/search",
            data={
                "role": "content creator",
                "country": "US",
                "state": "",
                "city": "",
                "org_type": "",
                "filter_status": "any",
                "exclude_words": "recruiter",
                "per_page": "5",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_search.call_args.kwargs["gl"], "us")
        self.assertIn(b'option value="content creator" selected', response.data)
        self.assertIn(b'option value="US" selected', response.data)
        self.assertIn(b'option value="5" selected', response.data)
        self.assertIn(b"Location: United States", response.data)
        restored = self.client.get("/")
        self.assertIn(b'option value="US" selected', restored.data)

    @patch("app.search_web")
    def test_empty_country_omits_serper_gl(self, mock_search):
        mock_search.return_value = []
        response = self.client.post(
            "/search",
            data={"role": "video editor", "country": "", "city": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("gl", mock_search.call_args.kwargs)

    @patch("app.search_web")
    def test_cache_is_separate_for_same_city_in_different_countries(self, mock_search):
        mock_search.return_value = []
        for country in ("US", "CA"):
            response = self.client.post(
                "/search",
                data={
                    "role": "video editor",
                    "country": country,
                    "state": "",
                    "city": "Springfield",
                    "per_page": "5",
                },
            )
            self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_search.call_count, 2)

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
        first_page = [{
                "title": "A Person - Video Editor",
                "link": "https://www.linkedin.com/in/a-person",
                "snippet": "Edits short films.",
            }]
        mock_search.side_effect = lambda _query, num, page: first_page if page == 2 else []
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
        mock_search.assert_any_call(
            'site:linkedin.com/in "video editor" Coimbatore -recruiter',
            num=5,
            page=2,
        )
        mock_search.assert_any_call(
            'site:linkedin.com/in "video editor" Coimbatore -recruiter',
            num=5,
            page=3,
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

    @patch("app.search_web")
    def test_search_results_show_shortlist_all_for_selectable_profiles(self, mock_search):
        saved_url = "https://www.linkedin.com/in/selection-saved"
        mock_search.return_value = [
            {"title": "Selectable One", "link": "https://www.linkedin.com/in/selectable-one", "snippet": "Editor"},
            {"title": "Saved Result", "link": saved_url, "snippet": "Editor"},
            {"title": "Selectable Two", "link": "https://www.linkedin.com/in/selectable-two", "snippet": "Editor"},
        ]
        with connect(self.database_path) as connection:
            connection.execute(
                "INSERT INTO people (name, url) VALUES (?, ?)",
                ("Saved Result", saved_url),
            )
        response = self.client.post(
            "/search",
            data={
                "role": "video editor",
                "city": "Coimbatore",
                "show_saved": "1",
                "per_page": "5",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"This page (2)", response.data)
        self.assertIn(b"All discovered so far (2)", response.data)
        self.assertIn(b"0 of 2 selected", response.data)
        self.assertIn(b'data-selectable-count="2"', response.data)
        saved_checkbox = re.search(
            rb'<input class="profile-check"[^>]*aria-label="Shortlist Saved Result"[^>]*>',
            response.data,
        )
        self.assertIsNotNone(saved_checkbox)
        self.assertIn(b"disabled", saved_checkbox.group(0))

    @patch("app.search_web")
    def test_pool_tracks_profiles_across_pages_for_all_discovered_scope(self, mock_search):
        def result_page(page):
            return [
                {
                    "title": f"Pool Person {page}-{index} - Video Editor",
                    "link": f"https://www.linkedin.com/in/pool-person-{page}-{index}",
                    "snippet": "Video editor",
                }
                for index in range(10)
            ]

        mock_search.side_effect = lambda _query, num, page, **kwargs: result_page(page)
        filters = {"role": "video editor", "city": "Coimbatore", "per_page": "10"}
        first = self.client.post("/search", data=filters)
        self.assertIn(b"All discovered so far (10)", first.data)
        next_page = self.client.post(
            "/search",
            data={**filters, "raw_page_cursor": "2", "prev_cursors": "1"},
        )
        self.assertIn(b"All discovered so far (20)", next_page.data)
        third_page = self.client.post(
            "/search",
            data={**filters, "raw_page_cursor": "3", "prev_cursors": "1,2"},
        )
        self.assertIn(b"All discovered so far (30)", third_page.data)
        with connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM search_pool").fetchone()[0],
                30,
            )

    def test_shortlist_pool_adds_all_skips_saved_and_is_idempotent(self):
        search_key = "a" * 64
        profiles = [
            {
                "name": f"Bulk Pool {index}",
                "url": f"https://www.linkedin.com/in/bulk-pool-{index}",
                "headline": "Video Editor",
                "snippet": "Video editing",
            }
            for index in range(30)
        ]
        with connect(self.database_path) as connection:
            connection.execute(
                "INSERT INTO people (name, url) VALUES (?, ?)",
                ("Existing Pool Profile", profiles[0]["url"]),
            )
            for profile in profiles:
                connection.execute(
                    """INSERT INTO search_pool
                       (search_key, url, profile_json, found_at) VALUES (?, ?, ?, ?)""",
                    (
                        search_key,
                        profile["url"],
                        json.dumps(profile),
                        "2026-11-07T06:00:00+00:00",
                    ),
                )
        response = self.client.post(
            "/shortlist/pool",
            data={"search_key": search_key, "confirm_all": "1"},
        )
        self.assertEqual(response.status_code, 302)
        with connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM people").fetchone()[0],
                30,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM status_history").fetchone()[0],
                29,
            )
        second = self.client.post(
            "/shortlist/pool",
            data={"search_key": search_key, "confirm_all": "1"},
            follow_redirects=True,
        )
        self.assertIn(b"Shortlisted 0 profiles (30 already saved, skipped)", second.data)

    def test_shortlist_pool_enforces_200_profile_cap(self):
        search_key = "b" * 64
        with connect(self.database_path) as connection:
            for index in range(230):
                profile = {
                    "name": f"Cap Person {index}",
                    "url": f"https://www.linkedin.com/in/cap-person-{index}",
                }
                connection.execute(
                    """INSERT INTO search_pool
                       (search_key, url, profile_json, found_at) VALUES (?, ?, ?, ?)""",
                    (
                        search_key,
                        profile["url"],
                        json.dumps(profile),
                        "2026-11-07T06:00:00+00:00",
                    ),
                )
        response = self.client.post(
            "/shortlist/pool",
            data={"search_key": search_key, "confirm_all": "1"},
            follow_redirects=True,
        )
        self.assertIn(b"Shortlisted 200 profiles", response.data)
        self.assertIn(b"30 profiles left", response.data)
        with connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM people").fetchone()[0],
                200,
            )

    @patch("app.search_web")
    def test_collect_uses_at_most_five_calls_and_stops_at_fifty(self, mock_search):
        mock_search.side_effect = lambda _query, num, page, **kwargs: [
            {
                "title": f"Collected {page}-{index} - Video Editor",
                "link": f"https://www.linkedin.com/in/collected-{page}-{index}",
                "snippet": "Video editor",
            }
            for index in range(10)
        ]
        filters = {"role": "video editor", "city": "Coimbatore", "per_page": "10"}
        self.client.post("/search", data=filters)
        collected = self.client.post(
            "/search", data={**filters, "collect": "1", "raw_page_cursor": "1"}
        )
        self.assertEqual(collected.status_code, 200)
        self.assertEqual(mock_search.call_count, 5)
        self.assertIn(b"All discovered so far (50)", collected.data)
        self.assertIn(b"Collected 40 profiles. Used 4 credits.", collected.data)

    @patch("app.search_web")
    def test_collect_uses_cached_pages_without_counting_them_as_credits(self, mock_search):
        mock_search.side_effect = lambda _query, num, page, **kwargs: [
            {
                "title": f"Cached Collect {page}-{index} - Video Editor",
                "link": f"https://www.linkedin.com/in/cached-collect-{page}-{index}",
                "snippet": "Video editor",
            }
            for index in range(10)
        ]
        filters = {"role": "video editor", "city": "Coimbatore", "per_page": "10"}
        self.client.post("/search", data=filters)
        normalized = normalize_filters(MultiDict(filters))
        cache_key = location_cache_key(normalized)
        cached_results = [
            {
                "title": f"Cached Collect 2-{index} - Video Editor",
                "link": f"https://www.linkedin.com/in/cached-collect-2-{index}",
                "snippet": "Video editor",
            }
            for index in range(10)
        ]
        with connect(self.database_path) as connection:
            connection.execute(
                """INSERT INTO search_cache
                   (query, page, per_page, results_json, cached_at)
                   VALUES (?, 2, 10, ?, ?)""",
                (cache_key, json.dumps(cached_results), datetime.now().timestamp()),
            )

        collected = self.client.post(
            "/search", data={**filters, "collect": "1", "raw_page_cursor": "1"}
        )

        self.assertEqual(mock_search.call_count, 4)
        self.assertIn(b"All discovered so far (50)", collected.data)
        self.assertIn(b"Collected 40 profiles. Used 3 credits.", collected.data)

    @patch("app.search_web")
    def test_collect_keeps_earlier_pages_when_later_page_errors(self, mock_search):
        def search(_query, num, page, **kwargs):
            if page == 4:
                raise SearchAPIError(429)
            return [
                {
                    "title": f"Partial {page}-{index} - Video Editor",
                    "link": f"https://www.linkedin.com/in/partial-{page}-{index}",
                    "snippet": "Video editor",
                }
                for index in range(10)
            ]

        mock_search.side_effect = search
        filters = {"role": "video editor", "city": "Coimbatore", "per_page": "10"}
        self.client.post("/search", data=filters)
        collected = self.client.post(
            "/search", data={**filters, "collect": "1", "raw_page_cursor": "1"}
        )
        self.assertIn(b"All discovered so far (30)", collected.data)
        self.assertIn(b"Serper rate limit reached or search credits exhausted", collected.data)
        with connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM search_pool").fetchone()[0],
                30,
            )

    def test_search_key_ignores_page_size_and_changes_with_role_or_location(self):
        filters = {
            "role": "video editor",
            "country": "IN",
            "state": "Tamil Nadu",
            "city": "Coimbatore",
            "org_type": "",
            "org_name": "",
            "filter_status": "any",
            "batch_year": "",
            "widen_search": False,
            "disambiguate": False,
            "exclude_words": "recruiter",
            "page": 1,
            "per_page": 5,
        }
        self.assertEqual(
            build_search_key(filters),
            build_search_key({**filters, "page": 4, "per_page": 10}),
        )
        self.assertNotEqual(
            build_search_key(filters),
            build_search_key({**filters, "role": "content creator"}),
        )
        self.assertNotEqual(
            build_search_key(filters),
            build_search_key({**filters, "city": "Chennai"}),
        )

    def test_pool_expired_rows_are_deleted_on_database_startup(self):
        search_key = "c" * 64
        with connect(self.database_path) as connection:
            connection.execute(
                """INSERT INTO search_pool
                   (search_key, url, profile_json, found_at)
                   VALUES (?, ?, ?, ?)""",
                (
                    search_key,
                    "https://www.linkedin.com/in/expired-pool",
                    "{}",
                    "2026-10-01T00:00:00+00:00",
                ),
            )
        init_db(self.database_path)
        with connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM search_pool").fetchone()[0],
                0,
            )

    def test_shortlist_post_saves_selected_profiles_once_and_skips_duplicates(self):
        profiles = [
            {
                "name": "Bulk One",
                "url": "https://www.linkedin.com/in/bulk-one",
            },
            {
                "name": "Bulk Two",
                "url": "https://www.linkedin.com/in/bulk-two",
            },
            {
                "name": "Bulk One Duplicate",
                "url": "https://www.linkedin.com/in/bulk-one",
            },
        ]
        response = self.client.post(
            "/shortlist",
            data={"profile": [json.dumps(profile) for profile in profiles]},
        )
        self.assertEqual(response.status_code, 302)
        with connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT url, COUNT(*) AS count FROM people GROUP BY url ORDER BY url"
            ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["count"] == 1 for row in rows))

    @patch("app.search_web")
    def test_browse_backfills_to_requested_usable_count(self, mock_search):
        first_page = [
            {"title": f"Article {index}", "link": f"https://example.com/{index}", "snippet": ""}
            for index in range(6)
        ]
        first_page.extend(
            [
                {
                    "title": "Already Saved",
                    "link": "https://www.linkedin.com/in/already-saved-backfill",
                    "snippet": "Editor",
                },
                *[
                    {
                        "title": f"First Page {index} - Video Editor",
                        "link": f"https://www.linkedin.com/in/first-page-{index}",
                        "snippet": "Editor",
                    }
                    for index in range(3)
                ],
            ]
        )
        second_page = [
            {
                "title": f"Second Page {index} - Video Editor",
                "link": f"https://www.linkedin.com/in/second-page-{index}",
                "snippet": "Editor",
            }
            for index in range(10)
        ]
        mock_search.side_effect = lambda _query, num, page: (
            first_page if page == 1 else second_page if page == 2 else []
        )
        with connect(self.database_path) as connection:
            connection.execute(
                "INSERT INTO people (name, url) VALUES (?, ?)",
                ("Already Saved", "https://www.linkedin.com/in/already-saved-backfill"),
            )

        response = self.client.post(
            "/search",
            data={"role": "video editor", "city": "Coimbatore", "per_page": "5"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.count(b'class="profile-name"'), 5)
        self.assertIn(b"Serper returned 20 results", response.data)
        self.assertIn(b"6 were not profile links", response.data)
        self.assertIn(b"1 already in your shortlist", response.data)
        self.assertIn(b"showing 5. Used 2 credits", response.data)
        self.assertEqual(mock_search.call_count, 2)

    @patch("app.search_web")
    def test_browse_next_is_available_for_short_raw_page(self, mock_search):
        mock_search.return_value = [
            {
                "title": f"Short Page {index} - Video Editor",
                "link": f"https://www.linkedin.com/in/short-page-{index}",
                "snippet": "Editor",
            }
            for index in range(7)
        ]
        response = self.client.post(
            "/search",
            data={"role": "video editor", "city": "Coimbatore", "per_page": "5"},
        )
        self.assertEqual(response.data.count(b'class="profile-name"'), 5)
        self.assertIn(b"Next", response.data)
        self.assertIn(b'name="raw_page_cursor" value="2"', response.data)

    @patch("app.search_web")
    def test_browse_backfill_respects_three_call_cap(self, mock_search):
        mock_search.return_value = [
            {"title": f"Article {i}", "link": f"https://example.com/{i}", "snippet": ""}
            for i in range(10)
        ]
        response = self.client.post(
            "/search",
            data={"role": "video editor", "city": "Coimbatore", "per_page": "5"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_search.call_count, 3)
        self.assertIn(b"Only 0 usable profiles found for this search", response.data)
        self.assertNotIn(b"flash-error", response.data)

    @patch("app.search_web")
    def test_browse_previous_uses_raw_cursor_history_and_cache(self, mock_search):
        page_results = {
            page: [
                {
                    "title": f"Page {page} Person {index} - Video Editor",
                    "link": f"https://www.linkedin.com/in/page-{page}-{index}",
                    "snippet": "Editor",
                }
                for index in range(5)
            ]
            for page in (1, 2)
        }
        mock_search.side_effect = lambda _query, num, page: page_results.get(page, [])
        form = {
            "role": "video editor",
            "city": "Coimbatore",
            "per_page": "5",
        }
        first = self.client.post("/search", data=form)
        next_page = self.client.post(
            "/search",
            data={**form, "raw_page_cursor": "2", "prev_cursors": "1"},
        )
        self.assertIn(b"Page 2", next_page.data)
        previous = self.client.post(
            "/search",
            data={**form, "raw_page_cursor": "1", "prev_cursors": ""},
        )
        self.assertIn(b"Page 1", previous.data)
        self.assertIn(b"Page 1 Person 0", previous.data)
        self.assertIn(b"Used 0 credits", previous.data)
        self.assertEqual(mock_search.call_count, 2)
        self.assertEqual(first.status_code, 200)

    @patch("app.search_web")
    def test_second_page_error_keeps_first_page_profiles(self, mock_search):
        first_page = [
            {
                "title": f"First Page {index} - Video Editor",
                "link": f"https://www.linkedin.com/in/error-first-{index}",
                "snippet": "Editor",
            }
            for index in range(2)
        ]
        mock_search.side_effect = [first_page, SearchAPIError(429)]
        response = self.client.post(
            "/search",
            data={"role": "video editor", "city": "Coimbatore", "per_page": "5"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"First Page 0", response.data)
        self.assertIn(b"First Page 1", response.data)
        self.assertIn(b"rate limit reached or search credits exhausted", response.data)
        self.assertEqual(mock_search.call_count, 2)

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
        self.assertIn(b"1 already in your shortlist", response.data)
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
        first_page = [{
            "title": "Cache Person",
            "link": "https://www.linkedin.com/in/cache-person-123",
            "snippet": "Video editor",
        }]
        mock_search.side_effect = lambda _query, num, page: first_page if page == 1 else []
        data = {"role": "video editor", "city": "Coimbatore", "per_page": "5"}
        first = self.client.post("/search", data=data)
        second = self.client.post("/search", data=data)
        refreshed = self.client.post("/search", data={**data, "refresh": "1"})
        self.assertEqual(first.status_code, 200)
        self.assertIn(b"Cached result", second.data)
        self.assertIn(b"Used 0 credits", second.data)
        self.assertEqual(mock_search.call_count, 3)
        self.assertEqual(refreshed.status_code, 200)
        with connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM search_cache").fetchone()[0],
                2,
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
