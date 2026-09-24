"""A stand-in for the ICV portal.

It answers in the real shapes -- the JSON record layout, the base64
`data-view-layouts` attribute, the verification token -- so the tests drive
the same parsing and paging code that runs against the live site. The build
environment cannot reach icv.tawteen.com.qa, so this is the only way the crawl
is exercised end to end before it is run on a QDB machine.

It also reproduces the three behaviours that would otherwise be discovered the
expensive way:

  * `ItemCount` lies (it is capped), so only `MoreRecords` may be believed;
  * asking past the last page serves the last page again rather than nothing;
  * the signed grid configuration, not `entityId`, decides whose history is
    returned -- so a company's page must be fetched before its history.
"""

from __future__ import annotations

import base64
import json
from datetime import date, timedelta
from typing import Any

GRID_URL = "/_services/entity-grid-data.json/fake-list"
SUBGRID_URL = "/_services/entity-subgrid-data.json/fake-page"
REL_HISTORY = "icv_account_icv_scorecardhistory_Supplier"
REL_GOODS_SERVICES = "icv_icv_suppliergoodsandservices_Supplier_acc"

ITEM_COUNT_CAP = 5000          # what the real portal reports, however many


def _attr(name: str, type_: str, value: Any, display: Any = None,
          formatted: Any = None) -> dict[str, Any]:
    return {"Name": name, "Type": type_, "Value": value,
            "FormattedValue": formatted if formatted is not None
            else ("" if display is None else str(display)),
            "DisplayValue": "" if display is None else str(display)}


def _ms(iso: str) -> str:
    from datetime import datetime, timezone
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return f"/Date({int(dt.timestamp() * 1000)})/"


class Company:
    def __init__(self, guid: str, cr: str, name: str, score: float,
                 industry: str = "Manufacturing", status: str = "Valid",
                 status_code: int = 100000002, certificate: str = "1000001",
                 expiry: str = "2027-12-23", manufacturer: bool | None = None):
        self.guid = guid
        self.cr = cr
        self.name = name
        self.score = score
        self.industry = industry
        self.status = status
        self.status_code = status_code
        self.certificate = certificate
        self.expiry = expiry
        self.manufacturer = manufacturer
        self.history: list[dict[str, Any]] = []
        self.services: list[str] = []

    def post(self, on: str, score: float, reason: str, change: float,
             row_id: str | None = None) -> "Company":
        self.history.append({
            "id": row_id or f"{self.guid[:8]}-h{len(self.history) + 1}",
            "on": on, "score": score, "reason": reason, "change": change})
        self.score = score
        return self

    def record(self) -> dict[str, Any]:
        attrs = [
            _attr("icv_mainactivity", "Microsoft.Xrm.Sdk.EntityReference",
                  {"Id": "act-" + self.industry, "LogicalName":
                   "icv_scorecardmainactivity", "Name": self.industry},
                  self.industry, self.industry),
            _attr("icv_totalscore", "System.Decimal", self.score, self.score,
                  f"{self.score:.2f}"),
            _attr("a_3455de4d6335ec118c64000d3a3a4aee.statuscode",
                  "Microsoft.Xrm.Sdk.OptionSetValue",
                  {"Value": self.status_code}, self.status, self.status),
            _attr("icv_commercialregistrationnumber", "System.String",
                  self.cr, self.cr),
            _attr("accountid", "System.Guid", self.guid, self.guid),
            _attr("icv_latestcertificate",
                  "Microsoft.Xrm.Sdk.EntityReference",
                  {"Id": "cert-" + self.certificate,
                   "LogicalName": "icv_icvcertification",
                   "Name": self.certificate},
                  self.certificate, self.certificate),
            _attr("icv_icvscoreexpirydate", "System.DateTime",
                  _ms(self.expiry), self.expiry,
                  "/".join(reversed(self.expiry.split("-")))),
            _attr("name", "System.String", self.name, self.name),
        ]
        # Absent, not null, when it has no value -- as on the live portal.
        if self.manufacturer is not None:
            attrs.append(_attr("icv_ismanufacturer", "System.Boolean",
                               self.manufacturer, None,
                               "Yes" if self.manufacturer else "No"))
        return {"Id": self.guid, "EntityName": "account", "Attributes": attrs}

    def history_records(self) -> list[dict[str, Any]]:
        out = []
        for h in sorted(self.history, key=lambda x: x["on"]):
            out.append({
                "Id": h["id"], "EntityName": "icv_scorecardhistory",
                "Attributes": [
                    _attr("statecode", "Microsoft.Xrm.Sdk.OptionSetValue",
                          {"Value": 0}, "Active", "Active"),
                    _attr("icv_scoredifference", "System.Decimal",
                          h["change"], h["change"], f"{h['change']:.2f}"),
                    _attr("statuscode", "Microsoft.Xrm.Sdk.OptionSetValue",
                          {"Value": 1}, h["reason"], h["reason"]),
                    _attr("icv_posteddate", "System.DateTime",
                          _ms(h["on"] + "T09:00:00"), h["on"] + "T09:00:00Z",
                          "/".join(reversed(h["on"].split("-"))) + " 12:00"),
                    _attr("icv_icvscorepercent", "System.Decimal", h["score"],
                          h["score"], f"{h['score']:.2f}"),
                    _attr("icv_scorecardhistoryid", "System.Guid", h["id"],
                          h["id"]),
                ]})
        return out

    def service_records(self) -> list[dict[str, Any]]:
        return [{"Id": f"{self.guid[:8]}-s{i}", "EntityName": "icv_sgs",
                 "Attributes": [_attr("icv_servicetype", "System.String", s, s)]}
                for i, s in enumerate(self.services, 1)]


