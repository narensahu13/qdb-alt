# Made in Qatar Gate

A fourth alternate-data source beside [`monaqasat`](../monaqasat),
[`icv`](../icv) and [`qdb_sme_directory`](../qdb_sme_directory): the factory
register at [madeinqatar.qa](https://madeinqatar.qa), where Qatari factories
present themselves to buyers — about 220 factories, each with an address, an
industrial sector, a description, a website and product pages.

For a borrower it answers "is this a registered, operating factory, in which
sector, since when", and the size of its product catalogue says something about
how established it is.

## Install

```powershell
pip install -r requirements.txt
```

`truststore` is there for the same reason as in `icv`: behind QDB's TLS proxy
`requests` needs the Windows certificate store.

## Loading it

```powershell
python -m made_in_qatar load
python -m made_in_qatar stats
```

The site's robots.txt disallows its WordPress API (`/wp-json/`), so the register
is read the way a search engine reads it: the factories sitemap, then each
factory page, one request every 2 seconds. The first load is about 220 pages,
some eight minutes. Pages are kept (compressed) in the database with the
sitemap's last-modified date, so the next `load` fetches only factories that
were added or changed — usually a handful — and a factory that disappears from
the sitemap is marked as no longer listed rather than deleted. If any page
cannot be read, the run fails and says which; nothing half-loaded is passed off
as the register.

## Matching the customer book

```powershell
python -m made_in_qatar match customers.csv --out made_in_qatar_features.csv
```

The customer file is the same as for the other sources: `customer_id`, `name`,
`cr_number` (and optionally `search_name`, the trading name to search for when
the registered name differs). One row per customer comes out:

| column | meaning |
|---|---|
| `miq_listed` | 1 if the company is in the register, 0 if not |
| `miq_sector` | the industrial sector the factory is filed under |
| `miq_founded_year`, `miq_age_years` | founding year, when the description states it |
| `miq_products` | number of product pages linked from the factory page |
| `miq_website` | the company website given on the page |
| `matched_factory`, `match_score`, `factory_url` | what was matched, for a person to check |

Empty means unknown, not zero: a company that is not in the register has
`miq_listed = 0` and every other column empty.

The register has no CR numbers, so the join is by name and it is strict: the
name must be close and carry the same brand word — *Al Thuraiya Plastic
Factory* is not *Al Rayyan Plastic Factory*, *Qatar International Plastic*
is not *Qatar National Plastic*. The rules are in `namematch.py` (the same file
as in `qdb_sme_directory`) with the real cases behind them in
`tests/test_namematch.py`.

## The run log

Every command is recorded in the `run` table: start, finish, duration, rows
written, a note ("222 factories, 3 pages fetched, 0 no longer listed") and, if
it failed, the error.

```powershell
python -m made_in_qatar runs
```

## Tests

```powershell
python -m unittest discover -s tests
```

They run offline against a stand-in site (`tests/fake_site.py`) and a made-up
factory page in the real markup.

## Known limits

- The register is self-declared and voluntary: `miq_listed = 0` means "not on
  the portal", not "not a factory".
- The founding year is read from free text ("since its establishment in
  2015"); many descriptions do not give one.
- `miq_products` counts product pages linked from the factory page, not the
  factory's whole range.
