"""Measured effects: what an anomaly did, read off a single world and its null twin.

A single world and its null world share a config, a seed and a regime; the
only thing that differs is one anomaly. Their day-by-day difference in a
metric is therefore the anomaly's causal effect **plus realization noise**,
and this module is careful about the second term because it is not small.

Where the noise comes from
--------------------------
Every entity builder draws from one named random stream for the whole
timeline. Once the anomaly changes how many values a builder consumes, every
later draw from that stream lands differently, and "later" means later in
*draw order*, which is not always later in time:

- a builder that draws day by day (CPG scan sales, retail session conversion)
  stays identical to the null world until the anomaly's first day;
- a builder that draws channel by channel, or customer by customer, hands
  different values to days long before the anomaly (B2C sessions for every
  channel after the anomalous one, retail repeat orders, the B2B pipeline).

So the twin difference is exactly zero before the anomaly for some metrics
and already noisy on day one for others. Making the draws line up would mean
changing how existing builders consume their streams, which moves the golden
hashes; this module measures the noise instead of removing it.

The noise floor
---------------
For one series (a metric, or a metric at one dimension value) there are two
estimates of the daily noise:

- ``pre_onset_rms_diff``: the root-mean-square twin difference over the days
  before the anomaly starts. It is zero when the streams are still aligned.
  For a metric dated by the day something happened nothing causal can be in
  it. A cohort-dated metric is the exception: B2C trial metrics are bucketed
  by trial start, so an anomaly reaches back a trial length into them, and
  for those this estimate is too large (which errs towards "not detected").
- a step estimate: the root-mean-square day-over-day change of the
  difference, over the days from the first divergence on, divided by sqrt(2).
  It sees the noise the anomaly itself unleashed. The two largest steps are
  set aside first: the anomaly switching on and off is two steps, and they
  are not noise. Days on which the metric is zero in both worlds are skipped
  (a weekly feed has six of them a week). Otherwise it is a plain RMS and not
  a robust scale, on purpose: bookings arrive in lumps, and a robust scale
  calls every lump an anomaly.

``sigma`` is the larger of the two and ``noise_floor = NOISE_K * sigma``. A
day *exceeds the floor* when ``|diff| > noise_floor`` on or after the
anomaly's start; ``onset`` and ``offset`` are the first and last such day and
``days_over_floor`` counts them. A sparse series (a few returns a day) can
carry a large effect without any single day clearing the floor; then ``onset``
is NULL and ``first_diff`` — the first day the twins differ at all, exact when
the streams were aligned until the anomaly — is the date to use.

Did it move?
------------
``z_window`` is the mean difference over the anomaly's declared window (on
the days the metric is live) divided by the standard error of that mean:

- for a flow whose twins already differed before the anomaly (with at least
  1.5 window lengths of such days): the RMS of the mean difference over
  every window of the anomaly's length before it. That is the noise of a
  window mean, measured directly, with nothing causal in it. It matters when
  the noise persists: two different sets of users are active in the twins
  and stay different for weeks, and ``sigma / sqrt(n)`` would call that
  wander a ten-sigma effect;
- for a flow whose twins were aligned until the anomaly, the smaller of two
  estimates, each of which can only be too large. (1) ``sigma / sqrt(n)``
  widened by the day-to-day persistence of the difference outside the window
  (``sqrt((1 + r) / (1 - r))`` for its lag-one autocorrelation ``r``). (2)
  When the timeline has at least ``MIN_PLACEBO_WINDOWS`` of them, the RMS of
  the mean difference over *placebo windows* — windows of the anomaly's
  length elsewhere in the same twin. That one sees noise that cancels: a
  return-rate anomaly re-deals the request delay of every later return,
  which moves returns between days and nets out inside a window. Placebo
  windows after a lasting effect (a ramp holds its level) contain the effect
  and are useless there, which is why (1) is kept beside it;
- for a stock-like series (MRR, active subscriptions, open pipeline), whose
  noise accumulates like a random walk and does not average out, the level
  noise expected at the window: ``pre_onset_rms_diff * sqrt(2 t / T)``, with
  ``T`` the days before the anomaly and ``t`` the middle of the window. A
  series is treated as stock-like when its pre-onset level noise is more than
  ``INTEGRATED_RATIO`` times its step noise.

``z_extended`` is the same over the window stretched by its own length, for
an effect that lands late (returns follow orders by two weeks). A series
``moved`` when ``|z_window|`` reaches ``NOISE_K``; or when ``|z_extended|``
does *and* the window itself points the same way with ``|z_window| >= 1``
(a stretched window alone, with nothing in the declared one, is what noise
looks like); or when there is no noise to compare with: none at all, or none
outside the window.

``moved`` is one twin's verdict and it is noisy: the same anomaly can clear
the bar for a metric in one seed and miss it in the next. For a decision per
anomaly and metric use the set-level ``verdict`` in
``effects_summary.parquet``, which tests the mean across seeds against the
seeds' own spread.

What this cannot do: confirm a small causal effect from one twin. An anomaly
that cuts orders by 4% is real and still inside one twin's noise; such a
metric is reported as ``not_detected``, which is a statement about this
twin's power and not about the generator. The estimate to trust is the
cross-seed one in ``effects_summary.parquet``
(:func:`summarize_across_seeds`): the mean effect across seeds with its
spread and standard error.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np
import pandas as pd

NOISE_K = 4.0  # noise floor (per day) and window z threshold, in noise sigmas
INTEGRATED_RATIO = 3.0  # pre-onset level noise / step noise above which a series is stock-like
MIN_STEPS_TO_TRIM = 6  # day-over-day steps needed before the two largest are set aside
MIN_PRE_DAYS = 7  # pre-onset days needed to call a series stock-like
FLOAT_RTOL = 1e-9  # twin values this close are the same number
MIN_PLACEBO_WINDOWS = 6  # placebo windows needed to trust their spread

EFFECT_COLUMNS = ["date", "metric", "dimension", "value", "null_value", "world_value", "diff"]
_KEYS = ["metric", "dimension", "value"]


def twin_effects(
    null: pd.DataFrame, world: pd.DataFrame, fills: dict[str, float | None]
) -> pd.DataFrame:
    """Day-by-day difference between a world's metric series and its null twin's.

    ``null`` / ``world`` are :meth:`SemanticLayer.series` frames. A slice that
    exists in only one of the two (a dimension value that never occurs in the
    other) is filled on the missing side with the metric's ``fill_nulls_with``
    from ``fills``, or left NULL when it has none.
    """
    keys = ["date", *_KEYS]
    sides = [c for c in ("numerator", "denominator") if c in null and c in world]
    null, world = null[[*keys, "v", *sides]], world[[*keys, "v", *sides]]
    merged = null.rename(columns={"v": "null_value", **{c: f"null_{c}" for c in sides}}).merge(
        world.rename(columns={"v": "world_value", **{c: f"world_{c}" for c in sides}}),
        on=keys,
        how="outer",
        indicator=True,
    )
    fill = merged["metric"].map(fills).astype(float)
    only_world = (merged["_merge"] == "right_only").to_numpy()
    only_null = (merged["_merge"] == "left_only").to_numpy()
    merged.loc[only_world, "null_value"] = fill[only_world]
    merged.loc[only_null, "world_value"] = fill[only_null]
    for c in sides:  # a side of a missing slice is an empty sum
        has = merged[f"null_{c}"].notna() | merged[f"world_{c}"].notna()
        merged.loc[only_world & has, f"null_{c}"] = 0.0
        merged.loc[only_null & has, f"world_{c}"] = 0.0
    diff = merged["world_value"] - merged["null_value"]
    # Two sums over the same rows in another order differ in the last bits
    # (a duplicated row deduplicated by staging, say). That is not a difference.
    same = np.isclose(merged["world_value"], merged["null_value"], rtol=FLOAT_RTOL, atol=0.0)
    merged["diff"] = diff.mask(same, 0.0)
    merged = merged.sort_values(["metric", "dimension", "value", "date"], na_position="first")
    extra = [f"{w}_{c}" for c in sides for w in ("null", "world")]
    return merged[[*EFFECT_COLUMNS, *extra]].reset_index(drop=True)


def _noise_sigma(
    diff: np.ndarray, active: np.ndarray, pre: np.ndarray
) -> tuple[float, float, float]:
    """``(sigma, pre_onset_rms, sigma_step)`` for one series; see the module docstring."""
    pre_vals = diff[pre & ~np.isnan(diff)]
    pre_rms = float(np.sqrt(np.mean(pre_vals**2))) if pre_vals.size else 0.0

    sigma_step = 0.0
    usable = active & ~np.isnan(diff)
    nonzero = np.flatnonzero(usable & (diff != 0))
    if nonzero.size:
        tail = diff[nonzero[0] :][usable[nonzero[0] :]]
        steps = np.diff(tail)
        if steps.size >= MIN_STEPS_TO_TRIM:
            # The anomaly switching on and off is two steps, and they are not noise.
            steps = np.sort(np.abs(steps))[:-2]
        if steps.size:
            sigma_step = float(np.sqrt(np.mean(steps**2)) / np.sqrt(2.0))
    return max(pre_rms, sigma_step), pre_rms, sigma_step


def _placebo_se(diff: np.ndarray, active: np.ndarray, w0: int, w1: int) -> float | None:
    """Noise of a window-length mean, read off the twin's other windows.

    Slides a window of the length ``w0..w1`` over the diverged part of the
    series, skipping anything within one window length of the anomaly, and
    returns the RMS of those placebo means. Unlike ``sigma / sqrt(n)`` it sees
    how the noise is correlated: re-dealt *timing* (the same returns landing
    on other days) cancels inside a window and gives a small value. ``None``
    when fewer than ``MIN_PLACEBO_WINDOWS`` placebo windows exist.
    """
    length = w1 - w0 + 1
    usable = active & ~np.isnan(diff)
    nonzero = np.flatnonzero(usable & (diff != 0))
    if nonzero.size == 0:
        return None
    step = max(1, length // 2)
    means = []
    for a in range(int(nonzero[0]), len(diff) - length + 1, step):
        b = a + length - 1
        if b >= w0 - length and a <= w1 + length:
            continue  # too close to the anomaly to be clean
        block = usable[a : b + 1]
        if block.any():
            means.append(diff[a : b + 1][block].mean())
    if len(means) < MIN_PLACEBO_WINDOWS:
        return None
    return float(np.sqrt(np.mean(np.square(means))))


def _pre_onset_se(diff: np.ndarray, active: np.ndarray, w0: int, length: int) -> float | None:
    """Noise of a window-length mean, from the days before the anomaly.

    The RMS of the mean difference over every window of ``length`` days that
    ends before day ``w0`` and starts after the twins first differ. ``None``
    when the twins were aligned before the anomaly or fewer than
    ``1.5 * length`` diverged days precede it.
    """
    usable = active & ~np.isnan(diff)
    nonzero = np.flatnonzero(usable[:w0] & (diff[:w0] != 0))
    if nonzero.size == 0:
        return None
    first = int(nonzero[0])
    if w0 - first < 1.5 * length:
        return None
    vals = np.where(usable[first:w0], np.nan_to_num(diff[first:w0]), 0.0)
    cnt = usable[first:w0].astype(float)
    csum = np.concatenate([[0.0], np.cumsum(vals)])
    ccnt = np.concatenate([[0.0], np.cumsum(cnt)])
    sums = csum[length:] - csum[:-length]
    counts = ccnt[length:] - ccnt[:-length]
    means = sums[counts > 0] / counts[counts > 0]
    if means.size == 0:
        return None
    return float(np.sqrt(np.mean(means**2)))


def _persistence(diff: np.ndarray, active: np.ndarray, w0: int, w1: int) -> float:
    """How much wider than ``sigma / sqrt(n)`` the error of a window mean is
    when the noise persists from day to day: ``sqrt((1 + r) / (1 - r))`` for a
    lag-one autocorrelation ``r`` of the difference outside the window
    (never below 1; ``r`` capped at 0.95)."""
    usable = active & ~np.isnan(diff)
    usable[w0 : w1 + 1] = False
    x = diff[usable]
    if x.size < 8 or np.all(x == x[0]):
        return 1.0
    x = x - x.mean()
    denom = float(np.dot(x, x))
    if denom == 0:
        return 1.0
    r = float(np.clip(np.dot(x[:-1], x[1:]) / denom, 0.0, 0.95))
    return float(np.sqrt((1.0 + r) / (1.0 - r)))


def _window_z(
    diff: np.ndarray,
    active: np.ndarray,
    w0: int,
    w1: int,
    sigma: float,
    stock_se: float | None,
) -> tuple[float | None, bool]:
    """``(z, quiet_elsewhere)`` for the mean difference over days ``w0..w1``.

    ``quiet_elsewhere`` is true when the twin has placebo windows and none of
    them differs at all: there is no noise to divide by, and any difference
    inside the window is the anomaly.
    """
    live = np.zeros(len(diff), dtype=bool)
    live[w0 : w1 + 1] = True
    live &= active & ~np.isnan(diff)
    n = int(live.sum())
    if n == 0:
        return None, False
    mean = float(diff[live].mean())
    if stock_se is not None:
        return (mean / stock_se if stock_se > 0 else None), False
    # Best: the twins already differed before the anomaly, so the spread of
    # window-length means there is the noise of a window mean, measured, with
    # nothing causal in it.
    se = _pre_onset_se(diff, active, w0, w1 - w0 + 1)
    placebo = None
    if se is None:
        placebo = _placebo_se(diff, active, w0, w1)
        # Independent days, widened for the day-to-day persistence of the noise.
        modelled = sigma / np.sqrt(n) * _persistence(diff, active, w0, w1)
        # The smaller of the two: each fails upwards. Placebo windows after a
        # lasting effect (a ramp holds its level) contain the effect itself,
        # and the persistence estimate is inflated by the same thing but capped.
        se = modelled if placebo is None else min(placebo, modelled)
    if se > 0:
        return mean / se, False
    return None, placebo == 0.0 and mean != 0


def _rel(world_mean: float, null_mean: float) -> float | None:
    if not np.isfinite(world_mean) or not np.isfinite(null_mean) or null_mean == 0:
        return None
    return float(world_mean / null_mean - 1.0)


def _mean(a: np.ndarray) -> float:
    a = a[~np.isnan(a)]
    return float(a.mean()) if a.size else float("nan")


def _side_value(values: np.ndarray, rule: str) -> float:
    """One side of a ratio over a window, by its input's time aggregation."""
    defined = values[~np.isnan(values)]
    if defined.size == 0:
        return float("nan")
    if rule == "sum":
        return float(defined.sum())
    if rule in ("end_of_period", "rolling_window"):
        return float(defined[-1])
    return float("nan")  # count_distinct and the like: not derivable from days


