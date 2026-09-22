"""SQLite store.

One file holds everything: the raw HTML exactly as fetched, the parsed rows,
the crawl position and the match results.

Raw HTML is kept deliberately. Parsers get things wrong and sites change; if
the pages are on disk you re-parse in seconds instead of re-crawling 1,200
pages and annoying a government web server. It also means the fetch date of
every field is provable, which is what makes this usable as evidence.

How a tender's state is kept (see FIXES.md, "state model"):

  sighting   one row per (tender, section) it has ever been listed in, with
             first_seen / last_seen, and gone_at once a full read of that
             section no longer lists it (two reads in a row, so a page that
             shifted mid-read cannot fake a departure).
  state      the most advanced section the tender is *currently* listed in.
             Nothing is ever deleted: a tender that moved from Available to
             Closed keeps its Available sighting, marked gone.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from .fingerprint import canonical_html_sha256

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_page (
    url         TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,          -- listing | detail | tender_details | classified
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
    parsed_at       TEXT,
    last_pass       INTEGER,            -- register pass it was last seen in
    missed          INTEGER NOT NULL DEFAULT 0,
    delisted_at     TEXT                -- set after two full passes without it
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
    stage             TEXT,              -- the page table the row came from:
                                         -- awarded | technical | financial |
                                         -- technical_financial | unknown
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
    method        TEXT NOT NULL,        -- cr | name_exact | name_fuzzy | name_translit
    score         REAL,
    customer_name TEXT,
    matched_name  TEXT,
    matched_cr    TEXT,
    matched_at    TEXT,
    PRIMARY KEY (customer_id, tender_id, role, seq)
);

-- Every section a tender has been listed in. Page numbers shift as new
-- tenders are published, so incremental crawling is keyed on tender id and
-- section, never on page number. This table is also the lifecycle record:
-- when a tender went from technical to financial to awarded, and when it
-- stopped being listed in each.
CREATE TABLE IF NOT EXISTS sighting (
    tender_id  TEXT NOT NULL,
    kind       TEXT NOT NULL,
    family     TEXT,
    state      TEXT,
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    last_run   INTEGER,                -- run that last listed it here
    missed     INTEGER NOT NULL DEFAULT 0,  -- full reads in a row without it
    gone_at    TEXT,                   -- no longer listed in this section
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
-- retried automatically later. kind is a section for listing pages,
-- 'companies' / 'tender_details' for detail pages (ref "id@state" for
-- companies), and 'missing:<page type>' for pages the site answers with its
-- home page -- those are not network failures and are retried weekly, three
-- times, without tripping the consecutive-failure stop.
CREATE TABLE IF NOT EXISTS failed_page (
    kind      TEXT NOT NULL,
    ref       TEXT NOT NULL,
    url       TEXT,
    error     TEXT,
    attempts  INTEGER NOT NULL DEFAULT 1,
    failed_at TEXT NOT NULL,
    PRIMARY KEY (kind, ref)
);

-- Per-section progress.
CREATE TABLE IF NOT EXISTS section_state (
    kind              TEXT PRIMARY KEY,
    reached_end       INTEGER NOT NULL DEFAULT 0,   -- read to the last page
    backfill_complete INTEGER NOT NULL DEFAULT 0,   -- ... and no holes left
    deepest_page      INTEGER NOT NULL DEFAULT 0,
    last_page_seen    INTEGER,
    last_update       TEXT,
    last_full_pass    TEXT,              -- last time every page was read
    roll_next         INTEGER,           -- next page of a rolling pass
    pass_id           INTEGER NOT NULL DEFAULT 0   -- register passes
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
    bids_12m_count         INTEGER,         -- tenders it appeared in
    decided_12m_count      INTEGER,         -- ... whose winners are known
    wins_12m_count         INTEGER,
    win_rate_12m           REAL,            -- wins / decided, >= 3 decided
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
"""

