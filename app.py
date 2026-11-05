import csv
import io
import json
import time
from datetime import date, datetime
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
from profile_urls import normalize_linkedin_url, parse_person_input
from search_provider import (
    SearchAPIError,
    SearchConnectionError,
    SearchResponseError,
    SearchTimeoutError,
    search_web,
)

STATUSES = ("new", "shortlisted", "sent", "replied", "agreed", "rejected")
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
VERIFIED_STATUSES = ("unverified", "current", "past", "unsure")
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
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template(
                "search.html",
                roles=ROLES,
                college_options=COLLEGES,
                org_types=ORG_TYPES,
                statuses=FILTER_STATUSES,
                default_excludes=DEFAULT_EXCLUDES,
                filters=request.form,
                results=None,
                page=1,
                per_page=normalize_page_size(request.form.get("per_page")),
            ), 400

        page = parse_page(request.form.get("page"))
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
                        VALUES (?, ?, ?, ?, '', 'Serper.dev', 'shortlisted',
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
            people = connection.execute(
                """SELECT * FROM people WHERE batch_id = ?
                   AND removed_from_shortlist = 0
                   ORDER BY shortlisted_at DESC, id DESC""",
                (batch["id"] if batch else -1,),
            ).fetchall()
        return render_template(
            "shortlist.html", people=people, batch=batch, person_count=len(people)
        )

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
        note = request.form.get("personal_note", "").strip()
        verified_note = request.form.get("verified_note", "").strip()
        if len(note) > MAX_NOTE_LENGTH or len(verified_note) > MAX_NOTE_LENGTH:
            flash(f"Notes must be {MAX_NOTE_LENGTH} characters or fewer.", "error")
            return redirect(url_for("shortlist_page"))
        with db() as connection:
            person = connection.execute(
                "SELECT verified_status, verified_note FROM people WHERE id = ?",
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
            cursor = connection.execute(
                """UPDATE people SET personal_note = ?, verified_status = ?,
                    verified_note = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE id = ?""",
                (note, verified_status, verified_note, person_id),
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
            if not note:
                flash("Add one specific thing you liked before generating a draft.", "error")
                return redirect(url_for("shortlist_page"))
            repeat_warning = person["status"] in ("sent", "replied", "agreed")
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
        draft = request.form.get("draft_text", "").strip()
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
                "INSERT INTO messages (person_id, channel, draft_text) VALUES (?, ?, ?)",
                (person_id, channel, draft),
            )
        flash("Edited draft saved.", "success")
        return redirect(url_for("shortlist_page"))

    @app.get("/tracker")
    def tracker():
        status_filter, verified_current, batch_filter = parse_tracker_filters(request.args)
        people = get_tracker_people(
            db, status_filter, verified_current, batch_filter, order_by="updated_at"
        )
        with db() as connection:
            batches = connection.execute(
                "SELECT id, name, is_current FROM batches ORDER BY created_at DESC, id DESC"
            ).fetchall()
            sent_timestamps = connection.execute(
                "SELECT sent_at FROM people WHERE sent_at IS NOT NULL"
            ).fetchall()
        sent_today = sum(
            1
            for row in sent_timestamps
            if (sent_time := as_ist_datetime(row["sent_at"])) is not None
            and sent_time.date() == datetime.now(IST).date()
        )
        return render_template(
            "tracker.html",
            people=people,
            statuses=STATUSES,
            status_filter=status_filter,
            verified_current=verified_current,
            batch_filter=batch_filter,
            batches=batches,
            sent_today=sent_today,
            warning_threshold=10,
        )

    @app.post("/people/<int:person_id>/update")
    def update_person(person_id):
        status = request.form.get("status", "")
        note = request.form.get("personal_note", "").strip()
        if status not in STATUSES:
            abort(400)
        if len(note) > MAX_NOTE_LENGTH:
            flash(f"Notes must be {MAX_NOTE_LENGTH} characters or fewer.", "error")
            return redirect(url_for("tracker"))
        with db() as connection:
            person = connection.execute(
                "SELECT status FROM people WHERE id = ?", (person_id,)
            ).fetchone()
            if person is None:
                abort(404)
            timestamp = ist_now()
            if person["status"] != status:
                connection.execute(
                    """UPDATE people SET status = ?, personal_note = ?,
                       updated_at = ?, status_changed_at = ?,
                       sent_at = CASE WHEN ? = 'sent' THEN ? ELSE sent_at END
                       WHERE id = ?""",
                    (status, note, timestamp, timestamp, status, timestamp, person_id),
                )
                connection.execute(
                    """INSERT INTO status_history
                       (person_id, old_status, new_status, changed_at)
                       VALUES (?, ?, ?, ?)""",
                    (person_id, person["status"], status, timestamp),
                )
            else:
                connection.execute(
                    "UPDATE people SET personal_note = ?, updated_at = ? WHERE id = ?",
                    (note, timestamp, person_id),
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
            )
        )

    @app.get("/export.csv")
    def export_csv():
        status_filter, verified_current, batch_filter = parse_tracker_filters(request.args)
        people = get_tracker_people(
            db, status_filter, verified_current, batch_filter, order_by="created_at"
        )
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(
            ["Name", "Headline", "Snippet", "Profile URL", "Organisation type",
             "Organisation name", "Search status hint", "Search years",
             "Verified status", "Verification note", "Source", "Personal note",
             "Status", "Draft", "Created at", "Updated at", "Sent at", "Batch",
             "Last status change", "Removed from shortlist"]
        )
        csv_fields = (
            "name", "headline", "snippet", "url", "org_type", "org_name",
            "status_hint", "years_text", "verified_status", "verified_note",
            "source", "personal_note", "status", "draft", "created_at",
            "updated_at", "sent_at", "batch_name", "status_changed_at",
            "removed_from_shortlist",
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
        status_filter, verified_current, batch_filter = parse_tracker_filters(request.args)
        people = get_tracker_people(
            db, status_filter, verified_current, batch_filter, order_by="created_at"
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
    if mode == "browse" and not role:
        role = ROLES[0]
    return {
        "mode": mode,
        "person_input": person_input,
        "person_kind": parsed_person["kind"] if parsed_person else "",
        "person_slug": parsed_person.get("slug", "") if parsed_person else "",
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
        fixed_length = len(f"Hi {name} — I liked your work on . I'd love to connect.")
        note_limit = max(0, 299 - fixed_length)
        short_note = personal_note[:note_limit].rstrip()
        prefix = f"Hi {name} — I liked your work on {short_note}. "
        suffix = "I'd love to connect."
        draft = prefix + suffix
        if len(draft) >= 300:
            draft = f"Hi {name} — I liked your work. I'd love to connect."
        return draft[:299]
    return (
        f"Hi {name}, thanks for connecting. I liked your work on {personal_note}. "
        f"{one_liner} {offer}"
    )


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
    return status_filter, verified_current, batch_filter


def get_tracker_people(
    db_factory,
    status_filter="",
    verified_current=False,
    batch_filter="",
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
    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    query = (
        "SELECT p.id, p.name, p.headline, p.snippet, p.url, p.org_type, p.org_name, "
        "p.status_hint, p.years_text, p.verified_status, p.verified_note, "
        "p.source, p.status, p.personal_note, "
        "(SELECT m.draft_text FROM messages m WHERE m.person_id = p.id "
        "ORDER BY m.created_at DESC, m.id DESC LIMIT 1) AS draft, "
        "p.created_at, p.updated_at, p.sent_at, p.batch_id, "
        "p.shortlisted_at, p.status_changed_at, p.removed_from_shortlist, "
        "b.name AS batch_name, "
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


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