def window_value(numerator: np.ndarray, denominator: np.ndarray, rules: tuple[str, str]) -> float:
    """A ratio metric's value over a window by its own aggregation rule:
    each side aggregated by its input's ``time_aggregation`` (``sum``: add the
    days; ``end_of_period``: the last day), then divided. NaN when the
    denominator is zero or undefined."""
    num, den = _side_value(numerator, rules[0]), _side_value(denominator, rules[1])
    if not np.isfinite(num) or not np.isfinite(den) or den == 0:
        return float("nan")
    return num / den


def evaluate_formula(node: dict, values: dict[str, float]) -> float:
    """A derived metric's formula tree (``SemanticLayer.formula``) over its
    inputs' window values. NaN propagates; ``nullif_zero`` of 0 is NaN."""
    if "const" in node:
        return float(node["const"])
    if "metric" in node:
        return float(values.get(node["metric"], float("nan")))
    args = [evaluate_formula(a, values) for a in node["args"]]
    op = node["op"]
    if op == "neg":
        return -args[0]
    if op == "nullif_zero":
        return float("nan") if args[0] == 0 else args[0]
    a, b = args
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if op == "/":
        return float("nan") if not np.isfinite(b) or b == 0 else a / b
    raise ValueError(f"unknown formula op {op!r}")


