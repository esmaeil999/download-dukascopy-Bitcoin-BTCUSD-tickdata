#!/usr/bin/env python3
"""Download Dukascopy tick data: jetta JSON API with automatic bi5 fallback.

Every hour is fetched from the jetta JSON API first. If jetta permanently
rejects the hour (http 4xx) or persistently returns no ticks, the classic
bi5 archive feed (datafeed.dukascopy.com — the feed JForex uses) is tried
instead, so old history stays downloadable even where jetta stops serving
it. After 24 consecutive permanent rejections the downloader switches to
bi5-first mode and probes jetta once per day (hour 00 GMT) to notice when
the range becomes servable again.

Logs are transparent: one progress line per 6-hour chunk, one line per
bi5-served hour, and explicit lines for empty/failed hours and mode
switches:

    chunk: 2018-07-12 00:00:00.000 -> 2018-07-12 06:00:00.000 | ticks: 17784 | total: 12097760 | 52%
    bi5:   2018-01-14 06:00:00.000 | 4823 ticks (jetta: http 400)
    note:  jetta rejected 24 consecutive hours (http 4xx) — switching to the bi5 archive feed
    empty: 2018-07-12 06:00:00.000 | server returned no ticks (6 attempts)

The jetta payload is delta-encoded per hour:
    {timestamp, multiplier, ask, bid, times[], asks[], bids[], askVolumes[], bidVolumes[]}
where times/asks/bids are successive deltas accumulated from the base
values. This mirrors dukascopy-node's data-normaliser.

The bi5 payload is an LZMA-alone stream of 20-byte records >IIIff:
    (ms offset, ask points, bid points, askVolume, bidVolume)
with price = points * point (per-instrument, see --point).

Output CSV (dukascopy-node compatible):
    timestamp,askPrice,bidPrice,askVolume,bidVolume

Usage:
    python download.py --instrument BTCUSD --from-date 2018-07-01 --to-date 2019-01-01 -o download/out.csv

Testing overrides: JETTA_ROOT and BI5_ROOT accept file:///path/to/dir.
"""

import argparse
import json
import lzma
import os
import re
import struct
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

JETTA_ROOT = os.environ.get("JETTA_ROOT", "https://jetta.dukascopy.com/v1/ticks")
BI5_ROOT = os.environ.get("BI5_ROOT", "https://datafeed.dukascopy.com/datafeed")
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
CHUNK_HOURS = 6
MUTE_AFTER = 24  # consecutive permanent jetta rejections before bi5-first mode
RECORD = struct.Struct(">IIIff")  # ms offset, ask points, bid points, askVolume, bidVolume
PERMANENT = frozenset({400, 401, 403, 404, 410})

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
        "--source",
        choices=["auto", "jetta", "bi5"],
        default="auto",
        help="auto: jetta first, bi5 fallback (default); jetta/bi5: force one feed",
    )
    parser.add_argument(
        "--point",
        type=float,
        default=None,
        help="bi5 price multiplier (default: built-in per instrument, BTCUSD=0.1)",
    )
    parser.add_argument(
        "--no-retry-on-empty",
        action="store_true",
        help="Accept empty datasets without retrying",
    )
    return parser.parse_args()


IDX_CMD_RE = re.compile(r"^([A-Z0-9]+?)(IDX|CMD)([A-Z]{3})$")
STOCK_RE = re.compile(r"^([A-Z0-9]+?)([A-Z]{2})([A-Z]{3})$")


def derive_code(instrument):
    """Derive the jetta instrument code from a plain instrument name.

    Verified against dukascopy-node's instrument metadata: matches
    1483/1499 entries (the only misses are single-letter stock tickers
    like A.US/USD -- pass --code for those).
    """
    base = instrument.strip().replace("/", "").upper()
    m = IDX_CMD_RE.match(base)
    if m and len(base) > 6:
        return f"{m.group(1)}.{m.group(2)}-{m.group(3)}"  # USA500IDXUSD -> USA500.IDX-USD
    if len(base) == 6:
        return base[:3] + "-" + base[3:]  # BTCUSD -> BTC-USD
    m = STOCK_RE.match(base)
    if m and len(base) > 6:
        return f"{m.group(1)}.{m.group(2)}-{m.group(3)}"  # AAPLUSUSD -> AAPL.US-USD
    return base


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


def plural(n):
    return "attempt" if n == 1 else "attempts"


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


