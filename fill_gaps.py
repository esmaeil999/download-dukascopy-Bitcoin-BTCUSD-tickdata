#!/usr/bin/env python3
"""Fill missing hours in a converted tick CSV from Dukascopy's classic bi5 feed.

The jetta JSON API (used by dukascopy-node) sometimes returns an empty
dataset for hours that do have data. The older bi5 feed
(datafeed.dukascopy.com) is a separate system that may still serve them.

This script:
  1. finds fully-empty hour buckets between the first and last tick
  2. downloads those hours from the bi5 feed (LZMA, 20-byte records:
     >IIIff = ms offset, ask, bid, askVolume, bidVolume)
  3. sanity-checks decoded prices against neighbouring CSV prices
     (protects against a wrong --point multiplier)
  4. merges the recovered ticks, keeping the file time-sorted

Best-effort: hours that cannot be recovered are reported and left empty;
gapcheck.py afterwards decides whether the final result is acceptable.

Downloads use curl with a browser User-Agent: the bi5 feed rejects or
mis-serves default scripting clients.

Usage:
    python fill_gaps.py ticks.csv --instrument BTCUSD [--point 0.1]
    python fill_gaps.py ticks.csv --instrument BTCUSD --bi5-dir ./cache  # offline/testing
"""

import argparse
import csv
import lzma
import os
import statistics
import struct
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

BI5_ROOT = "https://datafeed.dukascopy.com/datafeed"
RECORD = struct.Struct(">IIIff")  # ms offset, ask, bid, askVolume, bidVolume
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Fill missing hours in a tick CSV from the classic bi5 feed."
    )
    parser.add_argument("csv_path", help="Converted CSV (GmtTime,Bid,Ask,BidVolume,AskVolume)")
    parser.add_argument("--instrument", required=True, help="e.g. BTCUSD")
    parser.add_argument(
        "--point",
        type=float,
        default=0.1,
        help="Price multiplier for bi5 integer prices (BTCUSD: 0.1)",
    )
    parser.add_argument(
        "--bi5-dir",
        default="",
        help="Read .bi5 files from this folder instead of downloading",
    )
    parser.add_argument(
        "--max-deviation",
        type=float,
        default=0.2,
        help="Max allowed deviation vs neighbouring prices (default: 0.2 = 20%%)",
    )
    parser.add_argument("--retries", type=int, default=5)
    return parser.parse_args()


def hour_key(dt):
    return f"{dt:%Y-%m-%d %H}"


def fmt_num(v):
    s = f"{v:.10f}".rstrip("0").rstrip(".")
    return s if s else "0"