def _rule_value(values: np.ndarray, rule: str) -> float:
    """A metric without sides over a window, by its ``time_aggregation``.
    NaN where the rule cannot be applied to daily values (``count_distinct``)."""
    if rule == "sum":
        defined = values[~np.isnan(values)]
        return float(defined.sum()) if defined.size else float("nan")
    if rule in ("end_of_period", "rolling_window"):
        return _side_value(values, rule)
    return float("nan")


def summarize_twin(
    effects: pd.DataFrame,
    start: dt.date,
    end: dt.date | None,
    aggregation: dict[str, dict[str, Any]] | None = None,
) -> pd.DataFrame:
    """One row per series (metric x dimension x value) of an effects table.

    Columns: the series keys, then

    - ``null_mean`` / ``world_mean`` / ``mean_diff``: daily means over the
      anomaly's declared window (``start`` .. ``end``, or the end of the
      timeline for an open window);
    - ``rel_change``: ``world_mean / null_mean - 1`` over that window (NULL
      when the null mean is zero or undefined). This is the window mean of
      the *daily values*; for a ratio it averages daily ratios;
    - ``null_aggregated`` / ``world_aggregated`` / ``rel_change_aggregated``:
      the metric's value over the whole window by its own aggregation rule,
      and the relative change of that. ``aggregation`` maps each metric to
      ``{"rule": time_aggregation, "sides": (numerator rule, denominator
      rule) or None, "formula": tree or None}`` (``worlds.aggregation_spec``).
      ``sum``: the window total; ``end_of_period`` / ``rolling_window``: the
      last day; a ratio or an average: its sides aggregated by their own
      rules, then divided (:func:`window_value`, Σnumerator / Σdenominator
      for a rate); ``derived``: the formula over its inputs' window values.
      This is what a tool that aggregates a window the way the manifest
      says compares with. NULL for ``count_distinct`` metrics (not
      derivable from days) and anything computed from one, and for every
      metric when ``aggregation`` is not given;
    - ``pre_onset_rms_diff`` / ``pre_onset_max_abs_diff``: the twin difference
      before ``start`` — realization noise, by construction;
    - ``pre_onset_days``: the days before ``start`` on which the difference is
      defined (both twins have a value). With ``pre_onset_max_abs_diff`` zero
      and this above zero, the twins were identical until the anomaly;
    - ``noise_floor``: ``NOISE_K`` x the noise sigma;
    - ``first_diff``: the first day on or after ``start`` on which the twins
      differ at all. When ``pre_onset_rms_diff`` is zero the streams were
      aligned until the anomaly, so this is the exact day the effect arrives;
    - ``onset`` / ``offset`` / ``days_over_floor``, and ``differs`` (the
      twins differ on at least one day of the timeline);
    - ``z_window`` / ``z_extended`` / ``stock_like`` / ``moved``: the window
      test;
    - ``rel_change_onset_to_offset``: the relative change over the measured
      window instead of the declared one (it differs for lagged effects: a
      return-rate anomaly is dated by order and shows up weeks later).
    """
    lo = pd.Timestamp(start)
    aggregation = aggregation or {}
    has_sides = {"null_numerator", "null_denominator"} <= set(effects.columns)
    rows: list[dict[str, Any]] = []
    agg_values: dict[tuple, tuple[float, float]] = {}
    for (metric, dimension, value), g in effects.groupby(_KEYS, dropna=False, sort=False):
        dates = g["date"].to_numpy()
        null_v = g["null_value"].to_numpy(dtype=float)
        world_v = g["world_value"].to_numpy(dtype=float)
        diff = g["diff"].to_numpy(dtype=float)
        hi = pd.Timestamp(end) if end is not None else pd.Timestamp(dates.max())
        pre = dates < np.datetime64(lo)
        window = (dates >= np.datetime64(lo)) & (dates <= np.datetime64(hi))
        active = (np.nan_to_num(null_v) != 0) | (np.nan_to_num(world_v) != 0)

        sigma, pre_rms, sigma_step = _noise_sigma(diff, active, pre)
        floor = NOISE_K * sigma
        n_pre = int(pre.sum())
        stock_like = n_pre >= MIN_PRE_DAYS and pre_rms > INTEGRATED_RATIO * sigma_step > 0
        over = ~pre & ~np.isnan(diff) & (np.abs(diff) > floor)
        n_over = int(over.sum())
        onset = offset = first_diff = None
        rel_measured = None
        differs = np.flatnonzero(~pre & ~np.isnan(diff) & (diff != 0))
        if differs.size:
            first_diff = pd.Timestamp(dates[differs[0]]).date()
        if n_over:
            idx = np.flatnonzero(over)
            onset, offset = pd.Timestamp(dates[idx[0]]).date(), pd.Timestamp(dates[idx[-1]]).date()
            span = slice(idx[0], idx[-1] + 1)
            rel_measured = _rel(_mean(world_v[span]), _mean(null_v[span]))
        pre_abs = np.abs(diff[pre & ~np.isnan(diff)])
        null_mean, world_mean = _mean(null_v[window]), _mean(world_v[window])
        null_agg = world_agg = float("nan")
        spec = aggregation.get(metric)
        if spec is not None and spec.get("sides") is not None and has_sides:
            null_agg = window_value(
                g["null_numerator"].to_numpy(dtype=float)[window],
                g["null_denominator"].to_numpy(dtype=float)[window],
                spec["sides"],
            )
            world_agg = window_value(
                g["world_numerator"].to_numpy(dtype=float)[window],
                g["world_denominator"].to_numpy(dtype=float)[window],
                spec["sides"],
            )
        elif spec is not None and spec.get("formula") is None:
            null_agg = _rule_value(null_v[window], spec["rule"])
            world_agg = _rule_value(world_v[window], spec["rule"])
        key = (metric, None if pd.isna(dimension) else dimension, None if pd.isna(value) else value)
        agg_values[key] = (null_agg, world_agg)
        mean_diff = _mean(diff[window])
        # The window test, on the declared window and on the window stretched
        # by its own length (an effect that lands late).
        z_window = z_extended = None
        quiet = False
        w = np.flatnonzero(window)
        if w.size:
            w0, w1 = int(w[0]), int(w[-1])
            stock_se = None
            if stock_like:
                stock_se = pre_rms * np.sqrt(2.0 * (w0 + w1) / 2.0 / n_pre)
            z_window, quiet = _window_z(diff, active, w0, w1, sigma, stock_se)
            w2 = min(len(diff) - 1, w1 + (w1 - w0 + 1))
            z_extended, quiet_ext = _window_z(diff, active, w0, w2, sigma, stock_se)
            quiet = quiet or quiet_ext
        # No noise to compare with: either no noise at all (the streams never
        # diverged), or a twin that differs inside the window and nowhere else.
        exact = (sigma == 0 and n_over >= 1) or quiet
        in_window = z_window is not None and abs(z_window) >= NOISE_K
        late = (
            z_extended is not None
            and z_window is not None
            and abs(z_extended) >= NOISE_K
            and abs(z_window) >= 1.0
            and np.sign(z_extended) == np.sign(z_window)
        )
        moved = exact or in_window or late
        rows.append(
            {
                "metric": metric,
                "dimension": None if pd.isna(dimension) else dimension,
                "value": None if pd.isna(value) else value,
                "null_mean": null_mean,
                "world_mean": world_mean,
                "mean_diff": mean_diff,
                "rel_change": _rel(world_mean, null_mean),
                "null_aggregated": null_agg,
                "world_aggregated": world_agg,
                "rel_change_aggregated": _rel(world_agg, null_agg),
                "pre_onset_rms_diff": pre_rms,
                "pre_onset_max_abs_diff": float(pre_abs.max()) if pre_abs.size else 0.0,
                "pre_onset_days": int(pre_abs.size),
                "noise_floor": float(floor),
                "first_diff": first_diff,
                "onset": onset,
                "offset": offset,
                "days_over_floor": n_over,
                "differs": bool(np.any(diff[~np.isnan(diff)] != 0)),
                "z_window": z_window,
                "z_extended": z_extended,
                "stock_like": bool(stock_like),
                "moved": bool(moved),
                "rel_change_onset_to_offset": rel_measured,
            }
        )
    # Derived metrics: the formula over the inputs' window values, same slice.
    for row in rows:
        spec = aggregation.get(row["metric"])
        if spec is None or spec.get("formula") is None:
            continue
        both = []
        for side in (0, 1):
            inputs = {}
            for name in _formula_metrics(spec["formula"]):
                hit = agg_values.get((name, row["dimension"], row["value"]))
                inputs[name] = float("nan") if hit is None else hit[side]
            both.append(evaluate_formula(spec["formula"], inputs))
        row["null_aggregated"], row["world_aggregated"] = both
        row["rel_change_aggregated"] = _rel(both[1], both[0])
    return pd.DataFrame(rows)


