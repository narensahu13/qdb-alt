# GIS Qatar landmarks

GIS Qatar, the Ministry of Municipality's national map, publishes a layer of
named places -- about 7,200 "landmarks": businesses, hotels, schools,
clinics, banks, government offices, mosques -- each with its English and
Arabic name, category, district, zone and position.

A landmark entry confirms that a company has a physical premises the
national map knows about: someone surveyed it, named it and put it on the
map. For a borrower that is evidence the business exists at an address, and
several entries point to several sites (a factory and a showroom, branches).

## Install

```powershell
pip install -r requirements.txt
```

## Loading it

```powershell
python -m gis_qatar_landmarks load
python -m gis_qatar_landmarks stats
```

The layer is ArcGIS (`services.gisqatar.org.qa/.../Vector/Landmarks/MapServer/0`)
and is read with the standard query, 1,000 records a request with a
one-second pause -- eight requests in all; there is no robots.txt on the
host. ArcGIS answers a failed query with HTTP 200 and an `{"error": ...}`
body, so an answer without records, or a number of records that differs from
the count the layer reports, fails the run and leaves the stored layer as it
was. Each load replaces the layer with today's; a landmark that drops off is
kept and marked as no longer listed.

Only the fields below are requested. Phone, fax, P.O. box and e-mail are on
the layer and are not.

| field | kept as |
|---|---|
| `LANDMARK_ID` | `id` |
| `ENAME`, `ANAME` | `name`, `name_ar` |
| `CATEGORY`, `SUBCATEGORY_ANAME` | `category` (BUSINESS, LODGING, EDUCATION, HEALTH, FINANCE ...), `subcategory_ar` (Arabic only) |
| `DISTRICT_ENAME`, `ZONE_NO` | `district`, `zone` |
| `STATUS` | `status`, the layer's code as published |
| `WEBID` | `website` |
| `DATE_MODFD` | `modified`, the date the layer last changed the record |
| the point | `lat`, `lon` (asked for in WGS 84 with `outSR=4326`) |

The certificate of the host is complete and is checked; the Windows
certificate store is used through `truststore` as for the other sources
(`--no-system-trust` turns it off).

## Matching the customer book

```powershell
python -m gis_qatar_landmarks match customers.csv --out gis_features.csv
```

The customer file is the same as for the other sources: `customer_id`, `name`,
`cr_number` (and optionally `search_name`). One row per customer:

| column | meaning |
|---|---|
| `gis_landmark` | 1 if the company is on the map |
| `gis_landmarks` | how many landmarks carry its name |
| `gis_categories`, `gis_districts` | what kind of places, and where |
| `matched_landmarks` | the names matched, for a person to check |

The layer has no CR numbers, so the join is by name with the strict rule used
for every name-only source (`same_company` in `namematch.py`); every landmark
that passes it is counted.

## The run log

Every command is recorded in the `run` table -- start, finish, duration, rows,
a note and, if it failed, the error: `python -m gis_qatar_landmarks runs`.

## Tests

```powershell
python -m unittest discover -s tests
```

Offline, against a stand-in ArcGIS server and a made-up layer.

## Known limits

- The map names places, not legal entities: a landmark is listed under the
  name on the building ("Doha Academy, Al Waab"), which is not always the
  registered name, and many SMEs -- workshops in the Industrial Area,
  office-only companies -- are not on it at all. `0` means "not found on the
  map", not "has no premises".
- `STATUS` is a code (1, 3, 4 or 44 in October 2026) whose meaning the layer
  does not publish, so no feature is built on it.
- About 770 records share one modification date (9 October 2018), the date
  of a bulk load; `modified` says when a record was last edited, not when the
  place opened.
- `ZONE_NO` 999 looks like a placeholder: 279 records across many districts
  carry it. It is kept as published; use `district` instead.
