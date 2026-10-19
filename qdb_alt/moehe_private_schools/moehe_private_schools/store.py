"""SQLite store: private schools, customer matches, and a log of runs.

Each `load` replaces the layer with today's and remembers when every record
was first and last seen, so a school that leaves the layer stays in the
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
CREATE TABLE IF NOT EXISTS school (
    id            INTEGER PRIMARY KEY,     -- the layer's OBJECTID
    name          TEXT NOT NULL,
    name_ar       TEXT,
    levels        TEXT,                    -- JSON list: Kindergarten, Primary, Preparatory, Secondary
    curricula     TEXT,                    -- JSON list: British, Indian, IB, ...
    curriculum_ar TEXT,                    -- as published, in Arabic
    district      TEXT,
    municipality  TEXT,
    zone          INTEGER,
    status        TEXT,                    -- Open | Temporarily Closed | Permanently Closed
    listed        INTEGER NOT NULL DEFAULT 1,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS match (
    customer_id   TEXT PRIMARY KEY,
    customer_name TEXT,
    cr_number     TEXT,
    searched      INTEGER NOT NULL,        -- 0: the name is not a school's, not looked up
    school_ids    TEXT,                    -- JSON list; [] when searched and not found
    school_names  TEXT,
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

    def replace_list(self, schools: list[dict[str, Any]]) -> tuple[int, int]:
        """Today's layer. Returns (on the layer, dropped since last load)."""
        stamp = now()
        ids = {s["id"] for s in schools}
        dropped = [i for (i,) in self.db.execute("SELECT id FROM school WHERE listed = 1") if i not in ids]
        self.db.executemany("UPDATE school SET listed = 0 WHERE id = ?", [(i,) for i in dropped])
        for s in schools:
            self.db.execute(
                """INSERT INTO school (id, name, name_ar, levels, curricula, curriculum_ar, district, municipality,
                                       zone, status, listed, first_seen, last_seen)
                   VALUES (:id, :name, :name_ar, :levels, :curricula, :curriculum_ar, :district, :municipality,
                           :zone, :status, 1, :stamp, :stamp)
                   ON CONFLICT (id) DO UPDATE SET
                       name = excluded.name, name_ar = excluded.name_ar, levels = excluded.levels,
                       curricula = excluded.curricula, curriculum_ar = excluded.curriculum_ar,
                       district = excluded.district, municipality = excluded.municipality, zone = excluded.zone,
                       status = excluded.status, listed = 1, last_seen = excluded.last_seen""",
                {**s, "levels": json.dumps(s["levels"]), "curricula": json.dumps(s["curricula"]), "stamp": stamp})
        return len(schools), len(dropped)

    def schools(self) -> list[dict[str, Any]]:
        out = []
        for r in self.db.execute("SELECT * FROM school WHERE listed = 1 ORDER BY name, id"):
            d = dict(r)
            d["levels"] = json.loads(d["levels"] or "[]")
            d["curricula"] = json.loads(d["curricula"] or "[]")
            out.append(d)
        return out

    def save_matches(self, rows: list[dict[str, Any]], run_id: int) -> int:
        stamp = now()
        self.db.executemany(
            """INSERT OR REPLACE INTO match (customer_id, customer_name, cr_number, searched, school_ids,
                   school_names, run_id, matched_at)
               VALUES (:customer_id, :customer_name, :cr_number, :searched, :school_ids, :school_names,
                       :run_id, :matched_at)""",
            [{**r, "school_ids": json.dumps(r["school_ids"]) if r["searched"] else None,
              "run_id": run_id, "matched_at": stamp} for r in rows])
        return len(rows)

    def stats(self) -> list[tuple[str, Any]]:
        q = lambda sql: self.db.execute(sql).fetchone()[0]
        return [
            ("school records", q("SELECT COUNT(*) FROM school WHERE listed = 1")),
            ("schools (name + district)",
             q("SELECT COUNT(*) FROM (SELECT DISTINCT LOWER(name), district FROM school WHERE listed = 1)")),
            ("open", q("SELECT COUNT(*) FROM school WHERE listed = 1 AND status = 'Open'")),
            ("no longer listed", q("SELECT COUNT(*) FROM school WHERE listed = 0")),
            ("customers registered", q("SELECT COUNT(*) FROM match WHERE school_names IS NOT NULL")),
            ("customers with a school name", q("SELECT COUNT(*) FROM match WHERE searched = 1")),
            ("customers searched", q("SELECT COUNT(*) FROM match")),
        ]
