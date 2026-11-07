import csv
import hashlib
import io
import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo

from flask import (
    Flask,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from affiliation import analyze_affiliation
from config import Config
from database import connect, init_db
from export_excel import build_workbook
from messaging import clean_text, render_message
from profile_urls import normalize_linkedin_url, parse_person_input
from platform_search import (
    build_platform_queries,
    classify_link,
    extract_instagram_handle,
    instagram_handle_from_url,
    match_confidence,
    normalize_instagram_handle,
)
from search_provider import (
    SearchAPIError,
    SearchConnectionError,
    SearchResponseError,
    SearchTimeoutError,
    search_videos,
    search_web,
)

STATUSES = ("new", "shortlisted", "sent", "connected", "replied", "agreed", "rejected")
ROLES = (
    "video editor",
    "reel editor",
    "content creator",
    "videographer",
)
COLLEGES = (
    "PSG College of Arts and Science",
    "PSG College of Technology",
    "Kumaraguru College of Technology",
    "Sri Krishna Arts and Science College",
    "Hindusthan College of Arts and Science",
    "Coimbatore Institute of Technology",
    "Amrita Vishwa Vidyapeetham",
)
DEFAULT_EXCLUDES = "recruiter, hiring, HR, agency"
ORG_TYPES = ("college", "school", "company", "other")
FILTER_STATUSES = ("any", "current", "past")
SEARCH_PLATFORMS = ("linkedin", "instagram", "youtube")
VERIFIED_STATUSES = ("unverified", "current", "past", "unsure")
SOURCES = ("LinkedIn", "Instagram", "YouTube", "In person", "Referral", "Other")
COMMON_MESSAGE_DEFAULTS = {
    "connection": "Hi {first_name}, I liked your work on {specific_note}. I'd love to connect.",
    "follow_up": "Hi {first_name}, thanks for connecting. {specific_note} {my_name}",
}
MAX_NOTE_LENGTH = 1000
MAX_DRAFT_LENGTH = 5000
MAX_SEARCH_TEXT_LENGTH = 100
SEARCH_CACHE_SECONDS = 10 * 60
MAX_SERPER_CALLS_PER_SEARCH = 3
MAX_COLLECT_CALLS = 5
MAX_POOL_SHORTLIST = 200
POOL_TARGET_SIZE = 50
IST = ZoneInfo("Asia/Kolkata")
LOCATION_DATA_DIR = Path(__file__).resolve().parent / "static" / "data"
DEFAULT_SEARCH_LOCATION = {"country": "IN", "state": "Tamil Nadu", "city": "Coimbatore"}
COUNTRY_NAME_BY_CODE = {
    item["code"]: item["name"]
    for item in json.loads(
        (LOCATION_DATA_DIR / "countries.json").read_text(encoding="utf-8")
    )
}

ROLE_KEYWORDS = (
    ("Content Creator", ("content creator", "creator", "youtuber", "influencer", "vlogger")),
    ("Video Editor", ("video editor", "video editing", "editor", "premiere", "final cut", "davinci", "after effects")),
    ("Videographer", ("videographer", "cinematographer", "filmmaker", "director of photography", "dop")),
    ("Motion Designer", ("motion graphics", "motion designer", "animator", "vfx")),
    ("Photographer", ("photographer", "photography")),
    ("Social Media", ("social media", "content strategist", "content marketing", "community manager")),
    ("Designer", ("graphic designer", "ui/ux", "designer")),
)
STUDY_TEXT = re.compile(
    r"\b(?:"
    r"B\.?\s*E\.?|B\.?\s*Tech\.?|M\.?\s*Tech\.?|BSc|MSc|MBA|ECE|EEE|"
    r"engineering\s+student|CSE\s+student|student(?:\s+at\b|\b)|"
    r"pursuing\b|bachelor\s+of\b|master\s+of\b"
    r")",
    re.IGNORECASE,
)


def detect_role(headline, snippet, org_name=""):
    """Guess a creative role and clean study-related noise from a headline."""
    headline = " ".join(str(headline or "").split())
    snippet = " ".join(str(snippet or "").split())
    role_label = "Other"
    for source in (headline, snippet):
        source_folded = source.casefold()
        for label, keywords in ROLE_KEYWORDS:
            if any(
                re.search(r"(?<!\w)" + re.escape(keyword) + r"(?!\w)", source_folded)
                for keyword in keywords
            ):
                role_label = label
                break
        if role_label != "Other":
            break

    org_folded = " ".join(str(org_name or "").split()).casefold()
    segments = re.split(r"\s*(?:\||·|•|-)\s*", headline)
    meaningful = []
    for segment in segments:
        segment = " ".join(segment.split()).strip(" ,;:")
        if not segment or STUDY_TEXT.search(segment):
            continue
        if org_folded and segment.casefold() == org_folded:
            continue
        meaningful.append(segment)
        if len(meaningful) == 2:
            break
    cleaned_headline = " · ".join(meaningful) or role_label
    return role_label, cleaned_headline


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    init_db(app.config["DATABASE_PATH"])

    def db():
        return connect(current_app.config["DATABASE_PATH"])

    app.extensions["editorscout_db"] = db

    @app.context_processor
    def navigation_counts():
        today = datetime.now(IST).date().isoformat()
        with db() as connection:
            shortlisted_today = connection.execute(
                """SELECT COUNT(*) FROM people
                   WHERE removed_from_shortlist = 0
                     AND substr(COALESCE(NULLIF(shortlisted_at, ''), created_at), 1, 10) = ?""",
                (today,),
            ).fetchone()[0]
            follow_ups_due = connection.execute(
                """SELECT COUNT(*) FROM people
                   WHERE follow_up_date != '' AND follow_up_date <= ?""",
                (today,),
            ).fetchone()[0]
        return {
            "nav_shortlist_count": shortlisted_today,
            "nav_follow_up_count": follow_ups_due,
            "nav_today": today,
        }

    @app.get("/")
    def search_page():
        with db() as connection:
            settings = read_message_settings(connection)
        location_defaults = (
            DEFAULT_SEARCH_LOCATION
            if "search_country" not in settings
            else {
                "country": settings.get("search_country", ""),
                "state": settings.get("search_state", ""),
                "city": settings.get("search_city", ""),
            }
        )
        filters = {
            "mode": "browse",
            "role": ROLES[0],
            "country": location_defaults["country"],
            "state": location_defaults["state"],
            "city": location_defaults["city"],
            "city_custom": "",
            "state_custom": "",
            "disambiguate": False,
            "org_type": "",
            "org_name": "",
            "filter_status": "any",
            "exclude_words": DEFAULT_EXCLUDES,
            "per_page": 10,
        }
        filters.update(request.args.to_dict())
        filters["country"] = filters.get("country", location_defaults["country"])
        filters["state"] = filters.get("state", location_defaults["state"])
        filters["city"] = filters.get("city", location_defaults["city"])
        return render_template(
            "search.html",
            roles=ROLES,
            college_options=COLLEGES,
            org_types=ORG_TYPES,
            statuses=FILTER_STATUSES,
            platforms=SEARCH_PLATFORMS,
            default_excludes=DEFAULT_EXCLUDES,
            countries=load_countries(),
            filters=filters,
            results=None,
            page=1,
            per_page=10,
        )

    @app.post("/search")
    def search():
        try:
            filters = normalize_filters(request.form)
            if filters["mode"] == "browse":
                if filters["role"] and filters["role"] not in ROLES:
                    raise ValueError("Choose a valid role to browse.")
            elif not filters["person_input"]:
                raise ValueError("Enter a name, LinkedIn profile URL, or LinkedIn ID.")
            elif not filters["platforms"]:
                raise ValueError("Select at least one platform to search.")
        except ValueError as exc:
            flash(str(exc), "error")
            error_filters = request.form.to_dict(flat=True)
            error_filters["platforms"] = request.form.getlist("platforms")
            return render_template(
                "search.html",
                roles=ROLES,
                college_options=COLLEGES,
                org_types=ORG_TYPES,
                statuses=FILTER_STATUSES,
                platforms=SEARCH_PLATFORMS,
                countries=load_countries(),
                default_excludes=DEFAULT_EXCLUDES,
                filters=error_filters,
                results=None,
                page=1,
                per_page=normalize_page_size(request.form.get("per_page")),
            ), 400

        with db() as connection:
            save_message_settings(
                connection,
                {
                    "search_country": filters["country"],
                    "search_state": filters["state"],
                    "search_city": filters["city"],
                },
            )

        page = parse_page(request.form.get("page"))
        if filters["mode"] == "person":
            platform_groups, queries, errors, cache_status, next_page = (
                run_person_platform_search(
                    filters,
                    page,
                    request.form.get("refresh") == "1",
                    db,
                )
            )
            display_query = "\n".join(
                f"{platform.title()}: {query}"
                for platform, query in queries.items()
            )
            with db() as connection:
                connection.execute(
                    "INSERT INTO searches (query, filters_json, result_count) VALUES (?, ?, ?)",
                    (
                        display_query,
                        json.dumps(filters, ensure_ascii=False),
                        sum(len(group["results"]) for group in platform_groups),
                    ),
                )
            return render_template(
                "search.html",
                roles=ROLES,
                college_options=COLLEGES,
                org_types=ORG_TYPES,
                statuses=FILTER_STATUSES,
                platforms=SEARCH_PLATFORMS,
                countries=load_countries(),
                default_excludes=DEFAULT_EXCLUDES,
                filters=filters,
                results=[],
                platform_groups=platform_groups,
                page=page,
                per_page=filters["per_page"],
                next_page=next_page,
                search_query=display_query,
                cached=bool(cache_status) and all(cache_status.values()),
                cached_platforms=cache_status,
                already_saved_count=0,
                show_saved=False,
                selected_platform_count=len(filters["platforms"]),
                refresh=request.form.get("refresh") == "1",
                api_error=None,
                location_label=format_location(filters),
            )
        query = build_query(filters)
        location_label = format_location(filters)
        country_cache_key = location_cache_key(filters)
        search_key = build_search_key(filters)
        raw_cursor = parse_raw_cursor(
            request.form.get("raw_page_cursor"),
            fallback=page,
        )
        previous_cursors = parse_cursor_history(request.form.get("prev_cursors", ""))
        show_saved = request.form.get("show_saved") == "1"
        refresh = request.form.get("refresh") == "1"
        collection_message = ""
        collection_error = None
        collection_credits = 0
        pool_before = get_search_pool_summary(db, search_key)
        collect_cursor = get_next_collect_page(
            db, country_cache_key, filters["per_page"], pool_before["urls"]
        )
        if request.form.get("collect") == "1":
            remaining = max(0, POOL_TARGET_SIZE - pool_before["unsaved"])
            if remaining:
                collection = run_browse_search(
                    query=query,
                    cache_query=country_cache_key,
                    filters=filters,
                    raw_cursor=collect_cursor,
                    previous_cursors=[],
                    show_saved=False,
                    refresh=False,
                    gl=filters["country"].lower() if filters["country"] else None,
                    db_factory=db,
                    max_calls=MAX_COLLECT_CALLS,
                    max_pages=10,
                    target_count=remaining,
                    exclude_urls=pool_before["urls"],
                )
                save_search_pool(db, search_key, collection["pool_profiles"])
                pool_after = get_search_pool_summary(db, search_key)
                collected_count = max(0, pool_after["unsaved"] - pool_before["unsaved"])
                collection_credits = collection["used_credits"]
                collection_error = collection["api_error"]
                collection_cursor_after = get_next_collect_page(
                    db,
                    country_cache_key,
                    filters["per_page"],
                    pool_after["urls"],
                )
                collection_message = f"Collected {collected_count} profiles. Used {collection_credits} credits."
                if (
                    pool_after["unsaved"] < POOL_TARGET_SIZE
                    and collection_cursor_after > 10
                    and not collection_error
                ):
                    collection_message += (
                        f" Google returned only {pool_after['unsaved']} usable profiles for this search."
                    )
                collect_cursor = collection_cursor_after
            else:
                collection_message = (
                    f"Collected 0 profiles. Used 0 credits. "
                    f"Google returned only {pool_before['unsaved']} usable profiles for this search."
                )

        browse_results = run_browse_search(
            query=query,
            cache_query=country_cache_key,
            filters=filters,
            raw_cursor=raw_cursor,
            previous_cursors=previous_cursors,
            show_saved=show_saved,
            refresh=refresh,
            gl=filters["country"].lower() if filters["country"] else None,
            db_factory=db,
        )
        if request.form.get("skip_pool_upsert") != "1":
            save_search_pool(db, search_key, browse_results["pool_profiles"])
        pool_summary = get_search_pool_summary(db, search_key)
        collect_cursor = get_next_collect_page(
            db,
            country_cache_key,
            filters["per_page"],
            pool_summary["urls"],
        )
        results = browse_results["results"]
        page = browse_results["page"]
        next_page = browse_results["next_page"]
        cached = browse_results["cached"]
        api_error = browse_results["api_error"]
        already_saved = browse_results["already_saved_count"]

        with db() as connection:
            connection.execute(
                "INSERT INTO searches (query, filters_json, result_count) VALUES (?, ?, ?)",
                (query, json.dumps(filters, ensure_ascii=False), len(results)),
            )

        if api_error:
            flash(api_error, "error")
        if collection_error:
            flash(collection_error, "error")

        return render_template(
            "search.html",
            roles=ROLES,
            college_options=COLLEGES,
            org_types=ORG_TYPES,
            statuses=FILTER_STATUSES,
            platforms=SEARCH_PLATFORMS,
            countries=load_countries(),
            default_excludes=DEFAULT_EXCLUDES,
            filters=filters,
            results=results,
            page=page,
            per_page=filters["per_page"],
            next_page=next_page,
            search_query=query,
            cached=cached,
            refresh=refresh,
            already_saved_count=already_saved,
            show_saved=show_saved,
            raw_page_cursor=browse_results["raw_page_cursor"],
            next_raw_page_cursor=browse_results["next_raw_page_cursor"],
            previous_cursors=browse_results["previous_cursors"],
            next_previous_cursors=browse_results["next_previous_cursors"],
            raw_result_count=browse_results["raw_result_count"],
            non_profile_count=browse_results["non_profile_count"],
            used_credits=browse_results["used_credits"],
            underfilled=browse_results["underfilled"],
            widen_search=filters["widen_search"],
            role_helper=detect_role,
            location_label=location_label,
            search_key=search_key,
            pool_count=pool_summary["unsaved"],
            pool_total=pool_summary["total"],
            pool_saved_count=pool_summary["saved"],
            pool_page_count=get_cached_page_count(
                db, country_cache_key, filters["per_page"]
            ),
            pool_collect_cursor=collect_cursor,
            collection_message=collection_message,
            collection_credits=collection_credits,
            pool_has_more=collect_cursor <= 10,
        )

    @app.post("/shortlist")
    def shortlist():
        profiles = request.form.getlist("profile")
        if not profiles:
            flash("Select at least one profile to shortlist.", "error")
            return redirect(url_for("search_page"))
        try:
            selected = [json.loads(value) for value in profiles]
        except (json.JSONDecodeError, TypeError):
            abort(400)
        with db() as connection:
            batch_id = ensure_current_batch(connection)
            saved, _skipped = insert_selected_profiles(
                connection, selected, batch_id, ist_now()
            )
        flash(f"Shortlisted {saved} profile(s).", "success")
        return redirect(shortlist_url(date=datetime.now(IST).date().isoformat()))

    @app.post("/shortlist/pool")
    def shortlist_pool():
        search_key = clean_pool_search_key(request.form.get("search_key", ""))
        if request.form.get("confirm_all") != "1":
            flash("Confirm before shortlisting all discovered profiles.", "error")
            return redirect(url_for("search_page"))
        with db() as connection:
            pool_rows = connection.execute(
                """SELECT url, profile_json FROM search_pool
                   WHERE search_key = ? ORDER BY id""",
                (search_key,),
            ).fetchall()
            saved_urls = {
                normalize_linkedin_url(row["url"])
                for row in connection.execute("SELECT url FROM people").fetchall()
            }
            unsaved_rows = [
                row for row in pool_rows
                if normalize_linkedin_url(row["url"]) not in saved_urls
            ]
            selected_rows = unsaved_rows[:MAX_POOL_SHORTLIST]
            selected = []
            for row in selected_rows:
                try:
                    selected.append(json.loads(row["profile_json"]))
                except (json.JSONDecodeError, TypeError):
                    abort(400)
            skipped = len(pool_rows) - len(unsaved_rows)
            batch_id = ensure_current_batch(connection) if selected else None
            saved = 0
            if selected:
                saved, duplicate_skips = insert_selected_profiles(
                    connection, selected, batch_id, ist_now()
                )
                skipped += duplicate_skips
            remaining = max(0, len(unsaved_rows) - saved)
        message = f"Shortlisted {saved} profiles ({skipped} already saved, skipped)"
        if remaining:
            message += f". {remaining} profiles left; the per-click limit is {MAX_POOL_SHORTLIST}."
        flash(message + ".", "success")
        return redirect(shortlist_url(date=datetime.now(IST).date().isoformat()))

    @app.post("/search/pool/clear")
    def clear_search_pool():
        search_key = clean_pool_search_key(request.form.get("search_key", ""))
        with db() as connection:
            connection.execute(
                "DELETE FROM search_pool WHERE search_key = ?", (search_key,)
            )
        flash("Cleared discovered profiles for this search.", "success")
        return search()

    @app.get("/shortlist")
    def shortlist_page():
        selected_date = parse_shortlist_date(request.args.get("date"))
        with db() as connection:
            batch = connection.execute(
                "SELECT * FROM batches WHERE is_current = 1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
            all_people = connection.execute(
                """SELECT * FROM people WHERE removed_from_shortlist = 0
                   ORDER BY shortlisted_at DESC, id DESC"""
            ).fetchall()
            drafts = connection.execute(
                """SELECT m.* FROM messages m
                   JOIN (
                       SELECT person_id, channel, MAX(id) AS latest_id
                       FROM messages GROUP BY person_id, channel
                   ) latest ON latest.latest_id = m.id"""
            ).fetchall()
            sent_messages = connection.execute(
                """SELECT person_id, kind, text, sent_at
                   FROM sent_messages ORDER BY sent_at DESC, id DESC"""
            ).fetchall()
            settings = read_message_settings(connection)
            sent_timestamps = connection.execute(
                """SELECT sent_at FROM sent_messages
                   UNION ALL
                   SELECT p.sent_at FROM people p
                   WHERE p.sent_at IS NOT NULL AND NOT EXISTS (
                       SELECT 1 FROM sent_messages sm
                       WHERE sm.person_id = p.id AND sm.sent_at = p.sent_at
                   )"""
            ).fetchall()
            person_links_by_person = {}
            for link in connection.execute(
                """SELECT * FROM person_links WHERE dismissed = 0
                   ORDER BY confirmed DESC, found_at DESC, id DESC"""
            ).fetchall():
                person_links_by_person.setdefault(link["person_id"], []).append(link)
        date_log = build_date_log(all_people)
        total_shortlisted = sum(group["count"] for group in date_log)
        people = [
            dict(person)
            for person in all_people
            if selected_date and shortlist_date_of(person) == selected_date
        ]
        drafts_by_person = {}
        for draft in drafts:
            drafts_by_person.setdefault(draft["person_id"], {})[draft["channel"]] = dict(draft)
        sent_by_person = {}
        for sent in sent_messages:
            sent_by_person.setdefault(sent["person_id"], {}).setdefault(sent["kind"], sent)
        for person in people:
            person["drafts"] = drafts_by_person.get(person["id"], {})
            person["sent_kinds"] = sent_by_person.get(person["id"], {})
        sent_today = count_sent_today(sent_timestamps)
        common_messages = {
            "connection": settings.get(
                "common_connection", COMMON_MESSAGE_DEFAULTS["connection"]
            ),
            "follow_up": settings.get(
                "common_follow_up", COMMON_MESSAGE_DEFAULTS["follow_up"]
            ),
        }
        return render_template(
            "shortlist.html",
            people=people,
            batch=batch,
            person_count=len(people),
            total_shortlisted=total_shortlisted,
            date_log=date_log,
            selected_date=selected_date,
            selected_date_label=format_shortlist_date(selected_date) if selected_date else None,
            person_links_by_person=person_links_by_person,
            common_messages=common_messages,
            my_name=settings.get("my_name", ""),
            message_link=settings.get("link", ""),
            sent_today=sent_today,
            warning_threshold=10,
            sources=SOURCES,
            default_follow_up_date=(datetime.now(IST).date() + timedelta(days=4)).isoformat(),
            detect_role=detect_role,
            normalize_manual_instagram_handle=normalize_manual_instagram_handle,
        )

    @app.post("/shortlist/messages")
    def shortlist_messages():
        kind = request.form.get("kind", "connection")
        if kind not in ("connection", "follow_up"):
            abort(400)
        template = clean_text(request.form.get("common_message", ""), 5000)
        my_name = clean_text(request.form.get("my_name", ""), 200)
        message_link = clean_text(request.form.get("message_link", ""), 500)
        with db() as connection:
            save_message_settings(
                connection,
                {
                    f"common_{kind}": template,
                    "my_name": my_name,
                    "link": message_link,
                },
            )
            people = connection.execute(
                """SELECT * FROM people WHERE removed_from_shortlist = 0
                   ORDER BY shortlisted_at DESC, id DESC"""
            ).fetchall()
            selected_date = parse_shortlist_date(request.form.get("date"))
            if selected_date:
                people = [
                    person for person in people
                    if shortlist_date_of(person) == selected_date
                ]
            if request.form.get("action") == "save":
                flash("Common message and details saved.", "success")
                return redirect(shortlist_url())

            if request.form.get("apply_to") == "all":
                targets = people
            else:
                selected_ids = {
                    int(value)
                    for value in request.form.getlist("person_ids")
                    if value.isdigit()
                }
                targets = [person for person in people if person["id"] in selected_ids]
            overwrite_edited = request.form.get("overwrite_edited") == "1"
            include_contacted = request.form.get("include_contacted") == "1"
            generated = skipped_sent = skipped_edited = skipped_ineligible = 0
            unknown_placeholders = set()
            for person in targets:
                if kind == "follow_up" and person["status"] not in (
                    "connected", "replied", "agreed"
                ):
                    skipped_ineligible += 1
                    continue
                if (
                    kind == "connection"
                    and person["status"] in ("sent", "connected", "replied", "agreed")
                    and not include_contacted
                ):
                    skipped_sent += 1
                    continue
                existing = connection.execute(
                    """SELECT is_edited FROM messages
                       WHERE person_id = ? AND channel = ?
                       ORDER BY id DESC LIMIT 1""",
                    (person["id"], kind),
                ).fetchone()
                if existing and existing["is_edited"] and not overwrite_edited:
                    skipped_edited += 1
                    continue
                rendered, unknown, _empty = render_message(
                    template, person, {"my_name": my_name, "link": message_link}
                )
                unknown_placeholders.update(unknown)
                connection.execute(
                    """INSERT INTO messages
                       (person_id, channel, draft_text, is_edited)
                       VALUES (?, ?, ?, 0)""",
                    (person["id"], kind, rendered),
                )
                generated += 1
        if unknown_placeholders:
            flash(
                "Unknown placeholders were removed: "
                + ", ".join(f"{{{name}}}" for name in sorted(unknown_placeholders)),
                "warning",
            )
        flash(
            f"Generated {generated}, skipped {skipped_sent + skipped_edited + skipped_ineligible} "
            f"(already sent {skipped_sent}, edited {skipped_edited}, "
            f"not eligible {skipped_ineligible}).",
            "success",
        )
        return redirect(shortlist_url())

    @app.post("/people/<int:person_id>/find-links")
    def find_more_links(person_id):
        with db() as connection:
            person = connection.execute(
                "SELECT id, name, org_name FROM people WHERE id = ?", (person_id,)
            ).fetchone()
            if person is None:
                abort(404)
        try:
            queries = build_platform_queries(
                person["name"], person["org_name"], "", "", ("instagram", "youtube")
            )
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(shortlist_url())
        found_counts = {}
        errors = {}
        refresh = request.form.get("refresh") == "1"
        for platform, query in queries.items():
            items, error, _cached, _used_query, _next_page = get_platform_results(
                platform,
                query,
                1,
                10,
                db,
                refresh=refresh,
            )
            if error:
                errors[platform] = error
                continue
            candidates = shape_platform_results(
                items, platform, person["name"], person["org_name"]
            )
            with db() as connection:
                for candidate in candidates:
                    connection.execute(
                        """INSERT INTO person_links
                           (person_id, platform, kind, url, title, snippet, handle,
                            confidence, confirmed, dismissed)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
                           ON CONFLICT(person_id, url) DO UPDATE SET
                             title = excluded.title,
                             snippet = excluded.snippet,
                             handle = excluded.handle,
                             confidence = excluded.confidence,
                             found_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')""",
                        (
                            person_id,
                            candidate["platform"],
                            candidate["kind"],
                            candidate["url"],
                            candidate["title"],
                            candidate["snippet"],
                            candidate["handle"] or "",
                            candidate["confidence"],
                        ),
                    )
            found_counts[platform] = len(candidates)
        for platform, error in errors.items():
            flash(f"{platform.title()} search failed: {error}", "error")
        if found_counts:
            flash(
                "Search suggestions saved: "
                + ", ".join(f"{key.title()} {value}" for key, value in found_counts.items()),
                "success",
            )
        return redirect(shortlist_url(links_for=person_id))

    @app.post("/people/<int:person_id>/links/<int:link_id>/confirm")
    def confirm_person_link(person_id, link_id):
        with db() as connection:
            link = connection.execute(
                """SELECT * FROM person_links WHERE id = ? AND person_id = ?
                   AND dismissed = 0""",
                (link_id, person_id),
            ).fetchone()
            if link is None:
                abort(404)
            handle = link["handle"] or instagram_handle_from_url(link["url"])
            if link["platform"] == "instagram" and link["kind"] == "profile" and not handle:
                flash("This profile suggestion has no valid Instagram handle.", "error")
                return redirect(shortlist_url(links_for=person_id))
            if link["platform"] == "instagram" and link["kind"] == "profile":
                connection.execute(
                    """UPDATE person_links SET confirmed = 0
                       WHERE person_id = ? AND platform = 'instagram'
                       AND kind = 'profile'""",
                    (person_id,),
                )
            connection.execute(
                "UPDATE person_links SET confirmed = 1 WHERE id = ?",
                (link_id,),
            )
            if link["platform"] == "instagram" and link["kind"] == "profile":
                connection.execute(
                    "UPDATE people SET instagram_handle = ?, updated_at = ? WHERE id = ?",
                    (handle, ist_now(), person_id),
                )
        flash("Link confirmed after your manual review.", "success")
        return redirect(shortlist_url(links_for=person_id))

    @app.post("/people/<int:person_id>/links/<int:link_id>/dismiss")
    def dismiss_person_link(person_id, link_id):
        with db() as connection:
            cursor = connection.execute(
                """UPDATE person_links SET dismissed = 1, confirmed = 0
                   WHERE id = ? AND person_id = ? AND confirmed = 0""",
                (link_id, person_id),
            )
        if not cursor.rowcount:
            abort(404)
        return redirect(shortlist_url(links_for=person_id))

    @app.post("/batches/new")
    def start_new_batch():
        with db() as connection:
            connection.execute("UPDATE batches SET is_current = 0 WHERE is_current = 1")
            batch_id = create_batch(connection)
        flash("Started a new shortlist batch.", "success")
        return redirect(shortlist_url(batch=batch_id))

    @app.post("/people/<int:person_id>/remove")
    def remove_from_shortlist(person_id):
        timestamp = ist_now()
        with db() as connection:
            current = connection.execute(
                "SELECT id FROM batches WHERE is_current = 1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if current is None:
                abort(404)
            cursor = connection.execute(
                """UPDATE people SET removed_from_shortlist = 1, updated_at = ?
                   WHERE id = ? AND batch_id = ? AND removed_from_shortlist = 0""",
                (timestamp, person_id, current["id"]),
            )
        if not cursor.rowcount:
            abort(404)
        flash("Removed from the current shortlist; the tracker history is kept.", "success")
        return redirect(shortlist_url())

    @app.post("/people/<int:person_id>/save")
    def save_person(person_id):
        raw_note = request.form.get("personal_note", "")
        raw_verified_note = request.form.get("verified_note", "")
        raw_instagram_handle = request.form.get("instagram_handle")
        if len(raw_note) > MAX_NOTE_LENGTH or len(raw_verified_note) > MAX_NOTE_LENGTH:
            flash(f"Notes must be {MAX_NOTE_LENGTH} characters or fewer.", "error")
            return redirect(shortlist_url())
        note = clean_text(raw_note, MAX_NOTE_LENGTH).strip()
        verified_note = clean_text(raw_verified_note, MAX_NOTE_LENGTH).strip()
        with db() as connection:
            person = connection.execute(
                "SELECT verified_status, verified_note, instagram_handle FROM people WHERE id = ?",
                (person_id,),
            ).fetchone()
            if person is None:
                abort(404)
            verified_status = request.form.get(
                "verified_status", person["verified_status"]
            )
            if verified_status not in VERIFIED_STATUSES:
                abort(400)
            if "verified_note" not in request.form:
                verified_note = person["verified_note"]
            if raw_instagram_handle is None:
                instagram_handle = person["instagram_handle"]
            else:
                instagram_handle = normalize_manual_instagram_handle(raw_instagram_handle)
                if raw_instagram_handle.strip() and not instagram_handle:
                    flash("Enter a valid Instagram @handle or profile URL.", "error")
                    return redirect(shortlist_url())
            cursor = connection.execute(
                """UPDATE people SET personal_note = ?, verified_status = ?,
                    verified_note = ?, instagram_handle = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE id = ?""",
                (note, verified_status, verified_note, instagram_handle, person_id),
            )
            if instagram_handle:
                instagram_url = f"https://www.instagram.com/{instagram_handle}/"
                connection.execute(
                    """UPDATE person_links SET confirmed = 0
                       WHERE person_id = ? AND platform = 'instagram'
                       AND kind = 'profile'""",
                    (person_id,),
                )
                connection.execute(
                    """INSERT INTO person_links
                       (person_id, platform, kind, url, title, snippet, handle,
                        confidence, confirmed, dismissed)
                       VALUES (?, 'instagram', 'profile', ?, '', '', ?, 'likely', 1, 0)
                       ON CONFLICT(person_id, url) DO UPDATE SET
                         handle = excluded.handle, confirmed = 1, dismissed = 0""",
                    (person_id, instagram_url, instagram_handle),
                )
        if not cursor.rowcount:
            abort(404)
        flash("Notes and verification saved.", "success")
        return redirect(shortlist_url())

    @app.post("/people/<int:person_id>/draft")
    def generate_draft(person_id):
        channel = request.form.get("channel", "connection")
        if channel not in ("connection", "follow_up"):
            abort(400)
        with db() as connection:
            person = connection.execute(
                "SELECT * FROM people WHERE id = ?", (person_id,)
            ).fetchone()
            if person is None:
                abort(404)
            note = person["personal_note"].strip()
            if channel == "follow_up" and person["status"] not in (
                "connected", "replied", "agreed"
            ):
                flash(
                    "Follow-up drafts are available after the status is connected, replied, or agreed.",
                    "warning",
                )
                return redirect(shortlist_url())
            repeat_warning = person["status"] in ("sent", "connected", "replied", "agreed")
            if (
                channel == "connection"
                and repeat_warning
                and request.form.get("confirm_repeat") != "1"
            ):
                flash(
                    "This person is already marked as contacted. Confirm before preparing another connection note.",
                    "warning",
                )
                return redirect(shortlist_url())
            if channel == "connection" and person["verified_status"] == "unverified":
                flash(
                    "This profile is not verified yet. Please review it before sending any message.",
                    "warning",
                )
            draft = make_draft(
                channel,
                person["name"],
                note,
                current_app.config["INCUBRIX_ONE_LINER"],
                current_app.config["INCUBRIX_OFFER"],
            )
            connection.execute(
                "INSERT INTO messages (person_id, channel, draft_text) VALUES (?, ?, ?)",
                (person_id, channel, draft),
            )
        return render_template(
            "draft.html",
            person=person,
            channel=channel,
            draft=draft,
            repeat_warning=repeat_warning,
            max_connection_chars=300,
        )

    @app.post("/people/<int:person_id>/draft/save")
    def save_draft(person_id):
        channel = request.form.get("channel", "")
        raw_draft = request.form.get("draft_text", "").strip()
        if len(raw_draft) > MAX_DRAFT_LENGTH:
            flash(f"Draft must be between 1 and {MAX_DRAFT_LENGTH} characters.", "error")
            return redirect(shortlist_url())
        draft = clean_text(raw_draft, MAX_DRAFT_LENGTH)
        if channel not in ("connection", "follow_up"):
            abort(400)
        if not draft or len(draft) > MAX_DRAFT_LENGTH:
            flash(f"Draft must be between 1 and {MAX_DRAFT_LENGTH} characters.", "error")
            return redirect(shortlist_url())
        if channel == "connection" and len(draft) >= 300:
            flash("Connection notes must be under 300 characters.", "error")
            return redirect(shortlist_url())
        with db() as connection:
            exists = connection.execute(
                "SELECT 1 FROM people WHERE id = ?", (person_id,)
            ).fetchone()
            if exists is None:
                abort(404)
            connection.execute(
                """INSERT INTO messages
                   (person_id, channel, draft_text, is_edited) VALUES (?, ?, ?, 1)""",
                (person_id, channel, draft),
            )
        flash("Edited draft saved.", "success")
        return redirect(shortlist_url())

    @app.post("/people/<int:person_id>/draft/reset")
    def reset_shortlist_draft(person_id):
        kind = request.form.get("kind", "connection")
        if kind not in ("connection", "follow_up"):
            abort(400)
        with db() as connection:
            person = connection.execute(
                "SELECT * FROM people WHERE id = ?", (person_id,)
            ).fetchone()
            if person is None:
                abort(404)
            if kind == "follow_up" and person["status"] not in (
                "connected", "replied", "agreed"
            ):
                flash(
                    "Follow-up drafts are available after the status is connected, replied, or agreed.",
                    "warning",
                )
                return redirect(shortlist_url())
            settings = read_message_settings(connection)
            template = settings.get(f"common_{kind}", COMMON_MESSAGE_DEFAULTS[kind])
            rendered, unknown, _empty = render_message(template, person, settings)
            connection.execute(
                """INSERT INTO messages
                   (person_id, channel, draft_text, is_edited) VALUES (?, ?, ?, 0)""",
                (person_id, kind, rendered),
            )
        if unknown:
            flash(
                "Unknown placeholders were removed: "
                + ", ".join(f"{{{name}}}" for name in unknown),
                "warning",
            )
        return redirect(shortlist_url())

    @app.post("/people/<int:person_id>/sent")
    def mark_message_sent(person_id):
        kind = request.form.get("kind", "")
        raw_sent_text = request.form.get("sent_text", "").strip()
        sent_text = clean_text(raw_sent_text, MAX_DRAFT_LENGTH)
        follow_up_date = request.form.get("follow_up_date", "").strip()
        if (
            kind not in ("connection", "follow_up")
            or not sent_text
            or len(raw_sent_text) > MAX_DRAFT_LENGTH
        ):
            abort(400)
        if follow_up_date:
            try:
                date.fromisoformat(follow_up_date)
            except ValueError:
                abort(400)
        else:
            follow_up_date = (
                datetime.now(IST).date() + timedelta(days=4)
            ).isoformat()

        timestamp = ist_now()
        with db() as connection:
            person = connection.execute(
                "SELECT status FROM people WHERE id = ?", (person_id,)
            ).fetchone()
            if person is None:
                abort(404)
            already_sent = connection.execute(
                "SELECT 1 FROM sent_messages WHERE person_id = ? AND kind = ? LIMIT 1",
                (person_id, kind),
            ).fetchone()
            if already_sent and request.form.get("confirm_duplicate") != "1":
                flash(
                    "This message type was already marked sent. Confirm before recording it again.",
                    "warning",
                )
                return redirect(shortlist_url())
            connection.execute(
                """UPDATE people SET status = 'sent', sent_at = ?,
                   status_changed_at = ?, updated_at = ?, touch_count = touch_count + 1,
                   follow_up_date = ? WHERE id = ?""",
                (timestamp, timestamp, timestamp, follow_up_date, person_id),
            )
            connection.execute(
                """INSERT INTO status_history
                   (person_id, old_status, new_status, changed_at)
                   VALUES (?, ?, 'sent', ?)""",
                (person_id, person["status"], timestamp),
            )
            connection.execute(
                """INSERT INTO sent_messages (person_id, kind, text, sent_at)
                   VALUES (?, ?, ?, ?)""",
                (person_id, kind, sent_text, timestamp),
            )
        flash("Marked as sent. The message was recorded; nothing was sent by the app.", "success")
        return redirect(shortlist_url())

    @app.get("/tracker")
    def tracker():
        status_filter, verified_current, batch_filter, due_filter = parse_tracker_filters(request.args)
        people = get_tracker_people(
            db, status_filter, verified_current, batch_filter, due_filter,
            order_by="updated_at",
        )
        with db() as connection:
            batches = connection.execute(
                "SELECT id, name, is_current FROM batches ORDER BY created_at DESC, id DESC"
            ).fetchall()
            tracker_sent_count = connection.execute(
                """SELECT COUNT(*) FROM people
                   WHERE status IN ('sent', 'connected', 'replied', 'agreed')
                      OR touch_count >= 1"""
            ).fetchone()[0]
            sent_timestamps = connection.execute(
                """SELECT sent_at FROM sent_messages
                   UNION ALL
                   SELECT p.sent_at FROM people p
                   WHERE p.sent_at IS NOT NULL AND NOT EXISTS (
                       SELECT 1 FROM sent_messages sm
                       WHERE sm.person_id = p.id AND sm.sent_at = p.sent_at
                   )"""
            ).fetchall()
        sent_today = count_sent_today(sent_timestamps)
        return render_template(
            "tracker.html",
            people=people,
            statuses=STATUSES,
            status_filter=status_filter,
            verified_current=verified_current,
            batch_filter=batch_filter,
            due_filter=due_filter,
            batches=batches,
            tracker_sent_count=tracker_sent_count,
            sent_today=sent_today,
            warning_threshold=10,
            sources=SOURCES,
        )

    @app.post("/people/<int:person_id>/update")
    def update_person(person_id):
        status = request.form.get("status", "")
        raw_note = request.form.get("personal_note", "")
        note = clean_text(raw_note, MAX_NOTE_LENGTH).strip()
        if status not in STATUSES:
            abort(400)
        if len(raw_note) > MAX_NOTE_LENGTH:
            flash(f"Notes must be {MAX_NOTE_LENGTH} characters or fewer.", "error")
            return redirect(url_for("tracker"))
        with db() as connection:
            person = connection.execute(
                "SELECT status, source, referred_by, follow_up_date FROM people WHERE id = ?",
                (person_id,),
            ).fetchone()
            if person is None:
                abort(404)
            source = clean_text(request.form.get("source", person["source"]), 50).strip()
            referred_by = clean_text(
                request.form.get("referred_by", person["referred_by"]), 200
            ).strip()
            follow_up_date = clean_text(
                request.form.get("follow_up_date", person["follow_up_date"]), 10
            ).strip()
            if source not in SOURCES and source != person["source"]:
                abort(400)
            if follow_up_date:
                try:
                    date.fromisoformat(follow_up_date)
                except ValueError:
                    abort(400)
            timestamp = ist_now()
            if person["status"] != status:
                connection.execute(
                    """UPDATE people SET status = ?, personal_note = ?,
                       updated_at = ?, status_changed_at = ?,
                       sent_at = CASE WHEN ? = 'sent' THEN ? ELSE sent_at END,
                       source = ?, referred_by = ?, follow_up_date = ?
                       WHERE id = ?""",
                    (
                        status, note, timestamp, timestamp, status, timestamp,
                        source, referred_by, follow_up_date, person_id,
                    ),
                )
                connection.execute(
                    """INSERT INTO status_history
                       (person_id, old_status, new_status, changed_at)
                       VALUES (?, ?, ?, ?)""",
                    (person_id, person["status"], status, timestamp),
                )
            else:
                connection.execute(
                    """UPDATE people SET personal_note = ?, source = ?, referred_by = ?,
                       follow_up_date = ?, updated_at = ? WHERE id = ?""",
                    (note, source, referred_by, follow_up_date, timestamp, person_id),
                )
        flash("Tracker entry updated.", "success")
        return redirect(
            url_for(
                "tracker",
                status=status_filter_value(request.form),
                verified_current="1"
                if request.form.get("return_verified_current") == "1"
                else "",
                batch=request.form.get("return_batch", ""),
                due="1" if request.form.get("return_due") == "1" else "",
            )
        )

    @app.get("/export.csv")
    def export_csv():
        status_filter, verified_current, batch_filter, due_filter = parse_tracker_filters(request.args)
        people = get_tracker_people(
            db, status_filter, verified_current, batch_filter, due_filter,
            order_by="created_at",
        )
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(
            ["Name", "Headline", "Snippet", "Profile URL", "Organisation type",
             "Organisation name", "Search status hint", "Search years",
             "Verified status", "Verification note", "Source", "Personal note",
             "Status", "Draft", "Created at", "Updated at", "Sent at", "Batch",
             "Last status change", "Removed from shortlist", "Instagram handle",
             "Confirmed Instagram link", "Confirmed YouTube link", "Referred by",
             "Touch count", "Follow-up date", "Last sent message"]
        )
        csv_fields = (
            "name", "headline", "snippet", "url", "org_type", "org_name",
            "status_hint", "years_text", "verified_status", "verified_note",
            "source", "personal_note", "status", "draft", "created_at",
            "updated_at", "sent_at", "batch_name", "status_changed_at",
            "removed_from_shortlist", "instagram_handle", "confirmed_instagram_link",
            "confirmed_youtube_link", "referred_by", "touch_count",
            "follow_up_date", "last_sent_text",
        )
        for person in people:
            writer.writerow([csv_safe(person[key]) for key in csv_fields])
        return Response(
            "\ufeff" + output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=editorscout-tracker.csv"},
        )

    @app.get("/export.xlsx")
    def export_xlsx():
        status_filter, verified_current, batch_filter, due_filter = parse_tracker_filters(request.args)
        people = get_tracker_people(
            db, status_filter, verified_current, batch_filter, due_filter,
            order_by="created_at",
        )
        output = build_workbook(people)
        return Response(
            output.getvalue(),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": (
                    f"attachment; filename=editorscout_{date.today():%Y-%m-%d}.xlsx"
                )
            },
        )

    return app


def parse_shortlist_date(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed.isoformat() if parsed.isoformat() == value else None


def shortlist_url(**extra):
    explicit_date = extra.pop("date", None)
    date_value = explicit_date if explicit_date is not None else (
        request.form.get("date") or request.args.get("date")
    )
    selected_date = parse_shortlist_date(date_value)
    parameters = dict(extra)
    if selected_date:
        parameters["date"] = selected_date
    return url_for("shortlist_page", **parameters)


def shortlist_date_of(person):
    timestamp = person["shortlisted_at"] or person["created_at"]
    parsed = as_ist_datetime(timestamp)
    return parsed.date().isoformat() if parsed else None


def format_shortlist_date(date_text):
    parsed_text = parse_shortlist_date(date_text)
    if parsed_text is None:
        return ""
    parsed = date.fromisoformat(parsed_text)
    formatted = parsed.strftime("%a, %d %b %Y")
    today = datetime.now(IST).date()
    if parsed == today:
        return f"Today · {formatted}"
    if parsed == today - timedelta(days=1):
        return f"Yesterday · {formatted}"
    return formatted


def build_date_log(people):
    groups = {}
    for person in people:
        date_text = shortlist_date_of(person)
        if date_text is None:
            continue
        group = groups.setdefault(
            date_text,
            {"date": date_text, "label": format_shortlist_date(date_text),
             "count": 0, "sent": 0, "entries": []},
        )
        timestamp = as_ist_datetime(person["shortlisted_at"] or person["created_at"])
        group["count"] += 1
        if person["status"] != "shortlisted":
            group["sent"] += 1
        group["entries"].append(
            {
                "time": timestamp.strftime("%I:%M %p").lstrip("0") if timestamp else "",
                "name": person["name"],
                "status": person["status"],
            }
        )
    return sorted(groups.values(), key=lambda group: group["date"], reverse=True)


def normalize_filters(form):
    per_page_raw = form.get("per_page", "10")
    per_page = int(per_page_raw) if per_page_raw in ("5", "10") else 10
    mode = clean_search_input(form.get("mode", "browse")).lower()
    if mode not in ("browse", "person"):
        mode = "browse"
    person_input = clean_search_input(form.get("person_input", ""))
    parsed_person = parse_person_input(person_input) if person_input else None
    if parsed_person and parsed_person["kind"] == "invalid":
        raise ValueError("That LinkedIn URL is not a valid /in/ profile URL.")
    org_type = clean_search_input(form.get("org_type", "")).lower()
    if org_type not in ORG_TYPES:
        org_type = ""
    filter_status = clean_search_input(form.get("filter_status", "any")).lower()
    if filter_status not in FILTER_STATUSES:
        filter_status = "any"
    batch_year = clean_search_input(form.get("batch_year", ""))
    if not (len(batch_year) == 4 and batch_year.isdigit()):
        batch_year = ""
    role = clean_search_input(form.get("role", ""))
    country = clean_search_input(form.get("country", "")).upper()
    if country and country not in COUNTRY_NAME_BY_CODE:
        raise ValueError("Choose a valid country.")
    state = normalize_location_field(
        form.get("state_custom") or form.get("state", ""), "State"
    )
    city_value = form.get("city_custom") or form.get("city", "")
    city = normalize_location_field(
        "" if city_value == "__other__" else city_value, "City"
    )
    if state == "__other__":
        state = ""
    disambiguate = str(form.get("disambiguate", "")).lower() in (
        "1", "true", "on", "yes"
    )
    raw_org_name = clean_search_input(form.get("org_name", ""))
    org_name = raw_org_name if org_type else ""
    exclude_words = clean_search_input(
        form.get("exclude_words", DEFAULT_EXCLUDES)
    )
    platforms = [
        platform for platform in form.getlist("platforms")
        if platform in SEARCH_PLATFORMS
    ]
    if (
        mode == "person"
        and not platforms
        and form.get("platforms_enabled") != "1"
    ):
        platforms = ["linkedin"]
    widen_search = str(form.get("widen_search", "")).lower() in ("1", "true", "on")
    return {
        "mode": mode,
        "person_input": person_input,
        "person_kind": parsed_person["kind"] if parsed_person else "",
        "person_slug": parsed_person.get("slug", "") if parsed_person else "",
        "platforms": platforms,
        "role": role,
        "city": city,
        "country": country,
        "state": state,
        "city_custom": normalize_location_field(
            form.get("city_custom", ""), "City"
        ),
        "state_custom": normalize_location_field(
            form.get("state_custom", ""), "State"
        ),
        "disambiguate": disambiguate,
        "org_type": org_type,
        "org_name": org_name,
        "filter_status": filter_status,
        "batch_year": batch_year if org_type in ("college", "school") else "",
        "exclude_words": exclude_words,
        "per_page": per_page,
        "widen_search": widen_search,
    }


def normalize_location_field(value, label):
    text = " ".join(str(value or "").split())
    if len(text) > 80:
        raise ValueError(f"{label} must be 80 characters or fewer.")
    return text.translate(str.maketrans("", "", '"()\\')).strip()


def load_countries():
    return json.loads(
        (LOCATION_DATA_DIR / "countries.json").read_text(encoding="utf-8")
    )


def location_term(filters):
    if filters.get("city"):
        return filters["city"]
    if filters.get("state"):
        return filters["state"]
    country = filters.get("country", "")
    return COUNTRY_NAME_BY_CODE.get(country, "")


def location_cache_key(filters):
    query = build_query(filters)
    return f"country:{filters.get('country') or 'worldwide'}:{query}"


def format_location(filters):
    parts = [
        filters.get("city", ""),
        filters.get("state", ""),
        COUNTRY_NAME_BY_CODE.get(filters.get("country", ""), ""),
    ]
    return ", ".join(part for part in parts if part) or "Worldwide"


def clean_search_input(value):
    text = " ".join(str(value or "").split())
    if len(text) > MAX_SEARCH_TEXT_LENGTH:
        raise ValueError(
            f"Search fields must be {MAX_SEARCH_TEXT_LENGTH} characters or fewer."
        )
    return text


def normalize_page_size(value):
    return int(value) if value in ("5", "10") else 10


def normalize_manual_instagram_handle(value):
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if "instagram.com" in text.casefold():
        return instagram_handle_from_url(text)
    return normalize_instagram_handle(text)


def platform_search_name(filters):
    if filters.get("person_kind") == "profile":
        return filters["person_slug"].replace("-", " ").replace("_", " ")
    return filters["person_input"]


def run_person_platform_search(filters, page, refresh, db_factory):
    name = platform_search_name(filters)
    location = location_term(filters)
    queries = build_platform_queries(
        name,
        filters["org_name"],
        location,
        filters["role"],
        filters["platforms"],
    )
    if filters.get("disambiguate") and filters.get("city") and filters.get("state"):
        state_term = quote_search_term(filters["state"])
        queries = {
            platform: f"{query} {state_term}"
            for platform, query in queries.items()
        }
    if filters.get("person_kind") == "profile" and "linkedin" in queries:
        linkedin_terms = [f"linkedin.com/in/{filters['person_slug']}"]
        if filters["org_name"]:
            linkedin_terms.append(quote_search_term(filters["org_name"]))
        linkedin_terms.extend(location_query_terms(filters))
        if filters["role"]:
            linkedin_terms.append(quote_search_term(filters["role"]))
        queries["linkedin"] = " ".join(linkedin_terms)
    if filters["filter_status"] == "current":
        queries = {
            platform: f"{query} Present"
            for platform, query in queries.items()
        }
        if filters["org_type"] in ("college", "school") and filters["batch_year"]:
            queries = {
                platform: f"{query} {filters['batch_year']}"
                for platform, query in queries.items()
            }
    elif filters["filter_status"] == "past":
        queries = {
            platform: f"{query} -Present"
            for platform, query in queries.items()
        }
    groups = []
    errors = {}
    cache_status = {}
    next_page = False
    for platform, query in queries.items():
        items, error, cached, _used_query, has_next = get_platform_results(
            platform,
            query,
            page,
            filters["per_page"],
            db_factory,
            refresh=refresh,
            gl=filters["country"].lower() if filters["country"] else None,
            country=filters["country"],
        )
        cache_status[platform] = cached
        if error:
            errors[platform] = error
        results = shape_platform_results(items, platform, name, filters["org_name"])
        if platform == "linkedin":
            saved_people = get_saved_people_by_url(db_factory)
            for result in results:
                result["saved_person"] = saved_people.get(result["url"])
        next_page = next_page or has_next
        groups.append(
            {
                "platform": platform,
                "query": query,
                "results": results,
                "error": error,
                "cached": cached,
            }
        )
    return groups, queries, errors, cache_status, next_page


def get_platform_results(
    platform, query, page, per_page, db_factory, refresh=False, gl=None, country=""
):
    key_prefix = "videos" if platform == "youtube" else platform
    cache_query = f"platform:{key_prefix}:country:{country or 'worldwide'}:{query}"
    fallback_query = f"site:youtube.com {query}" if platform == "youtube" else ""
    fallback_key = (
        f"platform:youtube-web:country:{country or 'worldwide'}:{fallback_query}"
        if fallback_query else ""
    )
    if not refresh:
        cached_results = get_cached_results(db_factory, cache_query, page, per_page)
        if cached_results is not None and (cached_results or platform != "youtube"):
            return (
                cached_results,
                None,
                True,
                query,
                platform != "youtube" and len(cached_results) == per_page and page < 10,
            )
        if cached_results == [] and platform == "youtube":
            fallback_results = get_cached_results(
                db_factory, fallback_key, page, per_page
            )
            if fallback_results is not None:
                return fallback_results, None, True, fallback_query, False
    try:
        if platform == "youtube":
            items = cached_results if refresh is False and cached_results is not None else None
            if items is None:
                video_kwargs = {"num": per_page}
                if gl is not None:
                    video_kwargs["gl"] = gl
                items = search_videos(query, **video_kwargs)[:per_page]
                save_cached_results(db_factory, cache_query, page, per_page, items)
            if not items:
                items = (
                    get_cached_results(db_factory, fallback_key, page, per_page)
                    if not refresh
                    else None
                )
                if items is None:
                    search_kwargs = {"num": per_page, "page": page}
                    if gl is not None:
                        search_kwargs["gl"] = gl
                    items = search_web(fallback_query, **search_kwargs)
                    items = items[:per_page]
                    save_cached_results(db_factory, fallback_key, page, per_page, items)
                query = fallback_query
        else:
            search_kwargs = {"num": per_page, "page": page}
            if gl is not None:
                search_kwargs["gl"] = gl
            items = search_web(query, **search_kwargs)
            items = items[:per_page]
            save_cached_results(db_factory, cache_query, page, per_page, items)
        has_next = platform != "youtube" and len(items) == per_page and page < 10
        return items, None, False, query, has_next
    except RuntimeError as exc:
        return [], str(exc), False, query, False
    except SearchAPIError as exc:
        if exc.status_code in (401, 403):
            message = "Serper rejected the API key. Check SERPER_API_KEY."
        elif exc.status_code == 429:
            message = "Serper rate limit reached or search credits exhausted."
        else:
            message = f"Serper returned HTTP {exc.status_code}."
        return [], message, False, query, False
    except SearchTimeoutError:
        return [], "Serper search timed out.", False, query, False
    except SearchConnectionError:
        return [], "Could not connect to Serper.", False, query, False
    except SearchResponseError:
        return [], "Serper returned an unreadable response.", False, query, False


def canonicalize_platform_link(platform, kind, url):
    if platform == "linkedin":
        return normalize_linkedin_url(url)
    if not isinstance(url, str):
        return None
    try:
        parsed = urlsplit(url if "://" in url else f"https://{url}")
    except ValueError:
        return None
    path = (parsed.path or "").rstrip("/")
    if platform == "instagram":
        if kind == "profile":
            handle = instagram_handle_from_url(url)
            return f"https://www.instagram.com/{handle}/" if handle else None
        if kind in ("post", "reel"):
            parts = path.split("/")
            if len(parts) == 3 and parts[1] and parts[2]:
                return f"https://www.instagram.com/{parts[1].casefold()}/{parts[2]}"
    if platform == "youtube":
        if kind == "channel":
            if parsed.hostname and parsed.hostname.casefold().endswith("youtube.com"):
                return f"https://www.youtube.com{path}"
        if kind == "video":
            if parsed.hostname and parsed.hostname.casefold().endswith("youtu.be"):
                video_id = path.strip("/")
                return f"https://www.youtube.com/watch?v={video_id}" if video_id else None
            if path == "/watch":
                video_id = next(
                    (value for key, value in parse_qsl(parsed.query) if key == "v"),
                    "",
                )
                return f"https://www.youtube.com/watch?v={video_id}" if video_id else None
            if path.startswith("/shorts/"):
                return f"https://www.youtube.com{path}"
    return None


def shape_platform_results(items, platform, name, org):
    results = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        url = str(item.get("link") or "").strip()
        classified_platform, kind = classify_link(url)
        if classified_platform != platform or kind in (None, "other"):
            continue
        canonical_url = canonicalize_platform_link(platform, kind, url)
        if not canonical_url or canonical_url in seen:
            continue
        seen.add(canonical_url)
        handle = extract_instagram_handle(title, url) if platform == "instagram" else ""
        results.append(
            {
                "platform": platform,
                "kind": kind,
                "url": canonical_url,
                "title": title,
                "snippet": snippet,
                "handle": handle or "",
                "confidence": match_confidence(name, org, title, snippet),
                "date": str(item.get("date") or ""),
            }
        )
    return results


def quote_search_term(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def escape_query_fragment(value):
    return value.replace("\\", "\\\\").replace('"', '\\"')


def build_query(filters):
    location_terms = location_query_terms(filters)

    if filters.get("mode") == "person":
        if filters.get("person_kind") == "profile":
            terms = [f'linkedin.com/in/{filters["person_slug"]}']
            if filters.get("role"):
                terms.append(quote_search_term(filters["role"]))
            terms.extend(location_terms)
        else:
            terms = ["site:linkedin.com/in", quote_search_term(filters["person_input"])]
            if filters.get("role"):
                terms.append(quote_search_term(filters["role"]))
            terms.extend(location_terms)
    else:
        role_term = quote_search_term(filters["role"]) if filters.get("role") else ""
        if filters.get("widen_search") and filters.get("role"):
            synonyms = {
                "content creator": ("content creator", "creator", "youtuber", "influencer"),
                "video editor": ("video editor", "video editing", "editor"),
                "reel editor": ("reel editor", "video editor", "video editing", "editor"),
                "videographer": ("videographer", "cinematographer", "filmmaker"),
            }
            terms_for_role = synonyms.get(filters["role"].casefold())
            if terms_for_role:
                role_term = "(" + " OR ".join(
                    quote_search_term(term) for term in terms_for_role
                ) + ")"
        terms = ["site:linkedin.com/in"]
        if role_term:
            terms.append(role_term)
        terms.extend(location_terms)
    if filters["org_name"]:
        terms.append(quote_search_term(filters["org_name"]))
    if filters["filter_status"] == "current":
        terms.append("Present")
        if filters["org_type"] in ("college", "school") and filters["batch_year"]:
            terms.append(filters["batch_year"])
    elif filters["filter_status"] == "past":
        terms.append("-Present")
    terms.extend(
        f"-{word.strip().replace(chr(34), '')}"
        for word in filters["exclude_words"].split(",")
        if word.strip()
    )
    return " ".join(terms)


def location_query_terms(filters):
    if filters.get("disambiguate") and filters.get("city") and filters.get("state"):
        return [
            filters["city"]
            if " " not in filters["city"]
            else quote_search_term(filters["city"]),
            quote_search_term(filters["state"]),
        ]
    if filters.get("city"):
        return [
            filters["city"]
            if " " not in filters["city"]
            else quote_search_term(filters["city"])
        ]
    if filters.get("state"):
        return [quote_search_term(filters["state"])]
    if filters.get("country"):
        return [quote_search_term(COUNTRY_NAME_BY_CODE[filters["country"]])]
    return []


def parse_page(value):
    try:
        return max(1, min(int(value or "1"), 10))
    except ValueError:
        return 1


def shape_results(items, filters):
    shaped = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        url = normalize_linkedin_url(item.get("link", ""))
        if url is None or url in seen:
            continue
        seen.add(url)
        title = str(item.get("title") or "").strip()
        name, _, headline = title.partition(" - ")
        affiliation = analyze_affiliation(
            title, str(item.get("snippet") or ""), filters["org_name"]
        )
        shaped.append(
            {
                "name": name or "LinkedIn profile",
                "headline": headline,
                "snippet": str(item.get("snippet") or "").strip(),
                "url": url,
                "org_type": filters["org_type"],
                "org_name": filters["org_name"],
                **affiliation,
            }
        )
    return shaped


def parse_raw_cursor(value, fallback=1):
    try:
        cursor = int(value or fallback)
    except (TypeError, ValueError):
        cursor = fallback
    return max(1, min(cursor, 10))


def parse_cursor_history(value):
    cursors = []
    for item in str(value or "").split(","):
        try:
            cursor = int(item)
        except ValueError:
            continue
        if 1 <= cursor <= 10 and (not cursors or cursor > cursors[-1]):
            cursors.append(cursor)
    return cursors


def search_error_message(exc):
    if isinstance(exc, RuntimeError):
        return str(exc)
    if isinstance(exc, SearchAPIError):
        if exc.status_code in (401, 403):
            return "Serper rejected the API key. Check SERPER_API_KEY in your .env file."
        if exc.status_code == 429:
            return "Serper rate limit reached or search credits exhausted. Please try again later."
        return "Serper search could not complete this request. Please try again."
    if isinstance(exc, SearchTimeoutError):
        return "Serper search timed out. Please try again."
    if isinstance(exc, SearchConnectionError):
        return "Could not connect to Serper. Check your connection and try again."
    if isinstance(exc, SearchResponseError):
        return "Serper returned an unreadable response. Please try again."
    raise exc


def build_search_key(filters):
    key_filters = {
        key: filters.get(key, "")
        for key in (
            "role",
            "country",
            "state",
            "city",
            "org_type",
            "org_name",
            "filter_status",
            "batch_year",
            "widen_search",
            "disambiguate",
            "exclude_words",
        )
    }
    encoded = json.dumps(
        key_filters, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def clean_pool_search_key(value):
    search_key = str(value or "").strip()
    if not re.fullmatch(r"[a-f0-9]{64}", search_key):
        abort(400)
    return search_key


def insert_selected_profiles(connection, selected, batch_id, timestamp):
    saved = 0
    skipped = 0
    for item in selected:
        if not isinstance(item, dict):
            abort(400)
        url = normalize_linkedin_url(item.get("url", ""))
        if url is None:
            abort(400)
        org_type = str(item.get("org_type") or "").lower()
        if org_type not in ORG_TYPES:
            org_type = ""
        existing = connection.execute(
            "SELECT id FROM people WHERE normalize_linkedin_url(url) = ?",
            (url,),
        ).fetchone()
        if existing:
            skipped += 1
            continue
        cursor = connection.execute(
            """INSERT OR IGNORE INTO people
                (name, headline, snippet, url, college, source, status,
                 org_type, org_name, status_hint, years_text, batch_id,
                 shortlisted_at, status_changed_at)
                VALUES (?, ?, ?, ?, '', 'LinkedIn', 'shortlisted',
                        ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(item.get("name") or "LinkedIn profile")[:200],
                str(item.get("headline") or "")[:500],
                str(item.get("snippet") or "")[:2000],
                url[:500],
                org_type,
                str(item.get("org_name") or "")[:120],
                item.get("status_hint")
                if item.get("status_hint") in ("current", "past", "unclear")
                else "unclear",
                str(item.get("years_text") or "")[:40],
                batch_id,
                timestamp,
                timestamp,
            ),
        )
        saved += cursor.rowcount
        if not cursor.rowcount:
            skipped += 1
            continue
        connection.execute(
            """INSERT OR IGNORE INTO person_links
               (person_id, platform, kind, url, title, snippet, handle,
                confidence, confirmed, dismissed)
               VALUES (?, 'linkedin', 'profile', ?, ?, ?, '', 'likely', 1, 0)""",
            (
                cursor.lastrowid,
                url,
                str(item.get("name") or "")[:200],
                str(item.get("snippet") or "")[:2000],
            ),
        )
        connection.execute(
            """INSERT INTO status_history
               (person_id, old_status, new_status, changed_at)
               VALUES (?, NULL, 'shortlisted', ?)""",
            (cursor.lastrowid, timestamp),
        )
    return saved, skipped


def save_search_pool(db_factory, search_key, profiles):
    if not profiles:
        return
    found_at = datetime.now(ZoneInfo("UTC")).isoformat(timespec="seconds")
    with db_factory() as connection:
        for profile in profiles:
            url = normalize_linkedin_url(profile.get("url", ""))
            if not url:
                continue
            connection.execute(
                """INSERT INTO search_pool (search_key, url, profile_json, found_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(search_key, url) DO UPDATE SET
                     profile_json = excluded.profile_json,
                     found_at = excluded.found_at""",
                (
                    search_key,
                    url,
                    json.dumps(profile, ensure_ascii=False),
                    found_at,
                ),
            )


def get_search_pool_summary(db_factory, search_key):
    with db_factory() as connection:
        rows = connection.execute(
            "SELECT url FROM search_pool WHERE search_key = ? ORDER BY id",
            (search_key,),
        ).fetchall()
        existing = {
            normalize_linkedin_url(row["url"])
            for row in connection.execute("SELECT url FROM people").fetchall()
        }
    saved = sum(
        1 for row in rows if normalize_linkedin_url(row["url"]) in existing
    )
    return {
        "total": len(rows),
        "saved": saved,
        "unsaved": len(rows) - saved,
        "urls": {normalize_linkedin_url(row["url"]) for row in rows},
    }


def get_next_collect_page(db_factory, query, per_page, pooled_urls):
    with db_factory() as connection:
        rows = connection.execute(
            """SELECT page, results_json FROM search_cache
               WHERE query = ? AND per_page = ? ORDER BY page""",
            (query, per_page),
        ).fetchall()
    cached_pages = {row["page"]: row["results_json"] for row in rows}
    for page in range(1, 11):
        if page not in cached_pages:
            return page
        try:
            items = json.loads(cached_pages[page])
        except (json.JSONDecodeError, TypeError):
            items = []
        if not items:
            return 11
        for item in items:
            if not isinstance(item, dict):
                continue
            url = normalize_linkedin_url(item.get("link", ""))
            if url and url not in pooled_urls:
                return page
    return 11


def get_cached_page_count(db_factory, query, per_page):
    with db_factory() as connection:
        return connection.execute(
            """SELECT COUNT(DISTINCT page) FROM search_cache
               WHERE query = ? AND per_page = ?""",
            (query, per_page),
        ).fetchone()[0]


def run_browse_search(
    query,
    cache_query,
    filters,
    raw_cursor,
    previous_cursors,
    show_saved,
    refresh,
    gl,
    db_factory,
    max_calls=MAX_SERPER_CALLS_PER_SEARCH,
    max_pages=None,
    target_count=None,
    exclude_urls=None,
):
    """Fetch cached raw pages until this browse page is filled or bounded."""
    saved_people = get_saved_people_by_url(db_factory)
    results = []
    pool_profiles = []
    seen_urls = set(exclude_urls or ())
    counted_saved_urls = set()
    raw_result_count = 0
    non_profile_count = 0
    already_saved_count = 0
    used_credits = 0
    cache_hits = 0
    attempts = 0
    last_raw_count = 0
    last_consumed_cursor = raw_cursor - 1
    api_error = None
    target_count = filters["per_page"] if target_count is None else target_count

    max_pages = max_calls if max_pages is None else max_pages
    for offset in range(max_pages):
        if len(results) >= target_count:
            break
        raw_page = raw_cursor + offset
        if raw_page > 10:
            break
        attempts += 1
        provider_results = None
        if not (refresh and offset == 0):
            provider_results = get_cached_results(
                db_factory, cache_query, raw_page, filters["per_page"]
            )
            if provider_results is not None:
                cache_hits += 1
        if provider_results is None:
            if used_credits >= max_calls:
                break
            used_credits += 1
            try:
                search_kwargs = {"num": filters["per_page"], "page": raw_page}
                if gl is not None:
                    search_kwargs["gl"] = gl
                provider_results = search_web(query, **search_kwargs)
                save_cached_results(
                    db_factory, cache_query, raw_page, filters["per_page"], provider_results
                )
            except (
                RuntimeError,
                SearchAPIError,
                SearchTimeoutError,
                SearchConnectionError,
                SearchResponseError,
            ) as exc:
                api_error = search_error_message(exc)
                break

        last_consumed_cursor = raw_page
        last_raw_count = len(provider_results)
        raw_result_count += last_raw_count
        for item in provider_results:
            if not isinstance(item, dict):
                non_profile_count += 1
                continue
            normalized_url = normalize_linkedin_url(item.get("link", ""))
            if normalized_url is None:
                non_profile_count += 1
                continue
            if normalized_url in seen_urls:
                continue
            seen_urls.add(normalized_url)
            shaped = shape_results([item], filters)
            if not shaped:
                non_profile_count += 1
                continue
            result = shaped[0]
            saved = saved_people.get(normalized_url)
            result["saved_person"] = saved
            if saved:
                result["shortlist_disabled"] = True
            pool_profiles.append(result)
            if saved:
                if normalized_url not in counted_saved_urls:
                    already_saved_count += 1
                    counted_saved_urls.add(normalized_url)
                result["shortlist_disabled"] = True
                if not show_saved:
                    continue
            result["role_label"] = detect_role(
                result["headline"], result["snippet"], result["org_name"]
            )[0]
            if len(results) < target_count:
                results.append(result)
        if len(results) >= target_count or not provider_results or raw_page >= 10:
            break

    results = results[:filters["per_page"]]
    next_page = last_raw_count > 0 and last_consumed_cursor < 10
    history = list(previous_cursors)
    page_number = len(history) + 1
    next_history = history + [raw_cursor]
    cap_reached = (
        len(results) < filters["per_page"]
        and last_consumed_cursor >= raw_cursor
        and attempts >= max_calls
        and last_consumed_cursor < 10
        and api_error is None
    )
    return {
        "results": results,
        "pool_profiles": pool_profiles,
        "page": page_number,
        "next_page": next_page,
        "cached": cache_hits > 0,
        "api_error": api_error,
        "already_saved_count": already_saved_count,
        "raw_page_cursor": raw_cursor,
        "next_raw_page_cursor": last_consumed_cursor + 1,
        "previous_cursors": history,
        "next_previous_cursors": next_history,
        "raw_result_count": raw_result_count,
        "non_profile_count": non_profile_count,
        "used_credits": used_credits,
        "underfilled": cap_reached,
    }


def get_saved_people_by_url(db_factory):
    with db_factory() as connection:
        rows = connection.execute(
            """SELECT p.id, p.url, p.status, p.batch_id, b.name AS batch_name
               FROM people p LEFT JOIN batches b ON b.id = p.batch_id"""
        ).fetchall()
    saved = {}
    for row in rows:
        normalized = normalize_linkedin_url(row["url"])
        if normalized and normalized not in saved:
            saved[normalized] = {
                "id": row["id"],
                "status": row["status"],
                "batch_name": row["batch_name"] or "Earlier outreach",
            }
    return saved


def ist_now():
    return datetime.now(IST).isoformat(timespec="seconds")


def as_ist_datetime(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed.astimezone(IST)


def create_batch(connection):
    timestamp = ist_now()
    name = f"Batch {datetime.now(IST):%Y-%m-%d %H:%M}"
    cursor = connection.execute(
        "INSERT INTO batches (name, created_at, is_current) VALUES (?, ?, 1)",
        (name, timestamp),
    )
    return cursor.lastrowid


def ensure_current_batch(connection):
    current = connection.execute(
        "SELECT id FROM batches WHERE is_current = 1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return current["id"] if current else create_batch(connection)


def get_cached_results(db_factory, query, page, per_page):
    now = time.time()
    with db_factory() as connection:
        row = connection.execute(
            """SELECT results_json, cached_at FROM search_cache
               WHERE query = ? AND page = ? AND per_page = ?""",
            (query, page, per_page),
        ).fetchone()
        if row is None:
            return None
        if now - row["cached_at"] > SEARCH_CACHE_SECONDS:
            connection.execute(
                "DELETE FROM search_cache WHERE query = ? AND page = ? AND per_page = ?",
                (query, page, per_page),
            )
            return None
        try:
            results = json.loads(row["results_json"])
        except (json.JSONDecodeError, TypeError):
            connection.execute(
                "DELETE FROM search_cache WHERE query = ? AND page = ? AND per_page = ?",
                (query, page, per_page),
            )
            return None
        if not isinstance(results, list):
            connection.execute(
                "DELETE FROM search_cache WHERE query = ? AND page = ? AND per_page = ?",
                (query, page, per_page),
            )
            return None
        return results


def save_cached_results(db_factory, query, page, per_page, results):
    with db_factory() as connection:
        connection.execute(
            """INSERT OR REPLACE INTO search_cache
               (query, page, per_page, results_json, cached_at)
               VALUES (?, ?, ?, ?, ?)""",
            (query, page, per_page, json.dumps(results), time.time()),
        )


def make_draft(channel, name, personal_note, one_liner, offer):
    if channel == "connection":
        if not personal_note:
            return f"Hi {name} — I'd love to connect."
        fixed_length = len(f"Hi {name} — I liked your work on . I'd love to connect.")
        note_limit = max(0, 299 - fixed_length)
        short_note = personal_note[:note_limit].rstrip()
        prefix = f"Hi {name} — I liked your work on {short_note}. "
        suffix = "I'd love to connect."
        draft = prefix + suffix
        if len(draft) >= 300:
            draft = f"Hi {name} — I liked your work. I'd love to connect."
        return draft[:299]
    specific_work = f"I liked your work on {personal_note}. " if personal_note else ""
    return f"Hi {name}, thanks for connecting. {specific_work}{one_liner} {offer}"


def csv_safe(value):
    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def parse_tracker_filters(args):
    status_filter = args.get("status", "")
    verified_current = args.get("verified_current") == "1"
    if status_filter and status_filter not in STATUSES:
        abort(400)
    batch_filter = args.get("batch", "")
    if batch_filter not in ("", "current") and not batch_filter.isdigit():
        abort(400)
    if batch_filter.isdigit() and int(batch_filter) < 1:
        abort(400)
    due_filter = args.get("due") == "1"
    return status_filter, verified_current, batch_filter, due_filter


def get_tracker_people(
    db_factory,
    status_filter="",
    verified_current=False,
    batch_filter="",
    due_filter=False,
    order_by="updated_at",
):
    if order_by not in ("created_at", "updated_at"):
        raise ValueError(f"Unsupported tracker sort column: {order_by}")
    conditions = []
    parameters = []
    if status_filter:
        conditions.append("p.status = ?")
        parameters.append(status_filter)
    if verified_current:
        conditions.append("p.verified_status = 'current'")
    if batch_filter == "current":
        conditions.append("b.is_current = 1")
    elif batch_filter:
        conditions.append("p.batch_id = ?")
        parameters.append(int(batch_filter))
    if due_filter:
        conditions.append("p.follow_up_date != '' AND p.follow_up_date <= ?")
        parameters.append(datetime.now(IST).date().isoformat())
    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    query = (
        "SELECT p.id, p.name, p.headline, p.snippet, p.url, p.org_type, p.org_name, "
        "p.status_hint, p.years_text, p.verified_status, p.verified_note, "
        "p.source, p.referred_by, p.touch_count, p.follow_up_date, "
        "p.status, p.personal_note, "
        "(SELECT m.draft_text FROM messages m WHERE m.person_id = p.id "
        "ORDER BY m.created_at DESC, m.id DESC LIMIT 1) AS draft, "
        "p.created_at, p.updated_at, p.sent_at, p.batch_id, "
        "p.shortlisted_at, p.status_changed_at, p.removed_from_shortlist, "
        "b.name AS batch_name, "
        "p.instagram_handle, "
        "(SELECT l.url FROM person_links l WHERE l.person_id = p.id "
        "AND l.platform = 'instagram' AND l.kind = 'profile' "
        "AND l.confirmed = 1 AND l.dismissed = 0 "
        "ORDER BY l.id DESC LIMIT 1) "
        "AS confirmed_instagram_link, "
        "(SELECT l.url FROM person_links l WHERE l.person_id = p.id "
        "AND l.platform = 'youtube' AND l.confirmed = 1 AND l.dismissed = 0 "
        "ORDER BY l.id DESC LIMIT 1) AS confirmed_youtube_link, "
        "(SELECT sm.text FROM sent_messages sm WHERE sm.person_id = p.id "
        "ORDER BY sm.sent_at DESC, sm.id DESC LIMIT 1) AS last_sent_text, "
        "(SELECT h.changed_at FROM status_history h WHERE h.person_id = p.id "
        "ORDER BY h.changed_at DESC, h.id DESC LIMIT 1) AS last_status_changed_at "
        "FROM people p LEFT JOIN batches b ON b.id = p.batch_id"
        f"{where_clause} ORDER BY p.{order_by} DESC"
    )
    with db_factory() as connection:
        return connection.execute(query, parameters).fetchall()


def status_filter_value(form):
    value = form.get("return_status", "")
    return value if value in STATUSES else ""


def read_message_settings(connection):
    return {
        row["key"]: row["value"]
        for row in connection.execute("SELECT key, value FROM settings").fetchall()
    }


def save_message_settings(connection, values):
    connection.executemany(
        """INSERT INTO settings (key, value) VALUES (?, ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
        [(key, clean_text(value, 5000)) for key, value in values.items()],
    )


def count_sent_today(sent_timestamps):
    today = datetime.now(IST).date()
    return sum(
        1
        for row in sent_timestamps
        if (sent_time := as_ist_datetime(row["sent_at"])) is not None
        and sent_time.date() == today
    )


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
