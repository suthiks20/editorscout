import contextlib
import io
import tempfile
import unittest
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app import create_app
from database import connect
from profile_urls import normalize_linkedin_url
from scripts.seed_demo import demo_tracker_totals, main


IST = ZoneInfo("Asia/Kolkata")


class SeedDemoTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = str(Path(self.temp_dir.name) / "demo.sqlite3")

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_script(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main([*args, "--db", self.database_path, "--seed", "42"])
        self.assertEqual(result, 0)
        return output.getvalue()

    def test_seed_is_reproducible_unique_balanced_and_renders_in_app(self):
        output = self.run_script("--count", "200")
        self.assertIn("Added 200 demo people", output)
        self.assertIn("Added 0 demo people; skipped 200 existing URLs", self.run_script("--count", "200"))

        with connect(self.database_path) as connection:
            people = connection.execute(
                """SELECT id, name, url, personal_note, status, shortlisted_at,
                          touch_count, follow_up_date, instagram_handle,
                          verified_status, batch_id
                   FROM people WHERE instr(url, '/in/demo-') > 0"""
            ).fetchall()
            self.assertEqual(len(people), 200)
            self.assertEqual(len({person["url"] for person in people}), 200)
            self.assertTrue(
                all(person["url"].startswith("https://www.linkedin.com/in/demo-") for person in people)
            )
            self.assertTrue(all(normalize_linkedin_url(person["url"]) == person["url"] for person in people))
            self.assertTrue(all(person["personal_note"].startswith("[DEMO]") for person in people))

            status_counts = Counter(person["status"] for person in people)
            expected_counts = {
                "shortlisted": 90,
                "sent": 40,
                "connected": 30,
                "replied": 20,
                "agreed": 8,
                "rejected": 12,
            }
            self.assertEqual(status_counts, expected_counts)
            shortlist_dates = {
                datetime.fromisoformat(person["shortlisted_at"]).astimezone(IST).date()
                for person in people
            }
            self.assertGreaterEqual(len(shortlist_dates), 5)

            batch_ids = {person["batch_id"] for person in people}
            self.assertEqual(len(batch_ids), 1)
            batch = connection.execute(
                "SELECT name, is_current FROM batches WHERE id = ?",
                (next(iter(batch_ids)),),
            ).fetchone()
            self.assertEqual(batch["name"], "Demo data")
            self.assertEqual(batch["is_current"], 0)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM status_history").fetchone()[0],
                200,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM person_links WHERE platform = 'instagram' AND confirmed = 1"
                ).fetchone()[0],
                60,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM people WHERE verified_status = 'current'"
                ).fetchone()[0],
                40,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
                80,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM sent_messages sm JOIN people p ON p.id = sm.person_id WHERE p.status = 'sent'"
                ).fetchone()[0],
                40,
            )

        app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-key",
                "DATABASE_PATH": self.database_path,
                "SERPER_API_KEY": "test-api-key",
            }
        )
        client = app.test_client()
        tracker = client.get("/tracker")
        self.assertEqual(tracker.status_code, 200)
        self.assertIn(b"Demo data", tracker.data)
        shortlist = client.get(
            f"/shortlist?date={min(shortlist_dates).isoformat()}"
        )
        self.assertEqual(shortlist.status_code, 200)
        self.assertIn(b"[DEMO]", shortlist.data)

    def test_remove_deletes_only_marked_demo_people_and_related_rows(self):
        self.run_script("--count", "200")
        with connect(self.database_path) as connection:
            connection.execute(
                """INSERT INTO people (name, url, personal_note, status)
                   VALUES ('Real Person', 'https://www.linkedin.com/in/real-person-123',
                           'Keep this record', 'new')"""
            )
            real_person_id = connection.execute(
                "SELECT id FROM people WHERE url = ?",
                ("https://www.linkedin.com/in/real-person-123",),
            ).fetchone()["id"]

        output = self.run_script("--remove")
        self.assertIn("Removed 200 demo people.", output)
        with connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM people WHERE instr(url, '/in/demo-') > 0 AND substr(personal_note, 1, 6) = '[DEMO]'"
                ).fetchone()[0],
                0,
            )
            self.assertIsNotNone(
                connection.execute(
                    "SELECT id FROM people WHERE id = ?", (real_person_id,)
                ).fetchone()
            )
            for table in ("person_links", "messages", "sent_messages", "status_history"):
                self.assertEqual(
                    connection.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE person_id != ?",
                        (real_person_id,),
                    ).fetchone()[0],
                    0,
                )

    def test_exact186_profile_matches_tracker_counts_and_is_replaceable(self):
        output = self.run_script("--profile", "exact186", "--count", "1")
        self.assertIn(
            "Total 186 · Sent 178 · Connected 10 · Replied 4 · Follow-ups due 8 · Sent today ",
            output,
        )
        self.assertIn(
            "Total 186 · Sent 178 · Connected 10 · Replied 4 · Follow-ups due 8 · Sent today ",
            self.run_script("--profile", "exact186", "--count", "1"),
        )

        with connect(self.database_path) as connection:
            people = connection.execute(
                """SELECT status, follow_up_date, sent_at, personal_note, url
                   FROM people"""
            ).fetchall()
            counts = Counter(person["status"] for person in people)
            self.assertEqual(len(people), 186)
            self.assertEqual(
                counts,
                {"sent": 164, "connected": 10, "replied": 4, "shortlisted": 8},
            )
            self.assertTrue(all(person["personal_note"].startswith("[DEMO]") for person in people))
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM messages"
                ).fetchone()[0],
                178,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM sent_messages"
                ).fetchone()[0],
                178,
            )
            today = datetime.now(IST).date().isoformat()
            due_sent = sum(
                person["status"] == "sent"
                and person["follow_up_date"]
                and person["follow_up_date"] <= today
                for person in people
            )
            self.assertEqual(due_sent, 8)
            self.assertTrue(
                all(
                    person["follow_up_date"] > today
                    for person in people
                    if person["status"] == "sent" and person["follow_up_date"] > today
                )
            )

        totals = demo_tracker_totals(self.database_path)
        self.assertEqual(
            totals,
            {
                "total": 186,
                "sent": 178,
                "connected": 10,
                "replied": 4,
                "due": 8,
                "sent_today": totals["sent_today"],
            },
        )
        self.assertLessEqual(totals["sent_today"], 10)

        app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-key",
                "DATABASE_PATH": self.database_path,
                "SERPER_API_KEY": "test-api-key",
            }
        )
        client = app.test_client()
        tracker = client.get("/tracker")
        self.assertEqual(tracker.status_code, 200)
        for value in (186, 178, 10, 4, 8):
            self.assertIn(f"<strong>{value}</strong>".encode(), tracker.data)
        self.assertNotIn(b"You've passed 10 today", tracker.data)

        due_response = client.get("/tracker?due=1")
        self.assertEqual(due_response.status_code, 200)
        self.assertEqual(due_response.data.count(b'class="tracker-card"'), 8)

    def test_exact186_replaces_only_demo_profiles_and_preserves_real_person(self):
        self.run_script("--count", "12")
        with connect(self.database_path) as connection:
            connection.execute(
                """INSERT INTO people (name, url, personal_note, status)
                   VALUES ('Real Person', 'https://www.linkedin.com/in/real-person-456',
                           'Keep this record', 'new')"""
            )

        output = self.run_script("--profile", "exact186")
        self.assertIn("Replaced 12 existing demo people.", output)
        self.assertIn("Total 187 · Sent 178 · Connected 10 · Replied 4 · Follow-ups due 8 · Sent today ", output)
        with connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM people WHERE instr(url, '/in/demo-') > 0 AND substr(personal_note, 1, 6) = '[DEMO]'"
                ).fetchone()[0],
                186,
            )
            self.assertIsNotNone(
                connection.execute(
                    "SELECT id FROM people WHERE url = ?",
                    ("https://www.linkedin.com/in/real-person-456",),
                ).fetchone()
            )
        self.assertIn("Removed 186 demo people.", self.run_script("--remove"))
        with connect(self.database_path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM people").fetchone()[0], 1)

    def test_count_over_limit_is_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                main(["--count", "1001", "--db", self.database_path])
        self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
