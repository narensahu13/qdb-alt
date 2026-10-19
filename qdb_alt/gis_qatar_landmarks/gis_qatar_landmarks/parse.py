"""One record per landmark.

Names carry stray spaces; they are collapsed. `CATEGORY` is one of a dozen
groups (BUSINESS, LODGING, EDUCATION, HEALTH, FINANCE ...); the finer
subcategory is published in Arabic only and is kept as it is. `STATUS` is a
small number whose meaning the layer does not publish (1 on 93% of records);
it is kept as a number. `DATE_MODFD` is milliseconds since 1970.

Phone, fax, P.O. box and e-mail are on the layer too; they are not requested.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def clean(text: Any) -> str | None:
    if text is None:
        return None
    return " ".join(str(text).split()) or None


def _date(ms: Any) -> str | None:
    if not isinstance(ms, (int, float)):
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()


def _int(v: Any) -> int | None:
    return int(v) if isinstance(v, (int, float)) else None


def _coord(v: Any, digits: int = 6) -> float | None:
    return round(float(v), digits) if isinstance(v, (int, float)) else None


def landmark(feature: dict[str, Any]) -> dict[str, Any]:
    a = feature.get("attributes") or {}
    point = feature.get("geometry") or {}
    return {
        "id": int(a["LANDMARK_ID"]),
        "name": clean(a.get("ENAME")) or "",
        "name_ar": clean(a.get("ANAME")),
        "category": clean(a.get("CATEGORY")),
        "subcategory_ar": clean(a.get("SUBCATEGORY_ANAME")),
        "district": clean(a.get("DISTRICT_ENAME")),
        "zone": _int(a.get("ZONE_NO")),
        "status": _int(a.get("STATUS")),
        "website": clean(a.get("WEBID")),
        "lat": _coord(point.get("y")),
        "lon": _coord(point.get("x")),
        "modified": _date(a.get("DATE_MODFD")),
    }
