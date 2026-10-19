# Qatar Online Directory

Another alternate-data source beside [`monaqasat`](../monaqasat) and
[`icv`](../icv): [qataronlinedirectory.com](https://qataronlinedirectory.com),
a business directory of Qatari companies. A listing says which trade
categories the company is listed under, the city, whether it gives a P O Box
and a phone number, and for some the brands it carries.

For a borrower this is a cheap outside check that a company under the name it
borrows in is publicly listed as a business, and in which line of business.

## Install

```powershell
pip install -r requirements.txt
```

`truststore` is there for the same reason as in `icv`: behind QDB's TLS proxy
`requests` needs the Windows certificate store.

## Searching the customer book

The directory has no CR numbers and no bulk download, so every customer is
searched by name, one request per customer, and the listings that are the
customer are then read one page each.

```powershell
python -m qatar_online_directory search customers.csv
python -m qatar_online_directory match customers.csv --out qatar_online_directory_features.csv
python -m qatar_online_directory stats
```

`search` keeps every answer in the SQLite database (`qatar_online_directory.db`,
which git ignores) and re-uses it for 30 days (`--max-age-days`), so running
it again over the same book only asks for names that are new, stale or failed
last time. It waits 5 seconds between requests, the crawl delay the site's
robots.txt asks for, and sends its own User-Agent. A search the site does
not answer is recorded as failed and asked again on the next run; it never
replaces an earlier good answer. After 5 failed requests in a row it stops
(the site is down or refusing) and keeps what it has.

`match` reads only the database, never the site.

The customer file is the same as for the other sources: `customer_id`, `name`,
`cr_number` (and optionally `search_name`, the trading name to search for when
the registered name differs). One row per customer comes out:

| column | meaning |
|---|---|
| `qod_listed` | 1 if the company is in the directory, 0 if the search found no listing that is it |
| `qod_listings` | how many listings are the company (branches can have their own) |
| `qod_city` | the city of the listing (Doha for all 84 found on the client list below) |
| `qod_main_category` | the first category the company is listed under |
| `qod_categories` | number of distinct categories, over all its listings |
| `qod_category_list` | the categories, `; `-separated |
| `qod_brands` | number of brands the listing carries |
| `qod_has_po_box`, `qod_has_phone` | 1 if the listing gives one (the number itself is not kept) |
| `matched_name`, `match_score`, `listing_url` | what was matched, for a person to check |

Empty means unknown, not zero: a customer whose search failed (or has not been
run) has every column empty; one that was searched and is not listed has
`qod_listed = 0` and the rest empty; a listed company whose listing page could
not be read has `qod_listed = 1` and the page's columns empty.

### How the name match works

The search sent to the site is the customer's `search_name`, or the name
without brackets and legal form ("QATAR BELGIUM ALUMINUM CO L.L.C" ->
"Qatar Belgium Aluminum"). The site's search is a plain "name contains"
search, so the results are filtered with the strict rule shared by every
name-searched source in this repository (`namematch.same_company`): every
distinctive word of the customer's name must be in the listing's name, the
first one (the brand) must be the same word, and no other brand may stand in
front of it -- *Noor Plastic* is not *Abdulnoor Plastic*. Spelling variants,
plurals, glued words and the branch form ("Doha Academy Al Waab Campus") are
allowed. If several listings pass, those within 5 points of the closest name
are kept. The rules and the real cases behind them are in `namematch.py` and
`tests/test_namematch.py`.

## How a page is read

A search result is a list of links, `/company-details/<id>/<slug>`, with the
company name as the link text; nothing else is on the result card. A page
that does not say "You searched for ... which contains" is not a result page
(an error or block page) and counts as a failed search, not as "not listed".

A listing page has the name in its `<h1>`, a *Company Details* box (P O Box,
city, phone), the categories the company is listed under and, on some pages,
its brands. The page template also has a website button, but it was empty on
every listing read (October 2026) and no listing shows an e-mail address, so
neither is a column. Parts the site has commented out in its HTML are
ignored. Phone and P O Box numbers are read only as present / not present and
are never stored.

## The run log

Every command is recorded in the `run` table: when it started and finished,
how long it took, how many rows it wrote, a note ("319 names: 319 searched,
0 from cache, 0 searches failed; 84 listed; 84 listing pages read, 0 failed")
and, if it failed, the error.

```powershell
python -m qatar_online_directory runs
```

## Tests

```powershell
python -m unittest discover -s tests
```

They run offline: a fake site serves the made-up pages in `fixtures/`, which
copy the real layout.

## Known limits

- Being listed is a weak signal on its own: the site offers free listings and
  does not say when a listing was last checked. A listed company may have
  closed; an active one may never have listed.
- Name only, no CR: two companies with the same name are indistinguishable,
  and a company listed under a trading name other than the one in the book is
  missed unless `search_name` gives it.
- The site searches for listed names that *contain* the typed text, so a book
  name with a word the listing lacks finds nothing, and the name rule never
  sees the listing. `search_name` is the fix: give the shorter or trading name.
- One request per customer, 5 seconds apart: the first run over 319 customers
  took 34 minutes (319 searches and 84 listing pages). Re-runs within 30 days
  take seconds.
- The site's robots.txt (checked October 2026) lets every crawler read
  `/search` and the listing pages, closes `/register`, `/middleast` and
  `/dashboard` (never visited here) and asks for a 5-second crawl delay. If
  it changes, check it again before running.
- On the 319-company client list used to build this, 84 were found (all with
  one listing, all in Doha); 235 were searched and not found, none failed.
  Two searches returned only other companies ("Dserv" brought back an
  "...Foodservice..." company, "D Clad" a cladding company) and were rightly
  left at 0.
