import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from profile_urls import normalize_linkedin_url


@contextmanager
def connect(database_path):
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.create_function(
        "normalize_linkedin_url", 1, normalize_linkedin_url, deterministic=True
    )
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db(database_path, schema_path=None):
    schema_file = Path(schema_path) if schema_path else Path(__file__).with_name("schema.sql")
    with connect(database_path) as connection:
        connection.executescript(schema_file.read_text(encoding="utf-8"))
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(people)")
        }
        additions = {
            "org_type": "TEXT NOT NULL DEFAULT ''",
            "org_name": "TEXT NOT NULL DEFAULT ''",
            "status_hint": "TEXT NOT NULL DEFAULT 'unclear'",
            "years_text": "TEXT NOT NULL DEFAULT ''",
            "verified_status": "TEXT NOT NULL DEFAULT 'unverified'",
            "verified_note": "TEXT NOT NULL DEFAULT ''",
            "batch_id": "INTEGER REFERENCES batches(id)",
            "shortlisted_at": "TEXT",
            "status_changed_at": "TEXT",
            "removed_from_shortlist": "INTEGER NOT NULL DEFAULT 0",
        }
        for column, definition in additions.items():
            if column not in columns:
                connection.execute(f"ALTER TABLE people ADD COLUMN {column} {definition}")
        if "college" in columns:
            connection.execute(
                """UPDATE people SET org_name = college, org_type = 'College'
                   WHERE college != '' AND org_name = ''"""
            )
        connection.execute(
            """UPDATE people
               SET status_changed_at = COALESCE(status_changed_at, created_at)
               WHERE status_changed_at IS NULL"""
        )
        connection.execute(
            """UPDATE people
               SET sent_at = COALESCE(sent_at, status_changed_at, created_at)
               WHERE status IN ('sent', 'replied', 'agreed') AND sent_at IS NULL"""
        )
        unbatched = connection.execute(
            "SELECT COUNT(*) FROM people WHERE batch_id IS NULL"
        ).fetchone()[0]
        if unbatched:
            earlier = connection.execute(
                "SELECT id FROM batches WHERE name = 'Earlier outreach' "
                "ORDER BY id LIMIT 1"
            ).fetchone()
            if earlier is None:
                cursor = connection.execute(
                    """INSERT INTO batches (name, created_at, is_current)
                       VALUES ('Earlier outreach', ?, 0)""",
                    (datetime.now().astimezone().isoformat(timespec="seconds"),),
                )
                batch_id = cursor.lastrowid
            else:
                batch_id = earlier["id"]
            connection.execute(
                """UPDATE people SET batch_id = ?,
                   shortlisted_at = COALESCE(shortlisted_at, created_at)
                   WHERE batch_id IS NULL""",
                (batch_id,),
            )
        duplicate_urls = connection.execute(
            """SELECT COUNT(*) FROM (
                   SELECT normalize_linkedin_url(url) AS normalized_url
                   FROM people
                   GROUP BY normalized_url
                   HAVING normalized_url IS NOT NULL AND COUNT(*) > 1
               )"""
        ).fetchone()[0]
        if not duplicate_urls:
            connection.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS idx_people_normalized_url
                   ON people(normalize_linkedin_url(url))"""
            )
        current_batches = connection.execute(
            "SELECT id FROM batches WHERE is_current = 1 ORDER BY id DESC"
        ).fetchall()
        if len(current_batches) > 1:
            keep_id = current_batches[0]["id"]
            connection.execute(
                "UPDATE batches SET is_current = 0 WHERE is_current = 1 AND id != ?",
                (keep_id,),
            )
        connection.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_batches_one_current
               ON batches(is_current) WHERE is_current = 1"""
        )
        connection.execute(
            """INSERT INTO status_history (person_id, old_status, new_status, changed_at)
               SELECT p.id, NULL, p.status, COALESCE(p.status_changed_at, p.created_at)
               FROM people p
               WHERE NOT EXISTS (
                   SELECT 1 FROM status_history h WHERE h.person_id = p.id
               )"""
        )
