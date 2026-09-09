#!/usr/bin/env python3
"""Fill missing hours in a converted tick CSV from Dukascopy's classic bi5 feed.

The jetta JSON API (used by dukascopy-node) sometimes returns an empty
dataset for hours that do have data. The older bi5 feed
(datafeed.dukascopy.com) is a separate system that may still serve them.

This script:
  1. finds fully-empty hour buckets between the first and last tick
  2. checks those hours against the bi5 feed CONCURRENTLY (LZMA,
     20-byte records: >IIIff = ms offset, ask, bid, askVolume, bidVolume)
  3. sanity-checks decoded prices against neighbouring CSV prices
     (protects against a wrong --point multiplier)
  4. merges the recovered ticks, keeping the file time-sorted
  5. writes a sidecar file (<csv>.nodata) listing hours that are
     confirmed absent on BOTH feeds (e.g. nights/weekends for index
     instruments). gapcheck.py reads that sidecar and does not treat
     those hours as gaps. Hours already listed there (written by
     download.py) are trusted and not re-checked.

Best-effort: hours whose download keeps failing are reported and left
empty (and NOT written to the sidecar, so gapcheck still flags them).
Hours that fail the first (concurrent) pass get one slow, sequential
second pass with longer pauses before being given up on.

Downloads use curl with a browser User-Agent: the bi5 feed rejects or
mis-serves default scripting clients.

Usage:
    python fill_gaps.py ticks.csv --instrument BTCUSD
    python fill_gaps.py ticks.csv --instrument USA500IDXUSD --workers 12
    python fill_gaps.py ticks.csv --instrument BTCUSD --bi5-dir ./cache  # offline/testing
"""

import argparse
import csv
import lzma
import os
import re
import statistics
import struct
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

BI5_ROOT = os.environ.get("BI5_ROOT", "https://datafeed.dukascopy.com/datafeed")
RECORD = struct.Struct(">IIIff")  # ms offset, ask, bid, askVolume, bidVolume

# Price point sizes for decoding bi5 integer prices (--point overrides).
# bi5 stores prices as int = price / point; the point equals the smallest
# displayed quote digit on Dukascopy (e.g. USA500.IDX quotes 7666.789 -> 0.001).
POINTS = {
    "BTCUSD": 0.1,
    "XAUUSD": 0.001,
    "EURUSD": 0.00001,
    "GBPUSD": 0.00001,
    "USDJPY": 0.001,
    "DOLLARIDXUSD": 0.001,
    "USA30IDXUSD": 0.001,
    "USA500IDXUSD": 0.001,
    "USATECHIDXUSD": 0.001,
}

IDX_CMD_RE = re.compile(r"^([A-Z0-9]+?)(IDX|CMD)([A-Z]{3})$")
STOCK_RE = re.compile(r"^([A-Z0-9]+?)([A-Z]{2})([A-Z]{3})$")


def default_point(inst):
    """bi5 price point: explicit map, then rules (index/commodity CFDs
    quote 3 decimals, JPY pairs 3, other 6-letter FX pairs 5, stock
    CFDs 2). None when unknown."""
    if inst in POINTS:
        return POINTS[inst]
    if len(inst) > 6 and IDX_CMD_RE.match(inst):
        return 0.001
    if len(inst) == 6:
        return 0.001 if inst.endswith("JPY") else 0.00001
    if len(inst) > 6 and STOCK_RE.match(inst):
        return 0.01
    return None


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
        default=None,
        help="Price multiplier for bi5 integer prices (default: built-in per instrument, e.g. BTCUSD=0.1, EURUSD=0.00001, indices=0.001)",
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
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument(
        "--retry-pause", type=float, default=3.0, help="Seconds between retries"
    )
    args = parser.parse_args()
    if args.point is None:
        args.point = default_point(args.instrument.strip().replace("/", "").upper())
    if args.point is None:
        print(
            f"WARNING: no known point size for {args.instrument}; "
            "falling back to 0.1 (the sanity check may reject fills)"
        )
        args.point = 0.1
    return args


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
    """Return (bytes|None, status): status is 'ok' | 'nodata' | 'error'.

    404 (and 200 with empty body) is definitive: the feed has no such hour.
    Other statuses are retried; exhausting them yields 'error' (unknown).
    """
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
                return (data, "ok") if data else (None, "nodata")
            if status == 404:
                return None, "nodata"
        except Exception:
            pass
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        if attempt < retries - 1:
            time.sleep(pause)
    return None, "error"


