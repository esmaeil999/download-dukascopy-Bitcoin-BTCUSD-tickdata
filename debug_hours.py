#!/usr/bin/env python3
"""Diagnose missing tick hours on Dukascopy endpoints.

Modes:

  python3 debug_hours.py BTCUSD 2026-02-01/11 2026-02-01/12 ...
      For each probe (GMT) fetches BOTH endpoints and reports status,
      size and tick count:
        - jetta JSON API (what the downloader uses), one URL per hour:
          /v1/ticks/{CODE}/{Y}/{M}/{D}/{H} on jetta.dukascopy.com
          (CODE uses the metadata form, e.g. BTC-USD)
        - classic bi5 feed (LZMA, 20 bytes per tick):
          /datafeed/{INST}/{Y}/{MM0}/{DD}/{HH}h_ticks.bi5 on datafeed.dukascopy.com

      Requests go through curl with a browser User-Agent: both endpoints
      reject or mis-serve default scripting clients.

  python3 debug_hours.py --count download/file.csv
      Counts rows per hour in a raw tick CSV (timestamp,askPrice,bidPrice,...)
      so you can see exactly which hours the tool produced.

  python3 debug_hours.py --compare lib.csv ours.csv
      Compares two raw tick CSVs: row counts, shared timestamps, and
      price/volume mismatches (tolerant to float formatting).
"""

import csv
import json
import lzma
import math
import os
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone

JETTA_ROOT = "https://jetta.dukascopy.com/v1/ticks"
BI5_ROOT = "https://datafeed.dukascopy.com/datafeed"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def fetch(url):
    """Fetch via curl with a browser User-Agent; return (status, body)."""
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
        status = int(code) if code.isdigit() else None
        with open(tmp, "rb") as f:
            body = f.read()
        if status is None:
            return None, (proc.stderr.strip() or "curl failed").encode()
        return status, body
    except Exception as e:
        return None, str(e).encode()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def code_variants(instrument):
    """jetta uses the metadata code form (BTC-USD); try safe fallbacks too."""
    base = instrument.strip().replace("/", "").upper()
    out = []
    if len(base) == 6:
        out.append(base[:3] + "-" + base[3:])  # BTCUSD -> BTC-USD (metadata code)
    out.append(base)
    for v in list(out):
        out.append(v.lower())
    deduped = []
    for v in out:
        if v not in deduped:
            deduped.append(v)
    return deduped


def decode_bi5(blob):
    try:
        raw = lzma.decompress(blob, format=lzma.FORMAT_ALONE)
        if len(raw) % 20 == 0:
            return raw
    except Exception:
        pass
    try:
        raw = lzma.decompress(
            blob,
            format=lzma.FORMAT_RAW,
            filters=[{"id": lzma.FILTER_LZMA1, "preset": 9}],
        )
        if len(raw) % 20 == 0:
            return raw
    except Exception:
        pass
    return None


def probe_jetta(instrument, y, m, d, h):
    """Try code variants; stop at the first HTTP 200."""
    last = None
    for code in code_variants(instrument):
        url = f"{JETTA_ROOT}/{code}/{y}/{m}/{d}/{h}"
        status, body = fetch(url)
        ticks = None
        note = ""
        if status == 200 and body:
            try:
                data = json.loads(body)
                times = data.get("times")
                if isinstance(times, list):
                    ticks = len(times)
            except Exception as e:
                note = f" json_error={e}"
        last = (url, status, len(body), ticks, note)
        if status == 200:
            break
    url, status, size, ticks, note = last
    tick_txt = f" ticks={ticks}" if ticks is not None else ""
    return f"http={status} bytes={size}{tick_txt}{note}  [{url}]"


def probe_bi5(instrument, y, m, d, h):
    inst = instrument.strip().replace("/", "").upper()
    url = f"{BI5_ROOT}/{inst}/{y}/{m - 1:02d}/{d:02d}/{h:02d}h_ticks.bi5"
    status, body = fetch(url)
    ticks = None
    note = ""
    if status == 200 and body:
        raw = decode_bi5(body)
        if raw is not None:
            ticks = len(raw) // 20
        else:
            note = " lzma_error"
    tick_txt = f" ticks={ticks}" if ticks is not None else ""
    return f"http={status} bytes={len(body)}{tick_txt}{note}  [{url}]"


def run_probes(instrument, probes):
    print(f"Instrument: {instrument}")
    print(f"Probing {len(probes)} hour(s) on both Dukascopy endpoints...")
    for p in probes:
        date_part, hour_part = p.split("/")
        y, m, d = (int(x) for x in date_part.split("-"))
        h = int(hour_part)
        print(f"\n=== {y:04d}-{m:02d}-{d:02d} {h:02d}:00 GMT ===")
        print(f"  jetta: {probe_jetta(instrument, y, m, d, h)}")
        print(f"  bi5  : {probe_bi5(instrument, y, m, d, h)}")


