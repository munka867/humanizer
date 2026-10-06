#!/usr/bin/env python
"""Run quality checks on bar CSVs.  Example:
  python scripts/check_data.py data/raw/MES_1m_MESZ4.csv --freq 1min --tz America/New_York --label open
Exit code 1 if any file has error-severity issues or fails to load."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tradelab.contracts import Instrument  # noqa: E402
from tradelab.data.loader import load_bars  # noqa: E402
from tradelab.data.quality import quality_report, render_markdown  # noqa: E402

INSTRUMENTS = {"MES": Instrument("MES", 5.0, 0.25), "ES": Instrument("ES", 50.0, 0.25),
               "MNQ": Instrument("MNQ", 2.0, 0.25), "NQ": Instrument("NQ", 20.0, 0.25)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--freq", default="1min", help="expected bar size, e.g. 1min or 5min")
    ap.add_argument("--tz", default=None, help="IANA tz of NAIVE timestamps (e.g. America/New_York); not needed for UTC/epoch/offset times")
    ap.add_argument("--label", default="open", choices=["open", "close"], help="does the timestamp mark bar open or close?")
    ap.add_argument("--instrument", default="MES", choices=sorted(INSTRUMENTS))
    ap.add_argument("--sort", action="store_true", help="file is newest-first / unsorted")
    ap.add_argument("--contract", default=None, help="contract label if the file has no contract column, e.g. MESZ4")
    ap.add_argument("--out", default=None, help="also write the markdown report here")
    a = ap.parse_args(argv)
    rc, md = 0, []
    for f in a.files:
        try:
            bars = load_bars(f, tz_hint=a.tz, timestamp_label=a.label, bar_freq=a.freq, contract=a.contract,
                             validate="keep", sort=a.sort)
        except Exception as e:  # report and continue
            md.append(f"# {f}\n\nLOAD FAILED: {e}\n")
            rc = 1
            continue
        rep = quality_report(bars, a.freq, INSTRUMENTS[a.instrument])
        md.append(f"<!-- {f} -->\n" + render_markdown(rep))
        rc |= 0 if rep["ok"] else 1
    text = "\n".join(md)
    print(text)
    if a.out:
        Path(a.out).write_text(text)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