def fetch_bi5(inst, dt, args, retries=None, pause=None):
    """Return (bytes|None, status): 'ok' | 'nodata' | 'error'."""
    rel = f"{inst}/{dt:%Y}/{dt.month - 1:02d}/{dt:%d}/{dt:%H}h_ticks.bi5"
    if args.bi5_dir:
        try:
            with open(os.path.join(args.bi5_dir, rel), "rb") as f:
                data = f.read()
            return (data, "ok") if data else (None, "nodata")
        except OSError:
            return None, "nodata"
    return download(
        f"{BI5_ROOT}/{rel}",
        retries if retries is not None else args.retries,
        pause if pause is not None else args.retry_pause,
    )


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

    # hours the downloader already confirmed as absent on both feeds are
    # trusted and not re-checked
    known = set()
    sidecar_path = args.csv_path + ".nodata"
    if os.path.exists(sidecar_path):
        with open(sidecar_path) as f:
            known = {line.strip() for line in f if line.strip()}
    if known:
        before = len(empty)
        empty = [dt for dt in empty if hour_key(dt) not in known]
        print(
            f"skipping {before - len(empty)} hour(s) already confirmed "
            f"no-data by the downloader (see {os.path.basename(sidecar_path)})"
        )
    if not empty:
        print("OK: all missing hours are confirmed no-data; nothing to fill.")
        return

    print(
        f"found {len(empty)} empty hour(s); checking the classic bi5 feed "
        f"with {args.workers} workers (point={args.point})..."
    )

    def work(dt, retries=None, pause=None):
        blob, status = fetch_bi5(inst, dt, args, retries, pause)
        if status != "ok":
            return (dt, status, None)
        raw = decode_bi5(blob)
        if raw is None:
            return (dt, "error", None)  # downloaded but undecodable: unknown
        ticks = parse_ticks(raw, dt, args.point)
        if not ticks:
            return (dt, "nodata", None)
        before, after = neighbor_prices(present, dt)
        if not prices_sane(ticks, before, after, args.max_deviation):
            return (dt, "insane", None)
        return (dt, "ok", ticks)

    fills = {}
    nodata = []  # confirmed absent on BOTH feeds -> sidecar for gapcheck
    errors = []  # fetch/decode failure -> retried in a slow second pass
    insane = []  # decoded but rejected by the neighbour sanity check

    def classify(dt, status, ticks):
        key = hour_key(dt)
        if status == "ok":
            fills[key] = [
                f"{fmt_ts(ms)},{fmt_num(bid)},{fmt_num(ask)},"
                f"{fmt_num(bv)},{fmt_num(av)}"
                for ms, bid, ask, bv, av in ticks
            ]
            print(f"  {key}: recovered {len(ticks)} ticks from bi5")
        elif status == "nodata":
            nodata.append(key)
        elif status == "insane":
            print(
                f"  {key}: sanity check FAILED (median price far from "
                f"neighbours; wrong point?) - skipped"
            )
            insane.append(key)
        else:
            errors.append(dt)

    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for dt, status, ticks in pool.map(work, empty):
            done += 1
            classify(dt, status, ticks)
            if done % 250 == 0:
                print(
                    f"  ... checked {done}/{len(empty)} hours "
                    f"(recovered {len(fills)}, no-data {len(nodata)}, failed {len(errors)})"
                )

    # slow second pass for hours whose download failed (transient 5xx etc.)
    if errors:
        print(
            f"slow pass: retrying {len(errors)} hour(s) that failed to "
            "download (3 attempts, longer pauses)..."
        )
        remaining = []
        for dt in errors:
            dt2, status, ticks = work(dt, retries=3, pause=args.retry_pause * 4)
            if status == "error":
                print(f"  {hour_key(dt2)}: bi5 unavailable after slow pass too")
                remaining.append(dt2)
            else:
                classify(dt2, status, ticks)
        errors = remaining

    still = [hour_key(dt) for dt in errors] + insane

    if fills:
        merge(args.csv_path, fills)

    all_nodata = sorted(set(nodata) | known)
    if all_nodata:
        with open(args.csv_path + ".nodata", "w") as f:
            for k in all_nodata:
                f.write(k + "\n")

    total = sum(len(v) for v in fills.values())
    print(
        f"DONE: recovered {total} ticks into {len(fills)} hour(s); "
        f"confirmed no-data on both feeds: {len(all_nodata)} "
        f"(newly checked: {len(nodata)}, pre-confirmed: {len(known)})"
        + (f" (listed in {os.path.basename(args.csv_path)}.nodata)" if all_nodata else "")
        + f"; still unknown: {len(still)}"
    )
    if still:
        shown = ", ".join(still[:50])
        print("still unknown: " + shown + (" ..." if len(still) > 50 else ""))


if __name__ == "__main__":
    main()
