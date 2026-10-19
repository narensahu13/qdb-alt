"""SQLite store: sanctioned entities and their names, screening results, and a log of runs.

Each `load` replaces the lists with today's in one transaction and remembers
when every entity was first and last seen; an entity that is delisted stays in
the database, marked as no longer listed, with the date it was last seen.
Every command runs inside `Store.run(...)`, which records start, finish,
duration, rows, a note and -- if it failed -- the error.
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
CREATE TABLE IF NOT EXISTS entity (
    list          TEXT NOT NULL,          -- UN | OFAC SDN | OFAC non-SDN | EU | UK
    ref           TEXT NOT NULL,          -- the list's own reference
    name          TEXT NOT NULL,
    programme     TEXT,
    listed_on     TEXT,
    listed        INTEGER NOT NULL DEFAULT 1,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    PRIMARY KEY (list, ref)
);
CREATE TABLE IF NOT EXISTS alias (
    list          TEXT NOT NULL,
    ref           TEXT NOT NULL,
    name          TEXT NOT NULL           -- the main name and every alias
);
CREATE INDEX IF NOT EXISTS alias_entity ON alias (list, ref);
CREATE TABLE IF NOT EXISTS screening (
    customer_id   TEXT PRIMARY KEY,
    customer_name TEXT,
    cr_number     TEXT,
    hit           INTEGER NOT NULL,
    lists         TEXT,
    matched_name  TEXT,
    best_score    REAL,
    run_id        INTEGER,
    screened_at   TEXT NOT NULL
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

    def replace_list(self, lst: str, entities: list[dict[str, Any]]) -> tuple[int, int]:
        """Today's entities for one list. Returns (listed, delisted since last load)."""
        stamp = now()
        refs = {e["ref"] for e in entities}
        gone = [r for (r,) in self.db.execute("SELECT ref FROM entity WHERE list = ? AND listed = 1", (lst,))
                if r not in refs]
        self.db.executemany("UPDATE entity SET listed = 0 WHERE list = ? AND ref = ?", [(lst, r) for r in gone])
        self.db.execute("DELETE FROM alias WHERE list = ?", (lst,))
        for e in entities:
            self.db.execute(
                """INSERT INTO entity (list, ref, name, programme, listed_on, listed, first_seen, last_seen)
                   VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                   ON CONFLICT (list, ref) DO UPDATE SET name = excluded.name, programme = excluded.programme,
                       listed_on = excluded.listed_on, listed = 1, last_seen = excluded.last_seen""",
                (lst, e["ref"], e["name"], e["programme"], e["listed_on"], stamp, stamp))
            self.db.executemany("INSERT INTO alias (list, ref, name) VALUES (?, ?, ?)",
                                [(lst, e["ref"], n) for n in [e["name"], *e["aliases"]]])
        return len(entities), len(gone)

    def names(self) -> list[tuple[str, str, str]]:
        """(list, ref, name) for every name and alias of every listed entity."""
        return [tuple(r) for r in self.db.execute(
            """SELECT a.list, a.ref, a.name FROM alias a
               JOIN entity e ON e.list = a.list AND e.ref = a.ref WHERE e.listed = 1""")]

    def save_screening(self, rows: list[dict[str, Any]], run_id: int) -> int:
        stamp = now()
        self.db.executemany(
            """INSERT OR REPLACE INTO screening (customer_id, customer_name, cr_number, hit, lists, matched_name,
                   best_score, run_id, screened_at)
               VALUES (:customer_id, :customer_name, :cr_number, :sanctions_hit, :sanctions_lists,
                   :matched_name, :best_score, :run_id, :screened_at)""",
            [{**r, "run_id": run_id, "screened_at": stamp} for r in rows])
        return len(rows)

    def stats(self) -> list[tuple[str, Any]]:
        out = [(f"{lst}", n) for lst, n in self.db.execute(
            "SELECT list, COUNT(*) FROM entity WHERE listed = 1 GROUP BY list ORDER BY list")]
        q = lambda sql: self.db.execute(sql).fetchone()[0]
        return out + [("names and aliases", q("SELECT COUNT(*) FROM alias")),
                      ("delisted (kept)", q("SELECT COUNT(*) FROM entity WHERE listed = 0")),
                      ("customers with a hit", q("SELECT COUNT(*) FROM screening WHERE hit = 1")),
                      ("customers screened", q("SELECT COUNT(*) FROM screening"))]
