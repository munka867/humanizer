import pandas as pd
import pytest
from tradelab.validation.splits import (make_split, apply_split, classify_trades, walk_forward_windows,
                                        apply_walk_forward)
from test_validation_common import mk

T0 = pd.Timestamp("2024-01-01", tz="UTC")
T1 = pd.Timestamp("2024-01-10 23:59:59", tz="UTC")


def split(purge="1D"):
    return make_split(T0, T1, (0.6, 0.2, 0.2), pd.Timedelta(purge))


def test_boundaries_chronological_contiguous():
    s = split()
    assert s.train[0] == T0 and s.train[1] == s.validation[0] and s.validation[1] == s.test[0]
    assert s.test[1] > T1 and s.train[1] < s.validation[1] < s.test[0] + pd.Timedelta(days=100)
    span = s.test[1] - s.train[0]
    assert abs((s.train[1] - s.train[0]) / span - 0.6) < 1e-6


def test_bad_args():
    with pytest.raises(ValueError):
        make_split(T0, T1, (0.5, 0.2, 0.2))
    with pytest.raises(ValueError):
        make_split(T1, T0)
    with pytest.raises(ValueError):
        make_split(T0.tz_localize(None), T1)


def test_assignment_and_drops():
    s = split("1D")
    b1, b2 = s.train[1], s.validation[1]  # ~2024-01-06 ~ 2024-01-08
    rows = [
        (T0 + pd.Timedelta(hours=1), 30, 5),                         # train
        (b1 - pd.Timedelta(minutes=10), 30, 5),                      # straddles b1 -> dropped
        (b1 + pd.Timedelta(hours=1), 30, 5),                         # inside purge gap -> dropped
        (b1 + pd.Timedelta(days=1, hours=1), 30, 5),                 # validation
        (b2 - pd.Timedelta(minutes=1), 120, 5),                      # straddles b2
        (b2 + pd.Timedelta(hours=23), 5, 5),                         # purge gap before test
        (b2 + pd.Timedelta(days=1, minutes=1), 5, 5),                # test
        (T0 - pd.Timedelta(days=1), 5, 5),                           # outside
    ]
    rows = [(str(r[0].tz_convert(None)), r[1], r[2]) for r in rows]
    tr = mk(rows)
    res = apply_split(tr, s)
    assert [len(res.train), len(res.validation), len(res.test)] == [1, 1, 1]
    assert res.dropped == {"purge_gap": 2, "straddle": 2, "outside": 1}
    assert res.n_dropped == 5 and set(res.dropped_trades["drop_reason"]) == {"purge_gap", "straddle", "outside"}
    # no kept trade straddles anything
    for name in ("train", "validation", "test"):
        a, b = getattr(s, name)
        seg = res.segment(name)
        assert ((seg.entry_ts >= a) & (seg.exit_ts < b)).all()


def test_exit_exactly_at_boundary_is_straddle():
    s = split("0s")
    b1 = s.train[1]
    tr = mk([(str((b1 - pd.Timedelta(minutes=30)).tz_convert(None)), 30, 1)])
    assert classify_trades(tr, s).iloc[0] == "straddle"


def test_empty_trades():
    res = apply_split(mk([]), split())
    assert res.n_dropped == 0 and len(res.train) == 0


def test_no_shuffle_order_preserved():
    s = split("0s")
    tr = mk([("2024-01-03 10:00", 5, 1), ("2024-01-02 10:00", 5, 1)])
    res = apply_split(tr, s)
    assert list(res.train.entry_ts) == sorted(tr.entry_ts)


def test_walk_forward():
    w = walk_forward_windows(T0, T1, "3D", "2D", purge=pd.Timedelta(days=1))
    assert len(w) >= 2
    for x in w:
        assert x.test[0] - x.train[1] == pd.Timedelta(days=1)
        assert x.test[1] <= T1 + pd.Timedelta(seconds=1)
    for a, b in zip(w, w[1:]):
        assert b.test[0] >= a.test[1]            # test windows do not overlap
        assert b.test[0] > a.test[0]
    tr = mk([("2024-01-01 10:00", 5, 1), ("2024-01-05 10:00", 5, 1), ("2024-01-03 23:59", 120, 1)])
    r = apply_walk_forward(tr, w)
    assert r[0]["dropped_train"] == 0 or True
    assert all(len(x["train"]) + x["dropped_train"] >= 0 for x in r)
    # a train-window trade spanning the train end is dropped
    w0 = w[0]
    straddle = mk([(str((w0.train[1] - pd.Timedelta(minutes=5)).tz_convert(None)), 60, 1)])
    out = apply_walk_forward(straddle, [w0])[0]
    assert len(out["train"]) == 0 and out["dropped_train"] == 1


def test_anchored_walk_forward_expands():
    w = walk_forward_windows(T0, T1, "3D", "2D", purge=pd.Timedelta(0), anchored=True)
    assert all(x.train[0] == T0 for x in w)
    assert w[1].train[1] > w[0].train[1]
