"""From the site's sitemap and judgment pages to one record per judgment.

robots.txt on qicdrc.gov.qa disallows the paging and sorting query strings of
the judgments list (`/*?page=*&`, `/*?sort=` ...), so the judgments are read
the way a search engine reads them: the sitemap lists every judgment page with
its last-modified date (English pages under /judgments/, Arabic ones under
/ar/judgments/ -- only the English are read), and each page carries

    <title> / og:title      "CTAD0006/2021 - A LLC against B LLC, C LLC and D LLC"
                            (the parties in full -- on some pages; others give
                            only the case number and citation)
    <h2 class="page-title"> "A LLC v B LLC and Others"
    court, case number, neutral citation, date, keywords
                            in the page body and again in a schema.org
                            "Report" (JSON-LD), which is read first

The text of the judgment is a PDF, which is not read.

Parties: the first named side is called the claimant and the second the
defendant; on an appeal that is the appellant and the respondent. Only parties
that are evidently organisations are kept by name. People who are parties --
employees suing for their salary, guarantors, sheikhs -- are only counted:
the court publishes their names, this database does not need them.
"""

from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from .namematch import norm

SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
HOST = "www.qicdrc.gov.qa"
JUDGMENT_PATH = re.compile(r"^/judgments/[^/]+/?$")      # English only; Arabic is /ar/judgments/...


class ParseError(ValueError):
    pass


# ---- sitemap -----------------------------------------------------------------

def sitemap(xml_text: str) -> tuple[list[tuple[str, str]], list[str]]:
    """(judgment pages as (url, lastmod), child sitemaps). A plain sitemap has no
    children; a sitemap index has only children."""
    root = ET.fromstring(xml_text.encode("utf-8"))
    children = [(s.findtext(f"{SITEMAP_NS}loc") or "").strip() for s in root.iter(f"{SITEMAP_NS}sitemap")]
    pages = []
    for u in root.iter(f"{SITEMAP_NS}url"):
        loc = (u.findtext(f"{SITEMAP_NS}loc") or "").strip()
        parts = urlsplit(loc)
        if parts.netloc == HOST and JUDGMENT_PATH.match(parts.path) and not parts.query:
            pages.append((loc, (u.findtext(f"{SITEMAP_NS}lastmod") or "").strip()))
    return pages, [c for c in children if c]


# ---- parties -----------------------------------------------------------------

SIDES = re.compile(r"\s+(?:v|vs|versus|against)\.?\s+", re.I)
OTHERS = re.compile(r"[,\s]*(?:\band|&)\s+(?:an)?(?:others?|ors)\b\.?|\bet\s+al\b\.?", re.I)
NUMBERED = re.compile(r"\(\s*\d{1,2}\s*\)")                # (1) A (2) B
AND = re.compile(r"\s+and\s+", re.I)
# 'Re ...', 'In the Matter of ...', 'Practice Direction No. 1 ... In the Matters of ...'
MATTER_OF = re.compile(r"^(?:re\b:?\s*)?(?:.*?\bin\s+the\s+(?:matters?|liquidation|winding[\s-]up)\s+of\s+)?", re.I)
# '(on appeal from ...)', '(Permission to Appeal Application)', '- Corrigendum': about the judgment, not a party
NOTE = re.compile(r"\s*\([^()]*\b(?:appeal|judgment|concerning|applications?)\b[^()]*\)|\s+-\s+corrigendum\b"
                  r"|\s*\(\s*in\s+liquidation\b[^()]*\)?", re.I)
# a case number inside a heading that names several cases: 'X v Y and CTFIC0033/2024 Z v Y'
CASE_TOKEN = re.compile(r"(?:(?i:case\s+no)\.?\s*:?\s*)?\b[A-Z]{2,}\s?\d{3,}\s?/\s?\d{4}\b\s*-?"
                        r"|(?i:case\s+no)\.?\s*:?\s*\d+\s+of\s+\d{4}\b\s*-?")
EDGES = re.compile(r"^(?:(?:and|&|v|vs)\.?(?:\s+|$)|[-,:\s]+)+|(?:\s+(?:and|&|v|vs)\.?|[-,:\s]+)+$", re.I)

