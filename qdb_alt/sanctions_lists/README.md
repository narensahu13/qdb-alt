# Sanctions lists

Screens the customer book against the official sanctions lists, each taken
from the authority that publishes it:

| list | publisher | file |
|---|---|---|
| UN Security Council consolidated list | United Nations | XML |
| OFAC SDN list, and the non-SDN consolidated list | US Treasury | CSV + alias CSV |
| EU financial sanctions | European Commission | CSV |
| UK sanctions list | Foreign, Commonwealth & Development Office | CSV |

Only entities are kept — companies, organisations, banks — about 14,000 of
them with some 42,000 names and aliases. Individuals, vessels and aircraft are
left out: the customer book is a book of companies, and screening a company
name against people's names produces noise, not hits.

A hit is rare and should be: it is a compliance stop, not a scoring nuance.
The value of running it on every customer is the "0 of N" — evidence that the
book was screened, with the date and the lists' versions in the run log.

## Install

```powershell
pip install -r requirements.txt
```

## Loading it

```powershell
python -m sanctions_lists load
python -m sanctions_lists stats
```

Seven files, about 85 MB in all; a load takes a few minutes. The lists change
often, so load before every screening run. None of the hosts disallows these
files (UN, OFAC and EU publish no robots.txt; the UK host refuses `/robots.txt`
itself, which under RFC 9309 means no rules) — they are published to be
downloaded and screened against.

Every file is checked before anything is written: it must start the way the
list does, end the way it does (OFAC's files end with an end-of-file
character, the UN's with its closing tag), and be as long as the server said
(`Content-Length`). A download cut off half-way is therefore an error, not a
shorter list — which matters, because a short list screens clean. If any file
fails, the run fails and the stored lists stay as they were. An entity that
is delisted is kept, marked as no longer listed, with the date it was last seen.

## Screening the customer book

```powershell
python -m sanctions_lists match customers.csv --out sanctions_screening.csv
```

The customer file is the same as for the other sources: `customer_id`, `name`,
`cr_number` (and optionally `search_name`). One row per customer:

| column | meaning |
|---|---|
| `sanctions_hit` | 1 if the name matches a listed entity |
| `sanctions_lists` | which lists |
| `matched_name` | the listed name or alias matched |
| `best_score` | the closest name on any list (0–100), kept for near misses |

A hit needs a near-identical name once legal forms are dropped and words are
sorted (95 of 100) **and** the same brand word (rules in `namematch.py`), so
"Northwind Steel" does not hit a listed "Northwind Plastic Industries".
Anything scoring 90+ without a hit is worth a look by a person.

## The run log

Every command is recorded in the `run` table — start, finish, duration, rows,
a note ("UN 274, OFAC SDN 10040, OFAC non-SDN 368, EU 1775, UK 1667") and,
if it failed, the error: `python -m sanctions_lists runs`.

## Tests

```powershell
python -m unittest discover -s tests
```

Offline, against a stand-in server and made-up files in each list's format,
including a download cut off half-way and one missing its end marker.

## Known limits

- Qatar's national list (NCTC) is not included: it reproduces the UN list,
  which is here, and its local designations are not published as a
  downloadable file.
- Screening is by name only. A real compliance check also looks at owners and
  directors (individuals), which this package deliberately does not do.
- The EU file is fetched with the public token the EU's Financial Sanctions
  Files page itself uses; if the EU changes it, the load fails with a clear
  error rather than screening against nothing.
