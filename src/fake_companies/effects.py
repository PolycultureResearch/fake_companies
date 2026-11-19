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

- for a flow, ``sigma / sqrt(n)`` (independent daily noise), or, when the
  timeline is long enough to have at least ``MIN_PLACEBO_WINDOWS`` of them,
  the RMS of the mean difference over *placebo windows* — windows of the
  anomaly's length elsewhere in the same twin — if that is smaller. It is
  smaller when the noise is re-dealt timing: a return-rate anomaly re-deals
  the request delay of every later return, which moves returns between days
  and cancels within a window;
- for a stock-like series (MRR, active subscriptions, open pipeline), whose
  noise accumulates like a random walk and does not average out, the level
  noise expected at the window: ``pre_onset_rms_diff * sqrt(2 t / T)``, with
  ``T`` the days before the anomaly and ``t`` the middle of the window. A
  series is treated as stock-like when its pre-onset level noise is more than
  ``INTEGRATED_RATIO`` times its step noise.

``z_extended`` is the same over the window stretched by its own length, for
an effect that lands late (returns follow orders by two weeks). A series
``moved`` when ``|z_window|`` or ``|z_extended|`` reaches ``NOISE_K``, or
when there is no noise to compare with: none at all, or none outside the
window.

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
MIN_PLACEBO_WINDOWS = 8  # placebo windows needed to trust their spread

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
    merged = null.rename(columns={"v": "null_value"}).merge(
        world.rename(columns={"v": "world_value"}), on=keys, how="outer", indicator=True
    )
    fill = merged["metric"].map(fills).astype(float)
    only_world = (merged["_merge"] == "right_only").to_numpy()
    only_null = (merged["_merge"] == "left_only").to_numpy()
    merged.loc[only_world, "null_value"] = fill[only_world]
    merged.loc[only_null, "world_value"] = fill[only_null]
    diff = merged["world_value"] - merged["null_value"]
    # Two sums over the same rows in another order differ in the last bits
    # (a duplicated row deduplicated by staging, say). That is not a difference.
    same = np.isclose(merged["world_value"], merged["null_value"], rtol=FLOAT_RTOL, atol=0.0)
    merged["diff"] = diff.mask(same, 0.0)
    merged = merged.sort_values(["metric", "dimension", "value", "date"], na_position="first")
    return merged[EFFECT_COLUMNS].reset_index(drop=True)


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
    placebo = _placebo_se(diff, active, w0, w1)
    white = sigma / np.sqrt(n)
    se = white if placebo is None else min(white, placebo)
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


def summarize_twin(effects: pd.DataFrame, start: dt.date, end: dt.date | None) -> pd.DataFrame:
    """One row per series (metric x dimension x value) of an effects table.

    Columns: the series keys, then

    - ``null_mean`` / ``world_mean`` / ``mean_diff``: daily means over the
      anomaly's declared window (``start`` .. ``end``, or the end of the
      timeline for an open window);
    - ``rel_change``: ``world_mean / null_mean - 1`` over that window (NULL
      when the null mean is zero or undefined);
    - ``pre_onset_rms_diff`` / ``pre_onset_max_abs_diff``: the twin difference
      before ``start`` — realization noise, by construction;
    - ``noise_floor``: ``NOISE_K`` x the noise sigma;
    - ``first_diff``: the first day on or after ``start`` on which the twins
      differ at all. When ``pre_onset_rms_diff`` is zero the streams were
      aligned until the anomaly, so this is the exact day the effect arrives;
    - ``onset`` / ``offset`` / ``days_over_floor``;
    - ``z_window`` / ``z_extended`` / ``stock_like`` / ``moved``: the window
      test;
    - ``rel_change_onset_to_offset``: the relative change over the measured
      window instead of the declared one (it differs for lagged effects: a
      return-rate anomaly is dated by order and shows up weeks later).
    """
    lo = pd.Timestamp(start)
    rows: list[dict[str, Any]] = []
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
        moved = exact or any(z is not None and abs(z) >= NOISE_K for z in (z_window, z_extended))
        rows.append(
            {
                "metric": metric,
                "dimension": None if pd.isna(dimension) else dimension,
                "value": None if pd.isna(value) else value,
                "null_mean": null_mean,
                "world_mean": world_mean,
                "mean_diff": mean_diff,
                "rel_change": _rel(world_mean, null_mean),
                "pre_onset_rms_diff": pre_rms,
                "pre_onset_max_abs_diff": float(pre_abs.max()) if pre_abs.size else 0.0,
                "noise_floor": float(floor),
                "first_diff": first_diff,
                "onset": onset,
                "offset": offset,
                "days_over_floor": n_over,
                "z_window": z_window,
                "z_extended": z_extended,
                "stock_like": bool(stock_like),
                "moved": bool(moved),
                "rel_change_onset_to_offset": rel_measured,
            }
        )
    return pd.DataFrame(rows)


_STAT_FIELDS = [
    "rel_change",
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
        "declared_affected_metrics_confirmed": confirmed,
    }
    if root_metric is not None:
        row = _slice_row(summary, root_metric, where)
        out["root_effect"] = None if row is None else _stats(row)
        out["root_metric_confirmed"] = None if row is None else bool(row["moved"])
    return out


SUMMARY_KEYS = ["regime", "anomaly", "metric", "dimension", "value"]


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
    - ``moved_share``: the share of seeds in which the series moved, and
      ``z_window_median``, the median window z across seeds;
    - ``t_across_seeds``: ``rel_change_mean / rel_change_sem`` — how many
      standard errors the mean effect is from zero, judged by the seeds'
      own disagreement. It is a t statistic with ``n_seeds - 1`` degrees of
      freedom, so with three seeds only values beyond about 4.3 mean much
      (NULL with one seed);
    - ``noise_floor_mean`` and ``pre_onset_rms_diff_mean``: the typical noise;
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
    g = df.groupby(SUMMARY_KEYS, dropna=False, sort=True)
    out = g.agg(
        n_seeds=("seed", "nunique"),
        rel_change_mean=("rel_change", "mean"),
        rel_change_sd=("rel_change", "std"),
        rel_change_min=("rel_change", "min"),
        rel_change_max=("rel_change", "max"),
        mean_diff_mean=("mean_diff", "mean"),
        mean_diff_sd=("mean_diff", "std"),
        null_mean=("null_mean", "mean"),
        moved_share=("moved", "mean"),
        z_window_median=("z_window", "median"),
        noise_floor_mean=("noise_floor", "mean"),
        pre_onset_rms_diff_mean=("pre_onset_rms_diff", "mean"),
        first_diff_median_lag_days=("first_diff_lag", "median"),
        onset_median_lag_days=("onset_lag", "median"),
        offset_median_lag_days=("offset_lag", "median"),
    ).reset_index()
    out["rel_change_sem"] = out["rel_change_sd"] / np.sqrt(out["n_seeds"])
    with np.errstate(divide="ignore", invalid="ignore"):
        out["t_across_seeds"] = out["rel_change_mean"] / out["rel_change_sem"]
    return out
