import json
import importlib.util, pathlib
import pandas as pd
import pytest
from tradelab.backtest.guards import check_contract_labels
from tradelab.data.loader import resample_bars
from tradelab.data.synthetic import make_synthetic_bars

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("rb2", ROOT / "scripts" / "run_backtest.py")
rb = importlib.util.module_from_spec(spec); spec.loader.exec_module(rb)


@pytest.fixture(scope="module")
def csv(tmp_path_factory):
    p = tmp_path_factory.mktemp("d") / "SYNTHETIC_b.csv"
    b = resample_bars(make_synthetic_bars(5, start="2024-01-02", days=60), "5min")
    b.to_csv(p, index_label="ts_open")
    return p


def test_train_run_never_loads_later_segments(csv, tmp_path):
    rb.main(["--bars", str(csv), "--strategy", "sweep_reversal", "--out", str(tmp_path)])
    m = json.loads((tmp_path / "run_meta.json").read_text())
    assert m["segment"] == "train" and m["data_is_synthetic"] is True
    assert m["n_bars_used"] < m["n_bars_loaded"] * 0.7
    assert m["n_trading_days_in_segment"] > 0 and m["variant_id"].endswith("levels=both,target_r=2.0")
    t = pd.read_csv(tmp_path / "trades.csv", parse_dates=["exit_ts"])
    assert (t.exit_ts < pd.Timestamp(m["segment_bounds_utc"][1])).all()


def test_synthetic_refused_for_validation_without_flag_and_for_test(csv, tmp_path):
    with pytest.raises(SystemExit):
        rb.main(["--bars", str(csv), "--strategy", "orb", "--segment", "validation", "--out", str(tmp_path / "v")])
    log = tmp_path / "log.jsonl"
    with pytest.raises(SystemExit):
        rb.main(["--bars", str(csv), "--strategy", "orb", "--segment", "test", "--software-test",
                 "--test-log", str(log), "--out", str(tmp_path / "t")])
    assert not log.exists()    # refused before the guard: no look consumed
    rb.main(["--bars", str(csv), "--strategy", "orb", "--segment", "validation", "--software-test",
             "--out", str(tmp_path / "v2")])


def test_synthetic_marker_survives_without_filename(csv, tmp_path):
    p = tmp_path / "renamed.csv"
    p.write_text(pd.read_csv(csv).assign(SYNTHETIC=1).to_csv(index=False))
    with pytest.raises(SystemExit):
        rb.main(["--bars", str(p), "--strategy", "orb", "--segment", "validation", "--out", str(tmp_path / "o")])


def test_unlogged_variant_refused(csv, tmp_path):
    with pytest.raises(ValueError):
        rb.main(["--bars", str(csv), "--strategy", "sweep_reversal", "--set", "target_r=2", "--out", str(tmp_path)])


def test_contract_guard_for_real_data():
    idx = pd.date_range("2024-01-02 15:00", periods=3, freq="5min", tz="UTC")
    mk = lambda c: pd.DataFrame({"contract": c}, index=idx)
    check_contract_labels(mk(["MESH4"] * 3), synthetic=False)
    for bad in (["MES"] * 3, [None, "MESH4", "MESH4"]):
        with pytest.raises(ValueError):
            check_contract_labels(mk(bad), synthetic=False)
    with pytest.raises(ValueError):
        check_contract_labels(pd.DataFrame({"x": 1}, index=idx), synthetic=False)
    long = pd.DataFrame({"contract": "MESH4"}, index=pd.DatetimeIndex(["2024-01-02", "2024-09-02"], tz="UTC"))
    with pytest.raises(ValueError, match="spans"):
        check_contract_labels(long, synthetic=False)
