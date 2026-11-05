import csv
import io
import json
from urllib.parse import quote, unquote, urlsplit

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

from config import Config
from database import connect, init_db
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
MAX_NOTE_LENGTH = 1000
MAX_DRAFT_LENGTH = 5000


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
            colleges=COLLEGES,
            default_excludes=DEFAULT_EXCLUDES,
            filters=request.args,
            results=None,
            page=1,
            per_page=10,
        )

    @app.post("/search")
    def search():
        filters = normalize_filters(request.form)
        if filters["role"] not in ROLES or not filters["city"]:
            flash("Choose a role and enter a city to search.", "error")
            return render_template(
                "search.html",
                roles=ROLES,
                colleges=COLLEGES,
                default_excludes=DEFAULT_EXCLUDES,
                filters=filters,
                results=None,
                page=1,
                per_page=filters["per_page"],
            ), 400

        page = parse_page(request.form.get("page"))
        query = build_query(filters)
        results = []
        next_page = False
        api_error = None

        try:
            provider_results = search_web(
                query, num=filters["per_page"], page=page
            )
            results = shape_results(provider_results, filters["college"])
            next_page = len(provider_results) == filters["per_page"] and page < 10
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

        saved_urls = get_saved_urls(db)
        results = [item for item in results if item["url"] not in saved_urls]

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
            colleges=COLLEGES,
            default_excludes=DEFAULT_EXCLUDES,
            filters=filters,
            results=results,
            page=page,
            per_page=filters["per_page"],
            next_page=next_page,
            search_query=query,
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
            for item in selected:
                if not isinstance(item, dict):
                    abort(400)
                url = normalize_linkedin_url(item.get("url", ""))
                if url is None:
                    abort(400)
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO people
                        (name, headline, snippet, url, college, source, status)
                        VALUES (?, ?, ?, ?, ?, 'Serper.dev', 'shortlisted')""",
                    (
                        str(item.get("name") or "LinkedIn profile")[:200],
                        str(item.get("headline") or "")[:500],
                        str(item.get("snippet") or "")[:2000],
                        url[:500],
                        str(item.get("college", ""))[:120],
                    ),
                )
                saved += cursor.rowcount
        flash(f"Shortlisted {saved} profile(s).", "success")
        return redirect(url_for("shortlist_page"))

    @app.get("/shortlist")
    def shortlist_page():
        with db() as connection:
            people = connection.execute(
                "SELECT * FROM people WHERE status = 'shortlisted' ORDER BY updated_at DESC"
            ).fetchall()
        return render_template("shortlist.html", people=people)

    @app.post("/people/<int:person_id>/save")
    def save_person(person_id):
        note = request.form.get("personal_note", "").strip()
        if len(note) > MAX_NOTE_LENGTH:
            flash(f"Notes must be {MAX_NOTE_LENGTH} characters or fewer.", "error")
            return redirect(url_for("shortlist_page"))
        with db() as connection:
            cursor = connection.execute(
                """UPDATE people SET personal_note = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE id = ?""",
                (note, person_id),
            )
        if not cursor.rowcount:
            abort(404)
        flash("Personal note saved.", "success")
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
        status_filter = request.args.get("status", "")
        if status_filter and status_filter not in STATUSES:
            abort(400)
        with db() as connection:
            if status_filter:
                people = connection.execute(
                    "SELECT * FROM people WHERE status = ? ORDER BY updated_at DESC",
                    (status_filter,),
                ).fetchall()
            else:
                people = connection.execute(
                    "SELECT * FROM people ORDER BY updated_at DESC"
                ).fetchall()
            sent_today = connection.execute(
                "SELECT COUNT(*) FROM people WHERE date(sent_at) = date('now')"
            ).fetchone()[0]
        return render_template(
            "tracker.html",
            people=people,
            statuses=STATUSES,
            status_filter=status_filter,
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
            cursor = connection.execute(
                """UPDATE people SET status = ?, personal_note = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    sent_at = CASE
                        WHEN ? = 'sent' AND status != 'sent'
                        THEN strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                        ELSE sent_at
                    END
                    WHERE id = ?""",
                (status, note, status, person_id),
            )
        if not cursor.rowcount:
            abort(404)
        flash("Tracker entry updated.", "success")
        return redirect(url_for("tracker", status=status_filter_value(request.form)))

    @app.get("/export.csv")
    def export_csv():
        with db() as connection:
            people = connection.execute(
                "SELECT name, headline, snippet, url, college, source, personal_note, "
                "status, created_at, updated_at, sent_at FROM people ORDER BY created_at DESC"
            ).fetchall()
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(
            ["Name", "Headline", "Snippet", "Profile URL", "College", "Source",
             "Personal note", "Status", "Created at", "Updated at", "Sent at"]
        )
        for person in people:
            writer.writerow([csv_safe(person[key]) for key in person.keys()])
        return Response(
            "\ufeff" + output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=editorscout-tracker.csv"},
        )

    return app


def normalize_filters(form):
    per_page_raw = form.get("per_page", "10")
    per_page = int(per_page_raw) if per_page_raw in ("5", "10") else 10
    return {
        "role": form.get("role", ROLES[0]).strip()[:80],
        "city": form.get("city", "Coimbatore").strip()[:80],
        "college": (
            form.get("college_custom", "").strip()[:120]
            or form.get("college", "").strip()[:120]
        ),
        "exclude_words": form.get("exclude_words", DEFAULT_EXCLUDES).strip()[:200],
        "per_page": per_page,
    }


def build_query(filters):
    terms = ["site:linkedin.com/in", f'"{filters["role"]}"', filters["city"]]
    if filters["college"]:
        terms.append(f'"{filters["college"]}"')
    terms.extend(
        f"-{word.strip()}"
        for word in filters["exclude_words"].split(",")
        if word.strip()
    )
    return " ".join(terms)


def parse_page(value):
    try:
        return max(1, min(int(value or "1"), 10))
    except ValueError:
        return 1


def normalize_linkedin_url(url):
    if not isinstance(url, str):
        return None
    try:
        parsed = urlsplit(url.strip())
        host = parsed.hostname.lower() if parsed.hostname else ""
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.lower() not in ("http", "https")
        or parsed.username
        or parsed.password
        or (host != "linkedin.com" and not host.endswith(".linkedin.com"))
        or (port is not None and port not in (80, 443))
    ):
        return None
    path_parts = [unquote(part) for part in parsed.path.strip("/").split("/")]
    if len(path_parts) != 2 or path_parts[0].lower() != "in":
        return None
    slug = path_parts[1].strip()
    if not slug or "/" in slug or "\\" in slug:
        return None
    return f"https://www.linkedin.com/in/{quote(slug, safe='-._~')}"


def shape_results(items, college):
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
        shaped.append(
            {
                "name": name or "LinkedIn profile",
                "headline": headline,
                "snippet": str(item.get("snippet") or "").strip(),
                "url": url,
                "college": college,
            }
        )
    return shaped


def get_saved_urls(db_factory):
    with db_factory() as connection:
        return {
            normalized_url
            for row in connection.execute("SELECT url FROM people").fetchall()
            if (normalized_url := normalize_linkedin_url(row["url"])) is not None
        }


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


def status_filter_value(form):
    value = form.get("return_status", "")
    return value if value in STATUSES else ""


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
