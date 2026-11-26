# Skyline Snow

A 16-day mountain snow forecast, live at https://skyline-snow.github.io, for Mt. Bachelor (and any resort you add) that learns local corrections over time.

## Run it

```bash
python3 -m http.server 8765 --directory snowline
```

Then open http://localhost:8765.

## How it learns

| Script | When | What it does |
|---|---|---|
| `learn/collect.py` | every 3 h | Logs Mt. Bachelor's own sensors (temperature, wind, West Village snow depth) to `data/mtb_hourly.csv`, which the resort only keeps for 24 h. Saves the resort's official 7 am snow report (new snow, overnight, base depth, season total) to `data/observed/resort_snow_report.csv`; the feed only keeps about 10 days. Saves a snapshot of every model's forecast to `data/snapshots/`. |
| `learn/verify.py` | every 3 h | Saves measured daily snowfall from the SNOTEL gauges to `data/observed/snotel_daily.csv` and West Village daily depth to `data/observed/resort_daily.csv`, then scores every saved forecast snapshot against what actually fell in `data/verification.csv`. |
| `learn/past_week.py` | after collect and verify | Writes `data/past_week.json` for the app's past-week columns: each of the last 7 days' official snowfall (7 am report, shifted to the day it fell), SNOTEL water and West Village stake change, measured temperature and wind, and the forecast saved the day before. |
| `learn/stake_photo.py` | each morning | Saves one photo from the resort's snow-stake webcam (first run after 4 am, before the 7 am report) to the `stake-photos` branch. Keeps 7 days; an older photo is deleted only once that day's official report is logged. |
| `learn/calibrate.py` | daily | Re-fits corrections from SNOTEL measurements + archived and snapshot forecasts, tests each one on a season left out of its training, and writes `calibration.json`. Appends results to `data/score_history.csv`. |

Install the schedule (macOS):

```bash
bash learn/schedule.sh install
```

`bash learn/schedule.sh status` checks it; `uninstall` removes it. Logs go to `data/logs/`.

## Cloud and sync

GitHub Actions (`.github/workflows/update.yml`) runs `collect.py` every 3 hours and `calibrate.py` daily, saves the data to this repository and publishes the app to https://skyline-snow.github.io.

To keep a Mac copy current, `bash snowline/learn/autosync.sh install` pulls at login and daily at 07:00 (`status`, `now`, `uninstall` also work). The project must live outside Documents, Desktop and Downloads, which macOS blocks for background jobs.

## What is calibrated today

- **Snow ratio:** measured near Bachelor (settled depth per inch of water by temperature). This is in use and is the biggest change from raw model snowfall.
- **Precipitation amounts:** per model, lead time and upper-level wind direction. Each setting is used only if it beats the plain multi-model average on a season left out of its training. As of the first run, none does, so amounts are uncorrected.
- **Blend weights:** the same test decides whether better-scoring models get more weight.

## Data sources

- Open-Meteo forecast, ensemble and previous-runs APIs (GFS, ECMWF, GEM, ICON, NBM). Free for non-commercial use.
- USDA NRCS SNOTEL AWDB API: Three Creeks Meadow and Irish Taylor are the Bachelor stand-in; McKenzie and Roaring River are used for snow ratios.
- Mt. Bachelor's public 24-hour weather feed (the one behind mtbachelor.com's live weather page).
- Mt. Bachelor's official daily snow report feed (the one behind the Mountain Report page's new-snow and base-depth numbers).

## Caveats

- The truth data is SNOTEL gauges at 5,500–5,700 ft, not the ski area itself. Gauges under-catch snow by roughly 10–30%.
- Snow ratios are settled depth; a cleared storm board (what resorts report) usually shows 20–30% more.
- Days 8–16 reuse the day 5–7 setting and are not verified.
- Mt. Bachelor's sensor history starts 2026-11-20; it will be used for fitting once about 20 snow days are logged.