def parse_bi5(raw, hour_dt, point):
    """Decode bi5 records into CSV-ready text rows (same shape as jetta rows)."""
    base_ms = int(hour_dt.timestamp() * 1000)
    rows = []
    for off in range(0, len(raw), RECORD.size):
        ms, ask_i, bid_i, avol, bvol = RECORD.unpack_from(raw, off)
        rows.append(
            f"{base_ms + ms},{fmt_num(ask_i * point)},{fmt_num(bid_i * point)},"
            f"{fmt_num(avol)},{fmt_num(bvol)}"
        )
    return rows


def jetta_hour(url, args):
    """Return (kind, rows, short, detail, status).

    kind: ok | empty | failed. Permanent http 4xx is not retried (it cannot
    heal); transient errors and empty 200s are retried as configured.
    """
    attempts = args.retries + 1
    last_status = 0
    for a in range(attempts):
        status, body = fetch(url)
        last_status = status
        if status == 200 and body:
            try:
                data = json.loads(body)
            except Exception as e:
                short = f"bad json: {e}"
                return "failed", None, short, f"{short} ({a + 1} {plural(a + 1)})", status
            times = data.get("times")
            if isinstance(times, list):
                if times:
                    try:
                        return "ok", decode(data), "ok", "ok", status
                    except Exception as e:
                        short = f"decode: {e}"
                        return "failed", None, short, f"{short} ({a + 1} {plural(a + 1)})", status
                if a < attempts - 1 and not args.no_retry_on_empty:
                    time.sleep(args.retry_pause)
                    continue
                return "empty", None, "empty", f"empty ({a + 1} {plural(a + 1)})", status
        if status in PERMANENT:
            short = f"http {status}"
            return "failed", None, short, f"{short} ({a + 1} {plural(a + 1)})", status
        if a < attempts - 1:
            time.sleep(args.retry_pause)
    short = f"http {last_status}"
    return "failed", None, short, f"{short} ({attempts} {plural(attempts)})", last_status


def bi5_hour(inst, hour_dt, point, args):
    """Return (kind, rows, short, detail, status) for the classic bi5 feed."""
    rel = (
        f"{inst}/{hour_dt:%Y}/{hour_dt.month - 1:02d}/"
        f"{hour_dt:%d}/{hour_dt:%H}h_ticks.bi5"
    )
    url = f"{BI5_ROOT}/{rel}"
    attempts = args.retries + 1
    last_short = "error"
    last_status = 0
    for a in range(attempts):
        status, body = fetch(url)
        last_status = status
        if status == 200 and body:
            raw = decode_bi5(body)
            if raw is None:
                last_short = "bad lzma"
            else:
                rows = parse_bi5(raw, hour_dt, point)
                if rows:
                    return "ok", rows, "ok", "ok", status
                return "empty", None, "empty", f"empty ({a + 1} {plural(a + 1)})", status
        elif status in PERMANENT:
            short = f"http {status}"
            detail = f"{short} ({a + 1} {plural(a + 1)})"
            if status == 404:
                return "empty", None, short, detail, status
            return "failed", None, short, detail, status
        else:
            last_short = f"http {status}"
        if a < attempts - 1:
            time.sleep(args.retry_pause)
    return "failed", None, last_short, f"{last_short} ({attempts} {plural(attempts)})", last_status


