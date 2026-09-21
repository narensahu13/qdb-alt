"""HTML -> structured rows.

Written against the real markup of monaqasat.mof.gov.qa as of September 2026.
Two page shapes matter:

  AwardedTenders/{page}          card list -- yields tender ids
  TenderCompaniesDetails/{id}    header fields plus two company tables

Selection is by stable span id and by table header text, never by position,
so a layout change that moves a table does not silently shift the columns.
Anything the parser cannot find comes back as None rather than a guess.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from bs4 import BeautifulSoup

from .normalize import normalize_cr, normalize_name

# Fourteen listing families: seven states, twice over. "Tenders" are the
# government buying; "Bids" are the government selling (auctions and
# disposals). Both use the same card layout and the same detail pages.
#
# has_companies marks the states whose detail page lists companies. Those are
# the ones that tell you who bid: a borrower that appears in technical or
# financial opening but never in awarded is bidding and losing, which is a
# signal you cannot get from award data alone.
LISTING_KINDS: dict[str, dict[str, Any]] = {
    # tenders -- government buying
    "available":            {"path": "AvailableMinistriesTenders", "family": "tender", "state": "published",  "has_companies": False},
    "closed":               {"path": "ClosedTenders",              "family": "tender", "state": "closed",     "has_companies": False},
    "technical":            {"path": "TechnicallyOpenedTenders",   "family": "tender", "state": "technical",  "has_companies": True},
    "financial":            {"path": "FinanciallyOpenedTenders",   "family": "tender", "state": "financial",  "has_companies": True},
    "awarded":              {"path": "AwardedTenders",             "family": "tender", "state": "awarded",    "has_companies": True},
    "cancelled":            {"path": "CancelledTenders",           "family": "tender", "state": "cancelled",  "has_companies": False},
    "future":               {"path": "FutureTenders",              "family": "tender", "state": "future",     "has_companies": False},
    # bids -- government selling
    "bids-available":       {"path": "AvailableMinistriesBids",    "family": "bid",    "state": "published",  "has_companies": False},
    "bids-closed":          {"path": "ClosedBids",                 "family": "bid",    "state": "closed",     "has_companies": False},
    "bids-technical":       {"path": "TechnicallyOpenedBids",      "family": "bid",    "state": "technical",  "has_companies": True},
    "bids-financial":       {"path": "FinanciallyOpenedBids",      "family": "bid",    "state": "financial",  "has_companies": True},
    "bids-awarded":         {"path": "AwardedBids",                "family": "bid",    "state": "awarded",    "has_companies": True},
    "bids-cancelled":       {"path": "CancelledBids",              "family": "bid",    "state": "cancelled",  "has_companies": False},
    "bids-future":          {"path": "FutureBids",                 "family": "bid",    "state": "future",     "has_companies": False},
}

TENDER_KINDS = [k for k, v in LISTING_KINDS.items() if v["family"] == "tender"]
BID_KINDS = [k for k, v in LISTING_KINDS.items() if v["family"] == "bid"]
COMPANY_KINDS = [k for k, v in LISTING_KINDS.items() if v["has_companies"]]


def kind_path(kind: str) -> str:
    spec = LISTING_KINDS.get(kind)
    return spec["path"] if spec else kind

# span id -> field name on the tender record
_HEADER_FIELDS = {
    "lbl_type": "tender_type",
    "lblTenderClassification": "sector_type",
    "lblRequesterEntity": "ministry",
    "lbl_num": "tender_number",
    "lbl_subject": "subject",
    "lblEntityTenderNumber": "entity_tender_number",
    "lblTenderAnnouncementDate": "publish_date",
    "lblTenderEnvSystem": "envelope_system",
    "lbltenderClosingDate": "closing_date",
    "lbltenderDocumentValue": "document_value",
    "lblTenderInsurance": "tender_bond",
    "lblTechnicalOpenDate": "technical_open_date",
    "lblFinancialOpenDate": "financial_open_date",
    "lblAwardedDate": "awarded_date",
    "lbl_award": "awarded_amount",
}

_DATE_FIELDS = {"publish_date", "closing_date", "technical_open_date",
                "financial_open_date", "awarded_date"}
_MONEY_FIELDS = {"document_value", "tender_bond", "awarded_amount"}

# Listing-card labels, lowercased. Arabic aliases cover a session that
# slipped past the English culture cookie.
_LIST_LABELS = {
    "ministry": "ministry",
    "type": "tender_type",
    "requested sector type": "sector_type",
    "award date": "awarded_date",
    "publish date": "publish_date",
    "closing date": "closing_date",
    "tender bond (qar)": "tender_bond",
    "documents value (qr)": "document_value",
    "documents value (qar)": "document_value",
    "الجهة": "ministry",
    "الوزارة": "ministry",
    "النوع": "tender_type",
    "تاريخ الترسية": "awarded_date",
}

_STAGE_BY_HEADING = (
    ("awarded companies", "awarded"),
    ("technical/financial opened", "technical_financial"),
    ("technically opened", "technical"),
    ("financially opened", "financial"),
)

_ID_RE = re.compile(r"/TenderCompaniesDetails/(\d+)")
_DETAIL_ID_RE = re.compile(r"/TenderDetails/(\d+)")
_MONEY_RE = re.compile(r"-?[\d,]+(?:\.\d+)?")


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _text(node) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)) if node else ""


def _norm_label(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().rstrip(":").lower()


def kind_from_url(url: str) -> str | None:
    """Section kind from a listing URL, so re-parse does not guess by page number."""
    path_to_kind = {spec["path"]: kind for kind, spec in LISTING_KINDS.items()}
    for path, kind in path_to_kind.items():
        if f"/{path}/" in url or url.rstrip("/").endswith(f"/{path}"):
            return kind
    return None


def parse_date(raw: str | None) -> str | None:
    """dd/mm/yyyy -> ISO. Returns None rather than raising on anything else."""
    if not raw:
        return None
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", raw)
    if not m:
        return None
    d, mo, y = (int(x) for x in m.groups())
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def parse_money(raw: str | None) -> float | None:
    """"1,481,863.33 QAR" -> 1481863.33."""
    if not raw:
        return None
    m = _MONEY_RE.search(str(raw))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


# --------------------------------------------------------------------------
# listing
# --------------------------------------------------------------------------

def parse_listing(html: str, kind: str | None = None) -> list[dict[str, Any]]:
    """One row per card: the tender id plus whatever the card shows.

    The detail page is authoritative; these fields are here so a crawl that
    never reaches the detail pages is still worth something. Passing `kind`
    stamps each row with the family and state it was listed under, which is
    what makes "bid but did not win" answerable later.
    """
    spec = LISTING_KINDS.get(kind or "", {})
    soup = _soup(html)
    out: list[dict[str, Any]] = []

    for card in soup.select("div.row.custom-cards"):
        link = card.find("a", href=_ID_RE)
        detail = card.find("a", href=_DETAIL_ID_RE)
        tender_id = None
        if link:
            tender_id = _ID_RE.search(link["href"]).group(1)
        elif detail:
            tender_id = _DETAIL_ID_RE.search(detail["href"]).group(1)
        if not tender_id:
            continue

        fields: dict[str, str] = {}
        for row in card.select("div.cards-row, div.col-header"):
            label = row.select_one("span.card-label")
            value = row.select_one("span.card-title")
            if not label or not value:
                continue
            mapped = _LIST_LABELS.get(_norm_label(_text(label)))
            if mapped:
                fields[mapped] = _text(value)

        header = card.select_one("div.col-header span.card-label")
        out.append({
            "tender_id": tender_id,
            "tender_number": _text(header) or None,
            "subject": _text(detail) or None,
            "ministry": fields.get("ministry") or None,
            "tender_type": fields.get("tender_type") or None,
            "sector_type": fields.get("sector_type") or None,
            "awarded_date": parse_date(fields.get("awarded_date")),
            "publish_date": parse_date(fields.get("publish_date")),
            "closing_date": parse_date(fields.get("closing_date")),
            "tender_bond": parse_money(fields.get("tender_bond")),
            "document_value": parse_money(fields.get("document_value")),
            "family": spec.get("family"),
            "state": spec.get("state"),
        })
    return out


def last_listing_page(html: str, kind: str = "awarded") -> int | None:
    """Highest page number the pager links to -- the size of the crawl."""
    path = kind_path(kind)
    pages = [int(m.group(1)) for m in
             re.finditer(rf"/{path}/(\d+)", html)]
    return max(pages) if pages else None


# --------------------------------------------------------------------------
# detail
# --------------------------------------------------------------------------

_AWARDED_HEADERS = {"company name", "commercial registration number",
                    "approved value"}
_OPENED_HEADERS = {"company name", "commercial registration number",
                   "proposal amount"}


def _table_by_headers(soup: BeautifulSoup, required: set[str]):
    for table in soup.find_all("table"):
        heads = {_norm_label(_text(th)) for th in table.find_all("th")}
        if required <= heads:
            return table
    return None


def _rows(table) -> list[dict[str, str]]:
    if table is None:
        return []
    heads = [_text(th) for th in table.find_all("th")]
    out = []
    body = table.find("tbody") or table
    for tr in body.find_all("tr"):
        cells = tr.find_all("td")
        if not cells:
            continue
        out.append({heads[i] if i < len(heads) else f"col{i}": _text(td)
                    for i, td in enumerate(cells)})
    return out


def _stage_for_heading(heading: str) -> str:
    lowered = _norm_label(heading)
    for needle, stage in _STAGE_BY_HEADING:
        if needle in lowered:
            return stage
    return "unknown"


def _company_row(tender_id: str | None, role: str, seq: int, stage: str,
                 row: dict[str, str], value_col: str | None) -> dict[str, Any] | None:
    name = row.get("Company name") or ""
    if not name:
        return None
    cr, cr2 = normalize_cr(row.get("Commercial Registration Number"))
    value = parse_money(row.get(value_col)) if value_col else parse_money(
        row.get("Approved Value") or row.get("Proposal amount"))
    return {
        "tender_id": tender_id,
        "role": role,
        "seq": seq,
        "stage": stage,
        "name": name,
        "name_normalised": normalize_name(name),
        "cr_number": cr,
        "cr_secondary": cr2,
        "value": value,
        "financial_result": row.get("Financial Result") or None,
        "local_value_ratio": row.get("Local Value Ratio") or None,
        "notes": (row.get("Notes") or row.get("Approved Items") or None),
    }


def parse_detail(html: str, tender_id: str | None = None) -> dict[str, Any]:
    """Header fields plus awarded and bidding companies.

    Returns {"tender": {...}, "companies": [...], "found": bool}. A company
    appears once per role, so a winner that also shows in the opened table
    produces two rows -- role tells them apart. Older tenders split bidders
    into technically-opened and financially-opened tables (the technical
    table often has no amount column); newer ones use a single combined
    table. All of those tables are read. The site answers an unknown id
    with HTTP 200 and the home page; ``found`` is False in that case.
    """
    soup = _soup(html)

    tender: dict[str, Any] = {"tender_id": tender_id}
    for span_id, field in _HEADER_FIELDS.items():
        node = soup.find(id=span_id)
        raw = _text(node) if node else None
        if field in _DATE_FIELDS:
            tender[field] = parse_date(raw)
        elif field in _MONEY_FIELDS:
            tender[field] = parse_money(raw)
        else:
            tender[field] = raw or None

    title = soup.find(id="titleTechnicalOpenDate")
    if title and "technical/financial" in _norm_label(_text(title)):
        if tender.get("financial_open_date") is None:
            tender["financial_open_date"] = tender.get("technical_open_date")

    companies: list[dict[str, Any]] = []
    seq_by_role: dict[str, int] = {"awarded": 0, "bidder": 0}

    for table in soup.find_all("table"):
        heads = {_norm_label(_text(th)) for th in table.find_all("th")}
        if "company name" not in heads or "commercial registration number" not in heads:
            continue

        heading = table.find_previous(["h2", "h3", "h4", "h5"])
        stage = _stage_for_heading(_text(heading) if heading else "")
        if "approved value" in heads:
            role, value_col = "awarded", "Approved Value"
            if stage == "unknown":
                stage = "awarded"
        else:
            role = "bidder"
            value_col = "Proposal amount" if "proposal amount" in heads else None
            if stage == "unknown":
                stage = "technical_financial" if value_col else "technical"

        for row in _rows(table):
            parsed = _company_row(
                tender_id, role, seq_by_role[role], stage, row, value_col)
            if parsed is None:
                continue
            companies.append(parsed)
            seq_by_role[role] += 1

    found = bool(
        tender.get("tender_number")
        or tender.get("ministry")
        or tender.get("awarded_amount")
        or companies
    )
    return {"tender": tender, "companies": companies, "found": found}


def looks_rejected(html: str) -> bool:
    """The WAF's block page, which returns HTTP 200 with an error body."""
    return ("The requested URL was rejected" in html
            or "Your support ID is" in html)


