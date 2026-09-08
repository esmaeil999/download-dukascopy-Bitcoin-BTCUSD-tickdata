#!/usr/bin/env python3
"""Diagnose missing tick hours on Dukascopy endpoints.

Modes:

  python3 debug_hours.py BTCUSD 2026-02-01/11 2026-02-01/12 ...
      For each probe (GMT) fetches BOTH endpoints and reports status,
      size and tick count:
        - jetta JSON API (what dukascopy-node downloads):
          https://jetta.dukascopy.com/v1/ticks/{CODE}/{Y}/{M}/{D}/{H}
        - classic bi5 feed (LZMA, 20 bytes per tick):
          https://datafeed.dukascopy.com/datafeed/{INST}/{Y}/{MM0}/{DD}/{HH}h_ticks.bi5

  python3 debug_hours.py --count download/file.csv
      Counts rows per hour in a raw dukascopy-node CSV
      (timestamp,askPrice,bidPrice[,askVolume,bidVolume]) so you can see
      exactly which hours the tool produced.
"""

import csv
import json
import lzma
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone

JETTA_ROOT = "https://jetta.dukascopy.com/v1/ticks"
BI5_ROOT = "https://datafeed.dukascopy.com/datafeed"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "debug-hours/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception as e:  # network errors etc.
        return None, str(e).encode()


def code_variants(instrument):
    base = instrument.strip().replace("/", "")
    out = []
    for c in (base.upper(), base.lower()):
        if c not in out:
            out.append(c)
    return out


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
        try:
            raw = lzma.decompress(body, format=lzma.FORMAT_ALONE)
            ticks = len(raw) // 20
        except Exception as e:
            note = f" lzma_error={e}"
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


def main():
    args = sys.argv[1:]
    if args and args[0] == "--count":
        run_count(args[1])
        return
    if len(args) < 2:
        print(__doc__)
        sys.exit(1)
    run_probes(args[0], args[1:])


if __name__ == "__main__":
    main()
