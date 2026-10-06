import pandas as pd
import pytest
from tradelab.contracts import Instrument
from tradelab.data import calendar as cal
from tradelab.data.loader import load_bars
from tradelab.data.quality import drop_roll_sessions, quality_report, render_markdown, roll_flags
from tradelab.data.synthetic import Defects, make_synthetic_bars, write_fixtures

MES = Instrument("MES", 5.0, 0.25)
SPRING = dict(start="2024-03-06", days=8)   # DST starts Sun 2024-03-10
FALL = dict(start="2024-10-31", days=6)     # DST ends Sun 2024-11-03


def codes(rep):
    return {i["code"] for i in rep["issues"]}


def report(df, freq="1min"):
    return quality_report(df, freq, MES)


@pytest.mark.parametrize("kw", [SPRING, FALL])
def test_clean_synthetic_is_clean_across_dst(kw):
    df = make_synthetic_bars(1, **kw)
    rep = report(df)
    assert rep["issues"] == [] and rep["ok"] and rep["synthetic"]
    assert rep["dst"]["transition_dates"] and rep["rth"]["partial_days"] == []


def test_dst_week_open_utc_times():
    spring = make_synthetic_bars(1, **SPRING)
    fall = make_synthetic_bars(1, **FALL)
    first = lambda d, day: d[d.index.tz_convert(cal.NY).date == pd.Timestamp(day).date()].index[0]
    assert first(spring, "2024-03-10") == pd.Timestamp("2024-03-10 22:00", tz="UTC")  # 18:00 EDT
    assert first(fall, "2024-11-03") == pd.Timestamp("2024-11-03 23:00", tz="UTC")    # 18:00 EST


def test_rth_is_390_bars_on_dst_days():
    for kw, days in [(SPRING, ["2024-03-08", "2024-03-11"]), (FALL, ["2024-11-01", "2024-11-04"])]:
        df = make_synthetic_bars(1, **kw)
        per = pd.Series(1, index=cal.et_date(df.index[cal.rth_mask(df.index)])).groupby(level=0).sum()
        for d in days:
            assert per[pd.Timestamp(d)] == 390


def test_session_date_and_sunday_open():
    t = pd.DatetimeIndex(["2024-03-10 22:00", "2024-03-11 20:59"], tz="UTC")  # Sun 18:00 EDT, Mon 16:59 EDT
    assert list(cal.session_date(t)) == [pd.Timestamp("2024-03-11")] * 2


def test_dst_bug_detected():
    bad = make_synthetic_bars(1, **SPRING, defects=Defects(dst_bug=True))
    rep = report(bad)
    assert "closed_hours_bars" in codes(rep) and "late_week_open" in codes(rep)


def test_each_defect_detected():
    d = Defects(duplicates=5, gaps=3, bad_ohlc=4, zero_volume=6, spikes=2, stale_run=40, out_of_order=2)
    rep = report(make_synthetic_bars(7, **SPRING, defects=d))
    assert rep["duplicates"] == 5 and rep["non_monotonic_steps"] == 2
    assert rep["ohlc"]["inconsistent"] == 4 and rep["ohlc"]["zero_volume"] >= 6
    assert rep["gaps"]["n"] >= 3 and rep["spikes"]["n"] >= 2
    assert rep["stale"]["longest_run"] >= 40 and not rep["ok"]
    assert {"duplicates", "non_monotonic", "bad_ohlc", "zero_volume", "gaps", "spikes", "stale"} <= codes(rep)


def test_off_tick_nonpositive_nan_freq_mismatch():
    df = make_synthetic_bars(1, **SPRING)
    df.iloc[5, 3] += 0.1
    df.iloc[8, 3] = -1.0
    df.iloc[9, 0] = float("nan")
    rep = report(df)
    assert {"off_tick", "nonpositive_price", "nan"} <= codes(rep)
    assert "freq_mismatch" in codes(report(make_synthetic_bars(1, **SPRING), "5min"))


def test_gap_not_flagged_in_nominal_closed_hours_but_flagged_in_session():
    rep = report(make_synthetic_bars(1, **SPRING))  # has weekend + daily breaks, no gaps reported
    assert rep["gaps"]["n"] == 0
    df = make_synthetic_bars(1, **SPRING)
    t = pd.Timestamp("2024-03-07 15:00", tz="UTC")
    df = df.drop(index=pd.date_range(t, periods=30, freq="1min"))
    g = report(df)["gaps"]
    assert g["n"] == 1 and g["missing_bars"] == 30


def test_holiday_candidate_flagged_not_assumed():
    # Good Friday 2024-03-29, Thanksgiving 2024-11-28, MLK 2024-01-15
    for d in ["2024-03-29", "2024-11-28", "2024-01-15", "2024-12-25"]:
        assert cal.is_holiday_candidate(pd.Timestamp(d).date())
    assert not cal.is_holiday_candidate(pd.Timestamp("2024-03-13").date())
    df = make_synthetic_bars(1, start="2024-03-26", days=7)
    day = df.index.tz_convert(cal.NY).date == pd.Timestamp("2024-03-29").date()
    rep = report(df[~day])
    assert rep["gaps"]["n_holiday_candidate"] >= 1 and rep["rth"]["n_missing_weekdays"] == 1
    assert rep["rth"]["missing_weekdays"][0]["holiday_candidate"]


ROLL = Defects(roll_jump_points=55.0, roll_after="2024-03-07")


def test_roll_detected_by_contract_column_and_session_dropped():
    df = make_synthetic_bars(1, **SPRING, defects=ROLL)
    rep = report(df)
    assert rep["rolls"]["n"] == 1 and rep["rolls"]["method"] == "contract_column"
    assert rep["rolls"]["events"][0]["points"] == pytest.approx(55.0, abs=3)
    fl = roll_flags(df, "1min")
    assert fl.sum() == 1
    kept = drop_roll_sessions(df, fl)
    kept = drop_roll_sessions(df, fl)
    sd = pd.Series(cal.session_date(kept.index), index=kept.index)
    assert len(kept) < len(df)
    assert kept.groupby(sd.to_numpy())["contract"].nunique().max() == 1
    assert kept.groupby(sd.to_numpy())["close"].apply(lambda x: x.diff().abs().max()).max() < 20


def test_roll_detected_by_heuristic_without_contract():
    df = make_synthetic_bars(1, **SPRING, defects=Defects(roll_jump_points=55.0, roll_after="2024-03-07", drop_contract_col=True))
    rep = report(df)
    assert rep["rolls"]["method"].startswith("heuristic") and rep["rolls"]["n"] == 1


def test_big_gap_far_from_expiry_not_called_a_roll():
    df = make_synthetic_bars(1, start="2024-04-22", days=8, defects=Defects(roll_jump_points=55.0, roll_after="2024-04-23", drop_contract_col=True))
    assert report(df)["rolls"]["n"] == 0


def test_fixtures_deterministic_labelled_and_loadable(tmp_path):
    a = write_fixtures(tmp_path / "a", seed=5)
    b = write_fixtures(tmp_path / "b", seed=5)
    for x, y in zip(a, b):
        assert x.name.startswith("SYNTHETIC_") and x.read_bytes() == y.read_bytes()
    clean = load_bars(a[0])
    assert clean.attrs["SYNTHETIC"] and report(clean)["ok"]
    assert not report(load_bars(a[2], validate="keep"))["ok"]
    assert "SYNTHETIC DATA" in render_markdown(report(clean))