def fmt_ts(ms):
    dt = datetime.fromtimestamp(ms // 1000, tz=timezone.utc)
    return f"{dt:%Y-%m-%d %H:%M:%S}.{ms % 1000:03d}"


def scan(csv_path):
    """Return (per-hour [first_bid, last_bid], min_key, max_key, row_count)."""
    present = {}
    min_key = None
    max_key = None
    rows = 0
    with open(csv_path, newline="") as f:
        reader = csv.reader(f)
        next(reader, None)  # header
        for row in reader:
            if not row or len(row[0]) < 13:
                continue
            key = row[0][:13]
            try:
                bid = float(row[1])
            except (IndexError, ValueError):
                bid = None
            if key not in present:
                present[key] = [bid, bid]
            else:
                present[key][1] = bid
            if min_key is None or key < min_key:
                min_key = key
            if max_key is None or key > max_key:
                max_key = key
            rows += 1
    return present, min_key, max_key, rows


def find_empty_hours(present, min_key, max_key):
    start = datetime.strptime(min_key, "%Y-%m-%d %H")
    end = datetime.strptime(max_key, "%Y-%m-%d %H")
    out = []
    h = start
    while h <= end:
        if hour_key(h) not in present:
            out.append(h)
        h += timedelta(hours=1)
    return out


def download(url, retries, pause=3):
    """Download with curl + browser UA; return bytes or None."""
    for attempt in range(retries):
        fd, tmp = tempfile.mkstemp()
        os.close(fd)
        try:
            proc = subprocess.run(
                ["curl", "-sS", "-L", "--max-time", "60", "-A", UA,
                 "-o", tmp, "-w", "%{http_code}", url],
                capture_output=True,
                text=True,
                timeout=120,
            )
            code = proc.stdout.strip()
            status = int(code) if code.isdigit() else 0
            if status == 200:
                with open(tmp, "rb") as f:
                    data = f.read()
                return data if data else None
            if status == 404:
                return None
        except Exception:
            pass
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        if attempt < retries - 1:
            time.sleep(pause)
    return None


def fetch_bi5(inst, dt, args):
    rel = f"{inst}/{dt:%Y}/{dt.month - 1:02d}/{dt:%d}/{dt:%H}h_ticks.bi5"
    if args.bi5_dir:
        try:
            with open(os.path.join(args.bi5_dir, rel), "rb") as f:
                return f.read()
        except OSError:
            return None
    return download(f"{BI5_ROOT}/{rel}", args.retries)


def decode_bi5(blob):
    """bi5 files are LZMA 'alone' streams; fall back to raw LZMA1 just in case."""
    try:
        raw = lzma.decompress(blob, format=lzma.FORMAT_ALONE)
        if len(raw) % RECORD.size == 0:
            return raw
    except Exception:
        pass
    try:
        raw = lzma.decompress(
            blob,
            format=lzma.FORMAT_RAW,
            filters=[{"id": lzma.FILTER_LZMA1, "preset": 9}],
        )
        if len(raw) % RECORD.size == 0:
            return raw
    except Exception:
        pass
    return None


def parse_ticks(raw, hour_dt, point):
    base_ms = int(hour_dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
    ticks = []
    for off in range(0, len(raw), RECORD.size):
        ms, ask_i, bid_i, avol, bvol = RECORD.unpack_from(raw, off)
        ticks.append((base_ms + ms, bid_i * point, ask_i * point, bvol, avol))
    return ticks


def neighbor_prices(present, gap_dt, max_look=48):
    before = None
    after = None
    for i in range(1, max_look + 1):
        k = hour_key(gap_dt - timedelta(hours=i))
        if k in present:
            before = present[k][1]
            break
    for i in range(1, max_look + 1):
        k = hour_key(gap_dt + timedelta(hours=i))
        if k in present:
            after = present[k][0]
            break
    return before, after


def prices_sane(ticks, before, after, dev):
    refs = [p for p in (before, after) if p]
    if not refs:
        return True
    mid = statistics.median(t[1] for t in ticks[:200])
    lo = min(refs) * (1 - dev)
    hi = max(refs) * (1 + dev)
    return lo <= mid <= hi


def merge(csv_path, fills):
    keys = sorted(fills)
    tmp = csv_path + ".merged"
    with open(csv_path, newline="") as fin, open(tmp, "w", newline="") as fout:
        fout.write(fin.readline())  # header
        ki = 0
        for line in fin:
            key = line[:13]
            while ki < len(keys) and keys[ki] < key:
                fout.write("\n".join(fills[keys[ki]]) + "\n")
                ki += 1
            fout.write(line)
        while ki < len(keys):
            fout.write("\n".join(fills[keys[ki]]) + "\n")
            ki += 1
    os.replace(tmp, csv_path)


def main():
    args = parse_args()
    inst = args.instrument.strip().replace("/", "").upper()

    present, min_key, max_key, rows = scan(args.csv_path)
    if rows == 0:
        print("ERROR: CSV contains no tick rows.", file=sys.stderr)
        sys.exit(1)

    empty = find_empty_hours(present, min_key, max_key)
    if not empty:
        print("OK: no missing hours; nothing to fill.")
        return

    print(f"found {len(empty)} empty hour(s); trying classic bi5 feed...")

    fills = {}
    still = []
    for dt in empty:
        key = hour_key(dt)
        blob = fetch_bi5(inst, dt, args)
        if not blob:
            print(f"  {key}: bi5 not available")
            still.append(key)
            continue
        raw = decode_bi5(blob)
        if raw is None:
            print(f"  {key}: bi5 download could not be decoded")
            still.append(key)
            continue
        ticks = parse_ticks(raw, dt, args.point)
        before, after = neighbor_prices(present, dt)
        if not prices_sane(ticks, before, after, args.max_deviation):
            print(
                f"  {key}: sanity check FAILED (median price far from "
                f"neighbours; wrong --point?) - skipped"
            )
            still.append(key)
            continue
        fills[key] = [
            f"{fmt_ts(ms)},{fmt_num(bid)},{fmt_num(ask)},{fmt_num(bv)},{fmt_num(av)}"
            for ms, bid, ask, bv, av in ticks
        ]
        print(f"  {key}: recovered {len(ticks)} ticks from bi5")
        if not args.bi5_dir:
            time.sleep(0.3)  # be polite to the server

    if fills:
        merge(args.csv_path, fills)

    total = sum(len(v) for v in fills.values())
    print(
        f"DONE: recovered {total} ticks into {len(fills)} hour(s); "
        f"still empty: {len(still)}"
    )
    if still:
        print("still empty: " + ", ".join(still))


if __name__ == "__main__":
    main()
