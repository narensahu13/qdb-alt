"""SQLite store: the searches and listing pages fetched from the site, customer
matches, and a log of runs.

The site is searched one company at a time, so what was fetched is kept and
re-used for 30 days (`search.fetched_at`, `company.fetched_at`): a second run
over the same customer book asks the site only for what is new or stale. A
failed request is recorded with its error and retried on the next run; it
never replaces an earlier good answer.

Every command runs inside `Store.run(...)`, which records when it started and
finished, how long it took, how many rows it wrote, a note and -- if it
failed -- the error. `python -m qatar_online_directory runs` prints that log.
"""

from __future__ import annotations

import json
import sqlite3
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS search (
    query         TEXT PRIMARY KEY,  -- the name as sent to the site
    status        TEXT NOT NULL,     -- ok | error
    listings      TEXT,              -- JSON: [{listing_id, slug, name, url}], every result page
    pages         INTEGER,           -- result pages the site reported
    error         TEXT,
    fetched_at    TEXT NOT NULL,
    run_id        INTEGER
);
CREATE TABLE IF NOT EXISTS company (
    listing_id    INTEGER PRIMARY KEY,
    url           TEXT NOT NULL,
    status        TEXT NOT NULL,     -- ok | error
    name          TEXT,
    city          TEXT,
    has_po_box    INTEGER,
    has_phone     INTEGER,
    categories    TEXT,              -- JSON list
    brands        TEXT,              -- JSON list
    error         TEXT,
    fetched_at    TEXT NOT NULL,
    run_id        INTEGER
);
CREATE TABLE IF NOT EXISTS match (
    customer_id   TEXT PRIMARY KEY,
    customer_name TEXT,
    cr_number     TEXT,
    query         TEXT,
    status        TEXT,              -- found | not found | no search (not searched yet, or the search failed)
    listing_ids   TEXT,              -- JSON list, best first
    matched_name  TEXT,
    score         REAL,
    run_id        INTEGER,
    matched_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    command       TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    seconds       REAL,
    status        TEXT NOT NULL DEFAULT 'running',   -- running | ok | error
    rows          INTEGER NOT NULL DEFAULT 0,
    note          TEXT,
    error         TEXT
);
"""

COMPANY_FIELDS = ("name", "city", "has_po_box", "has_phone", "categories", "brands")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fresh(stamp: str | None, max_age_days: float) -> bool:
    if not stamp:
        return False
    return datetime.fromisoformat(stamp) > datetime.now(timezone.utc) - timedelta(days=max_age_days)


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- run log -----------------------------------------------------------

    @contextmanager
    def run(self, command: str) -> Iterator[dict[str, Any]]:
        """Log one command. The body sets info['rows'] / info['note']; an exception is
        recorded with its traceback and re-raised. What the body committed stays."""
        cur = self.db.execute("INSERT INTO run (command, started_at) VALUES (?, ?)", (command, now()))
        self.db.commit()
        info: dict[str, Any] = {"id": cur.lastrowid, "rows": 0, "note": None}
        t0 = time.monotonic()
        try:
            yield info
        except BaseException as e:
            self.db.rollback()
            self._finish(info, "error", t0, "".join(traceback.format_exception_only(type(e), e)).strip())
            raise
        else:
            self._finish(info, "ok", t0, None)

    def _finish(self, info: dict[str, Any], status: str, t0: float, error: str | None) -> None:
        self.db.execute(
            "UPDATE run SET finished_at = ?, seconds = ?, status = ?, rows = ?, note = ?, error = ? WHERE id = ?",
            (now(), round(time.monotonic() - t0, 2), status, info["rows"], info["note"], error, info["id"]))
        self.db.commit()

    def runs(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM run ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    # ---- searches ----------------------------------------------------------

    def search(self, query: str) -> dict[str, Any] | None:
        r = self.db.execute("SELECT * FROM search WHERE query = ?", (query,)).fetchone()
        if r is None:
            return None
        d = dict(r)
        d["listings"] = json.loads(d["listings"] or "[]")
        return d

    def search_is_fresh(self, query: str, max_age_days: float) -> bool:
        s = self.search(query)
        return bool(s and s["status"] == "ok" and _fresh(s["fetched_at"], max_age_days))

    def save_search(self, query: str, listings: list[dict[str, Any]] | None, pages: int | None,
                    run_id: int, error: str | None = None) -> None:
        """A good answer replaces what was there; a failure is only recorded where
        there is no earlier good answer to keep."""
        if error is not None:
            self.db.execute(
                """INSERT INTO search (query, status, error, fetched_at, run_id) VALUES (?, 'error', ?, ?, ?)
                   ON CONFLICT(query) DO UPDATE SET error = excluded.error, run_id = excluded.run_id
                   WHERE search.status = 'error'""", (query, error, now(), run_id))
        else:
            self.db.execute(
                "INSERT OR REPLACE INTO search (query, status, listings, pages, error, fetched_at, run_id) "
                "VALUES (?, 'ok', ?, ?, NULL, ?, ?)",
                (query, json.dumps(listings, ensure_ascii=False), pages, now(), run_id))

    # ---- listing pages -----------------------------------------------------

    def company(self, listing_id: int) -> dict[str, Any] | None:
        r = self.db.execute("SELECT * FROM company WHERE listing_id = ?", (listing_id,)).fetchone()
        if r is None:
            return None
        d = dict(r)
        for k in ("categories", "brands"):
            d[k] = json.loads(d[k]) if d[k] is not None else None
        return d

    def company_is_fresh(self, listing_id: int, max_age_days: float) -> bool:
        c = self.company(listing_id)
        return bool(c and c["status"] == "ok" and _fresh(c["fetched_at"], max_age_days))

    def save_company(self, listing_id: int, url: str, fields: dict[str, Any] | None, run_id: int,
                     error: str | None = None) -> None:
        if error is not None:
            self.db.execute(
                """INSERT INTO company (listing_id, url, status, error, fetched_at, run_id)
                   VALUES (?, ?, 'error', ?, ?, ?)
                   ON CONFLICT(listing_id) DO UPDATE SET error = excluded.error, run_id = excluded.run_id
                   WHERE company.status = 'error'""", (listing_id, url, error, now(), run_id))
            return
        row = {k: fields.get(k) for k in COMPANY_FIELDS}
        row["categories"] = json.dumps(row["categories"] or [], ensure_ascii=False)
        row["brands"] = json.dumps(row["brands"] or [], ensure_ascii=False)
        self.db.execute(
            f"""INSERT OR REPLACE INTO company (listing_id, url, status, {", ".join(COMPANY_FIELDS)},
                    error, fetched_at, run_id)
                VALUES (:listing_id, :url, 'ok', {", ".join(":" + k for k in COMPANY_FIELDS)},
                    NULL, :fetched_at, :run_id)""",
            {**row, "listing_id": listing_id, "url": url, "fetched_at": now(), "run_id": run_id})

    # ---- matches -----------------------------------------------------------

    def save_matches(self, rows: list[dict[str, Any]], run_id: int) -> int:
        stamp = now()
        self.db.executemany(
            """INSERT OR REPLACE INTO match (customer_id, customer_name, cr_number, query, status,
                   listing_ids, matched_name, score, run_id, matched_at)
               VALUES (:customer_id, :customer_name, :cr_number, :query, :status,
                   :listing_ids, :matched_name, :score, :run_id, :matched_at)""",
            [{**r, "listing_ids": json.dumps([l["listing_id"] for l in r["listings"]]),
              "run_id": run_id, "matched_at": stamp} for r in rows])
        return len(rows)

    def stats(self) -> list[tuple[str, Any]]:
        q = lambda sql: self.db.execute(sql).fetchone()[0]
        return [
            ("searches", q("SELECT COUNT(*) FROM search")),
            ("  answered", q("SELECT COUNT(*) FROM search WHERE status = 'ok'")),
            ("  failed", q("SELECT COUNT(*) FROM search WHERE status = 'error'")),
            ("  with any result", q("SELECT COUNT(*) FROM search WHERE status = 'ok' AND listings != '[]'")),
            ("listing pages", q("SELECT COUNT(*) FROM company")),
            ("  read", q("SELECT COUNT(*) FROM company WHERE status = 'ok'")),
            ("  failed", q("SELECT COUNT(*) FROM company WHERE status = 'error'")),
            ("customers matched", q("SELECT COUNT(*) FROM match WHERE status = 'found'")),
            ("customers checked by match", q("SELECT COUNT(*) FROM match")),
        ]
