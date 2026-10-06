"""Run-level guards (R-06): refuse real runs whose contract labels cannot support roll detection."""
from __future__ import annotations
import re
import pandas as pd

CONTRACT_RE = re.compile(r"[A-Z0-9]{1,4}[FGHJKMNQUVXZ]\d{1,2}")
MAX_SINGLE_CONTRACT_DAYS = 100


def check_contract_labels(bars: pd.DataFrame, synthetic: bool) -> None:
    """Raise unless a per-bar `contract` column exists with real month/year labels (SYNTH* allowed only for
    synthetic data), and a single label does not span more than ~100 days (a quarterly contract cannot)."""
    if "contract" not in bars.columns or bars["contract"].isna().any():
        raise ValueError("bars need a complete per-bar 'contract' column (e.g. MESZ4): without it roll days cannot "
                         "be detected and results would silently include roll gaps")
    labels = pd.Series(bars["contract"].astype(str).unique())
    if synthetic:
        return
    bad = [x for x in labels if not CONTRACT_RE.fullmatch(x)]
    if bad:
        raise ValueError(f"contract labels {bad[:5]} do not look like month/year contract codes (root symbols or a "
                         "stamped label disable roll detection)")
    span = (bars.index.max() - bars.index.min()).days
    if len(labels) == 1 and span > MAX_SINGLE_CONTRACT_DAYS:
        raise ValueError(f"a single contract label spans {span} days; the file is probably a stamped/continuous "
                         "series without real per-bar contracts, so roll detection is disabled")
