"""From the directory PDF to one record per company profile.

QDB's SME Directory 2024 is two PDFs (manufacturing and services) with one
company per page. The text layer is bilingual and the two columns come out
interleaved, but every profile page carries the line

    :أسماء المنتجاتProduct Names:

and the English company name is the block of Latin lines just above it, often
wrapped ("Al Raed Fresh Food Processing" / "Factory"). Everything else on the
page is read by pattern, not by position.

Contact people and phone numbers are on the page too; they are not kept.
"""

from __future__ import annotations

import re
from typing import Any

MARKER = "Product Names:"
ARABIC = re.compile(r"[؀-ۿ]")
LABEL = re.compile(r"^(?:Tel|Ph|Fax|Mob|HS Codes?|P\.?\s*O\.?\s*Box|Zone|Contact Person)\b", re.I)
WEBSITE = re.compile(r"^(?:https?://)?(?:www\.)?([a-z0-9-]+(?:\.[a-z]{2,4}){1,2})/?$", re.I)
EMAIL = re.compile(r"\S+@\S+")
FOUNDED = re.compile(r"(?:establish\w*|founded|since|incorporated|inception)[^.]{0,30}?\b((?:19|20)\d\d)\b", re.I)
HS = re.compile(r"HS Codes?:\s*([\d,\s]+)", re.I)


def _latin_title(line: str) -> bool:
    """A short line of Latin text that is not a label, a sentence end or a contact detail.
    A full stop is allowed on a short line: "Aqwat Meat & Co." is a name, not a sentence."""
    return (bool(line) and not ARABIC.search(line) and re.search(r"[A-Za-z]{2}", line) is not None
            and len(line) < 80 and not (line.endswith(".") and len(line.split()) > 5)
            and not LABEL.match(line) and not EMAIL.search(line) and not WEBSITE.match(line)
            and line != "Exporter")


def _block_above(lines: list[str]) -> str:
    block: list[str] = []
    for line in reversed(lines):
        # a full stop is only allowed on the name's own last line ("Aqwat Meat & Co."):
        # above that it ends the description
        if not _latin_title(line) or len(block) == 3 or (block and line.endswith(".")):
            break
        block.insert(0, line)
    return " ".join(" ".join(block).split()).strip(" “”\"")


def profile_name(lines: list[str]) -> str | None:
    """The English name: the Latin lines directly above the Product Names marker or,
    on pages laid out the other way round, the Latin lines that close the page."""
    i = next((k for k, l in enumerate(lines) if MARKER in l), None)
    if i is None:
        return None
    return _block_above(lines[:i]) or _block_above([l for l in lines if l]) or None


def parse_page(text: str, page: int, edition: str) -> dict[str, Any] | None:
    """One profile, or None for pages that are not a profile (foreword, index, award lists)."""
    if MARKER not in text:
        return None
    lines = [l.strip() for l in text.split("\n")]
    # the contact person: name and job title on the lines just above their phone number
    person = set()
    for k, line in enumerate(lines):
        if re.match(r"(?:Ph|Mob)\b", line):
            person.update(range(max(0, k - 3), k))
    lines = [l for k, l in enumerate(lines) if k not in person or not _latin_title(l)]
    english = " ".join(l for l in lines if l and not ARABIC.search(l))
    site = next((m.group(1).lower() for l in lines if (m := WEBSITE.match(l))), None)
    founded = FOUNDED.search(english)
    hs = sorted({c for grp in HS.findall(text) for c in re.findall(r"\d{6,}", grp)})
    products, take = [], False
    for line in lines:                         # bullet lists: "•" on one line, the item on the next
        if line.strip("•\t ") == "" and "•" in line:
            take = True
            continue
        if take and line and not ARABIC.search(line) and len(line) < 80:
            products.append(line)
        take = False
    return {
        "edition": edition,
        "page": page,
        "name": profile_name(lines),
        # other title-like lines, for profiles whose name is not above the marker
        "titles": [l for l in lines if _latin_title(l) and len(l) <= 60 and l not in products],
        "website": site,
        "founded_year": int(founded.group(1)) if founded else None,
        "hs_codes": hs,
        "exporter": int(any(l == "Exporter" for l in lines)),
        "products": products,
    }


def table_of_contents(pages: list[str]) -> dict[int, str]:
    """Page number -> company name, from the TABLE OF CONTENT pages.

    Each block there is a run of English names (sometimes led by a sector
    heading), the same names in Arabic, then the page numbers in the same
    order; names are paired with numbers from the end of the run, which drops
    the heading. The printed page numbers are the PDF's page numbers."""
    out: dict[int, str] = {}
    for page in pages:
        if "TABLE OF" not in page:
            continue
        latin: list[str] = []
        nums: list[int] = []
        for line in [l.strip() for l in page.split("\n") if l.strip()] + [""]:
            if re.fullmatch(r"\d{1,3}", line):
                nums.append(int(line))
                continue
            if nums:
                out.update(zip(nums, latin[-len(nums):]))
                latin, nums = [], []
            if line and not ARABIC.search(line) and line not in ("TABLE OF", "CONTENT"):
                latin.append(line)
    return out


def parse_text(text: str, edition: str) -> list[dict[str, Any]]:
    """The whole PDF's text, pages separated by form feeds (as extract_text writes it).
    The name printed in the table of contents is preferred to the one read off the page."""
    pages = text.split("\f")
    toc = table_of_contents(pages)
    out = []
    for n, page in enumerate(pages, start=1):
        rec = parse_page(page, n, edition)
        if rec:
            listed = " ".join(toc.get(n, "").split()) or None
            if listed and rec["name"] and listed != rec["name"]:
                rec["titles"].insert(0, rec["name"])
            rec["name"] = listed or rec["name"]
            out.append(rec)
    return out


def extract_text(pdf_path) -> str:
    """The PDF's text layer, pages separated by form feeds."""
    import pymupdf  # imported here so parsing and tests do not need it

    with pymupdf.open(pdf_path) as doc:
        return "\f".join(p.get_text() for p in doc)
