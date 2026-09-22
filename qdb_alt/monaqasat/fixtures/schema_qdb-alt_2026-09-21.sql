-- Schema written by qdb-alt commit abd4b46 (21 Sept 2026), for migration tests.
CREATE TABLE IF NOT EXISTS raw_page (
    url         TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,          -- listing | detail
    tender_id   TEXT,
    page        INTEGER,
    status      INTEGER,
    fetched_at  TEXT NOT NULL,
    first_seen_at TEXT,
    content_sha256 TEXT,
    html        TEXT
);

CREATE TABLE IF NOT EXISTS tender (
    tender_id            TEXT PRIMARY KEY,
    tender_number        TEXT,
    subject              TEXT,
    ministry             TEXT,
    tender_type          TEXT,
    sector_type          TEXT,
    entity_tender_number TEXT,
    publish_date         TEXT,
    closing_date         TEXT,
    envelope_system      TEXT,
    document_value       REAL,
    tender_bond          REAL,
    technical_open_date  TEXT,
    financial_open_date  TEXT,
    awarded_date         TEXT,
    awarded_amount       REAL,
    family               TEXT,              -- tender | bid
    state                TEXT,              -- published|closed|technical|
                                            -- financial|awarded|cancelled|future
    brief_description    TEXT,
    targeted_tenderer    TEXT,
    service_delivery     TEXT,
    auction_type         TEXT,
    local_value_system   TEXT,              -- the ICV requirement, if any
    validity_period      TEXT,
    evaluation_basis     TEXT,
    evaluation_criteria  TEXT,
    final_insurance      TEXT,
    delivery_location    TEXT,
    contract_prep_period TEXT,
    contract_duration    TEXT,
    warranty_period      TEXT,
    maintenance_period   TEXT,
    disbursement_method  TEXT,
    source_url           TEXT,
    parsed_at            TEXT
);

CREATE TABLE IF NOT EXISTS tender_activity (
    tender_id     TEXT NOT NULL,
    activity_code TEXT NOT NULL,
    activity_name TEXT,
    PRIMARY KEY (tender_id, activity_code)
);

CREATE TABLE IF NOT EXISTS classified_company (
    profile_number  TEXT PRIMARY KEY,
    company_type    TEXT,
    name            TEXT,
    name_normalised TEXT,
    cr_number       TEXT,
    cr_root         TEXT,
    cr_secondary    TEXT,
    size            TEXT,               -- Small | Medium | Large
    evaluation      TEXT,               -- government entities evaluation
    classification  TEXT,
    certificate_end TEXT,               -- classification expiry
    page            INTEGER,
    parsed_at       TEXT
);

CREATE TABLE IF NOT EXISTS company_activity (
    profile_number TEXT NOT NULL,
    activity_code  TEXT NOT NULL,
    activity_name  TEXT,
    grade          TEXT,
    PRIMARY KEY (profile_number, activity_code)
);

CREATE TABLE IF NOT EXISTS company (
    tender_id         TEXT NOT NULL,
    role              TEXT NOT NULL,     -- awarded | bidder
    seq               INTEGER NOT NULL,
    name              TEXT,
    name_normalised   TEXT,
    cr_number         TEXT,
    cr_root           TEXT,
    cr_secondary      TEXT,
    value             REAL,
    financial_result  TEXT,
    local_value_ratio TEXT,
    notes             TEXT,
    stage             TEXT,              -- awarded | technical | financial | technical_financial
    PRIMARY KEY (tender_id, role, seq)
);

CREATE TABLE IF NOT EXISTS crawl_state (
    kind       TEXT NOT NULL,
    page       INTEGER NOT NULL,
    fetched_at TEXT,
    n_ids      INTEGER,
    PRIMARY KEY (kind, page)
);

CREATE TABLE IF NOT EXISTS match (
    customer_id   TEXT NOT NULL,
    tender_id     TEXT NOT NULL,
    role          TEXT NOT NULL,
    seq           INTEGER NOT NULL,
    method        TEXT NOT NULL,        -- cr | name_exact | name_fuzzy
    score         REAL,
    customer_name TEXT,
    matched_name  TEXT,
    matched_cr    TEXT,
    matched_at    TEXT,
    PRIMARY KEY (customer_id, tender_id, role, seq)
);

