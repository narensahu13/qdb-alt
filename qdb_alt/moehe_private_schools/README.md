# MOEHE private schools

The Ministry of Education and Higher Education publishes its register of
private schools and kindergartens as a map layer behind the "My Schools" site
(myschools.edu.gov.qa) -- about 410 records, each with the school's name in
English and Arabic, the stages it teaches, its curriculum, district,
municipality, zone and whether it is open.

For a borrower that runs a school, being on the register says the ministry
licenses it; the number of campuses, the stages and the curriculum say how
large and what kind of school it is.

## Install

```powershell
pip install -r requirements.txt
```

## Loading it

```powershell
python -m moehe_private_schools load
python -m moehe_private_schools stats
```

The layer is ArcGIS (`.../V2/SchoolsEN/MapServer/1`) and is read with the
standard query, 1,000 records a request with a one-second pause; there is no
robots.txt on the host. ArcGIS answers a failed query with HTTP 200 and an
`{"error": ...}` body, so an answer without records, or a number of records
that differs from the count the layer reports, fails the run and leaves the
stored layer as it was. Each load replaces the layer with today's; a record
that drops off is kept and marked as no longer listed.

Only the fields below are requested. Phones and e-mail addresses are on the
layer and are not; student, staff and capacity columns exist but are empty.

| field | kept as |
|---|---|
| `NAME`, `ANAME` | `name`, `name_ar` |
| `CATEGORY` ("Kindergarten + Primary") | `levels`: Kindergarten, Primary, Preparatory, Secondary |
| `ADOPTEDCURRICULUM` (Arabic only) | `curricula` in English (British, Indian, IB ...) and `curriculum_ar` as published |
| `DISTRICT_NAME_EN`, `MNCP_NAME_EN`, `ZONENUM` | `district`, `municipality`, `zone` |
| `OperationalStatus` | `status`: Open, Temporarily Closed, Permanently Closed |

### The certificate

`myschools.edu.gov.qa` sends its own certificate without the DigiCert
intermediate that signed it. Windows fills the gap, the certificate bundle
shipped with `requests` does not, so the loader uses the Windows certificate
store through `truststore` (the default; `--no-system-trust` turns it off).
Certificates are always checked. On a machine where the system store cannot
complete the chain, point `REQUESTS_CA_BUNDLE` at a bundle that includes
"DigiCert Global G2 TLS RSA SHA256 2020 CA1".

## Matching the customer book

```powershell
python -m moehe_private_schools match customers.csv --out moehe_features.csv
```

The customer file is the same as for the other sources: `customer_id`, `name`,
`cr_number` (and optionally `search_name`). Only customers whose name says
they are a school -- SCHOOL, ACADEMY, KINDERGARTEN, COLLEGE, NURSERY,
EDUCATION -- are looked up. For everyone else the columns are empty, not 0.

| column | meaning |
|---|---|
| `school_registered` | 1 if the school is on the register, 0 if not |
| `school_campuses` | campuses on the register (a name in a district; the layer repeats some schools once per building) |
| `school_open` | 1 if at least one campus is open |
| `school_levels`, `school_curricula`, `school_districts` | what the campuses teach, and where |
| `matched_schools` | the names matched, for a person to check |

The layer has no CR numbers, so the join is by name: the strict rule used for
every name-only source (`same_company` in `namematch.py`), or the same brand
-- the first word that is neither generic nor a school word -- because the
register uses short names and adds the campus ("Awfaz International",
"Edison International Academy-Dahl Al Hamam"). The brand has to be the
school's own first word: "Newton International Kindergarten - Oryx" is not
Oryx's.

## The run log

Every command is recorded in the `run` table -- start, finish, duration, rows,
a note and, if it failed, the error: `python -m moehe_private_schools runs`.

## Tests

```powershell
python -m unittest discover -s tests
```

Offline, against a stand-in ArcGIS server and a made-up layer.

## Known limits

- The register covers private schools and kindergartens only. Universities
  are not on it, so a university college is either not found or -- through
  the brand rule -- matched to a school that shares its first word (on QDB's
  list, "Oryx Universal College" matched "ORYX International School").
  `matched_schools` is there to catch this.
- A group with schools under different brands is matched only on the brand
  in the customer's name.
- Records are keyed by the layer's `OBJECTID`. If the ministry republishes the
  layer with new ids, every school looks new once, and `first_seen` restarts.
- All 412 records said "Open" on 19 October 2026; a closed school may be
  removed from the layer rather than marked closed.