def _formula_metrics(node: dict) -> list[str]:
    if "metric" in node:
        return [node["metric"]]
    return [m for a in node.get("args", []) for m in _formula_metrics(a)]


_STAT_FIELDS = [
    "rel_change",
    "rel_change_aggregated",
    "mean_diff",
    "null_mean",
    "pre_onset_rms_diff",
    "noise_floor",
    "first_diff",
    "onset",
    "offset",
    "days_over_floor",
    "z_window",
    "z_extended",
    "moved",
    "rel_change_onset_to_offset",
]


def _clean(v: Any) -> Any:
    """A JSON-ready scalar: NaN -> None, numpy -> python, dates -> ISO."""
    if v is None:
        return None
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        return float(v) if np.isfinite(v) else None
    return None if pd.isna(v) else v


def _stats(row: pd.Series) -> dict[str, Any]:
    return {k: _clean(row[k]) for k in _STAT_FIELDS}


def _slice_row(summary: pd.DataFrame, metric: str, where: dict[str, str]) -> pd.Series | None:
    """The summary row for ``metric`` at the slice ``where`` (total when empty)."""
    rows = summary[summary["metric"] == metric]
    if not where:
        rows = rows[rows["dimension"].isna()]
    else:
        dim, val = ",".join(where), ",".join(where.values())
        rows = rows[(rows["dimension"] == dim) & (rows["value"] == val)]
    return rows.iloc[0] if len(rows) else None


