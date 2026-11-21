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
| `learn/collect.py` | every 3 h | Logs Mt. Bachelor's own sensors (temperature, wind, West Village snow depth) to `data/mtb_hourly.csv`, which the resort only keeps for 24 h. Saves a snapshot of every model's forecast to `data/snapshots/`. |
| `learn/calibrate.py` | daily | Re-fits corrections from SNOTEL measurements + archived and snapshot forecasts, tests each one on a season it never saw, and writes `calibration.json`. Appends results to `data/score_history.csv`. |

Install the schedule (macOS):

```bash
bash learn/schedule.sh install
```

`bash learn/schedule.sh status` checks it; `uninstall` removes it. Logs go to `data/logs/`.

## What is calibrated today

- **Snow ratio:** measured near Bachelor (settled depth per inch of water by temperature). This is in use and is the biggest change from raw model snowfall.
- **Precipitation amounts:** per model, lead time and upper-level wind direction. Each setting is only switched on if it beats the plain multi-model average on a held-out season. As of the first run, none does, so amounts are uncorrected.
- **Blend weights:** the same held-out test decides whether better-scoring models get more weight.

## Data sources

- Open-Meteo forecast, ensemble and previous-runs APIs (GFS, ECMWF, GEM, ICON, NBM). Free for non-commercial use.
- USDA NRCS SNOTEL AWDB API: Three Creeks Meadow and Irish Taylor are the Bachelor stand-in; McKenzie and Roaring River are used for snow ratios.
- Mt. Bachelor's public 24-hour weather feed (the one behind mtbachelor.com's live weather page).

## Caveats

- The truth data is SNOTEL gauges at 5,500–5,700 ft, not the ski area itself. Gauges under-catch snow by roughly 10–30%.
- Snow ratios are settled depth; a cleared storm board (what resorts report) usually shows 20–30% more.
- Days 8–16 reuse the day 5–7 setting and are not verified.
- Mt. Bachelor's sensor history starts 2026-11-20; it will be used for fitting once about 20 snow days are logged.
