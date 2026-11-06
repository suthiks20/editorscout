# EditorScout

EditorScout is a local-first helper for finding and organizing public search results for video editors and content creators in Coimbatore. It searches through Serper.dev and keeps a private SQLite outreach log on your machine.

**LinkedIn is never contacted by the app. No LinkedIn credentials are requested or stored.** Profiles must be reviewed manually before contact; sending messages and connection requests stays manual. The tracker warns when you mark more than about 10 messages sent in a day.

## Project structure

```text
EditorScout/
├── app.py                 # Flask routes and search-result handling
├── config.py              # .env-based configuration
├── database.py            # SQLite connection and initialization
├── search_provider.py     # Serper.dev API client
├── platform_search.py    # Platform query building and URL classification
├── messaging.py          # Safe placeholder rendering and name extraction
├── profile_urls.py        # LinkedIn profile URL and person-input normalization
├── schema.sql             # People, batches, status history, and search cache
├── export_excel.py        # In-memory Excel workbook generation
├── requirements.txt
├── .env.example
├── templates/             # Search, shortlist, draft, and tracker pages
├── static/                # Responsive CSS and small UI helpers
└── tests/                 # Offline unit and route tests
```

The SQLite database is initialized automatically on startup at `instance/editorscout.sqlite3` by default. It contains profile details and notes, so keep it private and back it up only to a location you trust.

## Setup (Windows PowerShell)

1. Install Python 3.10 or later.
2. From the project folder, create and activate a virtual environment:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. Install dependencies:

   ```powershell
   python -m pip install -r requirements.txt
   ```

   This installs `openpyxl` for generating `.xlsx` exports.

4. Sign up at <https://serper.dev/> and create an API key.
5. Create your local configuration file and edit it:

   ```powershell
   Copy-Item .env.example .env
   notepad .env
   ```

   Set your Serper key:

   ```dotenv
   SERPER_API_KEY=your-serper-api-key
   ```

   Optionally personalize the drafts using `INCUBRIX_ONE_LINER` and `INCUBRIX_OFFER`. Keep `.env` private; never put API keys in source code or share them.

6. Start the app and open <http://127.0.0.1:5000>:

   ```powershell
   python app.py
   ```

The app binds to `127.0.0.1` by default. It has no account/authentication layer; do not expose it to a public network.

## Use

1. In **Browse by role**, choose a role and city, then optionally filter by organisation, current/past status, and college or school batch year. **Find a specific person** accepts a name, LinkedIn profile URL, or LinkedIn ID, with optional organisation, location, role, and status hints. Select LinkedIn, Instagram, and/or YouTube; the displayed estimate is one Serper credit per selected platform. YouTube searches use Serper's video results and may use one extra credit for a web-search fallback when no video results are returned. Each request is limited to 10 results per page. Identical platform searches are cached for 10 minutes; use **Refresh** to bypass the cache.
2. Results come only from Serper.dev's public search APIs, with India selected as the search region. Instagram and YouTube suggestions are grouped by platform, labeled unverified, and must be confirmed manually. Only results indexed by Google and surfaced by Serper can appear; a missing result does not mean a profile or link does not exist. Search results cannot confirm identity or dates. Open a result manually and check the photo, name, and organisation before confirming or shortlisting.
3. **Shortlist** shows only the current batch. Shortlisting creates the first batch automatically; **Start new batch** opens a fresh one without deleting the prior history. Removed profiles stay in the tracker and are marked as removed. **Find more links** searches only Instagram and YouTube for that saved person. Review each unverified suggestion yourself and select **This is them** or **Not them**; the app never confirms a link automatically. You may also enter an Instagram `@handle` or profile URL manually. A confirmed Instagram profile provides an `ig.me` link for you to open; the app does not open or message it.
4. On **Shortlist**, save a common connection note and follow-up template. Insert `{first_name}`, `{name}`, `{organisation}`, `{role}`, `{specific_note}`, `{my_name}`, and `{link}` with the cheat-sheet buttons. Templates, your name, and your link are saved locally. Generate for all people or only checked cards; edited drafts and people already contacted are preserved unless you explicitly choose the corresponding override. Follow-up drafts are only generated for people whose status is connected, replied, or agreed.
5. Edit each person’s draft, save it, or copy it to send manually. Connection notes are limited to 299 characters for Copy; you can shorten an over-limit note or explicitly select **Copy anyway**. Queue mode displays one shortlist card at a time and remembers its position. The app never sends text or opens a messaging session.
6. After you manually send a message, click **Mark as sent** and choose a follow-up date (defaults to four days later). The app records the exact text you confirm as sent, increments the touch count, updates the status/history, and shows the daily count in India Standard Time. If that message kind was already recorded as sent, confirm before recording it again. The 10-per-day guideline is a warning, not a block.
7. **Tracker** shows people across all batches. Filter by outreach status (including connected), verified-current status, batch, or follow-ups due today/overdue. Record the source, referral, and status changes; use **Mark connected** after you manually confirm a connection.
8. Use **Export CSV** or **Export Excel** to download the filtered tracker log. Both exports share the tracker filters, including due follow-ups. They include source/referral, touch count, follow-up date, and last sent text; Excel also includes a formula-driven Summary with connected counts. Formula-like values are escaped in both formats.

Missing API keys, invalid keys, rate limits or exhausted credits, timeouts, connection failures, and unreadable API responses are shown in the interface.

## Tests

The tests use a temporary SQLite database and mocked search results; they do not make network requests:

```powershell
python -m unittest discover -s tests -v
```

## Data and privacy

- Search requests and your Serper API key are sent to Serper.dev. The app only uses Serper search metadata (title, link, snippet, and video date/channel); it never fetches, scrapes, or logs in to LinkedIn, Instagram, or YouTube pages.
- Profile result details, batches, your notes, common templates, saved drafts, exact sent-message history, status history, search cache, and search history remain in the configured local SQLite database. Safe migrations preserve existing records and rebuild the people table transactionally when adding the connected status.
- No LinkedIn, Instagram, or YouTube credentials, page-fetching, scraping, browser automation, auto-sending, or automated connection requests are used. You manually review profiles and manually send every message.
- Keep outreach individualized and modest; the app's daily guideline is about 10 manually sent messages.
- Excel exports are generated in memory using `openpyxl`; formula-like text is escaped in both export formats.
- `.env`, the SQLite database directory, and Python cache files are excluded by `.gitignore`.
- No account/authentication layer is included. Keep the server bound to localhost and the project files private.