# Words that make a party an organisation. A person's name has none of them.
ORG_WORDS = re.compile(r"\b(?:" + "|".join("""
    L\\.?\\s?L\\.?\\s?C W\\.?\\s?L\\.?\\s?L LTD LIMITED CO COMPANY INC INCORPORATED CORP CORPORATION PLC LLP LP
    Q\\.?P?\\.?S\\.?C QSC QPSC QFZ QSTP S\\.?A\\.?L? SPC GMBH AG BV NV FZE FZCO FZ DMCC SONS BROS BROTHERS
    BRANCH BANK BANKING AUTHORITY CENTRE CENTER GROUP HOLDINGS? TRADING CONTRACTING SERVICES SOLUTIONS?
    TECHNOLOGY TECHNOLOGIES ENGINEERING CONSULTANTS? CONSULTANCY CONSULTING PARTNERS PARTNERSHIP ASSOCIATES
    LAWYERS ADVOCATES FIRM INSURANCE REINSURANCE ASSURANCE INVESTMENTS? CAPITAL FUND PROPERTIES REAL\\s+ESTATE
    DEVELOPMENT DEVELOPMENTS ESTABLISHMENT EST ENTERPRISES? INDUSTRIES INDUSTRIAL FACTORY INTERNATIONAL MINISTRY
    COUNCIL AGENCY OFFICE INSTITUTE UNIVERSITY COLLEGE SCHOOL ACADEMY HOSPITAL CLINIC MEDICAL HEALTHCARE
    HOTELS? RESIDENCES RESTAURANTS? CAF[EÉ] STUDIOS? FILMS PTE S\\.?P\\.?A MIDDLE\\s+EAST MEDIA SYSTEMS LOGISTICS
    SHIPPING MARINE TRAVEL TOURS CARGO EXCHANGE FINANCE FINANCIAL SECURITIES BROKERAGE MANAGEMENT MARKETING
    ADVERTISING CONSTRUCTION ELECTRICAL MECHANICAL PETROLEUM
    ENERGY TELECOM TRUST FOUNDATION ASSOCIATION CLUB VENTURES GLOBAL PROJECTS SUPPLIES EQUIPMENT MOTORS
    PHARMACY PHARMACEUTICALS LABORATORIES NETWORKS DIGITAL SOFTWARE PRODUCTIONS EVENTS ARCHITECTS
    QFCRA QFCA QFC QFZA""".split()) + r")\b\.?", re.I)
# A legal form or an institution word at the end: "X LLC and Y" is two parties.
ENDS_AS_ORG = re.compile(r"\b(?:L\.?\s?L\.?\s?C|W\.?\s?L\.?\s?L|LTD|LIMITED|CO|COMPANY|INC|CORP|CORPORATION|PLC|LLP"
                         r"|Q\.?P?\.?S\.?C|QSC|QPSC|QFZ|GMBH|FZE|FZCO|DMCC|BRANCH|BANK|AUTHORITY|QFCRA|QFCA)\.?\)?$",
                         re.I)
PERSON_WORDS = re.compile(r"\b(?:MR|MRS|MS|MISS|DR|SHEIKH|SHEIKHA|SHAIKH|SHAIKHA|BIN|BINT|IBN|H\.\s?E)\b\.?", re.I)
ACRONYM = re.compile(r"\b[A-Z][A-Z&]{2,}\b")
NOT_ACRONYMS = {"MRS", "MISS", "AND", "THE", "III", "VII", "VIII"}


def is_organisation(name: str) -> bool:
    """'Meinhardt Bim Studios LLC', 'QFCRA', 'Gulf GRC and Trading Co' yes; 'DILIP KUMAR KUNDU' no,
    nor the initials the court gives an anonymised party ('A', 'MML'). An all-capitals word counts
    as an acronym only in a name that is not written all in capitals."""
    if ORG_WORDS.search(name):
        return True
    acronyms = [a for a in ACRONYM.findall(name) if a not in NOT_ACRONYMS]
    return bool(acronyms) and name.upper() != name


