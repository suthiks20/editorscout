#!/usr/bin/env python3
"""Seed or remove clearly marked EditorScout demo profiles."""

import argparse
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import Config
from database import connect, init_db


IST = ZoneInfo("Asia/Kolkata")
MAX_COUNT = 1000
DEMO_NOTE = "[DEMO] Fictional profile created for EditorScout UI testing."
STATUS_WEIGHTS = (
    ("shortlisted", 45),
    ("sent", 20),
    ("connected", 15),
    ("replied", 10),
    ("agreed", 4),
    ("rejected", 6),
)
ROLE_WEIGHTS = (
    ("Video Editor", 30),
    ("Content Creator", 25),
    ("Videographer", 12),
    ("Motion Designer", 8),
    ("Photographer", 7),
    ("Social Media Manager", 8),
    ("Graphic Designer", 5),
    ("Other", 5),
)
FIRST_NAMES = (
    "Aarvilo", "Aaryun", "Adhivya", "Adviro", "Akhvian", "Amrilo",
    "Anviro", "Arhanya", "Avyara", "Bhanvith", "Charvilo", "Chayara",
    "Dharvian", "Divyaro", "Eshvani", "Gaurvilo", "Harvian", "Ishviro",
    "Jhanvya", "Kairvilo", "Kaviro", "Lakshvian", "Mahviro", "Mihanya",
    "Navrilo", "Neyvian", "Ojasviro", "Pranvya", "Ravilo", "Rishvian",
    "Saanviro", "Tanvya", "Urvilo", "Vayvian", "Yashviro", "Zarvya",
    "Alviora", "Bexlian", "Calviro", "Denvira", "Elowynx", "Faylora",
    "Gavrielx", "Halvian", "Iverlyn", "Jorvian", "Kelmora", "Lunviro",
    "Marlune", "Nivelle", "Orvian", "Paxlora", "Quenvira", "Rovelle",
    "Sylvianx", "Tavelle", "Umbrin", "Vellora", "Wynviro", "Zenvira",
)
LAST_NAMES = (
    "Aarvani", "Aadhyaro", "Anvirel", "Arvilo", "Bhanvani", "Chayviro",
    "Dharvilo", "Eshvani", "Gaurviro", "Harvani", "Ishvilo", "Jhanviro",
    "Kairvani", "Kaviro", "Lakshvilo", "Mahvani", "Mihviro", "Navrani",
    "Neyvilo", "Ojasvani", "Pranviro", "Rishvani", "Saanvilo", "Tanviro",
    "Urvani", "Vayvilo", "Yashvani", "Zarviro", "Amrani", "Charvilo",
    "Divyani", "Avyaro", "Ravani", "Kelmviro", "Nimvani", "Suryvilo",
    "Alverune", "Bexlora", "Calderyn", "Denvell", "Eldervane", "Farlune",
    "Gavelle", "Halverin", "Ivervale", "Jorvane", "Kelmora", "Lunmere",
    "Marloweck", "Niverton", "Orvelle", "Paxmere", "Quenvell", "Roveland",
    "Sylverin", "Taverton", "Umberly", "Vellmere", "Wynforde", "Zenvell",
)
CITIES = ("Coimbatore", "Chennai", "Bengaluru", "Mumbai", "London", "Toronto", "Dubai")
ORGANISATIONS = (
    ("College", "Lumen Arts College"),
    ("College", "Silver Fern Creative Institute"),
    ("College", "Northbridge Design Academy"),
    ("College", "Blue Orchard Media School"),
    ("College", "Velvet Pine Arts Campus"),
    ("Freelance", "Independent"),
    ("Freelance", "Studio of {last}"),
    ("Freelance", "Personal Creative Practice"),
    ("Company", "Northbridge Media Studio"),
    ("Company", "Pixel Harbour Films"),
    ("Company", "Copper Kite Creative"),
    ("Company", "Willowframe Works"),
    ("Company", "Lantern Loop Media"),
    ("Company", "Cloudberry Motion House"),
    ("Company", "Quiet Current Pictures"),
    ("Company", "Bluejay Post Collective"),
)
HEADLINE_TEMPLATES = {
    "Video Editor": (
        "Freelance Video Editor | Reels & YouTube",
        "Video Editor at {organisation}",
        "Video Editor | Short-form stories and clean cuts",
    ),
    "Content Creator": (
        "Content Creator at {organisation}",
        "Independent Content Creator | Video-first storytelling",
        "Content Creator | Short-form and social video",
    ),
    "Videographer": (
        "Freelance Videographer | Events & brand films",
        "Videographer at {organisation}",
    ),
    "Motion Designer": (
        "Motion Designer | Titles, explainers & animation",
        "Motion Designer at {organisation}",
    ),
    "Photographer": (
        "Photographer | Portraits and visual stories",
        "Photographer at {organisation}",
    ),
    "Social Media Manager": (
        "Social Media Manager at {organisation}",
        "Freelance Social Media Manager | Content planning",
    ),
    "Graphic Designer": (
        "Graphic Designer at {organisation}",
        "Independent Graphic Designer | Brand visuals",
    ),
    "Other": (
        "Other Creative | Demo portfolio",
        "Independent Creative | Demo portfolio",
    ),
}
DRAFT_TEMPLATES = (
    "Hi {first_name}, I liked your {role} work in {city}. I'd love to connect.",
    "Hi {first_name}, your creative work stood out to me. Open to connecting?",
    "Hi {first_name}, I enjoyed seeing your approach to {role_lower} projects.",
)


