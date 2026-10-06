import numpy as np
import pandas as pd
import pytest
from tradelab.data.loader import resample_bars


def one_min(n=20, start="2024-01-02 14:30", contract=None):
    idx = pd.date_range(pd.Timestamp(start, tz="UTC"), periods=n, freq="1min", name="ts_open")
    c = np.arange(n, dtype=float) + 100
    df = pd.DataFrame({"open": c - 0.5, "high": c + 1, "low": c - 1, "close": c, "volume": np.ones(n)}, index=idx)
    if contract:
        df["contract"] = contract
    return df


def test_aggregation_and_open_label():
    out = resample_bars(one_min(), "5min")
    assert len(out) == 4
    r = out.iloc[0]
    assert out.index[0] == pd.Timestamp("2024-01-02 14:30", tz="UTC")  # labelled by bar OPEN
    assert (r.open, r.high, r.low, r.close, r.volume) == (99.5, 105.0, 99.0, 104.0, 5.0)


def test_incomplete_bins_dropped():
    df = one_min().drop(index=pd.Timestamp("2024-01-02 14:42", tz="UTC"))  # hole in 3rd bin
    out = resample_bars(df, "5min", src_freq="1min")
    assert pd.Timestamp("2024-01-02 14:40", tz="UTC") not in out.index and len(out) == 3
    # partial trailing bin dropped
    assert len(resample_bars(one_min(18), "5min")) == 3


def test_misaligned_start_does_not_create_partial_bar():
    out = resample_bars(one_min(20, "2024-01-02 14:32"), "5min")
    assert out.index[0] == pd.Timestamp("2024-01-02 14:35", tz="UTC")  # 14:30 bin is incomplete


def test_no_lookahead_prefix_invariance():
    """Output bars computed from a truncated history equal the same bars computed from full history,
    and a bar only appears once its last source minute has arrived."""
    full = one_min(60)
    full_out = resample_bars(full, "5min")
    for k in range(5, 60):
        part = resample_bars(full.iloc[:k], "5min", src_freq="1min")
        pd.testing.assert_frame_equal(part, full_out.loc[part.index], check_freq=False)
        for t in part.index:
            assert t + pd.Timedelta("5min") <= full.index[k - 1] + pd.Timedelta("1min")


def test_changing_future_bar_does_not_change_past_output():
    a, b = one_min(30), one_min(30)
    b.iloc[-1, :4] = 9999.0
    oa, ob = resample_bars(a, "5min"), resample_bars(b, "5min")
    pd.testing.assert_frame_equal(oa.iloc[:-1], ob.iloc[:-1])


def test_multi_contract_bin_dropped_and_errors():
    df = one_min(10, contract="A")
    df.loc[df.index[7:], "contract"] = "B"
    out = resample_bars(df, "5min")
    assert list(out.index) == [df.index[0]] and out.attrs["resample_dropped"]["multi_contract"] == 1
    with pytest.raises(ValueError):
        resample_bars(one_min(), "7min")
    dup = pd.concat([one_min(5), one_min(5)])
    with pytest.raises(ValueError):
        resample_bars(dup, "5min", src_freq="1min")
