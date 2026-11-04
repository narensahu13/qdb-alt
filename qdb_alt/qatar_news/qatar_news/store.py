"""SQLite store: the headline index, the sitemaps it was read from, cached
Gulf Times searches, customer matches, and a log of runs.

A sitemap is recorded with when it was fetched and whether it arrived whole.
Articles are taken only from a whole sitemap, so a cut-off download is never
half-counted, and the next `load` fetches it again. A month that was read
after it ended (plus two days for late additions) is not fetched again.
Every command runs inside `Store.run(...)`, which records when it started and
finished, how long it took, how many rows it wrote and -- if it failed -- the error.
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
CREATE TABLE IF NOT EXISTS article (
    url           TEXT PRIMARY KEY,
    paper         TEXT NOT NULL,     -- Gulf Times | Qatar Tribune | The Peninsula | QNA
    date          TEXT,              -- YYYY-MM-DD
    title         TEXT NOT NULL,     -- the headline (The Peninsula) or the words of the address (the others)
    sitemap_url   TEXT               -- the sitemap it was last seen in
);
CREATE INDEX IF NOT EXISTS article_paper ON article (paper, date);
CREATE TABLE IF NOT EXISTS sitemap (
    url           TEXT PRIMARY KEY,
    paper         TEXT NOT NULL,
    kind          TEXT NOT NULL,     -- month | index | feed
    period        TEXT,              -- YYYY-MM for a month
    lastmod       TEXT,              -- what the paper's index said when it was fetched
    fetched_at    TEXT,              -- last time it arrived whole
    rows          INTEGER,           -- articles in it then
    complete      INTEGER NOT NULL DEFAULT 0,   -- 1: a whole copy has been read at least once
    status        TEXT,              -- ok | failed | gone | disallowed (the last attempt)
    error         TEXT
);
CREATE TABLE IF NOT EXISTS gt_search (
    query         TEXT PRIMARY KEY,
    fetched_at    TEXT NOT NULL,
    articles      INTEGER NOT NULL,  -- the count Gulf Times gives for the exact phrase
    titles        TEXT NOT NULL      -- JSON: the titles on the first result page
);
CREATE TABLE IF NOT EXISTS match (
    customer_id     TEXT PRIMARY KEY,
    customer_name   TEXT,
    cr_number       TEXT,
    search_name     TEXT,
    status          TEXT NOT NULL,   -- found | not_found | generic
    generic         INTEGER NOT NULL,
    gt_articles     INTEGER,         -- NULL: generic name, search not done, or the search failed
    headlines       INTEGER,
    headlines_365d  INTEGER,
    papers          INTEGER,
    days_since_last INTEGER,
    negative        INTEGER,
    examples        TEXT,
    run_id          INTEGER,
    matched_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    command       TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    seconds       REAL,
    status        TEXT NOT NULL DEFAULT 'running',   -- running | ok | stopped | error
    rows          INTEGER NOT NULL DEFAULT 0,
    note          TEXT,
    error         TEXT
);
"""

