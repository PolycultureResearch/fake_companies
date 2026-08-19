"""Entity layer: raw rows drawn stochastically from the latent panel.

``build_all`` runs the entity chain in dependency order, writing each frame into
the shared ``frames`` dict keyed by table fqn. The chain:

    plans -> ad_spend -> sessions -> users            (M2)
          -> subscriptions / subscription_events       (M3, lifecycle)
          -> invoices / payments                        (M3, billing)
          -> product.events                             (M4, usage)
"""

from __future__ import annotations

import datetime as dt
import importlib.util

import pandas as pd

from ..config import ScenarioConfig
from ..core import RngHub
from ..core.calendar import Calendar
from ..latent import DriverPanel

__all__ = ["build_all"]


def build_all(
    cfg: ScenarioConfig,
    cal: Calendar,
    rng: RngHub,
    panel: DriverPanel,
    frames: dict[str, pd.DataFrame],
) -> None:
    from .funnel import build_users
    from .marketing import build_ad_spend
    from .plans import build_plan_index
    from .traffic import build_sessions

    nominal_loaded = dt.datetime.combine(cal.end, dt.time(3, 0))

    plan_index = build_plan_index(cfg)
    frames["app_db.plans"] = plan_index.frame(nominal_loaded)
    frames["ad_platform.ad_spend"] = build_ad_spend(cfg, cal, rng, panel)

    sessions = build_sessions(cfg, cal, rng, panel)
    users = build_users(cfg, cal, rng, panel, sessions)
    frames["web.sessions"] = sessions
    frames["app_db.users"] = users

    # Shared per-user engagement propensity: one latent drives trial activity,
    # conversion, churn and usage alike (the realistic confounding the demo's
    # learned edges recover). Generated once, passed everywhere.
    from .trial_activity import build_trial_activity, user_frailty

    n_users = int(users["user_id"].max()) if len(users) else 0
    frailty = user_frailty(rng.stream("frailty"), n_users, cfg.engagement.frailty_sigma)

    # Trial-window activity runs BEFORE the lifecycle: engagement during the
    # trial causes conversion, so it has to exist first.
    trial_events, trial_stats = build_trial_activity(
        cfg, cal, rng, panel, plan_index, frames, frailty
    )

    # --- M3: lifecycle + billing (added in later milestones) --------------- #
    if _has_module("lifecycle"):
        from .lifecycle import build_lifecycle

        build_lifecycle(
            cfg, cal, rng, panel, plan_index, frames, trial_stats=trial_stats, frailty=frailty
        )
    if _has_module("billing"):
        from .billing import build_billing

        build_billing(cfg, cal, rng, plan_index, frames)

    # --- M4: usage --------------------------------------------------------- #
    if _has_module("usage"):
        from .usage import build_usage

        build_usage(
            cfg, cal, rng, panel, plan_index, frames, frailty=frailty, trial_events=trial_events
        )


def _has_module(name: str) -> bool:
    return importlib.util.find_spec(f".{name}", __package__) is not None