def _weighted_values(rng, count, weighted_items):
    total_weight = sum(weight for _, weight in weighted_items)
    allocations = []
    remainders = []
    allocated = 0
    for value, weight in weighted_items:
        exact = count * weight / total_weight
        whole = int(exact)
        allocations.append([value, whole])
        remainders.append((exact - whole, value))
        allocated += whole
    remainders.sort(reverse=True)
    for _, value in remainders[: count - allocated]:
        next(item for item in allocations if item[0] == value)[1] += 1
    values = [value for value, amount in allocations for _ in range(amount)]
    rng.shuffle(values)
    return values


def _timestamp_for_day(rng, day):
    seconds_after_midnight = rng.randint(9 * 60 * 60, 22 * 60 * 60)
    return (
        datetime.combine(day, datetime.min.time(), tzinfo=IST)
        + timedelta(seconds=seconds_after_midnight)
    ).isoformat(timespec="seconds")


def _get_demo_batch_id(connection):
    batch = connection.execute(
        "SELECT id FROM batches WHERE name = ? ORDER BY id LIMIT 1",
        ("Demo data",),
    ).fetchone()
    if batch:
        connection.execute("UPDATE batches SET is_current = 0 WHERE id = ?", (batch["id"],))
        return batch["id"]
    cursor = connection.execute(
        "INSERT INTO batches (name, is_current) VALUES (?, 0)", ("Demo data",)
    )
    return cursor.lastrowid