def _layouts(secure: str, rel: str, sort: str, view: str, owner: str) -> str:
    return base64.b64encode(json.dumps([{
        "Source": {"Id": owner, "LogicalName": "account", "KeyAttributes": []},
        "Relationship": rel, "Configuration": "-",
        "Base64SecureConfiguration": secure, "ViewName": view,
        "Columns": [], "ColumnsTotalWidth": 100, "SortExpression": sort,
        "Id": view}]).encode()).decode()


class _Response:
    def __init__(self, status_code: int, text: str = "",
                 payload: Any = None, content_type: str = "text/html"):
        self.status_code = status_code
        self.text = text
        self._payload = payload
        self.headers = {"Content-Type": content_type}

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakePortal:
    """A requests.Session look-alike. Pass it as Portal(session=...)."""

    TOKEN = "fake-token-" + "0" * 40

    def __init__(self, companies: list[Company]):
        self.companies = {c.guid: c for c in companies}
        self.order = [c.guid for c in companies]
        self.requests: list[str] = []
        self.fail_after: int | None = None     # simulate a dropped sweep
        self.headers: dict[str, str] = {}      # a requests.Session has these

    # -- what the crawl sees -------------------------------------------

    def listing_html(self) -> str:
        layouts = _layouts("cfg:list", "", "icv_latestcertificate DESC",
                           "view-list", "list")
        return (f'<html><body><input name="__RequestVerificationToken" '
                f'value="{self.TOKEN}"/>'
                f'<div class="entity-grid" data-get-url="{GRID_URL}" '
                f'data-ref-entity="account" data-selected-view="view-list" '
                f'data-view-layouts="{layouts}"></div></body></html>')

    def detail_html(self, guid: str) -> str:
        def grid(secure, rel, sort, view):
            return (f'<div class="entity-grid subgrid" '
                    f'data-get-url="{SUBGRID_URL}" data-ref-entity="account" '
                    f'data-ref-id="{guid}" data-ref-rel="{rel}" '
                    f'data-selected-view="{view}" data-view-layouts='
                    f'"{_layouts(secure, rel, sort, view, guid)}"></div>')
        return (f'<html><body><input name="__RequestVerificationToken" '
                f'value="{self.TOKEN}"/>'
                + grid(f"cfg:services:{guid}", REL_GOODS_SERVICES,
                       "icv_servicetype ASC", "view-serv")
                + grid(f"cfg:goods:{guid}", REL_GOODS_SERVICES,
                       "icv_goodscategory ASC", "view-goods")
                + grid(f"cfg:history:{guid}", REL_HISTORY,
                       "icv_posteddate ASC", "view-hist")
                + '</body></html>')

    # -- the session interface ------------------------------------------

    def request(self, method: str, url: str, timeout=None, json=None,
                headers=None, **kw) -> _Response:
        self.requests.append(f"{method} {url}")
        if (self.fail_after is not None
                and len(self.requests) > self.fail_after):
            return _Response(503)
        if method == "GET":
            if "supplier-score-history" in url:
                guid = url.split("id=")[1].split("&")[0]
                if guid not in self.companies:
                    return _Response(404)
                return _Response(200, self.detail_html(guid))
            return _Response(200, self.listing_html())

        if headers is None or headers.get("__RequestVerificationToken") != self.TOKEN:
            return _Response(403)
        cfg = (json or {}).get("base64SecureConfiguration", "")
        page = (json or {}).get("page", 1)
        size = (json or {}).get("pageSize", 20)
        search = ((json or {}).get("search") or "").strip()

        if cfg == "cfg:list":
            guids = self.order
            if search:
                guids = [g for g in guids
                         if search.lower() in (self.companies[g].cr
                                               + self.companies[g].name).lower()]
            start = (page - 1) * size
            if start >= len(guids):              # past the end: the last page
                start = max(len(guids) - (len(guids) % size or size), 0)
            chunk = guids[start:start + size]
            return _Response(200, payload={
                "MoreRecords": start + size < len(guids),
                "ItemCount": min(len(guids), ITEM_COUNT_CAP),
                "PageNumber": page, "PageSize": size,
                "Records": [self.companies[g].record() for g in chunk],
            }, content_type="application/json")

        # The signature decides whose rows come back -- entityId is ignored,
        # exactly as the live portal behaves.
        if cfg.startswith("cfg:history:"):
            owner = self.companies[cfg.split(":")[2]]
            return _Response(200, payload={
                "MoreRecords": False, "ItemCount": len(owner.history),
                "PageNumber": 1, "PageSize": size,
                "Records": owner.history_records(),
            }, content_type="application/json")
        if cfg.startswith("cfg:services:"):
            owner = self.companies[cfg.split(":")[2]]
            return _Response(200, payload={
                "MoreRecords": False, "ItemCount": len(owner.services),
                "PageNumber": 1, "PageSize": size,
                "Records": owner.service_records(),
            }, content_type="application/json")
        if cfg.startswith("cfg:goods:"):
            return _Response(200, payload={
                "MoreRecords": False, "ItemCount": 0, "PageNumber": 1,
                "PageSize": size, "Records": []},
                content_type="application/json")
        return _Response(400)


def sample(n: int = 5) -> list[Company]:
    """A small register with a history each."""
    start = date(2024, 1, 15)
    out = []
    for i in range(n):
        c = Company(guid=f"{i:08d}-0000-0000-0000-000000000000",
                    cr=str(100000 + i), name=f"COMPANY {i} TRADING W L L",
                    score=0.0,
                    industry="Manufacturing" if i % 2 else "Other",
                    certificate=str(1000000 + i))
        c.post((start + timedelta(days=30 * i)).isoformat(), 10.0 + i,
               "New Certificate Issued", 10.0 + i)
        c.post((start + timedelta(days=30 * i + 200)).isoformat(),
               17.0 + i, "Transitional Percentage Added  ", 7.0)
        c.services = ["Manpower"] if i % 2 else []
        out.append(c)
    return out
