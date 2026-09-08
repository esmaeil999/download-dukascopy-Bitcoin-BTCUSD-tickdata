#!/usr/bin/env python3
"""Convert dukascopy-node CSV (timestamp,askPrice,bidPrice[,askVolume,bidVolume])
to JForex-style CSV: GmtTime,Bid,Ask,BidVolume,AskVolume (GMT).

Usage:
    python convert.py INPUT.csv [MORE.csv ...] -o OUTPUT.csv
    python convert.py   # reads ./download/*.csv -> ticks_converted.csv
"""

import argparse
import csv
import glob
import sys
from datetime import datetime, timezone


def parse_args():
    parser = argparse.ArgumentParser(
        description="Convert dukascopy-node CSV files to JForex-style CSV (GMT)."
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        help="Input CSV file(s). Defaults to all CSVs in ./download",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="ticks_converted.csv",
        help="Output CSV path (default: ticks_converted.csv)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    files = sorted(args.inputs) if args.inputs else sorted(glob.glob("download/*.csv"))
    if not files:
        print("No input CSV files found.", file=sys.stderr)
        sys.exit(1)

    total = 0
    with open(args.output, "w", newline="") as fo:
        fo.write("GmtTime,Bid,Ask,BidVolume,AskVolume\n")
        for path in files:
            with open(path, newline="") as fi:
                reader = csv.reader(fi)
                header = next(reader, None) or []
                idx = {name: i for i, name in enumerate(header)}
                has_vol = "bidVolume" in idx and "askVolume" in idx
                if not has_vol:
                    print(f"WARNING: {path} has no volume columns; writing 0", file=sys.stderr)
                for row in reader:
                    if not row:
                        continue
                    ts = int(float(row[idx["timestamp"]]))
                    dt = datetime.fromtimestamp(ts // 1000, tz=timezone.utc)
                    gmt = f"{dt:%Y-%m-%d %H:%M:%S}.{ts % 1000:03d}"
                    bid = row[idx["bidPrice"]]
                    ask = row[idx["askPrice"]]
                    bid_vol = row[idx["bidVolume"]] if has_vol else "0"
                    ask_vol = row[idx["askVolume"]] if has_vol else "0"
                    fo.write(f"{gmt},{bid},{ask},{bid_vol},{ask_vol}\n")
                    total += 1
            print(f"converted {path} (total rows: {total})", flush=True)

    print(f"DONE. rows = {total} -> {args.output}")


if __name__ == "__main__":
    main()
