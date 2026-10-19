"""SQLite store: the directory's profiles, customer matches, and a log of runs.

Every command runs inside `Store.run(...)`, which records when it started and
finished, how long it took, how many rows it wrote, and -- if it failed --
the error. `python -m qdb_sme_directory runs` prints that log.
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
CREATE TABLE IF NOT EXISTS profile (
    edition       TEXT NOT NULL,
    page          INTEGER NOT NULL,
    name          TEXT,
    titles        TEXT,              -- JSON: other title-like lines on the page
    website       TEXT,
    founded_year  INTEGER,
    hs_codes      TEXT,              -- JSON list
    hs_count      INTEGER NOT NULL DEFAULT 0,
    exporter      INTEGER NOT NULL DEFAULT 0,
    products      TEXT,              -- JSON list
    run_id        INTEGER,
    PRIMARY KEY (edition, page)
);
CREATE TABLE IF NOT EXISTS match (
    customer_id   TEXT PRIMARY KEY,
    customer_name TEXT,
    cr_number     TEXT,
    edition       TEXT,              -- NULL: searched, not in the directory
    page          INTEGER,
    profile_name  TEXT,
    score         REAL,
    method        TEXT,              -- name | title line
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
        recorded with its traceback and re-raised."""
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

    # ---- profiles ----------------------------------------------------------

    def save_profiles(self, profiles: list[dict[str, Any]], run_id: int) -> int:
        self.db.executemany(
            """INSERT OR REPLACE INTO profile (edition, page, name, titles, website, founded_year,
                   hs_codes, hs_count, exporter, products, run_id)
               VALUES (:edition, :page, :name, :titles, :website, :founded_year,
                   :hs_codes, :hs_count, :exporter, :products, :run_id)""",
            [{**p, "titles": json.dumps(p["titles"], ensure_ascii=False),
              "hs_codes": json.dumps(p["hs_codes"]), "hs_count": len(p["hs_codes"]),
              "products": json.dumps(p["products"], ensure_ascii=False), "run_id": run_id} for p in profiles])
        return len(profiles)

    def profiles(self) -> list[dict[str, Any]]:
        out = []
        for r in self.db.execute("SELECT * FROM profile ORDER BY edition, page"):
            d = dict(r)
            for k in ("titles", "hs_codes", "products"):
                d[k] = json.loads(d[k] or "[]")
            out.append(d)
        return out

    # ---- matches -----------------------------------------------------------

    def save_matches(self, rows: list[dict[str, Any]], run_id: int) -> int:
        stamp = now()
        self.db.executemany(
            """INSERT OR REPLACE INTO match (customer_id, customer_name, cr_number, edition, page,
                   profile_name, score, method, run_id, matched_at)
               VALUES (:customer_id, :customer_name, :cr_number, :edition, :page,
                   :profile_name, :score, :method, :run_id, :matched_at)""",
            [{**r, "run_id": run_id, "matched_at": stamp} for r in rows])
        return len(rows)

    def stats(self) -> list[tuple[str, Any]]:
        q = lambda sql: self.db.execute(sql).fetchone()[0]
        return [
            ("profiles", q("SELECT COUNT(*) FROM profile")),
            ("  by edition", ", ".join(f"{e}: {n}" for e, n in
                                       self.db.execute("SELECT edition, COUNT(*) FROM profile GROUP BY 1"))),
            ("  with a name", q("SELECT COUNT(*) FROM profile WHERE name IS NOT NULL")),
            ("  with a website", q("SELECT COUNT(*) FROM profile WHERE website IS NOT NULL")),
            ("  with a founding year", q("SELECT COUNT(*) FROM profile WHERE founded_year IS NOT NULL")),
            ("customers matched", q("SELECT COUNT(*) FROM match WHERE page IS NOT NULL")),
            ("customers searched", q("SELECT COUNT(*) FROM match")),
        ]