def _split_and(chunk: str) -> list[str]:
    """'Trimoo Parks LLC and ADABISC LLC' -> two; 'Al Wajba Dairy and Juices Factory' and
    'John and Wiedeman LLC' -> one (the company's own name); 'Mr A and Mr B', 'X Factory and
    Sheikh Y', 'X Office and Some Person' -> two."""
    pieces = AND.split(chunk)
    out = [pieces[0]]
    for nxt in pieces[1:]:
        cur = out[-1]
        joined = f"{cur} and {nxt}"
        if (ENDS_AS_ORG.search(cur) or PERSON_WORDS.search(cur) or PERSON_WORDS.search(nxt)
                or (is_organisation(cur) and not is_organisation(nxt))
                or not (is_organisation(cur) or is_organisation(joined))):
            out.append(nxt)
        else:
            out[-1] = joined
    return out


def split_parties(side: str) -> list[str]:
    """One side of a case name to its parties, without '... and Others'."""
    side = NUMBERED.sub(",", side)
    side = OTHERS.sub(" ", side)
    names = []
    for chunk in re.split(r"[,;]", side):
        chunk = EDGES.sub("", " ".join(chunk.split())).strip(" .")
        if chunk:
            names.extend(p for p in (EDGES.sub("", x).strip(" .") for x in _split_and(chunk)) if p)
    return names


def split_case_name(name: str) -> list[tuple[str, str]]:
    """'A LLC v B LLC and Others' -> [('claimant', 'A LLC'), ('defendant', 'B LLC')].
    With no 'v' / 'against' every name is just a 'party'. A heading that joins several
    cases ('A v B; C v D', or a case number between them) is read case by case."""
    name = " ".join(html.unescape(name or "").split())
    name = CASE_TOKEN.sub(";", CITATION.sub(" ", NOTE.sub(" ", name)))
    out = []
    for case in name.split(";"):
        case = EDGES.sub("", MATTER_OF.sub("", EDGES.sub("", case.strip())))
        if not case:
            continue
        sides = SIDES.split(NUMBERED.sub(", ", case))
        if len(sides) == 1:
            out += [("party", p) for p in split_parties(sides[0])]
            continue
        out += [("claimant", p) for p in split_parties(sides[0])] + [("defendant", p) for p in split_parties(sides[-1])]
        for middle in sides[1:-1]:   # 'A v B and C v D': B and C cannot be told apart
            out += [("party", p) for p in split_parties(middle)]
    return out


CASE_PREFIX = re.compile(r"^\s*(?:Case\s+No\.?\s*:?\s*)?(?:[A-Z]{3,}\s?\d+\s?/\s?\d{4}|\d+\s+of\s+\d{4})"
                         r"\s*(?:[-:]\s*)?", re.I)
CITATION = re.compile(r"\[\d{4}\]\s*QIC\s*(?:\([A-Z]+\))?\s*\d+")


def title_parties(title: str) -> str:
    """The parties part of a page title, or '' when the title is only a case number and a citation."""
    t = html.unescape(title or "").split(" | ")[0]
    t = CITATION.sub(" ", CASE_PREFIX.sub("", t)).strip(" -:")
    return t if SIDES.search(t) else ""


# ---- outcome -----------------------------------------------------------------

# Keywords the court itself tags a judgment with that state how it ended. Nothing
# else is taken as an outcome: the summary on the page is machine-written (the
# site says so) and a keyword like "Summary judgment" names a procedure, not a winner.
OUTCOMES = {
    "appeal dismissed": "appeal dismissed",
    "dismissal of appeal": "appeal dismissed",
    "acceptance of the appeal": "appeal allowed",
    "permission refused": "permission to appeal refused",
    "default judgment": "default judgment",
    "withdrawal of claim": "claim withdrawn",
}


def outcome(keywords: list[str]) -> str | None:
    found = []
    for k in keywords:
        o = OUTCOMES.get(k.strip().lower())
        if o and o not in found:
            found.append(o)
    return "; ".join(found) or None


# ---- the page ----------------------------------------------------------------

def _text(fragment: str | None) -> str | None:
    if fragment is None:
        return None
    s = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())
    return s or None


