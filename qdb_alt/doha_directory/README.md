# Doha Directory

An alternate-data source beside [`monaqasat`](../monaqasat) and [`icv`](../icv):
the free business directory at [www.doha.directory](https://www.doha.directory).
It has about 4,400 businesses in Qatar: shops, restaurants, contractors,
factories, schools and clinics. Each listing has a category, an address,
contact details, a website and social links, opening hours, a description
and, for some, a public star rating with the number of ratings.

For a borrower it answers "does this business show itself to customers, in
which line of trade, how reachable is it, and do customers rate it". A listing
with a website, opening hours and a hundred ratings says something different
from a bare name and address.

## Install

```powershell
pip install -r requirements.txt
```

`truststore` is there for the same reason as in `icv`: behind QDB's TLS proxy
`requests` needs the Windows certificate store.

## Loading it

```powershell
python -m doha_directory load
python -m doha_directory stats
```

robots.txt allows every path (`User-agent: *` / `Allow: /`) and points to a
sitemap that lists every listing page (`/<name>/i/<id>`) with its
last-modified date. `load` reads that sitemap and then each listing page, one
request every 2 seconds, under its own User-Agent
(`qdb-alt/doha_directory (+https://github.com/narensahu13/qdb-alt)`). The
first load is about 4,400 pages, some three hours. It commits as it goes, so
an interrupted load continues where it stopped. Pages are kept (compressed)
in the database with the sitemap's date, so the next `load` fetches only
listings that were added or changed, and a listing that leaves the sitemap is
marked as no longer listed rather than deleted.

A page that does not answer is tried once more after a longer pause. If it
still fails, the rest of the load is kept and the run ends as an error that
says how many pages failed; the next `load` fetches only those. A page the
site has removed (404) while the sitemap still lists it is counted as removed,
not as a failure.

Why the whole directory and not a search per customer: the sitemap names every
listing, so one load lets any customer book be matched offline, with the same
name rules as the other sources, instead of depending on the site's own
search.

## Matching the customer book

```powershell
python -m doha_directory match customers.csv --out doha_directory_features.csv
```

The customer file is the same as for the other sources: `customer_id`, `name`,
`cr_number` (and optionally `search_name`, the trading name to search for when
the registered name differs). One row per customer comes out:

| column | meaning |
|---|---|
| `dd_listed` | 1 if the business is in the directory, 0 if not |
| `dd_listings` | how many listings are the same business (branches, outlets) |
| `dd_category`, `dd_category_path` | the narrowest category, and the full path (`Services / Construction & Manufacturing / Manufacturing`) |
| `dd_rating`, `dd_reviews` | the star rating (1-5) and the number of ratings; `dd_reviews = 0` and no rating when nobody has rated it |
| `dd_has_phone`, `dd_has_email` | 1 if a phone number / an e-mail address is given (they are not stored) |
| `dd_website` | the website given on the listing |
| `dd_social_links` | number of social networks linked (Facebook, Instagram, LinkedIn ...) |
| `dd_open_days` | weekdays with opening hours; empty when no hours are given |
| `dd_founded_year`, `dd_age_years` | founding year, when the description states it |
| `dd_last_updated` | when the listing was last changed, from the sitemap |
| `dd_address` | the address on the listing |
| `matched_name`, `match_score`, `url` | what was matched, for a person to check |

Empty means unknown, not zero: a company that is not in the directory has
`dd_listed = 0`, `dd_listings = 0` and every other column empty. If a listing
that could be the customer's was never read (its page failed), the whole row
is empty, and the `match` run note counts these customers as unknown.

The directory has no CR numbers, so the join is by name and it is strict:
`same_company` in `namematch.py` (the same file as in `made_in_qatar`), with
the real cases behind it in `tests/test_namematch.py`. A listing is compared
both under its page title and under the name in its web address, so a listing
titled in Arabic is still found. When several listings pass, those within 5
points of the closest name are kept as the same business; the columns come
from the closest one (the most rated, on a tie).

## The run log

Every command is recorded in the `run` table: start, finish, duration, rows
written, a note ("4363 listings, 12 pages fetched, 0 failed, 0 removed from
the site, 1 no longer listed") and, if it failed, the error.

```powershell
python -m doha_directory runs
```

## Tests

```powershell
python -m unittest discover -s tests
```

They run offline against a stand-in site (`tests/fake_site.py`) and a made-up
listing page in the real markup.

## Known limits

- The directory is a free, self-service listing site: `dd_listed = 0` means
  "not in this directory", not "not trading". Its coverage leans to consumer
  businesses (restaurants, shops, schools, services) over factories.
- Ratings are the site's own and many listings have none; a small count says
  little.
- A name-only join can still pair two businesses that share their
  distinctive words. The rules reject the cases seen so far ("Doha Academy" is
  not "Qatar Academy Doha", "La Casa Industry" is not "La Mia Casa"), but check
  `matched_name` and `match_score` before using a low score.
- The area on the search pages (Doha, Lusail, ...) is not on the listing
  page; the full address is kept instead, as written by the business.
- The founding year is read from free text ("Established in 1980"); most
  descriptions do not give one.
