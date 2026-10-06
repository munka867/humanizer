"""Trade-table statistics. numpy/pandas only (no scipy): the t and normal CDFs are
implemented here (math.erf; regularised incomplete beta by continued fraction).

Conventions
-----------
* "Day" = America/New_York calendar date of entry_ts (override with day_tz). Futures
  sessions open 18:00 NY the prior evening, so a strategy that holds across 18:00 would be
  split oddly; for intraday-only strategies this is a reasonable proxy.
* Equity curve / drawdown are CLOSED-TRADE equity, ordered by exit_ts (no intratrade
  mark-to-market), so true drawdown is >= what is reported here.
* Losing streak: consecutive trades with pnl_net < 0 in exit order; a scratch (==0) ends it.
* All p-values are from approximations stated in the function docstrings.
"""
from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np
import pandas as pd

_ND = NormalDist()
EULER_GAMMA = 0.5772156649015329


# ----------------------------------------------------------------- distributions
def _betacf(a: float, b: float, x: float) -> float:
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d; d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c; c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d; d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c; c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    return h


def betainc_reg(a: float, b: float, x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1) / (a + b + 2):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1 - x) / b


def t_two_sided_p(t: float, df: float) -> float:
    if not np.isfinite(t) or df <= 0:
        return float("nan")
    return betainc_reg(df / 2.0, 0.5, df / (df + t * t))


def norm_cdf(x: float) -> float:
    return _ND.cdf(x)


def norm_ppf(p: float) -> float:
    return _ND.inv_cdf(p)


# ----------------------------------------------------------------- helpers
def day_codes(trades: pd.DataFrame, day_tz: str = "America/New_York") -> np.ndarray:
    """Integer day id per trade (chronological order of NY entry dates)."""
    if len(trades) == 0:
        return np.array([], dtype=int)
    d = trades["entry_ts"].dt.tz_convert(day_tz).dt.strftime("%Y-%m-%d")
    return pd.factorize(d, sort=True)[0]


def _pnl(trades: pd.DataFrame, col: str = "pnl_net") -> np.ndarray:
    return trades[col].to_numpy(dtype=float)