def seed_demo(database_path, count=200, seed=None, profile="random"):
    exact_profile = profile == "exact186"
    if exact_profile:
        count = 186
    elif profile != "random":
        raise ValueError(f"Unknown demo profile: {profile}")
    if count > MAX_COUNT:
        raise ValueError(f"--count cannot be greater than {MAX_COUNT}.")
    if count < 0:
        raise ValueError("--count must be zero or greater.")

    identity_rng = random.Random(seed)
    rng = random.Random(seed)
    roles = _weighted_values(identity_rng, count, ROLE_WEIGHTS)
    if exact_profile:
        statuses = _weighted_values(
            identity_rng,
            count,
            (
                ("sent", 164),
                ("connected", 10),
                ("replied", 4),
                ("shortlisted", 8),
            ),
        )
    else:
        statuses = _weighted_values(identity_rng, count, STATUS_WEIGHTS)
    today = datetime.now(IST).date()
    inserted = 0
    skipped = 0
    used_slugs = set()
    instagram_flags = _weighted_values(rng, count, ((True, 30), (False, 70)))
    verified_flags = _weighted_values(rng, count, ((True, 20), (False, 80)))
    contacted_statuses = {"sent", "connected", "replied", "agreed"}
    contacted_indexes = {
        index for index, status in enumerate(statuses) if status in contacted_statuses
    }
    sent_indexes = {index for index, status in enumerate(statuses) if status == "sent"}
    if exact_profile:
        draft_indexes = set(contacted_indexes)
        follow_up_due_indexes = set(
            rng.sample(
                [index for index in sent_indexes],
                8,
            )
        )
        sent_today_indexes = set(rng.sample(list(contacted_indexes), 8))
    else:
        draft_count = min(count, max(round(count * 0.4), len(sent_indexes)))
        draft_indexes = set(sent_indexes)
        remaining_draft_indexes = [
            index for index in range(count) if index not in sent_indexes
        ]
        draft_indexes.update(
            rng.sample(remaining_draft_indexes, draft_count - len(draft_indexes))
        )

    with connect(database_path) as connection:
        batch_id = _get_demo_batch_id(connection) if count else None
        for index in range(count):
            first_name = identity_rng.choice(FIRST_NAMES)
            last_name = identity_rng.choice(LAST_NAMES)
            while True:
                number = identity_rng.randint(0, 9999)
                slug = f"demo-{first_name.lower()}-{last_name.lower()}-{number:04d}"
                if slug not in used_slugs:
                    used_slugs.add(slug)
                    break
            url = f"https://www.linkedin.com/in/{slug}"
            if connection.execute("SELECT 1 FROM people WHERE url = ?", (url,)).fetchone():
                skipped += 1
                continue

            role = roles[index]
            status = statuses[index]
            city = rng.choice(CITIES)
            org_type, org_template = rng.choice(ORGANISATIONS)
            org_name = org_template.format(last=last_name)
            headline = rng.choice(HEADLINE_TEMPLATES[role]).format(
                organisation=org_name
            )
            snippet = (
                f"Fictional {role.lower()} based in {city}, sharing original demo "
                "projects and creative experiments."
            )
            shortlist_day = today - timedelta(days=rng.randrange(10))
            shortlisted_at = _timestamp_for_day(rng, shortlist_day)
            outreached = status in contacted_statuses
            if exact_profile and outreached:
                sent_day = (
                    today
                    if index in sent_today_indexes
                    else today - timedelta(days=rng.randint(1, 10))
                )
                sent_at = _timestamp_for_day(rng, sent_day)
            else:
                sent_at = (
                    _timestamp_for_day(
                        rng,
                        shortlist_day
                        + timedelta(days=rng.randrange(min(2, (today - shortlist_day).days) + 1)),
                    )
                    if outreached
                    else None
                )
            status_changed_at = sent_at or shortlisted_at
            touch_count = 0 if status == "shortlisted" else rng.randint(1, 3)
            follow_up_date = ""
            if exact_profile and index in follow_up_due_indexes:
                follow_up_date = (today - timedelta(days=rng.randrange(3))).isoformat()
            elif exact_profile and status == "sent":
                follow_up_date = (today + timedelta(days=rng.randint(1, 5))).isoformat()
            elif exact_profile and status in {"connected", "replied"}:
                follow_up_date = (
                    (today + timedelta(days=rng.randint(1, 5))).isoformat()
                    if rng.random() < 0.5
                    else ""
                )
            elif not exact_profile and outreached and rng.random() < 0.7:
                follow_up_date = (
                    today + timedelta(days=rng.choice((-5, -4, -3, -2, -1, 0, 0, 1, 2, 3, 5)))
                ).isoformat()
            has_instagram = instagram_flags[index]
            instagram_handle = (
                f"demo.{first_name.lower()}{rng.randint(10, 999)}"
                if has_instagram
                else ""
            )
            verified_status = "current" if verified_flags[index] else "unverified"
            has_draft = index in draft_indexes
            draft_text = (
                rng.choice(DRAFT_TEMPLATES).format(
                    first_name=first_name,
                    role=role,
                    role_lower=role.lower(),
                    city=city,
                )
                if has_draft
                else None
            )
            created_at = shortlisted_at
            cursor = connection.execute(
                """INSERT INTO people (
                       name, headline, snippet, url, college, source, personal_note,
                       status, created_at, updated_at, sent_at, batch_id,
                       shortlisted_at, status_changed_at, org_type, org_name,
                       status_hint, verified_status, instagram_handle,
                       touch_count, follow_up_date
                   ) VALUES (?, ?, ?, ?, ?, 'LinkedIn', ?, ?, ?, ?, ?, ?, ?, ?,
                             ?, ?, 'unclear', ?, ?, ?, ?)""",
                (
                    f"{first_name} {last_name}",
                    headline,
                    snippet,
                    url,
                    org_name if org_type == "College" else "",
                    DEMO_NOTE,
                    status,
                    created_at,
                    status_changed_at,
                    sent_at,
                    batch_id,
                    shortlisted_at,
                    status_changed_at,
                    org_type,
                    org_name,
                    verified_status,
                    instagram_handle,
                    touch_count,
                    follow_up_date,
                ),
            )
            person_id = cursor.lastrowid
            connection.execute(
                """INSERT INTO status_history
                   (person_id, old_status, new_status, changed_at)
                   VALUES (?, 'new', ?, ?)""",
                (person_id, status, status_changed_at),
            )
            if has_instagram:
                connection.execute(
                    """INSERT INTO person_links
                       (person_id, platform, kind, url, title, snippet, handle,
                        confidence, confirmed)
                       VALUES (?, 'instagram', 'profile', ?, ?, ?, ?, 'likely', 1)""",
                    (
                        person_id,
                        f"https://www.instagram.com/{instagram_handle}/",
                        "Demo Instagram profile",
                        f"Fictional Instagram profile for {first_name}.",
                        instagram_handle,
                    ),
                )
            if draft_text:
                connection.execute(
                    """INSERT INTO messages (person_id, channel, draft_text, created_at)
                       VALUES (?, 'connection', ?, ?)""",
                    (person_id, draft_text, created_at),
                )
                if (exact_profile and outreached) or status == "sent":
                    connection.execute(
                        """INSERT INTO sent_messages (person_id, kind, text, sent_at)
                           VALUES (?, 'connection', ?, ?)""",
                        (person_id, draft_text, sent_at),
                    )
            inserted += 1

    return inserted, skipped


