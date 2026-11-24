"""Run after collect.py. Keeps a permanent record of what actually fell and scores every saved
forecast snapshot against it, so model accuracy can be compared after the fact.

Writes:
  data/observed/snotel_daily.csv   measured daily water, snow depth and temperature at the SNOTEL
                                   gauges (NRCS can revise recent days, so the last 45 days are refreshed)
  data/observed/resort_daily.csv   Mt. Bachelor West Village snow depth per day, from the hourly log
  data/verification.csv            every snapshot forecast next to what was measured that day
"""
import csv, datetime as dt, json, statistics, sys
from collections import defaultdict
from lib import DATA, SNAPSHOTS, STATIONS, get_json

OBS_DIR = DATA / "observed"
SNOTEL_CSV = OBS_DIR / "snotel_daily.csv"
RESORT_CSV = OBS_DIR / "resort_daily.csv"
VERIFY_CSV = DATA / "verification.csv"
TARGETS = [sid for sid, s in STATIONS.items() if s["target"]]
SNOTEL_FIELDS = ["date", "station_id", "station", "prec_total_in", "new_water_mm", "swe_in", "depth_in", "tavg_f"]


def read_csv(path):
    if not path.exists():
        return []
    with path.open() as f:
        return list(csv.DictReader(f))


def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def update_snotel():
    """Merge the last 45 days of SNOTEL readings into the permanent file."""
    rows = {(r["date"], r["station_id"]): r for r in read_csv(SNOTEL_CSV)}
    end = dt.date.today()
    begin = end - dt.timedelta(days=45)
    trip = ",".join(f"{sid}:OR:SNTL" for sid in STATIONS)
    raw = get_json("https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/data?"
                   f"stationTriplets={trip}&elements=PREC,WTEQ,SNWD,TAVG&duration=DAILY"
                   f"&beginDate={begin}&endDate={end}")
    for s in raw:
        sid = s["stationTriplet"].split(":")[0]
        el = {e["stationElement"]["elementCode"]: {v["date"][:10]: v.get("value") for v in e["values"]} for e in s["data"]}
        P = el.get("PREC", {})
        for d in sorted(set().union(*[set(v) for v in el.values()]) if el else []):
            prev = (dt.date.fromisoformat(d) - dt.timedelta(days=1)).isoformat()
            # Daily PREC is the end-of-day running total, so day D's water is PREC[D] - PREC[D-1].
            inc = None
            if P.get(d) is not None and P.get(prev) is not None and P[d] - P[prev] >= -1:
                inc = round(max(P[d] - P[prev], 0) * 25.4, 1)
            elif (d, sid) in rows:
                inc = rows[(d, sid)]["new_water_mm"]   # keep a value worked out on an earlier run
            rows[(d, sid)] = {"date": d, "station_id": sid, "station": STATIONS[sid]["name"],
                              "prec_total_in": P.get(d), "new_water_mm": inc, "swe_in": el.get("WTEQ", {}).get(d),
                              "depth_in": el.get("SNWD", {}).get(d), "tavg_f": el.get("TAVG", {}).get(d)}
    out = sorted(rows.values(), key=lambda r: (r["date"], r["station_id"]))
    write_csv(SNOTEL_CSV, SNOTEL_FIELDS, out)
    return out


def update_resort():
    """Daily West Village depth (last reading of the day) and change from the day before."""
    by_day = {}
    for r in read_csv(DATA / "mtb_hourly.csv"):
        if r["station"] != "West Village" or r["depth_in"] in ("", "None"):
            continue
        d = r["time_local"][:10]
        if d not in by_day or r["time_local"] > by_day[d][0]:
            by_day[d] = (r["time_local"], float(r["depth_in"]), by_day.get(d, (None, None, 0))[2] + 1)
        else:
            by_day[d] = (by_day[d][0], by_day[d][1], by_day[d][2] + 1)
    out, prev = [], None
    for d in sorted(by_day):
        t, depth, n = by_day[d]
        out.append({"date": d, "depth_in": round(depth, 1), "change_in": round(depth - prev, 1) if prev is not None else "",
                    "last_reading": t, "hourly_readings": n})
        prev = depth
    write_csv(RESORT_CSV, ["date", "depth_in", "change_in", "last_reading", "hourly_readings"], out)
    return out


def build_verification(snotel):
    water = defaultdict(dict)   # date -> station -> mm
    for r in snotel:
        if r["new_water_mm"] not in (None, ""):
            water[r["date"]][r["station_id"]] = float(r["new_water_mm"])
    today = dt.date.today().isoformat()
    rows = []
    for f in sorted(SNAPSHOTS.glob("*.json")):
        snap = json.loads(f.read_text())
        issued = snap["issued"]
        for pid, models in snap["points"].items():
            for model, days in models.items():
                for day, vals in days.items():
                    if day >= today or day not in water:
                        continue
                    if pid == "bachelor_mid":   # scored against the two-gauge Bachelor stand-in
                        if not all(s in water[day] for s in TARGETS):
                            continue
                        obs = sum(water[day][s] for s in TARGETS) / len(TARGETS)
                    elif pid in water[day]:
                        obs = water[day][pid]
                    else:
                        continue
                    lead = (dt.date.fromisoformat(day) - dt.date.fromisoformat(issued[:10])).days
                    rows.append({"issued": issued, "target_date": day, "lead_days": lead, "point": pid, "model": model,
                                 "forecast_mm": vals[0], "observed_mm": round(obs, 1), "error_mm": round(vals[0] - obs, 1)})
    write_csv(VERIFY_CSV, ["issued", "target_date", "lead_days", "point", "model", "forecast_mm", "observed_mm", "error_mm"], rows)
    return rows


def summary(rows):
    acc = defaultdict(list)
    for r in rows:
        if r["point"] == "bachelor_mid" and r["lead_days"] >= 0:
            bucket = "1-2" if r["lead_days"] <= 1 else "3-4" if r["lead_days"] <= 3 else "5-7" if r["lead_days"] <= 6 else "8+"
            acc[(r["model"], bucket)].append(abs(r["error_mm"]))
    for (m, b), errs in sorted(acc.items()):
        print(f"  {m:16s} days {b:4s} mean error {statistics.mean(errs):5.1f} mm  ({len(errs)} forecasts)")


if __name__ == "__main__":
    ok = True
    try:
        snotel = update_snotel()
        print("SNOTEL days on file:", len({r['date'] for r in snotel}))
    except Exception as e:
        ok = False; print("SNOTEL update failed:", e, file=sys.stderr)
        snotel = read_csv(SNOTEL_CSV)
    try:
        print("resort days on file:", len(update_resort()))
    except Exception as e:
        ok = False; print("resort summary failed:", e, file=sys.stderr)
    rows = build_verification(snotel)
    print("scored forecasts:", len(rows))
    summary(rows)
    sys.exit(0 if ok else 1)
