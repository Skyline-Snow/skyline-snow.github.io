"""Run daily. Re-learns Skyline Snow's local corrections from everything collected so far and writes
snowline/calibration.json, which the app reads.

What it learns
- Precipitation correction factors per model, lead time (1-2, 3-4, 5-7 days) and upper-level flow
  direction (S-SW, W, NW-N, other), scored against SNOTEL gauges that stand in for Mt. Bachelor.
- Blend weights: models that verify better at each lead time count for more.
- Snow-to-liquid ratios by temperature, from SNOTEL depth and water changes.
- Out-of-sample validation (train on one season, test on the other), so the app can say whether
  the corrections actually help.

Ground truth today is SNOTEL (water). Mt. Bachelor's own snow-depth sensor is logged by collect.py and
is reported here as it accumulates; it is not used to fit anything until there is enough of it.
"""
import csv, datetime as dt, json, math, statistics, sys
from collections import defaultdict
from lib import (ROOT, DATA, ARCHIVE, SNAPSHOTS, STATIONS, ARCHIVE_MODELS, APP_MODEL_MAP, LEAD_BUCKETS,
                 SECTORS, TZ, sector, get_json, vec_mean_dir, write_json)

START = dt.date(2024, 10, 1)
K_MODEL = 60.0   # mm of "prior" pulling each model/lead factor toward 1.0
K_SECTOR = 30.0  # mm of prior pulling each flow-sector factor toward its model/lead factor
F_MIN, F_MAX = 0.4, 1.8
STORM_MM = 12.7  # 0.5 in of water, roughly 5-7 in of snow
DEFAULT_RATIO = [(20, 10.0), (26, 9.0), (30, 7.0), (34, 6.0)]


def season_of(day):
    y = day.year if day.month >= 7 else day.year - 1
    return f"{y}-{str(y + 1)[2:]}"


def in_snow_season(day):
    return day.month in (10, 11, 12, 1, 2, 3, 4, 5)


# ---------------- observations ----------------
def load_snotel():
    end = dt.date.today()
    trip = ",".join(f"{sid}:OR:SNTL" for sid in STATIONS)
    raw = get_json("https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/data?"
                   f"stationTriplets={trip}&elements=PREC,WTEQ,SNWD,TAVG&duration=DAILY"
                   f"&beginDate={START}&endDate={end}")
    obs = {}
    for s in raw:
        sid = s["stationTriplet"].split(":")[0]
        el = {e["stationElement"]["elementCode"]: {v["date"][:10]: v.get("value") for v in e["values"]} for e in s["data"]}
        obs[sid] = el
    return obs


def daily_water(el):
    """Daily precipitation (mm) for each date. SNOTEL daily PREC is the end-of-day total, so the
    increment for day D is PREC[D] - PREC[D-1] (verified: this alignment matches model days best)."""
    P = el.get("PREC", {})
    out = {}
    for d, v in P.items():
        prev = (dt.date.fromisoformat(d) - dt.timedelta(days=1)).isoformat()
        if v is None or P.get(prev) is None:
            continue
        inc = v - P[prev]
        if inc < -1:   # water-year reset on Oct 1
            continue
        out[d] = max(inc, 0.0) * 25.4
    return out


# ---------------- model archive ----------------
def months_to_fetch():
    m, end = dt.date(START.year, START.month, 1), dt.date.today()
    while m <= end:
        if in_snow_season(m):
            yield m
        m = dt.date(m.year + (m.month == 12), m.month % 12 + 1, 1)


