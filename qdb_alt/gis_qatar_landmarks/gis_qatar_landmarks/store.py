"""SQLite store: landmarks, customer matches, and a log of runs.

Each `load` replaces the layer with today's and remembers when every landmark
was first and last seen, so a place that leaves the map stays in the
database, marked as no longer listed. Every command runs inside
`Store.run(...)`, which records start, finish, duration, rows, a note and --
if it failed -- the error.
"""

from __future__ import annotations

import json
import sqlite3
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS landmark (
    id             INTEGER PRIMARY KEY,    -- the layer's LANDMARK_ID
    name           TEXT NOT NULL,
    name_ar        TEXT,
    category       TEXT,                   -- BUSINESS, LODGING, EDUCATION, HEALTH, FINANCE ...
    subcategory_ar TEXT,                   -- published in Arabic only
    district       TEXT,
    zone           INTEGER,
    status         INTEGER,                -- the layer's code, meaning not published
    website        TEXT,
    lat            REAL,
    lon            REAL,
    modified       TEXT,                   -- when the layer last changed the record
    listed         INTEGER NOT NULL DEFAULT 1,
    first_seen     TEXT NOT NULL,
    last_seen      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS match (
    customer_id    TEXT PRIMARY KEY,
    customer_name  TEXT,
    cr_number      TEXT,
    landmark_ids   TEXT,                   -- JSON list; [] when not on the map
    landmark_names TEXT,
    run_id         INTEGER,
    matched_at     TEXT NOT NULL
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

COLUMNS = ("id", "name", "name_ar", "category", "subcategory_ar", "district", "zone", "status", "website",
           "lat", "lon", "modified")


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

    def replace_list(self, landmarks: list[dict[str, Any]]) -> tuple[int, int]:
        """Today's layer. Returns (on the layer, dropped since last load)."""
        stamp = now()
        ids = {m["id"] for m in landmarks}
        dropped = [i for (i,) in self.db.execute("SELECT id FROM landmark WHERE listed = 1") if i not in ids]
        self.db.executemany("UPDATE landmark SET listed = 0 WHERE id = ?", [(i,) for i in dropped])
        updates = ", ".join(f"{c} = excluded.{c}" for c in COLUMNS if c != "id")
        self.db.executemany(
            f"""INSERT INTO landmark ({", ".join(COLUMNS)}, listed, first_seen, last_seen)
                VALUES ({", ".join(":" + c for c in COLUMNS)}, 1, :stamp, :stamp)
                ON CONFLICT (id) DO UPDATE SET {updates}, listed = 1, last_seen = excluded.last_seen""",
            [{**m, "stamp": stamp} for m in landmarks])
        return len(ids), len(dropped)

    def landmarks(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM landmark WHERE listed = 1 ORDER BY name, id")]

    def save_matches(self, rows: list[dict[str, Any]], run_id: int) -> int:
        stamp = now()
        self.db.executemany(
            """INSERT OR REPLACE INTO match (customer_id, customer_name, cr_number, landmark_ids, landmark_names,
                   run_id, matched_at)
               VALUES (:customer_id, :customer_name, :cr_number, :landmark_ids, :landmark_names,
                       :run_id, :matched_at)""",
            [{**r, "landmark_ids": json.dumps(r["landmark_ids"]), "run_id": run_id, "matched_at": stamp}
             for r in rows])
        return len(rows)

    def stats(self) -> list[tuple[str, Any]]:
        q = lambda sql: self.db.execute(sql).fetchone()[0]
        return [
            ("landmarks", q("SELECT COUNT(*) FROM landmark WHERE listed = 1")),
            ("business landmarks", q("SELECT COUNT(*) FROM landmark WHERE listed = 1 AND category = 'BUSINESS'")),
            ("no longer listed", q("SELECT COUNT(*) FROM landmark WHERE listed = 0")),
            ("customers on the map", q("SELECT COUNT(*) FROM match WHERE landmark_names IS NOT NULL")),
            ("customers searched", q("SELECT COUNT(*) FROM match")),
        ]
