# Fixtures

Captured from the live portal on 24 September 2026, through a browser, because
this repository's build environment cannot reach `icv.tawteen.com.qa`. Nothing
here is invented except where this file says so.

## `listing_page.json`

One real response from
`POST /_services/entity-grid-data.json/7b138792-1090-45b6-9241-8f8d96d8c372`
with `pageSize: 3`. Three real companies, with every attribute the view returns.

**Trimmed:** each attribute's `AttributeMetadata` block was removed. It is
Dataverse's description of the column — display names, option-set definitions,
audit flags — and is several hundred lines per attribute, which is why a
1,000-record response runs to megabytes. Nothing in this package reads it.
`Name`, `Type`, `Value`, `FormattedValue` and `DisplayValue` are untouched.

Worth noticing in the data:

- `ItemCount` says 5000. It is wrong: a full sweep finds 6,892 companies. The
  count is capped server-side; only `MoreRecords` can be trusted.
- `NextPagePagingCookie` comes back empty on the guest list, so paging has to
  use page numbers.
- `icv_ismanufacturer` is present on the third record and absent from the other
  two. Attributes are omitted, not nulled, so the parser must tolerate that.
- `icv_latestcertificate` is a lookup whose `Name` is the certificate *number*
  (`10011086`), not a date.

## `score_history.json`

One real response from
`POST /_services/entity-subgrid-data.json/7b138792-…` for
`ac7c7d49-…` (EPO POWER AND CONTROL TRADING), `pageSize: 100`, sorted
`icv_posteddate ASC`. All six of that company's history rows. Trimmed the same
way.

Worth noticing:

- `statuscode` carries the reason, and `"Transitional Percentage Added  "` has
  two trailing spaces in the source data.
- `icv_posteddate` gives `DisplayValue` in UTC (`2026-09-23T23:01:12Z`) and
  `FormattedValue` in Doha local time (`24/09/2026 02:01`) — a different
  calendar day. The store keeps UTC.
- The +7.00 on 23 November 2024 is `Transitional Percentage Added`: a policy
  adjustment, not the company improving. That row is why `reason` is kept.

## `detail_page.html`

**Reduced, not captured verbatim.** The real page is 304 KB, almost all of it
one 48 KB `data-view-layouts` attribute per grid plus the portal's own markup.
This file keeps the parts the crawler reads, with the real attribute names,
the real grid ids, the real relationship names and the real selected-view ids:

- the `__RequestVerificationToken` hidden input (value replaced)
- three `div.entity-grid` elements — services, goods, score history — each with
  a `data-view-layouts` holding a genuine base64 JSON array of the real shape
  (`Source`, `Relationship`, `Configuration`, `Base64SecureConfiguration`,
  `ViewName`, `Columns`, `ColumnsTotalWidth`, `SortExpression`, `Id`)

The `Base64SecureConfiguration` and `Configuration` values are placeholders.
The real ones are signed by the server and are what makes the subgrid call
work; they also bind the request to one company, which is why the crawler has
to fetch each company's page rather than reusing one configuration with a
different `entityId`.
