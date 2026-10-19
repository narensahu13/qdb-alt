"""Reading the two programme pages of Qatar Customs.

Both are SharePoint pages with an HTML table:

* Authorized Economic Operators -- accreditation year, certificate number
  (QA-AEO-000001), company name, row number;
* the Green List -- company name, row number.

The cells are sprinkled with zero-width spaces and the certificate numbers
are sometimes broken by spaces ("QA- AE O -010015"); both are cleaned up.
"""

from __future__ import annotations

import html
import re
from typing import Any

ZERO_WIDTH = re.compile("[​‌‍﻿]")


def _cell(raw: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    return " ".join(ZERO_WIDTH.sub("", text).split())


def _rows(page: str) -> list[list[str]]:
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S | re.I):
        cells = [_cell(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S | re.I)]
        if any(cells):
            out.append(cells)
    return out


def aeo(page: str) -> list[dict[str, Any]]:
    """One record per accredited operator: year, certificate number, name. The year cell spans
    several rows (rowspan), so a row without one takes the year of the row above."""
    out, last_year = [], None
    for cells in _rows(page):
        cert = next((c for c in cells if re.sub(r"\s", "", c).upper().startswith("QA-AEO")), None)
        year = next((int(c) for c in cells if re.fullmatch(r"(?:19|20)\d\d", c)), None) or last_year
        if not cert:
            continue                      # the header row
        last_year = year
        name = max((c for c in cells if c != cert and re.search(r"[A-Za-z]{2}", c)), key=len, default="")
        out.append({"programme": "aeo", "year": year, "certificate": re.sub(r"\s", "", cert).upper(), "name": name})
    return out


def green_list(page: str) -> list[dict[str, Any]]:
    """One record per company on the Green List (the page gives names only)."""
    out = []
    for cells in _rows(page):
        names = [c for c in cells if re.search(r"[A-Za-z]{3}", c)]
        if not names or names[0].lower() == "company name":
            continue
        out.append({"programme": "green_list", "year": None, "certificate": None, "name": max(names, key=len)})
    return out
