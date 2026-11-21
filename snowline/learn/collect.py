"""Run every few hours. Builds the local history that the calibration learns from:

1. Mt. Bachelor's own hourly sensors (the public feed behind mtbachelor.com's 24-hour weather page),
   which only keeps 24 hours, so it has to be logged continuously to build a history.
2. A snapshot of every model's forecast for Bachelor and the target SNOTEL stations, so future
   calibrations can score exactly what was forecast at each lead time (including GFS beyond day 4,
   which the public archive gets wrong).
"""
import csv, datetime as dt, sys
from lib import DATA, SNAPSHOTS, STATIONS, BACHELOR, TZ, get_json, vec_mean_dir, write_json

MTB_URL = "https://api.mtbachelor.com/api/v1/dor/24-hour-weather"
MTB_STATIONS = {  # feed key -> (label, elevation ft)
    "snowplot": ("West Village", 6300), "sunrise": ("Sunrise", 7300), "pine": ("Pine Marten", 7700),
    "cloudChaser": ("Cloud Chaser", 7700), "northwest": ("Northwest", 8000),
}
MTB_CSV = DATA / "mtb_hourly.csv"
FIELDS = ["station", "elev_ft", "time_local", "temp_f", "depth_in", "rh", "wind_avg_mph", "wind_dir", "wind_max_mph"]

SNAP_MODELS = ["gfs_seamless", "gfs_global", "ecmwf_ifs025", "gem_seamless", "icon_seamless", "ncep_nbm_conus"]


def log_resort():
    seen = set()
    if MTB_CSV.exists():
        with MTB_CSV.open() as f:
            for r in csv.DictReader(f):
                seen.add((r["station"], r["time_local"]))
    d = get_json(MTB_URL)
    new = []
    for key, rows in d.items():
        label, elev = MTB_STATIONS.get(key, (key, ""))
        for r in rows or []:
            # The feed labels times with "Z" but they are Pacific local time (they match the website's table).
            t = (r.get("timestamp") or "")[:16]
            if not t or (label, t) in seen:
                continue
            seen.add((label, t))
            new.append({"station": label, "elev_ft": elev, "time_local": t, "temp_f": r.get("temperature"),
                        "depth_in": r.get("depth"), "rh": r.get("rh"), "wind_avg_mph": r.get("windSpeedAvg"),
                        "wind_dir": r.get("windDirAvg"), "wind_max_mph": r.get("windSpeedMax")})
    write_header = not MTB_CSV.exists()
    with MTB_CSV.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        if write_header:
            w.writeheader()
        w.writerows(sorted(new, key=lambda x: (x["time_local"], x["station"])))
    return len(new)


def snapshot_forecasts():
    pts = [("bachelor_mid", BACHELOR["lat"], BACHELOR["lon"], BACHELOR["elev_m"]["mid"])]
    pts += [(sid, s["lat"], s["lon"], s["elev_m"]) for sid, s in STATIONS.items() if s["target"]]
    url = ("https://api.open-meteo.com/v1/forecast?"
           f"latitude={','.join(str(p[1]) for p in pts)}&longitude={','.join(str(p[2]) for p in pts)}"
           f"&elevation={','.join(str(p[3]) for p in pts)}"
           "&hourly=precipitation,temperature_2m,wind_direction_700hPa"
           f"&models={','.join(SNAP_MODELS)}&forecast_days=16&timezone={TZ}")
    res = get_json(url)
    res = res if isinstance(res, list) else [res]
    issued = dt.datetime.now().strftime("%Y-%m-%dT%H:%M")
    out = {"issued": issued, "points": {}}
    for (pid, *_), loc in zip(pts, res):
        h = loc["hourly"]
        out["points"][pid] = {}
        for m in SNAP_MODELS:
            P, T, D = (h.get(f"{v}_{m}") or [] for v in ("precipitation", "temperature_2m", "wind_direction_700hPa"))
            days = {}
            for i, t in enumerate(h["time"]):
                if i >= len(P) or P[i] is None:
                    continue
                d = days.setdefault(t[:10], {"p": 0.0, "n": 0, "t": [], "dir": [], "w": []})
                d["p"] += P[i]; d["n"] += 1
                if i < len(T) and T[i] is not None:
                    d["t"].append(T[i])
                if i < len(D) and D[i] is not None:
                    d["dir"].append(D[i]); d["w"].append(P[i] + 0.05)
            out["points"][pid][m] = {
                day: [round(v["p"], 2), round(sum(v["t"]) / len(v["t"]), 1) if v["t"] else None,
                      round(vec_mean_dir(v["dir"], v["w"]) or 0) if v["dir"] else None]
                for day, v in days.items() if v["n"] >= 20}
    path = SNAPSHOTS / f"{issued[:13].replace(':', '')}.json"
    write_json(path, out)
    return path.name


if __name__ == "__main__":
    ok = True
    try:
        print("resort sensor rows added:", log_resort())
    except Exception as e:
        ok = False; print("resort sensors failed:", e, file=sys.stderr)
    try:
        print("forecast snapshot:", snapshot_forecasts())
    except Exception as e:
        ok = False; print("forecast snapshot failed:", e, file=sys.stderr)
    sys.exit(0 if ok else 1)