# --------------------------------------------------------------------------
# TenderDetails -- the richer page: description, required activities, terms
# --------------------------------------------------------------------------

_DETAIL_HEADER = {
    "Tender number": "tender_number",
    "Type": "tender_type",
    "Subject": "subject",
    "Ministry": "ministry",
    "Entity's tender number": "entity_tender_number",
    "Request Types": "sector_type",
    "Envelopes system": "envelope_system",
    "Tender Bond": "tender_bond",
    "Documents value (QR)": "document_value",
    "Closing Date": "closing_date",
}

_DETAIL_TERMS = {
    "Brief Description": "brief_description",
    "Targeted Tenderer Type": "targeted_tenderer",
    "Service Delivery Method": "service_delivery",
    "Auction Type": "auction_type",
    "Local Value System": "local_value_system",
    "Tender Validity Period": "validity_period",
    "Evaluation Basis": "evaluation_basis",
}

_NAME_VALUE = {
    "Technical Evaluation Criteria": "evaluation_criteria",
    "Final Insurance": "final_insurance",
    "Execution Delivery Location": "delivery_location",
    "Contract Preparation Period": "contract_prep_period",
    "Contract Duration": "contract_duration",
    "Warranty Period": "warranty_period",
    "Maintenance Period": "maintenance_period",
    "Financial Disbursement Method": "disbursement_method",
}


