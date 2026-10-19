"""SQLite store: the two lists, customer matches, and a log of runs.

Each `load` replaces the lists with what the pages show today and remembers
when every company was first and last seen on them, so a company that drops
off a list stays in the database with its last-seen date. Every command runs
inside `Store.run(...)`, which records start, finish, duration, rows, a note
and -- if it failed -- the error.
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
CREATE TABLE IF NOT EXISTS trader (
    programme     TEXT NOT NULL,          -- aeo | green_list
    name          TEXT NOT NULL,
    year          INTEGER,                -- AEO accreditation year
    certificate   TEXT,                   -- AEO certificate number
    listed        INTEGER NOT NULL DEFAULT 1,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    PRIMARY KEY (programme, name)
);
CREATE TABLE IF NOT EXISTS match (
    customer_id   TEXT NOT NULL,
    programme     TEXT NOT NULL,
    customer_name TEXT,
    cr_number     TEXT,
    trader_name   TEXT,                   -- NULL: searched, not on this list
    run_id        INTEGER,
    matched_at    TEXT NOT NULL,
    PRIMARY KEY (customer_id, programme)
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

    @contextmanager
    def run(self, command: str) -> Iterator[dict[str, Any]]:
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

    def replace_list(self, programme: str, rows: list[dict[str, Any]]) -> tuple[int, int]:
        """Today's list for one programme. Returns (on the list, dropped since last load)."""
        stamp = now()
        names = {r["name"] for r in rows}
        dropped = [n for (n,) in self.db.execute(
            "SELECT name FROM trader WHERE programme = ? AND listed = 1", (programme,)) if n not in names]
        self.db.executemany("UPDATE trader SET listed = 0 WHERE programme = ? AND name = ?",
                            [(programme, n) for n in dropped])
        for r in rows:
            self.db.execute(
                """INSERT INTO trader (programme, name, year, certificate, listed, first_seen, last_seen)
                   VALUES (?, ?, ?, ?, 1, ?, ?)
                   ON CONFLICT (programme, name) DO UPDATE SET
                       year = excluded.year, certificate = excluded.certificate, listed = 1,
                       last_seen = excluded.last_seen""",
                (programme, r["name"], r["year"], r["certificate"], stamp, stamp))
        return len(rows), len(dropped)

    def traders(self, programme: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM trader WHERE programme = ? AND listed = 1 ORDER BY name", (programme,))]

    def save_matches(self, rows: list[dict[str, Any]], run_id: int) -> int:
        stamp = now()
        self.db.executemany(
            """INSERT OR REPLACE INTO match (customer_id, programme, customer_name, cr_number, trader_name,
                   run_id, matched_at)
               VALUES (:customer_id, :programme, :customer_name, :cr_number, :trader_name, :run_id, :matched_at)""",
            [{**r, "run_id": run_id, "matched_at": stamp} for r in rows])
        return len(rows)

    def stats(self) -> list[tuple[str, Any]]:
        q = lambda sql: self.db.execute(sql).fetchone()[0]
        return [
            ("authorized economic operators", q("SELECT COUNT(*) FROM trader WHERE programme = 'aeo' AND listed = 1")),
            ("green list", q("SELECT COUNT(*) FROM trader WHERE programme = 'green_list' AND listed = 1")),
            ("no longer listed", q("SELECT COUNT(*) FROM trader WHERE listed = 0")),
            ("customers on a list", q("SELECT COUNT(DISTINCT customer_id) FROM match WHERE trader_name IS NOT NULL")),
            ("customers searched", q("SELECT COUNT(DISTINCT customer_id) FROM match")),
        ]
