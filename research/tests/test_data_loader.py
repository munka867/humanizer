import numpy as np
import pandas as pd
import pytest
from tradelab.contracts import BAR_COLUMNS, BAR_INDEX_NAME
from tradelab.data.loader import load_bars

# 2024-01-02 14:30:00 UTC == 09:30 ET
T0 = pd.Timestamp("2024-01-02 14:30:00", tz="UTC")


def write(tmp_path, text, name="x.csv"):
    p = tmp_path / name
    p.write_text(text)
    return p


def check_format(df, n=2):
    assert list(df.columns[:5]) == BAR_COLUMNS
    assert df.index.name == BAR_INDEX_NAME and str(df.index.tz) == "UTC"
    assert all(df[c].dtype == "float64" for c in BAR_COLUMNS)
    assert len(df) == n and df.index[0] == T0 and df.index[1] == T0 + pd.Timedelta("1min")


def test_iso_z(tmp_path):
    p = write(tmp_path, "Time,Open,High,Low,Close,Volume\n2024-01-02T14:30:00Z,1,2,0.5,1.5,10\n2024-01-02T14:31:00Z,1.5,2,1,1.75,5\n")
    check_format(load_bars(p))


def test_iso_offsets_mixed_dst(tmp_path):
    p = write(tmp_path, "datetime,open,high,low,close,volume\n2024-01-02 09:30:00-05:00,1,2,0.5,1.5,10\n2024-01-02 09:31:00-05:00,1.5,2,1,1.75,5\n")
    check_format(load_bars(p))


@pytest.mark.parametrize("scale", [1, 1000])
def test_epoch(tmp_path, scale):
    t = int(T0.timestamp())
    p = write(tmp_path, f"timestamp,open,high,low,close,volume\n{t*scale},1,2,0.5,1.5,10\n{(t+60)*scale},1.5,2,1,1.75,5\n")
    check_format(load_bars(p))


def test_naive_requires_tz_then_converts(tmp_path):
    p = write(tmp_path, "Date,Open,High,Low,Close,Volume\n2024-01-02 09:30:00,1,2,0.5,1.5,10\n2024-01-02 09:31:00,1.5,2,1,1.75,5\n")
    with pytest.raises(ValueError, match="tz_hint"):
        load_bars(p)
    check_format(load_bars(p, tz_hint="America/New_York"))


def test_naive_summer_uses_dst_offset(tmp_path):
    p = write(tmp_path, "Date,Open,High,Low,Close,Volume\n2024-07-02 09:30:00,1,2,0.5,1.5,10\n")
    assert load_bars(p, tz_hint="America/New_York").index[0] == pd.Timestamp("2024-07-02 13:30", tz="UTC")


def test_separate_date_time_semicolon_and_extra_cols(tmp_path):
    p = write(tmp_path, "Date;Time;Open;High;Low;Close;Vol;WAP\n2024-01-02;09:30:00;1;2;0.5;1.5;10;9\n2024-01-02;09:31:00;1.5;2;1;1.75;5;9\n")
    check_format(load_bars(p, tz_hint="America/New_York"))


def test_ibkr_suffix_timezone(tmp_path):
    p = write(tmp_path, "date,open,high,low,close,volume,barCount\n20240102 09:30:00 US/Eastern,1,2,0.5,1.5,10,3\n20240102 09:31:00 US/Eastern,1.5,2,1,1.75,5,3\n")
    check_format(load_bars(p))
    with pytest.raises(ValueError, match="tz_hint"):
        load_bars(p, tz_hint="UTC")


def test_close_label_shifts_and_needs_freq(tmp_path):
    p = write(tmp_path, "Time,Open,High,Low,Close,Volume\n2024-01-02T14:31:00Z,1,2,0.5,1.5,10\n2024-01-02T14:32:00Z,1.5,2,1,1.75,5\n")
    with pytest.raises(ValueError, match="bar_freq"):
        load_bars(p, timestamp_label="close")
    check_format(load_bars(p, timestamp_label="close", bar_freq="1min"))


def test_close_named_column_with_open_label_raises(tmp_path):
    p = write(tmp_path, "close_time,open,high,low,close,volume\n2024-01-02T14:31:00Z,1,2,0.5,1.5,10\n")
    with pytest.raises(ValueError, match="CLOSE"):
        load_bars(p)


def test_ambiguous_local_time_raises(tmp_path):
    p = write(tmp_path, "Date,Open,High,Low,Close,Volume\n2024-11-03 01:30:00,1,2,0.5,1.5,10\n")
    with pytest.raises(ValueError, match="ambiguous"):
        load_bars(p, tz_hint="America/New_York")


def test_duplicates_order_and_keep(tmp_path):
    p = write(tmp_path, "time,open,high,low,close,volume\n2024-01-02T14:31:00Z,1,2,0.5,1.5,10\n2024-01-02T14:30:00Z,1,2,0.5,1.5,10\n")
    with pytest.raises(ValueError, match="sort"):
        load_bars(p)
    assert load_bars(p, sort=True).index.is_monotonic_increasing
    assert len(load_bars(p, validate="keep")) == 2
    d = write(tmp_path, "time,open,high,low,close,volume\n2024-01-02T14:30:00Z,1,2,0.5,1.5,10\n2024-01-02T14:30:00Z,1,2,0.5,1.5,10\n", "d.csv")
    with pytest.raises(ValueError, match="duplicate"):
        load_bars(d)


def test_missing_column_and_mixed_offsets_raise(tmp_path):
    with pytest.raises(ValueError, match="volume"):
        load_bars(write(tmp_path, "time,open,high,low,close\n2024-01-02T14:30:00Z,1,2,0.5,1.5\n"))
    p = write(tmp_path, "time,open,high,low,close,volume\n2024-01-02T14:30:00Z,1,2,0.5,1.5,1\n2024-01-02 14:31:00,1,2,0.5,1.5,1\n", "m.csv")
    with pytest.raises(ValueError):
        load_bars(p, tz_hint="UTC")


def test_contract_column_and_synthetic_marker(tmp_path):
    p = write(tmp_path, "time,open,high,low,close,volume,Symbol\n2024-01-02T14:30:00Z,1,2,0.5,1.5,1,MESH4\n", "SYNTHETIC_a.csv")
    df = load_bars(p)
    assert df["contract"].iloc[0] == "MESH4" and df.attrs["SYNTHETIC"]
