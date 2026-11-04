"""Finding a customer in the news.

Two signals per customer:

* Headlines. The customer's name, without its legal form, must stand in a
  headline as consecutive words ("Qatar Steel Industries Factory"), each word
  compared with the rules of namematch.py (plural, glued spelling, one letter
  off in a long word). A one-word name ("Delta") is searched with the word
  after it on the register ("Delta Corporation"): a single word in a headline
  is too often something else. The same headline in two papers, or twice in
  one, counts once.
* Gulf Times' own search, which reads the whole article text, for the exact
  phrase. Its count is what Gulf Times says ("(12 articles)"); the titles on
  its first result page also feed the negative-word count.

The generic-name rule. A short name (three words or fewer, counting words of
three letters or more) made only of common words -- "Doha Bus", "Qatar Solar
Energy" -- appears in headlines that are not about the company ("new Doha bus
routes"). For such a name no news is counted: every news column is empty and
`news_name_generic` = 1, for a person to look at. A word is common when The
Peninsula, whose headlines keep their capitals, writes it in lower case at
least half of the times it appears (and it appears at least 5 times, the
first word of a headline not counted): "bus", "solar", "energy" are common;
"Barzan", "Oryx" are not. A longer name of common words ("Qatar Steel
Industries Factory") is specific as a whole phrase and is searched.
"""

from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from .namematch import GENERIC, _same_word, norm, search_name

PENINSULA = "The Peninsula"
COMMON_MIN_SEEN = 5        # a word seen fewer times in Peninsula headlines is not judged
COMMON_LOWER_SHARE = 0.5   # ... and is common when at least half of them are in lower case
EXAMPLES = 3

# Whole words: "fire" but not "fireworks", "court" is not in it (a "food court" is not a lawsuit).
NEGATIVE = re.compile(r"\b(?:fire|blaze|closed|closure|shut ?down|fined|violations?|lawsuits?|sued|bankrupt\w*|"
                      r"liquidat\w*|default(?:ed|s)?|fraud\w*|arrest\w*|recalls?|recalled|penalt\w*|"
                      r"suspend\w*|disputes?)\b", re.I)

HEADERS = {  # accepted column names, as in the customer books used by icv and monaqasat
    "customer_id": {"customer_id", "id", "client_id"},
    "name": {"name", "company_name", "company", "customer_name"},
    "cr_number": {"cr_number", "cr", "cr_no", "commercial_registration"},
    # optional: how to search when the registered name is not the trading name
    "search_name": {"search_name"},
}


