"""Shared helpers for the Skyline Snow learning loop. Standard library only."""
import json, math, time, urllib.request, urllib.error, datetime as dt
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # snowline/
DATA = ROOT / "data"
ARCHIVE = DATA / "archive"        # Open-Meteo previous-runs cache (per station, per month)
SNAPSHOTS = DATA / "snapshots"    # our own forecast snapshots
for p in (DATA, ARCHIVE, SNAPSHOTS):
    p.mkdir(parents=True, exist_ok=True)

UA = "snowline-personal-forecast/1.0"
TZ = "America/Los_Angeles"

# Forecast points. "target" stations are the ones whose biases stand in for Mt. Bachelor:
# Three Creeks Meadow (east of the crest, rain shadow like Bachelor) and Irish Taylor (near the crest, SW).
STATIONS = {
    "815": dict(name="Three Creeks Meadow", lat=44.14425, lon=-121.64095, elev_m=1731, target=True),
    "545": dict(name="Irish Taylor",        lat=43.80368, lon=-121.94793, elev_m=1689, target=True),
    "619": dict(name="Mckenzie",            lat=44.21030, lon=-121.87292, elev_m=1454, target=False),
    "719": dict(name="Roaring River",       lat=43.90098, lon=-122.03063, elev_m=1511, target=False),
}
BACHELOR = dict(lat=43.9792, lon=-121.6886, elev_m={"base": 1920.3, "mid": 2369.9, "top": 2763.0})

ARCHIVE_MODELS = ["gfs_global", "ecmwf_ifs025", "gem_global", "icon_global"]
# The app uses "seamless" variants; corrections learned on the global runs are applied to them.
APP_MODEL_MAP = {"gfs_seamless": "gfs_global", "ecmwf_ifs025": "ecmwf_ifs025",
                 "gem_seamless": "gem_global", "icon_seamless": "icon_global", "ncep_nbm_conus": None}
LEAD_BUCKETS = {"1-2": (1, 2), "3-4": (3, 4), "5-7": (5, 7)}
SECTORS = ["S-SW", "W", "NW-N", "other"]


def sector(deg):
    if deg is None:
        return "other"
    d = deg % 360
    if 157.5 <= d < 247.5:
        return "S-SW"
    if 247.5 <= d < 292.5:
        return "W"
    if d >= 292.5 or d < 22.5:
        return "NW-N"
    return "other"


def get_json(url, retries=3, timeout=120):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
            time.sleep(4 * (i + 1))
    raise RuntimeError(f"fetch failed: {url[:120]}… ({last})")


def vec_mean_dir(dirs, weights=None):
    if not dirs:
        return None
    weights = weights or [1.0] * len(dirs)
    u = sum(-w * math.sin(math.radians(d)) for d, w in zip(dirs, weights))
    v = sum(-w * math.cos(math.radians(d)) for d, w in zip(dirs, weights))
    if abs(u) < 1e-9 and abs(v) < 1e-9:
        return None
    return (math.degrees(math.atan2(-u, -v)) + 360) % 360


def today():
    return dt.date.today()


def write_json(path, obj):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":")))
    tmp.replace(path)