def ground_truth_fields(
    summary: pd.DataFrame,
    affected_metrics: list[str],
    root_metric: str | None,
    root_slice: dict[str, str] | None,
) -> dict[str, Any]:
    """The measured fields of one anomaly's ground-truth record.

    - ``measured_effects``: per metric, the total's stats.
    - ``metrics_moved``: the metrics whose total moved, declared or not. Empty
      when the anomaly moved no metric at all, which is what most dq
      anomalies do.
    - ``declared_affected_metrics_confirmed``: each declared metric under
      ``moved`` (its total moved), ``moved_in_slice_only`` (the total did not,
      but the metric at the anomaly's own slice did — the designed behaviour
      of a segmented anomaly) or ``not_detected`` (neither cleared the noise
      in this twin: not proof that the generator left it alone).
    - ``root_effect`` / ``root_metric_confirmed``: the root metric at the root
      slice, and whether it moved.
    """
    totals = summary[summary["dimension"].isna()]
    measured = {row["metric"]: _stats(row) for _, row in totals.iterrows()}

    where = dict(root_slice or {})
    # The anomaly's slice, most specific first: the whole conjunction, then
    # each single dimension of it (a metric rarely carries all of them).
    candidates = [where, *({d: v} for d, v in where.items())] if where else []
    confirmed: dict[str, list[str]] = {"moved": [], "moved_in_slice_only": [], "not_detected": []}
    for metric in affected_metrics:
        total = measured.get(metric)
        if total is None:
            continue  # not computed (unsupported metric): neither confirmed nor refuted
        if total["moved"]:
            confirmed["moved"].append(metric)
            continue
        in_slice = False
        for cand in candidates:
            row = _slice_row(summary, metric, cand)
            if row is not None:
                in_slice = bool(row["moved"])
                break
        confirmed["moved_in_slice_only" if in_slice else "not_detected"].append(metric)

    out: dict[str, Any] = {
        "measured_effects": measured,
        "metrics_moved": sorted(m for m, stats in measured.items() if stats["moved"]),
        "declared_affected_metrics_confirmed": confirmed,
    }
    if root_metric is not None:
        row = _slice_row(summary, root_metric, where)
        out["root_effect"] = None if row is None else _stats(row)
        out["root_metric_confirmed"] = None if row is None else bool(row["moved"])
    return out


