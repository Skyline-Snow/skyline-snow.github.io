"""Builds data/past_week.json: what actually happened at Mt. Bachelor over the last 7 days, for the
app's "Past week" columns (Base view). The app fetches the day-before forecast itself, at base elevation.

Each day runs 06:00 -> 06:00 like the app's forecast days, which lines up with the resort's 7 am report
(the report dated D+1 covers snow that fell on day D). Called at the end of collect.py and verify.py.
"""
import csv, datetime as dt
from lib import DATA, today, write_json

OUT = DATA / "past_week.json"
DAYS = 7
# App elevation -> resort weather station used for measured temperature and wind.
# Pine Marten stopped reporting on 2026-11-18, so mid uses Cloud Chaser (same 7,700 ft).
ELEV_STATION = {"top": "Northwest", "mid": "Cloud Chaser", "base": "West Village"}
SNOTEL_TARGETS = ["815", "545"]


def num(v):
    try:
        return float(v) if v not in ("", "None", None) else None
    except ValueError:
        return None


def read_csv(path):
    return list(csv.DictReader(open(path))) if path.exists() else []


def build():
    t0 = today()
    dates = [(t0 - dt.timedelta(days=k)).isoformat() for k in range(DAYS, 0, -1)]

    # Official 7 am report: report dated D+1 -> snow on day D.
    report = {r["report_date"]: r for r in read_csv(DATA / "observed" / "resort_snow_report.csv")}
    snotel = {}
    for r in read_csv(DATA / "observed" / "snotel_daily.csv"):
        if r["station_id"] in SNOTEL_TARGETS and num(r["new_water_mm"]) is not None:
            snotel.setdefault(r["date"], []).append(num(r["new_water_mm"]))
    stake = {r["date"]: num(r["change_in"]) for r in read_csv(DATA / "observed" / "resort_daily.csv")}

    # Resort sensors, grouped into 06:00 -> 06:00 days per station.
    sensors = {}
    for r in read_csv(DATA / "mtb_hourly.csv"):
        t = dt.datetime.fromisoformat(r["time_local"])
        day = (t - dt.timedelta(hours=6)).date().isoformat()
        sensors.setdefault((r["station"], day), []).append(r)

    out = []
    for d in dates:
        nxt = (dt.date.fromisoformat(d) + dt.timedelta(days=1)).isoformat()
        rep = report.get(nxt)
        day = {
            "date": d,
            "report_in": num(rep["new_24h_in"]) if rep else None,
            "snotel_mm": round(sum(snotel[d]) / len(snotel[d]), 1) if d in snotel else None,
            "stake_change_in": stake.get(d),
            "obs": {},
        }
        for ek, st in ELEV_STATION.items():
            rows = sensors.get((st, d), [])
            temps = [num(r["temp_f"]) for r in rows if num(r["temp_f"]) is not None]
            winds = [(num(r["wind_avg_mph"]), num(r["wind_dir"])) for r in rows if (num(r["wind_avg_mph"]) or 0) > 0]
            if len(temps) < 12:          # fewer than half a day of readings: leave blank
                continue
            w = max(winds, default=None)
            day["obs"][ek] = {"station": st, "hours": len(temps), "tmax_f": round(max(temps), 1),
                              "tmin_f": round(min(temps), 1),
                              "wind_mph": round(w[0], 1) if w else None, "dir": round(w[1]) if w else None}
        out.append(day)
    write_json(OUT, {"generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "days": out})
    return OUT.name


if __name__ == "__main__":
    print("past week:", build())
