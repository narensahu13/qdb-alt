"""Turning a GLEIF LEI record (JSON:API) into one flat row.

The fields kept are the ones a lender can use: who the entity is (legal name,
other and transliterated names, legal form), where it is registered
(`registeredAt` -- the registration authority -- and `registeredAs`, its
number there), and the state of the LEI itself (issued, lapsed, ...; first
registered, last updated, due for renewal).

Two registration authorities matter in Qatar:

* RA000494 -- the commercial register of the Ministry of Commerce and
  Industry; `registeredAs` is the CR number the customer book uses;
* RA000495 -- the Qatar Financial Centre, with its own numbering, which is
  not a CR number and is never joined on.
"""

from __future__ import annotations

import json
import re
from typing import Any

QATAR_CR_REGISTER = "RA000494"

# The Entity Legal Form (ELF, ISO 20275) codes of Qatar. GLEIF publishes them in
# Arabic only; these are the English names. 8888 means "not on the ELF list" (the
# record then spells its form in `other`), 9999 means "not given".
LEGAL_FORMS = {
    "ADV0": "Limited Liability Company",
    "W50Q": "Private Shareholding Company",
    "QZ84": "Public Shareholding Company",
    "RN1W": "Partnership Limited by Shares",
    "B4L0": "Joint Venture Company",
}

CR = re.compile(r"\s*0*(\d+)\s*(?:/\s*\d+)?\s*")


def cr_root(cr: Any) -> str | None:
    """'000000019269' -> '19269', '0000012091/5' -> '12091'. Anything else
    ('01-0096-00', a QFC number, blank) -> None: not a CR to join on."""
    m = CR.fullmatch(str(cr or ""))
    return m.group(1) if m and m.group(1) != "0" else None


def legal_form(form: dict[str, Any] | None) -> str | None:
    code, other = (form or {}).get("id"), ((form or {}).get("other") or "").strip()
    if code in LEGAL_FORMS:
        return LEGAL_FORMS[code]
    if other:
        return other
    return None if code in (None, "8888", "9999") else code


def _date(value: str | None) -> str | None:
    return value[:10] if value else None


def record(rec: dict[str, Any]) -> dict[str, Any]:
    """One flat row per LEI record."""
    a = rec["attributes"]
    ent, reg = a.get("entity") or {}, a.get("registration") or {}
    others = [n.get("name") for n in (ent.get("otherNames") or []) + (ent.get("transliteratedOtherNames") or [])]
    others = list(dict.fromkeys(n.strip() for n in others if n and n.strip()))
    registered_at = (ent.get("registeredAt") or {}).get("id")
    registered_as = (ent.get("registeredAs") or "").strip() or None
    return {
        "lei": a.get("lei") or rec.get("id"),
        "legal_name": ((ent.get("legalName") or {}).get("name") or "").strip(),
        "other_names": json.dumps(others, ensure_ascii=False),
        "registered_at": registered_at,
        "registered_as": registered_as,
        "cr_root": cr_root(registered_as) if registered_at == QATAR_CR_REGISTER else None,
        "legal_form_code": (ent.get("legalForm") or {}).get("id"),
        "legal_form": legal_form(ent.get("legalForm")),
        "entity_status": ent.get("status"),
        "registration_status": reg.get("status"),
        "initial_registration": _date(reg.get("initialRegistrationDate")),
        "last_update": _date(reg.get("lastUpdateDate")),
        "next_renewal": _date(reg.get("nextRenewalDate")),
        "hq_city": ((ent.get("headquartersAddress") or {}).get("city") or "").strip() or None,
    }


def names(row: dict[str, Any]) -> list[str]:
    """The legal name and every other name of a stored row."""
    return [n for n in [row["legal_name"], *json.loads(row["other_names"] or "[]")] if n]