-- Every time a tender is seen in a section. Page numbers shift as new
-- tenders are published, so incremental crawling is keyed on tender id and
-- section, never on page number. This table is also the lifecycle record:
-- when a tender went from technical to financial to awarded.
CREATE TABLE IF NOT EXISTS sighting (
    tender_id  TEXT NOT NULL,
    kind       TEXT NOT NULL,
    family     TEXT,
    state      TEXT,
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    PRIMARY KEY (tender_id, kind)
);

-- Which detail pages we hold, and the tender's state when we fetched them.
-- A companies page fetched while a tender was only technically opened has
-- no winners on it; when the tender is later awarded it must be fetched
-- again. Keyed on URL alone, that refetch would never happen.
CREATE TABLE IF NOT EXISTS detail_fetch (
    tender_id  TEXT NOT NULL,
    page_type  TEXT NOT NULL,          -- tender_details | companies
    state      TEXT,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (tender_id, page_type)
);

-- Pages that failed. Skipped so one slow page cannot sink a long run, and
-- retried automatically on the next one.
CREATE TABLE IF NOT EXISTS failed_page (
    kind      TEXT NOT NULL,
    ref       TEXT NOT NULL,           -- page number, or tender id
    url       TEXT,
    error     TEXT,
    attempts  INTEGER NOT NULL DEFAULT 1,
    failed_at TEXT NOT NULL,
    PRIMARY KEY (kind, ref)
);

-- Per-section progress, so an interrupted first load is finished by later
-- update runs rather than silently abandoned.
CREATE TABLE IF NOT EXISTS section_state (
    kind              TEXT PRIMARY KEY,
    reached_end       INTEGER NOT NULL DEFAULT 0,   -- read to the last page
    backfill_complete INTEGER NOT NULL DEFAULT 0,   -- ... and no holes left
    deepest_page      INTEGER NOT NULL DEFAULT 0,
    last_page_seen    INTEGER,
    last_update       TEXT
);

CREATE TABLE IF NOT EXISTS run (
    run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    command       TEXT,
    target        TEXT,
    mode          TEXT,
    started_at    TEXT,
    finished_at   TEXT,
    listing_pages INTEGER NOT NULL DEFAULT 0,
    new_tenders   INTEGER NOT NULL DEFAULT 0,
    state_changes INTEGER NOT NULL DEFAULT 0,
    details       INTEGER NOT NULL DEFAULT 0,
    companies     INTEGER NOT NULL DEFAULT 0,
    failures      INTEGER NOT NULL DEFAULT 0,
    note          TEXT
);

CREATE TABLE IF NOT EXISTS feature_cr_month (
    cr_root                TEXT NOT NULL,
    as_of_month            TEXT NOT NULL,   -- ISO month-end
    awards_12m_count       INTEGER,
    awards_12m_value       REAL,
    awards_36m_count       INTEGER,
    awards_36m_value       REAL,
    awards_life_value      REAL,
    largest_award_12m      REAL,
    months_since_last_award INTEGER,
    bids_12m_count         INTEGER,
    wins_12m_count         INTEGER,
    win_rate_12m           REAL,
    distinct_buyers_12m    INTEGER,
    top_buyer_share_12m    REAL,
    months_since_first_seen INTEGER,
    classified_size        TEXT,
    classified_evaluation  TEXT,
    certificate_end        TEXT,
    feature_version        TEXT,
    built_at               TEXT,
    PRIMARY KEY (cr_root, as_of_month)
);

CREATE INDEX IF NOT EXISTS ix_company_cr   ON company(cr_root);
CREATE INDEX IF NOT EXISTS ix_company_name ON company(name_normalised);
CREATE INDEX IF NOT EXISTS ix_tender_award ON tender(awarded_date);
CREATE INDEX IF NOT EXISTS ix_tender_state ON tender(family, state);
CREATE INDEX IF NOT EXISTS ix_cc_cr        ON classified_company(cr_root);
CREATE INDEX IF NOT EXISTS ix_cc_name      ON classified_company(name_normalised);
