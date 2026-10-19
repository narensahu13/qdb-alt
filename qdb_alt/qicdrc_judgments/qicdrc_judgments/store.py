"""SQLite store: the judgments and their company parties, the sitemap as last
read, pages that failed, customer matches, and a log of runs.

Unlike the other sources here, the pages themselves are not kept: a judgment
page names the private individuals who are parties, and only the metadata is
needed. Each judgment keeps the sitemap's last-modified date it was read at,
so a later `load` fetches only judgments that are new or changed (and pages
that failed last time); a change to the parser needs `load --refetch`.
Every command runs inside `Store.run(...)`, which records when it started and
finished, how long it took, how many rows it wrote and -- if it failed -- the error.
"""

from __future__ import annotations

import sqlite3
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS listed_page (    -- the English judgment pages in the sitemap, as last read
    url          TEXT PRIMARY KEY,
    lastmod      TEXT
);
CREATE TABLE IF NOT EXISTS judgment (
    url                  TEXT PRIMARY KEY,
    lastmod              TEXT,              -- from the sitemap, when the page was read
    fetched_at           TEXT NOT NULL,
    case_number          TEXT,
    citation             TEXT,              -- neutral citation, e.g. [2022] QIC (F) 14
    court                TEXT,
    judgment_date        TEXT,              -- YYYY-MM-DD
    year                 INTEGER,
    outcome              TEXT,              -- only from the court's own keywords; mostly empty
    keywords             TEXT,
    status               TEXT,
    case_title           TEXT,              -- companies by name, people as a count
    n_individual_parties INTEGER NOT NULL DEFAULT 0,
    listed               INTEGER NOT NULL DEFAULT 1,   -- 0: no longer in the sitemap
    run_id               INTEGER
);
CREATE TABLE IF NOT EXISTS party (          -- organisations only
    url    TEXT NOT NULL,
    side   TEXT NOT NULL,                   -- claimant | defendant | party (no 'v' in the case name)
    name   TEXT NOT NULL,
    PRIMARY KEY (url, side, name)
);
CREATE TABLE IF NOT EXISTS failed_page (
    url         TEXT PRIMARY KEY,
    lastmod     TEXT,
    attempts    INTEGER NOT NULL DEFAULT 1,
    error       TEXT,
    last_tried  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS match (
    customer_id   TEXT PRIMARY KEY,
    customer_name TEXT,
    cr_number     TEXT,
    judgments     INTEGER NOT NULL,     -- 0: searched, a party to none
    run_id        INTEGER,
    matched_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS match_hit (
    customer_id  TEXT NOT NULL,
    url          TEXT NOT NULL,
    side         TEXT NOT NULL,
    party_name   TEXT NOT NULL,
    PRIMARY KEY (customer_id, url, side, party_name)
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

JUDGMENT_FIELDS = ("url", "case_number", "citation", "court", "judgment_date", "year", "outcome", "keywords",
                   "status", "case_title", "n_individual_parties")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
        """Log one command. The body sets info['rows'] / info['note']; an exception is
        recorded with its message and re-raised."""
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

    # ---- the sitemap and what is still to fetch -------------------------------

    def set_listed(self, pages: list[tuple[str, str]]) -> int:
        """Replace the sitemap as last read; mark judgments that left it. Returns how many left."""
        self.db.execute("DELETE FROM listed_page")
        self.db.executemany("INSERT OR REPLACE INTO listed_page (url, lastmod) VALUES (?, ?)", pages)
        gone = self.db.execute("""UPDATE judgment SET listed = 0 WHERE listed = 1
                                  AND url NOT IN (SELECT url FROM listed_page)""").rowcount
        self.db.execute("UPDATE judgment SET listed = 1 WHERE url IN (SELECT url FROM listed_page)")
        return gone

    def judgment_lastmod(self) -> dict[str, str]:
        return {r["url"]: r["lastmod"] for r in self.db.execute("SELECT url, lastmod FROM judgment")}

    def pending(self) -> int:
        """Listed pages not read yet, or changed since they were read."""
        return self.db.execute("""SELECT COUNT(*) FROM listed_page l LEFT JOIN judgment j ON j.url = l.url
                                  WHERE j.url IS NULL OR j.lastmod IS NOT l.lastmod""").fetchone()[0]

    # ---- judgments ---------------------------------------------------------

    def save_judgment(self, j: dict[str, Any], lastmod: str, run_id: int) -> None:
        self.db.execute(
            f"INSERT OR REPLACE INTO judgment ({', '.join(JUDGMENT_FIELDS)}, lastmod, fetched_at, listed, run_id) "
            f"VALUES ({', '.join(':' + k for k in JUDGMENT_FIELDS)}, :lastmod, :fetched_at, 1, :run_id)",
            {**j, "lastmod": lastmod, "fetched_at": now(), "run_id": run_id})
        self.db.execute("DELETE FROM party WHERE url = ?", (j["url"],))
        self.db.executemany("INSERT OR IGNORE INTO party (url, side, name) VALUES (?, ?, ?)",
                            [(j["url"], p["side"], p["name"]) for p in j["parties"]])
        self.db.execute("DELETE FROM failed_page WHERE url = ?", (j["url"],))

    def record_failure(self, url: str, lastmod: str, error: str) -> None:
        self.db.execute("""INSERT INTO failed_page (url, lastmod, error, last_tried) VALUES (?, ?, ?, ?)
                           ON CONFLICT(url) DO UPDATE SET attempts = attempts + 1, lastmod = excluded.lastmod,
                               error = excluded.error, last_tried = excluded.last_tried""",
                        (url, lastmod, error, now()))

    def failures(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM failed_page ORDER BY url").fetchall()

    def judgments(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM judgment WHERE listed = 1 ORDER BY judgment_date")]

    def parties(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute(
            "SELECT p.* FROM party p JOIN judgment j ON j.url = p.url WHERE j.listed = 1 ORDER BY p.url, p.side")]

    # ---- matches -----------------------------------------------------------

    def save_matches(self, rows: list[dict[str, Any]], run_id: int) -> int:
        stamp = now()
        for r in rows:
            self.db.execute("DELETE FROM match_hit WHERE customer_id = ?", (r["customer_id"],))
        self.db.executemany(
            """INSERT OR REPLACE INTO match (customer_id, customer_name, cr_number, judgments, run_id, matched_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [(r["customer_id"], r["customer_name"], r["cr_number"], len({h["url"] for h in r["hits"]}), run_id, stamp)
             for r in rows])
        self.db.executemany(
            "INSERT OR IGNORE INTO match_hit (customer_id, url, side, party_name) VALUES (?, ?, ?, ?)",
            [(r["customer_id"], h["url"], h["side"], h["name"]) for r in rows for h in r["hits"]])
        return len(rows)

    def stats(self) -> list[tuple[str, Any]]:
        q = lambda sql: self.db.execute(sql).fetchone()[0]
        return [
            ("judgments listed", q("SELECT COUNT(*) FROM judgment WHERE listed = 1")),
            ("  distinct cases", q("SELECT COUNT(DISTINCT case_number) FROM judgment WHERE listed = 1")),
            ("  years", q("SELECT COALESCE(MIN(year) || '-' || MAX(year), '') FROM judgment WHERE listed = 1")),
            ("  with an outcome keyword", q("SELECT COUNT(*) FROM judgment WHERE listed = 1 AND outcome IS NOT NULL")),
            ("  with an individual party",
             q("SELECT COUNT(*) FROM judgment WHERE listed = 1 AND n_individual_parties > 0")),
            ("company parties", q("SELECT COUNT(*) FROM party p JOIN judgment j ON j.url = p.url WHERE j.listed = 1")),
            ("  distinct names", q("SELECT COUNT(DISTINCT LOWER(name)) FROM party")),
            ("still to fetch", self.pending()),
            ("failed pages (retried next load)", q("SELECT COUNT(*) FROM failed_page")),
            ("no longer listed", q("SELECT COUNT(*) FROM judgment WHERE listed = 0")),
            ("customers who are a party", q("SELECT COUNT(*) FROM match WHERE judgments > 0")),
            ("customers searched", q("SELECT COUNT(*) FROM match")),
        ]