SETTLE_DAYS = 2    # a month's sitemap still gets late additions for a day or two after it ends


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _when(stamp: str | None) -> datetime | None:
    """'2026-10-01T00:00:00+03:00', '2026-11-04 04:28:27', '2026-11-04' -> an aware datetime (UTC when none given)."""
    if not stamp:
        return None
    try:
        t = datetime.fromisoformat(stamp.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def month_settled(period: str, fetched_at: str | None) -> bool:
    """True when the copy we hold was fetched after the month ended (plus SETTLE_DAYS)."""
    fetched = _when(fetched_at)
    if fetched is None:
        return False
    y, m = (int(x) for x in period.split("-"))
    ended = datetime(y + m // 12, m % 12 + 1, 1, tzinfo=timezone.utc)
    return fetched >= ended + timedelta(days=SETTLE_DAYS)


class Store:
    def __init__(self, path: str | Path):
        self.db = sqlite3.connect(Path(path))
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
        """Log one command. The body sets info['rows'] / info['note'] (and info['status'] =
        'stopped' when it ended early on purpose); an exception is recorded with its message
        and re-raised."""
        cur = self.db.execute("INSERT INTO run (command, started_at) VALUES (?, ?)", (command, now()))
        self.db.commit()
        info: dict[str, Any] = {"id": cur.lastrowid, "rows": 0, "note": None, "status": "ok"}
        t0 = time.monotonic()
        try:
            yield info
        except BaseException as e:
            self.db.rollback()
            self._finish(info, "error", t0, "".join(traceback.format_exception_only(type(e), e)).strip())
            raise
        else:
            self._finish(info, info["status"], t0, None)

    def _finish(self, info: dict[str, Any], status: str, t0: float, error: str | None) -> None:
        self.db.execute(
            "UPDATE run SET finished_at = ?, seconds = ?, status = ?, rows = ?, note = ?, error = ? WHERE id = ?",
            (now(), round(time.monotonic() - t0, 2), status, info["rows"], info["note"], error, info["id"]))
        self.db.commit()

    def runs(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM run ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    # ---- sitemaps ----------------------------------------------------------

    def sitemaps(self) -> dict[str, dict[str, Any]]:
        return {r["url"]: dict(r) for r in self.db.execute("SELECT * FROM sitemap")}

    def sitemap_read(self, url: str, paper: str, kind: str, period: str | None, lastmod: str, rows: int) -> None:
        self.db.execute(
            """INSERT INTO sitemap (url, paper, kind, period, lastmod, fetched_at, rows, complete, status, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'ok', NULL)
               ON CONFLICT(url) DO UPDATE SET lastmod = excluded.lastmod, fetched_at = excluded.fetched_at,
                   rows = excluded.rows, complete = 1, status = 'ok', error = NULL""",
            (url, paper, kind, period, lastmod, now(), rows))

    def sitemap_failed(self, url: str, paper: str, kind: str, period: str | None, status: str, error: str) -> None:
        """The last attempt failed; a whole copy read earlier (complete = 1) is kept as it was."""
        self.db.execute(
            """INSERT INTO sitemap (url, paper, kind, period, complete, status, error) VALUES (?, ?, ?, ?, 0, ?, ?)
               ON CONFLICT(url) DO UPDATE SET status = excluded.status, error = excluded.error""",
            (url, paper, kind, period, status, error))

    # ---- articles ----------------------------------------------------------

    def save_articles(self, rows: list[dict[str, Any]], sitemap_url: str) -> int:
        """Insert new articles and refresh the title/date of known ones; returns how many were new."""
        params = [{**r, "sitemap_url": sitemap_url} for r in rows]
        new = self.db.executemany(
            "INSERT OR IGNORE INTO article (url, paper, date, title, sitemap_url) "
            "VALUES (:url, :paper, :date, :title, :sitemap_url)", params).rowcount
        self.db.executemany(
            "UPDATE article SET date = :date, title = :title, sitemap_url = :sitemap_url "
            "WHERE url = :url AND (date IS NOT :date OR title IS NOT :title OR sitemap_url IS NOT :sitemap_url)",
            params)
        return max(new, 0)

    def articles(self) -> Iterator[sqlite3.Row]:
        return self.db.execute("SELECT paper, date, title, url FROM article")

    def paper_counts(self) -> dict[str, int]:
        return {r[0]: r[1] for r in self.db.execute("SELECT paper, COUNT(*) FROM article GROUP BY paper")}

    # ---- Gulf Times searches -----------------------------------------------

    def cached_search(self, query: str, max_age_days: float) -> tuple[int, list[str]] | None:
        row = self.db.execute("SELECT * FROM gt_search WHERE query = ?", (query,)).fetchone()
        fetched = _when(row["fetched_at"]) if row else None
        if fetched is None or datetime.now(timezone.utc) - fetched > timedelta(days=max_age_days):
            return None
        return row["articles"], json.loads(row["titles"])

    def save_search(self, query: str, articles: int, titles: list[str]) -> None:
        self.db.execute("INSERT OR REPLACE INTO gt_search (query, fetched_at, articles, titles) VALUES (?, ?, ?, ?)",
                        (query, now(), articles, json.dumps(titles, ensure_ascii=False)))

    # ---- matches -----------------------------------------------------------

    def save_matches(self, rows: list[dict[str, Any]], run_id: int) -> int:
        stamp = now()
        self.db.executemany(
            """INSERT OR REPLACE INTO match (customer_id, customer_name, cr_number, search_name, status, generic,
                   gt_articles, headlines, headlines_365d, papers, days_since_last, negative, examples, run_id,
                   matched_at)
               VALUES (:customer_id, :customer_name, :cr_number, :search_name, :status, :generic, :gt_articles,
                   :headlines, :headlines_365d, :papers, :days_since_last, :negative, :examples, :run_id,
                   :matched_at)""",
            [{**r, "run_id": run_id, "matched_at": stamp} for r in rows])
        return len(rows)

    def stats(self) -> list[tuple[str, Any]]:
        q = lambda sql, *a: self.db.execute(sql, a).fetchone()[0]
        out: list[tuple[str, Any]] = [("articles", q("SELECT COUNT(*) FROM article"))]
        for r in self.db.execute("SELECT paper, COUNT(*) n, MIN(date) first, MAX(date) last FROM article "
                                 "GROUP BY paper ORDER BY paper"):
            out.append((f"  {r['paper']}", f"{r['n']} ({r['first']} .. {r['last']})"))
        out += [
            ("sitemaps read whole", q("SELECT COUNT(*) FROM sitemap WHERE complete = 1")),
            ("sitemaps never read whole", q("SELECT COUNT(*) FROM sitemap WHERE complete = 0")),
            ("  last attempt failed", q("SELECT COUNT(*) FROM sitemap WHERE status = 'failed'")),
            ("  gone from the site", q("SELECT COUNT(*) FROM sitemap WHERE status = 'gone'")),
            ("Gulf Times searches cached", q("SELECT COUNT(*) FROM gt_search")),
            ("customers with news", q("SELECT COUNT(*) FROM match WHERE status = 'found'")),
            ("customers with a generic name", q("SELECT COUNT(*) FROM match WHERE status = 'generic'")),
            ("customers searched", q("SELECT COUNT(*) FROM match")),
        ]
        return out
