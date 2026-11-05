# EditorScout

EditorScout is a local-first helper for finding and organizing public search results for video editors and content creators in Coimbatore. It uses Google Programmable Search, keeps a private SQLite outreach log on your machine, and prepares editable message drafts.

**Review each profile yourself before contact.** The app does not log in to LinkedIn, request or store LinkedIn credentials, scrape profile pages, automate browser activity, or send messages. Copying a draft is the final outreach-related action it performs.

## Project structure

```text
EditorScout/
├── app.py                 # Flask routes and Google Search integration
├── config.py              # .env-based configuration
├── database.py            # SQLite connection and initialization
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

4. Create your local configuration file and edit it:

   ```powershell
   Copy-Item .env.example .env
   notepad .env
   ```

5. In Google Cloud, enable the **Custom Search JSON API** and create an API key. Create a Programmable Search Engine at <https://programmablesearchengine.google.com/> and copy its Search engine ID (the `cx` value) into `.env`:

   ```dotenv
   GOOGLE_API_KEY=your-google-api-key
   GOOGLE_CX=your-search-engine-id
   ```

   Configure the search engine to include the web or the sites you want to search. EditorScout adds `site:linkedin.com/in` to the query; Google results can vary based on your engine configuration, region, and API quota. Do not put credentials, tokens, or passwords in the app other than the Google Search API settings.

6. Optionally edit the draft personalization:

   ```dotenv
   INCUBRIX_ONE_LINER=Your accurate one-sentence IncuBrix description.
   INCUBRIX_OFFER=Your low-pressure offer or next step.
   ```

   Set a private random `SECRET_KEY` if you intend to keep a stable Flask session key. The server binds to `127.0.0.1` by default. Do not expose this single-user app to a public network.

7. Start the app:

   ```powershell
   python app.py
   ```

8. Open <http://127.0.0.1:5000>.

## Use

1. Pick a role, city, optional college, exclusion words, and page size. Google requests use a quoted role and add each exclusion as a negative query term. Pagination follows Google Custom Search's `start` index.
2. Review the displayed search result and open its public profile yourself. Select only profiles you want to retain; results already in your tracker are hidden on later searches.
3. On **Shortlist**, write one specific observation about the person's work. Generate a connection note (kept under 300 characters) or a longer follow-up draft. Edit, copy, and save it as needed. Nothing is sent.
4. On **Tracker**, record status changes and notes. Mark a person `sent` only after you manually send a message; the daily counter is based on those manual status changes and warns above 10.
5. Use **Export CSV** to download the local tracker log.

Google API quota/rate-limit errors, unavailable configuration, network errors, and invalid form values are surfaced in the interface. An empty result page can also mean profiles were already saved or the search returned no profile links.

## Tests

The tests use a temporary SQLite database and mocked Google API responses; they do not make network requests:

```powershell
python -m unittest discover -s tests -v
```

## Data and privacy

- Google Programmable Search receives the search query and API key from the server. LinkedIn is not contacted by the app.
- Profile result details, your notes, saved drafts, and search history remain in the configured local SQLite database. The CSV export contains tracker information.
- `.env`, the SQLite database directory, and Python cache files are excluded by `.gitignore`.
- No account/authentication layer is included. Keep the server bound to localhost and the project files private.
