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
month, closed this week, awarded today) wherever it lands in the list.

What changed on 22 Sept 2026 and why -- matching, CR numbers, section
updates, features -- is in
[`qdb_alt/monaqasat/FIXES.md`](qdb_alt/monaqasat/FIXES.md).

To keep extracting overnight without leaving the laptop lid-open in front of
you: `scripts\install-task.ps1` registers a nightly Windows task. Set lid-close
to **Do nothing** while plugged in, or run it on a VM that stays on.