SUMMARY_KEYS = ["regime", "anomaly", "metric", "dimension", "value"]

# Two-sided points of Student's t by degrees of freedom (1..30), then normal.
_T_CRIT = {
    0.01: (
        [
            63.657, 9.925, 5.841, 4.604, 4.032, 3.707, 3.499, 3.355, 3.250, 3.169,
            3.106, 3.055, 3.012, 2.977, 2.947, 2.921, 2.898, 2.878, 2.861, 2.845,
            2.831, 2.819, 2.807, 2.797, 2.787, 2.779, 2.771, 2.763, 2.756, 2.750,
        ],
        2.576,
    ),
    0.10: (
        [
            6.314, 2.920, 2.353, 2.132, 2.015, 1.943, 1.895, 1.860, 1.833, 1.812,
            1.796, 1.782, 1.771, 1.761, 1.753, 1.746, 1.740, 1.734, 1.729, 1.725,
            1.721, 1.717, 1.714, 1.711, 1.708, 1.706, 1.703, 1.701, 1.699, 1.697,
        ],
        1.645,
    ),
}  # fmt: skip

# The set's verdicts: (column, two-sided level, column of the projected seed
# count). ``verdict`` is the strict one and the one to pick targets by; the
# looser one exists for small sets, where the strict bar is out of reach
# (three seeds need |t| >= 9.9). Each column names its level so the two cannot
# be mistaken for each other; ``worlds.json`` -> ``verdicts`` describes both.
VERDICTS: list[tuple[str, float, str]] = [
    ("verdict", 0.01, "seeds_to_establish"),
    ("verdict_10pct", 0.10, "seeds_to_establish_10pct"),
]
MIN_SEEDS_TO_ESTABLISH = 2  # with one seed there is no spread, so nothing is established


