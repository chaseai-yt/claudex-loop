#!/usr/bin/env python3
"""Read the remaining Codex quota from local session rollouts — no API call.

Codex CLI writes a rate_limits snapshot into its session rollout files
(~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl) after every turn: `primary` is
the 5-hour window, `secondary` the weekly window, each with `used_percent` and
`resets_at` (epoch seconds), plus `credits.balance`. This tool finds the most
recent snapshot and prints it — so the loop can tell the user how much quota
is left and WHEN the reviewer comes back, instead of dead-ending.

The snapshot is as fresh as the last codex run. If that is too stale, a tiny
ping refreshes it:  codex exec -s read-only "OK" < /dev/null

Used by the quota-exhaustion protocol in FALLBACK.md: check before round 1
(exit 1 = don't start the loop, let the user decide), and consult after a
mid-loop failure to distinguish real exhaustion from a transient stumble.

Usage:  python scripts/codex_usage.py [--json] [--threshold 95]
Exit:   0 = quota available, 1 = a window is at/over the threshold,
        2 = no snapshot found (codex never ran on this machine?)
"""
import argparse
import datetime
import glob
import io
import json
import os
import sys


def find_latest_snapshot():
    """Most recent rate_limits entry across rollout files (newest files first)."""
    pattern = os.path.expanduser("~/.codex/sessions/*/*/*/rollout-*.jsonl")
    files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    for path in files[:10]:
        snap = None
        try:
            with io.open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if '"rate_limits"' not in line:
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    # The snapshot sits at different depths depending on event type.
                    for holder in (obj, obj.get("payload", {}),
                                   obj.get("payload", {}).get("info", {})):
                        if isinstance(holder, dict) and isinstance(holder.get("rate_limits"), dict):
                            snap = holder["rate_limits"]
        except OSError:
            continue
        if snap:
            return snap, path
    return None, None


def fmt_window(win, label):
    if not isinstance(win, dict) or win.get("used_percent") is None:
        return f"{label}: no data"
    used = win.get("used_percent")
    resets = win.get("resets_at")
    minutes = win.get("window_minutes")
    reset_txt = ""
    if resets:
        dt = datetime.datetime.fromtimestamp(resets)
        delta = dt - datetime.datetime.now()
        hours = max(0, int(delta.total_seconds() // 3600))
        reset_txt = f", resets {dt:%Y-%m-%d %H:%M} (~{hours} h)"
    return f"{label} ({minutes} min window): {used:.0f} % used{reset_txt}"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="print the raw snapshot as JSON")
    ap.add_argument("--threshold", type=float, default=95.0,
                    help="exit 1 if any window is at/over this percentage (default 95)")
    args = ap.parse_args()

    snap, path = find_latest_snapshot()
    if snap is None:
        print("No rate_limits snapshot found — has codex ever run on this machine?")
        return 2

    if args.json:
        print(json.dumps(snap, indent=2))
    else:
        print(f"Source: {path}")
        print(fmt_window(snap.get("primary"), "5-hour"))
        print(fmt_window(snap.get("secondary"), "weekly"))
        credits = snap.get("credits") or {}
        if credits:
            print(f"Credits: balance={credits.get('balance')}, unlimited={credits.get('unlimited')}")

    worst = max(
        ((w.get("used_percent") or 0.0)
         for w in (snap.get("primary") or {}, snap.get("secondary") or {})
         if isinstance(w, dict)),
        default=0.0,
    )
    if worst >= args.threshold:
        print(f"ERROR: Quota threshold reached ({worst:.0f} % >= {args.threshold:.0f} %) — "
              "don't start a review round; let the user decide (wait / fallback / skip).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
