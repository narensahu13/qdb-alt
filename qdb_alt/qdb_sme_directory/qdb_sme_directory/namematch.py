"""Matching a customer's name to a name found in a source.

The same rules are used by every name-searched source in this repository, so
that a match means the same thing everywhere. They are deliberately strict: a
missed company is a blank feature, a wrong company is a wrong feature.

* Every distinctive word of the customer's name (not QATAR, FACTORY, GROUP ...)
  must appear in the candidate.
* The first distinctive word -- the brand -- must match exactly, allowing only
  a plural, a glued spelling (WESTBAY, ALHAMMAD), an abbreviation at the start
  (EXTRA = EXTRACO) or one letter in a long word (ALUMINUM / ALUMINIUM).
  MANA is not DANA, NATIONAL is not INTERNATIONAL, NOOR is not ABDULNOOR.
* A foreign brand in front of ours is not us: "Abdul Noor Aluminum Extrusion"
  is not "Qatar Aluminum Extrusion".
* Text in brackets on the candidate is checked on its own:
  "Aseel Water (Westbay Ice & Water Factory)" is West Bay Ice.
"""

from __future__ import annotations

import re
from functools import lru_cache

from rapidfuzz import fuzz

# Legal forms and stop words that say nothing about which company it is.
LEGAL = {"WLL", "W", "L", "LLC", "CO", "COMPANY", "EST", "ESTABLISHMENT", "CORP", "CORPORATION",
         "QPSC", "QSC", "QPJSC", "QFZ", "LTD", "LIMITED", "THE", "AND", "FOR", "OF", "TRADING"}

# Words shared by thousands of Qatari company names; on their own they prove nothing.
GENERIC = {"QATAR", "DOHA", "AL", "GROUP", "ENTERPRISES", "INDUSTRIES", "INDUSTRY", "INDUSTRIAL", "FACTORY",
           "FACTORIES", "MANUFACTURING", "SERVICES", "INTERNATIONAL", "PROJECTS", "HOLDING", "CONTRACTING",
           "GENERAL", "MATERIALS"}


@lru_cache(maxsize=None)
def norm(name: str | None) -> str:
    """'QATAR STEEL INDUSTRIES FACTORY (QSIF) W.L.L' -> 'QATAR STEEL INDUSTRIES FACTORY'"""
    s = re.sub(r"['’`]", "", str(name or "").upper()).replace("&", " AND ")  # S'HAIL -> SHAIL
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    return " ".join(w for w in s.split() if w not in LEGAL)


def similarity(a: str | None, b: str | None) -> float:
    """0..100, word order ignored."""
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0
    return fuzz.token_sort_ratio(a, b)


@lru_cache(maxsize=None)
def _generic(w: str) -> bool:
    """A common word, typos included (FACORY = FACTORY)."""
    return w in GENERIC or (len(w) >= 5 and any(fuzz.ratio(w, g) >= 85 for g in GENERIC))


@lru_cache(maxsize=None)
def special_words(name: str) -> tuple[str, ...]:
    return tuple(w for w in norm(name).split() if not _generic(w) and len(w) > 1)


def _consonants(w: str) -> str:
    """Arabic names in Latin letters vary in vowels: RAHALAT / REHLAT -> RHLT."""
    return w[:1] + re.sub(r"[AEIOUY]", "", w[1:])


def _same_word(w: str, c: str, ours: set[str], strict: bool) -> bool:
    if w == c:
        return True
    base = c[:-1] if c.endswith("S") and len(c) > len(w) else c          # PLASTICS
    if base == w or (c.endswith("ES") and c[:-2] == w):
        return True
    if base.startswith(w) and (base[len(w):] in ours or len(w) >= (5 if strict else 3)):
        return True                                                        # WESTBAY, EXTRACO, ALUMINUM
    if base.endswith(w) and base[:-len(w)] in ours | {"AL"}:
        return True                                                        # WESTBAY, ALHAMMAD
    if len(w) >= 6 and c[:1] == w[:1] and abs(len(c) - len(w)) <= 1 and fuzz.ratio(w, c) >= 90:
        return True                                                        # ALUMINUM / ALUMINIUM
    return not strict and len(w) >= 5 and fuzz.ratio(w, c) >= 85          # RECYCLING / RECYLING


