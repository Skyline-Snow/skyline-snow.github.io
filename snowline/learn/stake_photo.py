"""Saves one Mt. Bachelor snow-stake webcam photo each morning, keeping only the last 7 days.

Photos live on their own branch, `stake-photos`, which is rewritten as a single commit every time
so a deleted photo is really gone (it never builds up in the main project's history). A photo older
than 7 days is only deleted once that day's official snow report is in
data/observed/resort_snow_report.csv, so the numbers are always kept even when the picture is not.

Runs from the GitHub workflow after collect.py. The photo is taken on the first run at or after
4 am Pacific, before the resort's 7 am report, so it shows the snow the report is about to measure.
"""
import csv, datetime as dt, subprocess, sys, urllib.request
from lib import DATA, TZ

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

CAM_URL = "https://api.mtbachelor.com/api/v1/cams/mtn/14"   # labelled "Snow Stake" on mtbachelor.com/webcams
BRANCH = "stake-photos"
KEEP_DAYS = 7
EARLIEST_HOUR = 4
README = (b"# Mt. Bachelor snow-stake photos\n\nOne photo per morning from the resort's snow-stake webcam, "
          b"taken by the Skyline Snow workflow before the 7 am snow report.\nOnly the last 7 days are kept; "
          b"older photos are deleted once that day's report is logged in\n"
          b"`snowline/data/observed/resort_snow_report.csv` on the main branch.\n")


def git(*args, data=None):
    return subprocess.run(["git", *args], input=data, capture_output=True, check=True).stdout


def existing_photos():
    """{filename: blob hash} currently on the photo branch (empty if the branch doesn't exist yet)."""
    try:
        git("fetch", "--quiet", "origin", f"+refs/heads/{BRANCH}:refs/remotes/origin/{BRANCH}")
    except subprocess.CalledProcessError:
        return {}
    out = {}
    for line in git("ls-tree", f"refs/remotes/origin/{BRANCH}").decode().splitlines():
        meta, name = line.split("\t", 1)
        if name.endswith(".jpg"):
            out[name] = meta.split()[2]
    return out


def reported_dates():
    path = DATA / "observed" / "resort_snow_report.csv"
    if not path.exists():
        return set()
    with path.open() as f:
        return {r["report_date"] for r in csv.DictReader(f)}


def main(force=False):
    now = dt.datetime.now(ZoneInfo(TZ)) if ZoneInfo else dt.datetime.now()
    today = now.date().isoformat()
    photos = existing_photos()
    have_today = any(n.startswith(today) for n in photos)

    added = None
    if not have_today and (now.hour >= EARLIEST_HOUR or force):
        req = urllib.request.Request(CAM_URL, headers={"User-Agent": "skyline-snow/1.0"})
        img = urllib.request.urlopen(req, timeout=30).read()
        if len(img) < 10_000 or not img.startswith(b"\xff\xd8"):
            raise RuntimeError(f"camera returned {len(img)} bytes, not a photo")
        added = f"{today}_{now:%H%M}.jpg"
        photos[added] = git("hash-object", "-w", "--stdin", data=img).decode().strip()

    cutoff = (now.date() - dt.timedelta(days=KEEP_DAYS - 1)).isoformat()
    reported = reported_dates()
    removed = [n for n in photos if n[:10] < cutoff and n[:10] in reported]
    for n in removed:
        del photos[n]

    if not added and not removed:
        print("snow-stake photo: nothing to do", "(already saved today)" if have_today else f"(before {EARLIEST_HOUR} am)")
        return
    readme = git("hash-object", "-w", "--stdin", data=README).decode().strip()
    tree_lines = [f"100644 blob {readme}\tREADME.md"] + [f"100644 blob {h}\t{n}" for n, h in sorted(photos.items())]
    tree = git("mktree", data=("\n".join(tree_lines) + "\n").encode()).decode().strip()
    commit = git("-c", "user.name=github-actions[bot]",
                 "-c", "user.email=41898283+github-actions[bot]@users.noreply.github.com",
                 "commit-tree", tree, "-m", f"Snow-stake photos, {len(photos)} kept").decode().strip()
    git("push", "--quiet", "--force", "origin", f"{commit}:refs/heads/{BRANCH}")
    print("snow-stake photo saved:", added or "none", "| deleted:", ", ".join(removed) or "none",
          "| kept:", len(photos))


if __name__ == "__main__":
    try:
        main(force="--force" in sys.argv)
    except Exception as e:
        print("snow-stake photo failed:", e, file=sys.stderr)
        sys.exit(1)
