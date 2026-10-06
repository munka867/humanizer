from tradelab.validation.verdict import classify_development, classify_final, REJECTED, INCONCLUSIVE, SUITABLE

GOOD = dict(n_train=300, n_val=120, n_val_days=60, ev_train_1x=2.0, ev_val_1x=3.0, ev_val_2x=1.0,
            val_ci_lo_at_pass_mult=0.5, val_ci_hi_1x=6.0, p_bonferroni_val=0.05, dsr=0.95,
            positive_quarter_fraction=0.75, max_period_share=0.4, baseline_percentile=0.97)


def test_suitable():
    assert classify_development(GOOD)[0] == SUITABLE


def test_each_condition_matters():
    for k, bad in [("n_val", 50), ("val_ci_lo_at_pass_mult", -0.1), ("dsr", 0.5), ("max_period_share", 0.9),
                   ("baseline_percentile", 0.5), ("p_bonferroni_val", 0.5), ("ev_val_2x", -1.0),
                   ("positive_quarter_fraction", 0.2), ("dsr", float("nan")), ("baseline_percentile", None)]:
        v, why = classify_development({**GOOD, k: bad})
        assert v == INCONCLUSIVE and why, k


def test_reject_needs_enough_trades():
    assert classify_development({**GOOD, "ev_val_1x": -1.0})[0] == REJECTED
    assert classify_development({**GOOD, "ev_val_1x": -1.0, "n_val": 10})[0] != REJECTED
    assert classify_development({**GOOD, "val_ci_hi_1x": -0.5})[0] == REJECTED


def test_final():
    assert classify_final(1, 0.5, 150, 0.01)[0] == SUITABLE
    assert classify_final(-1, -2, 150, 0.9)[0] == REJECTED
    assert classify_final(1, 0.5, 150, 0.2)[0] == INCONCLUSIVE
    assert classify_final(1, -0.5, 150, 0.01)[0] == INCONCLUSIVE
    assert classify_final(1, 0.5, 20, 0.01)[0] == INCONCLUSIVE
