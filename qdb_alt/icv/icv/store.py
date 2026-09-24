"""The ICV database.

One SQLite file. The shape follows one rule: a row is written once and then
only closed, never rewritten, so that any question can be asked *as at a date*
rather than only about today. That matters more here than it looks -- an ICV
score fed into a PD model has to be the score the company had at the
observation date, not the score it has now.

Two series, and they are not symmetric:

  score_observation   the portal publishes every change of score, with its
                      date and reason, back to the company's first
                      certificate. Retrospective and free: one backfill gets
                      all of it.

  listing_state       status, certificate number and expiry appear only in the
                      listing, never in the published history. This series
                      therefore begins the day the first sweep runs and can
                      never be recovered for the past. It is the reason to
                      start sweeping before the modelling work starts.
"""

from __future__ import annotations

import json
import sqlite3
import zlib
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMA_VERSION = 1

# A company has to be missing from this many consecutive complete sweeps
# before it is called gone. One miss is usually the portal, not the company.
MISSES_BEFORE_GONE = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS company (
    id            TEXT PRIMARY KEY,
    cr_number     TEXT,
    cr_root       TEXT,
    name          TEXT NOT NULL,
    name_norm     TEXT,
    industry      TEXT,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    misses        INTEGER NOT NULL DEFAULT 0,
    gone_at       TEXT,
    history_read  TEXT              -- when the score history was last fetched
);
CREATE INDEX IF NOT EXISTS company_cr   ON company(cr_root);
CREATE INDEX IF NOT EXISTS company_name ON company(name_norm);

