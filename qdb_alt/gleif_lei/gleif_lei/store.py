"""SQLite store: the LEI records of Qatar, customer matches, and a log of runs.

Each `load` replaces the records with what GLEIF publishes today and
remembers when every LEI was first and last seen, so a record that disappears
from the API (moved abroad, merged away) stays in the database with its
last-seen date. Every command runs inside `Store.run(...)`, which records
start, finish, duration, rows, a note and -- if it failed -- the error.
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
CREATE TABLE IF NOT EXISTS lei_record (
    lei                  TEXT PRIMARY KEY,
    legal_name           TEXT NOT NULL,
    other_names          TEXT NOT NULL DEFAULT '[]',   -- JSON list: other and transliterated names
    registered_at        TEXT,                         -- registration authority (RA000494: Qatar CR)
    registered_as        TEXT,                         -- the number at that authority
    cr_root              TEXT,                         -- CR number without zeros/branch, Qatar CR only
    legal_form_code      TEXT,                         -- ELF code
    legal_form           TEXT,
    entity_status        TEXT,                         -- ACTIVE | INACTIVE | NULL
    registration_status  TEXT,                         -- ISSUED | LAPSED | RETIRED | DUPLICATE ...
    initial_registration TEXT,
    last_update          TEXT,
    next_renewal         TEXT,
    hq_city              TEXT,
    listed               INTEGER NOT NULL DEFAULT 1,
    first_seen           TEXT NOT NULL,
    last_seen            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS lei_record_cr ON lei_record (cr_root);
CREATE TABLE IF NOT EXISTS match (
    customer_id   TEXT PRIMARY KEY,
    customer_name TEXT,
    cr_number     TEXT,
    lei           TEXT,                   -- NULL: searched, no LEI
    matched_name  TEXT,
    match_method  TEXT,                   -- cr | name
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

FIELDS = ("lei", "legal_name", "other_names", "registered_at", "registered_as", "cr_root", "legal_form_code",
          "legal_form", "entity_status", "registration_status", "initial_registration", "last_update",
          "next_renewal", "hq_city")


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

    def replace_records(self, rows: list[dict[str, Any]]) -> tuple[int, int]:
        """Today's records. Returns (listed, dropped since last load)."""
        stamp = now()
        leis = {r["lei"] for r in rows}
        dropped = [lei for (lei,) in self.db.execute("SELECT lei FROM lei_record WHERE listed = 1")
                   if lei not in leis]
        self.db.executemany("UPDATE lei_record SET listed = 0 WHERE lei = ?", [(lei,) for lei in dropped])
        cols = ", ".join(FIELDS)
        updates = ", ".join(f"{f} = excluded.{f}" for f in FIELDS if f != "lei")
        self.db.executemany(
            f"""INSERT INTO lei_record ({cols}, listed, first_seen, last_seen)
                VALUES ({", ".join(":" + f for f in FIELDS)}, 1, :stamp, :stamp)
                ON CONFLICT (lei) DO UPDATE SET {updates}, listed = 1, last_seen = excluded.last_seen""",
            [{**{f: r[f] for f in FIELDS}, "stamp": stamp} for r in rows])
        return len(rows), len(dropped)

    def records(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM lei_record WHERE listed = 1 ORDER BY lei")]

    def save_matches(self, rows: list[dict[str, Any]], run_id: int) -> int:
        stamp = now()
        self.db.executemany(
            """INSERT OR REPLACE INTO match (customer_id, customer_name, cr_number, lei, matched_name, match_method,
                   run_id, matched_at)
               VALUES (:customer_id, :customer_name, :cr_number, :lei, :matched_name, :match_method, :run_id,
                   :matched_at)""",
            [{**r, "run_id": run_id, "matched_at": stamp} for r in rows])
        return len(rows)

    def stats(self) -> list[tuple[str, Any]]:
        q = lambda sql: self.db.execute(sql).fetchone()[0]
        return [
            ("LEI records (listed)", q("SELECT COUNT(*) FROM lei_record WHERE listed = 1")),
            ("  issued", q("SELECT COUNT(*) FROM lei_record WHERE listed = 1 AND registration_status = 'ISSUED'")),
            ("  lapsed", q("SELECT COUNT(*) FROM lei_record WHERE listed = 1 AND registration_status = 'LAPSED'")),
            ("  with a Qatar CR number", q("SELECT COUNT(*) FROM lei_record WHERE listed = 1 AND cr_root IS NOT NULL")),
            ("no longer listed", q("SELECT COUNT(*) FROM lei_record WHERE listed = 0")),
            ("customers with an LEI", q("SELECT COUNT(*) FROM match WHERE lei IS NOT NULL")),
            ("customers searched", q("SELECT COUNT(*) FROM match")),
        ]