def _cell(page: str, cls: str) -> str | None:
    m = re.search(rf'class="[^"]*\b{cls}\b[^"]*"[^>]*>(.*?)</div>', page, re.S)
    return _text(m.group(1)) if m else None


def _report(page: str) -> dict[str, Any]:
    """The schema.org Report in the page's JSON-LD, or {}."""
    for block in re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for item in (data.get("@graph") or [data]) if isinstance(data, dict) else data:
            if isinstance(item, dict) and item.get("@type") == "Report":
                return item
    return {}


def _date(value: str | None) -> str | None:
    if not value:
        return None
    if re.match(r"\d{4}-\d\d-\d\d", value):
        return value[:10]
    for fmt in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _case_number(value: str | None) -> str | None:
    """'Case No. CTAD0006/2021' -> 'CTAD0006/2021'; 'Case No. 2 of 2020' -> '2 of 2020'."""
    if not value:
        return None
    return re.sub(r"^\s*Case\s+No\.?\s*:?\s*", "", value, flags=re.I).strip() or None


def parse_judgment(page: str, url: str) -> dict[str, Any]:
    report = _report(page)
    ids = {i.get("propertyID"): i.get("value") for i in report.get("identifier") or [] if isinstance(i, dict)}
    citation = ids.get("neutralCitation") or _cell(page, "judgement-body-citation-info")
    case_number = _case_number(ids.get("caseNumber") or _cell(page, "judgement-body-case-info"))
    if not citation and not case_number:
        raise ParseError(f"{url}: no case number or citation on the page -- has the layout changed?")
    court = (report.get("about") or {}).get("name") if isinstance(report.get("about"), dict) else None
    if not court:
        m = re.search(r'class="judgement-button-link">(.*?)<', page, re.S)
        court = _text(m.group(1)) if m else None
    date = _date(report.get("datePublished")) or _date(_cell(page, "judgement-body-date-info"))
    keywords = report.get("keywords")
    if not isinstance(keywords, list):
        keywords = [_text(k) for k in re.findall(r'class="judgement-keywords-button[^"]*"[^>]*>(.*?)</a>', page, re.S)]
    keywords = [k for k in keywords if k]
    status = report.get("creativeWorkStatus") or _cell(page, "judgement-body-status-info")

    h2 = re.search(r'<h2 class="page-title">(.*?)</h2>', page, re.S)
    og = re.search(r'<meta property="og:title" content="([^"]*)"', page)
    title = og.group(1) if og else (report.get("name") or "")

    # Companies: from the heading and the title together, each once. People: counted in each
    # and the larger count kept, since the two spell a name differently (Mohammad / Mohammed).
    seen, parties, people = set(), [], {"claimant": 0, "defendant": 0, "party": 0}
    for source in (_text(h2.group(1)) if h2 else "", title_parties(title)):
        counted = {"claimant": 0, "defendant": 0, "party": 0}
        for side, name in split_case_name(source):
            if not is_organisation(name):
                counted[side] += 1
                continue
            key = norm(name) or name.upper()
            if key not in seen:
                seen.add(key)
                parties.append({"side": side, "name": name})
        people = {s: max(people[s], counted[s]) for s in people}
    return {
        "url": url,
        "case_number": case_number,
        "citation": " ".join(citation.split()) if citation else None,
        "court": court,
        "judgment_date": date,
        "year": int(date[:4]) if date else None,
        "keywords": "; ".join(keywords) or None,
        "status": status or None,
        "outcome": outcome(keywords),
        "parties": parties,
        "n_individual_parties": sum(people.values()),
        "case_title": case_title(parties, people),
    }


def case_title(parties: list[dict[str, str]], people: dict[str, int]) -> str | None:
    """'Meinhardt Bim Studios LLC' sued by one person -> '1 individual v Meinhardt Bim Studios LLC'."""
    def side(s: str) -> str:
        names = [p["name"] for p in parties if p["side"] == s]
        if people[s]:
            names.append(f"{people[s]} individual" + ("s" if people[s] > 1 else ""))
        return ", ".join(names)
    if any(p["side"] != "party" for p in parties) or people["claimant"] or people["defendant"]:
        return f"{side('claimant')} v {side('defendant')}"
    return side("party") or None
