# Qatar Customs trusted traders

Two short lists Qatar Customs publishes on
[customs.gov.qa](https://www.customs.gov.qa/English/Procedures/FacilitationandCompliancePrograms/Pages/CertifiedEconomicOperator.aspx):

* **Authorized Economic Operators (AEO)** — about 110 companies certified as
  secure, compliant traders, with the year of accreditation and the
  certificate number;
* **the Green List** — about 60 importers and exporters cleared with fewer
  inspections.

Being on either list means Customs has audited the company's record and
controls, which is a strong signal for a trade-dependent borrower — and the
AEO year says for how long.

## Install

```powershell
pip install -r requirements.txt
```

customs.gov.qa serves an incomplete certificate chain; with `truststore` (the
Windows certificate store) it verifies, so certificate checks stay on.

## Loading it

```powershell
python -m customs_trusted_traders load
python -m customs_trusted_traders stats
```

Two page requests. Customs publishes no robots.txt, so nothing is disallowed;
requests are still made one at a time with a pause. Both pages are read before
anything is written: if either fails or comes back without its table, the run
fails and the stored lists stay as they were. Each load replaces the lists with
today's; a company that drops off a list is kept and marked as no longer
listed, with the date it was last seen.

## Matching the customer book

```powershell
python -m customs_trusted_traders match customers.csv --out customs_features.csv
```

The customer file is the same as for the other sources: `customer_id`, `name`,
`cr_number` (and optionally `search_name`). One row per customer:

| column | meaning |
|---|---|
| `customs_aeo` | 1 if the company is an Authorized Economic Operator |
| `customs_aeo_year`, `customs_aeo_years_held` | year of accreditation, and years since |
| `customs_green_list` | 1 if the company is on the Green List |
| `matched_aeo`, `matched_green_list` | the listed names matched, for a person to check |

The lists have no CR numbers, so the join is by name with the strict rule used
for every name-only source (`same_company` in `namematch.py`): every
distinctive word of the customer's name must be there and the brand word must
be the same word.

## The run log

Every command is recorded in the `run` table — start, finish, duration, rows,
a note ("aeo 109, green_list 57") and, if it failed, the error:
`python -m customs_trusted_traders runs`.

## Tests

```powershell
python -m unittest discover -s tests
```

Offline, against a stand-in site and made-up pages in the real markup.

## Known limits

- The lists are short: most borrowers will not be on them, so `0` is the
  normal value and `1` the signal.
- The Green List gives names only — no year, no number.
- The pages are edited by hand; a company can appear twice under two
  spellings (QATARGAS appears as two rows), which is harmless for matching.