def fetch_archive_month(sid, month):
    st = STATIONS[sid]
    path = ARCHIVE / f"{sid}_{month:%Y-%m}.json"
    fresh = (dt.date.today() - month).days < 45   # current or just-finished month: refetch
    if path.exists() and not fresh:
        return json.loads(path.read_text())
    last = min(dt.date(month.year + (month.month == 12), month.month % 12 + 1, 1) - dt.timedelta(days=1),
               dt.date.today() - dt.timedelta(days=1))
    leads = ",".join(["precipitation"] + [f"precipitation_previous_day{i}" for i in range(1, 8)])
    url = ("https://previous-runs-api.open-meteo.com/v1/forecast?"
           f"latitude={st['lat']}&longitude={st['lon']}&elevation={st['elev_m']}"
           f"&hourly={leads},wind_direction_700hPa&models={','.join(ARCHIVE_MODELS)}"
           f"&start_date={month}&end_date={last}&timezone={TZ}")
    h = get_json(url)["hourly"]
    days = defaultdict(lambda: {"n": 0})
    out = {"model": {}, "dir": {}}
    for m in ARCHIVE_MODELS:
        out["model"][m] = {}
        for L in range(0, 8):
            key = f"precipitation_{m}" if L == 0 else f"precipitation_previous_day{L}_{m}"
            v = h.get(key)
            if not v:
                continue
            agg, cnt = defaultdict(float), defaultdict(int)
            for t, x in zip(h["time"], v):
                if x is not None:
                    agg[t[:10]] += x; cnt[t[:10]] += 1
            out["model"][m][str(L)] = {d: round(agg[d], 2) for d in agg if cnt[d] >= 20}
    # flow direction of each day: precipitation-weighted 700 hPa wind (GFS and ECMWF analyses)
    dirs, wts = defaultdict(list), defaultdict(list)
    for m in ("gfs_global", "ecmwf_ifs025"):
        D, P = h.get(f"wind_direction_700hPa_{m}") or [], h.get(f"precipitation_{m}") or []
        for i, t in enumerate(h["time"]):
            if i < len(D) and D[i] is not None:
                dirs[t[:10]].append(D[i]); wts[t[:10]].append((P[i] or 0) + 0.05 if i < len(P) else 0.05)
    out["dir"] = {d: vec_mean_dir(dirs[d], wts[d]) for d in dirs}
    write_json(path, out)
    return out


def load_samples():
    """samples[(sid, model, lead, date)] = forecast mm; flow[date] = 700 hPa direction."""
    samples, flow, src = {}, {}, defaultdict(int)
    for sid, st in STATIONS.items():
        if not st["target"]:
            continue
        for month in months_to_fetch():
            try:
                a = fetch_archive_month(sid, month)
            except Exception as e:
                print(f"  archive {sid} {month:%Y-%m} unavailable: {e}", file=sys.stderr)
                continue
            for m, leads in a["model"].items():
                for L, days in leads.items():
                    if int(L) < 1:
                        continue
                    for d, v in days.items():
                        samples[(sid, m, int(L), d)] = v; src["archive"] += 1
            for d, v in a["dir"].items():
                if v is not None:
                    flow.setdefault(d, v)
    # Our own snapshots override the archive for the same station/model/lead/day.
    for f in sorted(SNAPSHOTS.glob("*.json")):
        snap = json.loads(f.read_text())
        issued = dt.date.fromisoformat(snap["issued"][:10])
        for sid, models in snap["points"].items():
            if sid not in STATIONS:
                continue
            for m, days in models.items():
                am = APP_MODEL_MAP.get(m, m)
                if m == "gfs_seamless" or am is None:
                    continue  # score the global GFS run only; NBM has no long archive yet
                for d, (p, t, dirn) in days.items():
                    lead = (dt.date.fromisoformat(d) - issued).days
                    if 1 <= lead <= 7 and dt.date.fromisoformat(d) < dt.date.today():
                        samples[(sid, am, lead, d)] = p; src["snapshot"] += 1
                        if dirn is not None:
                            flow.setdefault(d, dirn)
    return samples, flow, dict(src)


# ---------------- fitting ----------------
def bucket_of(lead):
    for b, (a, z) in LEAD_BUCKETS.items():
        if a <= lead <= z:
            return b
    return None


def suspect_leads(samples, obs_w):
    """Detect archive artifacts: a sudden drop in a model's wetness from one lead to the next
    (GFS 3-hourly output beyond day 4 is undercounted in the public archive)."""
    bias = defaultdict(lambda: [0.0, 0.0])
    for (sid, m, L, d), v in samples.items():
        o = obs_w[sid].get(d)
        if o is not None:
            bias[(m, L)][0] += v; bias[(m, L)][1] += o
    bad = set()
    for m in ARCHIVE_MODELS:
        for L in range(2, 8):
            a, b = bias.get((m, L - 1)), bias.get((m, L))
            if a and b and a[1] > 0 and b[1] > 0 and (b[0] / b[1]) < 0.6 * (a[0] / a[1]):
                bad.update((m, x) for x in range(L, 8))
                break
    return bad


