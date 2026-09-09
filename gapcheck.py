#!/usr/bin/env python3
"""Check a converted tick CSV for missing hours/days.

Dukascopy serves tick data as one file per hour. A failed hour download
silently disappears from the output. This script scans the converted
JForex-style CSV (GmtTime,Bid,Ask,BidVolume,AskVolume) and:

- reports every run of consecutive empty hours
- fails (exit 1) when a run inside the data range, or at the end of the
  expected range, reaches --max-consecutive-empty-hours (default 2)
- a run at the START of the expected range is only a WARNING, because the
  instrument may simply have no data that far back
- the last --trailing-grace-hours of the expected range are tolerated
  (the feed may lag for very recent dates)

Usage:
    python gapcheck.py ticks.csv --from-date 2025-01-01 --to-date 2026-01-01
"""

import argparse
import csv
import sys
from datetime import datetime, timedelta


def parse_args():
    parser = argparse.ArgumentParser(
        description="Check a tick CSV for missing hours/days."
    )
    parser.add_argument("csv_path", help="Converted CSV (GmtTime,Bid,Ask,...)")
    parser.add_argument(
        "--from-date",
        default="",
        help="Expected range start YYYY-MM-DD (GMT); leading gaps are warnings only",
    )
    parser.add_argument(
        "--to-date",
        default="",
        help="Expected range end YYYY-MM-DD (GMT); trailing gaps beyond the grace window fail",
    )
    parser.add_argument(
        "--max-consecutive-empty-hours",
        type=int,
        default=2,
        help="Fail when this many consecutive hours contain zero ticks (default: 2)",
    )
    parser.add_argument(
        "--trailing-grace-hours",
        type=int,
        default=3,
        help="Empty hours tolerated at the very end of the range (default: 3)",
    )
    return parser.parse_args()


def hour_key(dt):
    return f"{dt:%Y-%m-%d %H}"


def main():
    args = parse_args()

    present = set()  # hour buckets as "YYYY-MM-DD HH" strings
    min_key = None
    max_key = None
    rows = 0

    with open(args.csv_path, newline="") as f:
        reader = csv.reader(f)
        next(reader, None)  # header
        for row in reader:
            if not row or len(row[0]) < 13:
                continue
            key = row[0][:13]  # "YYYY-MM-DD HH"
            present.add(key)
            if min_key is None or key < min_key:
                min_key = key
            if max_key is None or key > max_key:
                max_key = key
            rows += 1

    if rows == 0:
        print("ERROR: CSV contains no tick rows.", file=sys.stderr)
        sys.exit(1)

    data_start = datetime.strptime(min_key, "%Y-%m-%d %H")
    data_end = datetime.strptime(max_key, "%Y-%m-%d %H")

    # (start, end, threshold_applies) tuples
    runs = []

    # ---- leading gap (expected start .. first tick): warning only
    if args.from_date:
        exp_start = datetime.strptime(args.from_date, "%Y-%m-%d")
        if exp_start < data_start:
            runs.append((exp_start, data_start - timedelta(hours=1), False))

    # ---- gaps inside the data range: threshold applies
    empty = []
    h = data_start
    while h <= data_end:
        if hour_key(h) not in present:
            empty.append(h)
        h += timedelta(hours=1)

    run_start = None
    prev = None
    for h in empty:
        if prev is not None and (h - prev) == timedelta(hours=1):
            pass  # extends current run
        else:
            if run_start is not None:
                runs.append((run_start, prev, True))
            run_start = h
        prev = h
    if run_start is not None:
        runs.append((run_start, prev, True))

    # ---- trailing gap (last tick .. expected end minus grace): threshold applies
    if args.to_date:
        exp_end = datetime.strptime(args.to_date, "%Y-%m-%d") - timedelta(hours=1)
        grace_end = exp_end - timedelta(hours=args.trailing_grace_hours)
        if grace_end > data_end:
            runs.append((data_end + timedelta(hours=1), grace_end, True))

    total_hours = int((data_end - data_start).total_seconds() // 3600) + 1
    empty_in_range = sum(
        int((e - s).total_seconds() // 3600) + 1
        for s, e, threshold_applies in runs
        if threshold_applies and s <= data_end and e >= data_start
    )
    print(
        f"rows={rows} data_range={min_key}:00..{max_key}:59 "
        f"hours_in_range={total_hours} empty_hours_in_range={empty_in_range}"
    )

    failed = False
    for run_start, run_end, threshold_applies in runs:
        n = int((run_end - run_start).total_seconds() // 3600) + 1
        bad = threshold_applies and n >= args.max_consecutive_empty_hours
        failed = failed or bad
        tag = "ERROR" if bad else "WARNING"
        print(
            f"{tag}: {n} consecutive empty hour(s): "
            f"{run_start:%Y-%m-%d %H:%M} .. {run_end:%Y-%m-%d %H:%M} GMT"
        )

    if failed:
        print(
            f"ERROR: found gap(s) of >= {args.max_consecutive_empty_hours} "
            "consecutive empty hours.",
            file=sys.stderr,
        )
        print(
            "Re-run this workflow job (Actions -> Re-run failed jobs) "
            "to retry the missing hours.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("OK: no significant gaps found.")


if __name__ == "__main__":
    main()