def run_count(csv_path):
    counts = Counter()
    with open(csv_path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None) or []
        idx = {name: i for i, name in enumerate(header)}
        ti = idx.get("timestamp")
        if ti is None:
            print("ERROR: no 'timestamp' column", file=sys.stderr)
            sys.exit(1)
        for row in reader:
            if not row:
                continue
            ts = int(float(row[ti]))
            key = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime(
                "%Y-%m-%d %H"
            )
            counts[key] += 1

    if not counts:
        print("CSV has no rows")
        return

    first = datetime.strptime(min(counts), "%Y-%m-%d %H")
    last = datetime.strptime(max(counts), "%Y-%m-%d %H")
    print("hour             rows")
    h = first
    while h <= last:
        key = h.strftime("%Y-%m-%d %H")
        n = counts.get(key, 0)
        mark = "" if n else "   <-- EMPTY"
        print(f"{key}   {n:>10}{mark}")
        h += timedelta(hours=1)


def load_ticks(path):
    """Return {timestamp_ms: (ask, bid, askVolume, bidVolume)}."""
    out = {}
    with open(path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None) or []
        idx = {name: i for i, name in enumerate(header)}
        ti = idx.get("timestamp")
        ai, bi = idx.get("askPrice"), idx.get("bidPrice")
        av, bv = idx.get("askVolume"), idx.get("bidVolume")
        if ti is None or ai is None or bi is None:
            print(f"ERROR: {path} lacks timestamp/askPrice/bidPrice columns", file=sys.stderr)
            sys.exit(1)
        for row in reader:
            if not row:
                continue
            ts = int(float(row[ti]))
            out[ts] = (
                float(row[ai]),
                float(row[bi]),
                float(row[av]) if av is not None else None,
                float(row[bv]) if bv is not None else None,
            )
    return out


def run_compare(a_path, b_path):
    A = load_ticks(a_path)
    B = load_ticks(b_path)
    keys_a = set(A)
    keys_b = set(B)
    common = keys_a & keys_b
    only_a = keys_a - keys_b
    only_b = keys_b - keys_a

    price_mismatch = 0
    vol_mismatch = 0
    samples = []
    for k in sorted(common):
        pa, pb = A[k], B[k]
        if not (
            math.isclose(pa[0], pb[0], rel_tol=1e-9, abs_tol=1e-9)
            and math.isclose(pa[1], pb[1], rel_tol=1e-9, abs_tol=1e-9)
        ):
            price_mismatch += 1
            if len(samples) < 5:
                samples.append((k, pa, pb))
        if pa[2] is not None and pb[2] is not None:
            if not (
                math.isclose(pa[2], pb[2], rel_tol=1e-6, abs_tol=1e-12)
                and math.isclose(pa[3], pb[3], rel_tol=1e-6, abs_tol=1e-12)
            ):
                vol_mismatch += 1

    print(f"compare: rows lib={len(A)} ours={len(B)}")
    print(
        f"compare: timestamps common={len(common)} "
        f"only_lib={len(only_a)} only_ours={len(only_b)}"
    )
    print(f"compare: price mismatches={price_mismatch} volume mismatches={vol_mismatch}")
    for k, pa, pb in samples:
        ts = datetime.fromtimestamp(k / 1000, tz=timezone.utc)
        print(f"  sample {ts:%Y-%m-%d %H:%M:%S.%f} lib={pa} ours={pb}")
    if only_a:
        ts = datetime.fromtimestamp(min(only_a) / 1000, tz=timezone.utc)
        print(f"  first only-in-lib timestamp: {ts:%Y-%m-%d %H:%M:%S.%f}")
    if only_b:
        ts = datetime.fromtimestamp(min(only_b) / 1000, tz=timezone.utc)
        print(f"  first only-in-ours timestamp: {ts:%Y-%m-%d %H:%M:%S.%f}")

    if only_a or only_b or price_mismatch:
        print("VERDICT: MISMATCH")
    else:
        print("VERDICT: outputs match")


def main():
    args = sys.argv[1:]
    if args and args[0] == "--count":
        run_count(args[1])
        return
    if args and args[0] == "--compare":
        run_compare(args[1], args[2])
        return
    if len(args) < 2:
        print(__doc__)
        sys.exit(1)
    run_probes(args[0], args[1:])


if __name__ == "__main__":
    main()
