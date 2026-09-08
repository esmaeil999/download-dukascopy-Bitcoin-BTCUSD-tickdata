#!/usr/bin/env python3
"""Download Dukascopy tick data straight from the jetta JSON API.

Self-contained replacement for the dukascopy-node CLI path, built for
transparent logs: every 6-hour chunk prints one progress line and every
empty/failed hour is reported explicitly, for example:

    chunk: 2018-07-12 00:00:00.000 -> 2018-07-12 06:00:00.000 | ticks: 17784 | total: 12097760 | 52%
    empty: 2018-07-12 06:00:00.000 | server returned no ticks (6 attempts)

The jetta payload is delta-encoded per hour:
    {timestamp, multiplier, ask, bid, times[], asks[], bids[], askVolumes[], bidVolumes[]}
where times/asks/bids are successive deltas accumulated from the base
values. This mirrors dukascopy-node's data-normaliser.

Output CSV (dukascopy-node compatible):
    timestamp,askPrice,bidPrice,askVolume,bidVolume

Usage:
    python download.py --instrument BTCUSD --from-date 2018-07-01 --to-date 2019-01-01 -o download/out.csv

Testing override: set JETTA_ROOT=file:///path/to/dir to read local JSON files.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

JETTA_ROOT = os.environ.get("JETTA_ROOT", "https://jetta.dukascopy.com/v1/ticks")
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
CHUNK_HOURS = 6


def parse_args():
    parser = argparse.ArgumentParser(
        description="Download Dukascopy tick data with per-chunk progress logs."
    )
    parser.add_argument("--instrument", required=True, help="e.g. BTCUSD")
    parser.add_argument("--code", default="", help="jetta instrument code (default: derived, BTCUSD -> BTC-USD)")
    parser.add_argument("--from-date", required=True, help="YYYY-MM-DD (GMT, inclusive)")
    parser.add_argument("--to-date", required=True, help="YYYY-MM-DD (GMT, exclusive)")
    parser.add_argument("-o", "--output", required=True, help="Output CSV path")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--retries", type=int, default=5, help="Retries per hour (default: 5)")
    parser.add_argument("--retry-pause", type=float, default=2.0, help="Seconds between retries")
    parser.add_argument("--pause", type=float, default=0.2, help="Per-request politeness pause per worker")
    parser.add_argument(
        "--no-retry-on-empty",
        action="store_true",
        help="Accept empty datasets without retrying",
    )
    return parser.parse_args()


def derive_code(instrument):
    base = instrument.strip().replace("/", "").upper()
    if len(base) == 6:
        return base[:3] + "-" + base[3:]  # BTCUSD -> BTC-USD
    return base


def price_scale(multiplier):
    """Same rule as dukascopy-node's getPriceScale."""
    s = repr(multiplier).lower()
    if "e" in s:
        coeff, exp = s.split("e")
        exp = int(exp)
    else:
        coeff, exp = s, 0
    dec = len(coeff.split(".")[1]) if "." in coeff else 0
    return max(0, dec - exp)


def fmt_num(v):
    s = f"{v:.10f}".rstrip("0").rstrip(".")
    return s if s else "0"


def fmt_price(units, multiplier, scale):
    s = f"{units * multiplier:.{scale}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s if s else "0"


def fetch(url, timeout=60):
    """Return (status, body). Supports file:// for local testing."""
    if url.startswith("file://"):
        try:
            with open(url[len("file://"):], "rb") as f:
                return 200, f.read()
        except OSError:
            return 404, b""
    fd, tmp = tempfile.mkstemp()
    os.close(fd)
    try:
        proc = subprocess.run(
            ["curl", "-sS", "-L", "--max-time", str(timeout), "-A", UA,
             "-o", tmp, "-w", "%{http_code}", url],
            capture_output=True,
            text=True,
            timeout=timeout + 30,
        )
        code = proc.stdout.strip()
        status = int(code) if code.isdigit() else 0
        with open(tmp, "rb") as f:
            body = f.read()
        return status, body
    except Exception:
        return 0, b""
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def decode(data):
    """Decode one hour of delta-encoded tick JSON into CSV-ready text rows."""
    times = data["times"]
    n = len(times)
    if n == 0:
        return []
    for col in ("asks", "bids", "askVolumes", "bidVolumes"):
        if not isinstance(data.get(col), list) or len(data[col]) != n:
            raise ValueError(f"column {col} length mismatch")
    multiplier = data["multiplier"]
    if not isinstance(multiplier, (int, float)) or multiplier <= 0:
        raise ValueError("bad multiplier")
    scale = price_scale(multiplier)
    timestamp = data["timestamp"]
    ask_units = round(data["ask"] / multiplier)
    bid_units = round(data["bid"] / multiplier)

    rows = []
    for i in range(n):
        timestamp += times[i]
        ask_units += data["asks"][i]
        bid_units += data["bids"][i]
        rows.append(
            f"{timestamp},{fmt_price(ask_units, multiplier, scale)},"
            f"{fmt_price(bid_units, multiplier, scale)},"
            f"{fmt_num(data['askVolumes'][i] / 1_000_000)},"
            f"{fmt_num(data['bidVolumes'][i] / 1_000_000)}"
        )
    return rows


