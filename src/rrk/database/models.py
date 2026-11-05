"""Table definitions."""

# One row per Advertisement, columns in the dataclass's field order.
SCHEMA = """
CREATE TABLE IF NOT EXISTS ads (
    tracking_code TEXT NOT NULL,
    title TEXT NOT NULL,
    letter_date TEXT,
    page_number TEXT,
    newspaper_number TEXT,
    newspaper_date TEXT,
    indicator_number TEXT,
    company_national_id TEXT,
    summary TEXT,
    detail_code TEXT PRIMARY KEY,  -- unique, unlike tracking_code
    status TEXT,
    newspaper_type_code TEXT,
    newspaper_type TEXT,
    pay_date TEXT,
    full_title TEXT,
    ad_type TEXT NOT NULL,         -- P199_NOE_AGAHI it was listed under
    source TEXT,                   -- --debug runs: the saved response it came from
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ad_types (
    code TEXT PRIMARY KEY,         -- P199_NOE_AGAHI value
    label TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL        -- older than the last run: gone from the dropdown
);

CREATE TABLE IF NOT EXISTS details (
    detail_code TEXT PRIMARY KEY REFERENCES ads (detail_code),
    full_text TEXT,                -- the AdDetail fields, in its field order; full_text: database.texts (read it as unzip(full_text))
    company_name TEXT,
    registration_number TEXT,
    publication_count TEXT,
    issuer TEXT,
    fetched_at TEXT,               -- NULL until a fetch succeeds
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);

-- The overview counts details per type from the index alone (fetched_at sits after full_text);
-- the GUI's number search finds registration numbers and tracking codes, also of ads without a detail page.
CREATE INDEX IF NOT EXISTS details_fetched ON details (detail_code, fetched_at);
CREATE INDEX IF NOT EXISTS details_registration ON details (registration_number);
CREATE INDEX IF NOT EXISTS ads_tracking ON ads (tracking_code);

-- Date windows a stopped run hadn't finished listing, for --resume.
-- database.texts: the zlib dictionaries details.full_text is packed with (python -m database.compact)
CREATE TABLE IF NOT EXISTS text_dicts (
    id INTEGER PRIMARY KEY CHECK (id BETWEEN 1 AND 255),
    dict BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS windows_left (
    ad_type TEXT NOT NULL,
    spec TEXT NOT NULL             -- a scraper.windows.Window as JSON
);
"""

# When an ad happened: its letter date, unless that is the 1300/02/01 placeholder.
DAY = "CASE WHEN ads.letter_date >= '1301' THEN ads.letter_date ELSE ads.newspaper_date END"

# Which notice an ad prints: one notice goes to several newspaper issues, an ad each, with the
# same tracking code and letter number.  Older ads reuse tracking codes ("-", "333", even 10
# digits a few years apart) for unrelated notices, so the letter number is part of the key, and
# an ad missing either number is a notice of its own.
NOTICE = ("CASE WHEN ads.tracking_code GLOB '*[0-9]*' AND ads.indicator_number GLOB '*[0-9]*'"
          " THEN ads.tracking_code || '|' || ads.indicator_number ELSE '#' || ads.detail_code END")

# The explorer's own database (connection.connect_explorer): what it read from
# details.full_text (parsing.entities), rebuilt from it at any time, so the
# scraper's database is only ever read by the GUI.
EXPLORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS mentions (
    detail_code TEXT NOT NULL,
    kind TEXT NOT NULL,            -- person | company | address
    entity_id TEXT NOT NULL,       -- national code | national id | postal code; a person named without
                                   -- a valid code: the code the same company's ads give that name, else
                                   -- "~<company id>~<name without spaces>" (gui.explore.resolve_people)
    written TEXT,                  -- the number as the text writes it; NULL: named without one
    valid INTEGER,                 -- its check digit holds (1) or not (0); NULL: no check (postal codes)
    name TEXT,                     -- as written, normalized
    role TEXT,                     -- parsing.entities.ROLES keys, comma separated
    represents TEXT,               -- national id of the company a person sits for
    subject INTEGER NOT NULL,      -- 1: the company the ad is about
    text_start INTEGER,            -- offsets in full_text; NULL: the subject isn't named in it
    text_end INTEGER,
    stake INTEGER                  -- a partner's share capital in rials it gives (parsing.entities._stake)
);
CREATE INDEX IF NOT EXISTS mentions_entity ON mentions (entity_id);
CREATE INDEX IF NOT EXISTS mentions_ad ON mentions (detail_code);

