"""Trial-window product activity, generated *before* the lifecycle.

The point of the ordering: trial engagement causes conversion, so the
activity a user shows during their 7-day trial must exist before the
lifecycle machine draws their conversion. This module generates each trial
user's activity for [created_day, trial_end), computes the two per-user
quantities the conversion draw consumes — ``activated`` (did the configured
aha-moment event) and ``days_active`` — and hands the events to ``usage``
for inclusion in ``product.events`` (usage covers everything *after* the
trial window; between the two, each user-day is generated exactly once).

Intensity per user-day:

    p_active[u, d] = dau_over_active[free][d] * trial_intensity_boost
                     * trial_engagement[d] * frailty[u] * weekend_uplift[d]

``trial_engagement`` is the shared day-level driver that also multiplies the
conversion probability (via the realized activation/days-active), which is
what makes the tree's activation -> conversion edge learnable from weekly
aggregates rather than only visible cross-sectionally.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import ScenarioConfig
from ..core import RngHub
from ..core.calendar import Calendar
from ..latent import DriverPanel
from ._util import (
    USAGE_HOUR_WEIGHTS,
    active_day_mask,
    events_on_active_days,
    intraday_seconds,
    sample_labels,
    timestamps_from_days,
)
from .plans import PlanIndex


@dataclass
class TrialStats:
    """Per-user trial engagement, indexed by ``user_id - 1`` (full length;
    zeros for users who never started a trial)."""

    activated: np.ndarray  # bool
    days_active: np.ndarray  # int


def user_frailty(gen: np.random.Generator, n_users: int, sigma: float) -> np.ndarray:
    """The shared per-user engagement propensity (lognormal, mean 1).

    Generated once in ``build_all`` and passed to trial activity, lifecycle
    and usage alike — the same latent drives how active a user is *and* how
    likely they are to convert and to stay, which is the realistic coupling
    the demo's learned edges recover.
    """
    if sigma <= 0:
        return np.ones(n_users)
    return gen.lognormal(-0.5 * sigma**2, sigma, size=n_users)


def build_trial_activity(
    cfg: ScenarioConfig,
    cal: Calendar,
    rng: RngHub,
    panel: DriverPanel,
    plan_index: PlanIndex,
    frames: dict[str, pd.DataFrame],
    frailty: np.ndarray,
) -> tuple[pd.DataFrame | None, TrialStats]:
    users = frames["app_db.users"]
    n_users = int(users["user_id"].max()) if len(users) else 0
    stats = TrialStats(
        activated=np.zeros(n_users, dtype=bool),
        days_active=np.zeros(n_users, dtype=np.int64),
    )
    if n_users == 0:
        return None, stats

    trial_users = users.loc[users["started_trial"].to_numpy(dtype=bool)]
    if len(trial_users) == 0:
        return None, stats

    gen = rng.stream("trial_activity")
    n_days = cal.n_days
    trial_days = cfg.lifecycle.trial_days
    eng = cfg.engagement

    uid0 = trial_users["user_id"].to_numpy(dtype=np.int64) - 1
    created = trial_users["day_index"].to_numpy(dtype=np.int64)
    country = trial_users["country"].to_numpy()
    device = trial_users["device_at_signup"].to_numpy()

    free_name = plan_index.free_name
    if free_name is None or free_name not in eng.dau_over_active:
        return None, stats
    dau = panel.get(f"dau_over_active.{free_name}")
    epd = panel.get(f"events_per_active_day.{free_name}")
    te = panel.get("trial_engagement")
    weekend = np.where(np.isin(cal.dow, [5, 6]), eng.weekend_uplift, 1.0)
    day_rate = dau * te * weekend * eng.trial_intensity_boost  # length n_days

    # (U, trial_days) day grid, clipped to the calendar; a day past the end is
    # masked out rather than wrapped.
    grid = created[:, None] + np.arange(trial_days)[None, :]
    in_range = grid < n_days
    grid_c = np.minimum(grid, n_days - 1)

    p_active = day_rate[grid_c] * frailty[uid0][:, None]
    p_active[~in_range] = 0.0
    active = active_day_mask(gen, p_active)
    stats.days_active[uid0] = active.sum(axis=1)

    counts = events_on_active_days(gen, active, epd[grid_c])
    total = int(counts.sum())
    if total == 0:
        return None, stats

    flat = counts.ravel()
    ev_cell = np.repeat(np.arange(flat.size), flat)
    ev_u = ev_cell // trial_days  # index into the trial-user arrays
    ev_day = grid_c.ravel()[ev_cell]

    feats = list(eng.feature_mix)
    fp = list(eng.feature_mix.values())
    names = sample_labels(gen, feats, fp, total)

    activated_events = names == eng.trial_activation_event
    if activated_events.any():
        stats.activated[uid0[np.unique(ev_u[activated_events])]] = True

    sec = intraday_seconds(gen, ev_day, USAGE_HOUR_WEIGHTS)
    events = pd.DataFrame(
        {
            "user_id": (uid0[ev_u] + 1).astype(np.int64),
            "event_name": names,
            "plan_at_event": free_name,
            "country": country[ev_u],
            "device": device[ev_u],
            "occurred_at": timestamps_from_days(cal.start, ev_day, sec),
        }
    )
    return events, stats
