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
├── schema.sql             # people, messages, searches tables
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

1. Choose a role, city, optional college, exclusion words, and page size. Results come from Serper.dev's public web search, with India selected as the search region.
2. Review every result yourself and open its public profile manually. Shortlist only profiles you want to keep. Regional LinkedIn profile URLs are normalized, and profiles already saved are hidden from later results.
3. On **Shortlist**, write one specific observation about the person's work. Generate an editable connection note (under 300 characters) or a longer follow-up draft. You can save or copy the text; nothing is sent.
4. On **Tracker**, record status changes and notes. Mark a person `sent` only after you manually send a message. The daily count warns above 10.
5. Use **Export CSV** to download the local tracker log.

Missing API keys, invalid keys, rate limits or exhausted credits, timeouts, connection failures, and unreadable API responses are shown in the interface.

## Tests

The tests use a temporary SQLite database and mocked search results; they do not make network requests:

```powershell
python -m unittest discover -s tests -v
```

## Data and privacy

- Search requests and your Serper API key are sent to Serper.dev. LinkedIn is never contacted by the app.
- Profile result details, your notes, saved drafts, and search history remain in the configured local SQLite database. The CSV export contains tracker information.
- `.env`, the SQLite database directory, and Python cache files are excluded by `.gitignore`.
- No account/authentication layer is included. Keep the server bound to localhost and the project files private.
