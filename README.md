# QDB alternate data

The working app lives in [`qdb_alt/monaqasat`](qdb_alt/monaqasat).

```powershell
cd qdb_alt\monaqasat
pip install -r requirements.txt
python -m monaqasat harvest --max-hours 6
```

A first load takes more than one sitting. `harvest` stops cleanly at the time
limit and the next run continues. After backfill, the same command only
stores new tenders and updates ones that changed state (for example open last
month, awarded this month).

To keep extracting overnight without leaving the laptop lid-open in front of
you: `scripts\install-task.ps1` registers a nightly Windows task. Set lid-close
to **Do nothing** while plugged in, or run it on a VM that stays on.
