"""Turning the portal's answers into rows.

The ICV portal is Microsoft Power Pages over Dataverse, so almost nothing here
is HTML parsing: the lists come back as JSON and the only thing read out of a
page is the handful of attributes needed to ask for the next JSON.

A record in one of those answers looks like

    {"Id": "...", "EntityName": "account",
     "Attributes": [{"Name": "icv_totalscore", "Type": "System.Decimal",
                     "Value": 18.97, "FormattedValue": "18.97",
                     "DisplayValue": "18.97", "AttributeMetadata": {...}}, ...]}

Three things about that shape cost time if you meet them by surprise:

  * an attribute that is empty is *absent*, not null -- so every read has to
    tolerate a missing name (see icv_ismanufacturer in the fixtures);
  * dates arrive as "/Date(1790204472000)/" in Value, as UTC ISO in
    DisplayValue and as Doha local time in FormattedValue, and the last two
    can fall on different calendar days;
  * option-set labels carry whatever whitespace the administrator typed --
    "Transitional Percentage Added  " really has two trailing spaces.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import date, datetime, timezone
from typing import Any

from bs4 import BeautifulSoup

from .normalize import cr_root, normalize_name

# Relationship names, as the portal's own markup spells them. The score
# history and the goods/services lists are separate subgrids on the same page.
REL_HISTORY = "icv_account_icv_scorecardhistory_Supplier"
REL_GOODS_SERVICES = "icv_icv_suppliergoodsandservices_Supplier_acc"

_MS_DATE = re.compile(r"/Date\((-?\d+)")
_DMY = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})")


# --------------------------------------------------------------------------
# values
# --------------------------------------------------------------------------

def attributes(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """{name: {value, display, formatted}} for one record."""
    out: dict[str, dict[str, Any]] = {}
    for a in record.get("Attributes") or ():
        name = a.get("Name")
        if not name:
            continue
        out[name] = {"value": a.get("Value"),
                     "display": a.get("DisplayValue"),
                     "formatted": a.get("FormattedValue")}
    return out


def text(attrs: dict[str, dict[str, Any]], name: str) -> str | None:
    """A label, stripped. Prefers the rendered form, which is what a person
    reading the site sees."""
    a = attrs.get(name)
    if not a:
        return None
    for key in ("display", "formatted", "value"):
        v = a.get(key)
        if isinstance(v, dict):                     # lookup / option set
            v = v.get("Name")
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def number(attrs: dict[str, dict[str, Any]], name: str) -> float | None:
    a = attrs.get(name)
    if not a:
        return None
    v = a.get("value")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    for key in ("display", "formatted"):
        s = a.get(key)
        if isinstance(s, str) and s.strip():
            try:
                return float(s.replace(",", "").strip())
            except ValueError:
                pass
    return None


def boolean(attrs: dict[str, dict[str, Any]], name: str) -> bool | None:
    a = attrs.get(name)
    if not a:
        return None
    v = a.get("value")
    if isinstance(v, bool):
        return v
    s = (a.get("display") or a.get("formatted") or "").strip().lower()
    if s in ("yes", "true", "1"):
        return True
    if s in ("no", "false", "0"):
        return False
    return None


def option(attrs: dict[str, dict[str, Any]], name: str) -> int | None:
    a = attrs.get(name)
    v = a.get("value") if a else None
    if isinstance(v, dict) and isinstance(v.get("Value"), int):
        return v["Value"]
    return None


def moment(attrs: dict[str, dict[str, Any]], name: str) -> str | None:
    """An instant, as UTC ISO-8601.

    Taken from /Date(ms)/ where possible, because that is unambiguous. The
    rendered forms are a fallback, and DisplayValue is preferred over
    FormattedValue: the first is UTC, the second is Doha local time and can
    name a different day.
    """
    a = attrs.get(name)
    if not a:
        return None
    raw = a.get("value")
    if isinstance(raw, str):
        m = _MS_DATE.search(raw)
        if m:
            dt = datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc)
            return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    for key in ("display", "formatted"):
        s = a.get(key)
        if not isinstance(s, str) or not s.strip():
            continue
        s = s.strip()
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (dt.astimezone(timezone.utc).replace(microsecond=0)
                    .isoformat().replace("+00:00", "Z"))
        except ValueError:
            pass
        m = _DMY.match(s)
        if m:
            d, mo, y = (int(x) for x in m.groups())
            try:
                return date(y, mo, d).isoformat()
            except ValueError:
                pass
    return None


def day(attrs: dict[str, dict[str, Any]], name: str) -> str | None:
    """A calendar date (expiry dates have no meaningful time of day)."""
    v = moment(attrs, name)
    return v[:10] if v else None


# --------------------------------------------------------------------------
# rows
# --------------------------------------------------------------------------

def company(record: dict[str, Any]) -> dict[str, Any] | None:
    """One row of the supplier listing."""
    a = attributes(record)
    guid = record.get("Id") or text(a, "accountid")
    name = text(a, "name")
    if not guid or not name:
        return None
    cr = text(a, "icv_commercialregistrationnumber")
    return {
        "id": guid.lower(),
        "cr_number": cr,
        "cr_root": cr_root(cr),
        "name": name,
        "name_norm": normalize_name(name),
        "industry": text(a, "icv_mainactivity"),
        "total_score": number(a, "icv_totalscore"),
        "status": text(a, "a_3455de4d6335ec118c64000d3a3a4aee.statuscode"),
        "status_code": option(a, "a_3455de4d6335ec118c64000d3a3a4aee.statuscode"),
        # A lookup to the certificate record, whose Name is the certificate
        # NUMBER (10011086), not a date. The list is sorted by it descending,
        # which is newest-certificate-first because the numbers increment.
        "certificate_no": text(a, "icv_latestcertificate"),
        "expiry_date": day(a, "icv_icvscoreexpirydate"),
        "manufacturer": boolean(a, "icv_ismanufacturer"),
    }


def history(record: dict[str, Any]) -> dict[str, Any] | None:
    """One published change of score."""
    a = attributes(record)
    guid = record.get("Id") or text(a, "icv_scorecardhistoryid")
    posted = moment(a, "icv_posteddate")
    score = number(a, "icv_icvscorepercent")
    if not guid or not posted or score is None:
        return None
    return {
        "id": guid.lower(),
        "posted_at": posted,
        "posted_date": posted[:10],
        "score": score,
        "change": number(a, "icv_scoredifference"),
        # The reason lives in statuscode: "New Certificate Issued",
        # "Certificate Renewed", "Sub Supplier Score Updated",
        # "Transitional Percentage Added". The last of those is a policy
        # adjustment, not the company improving, which is why it is kept.
        "reason": text(a, "statuscode"),
        "reason_code": option(a, "statuscode"),
        "active": boolean(a, "statecode"),
    }


def goods_or_service(record: dict[str, Any]) -> dict[str, Any] | None:
    """One row of a company's goods or services list.

    The two subgrids share a relationship and differ only by view, so the
    caller says which kind it asked for; this reads whichever columns came
    back.
    """
    a = attributes(record)
    guid = record.get("Id")
    if not guid:
        return None
    return {
        "id": guid.lower(),
        "service_type": text(a, "icv_servicetype"),
        "service_type_ar": text(a, "icv_servicetypearabicname"),
        "category": text(a, "icv_goodscategory"),
        "sub_category": text(a, "icv_goodssubcategory"),
        "category_ar": text(a, "icv_goodscategoryarabicname"),
        "sub_category_ar": text(a, "icv_goodssubcategoryarabicname"),
    }


def records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return list(payload.get("Records") or ())


def more(payload: dict[str, Any]) -> bool:
    """Whether another page exists.

    ItemCount is NOT usable: the guest list reports 5000 however many there
    are, and a full sweep finds 6,892. Only this flag tells the truth, and
    even then the last page is served again when you ask past the end, so the
    caller must also stop when a page brings nothing new.
    """
    return bool(payload.get("MoreRecords"))


# --------------------------------------------------------------------------
# what has to be read out of a page
# --------------------------------------------------------------------------

class GridConfig:
    """Everything needed to ask a grid for its data.

    `secure_config` is signed by the server and carries the identity of the
    record the grid belongs to. Passing a different entityId alongside it does
    nothing -- the server answers for whoever the signature says -- so a
    company's history can only be fetched after fetching that company's page.
    """

    __slots__ = ("get_url", "secure_config", "sort", "entity_name",
                 "entity_id", "relationship", "view_id")

    def __init__(self, get_url, secure_config, sort, entity_name, entity_id,
                 relationship, view_id):
        self.get_url = get_url
        self.secure_config = secure_config
        self.sort = sort
        self.entity_name = entity_name
        self.entity_id = entity_id
        self.relationship = relationship
        self.view_id = view_id

    def body(self, page: int = 1, page_size: int = 100, search: str = "",
             sort: str | None = None, paging_cookie: str = "",
             timezone_offset: int = 180) -> dict[str, Any]:
        return {
            "base64SecureConfiguration": self.secure_config,
            "sortExpression": sort if sort is not None else (self.sort or ""),
            "search": search,
            "page": page,
            "pageSize": page_size,
            "pagingCookie": paging_cookie,
            "filter": None,
            "metaFilter": None,
            "nlSearchFilter": None,
            "timezoneOffset": timezone_offset,
            "customParameters": [],
            "entityName": self.entity_name,
            "entityId": self.entity_id,
        }

    def __repr__(self) -> str:                       # pragma: no cover
        return (f"GridConfig(rel={self.relationship!r}, "
                f"entity_id={self.entity_id!r}, sort={self.sort!r})")


class PageShapeError(RuntimeError):
    """The page did not look like the portal we were built against."""


def verification_token(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    el = soup.find(attrs={"name": "__RequestVerificationToken"})
    value = el.get("value") if el is not None else None
    if not value:
        raise PageShapeError(
            "no __RequestVerificationToken on the page -- either the portal "
            "changed, or what came back is a sign-in or block page rather "
            "than the portal (check `doctor`)")
    return value


def grid_configs(html: str) -> list[GridConfig]:
    """Every entity grid on a page, in document order."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[GridConfig] = []
    for div in soup.select("div.entity-grid"):
        raw = div.get("data-view-layouts")
        if not raw:
            continue
        try:
            layouts = json.loads(base64.b64decode(raw).decode("utf-8"))
        except Exception:                            # noqa: BLE001
            continue
        if not isinstance(layouts, list) or not layouts:
            continue
        selected = div.get("data-selected-view")
        layout = next((v for v in layouts if v.get("Id") == selected),
                      layouts[0])
        cfg = layout.get("Base64SecureConfiguration")
        if not cfg:
            continue
        out.append(GridConfig(
            get_url=div.get("data-get-url") or "",
            secure_config=cfg,
            sort=layout.get("SortExpression") or "",
            entity_name=div.get("data-ref-entity") or "account",
            entity_id=(div.get("data-ref-id") or "").lower() or None,
            relationship=div.get("data-ref-rel") or "",
            view_id=selected or layout.get("Id"),
        ))
    if not out:
        raise PageShapeError(
            "no entity grid with a view layout on the page -- the portal's "
            "markup has changed, or the page was served without its data")
    return out


def listing_config(html: str) -> GridConfig:
    """The supplier list on the guest database page."""
    return grid_configs(html)[0]


def history_config(html: str) -> GridConfig:
    """The score-history subgrid on a company's page."""
    grids = grid_configs(html)
    for g in grids:
        if REL_HISTORY in (g.relationship or ""):
            return g
    raise PageShapeError(
        f"no {REL_HISTORY} subgrid on the page; found "
        + ", ".join(repr(g.relationship) for g in grids))


def goods_services_configs(html: str) -> list[GridConfig]:
    """The two goods/services subgrids, if they are there."""
    return [g for g in grid_configs(html)
            if REL_GOODS_SERVICES in (g.relationship or "")]