def _header_row(table) -> dict[str, str]:
    """A one-row table with <th> headers -> {header: cell}."""
    if table is None:
        return {}
    heads = [_text(th) for th in table.find_all("th")]
    body = table.find("tbody") or table
    tr = body.find("tr")
    if not tr:
        return {}
    cells = tr.find_all("td")
    return {heads[i]: _text(td) for i, td in enumerate(cells) if i < len(heads)}


def parse_tender_details(html: str, tender_id: str | None = None) -> dict[str, Any]:
    """The TenderDetails page.

    Carries several things the companies page does not: the brief description
    (usually Arabic, even on the English pages), the activity codes the
    tender requires, whether a Local Value (ICV) certificate is demanded, and
    the contract terms.

    Returns {"tender": {...}, "activities": [{code, name}]}.
    """
    soup = _soup(html)
    tender: dict[str, Any] = {"tender_id": tender_id}

    for table in soup.find_all("table"):
        row = _header_row(table)
        if not row:
            continue
        for label, field in _DETAIL_HEADER.items():
            if label in row:
                tender[field] = row[label] or None
        for label, field in _DETAIL_TERMS.items():
            if label in row:
                tender[field] = row[label] or None

    for field in ("closing_date",):
        if tender.get(field):
            tender[field] = parse_date(tender[field])
    for field in ("tender_bond", "document_value"):
        if tender.get(field):
            tender[field] = parse_money(tender[field])

    # Name/Value table: headers are <th> inside each body row.
    for table in soup.find_all("table"):
        heads = [_text(th) for th in table.find_all("th")]
        if heads[:2] != ["Name", "Value"]:
            continue
        for tr in (table.find("tbody") or table).find_all("tr"):
            key = tr.find("th")
            val = tr.find("td")
            if key and val and _text(key) in _NAME_VALUE:
                tender[_NAME_VALUE[_text(key)]] = _text(val) or None

    activities = []
    table = _table_by_headers(soup, {"activity code", "activity name"})
    for row in _rows(table):
        code = row.get("Activity code")
        if code:
            activities.append({
                "tender_id": tender_id,
                "activity_code": code,
                "activity_name": row.get("Activity name") or None,
            })

    found = bool(tender.get("tender_number") or tender.get("ministry") or activities)
    return {"tender": tender, "activities": activities, "found": found}