def _foreign(c: str, ours: set[str]) -> bool:
    """A word of the candidate that is someone else's brand."""
    return len(c) > 2 and not _generic(c) and not any(_same_word(o, c, ours, False) for o in ours)


def brand_match(me: str, candidate: str) -> bool:
    """Our brand word is in the candidate, with no foreign brand before it.
    Allows spelling variants of the brand (HUDAIFI / HODAIFI, RAHALAT / REHLAT)."""
    special = special_words(me)
    if not special:
        return False
    w, ours = special[0], set(norm(me).split())
    words = norm(candidate).split()
    for i, c in enumerate(words):
        if (_same_word(w, c, ours, strict=True)
                or (len(w) >= 5 and c[:1] == w[:1] and fuzz.ratio(w, c) >= 85)
                or (len(w) >= 5 and _consonants(w) == _consonants(c))):
            return not any(_foreign(x, ours) for x in words[:i])
    return False


def _same_company(me: str, candidate: str) -> bool:
    a, b = norm(me), norm(candidate)
    special = special_words(me)
    if not special or not b:
        return False
    words, ours = b.split(), set(a.split())
    if not all(any(_same_word(w, c, ours, k == 0) for c in words) for k, w in enumerate(special)):
        return False
    first = next(i for i, c in enumerate(words) if _same_word(special[0], c, ours, True))
    if any(_foreign(c, ours) for c in words[:first]):
        return False
    if b.startswith(a + " ") and len(a.split()) >= 2:   # a branch or campus: "Doha Academy Al Waab Campus"
        return True
    if len(special) == 1:                                 # one distinctive word: nearly the same name
        return not any(_foreign(c, ours) for c in words) and fuzz.token_sort_ratio(a, b) >= 75
    return fuzz.token_sort_ratio(a, b) >= 50


def same_company(me: str, candidate: str) -> bool:
    """Strict check for a name-only source (see the module docstring)."""
    variants = [candidate] + re.findall(r"\((.*?)\)", str(candidate or ""))
    return any(_same_company(me, v) for v in variants)


def best_match(name: str, candidates, key=lambda c: c, threshold: float = 88):
    """The most similar candidate that carries our brand word: (candidate, score) or (None, score)."""
    best, score = None, 0.0
    for c in candidates:
        s = similarity(name, key(c))
        if s > score and brand_match(name, key(c)):
            best, score = c, s
    return (best, score) if score >= threshold else (None, score)


# Legal form at the end of a name: W.L.L, L.L.C, Q.P.S.C, Ltd, Company, Est. ...
LEGAL_SUFFIX = re.compile(r"[\s,./-]*\b(W\s*\.?\s*L\s*\.?\s*L|L\s*\.?\s*L\s*\.?\s*C|Q\s*\.?\s*P?\s*\.?\s*S\s*\.?\s*C"
                          r"|EST|CO|CORP|LLC|LTD|LIMITED|COMPANY)\b[\s,./-]*$", re.I)


def search_name(name: str) -> str:
    """The name to search for: no brackets, no trailing legal form.

    Customer lists exported from fixed-width systems wrap long names at 40
    characters with a space inserted mid-word ("PLASTIC PROD UCTS"); that one
    space is removed."""
    if len(name) > 41 and name[40] == " " and name[39] != " " and name[41] != " ":
        name = name[:40] + name[41:]
    s = re.sub(r"\(.*?\)", " ", name)
    while LEGAL_SUFFIX.search(s) and LEGAL_SUFFIX.sub("", s).strip():
        s = LEGAL_SUFFIX.sub("", s)
    return " ".join(s.replace(",", " ").split()).title()
