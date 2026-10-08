"""Ingest raw get_price_history JSON saved from the IBKR connector (read-only market data).

Raw files are preserved untouched in data/raw/ibkr_connector/ with a manifest (sha256, retrieval info).
Normalised output follows contracts.py bars (UTC 'ts_open' index). Daily bars are labelled by the RTH session
open; one bar covers that whole session. Source prices are 'Last' and UNADJUSTED for dividends (ETF ex-dividend
drops appear as price drops); delayed feed. These facts are written into the manifest.
"""
from __future__ import annotations
import hashlib, json, shutil
from pathlib import Path
import pandas as pd


def ingest(raw_json: str | Path, symbol: str, contract_id: int, exchange: str, raw_dir: str | Path,
           processed_dir: str | Path, retrieved_on: str) -> dict:
    raw_json, raw_dir, processed_dir = Path(raw_json), Path(raw_dir), Path(processed_dir)
    raw_dir.mkdir(parents=True, exist_ok=True); processed_dir.mkdir(parents=True, exist_ok=True)
    blob = raw_json.read_bytes()
    sha = hashlib.sha256(blob).hexdigest()
    dest = raw_dir / f"{symbol}_1d_{sha[:12]}.json"
    if not dest.exists():
        shutil.copyfile(raw_json, dest)
    d = json.loads(blob)
    n = len(d["time"])
    for k in ("open", "high", "low", "close", "volume"):
        if len(d[k]) != n:
            raise ValueError(f"{symbol}: array length mismatch for {k}")
    idx = pd.DatetimeIndex(pd.to_datetime(d["time"], utc=True), name="ts_open")
    df = pd.DataFrame({k: pd.array(d[k], dtype="float64") for k in ("open", "high", "low", "close", "volume")}, index=idx)
    df["contract"] = symbol  # equities: symbol only; no roll concept
    if not df.index.is_monotonic_increasing or df.index.has_duplicates:
        raise ValueError(f"{symbol}: timestamps not strictly increasing")
    out = processed_dir / f"{symbol}_1d.csv"
    df.to_csv(out)
    return {"symbol": symbol, "contract_id": contract_id, "exchange": exchange, "raw_file": str(dest),
            "sha256": sha, "n_bars": n, "first": str(idx[0]), "last": str(idx[-1]), "retrieved_on": retrieved_on,
            "source_tag": d.get("source"), "delayed_seconds": d.get("delayed"), "chart_step_s": d.get("chart_step"),
            "adjusted_for_dividends": False, "processed_file": str(out)}