# --------------------------------------------------------------------------
# ClassifiedCompaniesProfilesList -- the register of companies allowed to bid
# --------------------------------------------------------------------------

_COMPANY_HEADERS = {"profile number", "company name",
                    "commercial registration number"}
_MODAL_RE = re.compile(r"openCompanyDataModel\((\d+)\)")
_CERT_RE = re.compile(r"Certificate end date\s*:\s*(\d{1,2}/\d{1,2}/\d{4})")


def parse_company_list(html: str) -> list[dict[str, Any]]:
    """One row per classified company, with its activities.

    The activity tables are rendered into hidden modals on the same page, so
    a single request yields twenty companies complete with their activity
    codes and classification grades -- no second fetch per company.
    """
    soup = _soup(html)
    table = _table_by_headers(soup, _COMPANY_HEADERS)
    if table is None:
        return []

    heads = [_text(th) for th in table.find_all("th")]
    out: list[dict[str, Any]] = []

    for tr in (table.find("tbody") or table).find_all("tr"):
        cells = tr.find_all("td")
        if not cells:
            continue
        row = {heads[i]: _text(td) for i, td in enumerate(cells)
               if i < len(heads)}
        profile = row.get("Profile number")
        if not profile:
            continue

        cr, cr2 = normalize_cr(row.get("Commercial Registration Number"))
        name = row.get("Company name") or ""

        modal_id = None
        link = tr.find("a", href=_MODAL_RE)
        if link:
            modal_id = _MODAL_RE.search(link["href"]).group(1)

        classification = certificate_end = None
        activities: list[dict[str, Any]] = []
        if modal_id:
            modal = soup.find(id=f"CompanyData-{modal_id}")
            if modal:
                banner = modal.find(class_=re.compile("alert"))
                if banner:
                    text = _text(banner)
                    m = _CERT_RE.search(text)
                    certificate_end = parse_date(m.group(1)) if m else None
                    classification = re.split(r"\s*-\s*Certificate end date",
                                              text)[0].strip() or None
                atable = _table_by_headers(modal, {"activity code",
                                                   "activity name"})
                for a in _rows(atable):
                    if a.get("Activity code"):
                        activities.append({
                            "profile_number": profile,
                            "activity_code": a["Activity code"],
                            "activity_name": a.get("Activity name") or None,
                            "grade": a.get("Classification grade") or None,
                        })

        out.append({
            "profile_number": profile,
            "company_type": row.get("Company type") or None,
            "name": name or None,
            "name_normalised": normalize_name(name),
            "cr_number": cr,
            "cr_secondary": cr2,
            "size": row.get("Company size") or None,
            "evaluation": row.get("Government entities evaluation") or None,
            "classification": classification,
            "certificate_end": certificate_end,
            "activities": activities,
        })
    return out
