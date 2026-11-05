CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    headline TEXT NOT NULL DEFAULT '',
    snippet TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL UNIQUE,
    college TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'Serper.dev',
    personal_note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'new'
        CHECK (status IN ('new', 'shortlisted', 'sent', 'replied', 'agreed', 'rejected')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    sent_at TEXT,
    batch_id INTEGER REFERENCES batches(id),
    shortlisted_at TEXT,
    status_changed_at TEXT,
    removed_from_shortlist INTEGER NOT NULL DEFAULT 0,
    org_type TEXT NOT NULL DEFAULT '',
    org_name TEXT NOT NULL DEFAULT '',
    status_hint TEXT NOT NULL DEFAULT 'unclear',
    years_text TEXT NOT NULL DEFAULT '',
    verified_status TEXT NOT NULL DEFAULT 'unverified',
    verified_note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    is_current INTEGER NOT NULL DEFAULT 0 CHECK (is_current IN (0, 1))
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    draft_text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    old_status TEXT,
    new_status TEXT NOT NULL,
    changed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS search_cache (
    query TEXT NOT NULL,
    page INTEGER NOT NULL,
    per_page INTEGER NOT NULL,
    results_json TEXT NOT NULL,
    cached_at REAL NOT NULL,
    PRIMARY KEY (query, page, per_page)
);

CREATE TABLE IF NOT EXISTS searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,
    filters_json TEXT NOT NULL,
    result_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_people_status ON people(status);
CREATE INDEX IF NOT EXISTS idx_people_sent_at ON people(sent_at);
CREATE INDEX IF NOT EXISTS idx_messages_person_id ON messages(person_id);
