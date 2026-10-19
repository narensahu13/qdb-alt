"""SQLite store: licensed operators, customer matches, and a log of runs.

Each `load` replaces the list with today's and remembers when every company
was first and last seen, so a company whose licence is withdrawn stays in the
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
CREATE TABLE IF NOT EXISTS operator (
    id            INTEGER PRIMARY KEY,     -- the SharePoint list item id
    name          TEXT NOT NULL,
    name_ar       TEXT,
    licences      TEXT,                    -- JSON list: Clearing Agencies, Shipping Agencies, ...
    website       TEXT,
    listed_since  TEXT,                    -- when the item was created on the list
    modified      TEXT,
    listed        INTEGER NOT NULL DEFAULT 1,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS match (
    customer_id   TEXT PRIMARY KEY,
    customer_name TEXT,
    cr_number     TEXT,
    operator_id   INTEGER,                 -- NULL: searched, not on the list
    operator_name TEXT,
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

    def replace_list(self, operators: list[dict[str, Any]]) -> tuple[int, int]:
        """Today's list. Returns (on the list, dropped since last load)."""
        stamp = now()
        ids = {o["id"] for o in operators}
        dropped = [i for (i,) in self.db.execute("SELECT id FROM operator WHERE listed = 1") if i not in ids]
        self.db.executemany("UPDATE operator SET listed = 0 WHERE id = ?", [(i,) for i in dropped])
        for o in operators:
            self.db.execute(
                """INSERT INTO operator (id, name, name_ar, licences, website, listed_since, modified,
                                         listed, first_seen, last_seen)
                   VALUES (:id, :name, :name_ar, :licences, :website, :listed_since, :modified, 1, :stamp, :stamp)
                   ON CONFLICT (id) DO UPDATE SET
                       name = excluded.name, name_ar = excluded.name_ar, licences = excluded.licences,
                       website = excluded.website, modified = excluded.modified, listed = 1,
                       last_seen = excluded.last_seen""",
                {**o, "licences": json.dumps(o["licences"]), "stamp": stamp})
        return len(operators), len(dropped)

    def operators(self) -> list[dict[str, Any]]:
        out = []
        for r in self.db.execute("SELECT * FROM operator WHERE listed = 1 ORDER BY name"):
            d = dict(r)
            d["licences"] = json.loads(d["licences"] or "[]")
            out.append(d)
        return out

    def save_matches(self, rows: list[dict[str, Any]], run_id: int) -> int:
        stamp = now()
        self.db.executemany(
            """INSERT OR REPLACE INTO match (customer_id, customer_name, cr_number, operator_id, operator_name,
                   run_id, matched_at)
               VALUES (:customer_id, :customer_name, :cr_number, :operator_id, :operator_name, :run_id, :matched_at)""",
            [{**r, "run_id": run_id, "matched_at": stamp} for r in rows])
        return len(rows)

    def stats(self) -> list[tuple[str, Any]]:
        q = lambda sql: self.db.execute(sql).fetchone()[0]
        return [
            ("licensed operators", q("SELECT COUNT(*) FROM operator WHERE listed = 1")),
            ("no longer listed", q("SELECT COUNT(*) FROM operator WHERE listed = 0")),
            ("customers on the list", q("SELECT COUNT(*) FROM match WHERE operator_id IS NOT NULL")),
            ("customers searched", q("SELECT COUNT(*) FROM match")),
        ]