def load_customers(path: str | Path) -> list[dict[str, str]]:
    """customer_id, name, cr_number (and search_name) from a CSV; a missing customer_id becomes the row number."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return []
    cols = {field: next((h for h in rows[0] if h and h.strip().lower() in names), None)
            for field, names in HEADERS.items()}
    if not cols["name"]:
        raise ValueError(f"{Path(path).name}: no name column (accepted: {sorted(HEADERS['name'])})")
    cell = lambda r, field: (r.get(cols[field]) or "").strip() if cols[field] else ""
    return [{"customer_id": cell(r, "customer_id") or str(i), "name": cell(r, "name"),
             "cr_number": cell(r, "cr_number"), "search_name": cell(r, "search_name")}
            for i, r in enumerate(rows, start=1) if cell(r, "name")]


def query_for(customer: dict[str, str]) -> str:
    return customer.get("search_name") or search_name(customer["name"])


# ---- words ------------------------------------------------------------------

def words(text: str) -> list[str]:
    """'QDB joins building-materials expo' -> ['QDB', 'JOINS', 'BUILDING', 'MATERIALS', 'EXPO']"""
    return re.sub(r"[^A-Z0-9 ]", " ", str(text).upper()).split()


def phrase(name: str) -> list[str]:
    """The words that must stand together: the name without its legal form; if that
    leaves one word ("Delta"), the whole name ("Delta Corporation")."""
    ws = norm(name).split()
    if len(ws) < 2:
        raw = words(name)
        ws = raw if len(raw) >= 2 else ws
    return ws


def anchor(ws: list[str]) -> str:
    """The longest distinctive word: headlines are first narrowed to those with a word starting with it."""
    return max((w for w in ws if w not in GENERIC), key=len, default=ws[0] if ws else "")


def mentions(ws: list[str], title_words: list[str]) -> bool:
    n, ours = len(ws), set(ws)
    return n > 0 and any(all(_same_word(w, title_words[i + k], ours, True) for k, w in enumerate(ws))
                         for i in range(len(title_words) - n + 1))


def common_words(peninsula_titles: Iterable[str]) -> set[str]:
    low: Counter[str] = Counter()
    total: Counter[str] = Counter()
    for t in peninsula_titles:
        for w in re.findall(r"[A-Za-z]+", t)[1:]:      # the first word of a headline is always capitalised
            total[w.upper()] += 1
            low[w.upper()] += w.islower()
    return {w for w, n in total.items() if n >= COMMON_MIN_SEEN and low[w] / n >= COMMON_LOWER_SHARE}


def is_generic(name: str, common: set[str]) -> bool:
    """A short name (up to 3 words) of common words only -- news found for it is unreliable.
    A longer phrase of common words ("Qatar Steel Industries Factory") is specific as a phrase."""
    ws = [w for w in norm(name).split() if len(w) > 2]
    distinctive = [w for w in ws if w not in GENERIC]
    return not distinctive or (len(ws) <= 3 and all(w in common for w in distinctive))


# ---- the headline index, narrowed to the customers' anchor words -------------

@dataclass
class Headlines:
    common: set[str] = field(default_factory=set)
    peninsula_titles: int = 0
    by_anchor: dict[str, list[tuple[str, str, str, str, tuple[str, ...]]]] = field(default_factory=dict)

    @classmethod
    def build(cls, rows: Iterable[Any], anchors: set[str]) -> "Headlines":
        """One pass over the article table: The Peninsula's cased headlines give the common
        words, and every headline with a word starting with one of the anchors is kept."""
        anchors = {a for a in anchors if a}
        lengths = sorted({len(a) for a in anchors})
        hit_memo: dict[str, tuple[str, ...]] = {}
        by_anchor: dict[str, list] = defaultdict(list)
        peninsula: list[str] = []
        for paper, day, title, url in rows:
            if paper == PENINSULA:
                peninsula.append(title)
            tw = words(title)
            found: set[str] = set()
            for w in tw:
                hits = hit_memo.get(w)
                if hits is None:
                    hits = hit_memo[w] = tuple(w[:n] for n in lengths if w[:n] in anchors)
                found.update(hits)
            for a in found:
                by_anchor[a].append((paper, day or "", title, url, tuple(tw)))
        return cls(common_words(peninsula), len(peninsula), dict(by_anchor))

    def find(self, name: str) -> tuple[list[tuple[str, str, str, str]], set[str]]:
        """(paper, date, title, url) of every headline that names the company, newest first, the same
        words counted once; and every paper that carried one (a headline two papers ran counts for both)."""
        ws = phrase(name)
        seen, out, papers = set(), [], set()
        for paper, day, title, url, tw in sorted(self.by_anchor.get(anchor(ws), []), key=lambda h: (h[1], h[3]),
                                                 reverse=True):
            if mentions(ws, list(tw)):
                papers.add(paper)
                if tw not in seen:
                    seen.add(tw)
                    out.append((paper, day, title, url))
        return out, papers


# ---- one row per customer ---------------------------------------------------

def _days_ago(day: str, today: date) -> int | None:
    try:
        return (today - date.fromisoformat(day)).days
    except ValueError:
        return None


def match_one(customer: dict[str, str], headlines: Headlines, search: tuple[int, list[str]] | None,
              today: date | None = None) -> dict[str, Any]:
    """`search` is the Gulf Times result (count, titles), or None when it was not done or failed:
    then `gt_articles` is empty, not 0."""
    today = today or date.today()
    q = query_for(customer)
    row = {"customer_id": customer["customer_id"], "customer_name": customer["name"],
           "cr_number": customer["cr_number"], "search_name": q, "status": "generic", "generic": 1,
           "gt_articles": None, "headlines": None, "headlines_365d": None, "papers": None,
           "days_since_last": None, "negative": None, "examples": None}
    if is_generic(q, headlines.common):
        return row
    hits, papers = headlines.find(q)
    days = [d for d in (_days_ago(h[1], today) for h in hits) if d is not None]
    negative = {" ".join(words(t)) for t in (search[1] if search else []) if NEGATIVE.search(t)}
    negative |= {" ".join(words(h[2])) for h in hits if NEGATIVE.search(h[2])}
    gt = search[0] if search else None
    row.update(generic=0, gt_articles=gt, headlines=len(hits), headlines_365d=sum(d <= 365 for d in days),
               papers=len(papers), days_since_last=min(days) if days else None,
               negative=len(negative),
               examples=" | ".join(f"{h[1]} {h[0]}: {h[2]}" for h in hits[:EXAMPLES]) or None,
               status="found" if hits or gt else "not_found")
    row["_urls"] = " ".join(h[3] for h in hits[:EXAMPLES]) or None
    return row


def features(row: dict[str, Any], search_url: str | None) -> dict[str, Any]:
    """Model-ready columns for one customer: empty (not zero) where nothing is known."""
    return {"customer_id": row["customer_id"], "customer_name": row["customer_name"], "cr_number": row["cr_number"],
            "search_name": row["search_name"],
            "news_gulf_times_articles": row["gt_articles"],
            "news_headlines": row["headlines"],
            "news_headlines_365d": row["headlines_365d"],
            "news_papers": row["papers"],
            "news_days_since_last": row["days_since_last"],
            "news_negative_count": row["negative"],
            "news_name_generic": row["generic"],
            "example_headlines": row["examples"],
            "example_urls": row.get("_urls"),
            "gulf_times_search_url": search_url}
