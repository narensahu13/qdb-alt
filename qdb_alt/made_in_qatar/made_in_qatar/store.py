"""SQLite store: factory pages, the factories read from them, customer matches,
and a log of runs.

Pages are kept compressed with the sitemap's last-modified date, so a later
`load` fetches only the pages that changed and can re-read the rest offline.
Every command runs inside `Store.run(...)`, which records when it started and
finished, how long it took, how many rows it wrote and -- if it failed -- the error.
"""

from __future__ import annotations

import sqlite3
import time
import traceback
import zlib
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS page (
    url          TEXT PRIMARY KEY,
    lastmod      TEXT,              -- from the sitemap
    fetched_at   TEXT NOT NULL,
    html         BLOB NOT NULL      -- zlib-compressed
);
CREATE TABLE IF NOT EXISTS factory (
    url               TEXT PRIMARY KEY,
    slug              TEXT,
    name              TEXT,
    address           TEXT,
    sector            TEXT,
    founded_year      INTEGER,
    website           TEXT,
    products          INTEGER NOT NULL DEFAULT 0,
    description_chars INTEGER NOT NULL DEFAULT 0,
    listed            INTEGER NOT NULL DEFAULT 1,   -- 0: no longer in the sitemap
    run_id            INTEGER
);
CREATE TABLE IF NOT EXISTS match (
    customer_id   TEXT PRIMARY KEY,
    customer_name TEXT,
    cr_number     TEXT,
    url           TEXT,              -- NULL: searched, not in the register
    factory_name  TEXT,
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

FACTORY_FIELDS = ("url", "slug", "name", "address", "sector", "founded_year", "website", "products",
                  "description_chars")


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

    # ---- pages -------------------------------------------------------------

    def page_lastmod(self) -> dict[str, str]:
        return {r["url"]: r["lastmod"] for r in self.db.execute("SELECT url, lastmod FROM page")}

    def save_page(self, url: str, lastmod: str, html: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO page (url, lastmod, fetched_at, html) VALUES (?, ?, ?, ?)",
                        (url, lastmod, now(), zlib.compress(html.encode("utf-8"))))

    def page_html(self, url: str) -> str:
        row = self.db.execute("SELECT html FROM page WHERE url = ?", (url,)).fetchone()
        return zlib.decompress(row["html"]).decode("utf-8")

    # ---- factories ---------------------------------------------------------

    def save_factory(self, f: dict[str, Any], run_id: int) -> None:
        self.db.execute(
            f"INSERT OR REPLACE INTO factory ({', '.join(FACTORY_FIELDS)}, listed, run_id) "
            f"VALUES ({', '.join(':' + k for k in FACTORY_FIELDS)}, 1, :run_id)", {**f, "run_id": run_id})

    def mark_unlisted(self, listed_urls: set[str]) -> int:
        gone = [r["url"] for r in self.db.execute("SELECT url FROM factory WHERE listed = 1")
                if r["url"] not in listed_urls]
        self.db.executemany("UPDATE factory SET listed = 0 WHERE url = ?", [(u,) for u in gone])
        return len(gone)

    def factories(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM factory WHERE listed = 1 ORDER BY name")]

    # ---- matches -----------------------------------------------------------

    def save_matches(self, rows: list[dict[str, Any]], run_id: int) -> int:
        stamp = now()
        self.db.executemany(
            """INSERT OR REPLACE INTO match (customer_id, customer_name, cr_number, url, factory_name,
                   score, run_id, matched_at)
               VALUES (:customer_id, :customer_name, :cr_number, :url, :factory_name, :score, :run_id, :matched_at)""",
            [{**r, "run_id": run_id, "matched_at": stamp} for r in rows])
        return len(rows)

    def stats(self) -> list[tuple[str, Any]]:
        q = lambda sql: self.db.execute(sql).fetchone()[0]
        return [
            ("factories listed", q("SELECT COUNT(*) FROM factory WHERE listed = 1")),
            ("  with a website", q("SELECT COUNT(*) FROM factory WHERE listed = 1 AND website IS NOT NULL")),
            ("  with a founding year", q("SELECT COUNT(*) FROM factory WHERE listed = 1 AND founded_year IS NOT NULL")),
            ("  sectors", q("SELECT COUNT(DISTINCT sector) FROM factory WHERE listed = 1")),
            ("no longer listed", q("SELECT COUNT(*) FROM factory WHERE listed = 0")),
            ("pages stored", q("SELECT COUNT(*) FROM page")),
            ("customers matched", q("SELECT COUNT(*) FROM match WHERE url IS NOT NULL")),
            ("customers searched", q("SELECT COUNT(*) FROM match")),
        ]