def composite(samples, obs_w):
    """Mt. Bachelor stand-in: the average of the target stations, for forecasts and for measurements.
    comp[(m, L, d)] = forecast mm, cobs[d] = measured mm (only days every target station reported)."""
    targets = [s for s in STATIONS if STATIONS[s]["target"]]
    by = defaultdict(dict)
    for (sid, m, L, d), v in samples.items():
        if sid in targets:
            by[(m, L, d)][sid] = v
    cobs = {d: statistics.mean(obs_w[s][d] for s in targets)
            for d in set.intersection(*[set(obs_w[s]) for s in targets])}
    comp = {k: statistics.mean(v.values()) for k, v in by.items() if len(v) == len(targets) and k[2] in cobs}
    return comp, cobs


def fit(comp, cobs, flow, days_ok, bad, use_sector=True):
    agg = defaultdict(lambda: [0.0, 0.0])
    for (m, L, d), v in comp.items():
        if (m, L) in bad or d not in days_ok:
            continue
        b = bucket_of(L); o = cobs[d]
        agg[(m, b)][0] += o; agg[(m, b)][1] += v
        s = sector(flow.get(d))
        agg[(m, b, s)][0] += o; agg[(m, b, s)][1] += v
    f = {}
    for m in ARCHIVE_MODELS:
        f[m] = {}
        for b in LEAD_BUCKETS:
            O, M = agg[(m, b)]
            base = min(F_MAX, max(F_MIN, (O + K_MODEL) / (M + K_MODEL)))
            f[m][b] = {}
            for s in SECTORS:
                Os, Ms = agg[(m, b, s)]
                fs = (Os + K_SECTOR * base) / (Ms + K_SECTOR) if use_sector else base
                f[m][b][s] = round(min(F_MAX, max(F_MIN, fs)), 3)
    return f


def three_day_errors(comp, cobs, flow, days_ok, bad, factors=None, weights=None):
    """MAE (mm) of 3-day water totals for each model and the blend, per lead bucket.
    Forecasts at the leads inside a bucket are averaged per day."""
    per = defaultdict(lambda: defaultdict(list))   # (m, b) -> date -> [values]
    for (m, L, d), v in comp.items():
        if (m, L) in bad or d not in days_ok:
            continue
        b = bucket_of(L)
        if factors:
            v *= factors[m][b][sector(flow.get(d))]
        per[(m, b)][d].append(v)
    err = defaultdict(list)
    for b in LEAD_BUCKETS:
        ms = {m: {d: statistics.mean(x) for d, x in per[(m, b)].items()} for m in ARCHIVE_MODELS if per[(m, b)]}
        if not ms:
            continue
        days = sorted(set.union(*[set(x) for x in ms.values()]))
        for i in range(0, len(days) - 2, 3):
            win = days[i:i + 3]
            if (dt.date.fromisoformat(win[-1]) - dt.date.fromisoformat(win[0])).days != 2:
                continue
            o = sum(cobs[d] for d in win)
            vals = {m: sum(ms[m][d] for d in win) for m in ms if all(d in ms[m] for d in win)}
            for m, v in vals.items():
                err[(m, b)].append(abs(v - o))
            if vals:
                w = {m: (weights[b].get(m, 0) if weights else 1.0) for m in vals}
                tw = sum(w.values()) or 1
                err[("blend", b)].append(abs(sum(vals[m] * w[m] for m in vals) / tw - o))
    return {k: (statistics.mean(v), len(v)) for k, v in err.items() if v}


def blend_weights(err):
    w = {}
    for b in LEAD_BUCKETS:
        raw = {m: 1 / max(err[(m, b)][0], 0.5) ** 2 for m in ARCHIVE_MODELS if (m, b) in err}
        tot = sum(raw.values()) or 1
        w[b] = {m: max(0.08, v / tot) for m, v in raw.items()}
        t2 = sum(w[b].values())
        w[b] = {m: round(v / t2, 3) for m, v in w[b].items()}
    return w


ONES = {m: {b: {s: 1.0 for s in SECTORS} for b in LEAD_BUCKETS} for m in ARCHIVE_MODELS}
CONFIGS = {  # name -> (correct amounts?, by flow direction?, skill weights?)
    "raw": (False, False, False), "weights": (False, False, True),
    "bias": (True, False, False), "bias+weights": (True, False, True),
    "bias+flow": (True, True, False), "bias+flow+weights": (True, True, True),
}