def t_critical(n_seeds: int, level: float = 0.01) -> float:
    """The |t| a cross-seed mean must reach to be *established* at a
    two-sided ``level`` (0.01 or 0.10)."""
    if level not in _T_CRIT:
        raise ValueError(f"no t table for level {level!r}; known: {sorted(_T_CRIT)}")
    table, normal = _T_CRIT[level]
    df = n_seeds - 1
    if df < 1:
        return float("inf")
    return table[df - 1] if df <= len(table) else normal


def set_verdict(
    mean: float, sd: float, n_seeds: int, differs_share: float, level: float = 0.01
) -> str:
    """One verdict per (anomaly, metric, slice) for the whole set.

    - ``exactly_zero``: the twins never differ in any seed (the anomaly cannot
      reach the metric, or reaches it only through aligned draws that did not
      change);
    - ``established_up`` / ``established_down``: the mean relative change
      across seeds is at least :func:`t_critical` standard errors from zero
      (two-sided ``level``, 1% by default), judged by the seeds' own spread;
    - ``open``: neither. More seeds may settle it.
    - ``undefined``: no relative change exists (the null level is zero).

    With one seed nothing can be established.
    """
    if differs_share == 0:  # no seed's twins differ on any day
        return "exactly_zero"
    if not np.isfinite(mean):
        return "undefined"
    if not np.isfinite(sd):
        return "open"
    if sd == 0:
        return "open" if mean == 0 else ("established_up" if mean > 0 else "established_down")
    if abs(mean) / (sd / np.sqrt(n_seeds)) >= t_critical(n_seeds, level):
        return "established_up" if mean > 0 else "established_down"
    return "open"


def seeds_to_establish(mean: float, sd: float, level: float = 0.01) -> int | None:
    """The fewest seeds at which this mean and spread would be established.

    A projection, not a promise: it assumes the cross-seed mean and standard
    deviation stay what this set measured, and finds the smallest ``n`` with
    ``|mean| / (sd / sqrt(n)) >= t_critical(n, level)``. ``None`` when there
    is nothing to establish (a zero or undefined mean) or no spread to
    project from (one seed).
    """
    if not (np.isfinite(mean) and np.isfinite(sd)) or mean == 0:
        return None
    if sd == 0:
        return MIN_SEEDS_TO_ESTABLISH
    ratio = abs(mean) / sd
    table, normal = _T_CRIT[level]
    for n in range(MIN_SEEDS_TO_ESTABLISH, len(table) + 2):
        if ratio * np.sqrt(n) >= t_critical(n, level):
            return n
    return max(len(table) + 2, int(np.ceil((normal / ratio) ** 2)))


