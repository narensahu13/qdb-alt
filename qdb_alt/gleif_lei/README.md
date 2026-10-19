# GLEIF Legal Entity Identifiers (LEI)

The [Global Legal Entity Identifier Foundation](https://www.gleif.org) keeps the
public register of LEIs — the 20-character code a company needs to trade
securities, use some payment systems or deal with banks abroad. Its API
([api.gleif.org](https://api.gleif.org/api/v1)) lists about 375 entities with
their legal address in Qatar, each with its legal name (often Arabic, with a
Latin transliteration), legal form, the register it is entered in and its
number there — for Qatari companies, the CR number — and the state of the
LEI: issued, or lapsed because the yearly renewal was not paid.

For a lender, an LEI says the company has dealt with international banks or
markets and kept its reference data checked; a lapsed one says it stopped
paying for that.

## Install

```powershell
pip install -r requirements.txt
```

## Loading it

```powershell
python -m gleif_lei load
python -m gleif_lei stats
```

Two API requests (200 records a page, the API's maximum). The API is public
and documented for programmatic use, and its robots.txt disallows nothing;
requests are still made one at a time with a pause of a second. Every page is
read before anything is written: if a page fails, is not JSON, or the records
add up to fewer than the API announced, the run fails and the stored records
stay as they were. Each load replaces the records with today's; an LEI that
disappears from the Qatar list is kept and marked as no longer listed, with
the date it was last seen.

## Matching the customer book

```powershell
python -m gleif_lei match customers.csv --out gleif_lei_features.csv
```

The customer file is the same as for the other sources: `customer_id`, `name`,
`cr_number` (and optionally `search_name`). One row per customer:

| column | meaning |
|---|---|
| `lei_exists` | 1 if the company has an LEI record, 0 if none was found |
| `lei_status_issued` | 1 if the LEI is current (`ISSUED`) |
| `lei_lapsed` | 1 if it lapsed: not renewed |
| `lei_years_since_registration` | years since the LEI was first issued |
| `lei_legal_form` | legal form (Limited Liability Company, Private / Public Shareholding Company ...) |
| `matched_name`, `match_method`, `lei` | the name matched, `cr` or `name`, and the code — for a person to check |

When no record is found, `lei_exists` is 0 and the other columns are empty.

How the join works:

1. **By CR number.** Only records entered in the Qatar commercial register
   (registration authority `RA000494`) carry a CR number; records of the Qatar
   Financial Centre (`RA000495`) use the QFC's own numbering and are never
   joined on it. CR numbers are compared without leading zeros and without a
   branch suffix (`0000012091/5` → `12091`). The names must agree as well —
   a shared distinctive word, or distinctive words at least 40% alike —
   because the root of a branch CR is the parent's number, and the parent is
   often another company.
2. **Otherwise by name**, against the legal name and every other or
   transliterated name, with the strict rule used across this repository
   (`best_match` in `namematch.py`, at 92): nearly the same name and the same
   brand word. A record with a different Qatar CR number is a different
   registration and is not taken by name.

If several records fit, an issued LEI is preferred to a lapsed one, and a
lapsed one to a retired or duplicate record.

## The run log

Every command is recorded in the `run` table — start, finish, duration, rows,
a note ("376 records, 240 issued, 213 with a Qatar CR") and, if it failed, the
error: `python -m gleif_lei runs`.

## Tests

```powershell
python -m unittest discover -s tests
```

Offline, against a stand-in API and made-up records in the real JSON shape.

## Known limits

- Few Qatari SMEs have an LEI: of 319 companies in a QDB client list, 2 were
  found (both lapsed). `0` is the normal value and `1` the signal.
- Only entities with their *legal* address in Qatar are read; a Qatari
  company registered abroad (a holding in the UK, say) is not in the list.
- Two Qatar CR records have numbers in another form (`01-0096-00`); they can
  be found by name only.
- A name match against a record that has no CR number (QFC companies,
  foreign-registered ones) rests on the name alone.
- GLEIF names its Qatari legal forms in Arabic only; the English names of the
  five codes in use are kept in `parse.py`. Other codes are shown as written
  in the record, or as the code.
- The record shows whether the company paid its renewal, not whether it is
  trading; `entity_status` stays `ACTIVE` on lapsed records too.