CREATE TABLE IF NOT EXISTS indexed (
    detail_code TEXT PRIMARY KEY,
    version INTEGER NOT NULL,      -- parsing.entities.VERSION it was read with
    notice TEXT,                   -- NOTICE: one notice printed in several issues
    day TEXT,                      -- DAY
    subject TEXT,                  -- national id of the company it is about
    issuer TEXT,                   -- details.issuer: the registration office
    text INTEGER,                  -- details rowid: its row in ad_text
    events TEXT,                   -- parsing.events.notice_events, ",a,b,": instr(events, ',board,')
    capital_from INTEGER,          -- parsing.events.notice_capital, in rials
    capital_to INTEGER,
    office TEXT                    -- models.issuer.office: the issuer's city, else "~<kind>"
);
CREATE INDEX IF NOT EXISTS indexed_notice ON indexed (notice);

-- One row per notice, however many issues printed it: what the Notices page pages through
-- (gui.explore.build_notices, with the graph).
CREATE TABLE IF NOT EXISTS notices (
    notice TEXT NOT NULL,          -- NOTICE
    code TEXT NOT NULL,            -- the ad shown: its first printing with a text read
    text INTEGER,                  -- its text's row in ad_text
    day TEXT,                      -- the first DAY of its printings
    types TEXT NOT NULL,           -- the ad types it was listed under, comma separated
    issuer TEXT,
    issues INTEGER NOT NULL,       -- printings
    events TEXT,                   -- as in indexed
    capital_from INTEGER,
    capital_to INTEGER,
    office TEXT,
    roles TEXT,                    -- the ROLES keys it names someone in, ",a,b,": instr(roles, ',ceo,')
    subject TEXT                   -- national id of the company it is about (index notices_subject: build_notices)
);
CREATE INDEX IF NOT EXISTS notices_day ON notices (day, code);
CREATE INDEX IF NOT EXISTS notices_notice ON notices (notice);  -- the notices naming one node

-- The graph of who is linked to which company (gui.explore.build_graph), rebuilt from
-- mentions; a link counts each notice once, however many issues printed it.
CREATE TABLE IF NOT EXISTS g_nodes (
    id INTEGER PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,      -- "<p|c|a>:<entity_id>"
    kind TEXT NOT NULL,
    name TEXT,                     -- its company page's name, else the one written most
    names TEXT,                    -- every spelling, joined by "|" (for search)
    notices INTEGER NOT NULL,      -- naming it
    about INTEGER NOT NULL,        -- about it (companies)
    links INTEGER NOT NULL DEFAULT 0,
    current INTEGER NOT NULL DEFAULT 0,  -- links no notice ended
    first TEXT,                    -- the first and latest day of the notices naming it
    last TEXT,
    roles TEXT                     -- the ROLES keys its notices give it elsewhere, ",a,b,": each combination once
);
CREATE TABLE IF NOT EXISTS g_edges (
    a INTEGER NOT NULL,            -- g_nodes ids, a < b
    b INTEGER NOT NULL,
    dirs INTEGER NOT NULL,         -- 1: a is named in b's notices (sits on it), 2: b in a's
    notices INTEGER NOT NULL,
    roles TEXT,                    -- ROLES keys, ",a,b,a,": once per notice
    first TEXT,
    last TEXT,
    ended INTEGER NOT NULL,        -- its latest notice records them leaving
    service INTEGER NOT NULL,      -- only inspectors' roles: auditors link unrelated companies
    kind TEXT                      -- what it links to a company: person | company | address
);
CREATE INDEX IF NOT EXISTS g_edges_a ON g_edges (a);
CREATE INDEX IF NOT EXISTS g_edges_b ON g_edges (b);
CREATE TABLE IF NOT EXISTS g_meta (
    built TEXT NOT NULL,
    mentions INTEGER NOT NULL,     -- max(mentions.rowid) it was built from
    leads TEXT NOT NULL            -- JSON
);

-- Full-text search over details.full_text (normalized), keyed on details.rowid
-- (contentless_delete needs SQLite 3.43).
CREATE VIRTUAL TABLE IF NOT EXISTS ad_text USING fts5(text, content='', contentless_delete=1);
"""
