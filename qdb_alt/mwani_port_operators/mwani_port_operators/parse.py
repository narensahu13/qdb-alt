"""One record per licensed company from the SharePoint list rows.

`Category` holds the licences as "English;Arabic" pairs, e.g.
["Clearing Agencies;...", "Freight Forwarders;..."]. Phone, fax and e-mail
are on the list too; they are contact details of people and are not kept.
"""

from __future__ import annotations

from typing import Any


def operator(row: dict[str, Any]) -> dict[str, Any]:
    licences = sorted({c.split(";")[0].strip() for c in (row.get("Category") or []) if c and c.strip()})
    url = row.get("Url") or ""
    if isinstance(url, dict):                     # a hyperlink column: {"Url": ..., "Description": ...}
        url = url.get("Url") or ""
    url = url.strip()
    return {
        "id": int(row["Id"]),
        "name": " ".join((row.get("Title") or "").split()),
        "name_ar": " ".join((row.get("Title_Arabic") or "").split()) or None,
        "licences": licences,
        "website": url or None,
        "listed_since": (row.get("Created") or "")[:10] or None,
        "modified": (row.get("Modified") or "")[:10] or None,
    }
