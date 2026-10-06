"""Pre-registered decision thresholds (fixed BEFORE seeing data; see docs/VALIDATION_PLAN.md).
Changing any value after results are visible must be logged in docs/EXPERIMENT_LOG.md and the
verdict reported as 'thresholds changed post hoc'.

classify_development() takes plain numbers computed elsewhere (stats.metrics, cost_stress,
baseline comparison) so the rule is auditable and unit-tested."""
from __future__ import annotations

from dataclasses import dataclass

REJECTED, INCONCLUSIVE, SUITABLE = "REJECTED", "INCONCLUSIVE", "SUITABLE_FOR_FURTHER_PAPER_TESTING"


@dataclass(frozen=True)
class Thresholds:
    min_trades_each_segment_for_reject: int = 30     # below this a negative point estimate is just noise
    min_trades_train_plus_val: int = 200
    min_trades_validation: int = 100
    min_validation_days: int = 40
    ci_alpha_two_sided: float = 0.05                 # lower bound of 95% two-sided CI == 2.5% one-sided
    cost_mult_for_pass: float = 1.5
    p_bonferroni_max: float = 0.10
    dsr_min: float = 0.90
    min_positive_quarter_fraction: float = 0.60      # among quarters with >= min_trades_per_period trades
    min_trades_per_period: int = 10
    max_single_period_share: float = 0.50            # no month may supply > 50% of total net P&L
    baseline_percentile_min: float = 0.95            # strategy EV must beat this percentile of random baseline
    # final test (single frozen hypothesis, evaluated once)
    final_min_trades: int = 100
    final_p_one_sided_max: float = 0.05


def classify_development(e: dict, th: Thresholds = Thresholds()) -> tuple[str, list[str]]:
    """Required keys in e (all measured with the stated cost multiple; None/nan = unavailable -> fails):
      n_train, n_val, n_val_days, ev_train_1x, ev_val_1x, ev_val_2x, val_ci_lo_at_pass_mult,
      val_ci_hi_1x, p_bonferroni_val, dsr, positive_quarter_fraction, max_period_share, baseline_percentile
    Returns (verdict, reasons)."""
    g = lambda k: e.get(k)
    ok = lambda v: v is not None and v == v
    reasons: list[str] = []
    # ---- rejection: enough data AND clearly not positive
    if ok(g("ev_train_1x")) and g("n_train") >= th.min_trades_each_segment_for_reject and g("ev_train_1x") <= 0:
        reasons.append("train net EV/trade <= 0 at 1.0x costs")
    if ok(g("ev_val_1x")) and g("n_val") >= th.min_trades_each_segment_for_reject and g("ev_val_1x") <= 0:
        reasons.append("validation net EV/trade <= 0 at 1.0x costs")
    if ok(g("val_ci_hi_1x")) and g("val_ci_hi_1x") < 0:
        reasons.append("upper CI bound of validation net EV/trade < 0 (evidence of negative EV)")
    if reasons:
        return REJECTED, reasons
    # ---- suitable: ALL conditions
    checks = {
        f"n_train+n_val >= {th.min_trades_train_plus_val}": (g("n_train") or 0) + (g("n_val") or 0) >= th.min_trades_train_plus_val,
        f"n_val >= {th.min_trades_validation}": (g("n_val") or 0) >= th.min_trades_validation,
        f"n_val_days >= {th.min_validation_days}": (g("n_val_days") or 0) >= th.min_validation_days,
        f"validation CI lower bound > 0 at {th.cost_mult_for_pass}x costs": ok(g("val_ci_lo_at_pass_mult")) and g("val_ci_lo_at_pass_mult") > 0,
        "validation EV > 0 at 2.0x costs": ok(g("ev_val_2x")) and g("ev_val_2x") > 0,
        f"Bonferroni p <= {th.p_bonferroni_max}": ok(g("p_bonferroni_val")) and g("p_bonferroni_val") <= th.p_bonferroni_max,
        f"DSR >= {th.dsr_min}": ok(g("dsr")) and g("dsr") >= th.dsr_min,
        f"positive quarters >= {th.min_positive_quarter_fraction:.0%}": ok(g("positive_quarter_fraction")) and g("positive_quarter_fraction") >= th.min_positive_quarter_fraction,
        f"max single-period P&L share <= {th.max_single_period_share:.0%}": ok(g("max_period_share")) and g("max_period_share") <= th.max_single_period_share,
        f"beats random baseline at p{th.baseline_percentile_min:.0%}": ok(g("baseline_percentile")) and g("baseline_percentile") >= th.baseline_percentile_min,
    }
    failed = [k for k, v in checks.items() if not v]
    if not failed:
        return SUITABLE, ["all pre-registered conditions met (paper testing only; not evidence of live profitability)"]
    return INCONCLUSIVE, ["not met: " + k for k in failed]


def classify_final(ev_1x: float, ev_15x: float, n: int, p_one_sided: float, th: Thresholds = Thresholds()) -> tuple[str, list[str]]:
    """Single frozen-hypothesis test. Reject if EV<=0 at 1x (n adequate); pass only if EV>0 at
    1.5x AND p_one_sided <= alpha AND n adequate; otherwise inconclusive."""
    if n < th.final_min_trades:
        return INCONCLUSIVE, [f"n={n} < {th.final_min_trades}"]
    if ev_1x <= 0:
        return REJECTED, ["final-test net EV/trade <= 0 at 1.0x costs"]
    if ev_15x > 0 and p_one_sided <= th.final_p_one_sided_max:
        return SUITABLE, ["final test passed (frozen config, single look)"]
    return INCONCLUSIVE, ["EV>0 at 1x but not significant / not robust to 1.5x costs"]