def remove_demo(database_path):
    with connect(database_path) as connection:
        demo_rows = connection.execute(
            """SELECT id FROM people
               WHERE instr(url, '/in/demo-') > 0
                 AND substr(personal_note, 1, 6) = '[DEMO]'"""
        ).fetchall()
        person_ids = [row["id"] for row in demo_rows]
        if person_ids:
            placeholders = ",".join("?" for _ in person_ids)
            for table in ("person_links", "messages", "sent_messages", "status_history"):
                connection.execute(
                    f"DELETE FROM {table} WHERE person_id IN ({placeholders})",
                    person_ids,
                )
            connection.execute(
                f"DELETE FROM people WHERE id IN ({placeholders})", person_ids
            )
    return len(person_ids)


def demo_tracker_totals(database_path):
    today = datetime.now(IST).date().isoformat()
    with connect(database_path) as connection:
        row = connection.execute(
            """SELECT
                   COUNT(*) AS total,
                   SUM(CASE WHEN status IN ('sent', 'connected', 'replied', 'agreed')
                                 OR touch_count >= 1 THEN 1 ELSE 0 END) AS sent,
                   SUM(CASE WHEN status = 'connected' THEN 1 ELSE 0 END) AS connected,
                   SUM(CASE WHEN status = 'replied' THEN 1 ELSE 0 END) AS replied,
                   SUM(CASE WHEN status != 'rejected'
                                 AND follow_up_date != ''
                                 AND follow_up_date <= ? THEN 1 ELSE 0 END) AS due
               FROM people""",
            (today,),
        ).fetchone()
        sent_timestamps = connection.execute(
            """SELECT sent_at FROM sent_messages
               UNION ALL
               SELECT p.sent_at FROM people p
               WHERE p.sent_at IS NOT NULL AND NOT EXISTS (
                   SELECT 1 FROM sent_messages sm
                   WHERE sm.person_id = p.id AND sm.sent_at = p.sent_at
               )"""
        ).fetchall()
    sent_today = sum(
        1
        for timestamp in sent_timestamps
        if (sent_time := _as_ist_datetime(timestamp["sent_at"])) is not None
        and sent_time.date() == datetime.now(IST).date()
    )
    return {
        "total": row["total"],
        "sent": row["sent"] or 0,
        "connected": row["connected"] or 0,
        "replied": row["replied"] or 0,
        "due": row["due"] or 0,
        "sent_today": sent_today,
    }


def _as_ist_datetime(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed.astimezone(IST)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=200, help=f"profiles to add (max {MAX_COUNT})")
    parser.add_argument("--db", help="database path (defaults to config.DATABASE_PATH)")
    parser.add_argument("--seed", type=int, help="random seed for reproducible profiles")
    parser.add_argument(
        "--profile",
        choices=("random", "exact186"),
        default="random",
        help="demo profile shape (exact186 replaces old demo rows and ignores --count)",
    )
    parser.add_argument("--remove", action="store_true", help="remove only marked demo profiles")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.remove and args.profile != "exact186" and (
        args.count < 0 or args.count > MAX_COUNT
    ):
        parser.error(f"--count must be between 0 and {MAX_COUNT}.")

    database_path = Path(args.db).expanduser() if args.db else Path(Config.DATABASE_PATH)
    if not database_path.is_absolute():
        database_path = Path.cwd() / database_path
    real_database = Path(Config.DATABASE_PATH).expanduser().resolve()
    if not args.remove and database_path.resolve() == real_database:
        print(
            "WARNING: Demo data will appear in your tracker, exports and daily counts. "
            "Use --db demo.sqlite3 for a separate file, or run --remove afterwards."
        )
    init_db(database_path)

    if args.remove:
        removed = remove_demo(database_path)
        print(f"Removed {removed} demo people.")
        return 0

    if args.profile == "exact186":
        removed = remove_demo(database_path)
        if removed:
            print(f"Replaced {removed} existing demo people.")
    inserted, skipped = seed_demo(
        database_path,
        args.count,
        args.seed,
        profile=args.profile,
    )
    print(f"Added {inserted} demo people; skipped {skipped} existing URLs.")
    totals = demo_tracker_totals(database_path)
    print(
        f"Total {totals['total']} · Sent {totals['sent']} · "
        f"Connected {totals['connected']} · Replied {totals['replied']} · "
        f"Follow-ups due {totals['due']} · Sent today {totals['sent_today']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
