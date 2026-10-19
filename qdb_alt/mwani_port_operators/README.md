# Mwani port operators

[Mwani Qatar](https://www.mwani.com.qa) runs Qatar's ports and publishes the
companies it has licensed to work in them — about 120 clearing agencies,
shipping agencies, freight forwarders, ship chandlers, waste collectors and
others, each with its licence types and website.

For a borrower in trade or logistics, a Mwani licence says the company is
allowed to operate in the ports and has been for some years; the licence types
say what it actually does there.

## Install

```powershell
pip install -r requirements.txt
```

## Loading it

```powershell
python -m mwani_port_operators load
python -m mwani_port_operators stats
```

One request. The site is SharePoint; the list behind its "Shipping Lines"
page is read through the site's REST endpoint (`/_api/`). robots.txt disallows
`/_layouts/`, `/_vti_bin/` and `/_catalogs/` only. Each load replaces the list
with today's; a company that drops off is kept and marked as no longer listed.
An error answer or an empty list fails the run and leaves the stored list as it
was. Phone numbers and e-mail addresses are on the list too and are not kept.

## Matching the customer book

```powershell
python -m mwani_port_operators match customers.csv --out mwani_features.csv
```

The customer file is the same as for the other sources: `customer_id`, `name`,
`cr_number` (and optionally `search_name`). One row per customer:

| column | meaning |
|---|---|
| `mwani_authorized` | 1 if the company holds a Mwani licence |
| `mwani_licences`, `mwani_licence_types` | how many licence types, and which |
| `mwani_years_listed` | years since the company was added to the list |
| `matched_operator` | the listed name matched, for a person to check |

The list has no CR numbers, so the join is by name with the strict rule used
for every name-only source (`same_company` in `namematch.py`).

## The run log

Every command is recorded in the `run` table — start, finish, duration, rows,
a note and, if it failed, the error: `python -m mwani_port_operators runs`.

## Tests

```powershell
python -m unittest discover -s tests
```

Offline, against a stand-in endpoint and a made-up list.

## Known limits

- Only port-facing businesses are on the list; for a manufacturer `0` is the
  normal value.
- "Years listed" counts from when the company was added to the website's
  list (the earliest entries date from 2018, when the list was set up), not
  from when its licence was first issued.
