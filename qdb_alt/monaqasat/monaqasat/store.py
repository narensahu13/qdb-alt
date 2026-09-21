"""SQLite store.

One file holds everything: the raw HTML exactly as fetched, the parsed rows,
the crawl position and the match results.

Raw HTML is kept deliberately. Parsers get things wrong and sites change; if
the pages are on disk you re-parse in seconds instead of re-crawling 1,200
pages and annoying a government web server. It also means the fetch date of
every field is provable, which is what makes this usable as evidence.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

from .fingerprint import canonical_html_sha256

SCHEMA = """
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
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# How far along its life a tender is. A tender listed under several sections
# over time keeps the most advanced one. Cancelled is terminal.
STATE_RANK = {
    None: -1, "future": 0, "published": 1, "closed": 2, "technical": 3,
    "financial": 4, "awarded": 5, "cancelled": 6,
}


def state_rank(state: str | None) -> int:
    return STATE_RANK.get(state, -1)


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

    def _migrate(self) -> None:
        """Bring a database from an earlier version up to date, once.

        Older stores kept no sightings or fetch log. Rebuild both from what
        is already on disk so the first incremental run does not refetch
        pages we hold.
        """
        c = self.conn
        cols = {r[1] for r in c.execute("PRAGMA table_info(section_state)")}
        if "reached_end" not in cols:
            c.execute("ALTER TABLE section_state ADD COLUMN reached_end"
                      " INTEGER NOT NULL DEFAULT 0")
            c.execute("UPDATE section_state SET reached_end=backfill_complete")

        raw_cols = {r[1] for r in c.execute("PRAGMA table_info(raw_page)")}
        if "content_sha256" not in raw_cols:
            c.execute("ALTER TABLE raw_page ADD COLUMN content_sha256 TEXT")
        if "first_seen_at" not in raw_cols:
            c.execute("ALTER TABLE raw_page ADD COLUMN first_seen_at TEXT")
            c.execute("UPDATE raw_page SET first_seen_at=fetched_at"
                      " WHERE first_seen_at IS NULL")

        tender_cols = {r[1] for r in c.execute("PRAGMA table_info(tender)")}
        if "financial_open_date" not in tender_cols:
            c.execute("ALTER TABLE tender ADD COLUMN financial_open_date TEXT")

        company_cols = {r[1] for r in c.execute("PRAGMA table_info(company)")}
        if "stage" not in company_cols:
            c.execute("ALTER TABLE company ADD COLUMN stage TEXT")
            c.execute("UPDATE company SET stage=role WHERE stage IS NULL")

        if c.execute("SELECT COUNT(*) FROM sighting").fetchone()[0] == 0:
            c.execute(
                "INSERT OR IGNORE INTO sighting"
                " (tender_id, kind, family, state, first_seen, last_seen)"
                " SELECT tender_id, COALESCE(state, 'unknown'), family, state,"
                "        COALESCE(parsed_at, ?), COALESCE(parsed_at, ?)"
                " FROM tender WHERE state IS NOT NULL", (_now(), _now()))
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
        c.commit()

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

    def missing_detail_ids(self) -> list[str]:
        """Tender ids seen in listings whose detail page is not yet stored."""
        cur = self.conn.execute(
            "SELECT DISTINCT t.tender_id FROM tender t"
            " LEFT JOIN raw_page r ON r.kind='detail' AND r.tender_id=t.tender_id"
            " WHERE r.url IS NULL")
        return [r[0] for r in cur.fetchall()]

    # -- parsed ---------------------------------------------------------
    def upsert_tender(self, row: dict[str, Any], source_url: str = "") -> None:
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
        # Never overwrite a populated column with NULL: the listing carries
        # less than the detail page and may be parsed afterwards. And never
        # move a tender backwards: crawling "technical" after "awarded" must
        # not demote an awarded tender.
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
        from .normalize import cr_root
        self.conn.execute("DELETE FROM company WHERE tender_id=?", (tender_id,))
        for r in rows:
            self.conn.execute(
                "INSERT OR REPLACE INTO company (tender_id, role, seq, name,"
                " name_normalised, cr_number, cr_root, cr_secondary, value,"
                " financial_result, local_value_ratio, notes, stage)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tender_id, r["role"], r["seq"], r.get("name"),
                 r.get("name_normalised"), r.get("cr_number"),
                 cr_root(r.get("cr_number")), r.get("cr_secondary"),
                 r.get("value"), r.get("financial_result"),
                 r.get("local_value_ratio"), r.get("notes"),
                 r.get("stage") or r["role"]),
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

    def upsert_company(self, row: dict[str, Any], page: int | None = None) -> None:
        from .normalize import cr_root
        self.conn.execute(
            "INSERT OR REPLACE INTO classified_company (profile_number,"
            " company_type, name, name_normalised, cr_number, cr_root,"
            " cr_secondary, size, evaluation, classification, certificate_end,"
            " page, parsed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (row["profile_number"], row.get("company_type"), row.get("name"),
             row.get("name_normalised"), row.get("cr_number"),
             cr_root(row.get("cr_number")), row.get("cr_secondary"),
             row.get("size"), row.get("evaluation"), row.get("classification"),
             row.get("certificate_end"), page, _now()))
        self.conn.execute("DELETE FROM company_activity WHERE profile_number=?",
                          (row["profile_number"],))
        for a in row.get("activities", []):
            self.conn.execute(
                "INSERT OR REPLACE INTO company_activity (profile_number,"
                " activity_code, activity_name, grade) VALUES (?,?,?,?)",
                (row["profile_number"], a["activity_code"],
                 a.get("activity_name"), a.get("grade")))

    # -- incremental bookkeeping ----------------------------------------
    def record_sighting(self, tender_id: str, kind: str, family: str | None,
                        state: str | None) -> tuple[bool, bool]:
        """Note that a tender was listed under a section.

        Returns (new_in_section, state_advanced). new_in_section is what
        drives the "caught up" test; state_advanced is what triggers a
        refetch of the companies page.
        """
        now = _now()
        prev = self.conn.execute(
            "SELECT state FROM tender WHERE tender_id=?", (tender_id,)
        ).fetchone()
        seen = self.conn.execute(
            "SELECT 1 FROM sighting WHERE tender_id=? AND kind=?",
            (tender_id, kind)).fetchone()
        if seen:
            self.conn.execute(
                "UPDATE sighting SET last_seen=? WHERE tender_id=? AND kind=?",
                (now, tender_id, kind))
        else:
            self.conn.execute(
                "INSERT INTO sighting (tender_id, kind, family, state,"
                " first_seen, last_seen) VALUES (?,?,?,?,?,?)",
                (tender_id, kind, family, state, now, now))
        advanced = (prev is not None
                    and state_rank(state) > state_rank(prev["state"]))
        return (seen is None, advanced)

    def needs_detail(self, tender_id: str, page_type: str,
                     state: str | None) -> bool:
        """Fetch if we never have, or if the tender has moved on since."""
        row = self.conn.execute(
            "SELECT state FROM detail_fetch WHERE tender_id=? AND page_type=?",
            (tender_id, page_type)).fetchone()
        if row is None:
            return True
        if page_type == "companies":
            return state_rank(state) > state_rank(row["state"])
        return False            # the description does not change with state

    def mark_detail(self, tender_id: str, page_type: str,
                    state: str | None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO detail_fetch"
            " (tender_id, page_type, state, fetched_at) VALUES (?,?,?,?)",
            (tender_id, page_type, state, _now()))

    def current_state(self, tender_id: str) -> str | None:
        row = self.conn.execute("SELECT state FROM tender WHERE tender_id=?",
                                (tender_id,)).fetchone()
        return row["state"] if row else None

    def pending_details(self, kind: str, page_type: str,
                        max_attempts: int = 5) -> list[tuple[str, str | None]]:
        """Tenders sighted in a section whose detail page is missing or stale.

        Driven by what is stored, not by which pages this run happened to
        walk -- so a detail phase interrupted last week is finished this
        week without re-reading a single listing page. Pages that have
        failed max_attempts times are left alone and reported instead.
        """
        rows = self.conn.execute(
            "SELECT s.tender_id, t.state AS now_state, d.state AS got_state,"
            "       d.tender_id AS have"
            " FROM sighting s JOIN tender t USING (tender_id)"
            " LEFT JOIN detail_fetch d"
            "   ON d.tender_id = s.tender_id AND d.page_type = ?"
            " WHERE s.kind = ?", (page_type, kind)).fetchall()
        dead = {r["ref"] for r in self.conn.execute(
            "SELECT ref FROM failed_page WHERE kind=? AND attempts>=?",
            (f"{kind}:{page_type}", max_attempts))}
        out = []
        for r in rows:
            if r["tender_id"] in dead:
                continue
            if r["have"] is None:
                out.append((r["tender_id"], r["now_state"]))
            elif (page_type == "companies"
                  and state_rank(r["now_state"]) > state_rank(r["got_state"])):
                out.append((r["tender_id"], r["now_state"]))
        return out

    def record_failure(self, kind: str, ref: str, url: str,
                       error: str) -> None:
        self.conn.execute(
            "INSERT INTO failed_page (kind, ref, url, error, attempts,"
            " failed_at) VALUES (?,?,?,?,1,?)"
            " ON CONFLICT(kind, ref) DO UPDATE SET error=excluded.error,"
            " attempts=failed_page.attempts+1, failed_at=excluded.failed_at",
            (kind, str(ref), url, error[:300], _now()))
        self.conn.commit()

    def clear_failure(self, kind: str, ref: str) -> None:
        self.conn.execute("DELETE FROM failed_page WHERE kind=? AND ref=?",
                          (kind, str(ref)))

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
        self.conn.execute("DELETE FROM feature_cr_month")
        for r in rows:
            self.conn.execute(
                "INSERT INTO feature_cr_month (cr_root, as_of_month,"
                " awards_12m_count, awards_12m_value, awards_36m_count,"
                " awards_36m_value, awards_life_value, largest_award_12m,"
                " months_since_last_award, bids_12m_count, wins_12m_count,"
                " win_rate_12m, distinct_buyers_12m, top_buyer_share_12m,"
                " months_since_first_seen, classified_size,"
                " classified_evaluation, certificate_end, feature_version,"
                " built_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r["cr_root"], r["as_of_month"], r.get("awards_12m_count"),
                 r.get("awards_12m_value"), r.get("awards_36m_count"),
                 r.get("awards_36m_value"), r.get("awards_life_value"),
                 r.get("largest_award_12m"), r.get("months_since_last_award"),
                 r.get("bids_12m_count"), r.get("wins_12m_count"),
                 r.get("win_rate_12m"), r.get("distinct_buyers_12m"),
                 r.get("top_buyer_share_12m"), r.get("months_since_first_seen"),
                 r.get("classified_size"), r.get("classified_evaluation"),
                 r.get("certificate_end"), r.get("feature_version"),
                 r.get("built_at") or _now()),
            )
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

    def clear_matches(self) -> None:
        self.conn.execute("DELETE FROM match")
        self.conn.commit()

    # -- reads ----------------------------------------------------------
    def companies(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT c.*, t.awarded_date, t.ministry, t.awarded_amount"
            " FROM company c JOIN tender t USING (tender_id)").fetchall()

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
            "award rows": one("SELECT COUNT(*) FROM company WHERE role='awarded'"),
            "bidder rows": one("SELECT COUNT(*) FROM company WHERE role='bidder'"),
            "distinct companies": one("SELECT COUNT(DISTINCT COALESCE(cr_root, name_normalised)) FROM company"),
            "companies with CR": one("SELECT COUNT(*) FROM company WHERE cr_root IS NOT NULL"),
            "earliest award": one("SELECT MIN(awarded_date) FROM tender WHERE awarded_date IS NOT NULL"),
            "latest award": one("SELECT MAX(awarded_date) FROM tender WHERE awarded_date IS NOT NULL"),
            "total awarded QAR": one("SELECT ROUND(SUM(awarded_amount),2) FROM tender"),
            "classified companies": one("SELECT COUNT(*) FROM classified_company"),
            "company activities": one("SELECT COUNT(*) FROM company_activity"),
            "tender activities": one("SELECT COUNT(*) FROM tender_activity"),
            "matches": one("SELECT COUNT(*) FROM match"),
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