# Indexes on columns that older databases only get from _migrate().
LATE_INDEXES = """
CREATE INDEX IF NOT EXISTS ix_sighting_kind ON sighting(kind, gone_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# How far along its life a tender is. Cancelled is terminal.
STATE_RANK = {
    None: -1, "future": 0, "published": 1, "closed": 2, "technical": 3,
    "financial": 4, "awarded": 5, "cancelled": 6,
}
# States a tender can genuinely return to: a closing date extended after
# closing puts a tender back in Available. Once opened, it never goes back.
PRE_OPENING = {"future", "published", "closed"}
COMPANY_STATES = ("technical", "financial", "awarded")

GONE_AFTER = 2              # full reads in a row without a tender
DETAIL_MAX_ATTEMPTS = 5     # network failures before a page is left alone
MISSING_MAX_ATTEMPTS = 3    # home-page answers before a page is left alone
MISSING_RETRY_DAYS = 7


def state_rank(state: str | None) -> int:
    return STATE_RANK.get(state, -1)


def companies_ref(tender_id: str, state: str | None) -> str:
    """Failure key for a companies page: per tender *and* state, so a page
    that kept failing before the award gets a fresh start after it."""
    return f"{tender_id}@{state or ''}"


def pack_database(src: str, dest: str) -> int:
    """Copy the database without stored HTML.

    raw_page is almost all of the file size (the pages themselves). Crawl
    progress, tenders and companies live in the other tables, so a laptop
    can resume from the packed copy. Returns the new file size in bytes.
    """
    src_path = Path(src).resolve()
    dest_path = Path(dest).resolve()
    if src_path == dest_path:
        raise ValueError("pack destination must be a different file")
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists():
        dest_path.unlink()
    src_conn = sqlite3.connect(f"file:{src_path.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(dest_path)
    try:
        dst.execute("PRAGMA journal_mode=OFF")
        dst.execute("PRAGMA synchronous=OFF")
        objects = src_conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL "
            "AND name NOT LIKE 'sqlite_%' AND type IN ('table','index') "
            "ORDER BY type DESC"
        ).fetchall()
        for _name, sql in objects:
            dst.execute(sql)
        tables = [
            name for name, sql in objects
            if sql.lstrip().upper().startswith("CREATE TABLE")
            and name != "raw_page"
        ]
        dst.execute("ATTACH DATABASE ? AS src", (str(src_path),))
        for name in tables:
            dst.execute(f'INSERT INTO "{name}" SELECT * FROM src."{name}"')
        version = dst.execute("PRAGMA src.user_version").fetchone()[0]
        dst.execute(f"PRAGMA user_version = {version}")
        dst.commit()
        dst.execute("DETACH DATABASE src")
        dst.execute("VACUUM")
    finally:
        dst.close()
        src_conn.close()
    return dest_path.stat().st_size


class Store:
    def __init__(self, path: str):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self._migrate()
        self.conn.executescript(LATE_INDEXES)
        self.conn.commit()

    # -- migration --------------------------------------------------------
    def _cols(self, table: str) -> set[str]:
        return {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}

    def _add(self, table: str, column: str, decl: str) -> bool:
        if column in self._cols(table):
            return False
        self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        return True

    def _migrate(self) -> None:
        """Bring a database from any earlier version up to date.

        Every step checks before it acts, so running it on a current
        database does nothing.
        """
        c = self.conn
        if self._add("section_state", "reached_end",
                     "INTEGER NOT NULL DEFAULT 0"):
            c.execute("UPDATE section_state SET reached_end=backfill_complete")
        self._add("section_state", "last_full_pass", "TEXT")
        self._add("section_state", "roll_next", "INTEGER")
        self._add("section_state", "pass_id", "INTEGER NOT NULL DEFAULT 0")

        if self._add("raw_page", "first_seen_at", "TEXT"):
            c.execute("UPDATE raw_page SET first_seen_at=fetched_at"
                      " WHERE first_seen_at IS NULL")
        self._add("raw_page", "content_sha256", "TEXT")
        self._add("tender", "financial_open_date", "TEXT")
        self._add("company", "stage", "TEXT")

        self._add("sighting", "last_run", "INTEGER")
        self._add("sighting", "missed", "INTEGER NOT NULL DEFAULT 0")
        self._add("sighting", "gone_at", "TEXT")

        self._add("classified_company", "last_pass", "INTEGER")
        self._add("classified_company", "missed", "INTEGER NOT NULL DEFAULT 0")
        self._add("classified_company", "delisted_at", "TEXT")

        self._add("feature_cr_month", "decided_12m_count", "INTEGER")

        # Sightings and the fetch log, rebuilt from what is on disk for
        # databases that predate them, so the first run does not refetch.
        if c.execute("SELECT COUNT(*) FROM sighting").fetchone()[0] == 0:
            now = _now()
            for row in c.execute("SELECT tender_id, family, state, parsed_at"
                                 " FROM tender WHERE state IS NOT NULL"
                                 ).fetchall():
                kind = _kind_for(row["family"], row["state"])
                if kind:
                    c.execute(
                        "INSERT OR IGNORE INTO sighting (tender_id, kind,"
                        " family, state, first_seen, last_seen)"
                        " VALUES (?,?,?,?,?,?)",
                        (row["tender_id"], kind, row["family"], row["state"],
                         row["parsed_at"] or now, row["parsed_at"] or now))
        if c.execute("SELECT COUNT(*) FROM detail_fetch").fetchone()[0] == 0:
            c.execute(
                "INSERT OR IGNORE INTO detail_fetch"
                " (tender_id, page_type, state, fetched_at)"
                " SELECT r.tender_id,"
                "        CASE r.kind WHEN 'detail' THEN 'companies'"
                "                    ELSE 'tender_details' END,"
                "        t.state, r.fetched_at"
                " FROM raw_page r LEFT JOIN tender t USING (tender_id)"
                " WHERE r.kind IN ('detail', 'tender_details')"
                "   AND r.tender_id IS NOT NULL")

        version = c.execute("PRAGMA user_version").fetchone()[0]
        if version < 2:
            self._migrate_to_2()
        c.commit()

    def _migrate_to_2(self) -> None:
        """Repairs for databases written before the Sept 2026 fixes."""
        from .normalize import cr_root, normalize_name
        from .parse import LISTING_KINDS
        c = self.conn

        # 1. CR roots and names were normalised by functions that turned
        #    "29,309" into "29" and Arabic names into "". Recompute them.
        for table, key in (("company", ("tender_id", "role", "seq")),
                           ("classified_company", ("profile_number",))):
            rows = c.execute(
                f"SELECT {', '.join(key)}, name, cr_number FROM {table}"
            ).fetchall()
            c.executemany(
                f"UPDATE {table} SET cr_root=?, name_normalised=?"
                f" WHERE {' AND '.join(f'{k}=?' for k in key)}",
                [(cr_root(r["cr_number"]), normalize_name(r["name"]),
                  *(r[k] for k in key)) for r in rows])

        # 2. The first stage migration copied role into stage, leaving the
        #    value 'bidder' (and databases from before it have no stage at
        #    all). For bidders the table it came from is not known; `parse`
        #    recovers it from the stored HTML.
        c.execute("UPDATE company SET stage = CASE role WHEN 'awarded'"
                  " THEN 'awarded' ELSE 'unknown' END"
                  " WHERE stage IS NULL OR stage='bidder'")

        # 3. Sightings backfilled from state names ('published', or a bid
        #    'awarded') used kinds that do not exist. Re-key them.
        for row in c.execute("SELECT rowid, * FROM sighting").fetchall():
            if row["kind"] in LISTING_KINDS and (
                    LISTING_KINDS[row["kind"]]["family"] == row["family"]
                    or row["family"] is None):
                continue
            kind = _kind_for(row["family"], row["state"])
            if kind and kind != row["kind"]:
                c.execute("INSERT OR IGNORE INTO sighting (tender_id, kind,"
                          " family, state, first_seen, last_seen)"
                          " VALUES (?,?,?,?,?,?)",
                          (row["tender_id"], kind, row["family"],
                           row["state"], row["first_seen"], row["last_seen"]))
                c.execute("DELETE FROM sighting WHERE rowid=?", (row["rowid"],))

        # 4. Detail failures were keyed per section ("awarded:companies").
        #    Detail fetching is now one queue; drop the old keys and let
        #    those pages be tried again under the new ones.
        c.execute("DELETE FROM failed_page WHERE kind LIKE '%:%'"
                  " AND kind NOT LIKE 'missing:%'")
        c.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def close(self) -> None:
        self.conn.close()

    # -- raw ------------------------------------------------------------
    def save_page(self, url: str, kind: str, html: str, *, status: int = 200,
                  tender_id: str | None = None, page: int | None = None) -> str:
        """Store raw HTML. Returns new | changed | unchanged.

        Unchanged means the canonical content hash matches what we already
        hold -- Dynatrace script noise is ignored -- so a periodic crawl
        does not rewrite every page.
        """
        now = _now()
        sha = canonical_html_sha256(html)
        prev = self.conn.execute(
            "SELECT content_sha256, first_seen_at FROM raw_page WHERE url=?",
            (url,)).fetchone()
        if prev is not None and prev["content_sha256"] == sha:
            self.conn.execute(
                "UPDATE raw_page SET fetched_at=?, status=? WHERE url=?",
                (now, status, url))
            self.conn.commit()
            return "unchanged"
        first = prev["first_seen_at"] if prev and prev["first_seen_at"] else now
        self.conn.execute(
            "INSERT INTO raw_page"
            " (url, kind, tender_id, page, status, fetched_at, first_seen_at,"
            "  content_sha256, html)"
            " VALUES (?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(url) DO UPDATE SET"
            " kind=excluded.kind, tender_id=excluded.tender_id,"
            " page=excluded.page, status=excluded.status,"
            " fetched_at=excluded.fetched_at,"
            " content_sha256=excluded.content_sha256, html=excluded.html",
            (url, kind, tender_id, page, status, now, first, sha, html),
        )
        self.conn.commit()
        return "changed" if prev is not None else "new"

    def has_page(self, url: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM raw_page WHERE url=? AND html IS NOT NULL"
            " AND length(html) > 0", (url,))
        return cur.fetchone() is not None

    def pages(self, kind: str) -> Iterable[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM raw_page WHERE kind=? ORDER BY page, tender_id",
            (kind,))

    # -- parsed ---------------------------------------------------------
    def upsert_tender(self, row: dict[str, Any], source_url: str = "") -> None:
        """Insert or update a tender's fields.

        Never overwrites a populated column with NULL: the listing carries
        less than the detail page and may be parsed afterwards. A state given
        here only ever moves forward; the crawl itself does not pass one --
        it records sightings and calls refresh_state(), which can also move a
        re-opened tender back to Available.
        """
        row = dict(row)
        row["source_url"] = source_url
        row["parsed_at"] = _now()
        cols = [c for c in (
            "tender_id", "tender_number", "subject", "ministry", "tender_type",
            "sector_type", "entity_tender_number", "publish_date",
            "closing_date", "envelope_system", "document_value", "tender_bond",
            "technical_open_date", "financial_open_date", "awarded_date",
            "awarded_amount",
            "family", "state", "brief_description", "targeted_tenderer",
            "service_delivery", "auction_type", "local_value_system",
            "validity_period", "evaluation_basis", "evaluation_criteria",
            "final_insurance", "delivery_location", "contract_prep_period",
            "contract_duration", "warranty_period", "maintenance_period",
            "disbursement_method",
            "source_url", "parsed_at") if c in row]
        rank_case = " ".join(f"WHEN '{k}' THEN {v}"
                             for k, v in STATE_RANK.items() if k)

        def _set(c):
            if c == "state":
                return (f"state=CASE WHEN (CASE excluded.state {rank_case}"
                        f" ELSE -1 END) >= (CASE tender.state {rank_case}"
                        f" ELSE -1 END) THEN COALESCE(excluded.state,"
                        f" tender.state) ELSE tender.state END")
            if c == "family":
                return "family=COALESCE(tender.family, excluded.family)"
            return f"{c}=COALESCE(excluded.{c}, tender.{c})"
        sets = ", ".join(_set(c) for c in cols if c != "tender_id")
        self.conn.execute(
            f"INSERT INTO tender ({', '.join(cols)})"
            f" VALUES ({', '.join('?' * len(cols))})"
            f" ON CONFLICT(tender_id) DO UPDATE SET {sets}",
            [row.get(c) for c in cols],
        )

    def replace_companies(self, tender_id: str,
                          rows: list[dict[str, Any]]) -> None:
        """Replace a tender's company rows with those from its latest page.

        Safe because the page only ever gains information as a tender
        advances (checked on the live site, Sept 2026): at financial opening
        it lists bidder names only; after the award it lists the winners,
        every bidder, and the prices.
        """
        from .normalize import cr_root, normalize_name
        self.conn.execute("DELETE FROM company WHERE tender_id=?", (tender_id,))
        for r in rows:
            self.conn.execute(
                "INSERT OR REPLACE INTO company (tender_id, role, seq, name,"
                " name_normalised, cr_number, cr_root, cr_secondary, value,"
                " financial_result, local_value_ratio, notes, stage)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tender_id, r["role"], r["seq"], r.get("name"),
                 r.get("name_normalised") or normalize_name(r.get("name")),
                 r.get("cr_number"),
                 cr_root(r.get("cr_number")), r.get("cr_secondary"),
                 r.get("value"), r.get("financial_result"),
                 r.get("local_value_ratio"), r.get("notes"),
                 r.get("stage") or ("awarded" if r["role"] == "awarded"
                                    else "unknown")),
            )

    def replace_tender_activities(self, tender_id: str,
                                  rows: list[dict[str, Any]]) -> None:
        self.conn.execute("DELETE FROM tender_activity WHERE tender_id=?",
                          (tender_id,))
        for r in rows:
            self.conn.execute(
                "INSERT OR REPLACE INTO tender_activity"
                " (tender_id, activity_code, activity_name) VALUES (?,?,?)",
                (tender_id, r["activity_code"], r.get("activity_name")))

    def upsert_company(self, row: dict[str, Any], page: int | None = None,
                       pass_id: int | None = None) -> None:
        """A classified-register row.

        With a pass_id the company was just seen on the live register, which
        resets any delisting. Without one (re-parsing stored HTML) the
        delisting bookkeeping is left exactly as it was.
        """
        from .normalize import cr_root, normalize_name
        self.conn.execute(
            "INSERT INTO classified_company (profile_number,"
            " company_type, name, name_normalised, cr_number, cr_root,"
            " cr_secondary, size, evaluation, classification, certificate_end,"
            " page, parsed_at, last_pass, missed, delisted_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,NULL)"
            " ON CONFLICT(profile_number) DO UPDATE SET"
            " company_type=excluded.company_type, name=excluded.name,"
            " name_normalised=excluded.name_normalised,"
            " cr_number=excluded.cr_number, cr_root=excluded.cr_root,"
            " cr_secondary=excluded.cr_secondary, size=excluded.size,"
            " evaluation=excluded.evaluation,"
            " classification=excluded.classification,"
            " certificate_end=excluded.certificate_end, page=excluded.page,"
            " parsed_at=excluded.parsed_at,"
            " last_pass=COALESCE(excluded.last_pass,"
            "                    classified_company.last_pass),"
            " missed=CASE WHEN excluded.last_pass IS NULL"
            "        THEN classified_company.missed ELSE 0 END,"
            " delisted_at=CASE WHEN excluded.last_pass IS NULL"
            "        THEN classified_company.delisted_at ELSE NULL END",
            (row["profile_number"], row.get("company_type"), row.get("name"),
             row.get("name_normalised") or normalize_name(row.get("name")),
             row.get("cr_number"),
             cr_root(row.get("cr_number")), row.get("cr_secondary"),
             row.get("size"), row.get("evaluation"), row.get("classification"),
             row.get("certificate_end"), page, _now(), pass_id))
        self.conn.execute("DELETE FROM company_activity WHERE profile_number=?",
                          (row["profile_number"],))
        for a in row.get("activities", []):
            self.conn.execute(
                "INSERT OR REPLACE INTO company_activity (profile_number,"
                " activity_code, activity_name, grade) VALUES (?,?,?,?)",
                (row["profile_number"], a["activity_code"],
                 a.get("activity_name"), a.get("grade")))

    def mark_unlisted(self, pass_id: int) -> tuple[int, int]:
        """After a complete register pass: count a miss for every company
        the pass did not see, and mark it delisted after GONE_AFTER passes.
        Returns (missed now, newly delisted)."""
        cur = self.conn.execute(
            "UPDATE classified_company SET missed = missed + 1"
            " WHERE delisted_at IS NULL"
            "   AND (last_pass IS NULL OR last_pass != ?)", (pass_id,))
        missed = cur.rowcount
        cur = self.conn.execute(
            "UPDATE classified_company SET delisted_at=?"
            " WHERE delisted_at IS NULL AND missed >= ?", (_now(), GONE_AFTER))
        self.conn.commit()
        return missed, cur.rowcount

    # -- lifecycle ------------------------------------------------------
    def record_sighting(self, tender_id: str, kind: str, family: str | None,
                        state: str | None, run_id: int | None = None) -> bool:
        """Note that a tender is listed under a section right now.

        Returns True the first time it is seen in that section -- what drives
        the "caught up" test. Seeing it again clears any gone marker: it is
        back on the list.
        """
        now = _now()
        cur = self.conn.execute(
            "UPDATE sighting SET last_seen=?, last_run=?, missed=0,"
            " gone_at=NULL WHERE tender_id=? AND kind=?",
            (now, run_id, tender_id, kind))
        if cur.rowcount:
            return False
        self.conn.execute(
            "INSERT INTO sighting (tender_id, kind, family, state, first_seen,"
            " last_seen, last_run) VALUES (?,?,?,?,?,?,?)",
            (tender_id, kind, family, state, now, now, run_id))
        return True

    def refresh_state(self, tender_id: str) -> tuple[str | None, str] | None:
        """Set a tender's state from the sections it is listed in now.

        The most advanced current listing wins, because the site lists a
        tender in several sections at once (every Financially Opened tender
        is also under Technically Opened). A state only moves backwards
        before opening -- a closing date extended after closing sends a
        tender back to Available. A tender listed nowhere keeps its last
        known state. Returns (old, new) when it changed.
        """
        row = self.conn.execute("SELECT state FROM tender WHERE tender_id=?",
                                (tender_id,)).fetchone()
        if row is None:
            return None
        old = row["state"]
        listed = [r["state"] for r in self.conn.execute(
            "SELECT state FROM sighting WHERE tender_id=? AND gone_at IS NULL",
            (tender_id,))]
        if not listed:
            return None
        best = max(listed, key=state_rank)
        if state_rank(best) > state_rank(old):
            new = best
        elif state_rank(best) < state_rank(old) and old in PRE_OPENING:
            new = best
        else:
            return None
        if new == old:
            return None
        self.conn.execute("UPDATE tender SET state=? WHERE tender_id=?",
                          (new, tender_id))
        return old, new

    def mark_unseen(self, kind: str, run_id: int) -> list[str]:
        """After a complete, clean read of a section: count a miss for every
        tender listed there before but not in this read; after GONE_AFTER
        misses in a row, mark it gone and re-derive its state.

        Two misses, not one: a tender that leaves a list while it is being
        read shifts the rest up a place, and the one that crosses the page
        boundary is skipped for that read. Returns tenders newly gone.
        """
        self.conn.execute(
            "UPDATE sighting SET missed = missed + 1"
            " WHERE kind=? AND gone_at IS NULL"
            "   AND (last_run IS NULL OR last_run != ?)", (kind, run_id))
        gone = [r[0] for r in self.conn.execute(
            "SELECT tender_id FROM sighting WHERE kind=? AND gone_at IS NULL"
            " AND missed >= ?", (kind, GONE_AFTER))]
        self.conn.execute(
            "UPDATE sighting SET gone_at=? WHERE kind=? AND gone_at IS NULL"
            " AND missed >= ?", (_now(), kind, GONE_AFTER))
        for tid in gone:
            self.refresh_state(tid)
        self.conn.commit()
        return gone

    def current_state(self, tender_id: str) -> str | None:
        row = self.conn.execute("SELECT state FROM tender WHERE tender_id=?",
                                (tender_id,)).fetchone()
        return row["state"] if row else None

    def watermark(self, kind: str, field: str = "awarded_date") -> str | None:
        """Latest date among tenders already listed in a section, capped at
        today (a mistyped future date must not move the stopping point)."""
        today = date.today().isoformat()
        row = self.conn.execute(
            f"SELECT MAX(t.{field}) FROM tender t JOIN sighting s"
            f" ON s.tender_id=t.tender_id AND s.kind=?"
            f" WHERE t.{field} IS NOT NULL AND t.{field} <= ?",
            (kind, today)).fetchone()
        return row[0] if row else None

    def left_every_list(self) -> list[str]:
        """Tenders no longer listed anywhere we have read, and not yet seen
        in a final state. Usually a tender whose next section has not been
        reached yet (a rolling pass of Awarded or Cancelled finds it)."""
        sql = ("SELECT t.tender_id FROM tender t"
               " WHERE t.state NOT IN ('awarded', 'cancelled')"
               "   AND EXISTS (SELECT 1 FROM sighting s"
               "               WHERE s.tender_id=t.tender_id)"
               "   AND NOT EXISTS (SELECT 1 FROM sighting s"
               "                   WHERE s.tender_id=t.tender_id"
               "                     AND s.gone_at IS NULL)")
        return [r[0] for r in self.conn.execute(sql)]

    # -- details --------------------------------------------------------
    def mark_detail(self, tender_id: str, page_type: str,
                    state: str | None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO detail_fetch"
            " (tender_id, page_type, state, fetched_at) VALUES (?,?,?,?)",
            (tender_id, page_type, state, _now()))

    def detail_jobs(self, kinds: Iterable[str], *, tender_details: bool = True
                    ) -> list[tuple[str, str, str | None]]:
        """Detail pages to fetch, most valuable first: (page_type, id, state).

          1. companies pages of awarded tenders not yet read since the award
             -- winners, every bidder, and prices
          2. companies pages of tenders in technical / financial opening,
             never read -- who is bidding now
          3. TenderDetails pages never read

        A companies page is read again only when the tender reaches
        'awarded': at financial opening the page still shows the technical
        list only (live site, Sept 2026), so re-reading it then adds nothing.
        Driven by what is stored, not by which pages this run walked, so an
        interrupted detail phase is finished next time without re-reading a
        single listing page.
        """
        kinds = list(kinds)
        if not kinds:
            return []
        marks = ",".join("?" * len(kinds))
        sighted = (f"EXISTS (SELECT 1 FROM sighting s WHERE s.tender_id ="
                   f" t.tender_id AND s.kind IN ({marks}))")
        dead = {(r["kind"], r["ref"]) for r in self.conn.execute(
            "SELECT kind, ref FROM failed_page WHERE"
            " (kind IN ('companies', 'tender_details') AND attempts >= ?)"
            " OR (kind LIKE 'missing:%' AND (attempts >= ? OR failed_at > ?))",
            (DETAIL_MAX_ATTEMPTS, MISSING_MAX_ATTEMPTS,
             (datetime.now(timezone.utc)
              - timedelta(days=MISSING_RETRY_DAYS)).isoformat(
                  timespec="seconds")))}

        def alive(ptype, tid, state):
            if (f"missing:{ptype}", tid) in dead:
                return False
            ref = companies_ref(tid, state) if ptype == "companies" else tid
            return (ptype, ref) not in dead

        comp = self.conn.execute(
            f"SELECT t.tender_id, t.state FROM tender t"
            f" LEFT JOIN detail_fetch d ON d.tender_id = t.tender_id"
            f"   AND d.page_type = 'companies'"
            f" WHERE {sighted} AND t.state IN ('technical','financial','awarded')"
            f"   AND (d.tender_id IS NULL"
            f"        OR (t.state = 'awarded' AND COALESCE(d.state, '')"
            f"            != 'awarded'))"
            f" ORDER BY CASE t.state WHEN 'awarded' THEN 0 ELSE 1 END,"
            f"          COALESCE(t.awarded_date, '') DESC,"
            f"          CAST(t.tender_id AS INTEGER) DESC",
            kinds).fetchall()
        jobs = [("companies", r["tender_id"], r["state"]) for r in comp
                if alive("companies", r["tender_id"], r["state"])]
        if tender_details:
            det = self.conn.execute(
                f"SELECT t.tender_id, t.state FROM tender t"
                f" LEFT JOIN detail_fetch d ON d.tender_id = t.tender_id"
                f"   AND d.page_type = 'tender_details'"
                f" WHERE {sighted} AND d.tender_id IS NULL"
                f" ORDER BY CAST(t.tender_id AS INTEGER) DESC",
                kinds).fetchall()
            jobs += [("tender_details", r["tender_id"], r["state"])
                     for r in det
                     if alive("tender_details", r["tender_id"], r["state"])]
        return jobs

    def record_failure(self, kind: str, ref: str, url: str,
                       error: str) -> None:
        self.conn.execute(
            "INSERT INTO failed_page (kind, ref, url, error, attempts,"
            " failed_at) VALUES (?,?,?,?,1,?)"
            " ON CONFLICT(kind, ref) DO UPDATE SET error=excluded.error,"
            " attempts=failed_page.attempts+1, failed_at=excluded.failed_at",
            (kind, str(ref), url, error[:300], _now()))
        self.conn.commit()

    def record_missing(self, page_type: str, tender_id: str, url: str) -> None:
        """The site answered with its home page: not a network failure."""
        self.record_failure(f"missing:{page_type}", tender_id, url,
                            "the site returned its home page for this id")

    def clear_failure(self, kind: str, ref: str) -> None:
        self.conn.execute("DELETE FROM failed_page WHERE kind=? AND ref=?",
                          (kind, str(ref)))

    def clear_failures_beyond(self, kind: str, last: int) -> None:
        """Listing failures on pages past the end: the section shrank."""
        for r in self.failures(kind):
            if str(r["ref"]).isdigit() and int(r["ref"]) > last:
                self.clear_failure(kind, r["ref"])

    def failures(self, kind: str | None = None) -> list[sqlite3.Row]:
        if kind:
            return self.conn.execute(
                "SELECT * FROM failed_page WHERE kind=? ORDER BY ref",
                (kind,)).fetchall()
        return self.conn.execute(
            "SELECT * FROM failed_page ORDER BY kind, ref").fetchall()

    def section(self, kind: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM section_state WHERE kind=?",
                                (kind,)).fetchone()
        if row is None:
            self.conn.execute("INSERT INTO section_state (kind) VALUES (?)",
                              (kind,))
            self.conn.commit()
            row = self.conn.execute(
                "SELECT * FROM section_state WHERE kind=?", (kind,)).fetchone()
        return row

    def update_section(self, kind: str, **fields: Any) -> None:
        self.section(kind)
        fields["last_update"] = _now()
        sets = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE section_state SET {sets} WHERE kind=?",
                          (*fields.values(), kind))
        self.conn.commit()

    # -- run log --------------------------------------------------------
    def start_run(self, command: str, target: str, mode: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO run (command, target, mode, started_at)"
            " VALUES (?,?,?,?)", (command, target, mode, _now()))
        self.conn.commit()
        return cur.lastrowid

    def bump(self, run_id: int, **counts: int) -> None:
        sets = ", ".join(f"{k}={k}+?" for k in counts)
        self.conn.execute(f"UPDATE run SET {sets} WHERE run_id=?",
                          (*counts.values(), run_id))

    def finish_run(self, run_id: int, note: str = "") -> sqlite3.Row:
        self.conn.execute("UPDATE run SET finished_at=?, note=? WHERE run_id=?",
                          (_now(), note, run_id))
        self.conn.commit()
        return self.conn.execute("SELECT * FROM run WHERE run_id=?",
                                 (run_id,)).fetchone()

    def runs(self, limit: int = 10) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM run ORDER BY run_id DESC LIMIT ?",
            (limit,)).fetchall()

    def mark_page(self, kind: str, page: int, n_ids: int) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO crawl_state (kind, page, fetched_at, n_ids)"
            " VALUES (?,?,?,?)", (kind, page, _now(), n_ids))
        self.conn.commit()

    def done_pages(self, kind: str) -> set[int]:
        cur = self.conn.execute(
            "SELECT page FROM crawl_state WHERE kind=?", (kind,))
        return {r[0] for r in cur.fetchall()}

    def replace_features(self, rows: list[dict[str, Any]]) -> int:
        cols = ["cr_root", "as_of_month", "awards_12m_count",
                "awards_12m_value", "awards_36m_count", "awards_36m_value",
                "awards_life_value", "largest_award_12m",
                "months_since_last_award", "bids_12m_count",
                "decided_12m_count", "wins_12m_count", "win_rate_12m",
                "distinct_buyers_12m", "top_buyer_share_12m",
                "months_since_first_seen", "classified_size",
                "classified_evaluation", "certificate_end", "feature_version",
                "built_at"]
        self.conn.execute("DELETE FROM feature_cr_month")
        self.conn.executemany(
            f"INSERT INTO feature_cr_month ({', '.join(cols)})"
            f" VALUES ({', '.join('?' * len(cols))})",
            [[r.get(c) if c != "built_at" else (r.get(c) or _now())
              for c in cols] for r in rows])
        self.conn.commit()
        return len(rows)

    def commit(self) -> None:
        self.conn.commit()

    # -- matches --------------------------------------------------------
    def save_matches(self, rows: list[dict[str, Any]]) -> None:
        for r in rows:
            self.conn.execute(
                "INSERT OR REPLACE INTO match (customer_id, tender_id, role,"
                " seq, method, score, customer_name, matched_name, matched_cr,"
                " matched_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (r["customer_id"], r["tender_id"], r["role"], r.get("seq", 0),
                 r["method"], r.get("score"), r.get("customer_name"),
                 r.get("matched_name"), r.get("matched_cr"), _now()),
            )
        self.conn.commit()

    def clear_matches(self, customer_ids: Iterable[str] | None = None) -> None:
        """Clear all matches, or only those of the given customers -- so a
        re-run never leaves yesterday's matches next to today's."""
        if customer_ids is None:
            self.conn.execute("DELETE FROM match")
        else:
            self.conn.executemany("DELETE FROM match WHERE customer_id=?",
                                  [(c,) for c in set(customer_ids)])
        self.conn.commit()

    # -- reads ----------------------------------------------------------
    def companies(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT c.*, t.awarded_date, t.ministry, t.awarded_amount"
            " FROM company c JOIN tender t USING (tender_id)").fetchall()

    def history(self, tender_id: str) -> dict[str, Any]:
        """Everything recorded about one tender: its lifecycle as the crawl
        saw it, which pages were read at which state, and its companies."""
        q = self.conn.execute
        return {
            "tender": q("SELECT * FROM tender WHERE tender_id=?",
                        (tender_id,)).fetchone(),
            "sightings": q("SELECT * FROM sighting WHERE tender_id=?"
                           " ORDER BY first_seen, kind",
                           (tender_id,)).fetchall(),
            "fetches": q("SELECT * FROM detail_fetch WHERE tender_id=?"
                         " ORDER BY page_type", (tender_id,)).fetchall(),
            "companies": q("SELECT role, stage, seq, name, cr_number, value"
                           " FROM company WHERE tender_id=?"
                           " ORDER BY role, seq", (tender_id,)).fetchall(),
            "failures": q("SELECT * FROM failed_page WHERE ref=? OR ref LIKE ?",
                          (tender_id, f"{tender_id}@%")).fetchall(),
        }

    def stats(self) -> dict[str, Any]:
        q = self.conn.execute

        def one(sql, *a):
            r = q(sql, a).fetchone()
            return r[0] if r else None
        return {
            "listing pages stored": one("SELECT COUNT(*) FROM raw_page WHERE kind='listing'"),
            "detail pages stored": one("SELECT COUNT(*) FROM raw_page WHERE kind='detail'"),
            "tenders": one("SELECT COUNT(*) FROM tender"),
            "tenders with award date": one("SELECT COUNT(*) FROM tender WHERE awarded_date IS NOT NULL"),
            "tenders listed nowhere now": len(self.left_every_list()),
            "award rows": one("SELECT COUNT(*) FROM company WHERE role='awarded'"),
            "bidder rows": one("SELECT COUNT(*) FROM company WHERE role='bidder'"),
            "distinct companies": one("SELECT COUNT(DISTINCT COALESCE(cr_root, name_normalised)) FROM company"),
            "companies with CR": one("SELECT COUNT(*) FROM company WHERE cr_root IS NOT NULL"),
            "earliest award": one("SELECT MIN(awarded_date) FROM tender WHERE awarded_date IS NOT NULL"),
            "latest award": one("SELECT MAX(awarded_date) FROM tender WHERE awarded_date IS NOT NULL"),
            "total awarded QAR": one("SELECT ROUND(SUM(awarded_amount),2) FROM tender"),
            "classified companies": one("SELECT COUNT(*) FROM classified_company"),
            "classified, delisted": one("SELECT COUNT(*) FROM classified_company WHERE delisted_at IS NOT NULL"),
            "company activities": one("SELECT COUNT(*) FROM company_activity"),
            "tender activities": one("SELECT COUNT(*) FROM tender_activity"),
            "matches": one("SELECT COUNT(*) FROM match"),
            "matches needing review": one("SELECT COUNT(*) FROM match WHERE method IN ('name_fuzzy', 'name_translit')"),
            "feature rows": one("SELECT COUNT(*) FROM feature_cr_month"),
            "pages awaiting retry": one("SELECT COUNT(*) FROM failed_page"),
            "last run": one("SELECT finished_at FROM run WHERE finished_at"
                            " IS NOT NULL ORDER BY run_id DESC LIMIT 1"),
        }

    def by_state(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT family, state, COUNT(*) n,"
            " SUM(awarded_amount) value FROM tender"
            " WHERE family IS NOT NULL GROUP BY family, state"
            " ORDER BY family, state").fetchall()


def _kind_for(family: str | None, state: str | None) -> str | None:
    """The listing section for a (family, state) pair."""
    from .parse import LISTING_KINDS
    for kind, spec in LISTING_KINDS.items():
        if spec["state"] == state and spec["family"] == (family or "tender"):
            return kind
    return None