def build(cfg, comp, cobs, flow, days, bad):
    corr, by_flow, use_w = CONFIGS[cfg]
    f = fit(comp, cobs, flow, days, bad, by_flow) if corr else ONES
    w = blend_weights(three_day_errors(comp, cobs, flow, days, bad, f)) if use_w else None
    return f, w


def snow_ratios(obs):
    bins = defaultdict(list)
    for sid, el in obs.items():
        W, S, T = el.get("WTEQ", {}), el.get("SNWD", {}), el.get("TAVG", {})
        for d in W:
            p = (dt.date.fromisoformat(d) - dt.timedelta(days=1)).isoformat()
            if None in (W.get(d), W.get(p), S.get(d), S.get(p), T.get(d)):
                continue
            dw, ds = W[d] - W[p], S[d] - S[p]
            if dw >= 0.3 and ds > 0:
                t = T[d]
                for mx, _ in DEFAULT_RATIO:
                    if t < mx:
                        bins[mx].append(ds / dw); break
    table, prev = [], None
    for mx, default in DEFAULT_RATIO:
        v = bins.get(mx, [])
        r = statistics.median(v) if len(v) >= 6 else default
        r = min(r, prev) if prev else r          # colder air can't give denser snow
        table.append({"max_f": mx, "ratio": round(r, 1), "n": len(v), "measured": len(v) >= 6})
        prev = r
    return table


def resort_depth_summary():
    path = DATA / "mtb_hourly.csv"
    if not path.exists():
        return {"days_logged": 0}
    depth = defaultdict(list)
    with path.open() as f:
        for r in csv.DictReader(f):
            if r["station"] == "West Village" and r["depth_in"] not in ("", "None", None):
                try:
                    depth[r["time_local"][:10]].append(float(r["depth_in"]))
                except ValueError:
                    pass
    days = sorted(depth)
    gains = []
    for a, b in zip(days, days[1:]):
        g = max(depth[b]) - max(depth[a])
        if g >= 1:
            gains.append({"date": b, "gain_in": round(g, 1)})
    return {"days_logged": len(days), "first": days[0] if days else None, "snow_days": len(gains),
            "recent_gains": gains[-10:], "used_for_fitting": False,
            "note": "Used for fitting once about 20 snow days with forecast snapshots are logged."}


