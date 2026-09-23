# QDB alternate data

The working app lives in [`qdb_alt/monaqasat`](qdb_alt/monaqasat).

```powershell
cd qdb_alt\monaqasat
pip install -r requirements.txt
python -m monaqasat harvest --max-hours 6
```

A first load takes more than one sitting. `harvest` stops cleanly at the time
limit and the next run continues. After backfill, the same command keeps the
store current: new tenders, and every tender that moved section (open last
month, closed this week, awarded today) wherever it lands in the list. Run it
nightly or once a month -- it does as much of its re-reading as the gap since
the last run calls for.

What changed and why -- matching, CR numbers, section updates, features, and
the storage work of 23 Sept -- is in
[`qdb_alt/monaqasat/FIXES.md`](qdb_alt/monaqasat/FIXES.md).

Pages are stored compressed, which keeps the database to roughly a tenth of
the HTML it holds. A database from before v0.5 is shrunk in place with
`python -m monaqasat compact`.

The live database is still too big for GitHub, which rejects any file over
100 MB, so it stays on the machine that crawled it.
`data/monaqasat.slim.db` is the same database with the pages removed (about
20 MB): tenders, companies, and crawl progress, and no customer matches. On
the work laptop:

```powershell
cd qdb_alt\monaqasat
copy data\monaqasat.slim.db monaqasat.db
python -m monaqasat status
python -m monaqasat harvest --max-hours 12
```

Refresh the snapshot after a harvest with `python -m monaqasat pack`. The
working `monaqasat.db` (with HTML) is gitignored.

To keep extracting overnight without leaving the laptop lid-open in front of
you: `scripts\install-task.ps1` registers a nightly Windows task. Set lid-close
to **Do nothing** while plugged in, or run it on a VM that stays on.
