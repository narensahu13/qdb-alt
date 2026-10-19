"""One record per row of the private-schools layer.

The layer's English and Arabic names carry stray spaces, line breaks and
non-breaking spaces; they are collapsed. `CATEGORY` lists the stages taught
("Kindergarten+Primary +Preparatory+Secondary"), `ADOPTEDCURRICULUM` the
curriculum, in Arabic only ("British curriculum" as المنهج البريطاني); both
are turned into short English lists, with the Arabic kept as published.
`OperationalStatus` is a coded value: Open, TempClosed or Closed.

Phones and e-mail addresses are on the layer too; they are not requested.
Student and staff numbers have columns but the ministry leaves them empty.
"""

from __future__ import annotations

import re
from typing import Any

LEVELS = ("Kindergarten", "Primary", "Preparatory", "Secondary")

# The Arabic word for each curriculum as it appears in ADOPTEDCURRICULUM
# (without the article, so that both "المنهج البريطاني" and "بريطاني" are found).
CURRICULA = (
    ("المعايير الوطنية", "Qatari national"),
    ("بريطاني", "British"), ("أمريكي", "American"), ("هندي", "Indian"), ("كندي", "Canadian"),
    ("فرنسي", "French"), ("ألماني", "German"), ("فنلندي", "Finnish"), ("ياباني", "Japanese"),
    ("تركي", "Turkish"), ("إيراني", "Iranian"), ("باكستاني", "Pakistani"), ("بنغلاديشي", "Bangladeshi"),
    ("فلبيني", "Filipino"), ("أثيوبي", "Ethiopian"), ("مصري", "Egyptian"), ("تونسي", "Tunisian"),
    ("سوري", "Syrian"), ("لبناني", "Lebanese"), ("أردني", "Jordanian"), ("فلسطيني", "Palestinian"),
    ("سوداني", "Sudanese"),
)

STATUS = {"Open": "Open", "TempClosed": "Temporarily Closed", "Closed": "Permanently Closed"}


def clean(text: Any) -> str | None:
    if text is None:
        return None
    return " ".join(str(text).split()) or None


def levels(category: str | None) -> list[str]:
    """'Kindergarten + Primary' -> ['Kindergarten', 'Primary']; an unknown text is kept as it is."""
    text = clean(category) or ""
    found = [lv for lv in LEVELS if re.search(rf"\b{lv}\b", text, re.I)]
    return found or ([text] if text else [])


def curricula(adopted: str | None) -> list[str]:
    """'المنهج البريطاني' -> ['British'], 'IB' -> ['IB']; an unknown text is kept as it is, in Arabic."""
    text = clean(adopted) or ""
    hits = [(text.find(ar), en) for ar, en in CURRICULA if ar in text]
    ib = re.search(r"\bIB\b", text)
    if ib:
        hits.append((ib.start(), "IB"))
    return [en for _, en in sorted(hits)] or ([text] if text else [])


def school(attrs: dict[str, Any]) -> dict[str, Any]:
    status = clean(attrs.get("OperationalStatus"))
    zone = attrs.get("ZONENUM")
    return {
        "id": int(attrs["OBJECTID"]),
        "name": clean(attrs.get("NAME")) or "",
        "name_ar": clean(attrs.get("ANAME")),
        "levels": levels(attrs.get("CATEGORY")),
        "curricula": curricula(attrs.get("ADOPTEDCURRICULUM")),
        "curriculum_ar": clean(attrs.get("ADOPTEDCURRICULUM")),
        "district": clean(attrs.get("DISTRICT_NAME_EN")),
        "municipality": clean(attrs.get("MNCP_NAME_EN")),
        "zone": int(zone) if zone not in (None, "") else None,
        "status": STATUS.get(status or "", status),
    }