def fetch_hour(url, args):
    """Return (kind, payload): ('ok', rows) | ('empty', None) | ('failed', note)."""
    attempts = args.retries + 1
    last_status = None
    for a in range(attempts):
        status, body = fetch(url)
        last_status = status
        if status == 200 and body:
            try:
                data = json.loads(body)
            except Exception as e:
                return ("failed", f"bad json: {e}")
            times = data.get("times")
            if isinstance(times, list):
                if times:
                    try:
                        return ("ok", decode(data))
                    except Exception as e:
                        return ("failed", f"decode: {e}")
                if a < attempts - 1 and not args.no_retry_on_empty:
                    time.sleep(args.retry_pause)
                    continue
                return ("empty", None)
        if a < attempts - 1:
            time.sleep(args.retry_pause)
    return ("failed", f"http {last_status}")


def main():
    args = parse_args()
    code = args.code or derive_code(args.instrument)

    start = datetime.strptime(args.from_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(args.to_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if end <= start:
        print("ERROR: --to-date must be after --from-date", file=sys.stderr)
        sys.exit(1)

    total_hours = int((end - start).total_seconds() // 3600)
    hours = [start + timedelta(hours=i) for i in range(total_hours)]

    print("================================")
    print(f"Instrument: {args.instrument} (jetta code: {code})")
    print(f"Range: {args.from_date} -> {args.to_date} GMT ({total_hours} hours)")
    print(f"Workers: {args.workers} | retries/hour: {args.retries}")
    print("================================")

    results = {}
    cond = threading.Condition()
    t0 = time.time()

    def attempt_hour(idx, hour_dt):
        url = f"{JETTA_ROOT}/{code}/{hour_dt:%Y}/{hour_dt.month}/{hour_dt.day}/{hour_dt.hour}"
        outcome = fetch_hour(url, args)
        with cond:
            results[idx] = (hour_dt, outcome)
            cond.notify_all()
        if args.pause:
            time.sleep(args.pause)

    with open(args.output, "w", newline="") as fout:
        fout.write("timestamp,askPrice,bidPrice,askVolume,bidVolume\n")

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for i, h in enumerate(hours):
                pool.submit(attempt_hour, i, h)

            written = 0
            total_ticks = 0
            chunk_start = None
            chunk_ticks = 0
            empties = []
            failed = []

            def flush_chunk(end_dt):
                pct = 100 * written // total_hours
                print(
                    f"chunk: {chunk_start:%Y-%m-%d %H:%M:%S}.000 -> "
                    f"{end_dt:%Y-%m-%d %H:%M:%S}.000 | ticks: {chunk_ticks} | "
                    f"total: {total_ticks} | {pct}%",
                    flush=True,
                )

            with cond:
                while written < total_hours:
                    cond.wait_for(lambda: written in results)
                    hour_dt, (kind, payload) = results.pop(written)

                    if chunk_start is None:
                        chunk_start = hour_dt

                    n = 0
                    if kind == "ok":
                        for row in payload:
                            fout.write(row)
                            fout.write("\n")
                        n = len(payload)
                    elif kind == "empty":
                        empties.append(hour_dt)
                        print(
                            f"empty:  {hour_dt:%Y-%m-%d %H:%M:%S}.000 | "
                            f"server returned no ticks ({args.retries + 1} attempts)",
                            flush=True,
                        )
                    else:
                        failed.append((hour_dt, payload))
                        print(
                            f"failed: {hour_dt:%Y-%m-%d %H:%M:%S}.000 | {payload} "
                            f"({args.retries + 1} attempts)",
                            flush=True,
                        )

                    total_ticks += n
                    chunk_ticks += n
                    written += 1

                    nxt = hour_dt + timedelta(hours=1)
                    if nxt.hour % CHUNK_HOURS == 0:
                        flush_chunk(nxt)
                        chunk_start = None
                        chunk_ticks = 0

            if chunk_start is not None:
                flush_chunk(end)

    duration = time.time() - t0
    print("================================")
    print(f"DONE in {duration:.0f}s | total ticks: {total_ticks}")
    print(f"empty hours: {len(empties)}")
    for h in empties:
        print(f"  empty:  {h:%Y-%m-%d %H:%M} GMT")
    print(f"failed hours: {len(failed)}")
    for h, note in failed:
        print(f"  failed: {h:%Y-%m-%d %H:%M} GMT ({note})")

    if total_ticks == 0:
        print("ERROR: no ticks downloaded at all", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
