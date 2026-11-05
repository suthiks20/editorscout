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

1. In **Browse by role**, choose a role and city, then optionally filter by organisation, current/past status, and college or school batch year. **Find a specific person** accepts a name, LinkedIn profile URL, or LinkedIn ID, with optional organisation, location, role, and status hints. Each request is limited to 10 results per page. Identical searches are cached for 10 minutes; use **Refresh** to bypass the cache.
2. Results come from Serper.dev's public web search, with India selected as the search region. Search results cannot confirm identity or dates. Open profiles manually and check the photo, name, and organisation before shortlisting. Affiliation badges are unverified hints.
3. **Shortlist** shows only the current batch. Shortlisting creates the first batch automatically; **Start new batch** opens a fresh one without deleting the prior history. Removed profiles stay in the tracker and are marked as removed. Write one specific observation about each person's work and record what you verified after opening their profile.
4. Generate an editable connection note (under 300 characters) or longer follow-up pitch. Unverified profiles receive a reminder. If a person is already marked sent, replied, or agreed, confirm before drafting another connection note. You can edit, save, or copy text; nothing is sent.
5. **Tracker** shows people across all batches. Filter by outreach status, verified-current status, or batch. Record status changes and notes yourself; the latest change is shown in the tracker. Mark a person `sent` only after manually sending a message. The daily count uses India Standard Time and warns after 10.
6. Use **Export CSV** or **Export Excel** to download the filtered tracker log. Both exports share the tracker filters, including batch. Excel includes an Outreach sheet with editable status dropdowns and a formula-driven Summary sheet. Formula-like values are escaped in both formats.

Missing API keys, invalid keys, rate limits or exhausted credits, timeouts, connection failures, and unreadable API responses are shown in the interface.

## Tests

The tests use a temporary SQLite database and mocked search results; they do not make network requests:

```powershell
python -m unittest discover -s tests -v
```

## Data and privacy

- Search requests and your Serper API key are sent to Serper.dev. LinkedIn is never contacted by the app.
- Profile result details, batches, your notes, saved drafts, status history, search cache, and search history remain in the configured local SQLite database. Database migrations add new fields and tables without deleting existing outreach.
- No LinkedIn credentials, scraping, browser automation, auto-sending, or automated connection requests are used. You manually review profiles and manually send every message.
- Keep outreach individualized and modest; the app's daily guideline is about 10 manually sent messages.
- Excel exports are generated in memory using `openpyxl`; formula-like text is escaped in both export formats.
- `.env`, the SQLite database directory, and Python cache files are excluded by `.gitignore`.
- No account/authentication layer is included. Keep the server bound to localhost and the project files private.