def main():
    args = parse_args()
    inst = args.instrument.strip().replace("/", "").upper()
    code = args.code or derive_code(args.instrument)

    point = args.point
    if point is None:
        point = default_point(inst)
    if point is None and args.source != "jetta":
        if args.source == "bi5":
            print(f"ERROR: no known point size for {inst}; pass --point", file=sys.stderr)
            sys.exit(1)
        print(f"note:  no known point size for {inst}; bi5 fallback disabled (pass --point)")

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
    print(f"Source: {args.source}" + (f" | bi5 point: {point}" if point is not None else ""))
    print(f"Workers: {args.workers} | retries/hour: {args.retries}")
    print("================================")

    results = {}
    cond = threading.Condition()
    state = {"streak": 0, "muted": False}
    t0 = time.time()

    def attempt_hour(idx, hour_dt):
        with cond:
            muted = state["muted"]

        use_jetta = args.source != "bi5" and (not muted or hour_dt.hour == 0)

        if use_jetta:
            url = (
                f"{JETTA_ROOT}/{code}/{hour_dt:%Y}/"
                f"{hour_dt.month}/{hour_dt.day}/{hour_dt.hour}"
            )
            jkind, rows, jshort, jlong, jstatus = jetta_hour(url, args)
            if jkind in ("ok", "empty"):
                jevent = jkind
            elif jstatus in PERMANENT:
                jevent = "perm_fail"
            else:
                jevent = "transient_fail"
            with cond:
                if jkind in ("ok", "empty"):
                    # any real 200 response proves jetta serves this range
                    state["streak"] = 0
                    state["muted"] = False
                elif jstatus in PERMANENT:
                    state["streak"] += 1
                    if state["streak"] >= MUTE_AFTER:
                        state["muted"] = True
                # transient failures leave streak/muted untouched
            if jkind == "ok":
                with cond:
                    results[idx] = (hour_dt, ("ok", rows, "jetta", None, "ok"))
                    cond.notify_all()
                if args.pause:
                    time.sleep(args.pause)
                return
        else:
            jkind = None
            jevent = "skipped" if args.source == "bi5" else "skipped_muted"

        # bi5 fallback (or bi5-only mode)
        if args.source != "jetta" and point is not None:
            bkind, brows, bshort, blong, bstatus = bi5_hour(inst, hour_dt, point, args)
            if bkind == "ok":
                if not use_jetta:
                    jnote = "disabled" if args.source == "bi5" else "skipped (jetta muted)"
                else:
                    jnote = jshort
                outcome = ("ok", brows, "bi5", jnote, jevent)
            elif use_jetta:
                if bkind == "empty":
                    outcome = (
                        "empty", None, "-",
                        f"no ticks (jetta: {jlong}, bi5: {blong})", jevent,
                    )
                else:
                    outcome = (
                        "failed", f"jetta {jlong}; bi5 {blong}", "-", None, jevent,
                    )
            else:
                if bkind == "empty":
                    outcome = ("empty", None, "-", f"no ticks (bi5: {blong})", jevent)
                else:
                    outcome = ("failed", f"bi5 {blong}", "-", None, jevent)
        else:
            if use_jetta and jkind == "empty":
                outcome = ("empty", None, "jetta", None, "empty")
            elif use_jetta:
                outcome = ("failed", f"jetta {jlong}", "jetta", None, jevent)
            else:
                outcome = ("failed", "jetta disabled", "jetta", None, jevent)

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
            src_counts = {"jetta": 0, "bi5": 0}
            bi5_active = args.source != "jetta" and point is not None
            local_streak = 0
            local_muted = False

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
                    hour_dt, (kind, payload, source, note, jevent) = results.pop(written)

                    # Mode-switch notes are derived here, in hour order, so
                    # worker timing can never scramble them.
                    if jevent in ("ok", "empty"):
                        if local_muted:
                            print(
                                "note:  jetta is responding again — back to jetta",
                                flush=True,
                            )
                            local_muted = False
                        local_streak = 0
                    elif jevent in ("perm_fail", "skipped_muted"):
                        local_streak += 1
                        if bi5_active and not local_muted and local_streak >= MUTE_AFTER:
                            print(
                                f"note:  jetta rejected {MUTE_AFTER} consecutive hours "
                                "(http 4xx) — switching to the bi5 archive feed",
                                flush=True,
                            )
                            local_muted = True

                    if chunk_start is None:
                        chunk_start = hour_dt

                    n = 0
                    if kind == "ok":
                        for row in payload:
                            fout.write(row)
                            fout.write("\n")
                        n = len(payload)
                        src_counts[source] = src_counts.get(source, 0) + 1
                        if source == "bi5":
                            print(
                                f"bi5:   {hour_dt:%Y-%m-%d %H:%M:%S}.000 | "
                                f"{n} ticks (jetta: {note})",
                                flush=True,
                            )
                    elif kind == "empty":
                        empties.append(hour_dt)
                        if note is None:
                            note = f"server returned no ticks ({args.retries + 1} attempts)"
                        print(
                            f"empty:  {hour_dt:%Y-%m-%d %H:%M:%S}.000 | {note}",
                            flush=True,
                        )
                    else:
                        failed.append((hour_dt, payload))
                        print(
                            f"failed: {hour_dt:%Y-%m-%d %H:%M:%S}.000 | {payload}",
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
    print(f"sources: jetta {src_counts.get('jetta', 0)}h | bi5 {src_counts.get('bi5', 0)}h")
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
