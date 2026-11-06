import csv
import io
import json
import time
from datetime import date, datetime, timedelta
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
IST = ZoneInfo("Asia/Kolkata")


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    init_db(app.config["DATABASE_PATH"])

    def db():
        return connect(current_app.config["DATABASE_PATH"])

    app.extensions["editorscout_db"] = db

    @app.get("/")
    def search_page():
        return render_template(
            "search.html",
            roles=ROLES,
            college_options=COLLEGES,
            org_types=ORG_TYPES,
            statuses=FILTER_STATUSES,
            platforms=SEARCH_PLATFORMS,
            default_excludes=DEFAULT_EXCLUDES,
            filters=request.args,
            results=None,
            page=1,
            per_page=10,
        )

    @app.post("/search")
    def search():
        try:
            filters = normalize_filters(request.form)
            if filters["mode"] == "browse":
                if filters["role"] not in ROLES or not filters["city"]:
                    raise ValueError("Choose a role and enter a city to browse.")
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
                default_excludes=DEFAULT_EXCLUDES,
                filters=error_filters,
                results=None,
                page=1,
                per_page=normalize_page_size(request.form.get("per_page")),
            ), 400

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
            )
        query = build_query(filters)
        results = []
        next_page = False
        api_error = None
        cached = False
        refresh = request.form.get("refresh") == "1"
        provider_results = None
        if not refresh:
            provider_results = get_cached_results(db, query, page, filters["per_page"])
            cached = provider_results is not None
        if provider_results is None:
            try:
                provider_results = search_web(
                    query, num=filters["per_page"], page=page
                )
                provider_results = provider_results[:filters["per_page"]]
                save_cached_results(
                    db, query, page, filters["per_page"], provider_results
                )
            except RuntimeError as exc:
                api_error = str(exc)
            except SearchAPIError as exc:
                if exc.status_code in (401, 403):
                    api_error = "Serper rejected the API key. Check SERPER_API_KEY in your .env file."
                elif exc.status_code == 429:
                    api_error = "Serper rate limit reached or search credits exhausted. Please try again later."
                else:
                    api_error = "Serper search could not complete this request. Please try again."
            except SearchTimeoutError:
                api_error = "Serper search timed out. Please try again."
            except SearchConnectionError:
                api_error = "Could not connect to Serper. Check your connection and try again."
            except SearchResponseError:
                api_error = "Serper returned an unreadable response. Please try again."

        if provider_results is not None:
            results = shape_results(provider_results, filters)
            saved_people = get_saved_people_by_url(db)
            already_saved = 0
            for result in results:
                saved = saved_people.get(result["url"])
                result["saved_person"] = saved
                if saved:
                    already_saved += 1
            if filters["mode"] == "browse":
                if request.form.get("show_saved") != "1":
                    results = [result for result in results if not result["saved_person"]]
                else:
                    for result in results:
                        result["shortlist_disabled"] = bool(result["saved_person"])
            else:
                for result in results:
                    result["shortlist_disabled"] = bool(result["saved_person"])
            next_page = len(provider_results) == filters["per_page"] and page < 10
        else:
            already_saved = 0

        with db() as connection:
            connection.execute(
                "INSERT INTO searches (query, filters_json, result_count) VALUES (?, ?, ?)",
                (query, json.dumps(filters, ensure_ascii=False), len(results)),
            )

        if api_error:
            flash(api_error, "error")

        return render_template(
            "search.html",
            roles=ROLES,
            college_options=COLLEGES,
            org_types=ORG_TYPES,
            statuses=FILTER_STATUSES,
            platforms=SEARCH_PLATFORMS,
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
            show_saved=request.form.get("show_saved") == "1",
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
        saved = 0
        with db() as connection:
            batch_id = ensure_current_batch(connection)
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
                    continue
                timestamp = ist_now()
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
                if cursor.rowcount:
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
        flash(f"Shortlisted {saved} profile(s).", "success")
        return redirect(url_for("shortlist_page"))

    @app.get("/shortlist")
    def shortlist_page():
        with db() as connection:
            batch = connection.execute(
                "SELECT * FROM batches WHERE is_current = 1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
            people = [
                dict(person)
                for person in connection.execute(
                    """SELECT * FROM people WHERE batch_id = ?
                       AND removed_from_shortlist = 0
                       ORDER BY shortlisted_at DESC, id DESC""",
                    (batch["id"] if batch else -1,),
                ).fetchall()
            ]
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
            person_links_by_person=person_links_by_person,
            common_messages=common_messages,
            my_name=settings.get("my_name", ""),
            message_link=settings.get("link", ""),
            sent_today=sent_today,
            warning_threshold=10,
            sources=SOURCES,
            default_follow_up_date=(datetime.now(IST).date() + timedelta(days=4)).isoformat(),
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
                """SELECT * FROM people WHERE batch_id = (
                       SELECT id FROM batches WHERE is_current = 1
                       ORDER BY id DESC LIMIT 1
                   ) AND removed_from_shortlist = 0 ORDER BY id"""
            ).fetchall()
            if request.form.get("action") == "save":
                flash("Common message and details saved.", "success")
                return redirect(url_for("shortlist_page"))

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
        return redirect(url_for("shortlist_page"))

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
            return redirect(url_for("shortlist_page"))
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
        return redirect(url_for("shortlist_page", links_for=person_id))

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
                return redirect(url_for("shortlist_page", links_for=person_id))
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
        return redirect(url_for("shortlist_page", links_for=person_id))

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
        return redirect(url_for("shortlist_page", links_for=person_id))

    @app.post("/batches/new")
    def start_new_batch():
        with db() as connection:
            connection.execute("UPDATE batches SET is_current = 0 WHERE is_current = 1")
            batch_id = create_batch(connection)
        flash("Started a new shortlist batch.", "success")
        return redirect(url_for("shortlist_page", batch=batch_id))

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
        return redirect(url_for("shortlist_page"))

    @app.post("/people/<int:person_id>/save")
    def save_person(person_id):
        raw_note = request.form.get("personal_note", "")
        raw_verified_note = request.form.get("verified_note", "")
        raw_instagram_handle = request.form.get("instagram_handle")
        if len(raw_note) > MAX_NOTE_LENGTH or len(raw_verified_note) > MAX_NOTE_LENGTH:
            flash(f"Notes must be {MAX_NOTE_LENGTH} characters or fewer.", "error")
            return redirect(url_for("shortlist_page"))
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
                    return redirect(url_for("shortlist_page"))
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
        return redirect(url_for("shortlist_page"))

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
                return redirect(url_for("shortlist_page"))
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
                return redirect(url_for("shortlist_page"))
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
            return redirect(url_for("shortlist_page"))
        draft = clean_text(raw_draft, MAX_DRAFT_LENGTH)
        if channel not in ("connection", "follow_up"):
            abort(400)
        if not draft or len(draft) > MAX_DRAFT_LENGTH:
            flash(f"Draft must be between 1 and {MAX_DRAFT_LENGTH} characters.", "error")
            return redirect(url_for("shortlist_page"))
        if channel == "connection" and len(draft) >= 300:
            flash("Connection notes must be under 300 characters.", "error")
            return redirect(url_for("shortlist_page"))
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
        return redirect(url_for("shortlist_page"))

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
                return redirect(url_for("shortlist_page"))
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
        return redirect(url_for("shortlist_page"))

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
                return redirect(url_for("shortlist_page"))
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
        return redirect(url_for("shortlist_page"))

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
    city = clean_search_input(form.get("city", ""))
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
    if mode == "browse" and not role:
        role = ROLES[0]
    return {
        "mode": mode,
        "person_input": person_input,
        "person_kind": parsed_person["kind"] if parsed_person else "",
        "person_slug": parsed_person.get("slug", "") if parsed_person else "",
        "platforms": platforms,
        "role": role,
        "city": city,
        "org_type": org_type,
        "org_name": org_name,
        "filter_status": filter_status,
        "batch_year": batch_year if org_type in ("college", "school") else "",
        "exclude_words": exclude_words,
        "per_page": per_page,
    }


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
    queries = build_platform_queries(
        name,
        filters["org_name"],
        filters["city"],
        filters["role"],
        filters["platforms"],
    )
    if filters.get("person_kind") == "profile" and "linkedin" in queries:
        linkedin_terms = [f"linkedin.com/in/{filters['person_slug']}"]
        if filters["org_name"]:
            linkedin_terms.append(quote_search_term(filters["org_name"]))
        if filters["city"]:
            linkedin_terms.append(escape_query_fragment(filters["city"]))
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


def get_platform_results(platform, query, page, per_page, db_factory, refresh=False):
    key_prefix = "videos" if platform == "youtube" else platform
    cache_query = f"platform:{key_prefix}:{query}"
    fallback_query = f"site:youtube.com {query}" if platform == "youtube" else ""
    fallback_key = f"platform:youtube-web:{fallback_query}" if fallback_query else ""
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
                items = search_videos(query, num=per_page)[:per_page]
                save_cached_results(db_factory, cache_query, page, per_page, items)
            if not items:
                items = (
                    get_cached_results(db_factory, fallback_key, page, per_page)
                    if not refresh
                    else None
                )
                if items is None:
                    items = search_web(fallback_query, num=per_page, page=page)
                    items = items[:per_page]
                    save_cached_results(db_factory, fallback_key, page, per_page, items)
                query = fallback_query
        else:
            items = search_web(query, num=per_page, page=page)
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
    if filters.get("mode") == "person":
        if filters.get("person_kind") == "profile":
            terms = [f'linkedin.com/in/{filters["person_slug"]}']
            if filters.get("role"):
                terms.append(quote_search_term(filters["role"]))
            if filters.get("city"):
                terms.append(escape_query_fragment(filters["city"]))
        else:
            terms = ["site:linkedin.com/in", quote_search_term(filters["person_input"])]
            if filters.get("role"):
                terms.append(quote_search_term(filters["role"]))
            if filters.get("city"):
                terms.append(escape_query_fragment(filters["city"]))
    else:
        terms = [
            "site:linkedin.com/in",
            quote_search_term(filters["role"]),
            escape_query_fragment(filters["city"]),
        ]
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
