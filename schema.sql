CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    headline TEXT NOT NULL DEFAULT '',
    snippet TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL UNIQUE,
    college TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'LinkedIn',
    personal_note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'new'
        CHECK (status IN ('new', 'shortlisted', 'sent', 'connected', 'replied', 'agreed', 'rejected')),
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
    verified_note TEXT NOT NULL DEFAULT '',
    instagram_handle TEXT NOT NULL DEFAULT '',
    referred_by TEXT NOT NULL DEFAULT '',
    touch_count INTEGER NOT NULL DEFAULT 0,
    follow_up_date TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS person_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    platform TEXT NOT NULL CHECK (platform IN ('linkedin', 'instagram', 'youtube')),
    kind TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    snippet TEXT NOT NULL DEFAULT '',
    handle TEXT NOT NULL DEFAULT '',
    confidence TEXT NOT NULL DEFAULT 'unsure'
        CHECK (confidence IN ('likely', 'possible', 'unsure')),
    confirmed INTEGER NOT NULL DEFAULT 0 CHECK (confirmed IN (0, 1)),
    dismissed INTEGER NOT NULL DEFAULT 0 CHECK (dismissed IN (0, 1)),
    found_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (person_id, url)
);

CREATE INDEX IF NOT EXISTS idx_person_links_person ON person_links(person_id);

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
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    is_edited INTEGER NOT NULL DEFAULT 0 CHECK (is_edited IN (0, 1))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS sent_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('connection', 'follow_up')),
    text TEXT NOT NULL,
    sent_at TEXT NOT NULL
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

CREATE TABLE IF NOT EXISTS search_pool (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    search_key TEXT NOT NULL,
    url TEXT NOT NULL,
    profile_json TEXT NOT NULL,
    found_at TEXT NOT NULL,
    UNIQUE (search_key, url)
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
CREATE INDEX IF NOT EXISTS idx_sent_messages_person_id ON sent_messages(person_id);
CREATE INDEX IF NOT EXISTS idx_search_pool_key ON search_pool(search_key);