def main():
    print("Loading SNOTEL…")
    obs = load_snotel()
    obs_w = {sid: daily_water(el) for sid, el in obs.items()}
    print("Loading forecast archive and snapshots…")
    samples, flow, src = load_samples()
    bad = suspect_leads(samples, obs_w)
    comp, cobs = composite(samples, obs_w)
    all_days = {d for (_, _, d) in comp}
    seasons = sorted({season_of(dt.date.fromisoformat(d)) for d in all_days})
    held = [s for s in seasons if sum(1 for d in all_days if season_of(dt.date.fromisoformat(d)) == s) >= 60]

    # Leave-one-season-out test of every configuration; keep the best per lead bucket.
    scores = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))   # bucket -> cfg -> [sum err, n]
    for hold in held:
        train = {d for d in all_days if season_of(dt.date.fromisoformat(d)) != hold}
        test = {d for d in all_days if season_of(dt.date.fromisoformat(d)) == hold}
        for cfg in CONFIGS:
            f, w = build(cfg, comp, cobs, flow, train, bad)
            e = three_day_errors(comp, cobs, flow, test, bad, f, w)
            for b in LEAD_BUCKETS:
                if ("blend", b) in e:
                    mae, n = e[("blend", b)]
                    scores[b][cfg][0] += mae * n; scores[b][cfg][1] += n
    chosen, val = {}, {}
    for b in LEAD_BUCKETS:
        res = {c: s / n for c, (s, n) in scores[b].items() if n}
        if not res:
            chosen[b] = "raw"; continue
        best = min(res, key=res.get)
        # Only accept a correction if it beats raw by at least 2% out of sample.
        chosen[b] = best if res[best] <= 0.98 * res["raw"] else "raw"
        val[b] = {"raw_mae_mm": round(res["raw"], 1), "chosen": chosen[b],
                  "chosen_mae_mm": round(res[chosen[b]], 1),
                  "improvement_pct": round(100 * (res["raw"] - res[chosen[b]]) / res["raw"], 1),
                  "all": {c: round(v, 2) for c, v in sorted(res.items(), key=lambda x: x[1])},
                  "windows": scores[b]["raw"][1]}

    factors = {m: {} for m in ARCHIVE_MODELS}
    weights = {}
    for b in LEAD_BUCKETS:
        f, w = build(chosen[b], comp, cobs, flow, all_days, bad)
        for m in ARCHIVE_MODELS:
            factors[m][b] = f[m][b]
        weights[b] = w[b] if w else {m: round(1 / len(ARCHIVE_MODELS), 3) for m in ARCHIVE_MODELS}
    ratios = snow_ratios(obs)

    app_factors, app_weights = {}, {}
    for app_id, am in APP_MODEL_MAP.items():
        app_factors[app_id] = factors[am] if am else {
            b: {s: round(statistics.mean(factors[m][b][s] for m in ARCHIVE_MODELS), 3) for s in SECTORS} for b in LEAD_BUCKETS}
    for b in LEAD_BUCKETS:
        app_weights[b] = {app_id: weights[b].get(am, 0) for app_id, am in APP_MODEL_MAP.items() if am}
        app_weights[b]["ncep_nbm_conus"] = round(statistics.mean(app_weights[b].values()), 3)

    # Plain-language summary of each model's typical error at the Bachelor stand-in (all data, raw).
    raw_bias = {}
    for m in ARCHIVE_MODELS:
        for b in LEAD_BUCKETS:
            O = M = 0.0
            for (mm, L, d), v in comp.items():
                if mm == m and bucket_of(L) == b and (m, L) not in bad:
                    O += cobs[d]; M += v
            if O:
                raw_bias.setdefault(m, {})[b] = round(M / O, 2)

    trained = max(all_days)
    cal = {
        "version": 2,
        "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "trained_through": trained,
        "seasons": seasons,
        "days": len(all_days),
        "sample_sources": src,
        "target": "Average of " + " and ".join(STATIONS[s]["name"] for s in STATIONS if STATIONS[s]["target"]) + " SNOTEL",
        "chosen": chosen,
        "precip_factors": app_factors,
        "blend_weights": app_weights,
        "raw_bias": {k: v for k, v in raw_bias.items()},
        "snow_ratio": ratios,
        "validation": val,
        "excluded_leads": sorted(f"{m} day {L}" for m, L in bad),
        "resort_depth": resort_depth_summary(),
        "caveats": [
            "Corrections are learned at two SNOTEL gauges (5,500-5,700 ft) that stand in for Bachelor, not on the mountain itself.",
            "SNOTEL gauges under-catch snow by roughly 10-30%, so corrected amounts may run slightly low.",
            "Snow ratios are settled depth gained per unit of water; a cleared storm board usually shows more.",
            "Days 8-16 reuse the day 5-7 setting; they have not been verified.",
            "NBM has no forecast archive yet, so it gets the average correction of the other models.",
            "A correction is used only if it beats the plain average on a season left out of its training.",
        ],
    }
    write_json(ROOT / "calibration.json", cal)

    hist = DATA / "score_history.csv"
    new = not hist.exists() or "chosen" not in hist.read_text().split("\n", 1)[0]
    with hist.open("w" if new else "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["generated", "trained_through", "days", "bucket", "chosen", "raw_mae_mm", "chosen_mae_mm", "improvement_pct"])
        for b, v in val.items():
            w.writerow([cal["generated"], trained, cal["days"], b, v["chosen"], v["raw_mae_mm"], v["chosen_mae_mm"], v["improvement_pct"]])

    print(f"Trained on {cal['days']} days ({', '.join(seasons)}), through {trained}. Samples: {src}")
    print("Excluded:", cal["excluded_leads"] or "none")
    for b, v in val.items():
        print(f"  days {b}: chose {v['chosen']}: 3-day error {v['raw_mae_mm']} -> {v['chosen_mae_mm']} mm ({v['improvement_pct']:+}%)  all={v['all']}")
    print("Raw bias at stand-in:", raw_bias)
    print("Snow ratios:", [(r["max_f"], r["ratio"], r["n"]) for r in ratios])


if __name__ == "__main__":
    main()