def _r_values(trades: pd.DataFrame) -> np.ndarray:
    if "risk_usd" in trades.columns:
        risk = trades["risk_usd"].to_numpy(dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(risk > 0, _pnl(trades) / risk, np.nan)
    return trades["r_multiple"].to_numpy(dtype=float)


def longest_losing_streak(pnl: np.ndarray) -> int:
    best = cur = 0
    for v in pnl:
        cur = cur + 1 if v < 0 else 0
        best = max(best, cur)
    return best


def max_drawdown(pnl: np.ndarray, equity_start: float) -> tuple[float, float]:
    """(max drawdown USD, max drawdown as fraction of running peak equity). Peak includes
    the starting equity. Fraction is nan if the peak was <= 0 at the worst point."""
    if len(pnl) == 0:
        return 0.0, 0.0
    eq = equity_start + np.cumsum(pnl)
    peak = np.maximum.accumulate(np.concatenate([[equity_start], eq]))[1:]
    dd = peak - eq
    i = int(np.argmax(dd))
    pct = dd[i] / peak[i] if peak[i] > 0 else float("nan")
    return float(dd[i]), float(pct)


def profit_factor(pnl: np.ndarray) -> float:
    w, l = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    if l == 0:
        return float("inf") if w > 0 else float("nan")
    return float(w / l)


def exposure_fraction(trades: pd.DataFrame, n_session_days: int | None = None,
                      session_hours_per_day: float = 6.5) -> float:
    """Union of [entry_ts, exit_ts] holding time / (n_session_days * session hours).
    Default n_session_days = weekdays between first and last entry date (holidays not
    removed, so exposure is slightly understated). Overlapping trades are not double counted."""
    if len(trades) == 0:
        return 0.0
    iv = sorted(zip(trades["entry_ts"], trades["exit_ts"]))
    total, cs, ce = 0.0, iv[0][0], iv[0][1]
    for s, e in iv[1:]:
        if s <= ce:
            ce = max(ce, e)
        else:
            total += (ce - cs).total_seconds(); cs, ce = s, e
    total += (ce - cs).total_seconds()
    if n_session_days is None:
        d0 = trades["entry_ts"].min().tz_convert("America/New_York").date()
        d1 = trades["exit_ts"].max().tz_convert("America/New_York").date()
        n_session_days = max(1, int(np.busday_count(d0, d1 + pd.Timedelta(days=1))))
    return float(total / (n_session_days * session_hours_per_day * 3600.0))


# ----------------------------------------------------------------- inference
def mean_tstat(x: np.ndarray) -> dict:
    """One-sample t-stat of the mean; two-sided p from Student-t with n-1 d.f.
    Assumes independent observations -- trades within a day are not independent, so
    prefer the day-level version (mean_tstat on daily P&L) or the day bootstrap."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < 2:
        return {"n": n, "mean": float(x.mean()) if n else float("nan"), "t": float("nan"), "p_two_sided": float("nan"), "p_one_sided": float("nan")}
    sd = x.std(ddof=1)
    if sd == 0:
        t = float("inf") if x.mean() > 0 else (float("-inf") if x.mean() < 0 else float("nan"))
        p2 = 0.0 if x.mean() != 0 else float("nan")
    else:
        t = float(x.mean() / (sd / math.sqrt(n)))
        p2 = t_two_sided_p(t, n - 1)
    if not np.isfinite(p2):
        p1 = float("nan")
    else:
        p1 = p2 / 2 if t > 0 else 1 - p2 / 2  # P(T >= t) under H0 for H1: mean > 0
    return {"n": n, "mean": float(x.mean()), "t": t, "p_two_sided": p2, "p_one_sided": p1}


def daily_pnl(trades: pd.DataFrame, col: str = "pnl_net", day_tz: str = "America/New_York") -> pd.Series:
    if len(trades) == 0:
        return pd.Series(dtype=float)
    d = trades["entry_ts"].dt.tz_convert(day_tz).dt.strftime("%Y-%m-%d")
    return trades.groupby(d.to_numpy())[col].sum()


def bootstrap_mean_ci_by_day(trades: pd.DataFrame, n_boot: int = 5000, alpha: float = 0.05, seed: int = 0,
                             col: str = "pnl_net", day_tz: str = "America/New_York") -> dict:
    """Percentile CI of the mean per-trade value under a block bootstrap by DAY: resample
    whole days with replacement, statistic = sum(pnl)/sum(n_trades) of the resampled days
    (ratio estimator, so days with more trades weigh more). Keeps within-day dependence.
    Caveats: percentile intervals are anti-conservative for small numbers of days (<~30)
    and ignore dependence across days (e.g. volatility regimes)."""
    n = len(trades)
    if n == 0:
        return {"lo": float("nan"), "hi": float("nan"), "mean": float("nan"), "n_days": 0, "alpha": alpha, "n_boot": n_boot}
    codes = day_codes(trades, day_tz)
    nd = codes.max() + 1
    S = np.bincount(codes, weights=trades[col].to_numpy(dtype=float), minlength=nd)
    N = np.bincount(codes, minlength=nd).astype(float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, nd, size=(n_boot, nd))
    est = S[idx].sum(1) / N[idx].sum(1)
    lo, hi = np.quantile(est, [alpha / 2, 1 - alpha / 2])
    return {"lo": float(lo), "hi": float(hi), "mean": float(S.sum() / N.sum()), "n_days": int(nd), "alpha": alpha,
            "n_boot": n_boot, "p_mean_le_0": float((est <= 0).mean())}


def sharpe_per_day(daily: np.ndarray) -> dict:
    """Mean/std of daily net P&L over days WITH trades (zero-trade days excluded, which
    inflates it relative to a calendar Sharpe). `annualised` = x sqrt(252), a convention only."""
    daily = np.asarray(daily, dtype=float)
    if len(daily) < 2 or daily.std(ddof=1) == 0:
        return {"per_day": float("nan"), "annualised": float("nan"), "n_days": len(daily)}
    s = float(daily.mean() / daily.std(ddof=1))
    return {"per_day": s, "annualised": s * math.sqrt(252), "n_days": len(daily)}


# ----------------------------------------------------------------- multiple testing
def bonferroni(p: float, n_variants_tried: int) -> float:
    """Bonferroni: p_adj = min(1, p * N). Valid under any dependence; conservative when
    variants are correlated (as parameter neighbours are). N must include EVERY variant
    evaluated (including ones discarded), cf. configs/search_budget.yaml."""
    return float(min(1.0, p * max(1, n_variants_tried))) if np.isfinite(p) else float("nan")


def expected_max_sharpe(n_trials: int, var_sr: float) -> float:
    """SR0 = sqrt(V) * [ (1-g) * Phi^-1(1 - 1/N) + g * Phi^-1(1 - 1/(N e)) ],  g = Euler-Mascheroni.
    Expected maximum of N independent Sharpe estimates with cross-trial variance V under a
    true Sharpe of 0 (the 'false strategy theorem' approximation in Bailey & Lopez de Prado,
    'The Deflated Sharpe Ratio', 2014)."""
    if n_trials <= 1:
        return 0.0
    return math.sqrt(var_sr) * ((1 - EULER_GAMMA) * norm_ppf(1 - 1 / n_trials)
                                + EULER_GAMMA * norm_ppf(1 - 1 / (n_trials * math.e)))


def deflated_sharpe(returns: np.ndarray, n_variants_tried: int, var_sr_trials: float | None = None) -> dict:
    """Deflated-Sharpe-style haircut on a per-period return series (use DAILY net P&L).

        SR_hat = mean/std (per period, NOT annualised), T = number of periods
        DSR    = Phi( (SR_hat - SR0) * sqrt(T-1) / sqrt(1 - g3*SR_hat + (g4-1)/4 * SR_hat^2) )
        SR0    = expected_max_sharpe(N, V)
    g3 = sample skewness, g4 = sample (non-excess) kurtosis; DSR is the probability that the
    true Sharpe exceeds the max-of-N benchmark SR0 (Bailey & Lopez de Prado 2014).

    HONEST APPROXIMATIONS: (1) V (variance of Sharpe estimates across the N variants tried)
    is normally computed from all trial results; we do not have them here, so by default
    V = 1/(T-1), the sampling variance of one SR estimate when the true SR is ~0. This
    assumes all N trials are exchangeable noise; if the variants truly differ, V is larger
    and SR0 (the haircut) is understated. Pass var_sr_trials to override with the empirical
    variance. (2) N is treated as independent trials; correlated variants make the effective
    N smaller, so the haircut is conservative on that axis. (3) Daily P&L is treated as iid.
    (4) Moment estimates from short samples are noisy. Treat DSR as a rough screen only."""
    r = np.asarray(returns, dtype=float)
    T = len(r)
    if T < 3 or r.std(ddof=1) == 0:
        return {"T": T, "sr_hat": float("nan"), "sr0": float("nan"), "dsr": float("nan"), "n_variants": n_variants_tried}
    sr = float(r.mean() / r.std(ddof=1))
    z = (r - r.mean()) / r.std(ddof=0)
    g3, g4 = float((z ** 3).mean()), float((z ** 4).mean())
    var = var_sr_trials if var_sr_trials is not None else 1.0 / (T - 1)
    sr0 = expected_max_sharpe(n_variants_tried, var)
    denom2 = 1 - g3 * sr + (g4 - 1) / 4 * sr * sr
    if denom2 <= 0:
        return {"T": T, "sr_hat": sr, "sr0": sr0, "dsr": float("nan"), "n_variants": n_variants_tried}
    dsr = norm_cdf((sr - sr0) * math.sqrt(T - 1) / math.sqrt(denom2))
    return {"T": T, "sr_hat": sr, "sr0": sr0, "sr_haircut": sr - sr0, "skew": g3, "kurt": g4, "dsr": float(dsr),
            "n_variants": n_variants_tried, "var_sr_trials": var}


# ----------------------------------------------------------------- periods
def period_breakdown(trades: pd.DataFrame, freq: str = "month", tz: str = "America/New_York") -> pd.DataFrame:
    """Per calendar period (by NY entry time): n, net sum, mean, hit rate, share of total P&L."""
    cols = ["period", "n", "pnl_net_sum", "pnl_net_mean", "hit_rate", "share_of_total"]
    if len(trades) == 0:
        return pd.DataFrame(columns=cols)
    loc = trades["entry_ts"].dt.tz_convert(tz)
    key = {"month": loc.dt.strftime("%Y-%m"), "year": loc.dt.strftime("%Y"),
           "quarter": loc.dt.year.astype(str) + "Q" + loc.dt.quarter.astype(str)}[freq]
    g = trades.assign(_k=key.to_numpy()).groupby("_k")["pnl_net"]
    out = pd.DataFrame({"n": g.size(), "pnl_net_sum": g.sum(), "pnl_net_mean": g.mean(),
                        "hit_rate": g.apply(lambda s: (s > 0).mean())})
    tot = out["pnl_net_sum"].sum()
    out["share_of_total"] = out["pnl_net_sum"] / tot if tot != 0 else np.nan
    out.index.name = "period"
    return out.reset_index()[cols]


# ----------------------------------------------------------------- headline
def metrics(trades: pd.DataFrame, equity_start: float, n_variants_tried: int = 1, n_boot: int = 5000,
            seed: int = 0, n_session_days: int | None = None, session_hours_per_day: float = 6.5,
            include_periods: bool = True) -> dict:
    n = len(trades)
    out: dict = {"n_trades": n, "equity_start": equity_start, "n_variants_tried": n_variants_tried}
    if n == 0:
        out.update(ev_usd=float("nan"), ev_r=float("nan"), note="no trades")
        return out
    t = trades.sort_values(["exit_ts", "entry_ts"], kind="stable")
    pnl = _pnl(t)
    r = _r_values(t)
    dd_usd, dd_pct = max_drawdown(pnl, equity_start)
    daily = daily_pnl(t).to_numpy()
    tt = mean_tstat(pnl)
    td = mean_tstat(daily)
    ci = bootstrap_mean_ci_by_day(t, n_boot=n_boot, seed=seed)
    ci_r = (bootstrap_mean_ci_by_day(t.assign(r_multiple=r), n_boot=n_boot, seed=seed, col="r_multiple")
            if np.isfinite(r).all() else None)  # STRATEGY_SPEC s2 criterion 1 is stated in R
    out.update(
        ev_usd=float(pnl.mean()), ev_r=float(np.nanmean(r)) if np.isfinite(r).any() else float("nan"),
        std_usd=float(pnl.std(ddof=1)) if n > 1 else float("nan"),
        std_r=float(np.nanstd(r, ddof=1)) if np.isfinite(r).sum() > 1 else float("nan"),
        total_pnl_net=float(pnl.sum()), hit_rate=float((pnl > 0).mean()), profit_factor=profit_factor(pnl),
        max_dd_usd=dd_usd, max_dd_pct=dd_pct, longest_losing_streak=longest_losing_streak(pnl),
        exposure=exposure_fraction(t, n_session_days, session_hours_per_day),
        n_days=len(daily), t_trade=tt["t"], p_trade_two_sided=tt["p_two_sided"],
        t_day=td["t"], p_day_two_sided=td["p_two_sided"], p_day_one_sided=td["p_one_sided"],
        boot_ci_ev_usd=(ci["lo"], ci["hi"]), boot_ci_ev_r=(ci_r["lo"], ci_r["hi"]) if ci_r else None, boot_alpha=ci["alpha"], boot_p_ev_le_0=ci.get("p_mean_le_0"),
        sharpe_per_day=sharpe_per_day(daily),
        p_bonferroni_day=bonferroni(td["p_two_sided"], n_variants_tried),
        deflated_sharpe=deflated_sharpe(daily, n_variants_tried),
        caveat=("Trades on one day are dependent; day-level t/bootstrap are the primary inference. "
                "Multiple-testing adjustments are approximations (see docstrings)."),
    )
    if include_periods:
        out["periods"] = {f: period_breakdown(t, f).to_dict("records") for f in ("month", "quarter", "year")}
    return out


# ----------------------------------------------------------------- power
def required_trades(edge: float, sd: float, alpha: float = 0.05, power: float = 0.8, one_sided: bool = True) -> float:
    """n = ((z_{1-alpha} + z_{power}) * sd / edge)^2  (one-sample z-test of mean>0, independent trades).
    two-sided uses z_{1-alpha/2}. Normal approximation, ignores dependence/fat tails; with
    day-clustering the effective n is smaller, so treat as a LOWER bound."""
    za = norm_ppf(1 - alpha) if one_sided else norm_ppf(1 - alpha / 2)
    return float(((za + norm_ppf(power)) * sd / edge) ** 2)


def power_table(edges_r=(0.02, 0.05, 0.10, 0.20), sd_r=(1.0, 1.5), alphas=(0.05,), n_variants=24, power=0.8) -> pd.DataFrame:
    rows = []
    for sd in sd_r:
        for e in edges_r:
            for a in alphas:
                rows.append({"edge_R_per_trade": e, "sd_R": sd, "n_alpha_0.05": required_trades(e, sd, a, power),
                             f"n_bonf_alpha_{a/n_variants:.4f}": required_trades(e, sd, a / n_variants, power)})
    return pd.DataFrame(rows)
