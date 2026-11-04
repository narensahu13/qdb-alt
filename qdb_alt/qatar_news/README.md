# Qatar news

An alternate-data source beside [`monaqasat`](../monaqasat) and [`icv`](../icv):
what four Qatari news outlets have published about a company --
[Gulf Times](https://www.gulf-times.com), [Qatar Tribune](https://www.qatar-tribune.com),
[The Peninsula](https://thepeninsulaqatar.com) and the Qatar News Agency
([QNA](https://www.qna.org.qa)), in English.

For a borrower it answers "is this business visible in the national press, how
recently, in how many papers, and is the news bad". A contractor that wins
public contracts, a factory that opens a line or exports, a clinic that is
fined -- these reach the papers; most small businesses never do, so an empty
record is the usual case and says little on its own.

Two signals:

* **Headlines** of every article the four outlets list in their sitemaps --
  about 1.7 million, back to 2011 for Gulf Times. Read once, kept in SQLite,
  matched offline.
* **Gulf Times' own search** for the company's name as an exact phrase. It
  reads the whole article text, not only the headline, and says how many
  articles it found.

## Install

```powershell
pip install -r requirements.txt
```

`truststore` is there for the same reason as in `icv`: behind QDB's TLS proxy
`requests` needs the Windows certificate store.

## What robots.txt allows, and what this package therefore reads

Every host's robots.txt is read before its first request, and every URL is
checked against it (RFC 9309: longest rule wins, wildcards, merged groups; see
`robots.py`) before it is requested. A URL it does not allow is never asked
for. The pause between two requests to one host is never shorter than its
Crawl-delay, nor than 2 seconds. The User-Agent says who is asking:
`qdb-alt/qatar_news (+https://github.com/narensahu13/qdb-alt)`.

| outlet | robots.txt (checked 4 Nov 2026) | what is read |
|---|---|---|
| Gulf Times | `/search` allowed; `/archive/*`, `*/page/*`, `/rss` disallowed; `Crawl-delay: 2` (in a second `User-agent: *` group) | the sitemaps, and the first page of `/search?query=<name>&match=exact` per customer |
| Qatar Tribune | `/search*` disallowed; no Crawl-delay | the sitemaps only |
| The Peninsula | `/search/` disallowed; `Crawl-delay: 10` | the sitemaps only, one every 10 seconds |
| QNA | `/umbraco/` disallowed -- its site search runs through `/umbraco/api/`, so it is not used | the English sitemaps only (`Language=en`) |

No article page is ever opened: the headline comes from the sitemap (The
Peninsula's carries the real headline; the others' only the article address,
whose words are the headline).

## Loading the headline index

```powershell
python -m qatar_news load
python -m qatar_news stats
```

`load` reads each outlet's sitemap index, then every monthly sitemap it has
not read yet. The first load is about 590 sitemaps and took 43 minutes,
most of it The Peninsula's 10-second Crawl-delay. It commits after every
sitemap, so an interrupted load continues where it stopped; `--max-minutes 20`
stops cleanly at a time limit (the run is logged as `stopped`) and the next
`load` carries on. `--papers gulf_times,qna` limits it to some outlets
(`gulf_times`, `qatar_tribune`, `qna`, `peninsula`).

After that a `load` is short: a month is not fetched again once a whole copy
was read after the month ended (plus two days for late additions), unless
Gulf Times' or Qatar Tribune's index shows it changed since. So a monthly
`load` reads the indexes, the current month, and QNA's newest pages. QNA lists
its items 5,000 to a page, newest first, so each new item pushes the others
one place along: page 1 is always read, and the next page only while the one
before brought new articles.

A sitemap must arrive whole -- well-formed and ending with its closing tag --
or nothing of it is kept; it is tried once more and, if still cut off, counted
as failed and fetched again on the next `load`. A sitemap the index lists but
the site no longer has (404) is counted as gone, not as failed. If a host's
robots.txt cannot be read (a server error), nothing else is asked of that host.

## Matching the customer book

```powershell
python -m qatar_news match customers.csv --out qatar_news_features.csv
```

The customer file is the same as for the other sources: `customer_id`, `name`,
`cr_number` (and optionally `search_name`, the trading name to search for when
the registered name differs). Headlines are matched offline; Gulf Times'
search is one request per customer, 2 seconds apart, and each answer is
cached for 30 days (`--search-max-age-days`), so a second `match` within the
month makes no requests. `--no-search` matches headlines only.

| column | meaning |
|---|---|
| `news_gulf_times_articles` | articles Gulf Times' search finds for the exact name (whole text) |
| `news_headlines` | headlines in the four outlets that carry the name (the same headline twice counts once) |
| `news_headlines_365d` | of those, published in the last 365 days |
| `news_papers` | how many of the four outlets carried such a headline |
| `news_days_since_last` | days since the newest such headline |
| `news_negative_count` | distinct headlines (and Gulf Times result titles) with a word like fined, fire, closure, lawsuit, fraud, recall, suspended, liquidation, default |
| `news_name_generic` | 1 if the name is too generic to count news for (below); 0 otherwise |
| `search_name`, `example_headlines`, `example_urls`, `gulf_times_search_url` | what was searched and up to three of the newest headlines found, for a person to check |

Empty means unknown, not zero. A failed Gulf Times search (no answer, a block
page, or robots.txt forbidding it) leaves `news_gulf_times_articles` empty,
and the `match` run note counts these. After five failures in a row the rest of
the book is not searched in that run.

**How a name is found in a headline.** The name without its legal form
(`search_name` in `namematch.py`) must stand in the headline as consecutive
words, each compared with the same word rules as the other sources (a plural,
a glued spelling, one letter off in a long word). A one-word name ("Delta")
is searched together with the word after it on the register ("Delta
Corporation").

**The generic-name rule.** A short name -- three words or fewer, counting words
of three letters or more -- made only of common words is found in headlines
that are not about the company ("Doha Bus": "new Doha bus routes"). For such a
name no news is counted: every news column is empty and `news_name_generic` =
1. A word is common when The Peninsula, whose headlines keep their capitals,
writes it in lower case at least half the times it appears (seen at least 5
times; the first word of a headline is not counted): "bus", "solar", "energy"
are common, "Barzan", "Oryx" are not. Words shared by most Qatari company
names (Qatar, Doha, Al, Group, Factory ...) never count as distinctive. A
longer name of common words ("Qatar Steel Industries Factory") is specific as
a whole phrase and is searched.

## The run log

Every command is recorded in the `run` table: start, finish, duration, rows
written, a note ("Gulf Times: 1 sitemaps read, 191 unchanged, 0 failed,
1 gone, 0 disallowed, 104 new articles; ...") and, if it failed, the error.

```powershell
python -m qatar_news runs
```

## Tests

```powershell
python -m unittest discover -s tests
```

They run offline against a stand-in for the four sites (`tests/fake_site.py`)
with made-up headlines, a stand-in clock, and trimmed copies of the real
robots.txt rules. They check, among the rest, that every URL requested is
allowed by its host's robots.txt, that robots.txt is read before anything
else on a host, and that the Crawl-delay is kept between every two requests.

## Known limits

- **Headlines only, for three of the four.** No article text is read. Gulf
  Times' search covers its full text; for Qatar Tribune, The Peninsula and QNA
  only a company named in the headline is found. A company named in the third
  paragraph is not.
- **Gulf Times' "exact phrase" is not strictly exact** for short names: it
  finds "black-clad" for "D Clad" and hundreds of articles for a name made of
  everyday words. The generic-name rule removes the worst of these; check
  `news_gulf_times_articles` against `example_headlines` before trusting a
  large count for a short name.
- **The generic-name rule is a rule of thumb.** It can blank a real but
  plainly-named company ("Qatar Solar Energy") and miss an ordinary word that
  The Peninsula happens to capitalise. Rows with `news_name_generic = 1` are
  for a person to look at.
- **A name is still only a name.** "Aqua Park" in Qatar and in Samarkand,
  "Metalica" and the band: matching uses names only (the papers print no CR
  numbers), so read `example_headlines` before using a feature.
- **QNA in English only.** The Arabic, French, Spanish and other lists are not
  read.
- **Coverage by outlet** (a first load on 4 Nov 2026): Gulf Times about
  490,000 articles from Jan 2011 (to Nov 2022 from its archive sitemaps),
  Qatar Tribune 346,000 from Feb 2016 (five monthly sitemaps of 2020-2022 that
  its index lists answer 404), The Peninsula 442,000 from Sep 2012, QNA
  English 407,000, nearly all from Sep 2013. What an outlet removes from its
  sitemaps disappears from new loads but stays in the database.
- **A sitemap the site itself serves cut off** cannot be read, and every
  `load` reports it as failed until the site serves it whole (on 4 Nov 2026:
  Qatar Tribune, September 2026, cut at 472 KB on every request). The rest
  of the load is kept.
- Dates are the sitemap's: the date in The Peninsula's article address, else
  its Google News publication date; the `lastmod` for the others, which for
  QNA can be a later edit.