def summarize_across_seeds(per_twin: pd.DataFrame) -> pd.DataFrame:
    """Cross-seed mean and spread of every effect: ``effects_summary.parquet``.

    ``per_twin`` is the concatenation of :func:`summarize_twin` frames with
    ``regime``, ``seed`` and ``anomaly`` columns. One row per (regime,
    anomaly, metric, dimension, value):

    - ``n_seeds``;
    - ``rel_change_mean`` / ``_sd`` / ``_min`` / ``_max`` and
      ``rel_change_sem`` (sd / sqrt(n)): the mean is the best estimate of the
      expected effect, the spread is how far one seed can be from it;
    - ``mean_diff_mean`` / ``mean_diff_sd``: the same in the metric's units;
    - ``rel_change_aggregated_mean`` / ``_sd`` / ``_sem`` / ``_min`` /
      ``_max`` and ``t_across_seeds_aggregated``: the same for
      ``rel_change_aggregated`` (ratios and averages only; NULL otherwise).
      The verdicts test ``rel_change``, the window mean of daily values;
    - ``verdict``: :func:`set_verdict` at two-sided 1% — ``established_up``,
      ``established_down``, ``open``, ``exactly_zero`` or ``undefined``. This
      is the set's answer to "did this anomaly move this metric", and the one
      to pick a target by;
    - ``verdict_10pct``: the same test at two-sided 10%, for small sets where
      1% is out of reach. It establishes about one in ten effects that do not
      exist; it is never a substitute for ``verdict`` (see :data:`VERDICTS`);
    - ``seeds_to_establish`` / ``seeds_to_establish_10pct``:
      :func:`seeds_to_establish` at each level, the projected seed count at
      which this row's verdict would be established (NULL when there is
      nothing to establish);
    - ``moved_share``: the share of seeds in which the series moved,
      ``differs_share`` the share in which the twins differ on any day, and
      ``z_window_median``, the median window z across seeds;
    - ``t_across_seeds``: ``rel_change_mean / rel_change_sem`` — how many
      standard errors the mean effect is from zero, judged by the seeds'
      own disagreement. It is a t statistic with ``n_seeds - 1`` degrees of
      freedom, so with three seeds only values beyond about 4.3 mean much
      (NULL with one seed);
    - ``noise_floor_mean`` and ``pre_onset_rms_diff_mean``: the typical noise;
    - ``aligned_before_start``: true when in every seed the twins were
      identical on every day before the anomaly's declared start (the
      difference is defined on at least one such day and is zero on all of
      them); false when any seed's twins differed; NULL otherwise (some seed
      has no day before the start to judge by, e.g. a slice that first
      occurs after it). ``aligned_before_start_share`` is the share of the
      judged seeds that were aligned. Where it is true, ``first_diff`` is the exact
      day the effect arrives and one twin's difference has no realization
      noise before the anomaly (it can still have some after: the anomaly
      re-deals the entities it touches);
    - ``first_diff_median_lag_days`` / ``onset_median_lag_days`` /
      ``offset_median_lag_days``: median days from the anomaly's declared
      start to the first differing day, the measured onset and the offset.
    """
    if per_twin.empty:
        return pd.DataFrame(columns=[*SUMMARY_KEYS, "n_seeds"])
    df = per_twin.copy()
    start = pd.to_datetime(df["start"])
    df["onset_lag"] = (pd.to_datetime(df["onset"]) - start).dt.days
    df["first_diff_lag"] = (pd.to_datetime(df["first_diff"]) - start).dt.days
    df["offset_lag"] = (pd.to_datetime(df["offset"]) - start).dt.days
    df["rel_change"] = df["rel_change"].astype(float)
    df["z_window"] = df["z_window"].astype(float)
    if "rel_change_aggregated" not in df:
        df["rel_change_aggregated"] = np.nan
    df["rel_change_aggregated"] = df["rel_change_aggregated"].astype(float)
    if "pre_onset_days" not in df:  # summaries written before the field existed
        df["pre_onset_days"] = np.nan
    if "pre_onset_max_abs_diff" not in df:
        df["pre_onset_max_abs_diff"] = df["pre_onset_rms_diff"]
    judged = df["pre_onset_days"].fillna(0) > 0
    df["_aligned"] = (judged & (df["pre_onset_max_abs_diff"] == 0)).astype(float).where(judged)
    g = df.groupby(SUMMARY_KEYS, dropna=False, sort=True)
    out = g.agg(
        n_seeds=("seed", "nunique"),
        rel_change_mean=("rel_change", "mean"),
        rel_change_sd=("rel_change", "std"),
        rel_change_min=("rel_change", "min"),
        rel_change_max=("rel_change", "max"),
        rel_change_aggregated_mean=("rel_change_aggregated", "mean"),
        rel_change_aggregated_sd=("rel_change_aggregated", "std"),
        rel_change_aggregated_min=("rel_change_aggregated", "min"),
        rel_change_aggregated_max=("rel_change_aggregated", "max"),
        mean_diff_mean=("mean_diff", "mean"),
        mean_diff_sd=("mean_diff", "std"),
        null_mean=("null_mean", "mean"),
        moved_share=("moved", "mean"),
        differs_share=("differs", "mean"),
        z_window_median=("z_window", "median"),
        noise_floor_mean=("noise_floor", "mean"),
        pre_onset_rms_diff_mean=("pre_onset_rms_diff", "mean"),
        first_diff_median_lag_days=("first_diff_lag", "median"),
        onset_median_lag_days=("onset_lag", "median"),
        offset_median_lag_days=("offset_lag", "median"),
        aligned_before_start_share=("_aligned", "mean"),
        _judged=("_aligned", "count"),
    ).reset_index()
    # False as soon as one seed's twins differed; True only when every seed
    # was judged and aligned; NULL when some seed could not be judged.
    flags = []
    for share, judged, n in zip(
        out["aligned_before_start_share"], out.pop("_judged"), out["n_seeds"]
    ):
        if pd.notna(share) and share < 1.0:
            flags.append(False)
        elif pd.notna(share) and judged == n:
            flags.append(True)
        else:
            flags.append(None)
    out["aligned_before_start"] = pd.array(flags, dtype="boolean")
    out["rel_change_sem"] = out["rel_change_sd"] / np.sqrt(out["n_seeds"])
    out["rel_change_aggregated_sem"] = out["rel_change_aggregated_sd"] / np.sqrt(out["n_seeds"])
    with np.errstate(divide="ignore", invalid="ignore"):
        out["t_across_seeds"] = out["rel_change_mean"] / out["rel_change_sem"]
        out["t_across_seeds_aggregated"] = (
            out["rel_change_aggregated_mean"] / out["rel_change_aggregated_sem"]
        )
    rows = list(
        zip(out["rel_change_mean"], out["rel_change_sd"], out["n_seeds"], out["differs_share"])
    )
    for column, level, seeds_column in VERDICTS:
        out[column] = [set_verdict(m, sd, n, d, level) for m, sd, n, d in rows]
        out[seeds_column] = pd.array(
            [None if d == 0 else seeds_to_establish(m, sd, level) for m, sd, _, d in rows],
            dtype="Int64",
        )
    return out