CREATE TABLE IF NOT EXISTS score_observation (
    id          TEXT PRIMARY KEY,   -- icv_scorecardhistoryid, the portal's own
    company_id  TEXT NOT NULL,
    posted_at   TEXT NOT NULL,      -- UTC ISO-8601
    posted_date TEXT NOT NULL,      -- the date part, for point-in-time reads
    score       REAL NOT NULL,
    change      REAL,
    reason      TEXT,
    reason_code INTEGER,
    first_seen  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS score_company ON score_observation(company_id, posted_date);

CREATE TABLE IF NOT EXISTS listing_state (
    company_id     TEXT NOT NULL,
    from_date      TEXT NOT NULL,
    to_date        TEXT,            -- NULL while this is the current state
    total_score    REAL,
    status         TEXT,
    status_code    INTEGER,
    certificate_no TEXT,
    expiry_date    TEXT,
    manufacturer   INTEGER,
    PRIMARY KEY (company_id, from_date)
);
CREATE INDEX IF NOT EXISTS listing_open ON listing_state(company_id, to_date);

CREATE TABLE IF NOT EXISTS company_service (
    company_id      TEXT NOT NULL,
    service_type    TEXT NOT NULL,
    service_type_ar TEXT,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    PRIMARY KEY (company_id, service_type)
);

CREATE TABLE IF NOT EXISTS company_good (
    company_id      TEXT NOT NULL,
    category        TEXT NOT NULL,
    sub_category    TEXT NOT NULL,
    category_ar     TEXT,
    sub_category_ar TEXT,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    PRIMARY KEY (company_id, category, sub_category)
);

CREATE TABLE IF NOT EXISTS raw_page (
    url        TEXT NOT NULL,
    kind       TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    body       BLOB NOT NULL,
    comp       TEXT NOT NULL,
    PRIMARY KEY (url, fetched_at)
);

CREATE TABLE IF NOT EXISTS run (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    mode        TEXT,
    listed      INTEGER NOT NULL DEFAULT 0,
    details     INTEGER NOT NULL DEFAULT 0,
    new_scores  INTEGER NOT NULL DEFAULT 0,
    changed     INTEGER NOT NULL DEFAULT 0,
    note        TEXT
);

CREATE TABLE IF NOT EXISTS crawl_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Point-in-time features, one row per matched client per month. A row dated
-- a month is built only from observations published on or before it.
CREATE TABLE IF NOT EXISTS client_month (
    client_id                TEXT NOT NULL,
    as_of_month              TEXT NOT NULL,
    company_id               TEXT,
    industry                 TEXT,
    score                    REAL,
    score_date               TEXT,
    score_change_12m         REAL,
    organic_change_12m       REAL,
    policy_change_12m        REAL,
    months_since_last_change INTEGER,
    months_certified         INTEGER,
    observations             INTEGER,
    status                   TEXT,
    days_to_expiry           INTEGER,
    standing_known           INTEGER,
    industry_median          REAL,
    vs_industry              REAL,
    feature_version          TEXT,
    built_at                 TEXT,
    PRIMARY KEY (client_id, as_of_month)
);

-- Customer identities. Never leaves this file: excluded from `pack`, and
-- there is a test that fails if it ever appears in a packed copy.
CREATE TABLE IF NOT EXISTS match (
    customer_id  TEXT NOT NULL,
    company_id   TEXT NOT NULL,
    method       TEXT NOT NULL,
    matched_on   TEXT,
    confidence   REAL,
    run_id       INTEGER,
    matched_at   TEXT NOT NULL,
    PRIMARY KEY (customer_id, company_id)
);
"""

PACK_SKIP = ("raw_page", "match", "client_month")


def _now() -> str:
    return (datetime.now(timezone.utc).replace(microsecond=0)
            .isoformat().replace("+00:00", "Z"))


def today() -> str:
    return date.today().isoformat()


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('schema', ?)",
            (str(SCHEMA_VERSION),))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---------------------------------------------------------------- runs

    def start_run(self, mode: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO run(started_at, mode) VALUES(?, ?)", (_now(), mode))
        self.conn.commit()
        return int(cur.lastrowid)

    def bump(self, run_id: int, **counts: int) -> None:
        if not counts:
            return
        sets = ", ".join(f"{k} = {k} + ?" for k in counts)
        self.conn.execute(f"UPDATE run SET {sets} WHERE id = ?",
                          (*counts.values(), run_id))

    def finish_run(self, run_id: int, note: str | None = None) -> sqlite3.Row:
        self.conn.execute(
            "UPDATE run SET finished_at = ?, note = ? WHERE id = ?",
            (_now(), note, run_id))
        self.conn.commit()
        return self.conn.execute("SELECT * FROM run WHERE id = ?",
                                 (run_id,)).fetchone()

    def last_run(self) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM run WHERE finished_at IS NOT NULL "
            "ORDER BY id DESC LIMIT 1").fetchone()

    # ------------------------------------------------------------ listing

    def see_company(self, row: dict[str, Any], seen_on: str | None = None
                    ) -> str:
        """Record one listing row. Returns 'new', 'changed' or 'same'.

        'changed' means something the listing carries -- score, status,
        certificate, expiry -- moved, which is the signal to re-read that
        company's score history.
        """
        seen_on = seen_on or today()
        cid = row["id"]
        existing = self.conn.execute(
            "SELECT * FROM company WHERE id = ?", (cid,)).fetchone()
        if existing is None:
            self.conn.execute(
                "INSERT INTO company(id, cr_number, cr_root, name, name_norm,"
                " industry, first_seen, last_seen) VALUES(?,?,?,?,?,?,?,?)",
                (cid, row.get("cr_number"), row.get("cr_root"), row["name"],
                 row.get("name_norm"), row.get("industry"), seen_on, seen_on))
            outcome = "new"
        else:
            self.conn.execute(
                "UPDATE company SET cr_number = ?, cr_root = ?, name = ?,"
                " name_norm = ?, industry = ?, last_seen = ?, misses = 0,"
                " gone_at = NULL WHERE id = ?",
                (row.get("cr_number"), row.get("cr_root"), row["name"],
                 row.get("name_norm"), row.get("industry"), seen_on, cid))
            outcome = "same"

        state = (row.get("total_score"), row.get("status"),
                 row.get("status_code"), row.get("certificate_no"),
                 row.get("expiry_date"),
                 None if row.get("manufacturer") is None
                 else int(bool(row["manufacturer"])))
        open_row = self.conn.execute(
            "SELECT * FROM listing_state WHERE company_id = ? AND to_date IS NULL"
            " ORDER BY from_date DESC LIMIT 1", (cid,)).fetchone()
        if open_row is None:
            self.conn.execute(
                "INSERT OR REPLACE INTO listing_state(company_id, from_date,"
                " to_date, total_score, status, status_code, certificate_no,"
                " expiry_date, manufacturer) VALUES(?,?,NULL,?,?,?,?,?,?)",
                (cid, seen_on, *state))
        else:
            current = (open_row["total_score"], open_row["status"],
                       open_row["status_code"], open_row["certificate_no"],
                       open_row["expiry_date"], open_row["manufacturer"])
            if current != state:
                if open_row["from_date"] == seen_on:
                    # Two sweeps in one day. The earlier reading would become
                    # a period that starts and ends on the same date, which
                    # no point-in-time query can ever return, so the later
                    # reading replaces it instead of stacking behind it.
                    self.conn.execute(
                        "UPDATE listing_state SET total_score = ?, status = ?,"
                        " status_code = ?, certificate_no = ?, expiry_date = ?,"
                        " manufacturer = ? WHERE company_id = ? AND from_date = ?",
                        (*state, cid, seen_on))
                else:
                    self.conn.execute(
                        "UPDATE listing_state SET to_date = ? WHERE"
                        " company_id = ? AND from_date = ?",
                        (seen_on, cid, open_row["from_date"]))
                    self.conn.execute(
                        "INSERT INTO listing_state(company_id, from_date,"
                        " to_date, total_score, status, status_code,"
                        " certificate_no, expiry_date, manufacturer)"
                        " VALUES(?,?,NULL,?,?,?,?,?,?)", (cid, seen_on, *state))
                if outcome == "same":
                    outcome = "changed"
        return outcome

    def close_missing(self, seen_ids: Iterable[str], seen_on: str | None = None
                      ) -> int:
        """After a *complete* sweep, count a miss against everything not in it.

        Only ever call this when the sweep really finished. A half-read sweep
        that called this would mark thousands of live companies as gone.
        """
        seen_on = seen_on or today()
        seen = set(seen_ids)
        gone = 0
        for r in self.conn.execute(
                "SELECT id, misses FROM company WHERE gone_at IS NULL"):
            if r["id"] in seen:
                continue
            misses = r["misses"] + 1
            if misses >= MISSES_BEFORE_GONE:
                self.conn.execute(
                    "UPDATE company SET misses = ?, gone_at = ? WHERE id = ?",
                    (misses, seen_on, r["id"]))
                self.conn.execute(
                    "UPDATE listing_state SET to_date = ? WHERE company_id = ?"
                    " AND to_date IS NULL", (seen_on, r["id"]))
                gone += 1
            else:
                self.conn.execute("UPDATE company SET misses = ? WHERE id = ?",
                                  (misses, r["id"]))
        self.conn.commit()
        return gone

    # ------------------------------------------------------------ history

    def save_history(self, company_id: str, rows: Sequence[dict[str, Any]],
                     seen_on: str | None = None) -> int:
        """Store a company's published score history. Returns how many rows
        were new. Existing rows are left exactly as they were."""
        seen_on = seen_on or today()
        added = 0
        for row in rows:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO score_observation(id, company_id,"
                " posted_at, posted_date, score, change, reason, reason_code,"
                " first_seen) VALUES(?,?,?,?,?,?,?,?,?)",
                (row["id"], company_id, row["posted_at"], row["posted_date"],
                 row["score"], row.get("change"), row.get("reason"),
                 row.get("reason_code"), seen_on))
            added += cur.rowcount or 0
        self.conn.execute(
            "UPDATE company SET history_read = ? WHERE id = ?",
            (seen_on, company_id))
        self.conn.commit()
        return added

    def save_goods_services(self, company_id: str,
                            rows: Sequence[dict[str, Any]],
                            seen_on: str | None = None) -> int:
        seen_on = seen_on or today()
        n = 0
        for row in rows:
            if row.get("service_type"):
                self.conn.execute(
                    "INSERT INTO company_service(company_id, service_type,"
                    " service_type_ar, first_seen, last_seen)"
                    " VALUES(?,?,?,?,?) ON CONFLICT(company_id, service_type)"
                    " DO UPDATE SET last_seen = excluded.last_seen",
                    (company_id, row["service_type"], row.get("service_type_ar"),
                     seen_on, seen_on))
                n += 1
            elif row.get("category"):
                self.conn.execute(
                    "INSERT INTO company_good(company_id, category,"
                    " sub_category, category_ar, sub_category_ar, first_seen,"
                    " last_seen) VALUES(?,?,?,?,?,?,?)"
                    " ON CONFLICT(company_id, category, sub_category)"
                    " DO UPDATE SET last_seen = excluded.last_seen",
                    (company_id, row["category"], row.get("sub_category") or "",
                     row.get("category_ar"), row.get("sub_category_ar"),
                     seen_on, seen_on))
                n += 1
        self.conn.commit()
        return n

    # ------------------------------------------------------------- queues

    def needs_history(self, changed: Iterable[str] = (),
                      include_unread: bool = True) -> list[str]:
        """Which companies to fetch a detail page for.

        Everything whose listing moved this run, plus -- on a first load or
        after an interruption -- everything whose history has never been read.
        """
        want = list(dict.fromkeys(changed))
        if include_unread:
            for r in self.conn.execute(
                    "SELECT id FROM company WHERE history_read IS NULL"
                    " AND gone_at IS NULL ORDER BY last_seen DESC"):
                if r["id"] not in want:
                    want.append(r["id"])
        return want

    # ----------------------------------------------------- point in time

    def score_as_at(self, company_id: str, asof: str | None = None
                    ) -> sqlite3.Row | None:
        asof = asof or today()
        return self.conn.execute(
            "SELECT * FROM score_observation WHERE company_id = ?"
            " AND posted_date <= ? ORDER BY posted_date DESC, posted_at DESC"
            " LIMIT 1", (company_id, asof)).fetchone()

    def status_as_at(self, company_id: str, asof: str | None = None
                     ) -> sqlite3.Row | None:
        asof = asof or today()
        return self.conn.execute(
            "SELECT * FROM listing_state WHERE company_id = ?"
            " AND from_date <= ? AND (to_date IS NULL OR to_date >= ?)"
            " ORDER BY from_date DESC LIMIT 1",
            (company_id, asof, asof)).fetchone()

    def industry_scores_as_at(self, industry: str, asof: str | None = None
                              ) -> list[float]:
        """Every company's score in one industry as at a date -- the cohort a
        borrower should be read against."""
        asof = asof or today()
        out = []
        for r in self.conn.execute(
                "SELECT id FROM company WHERE industry = ?", (industry,)):
            row = self.score_as_at(r["id"], asof)
            if row is not None:
                out.append(row["score"])
        return out

    # -------------------------------------------------------------- misc

    def save_raw(self, url: str, kind: str, payload: Any) -> None:
        body = zlib.compress(json.dumps(payload, separators=(",", ":"),
                                        ensure_ascii=False).encode("utf-8"), 9)
        self.conn.execute(
            "INSERT OR REPLACE INTO raw_page(url, kind, fetched_at, body, comp)"
            " VALUES(?,?,?,?,'z')", (url, kind, _now(), body))

    def companies(self, include_gone: bool = False) -> list[sqlite3.Row]:
        sql = ("SELECT c.*, s.total_score, s.status, s.certificate_no,"
               " s.expiry_date, s.manufacturer FROM company c"
               " LEFT JOIN listing_state s ON s.company_id = c.id"
               " AND s.to_date IS NULL")
        if not include_gone:
            sql += " WHERE c.gone_at IS NULL"
        return self.conn.execute(sql).fetchall()

    def stats(self) -> dict[str, Any]:
        q = lambda sql: self.conn.execute(sql).fetchone()[0]   # noqa: E731
        return {
            "companies": q("SELECT COUNT(*) FROM company WHERE gone_at IS NULL"),
            "gone": q("SELECT COUNT(*) FROM company WHERE gone_at IS NOT NULL"),
            "with CR": q("SELECT COUNT(*) FROM company WHERE cr_root IS NOT NULL"),
            "history read": q("SELECT COUNT(*) FROM company WHERE history_read IS NOT NULL"),
            "score observations": q("SELECT COUNT(*) FROM score_observation"),
            "listing states": q("SELECT COUNT(*) FROM listing_state"),
            "services": q("SELECT COUNT(*) FROM company_service"),
            "goods": q("SELECT COUNT(*) FROM company_good"),
            "matches": q("SELECT COUNT(*) FROM match"),
            "runs": q("SELECT COUNT(*) FROM run"),
        }

    def save_matches(self, matches: Iterable[dict[str, Any]], run_id: int | None,
                     replace: bool = False) -> int:
        if replace:
            self.conn.execute("DELETE FROM match")
        n = 0
        for m in matches:
            self.conn.execute(
                "INSERT OR REPLACE INTO match(customer_id, company_id, method,"
                " matched_on, confidence, run_id, matched_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (m["customer_id"], m["company_id"], m.get("method"),
                 m.get("matched_on"), m.get("confidence"), run_id, _now()))
            n += 1
        self.conn.commit()
        return n

    def save_client_months(self, rows: Iterable[dict[str, Any]]) -> int:
        n = 0
        for r in rows:
            cols = ", ".join(r)
            marks = ", ".join("?" * len(r))
            self.conn.execute(
                f"INSERT OR REPLACE INTO client_month ({cols})"
                f" VALUES ({marks})", tuple(r.values()))
            n += 1
        self.conn.commit()
        return n

    def set_state(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO crawl_state(key, value) VALUES(?,?)",
            (key, value))
        self.conn.commit()

    def get_state(self, key: str) -> str | None:
        r = self.conn.execute("SELECT value FROM crawl_state WHERE key = ?",
                              (key,)).fetchone()
        return r["value"] if r else None
