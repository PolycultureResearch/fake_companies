"""CRM pipeline: accounts, contacts, deals, and stage-transition events.

Leads arrive per source as daily Poisson counts from ``leads.<source>`` and
become deals entering the first pipeline stage. Each stage resolves after a
lognormal duration (scaled by ``cycle_time_scale``) by either advancing —
probability ``stage_conversion.<stage>`` at the day the stage was entered,
overridden by segment-specific drivers so a segmented anomaly (e.g. an
industry-specific proposal slump) materializes — or closing lost. Advancing
out of the last stage is closed_won. Resolutions past the timeline end are
censored: the deal is simply still open in the observed data.

Win rate and cycle time are never set directly; they emerge from the
per-stage gates and durations.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ....core import RngHub
from ....core.calendar import Calendar
from ....latent import DriverPanel
from ....latent.panel import driver_key
from ....shared._util import expand_day_index, lognormal_around, poisson_counts, sample_labels
from ..config import B2BServicesScenarioConfig as ScenarioConfig

# CRM activity happens during business hours.
BUSINESS_HOUR_WEIGHTS = np.array(
    [0.02] * 7 + [0.3, 1.2, 2.6, 3.0, 2.8, 2.0, 2.6, 3.0, 2.9, 2.4, 1.4, 0.5] + [0.08] * 5,
    dtype=float,
)

_TITLES = ["VP Operations", "Director of IT", "COO", "Head of Procurement", "CTO", "CFO"]


def _pools(seed_names: int, seed_companies: int) -> tuple[list[str], list[str], list[str]]:
    from faker import Faker

    fake = Faker()
    Faker.seed(seed_names)
    first = [fake.first_name() for _ in range(2000)]
    last = [fake.last_name() for _ in range(2000)]
    Faker.seed(seed_companies)
    companies = [fake.company() for _ in range(4000)]
    return first, last, companies


def _business_seconds(gen: np.random.Generator, n: int) -> np.ndarray:
    w = BUSINESS_HOUR_WEIGHTS / BUSINESS_HOUR_WEIGHTS.sum()
    hours = gen.choice(24, size=n, p=w)
    return hours * 3600 + gen.integers(0, 3600, size=n)


def _timestamps(cal: Calendar, day: np.ndarray, secs: np.ndarray) -> np.ndarray:
    base = np.datetime64(cal.start, "s")
    return base + (day.astype("int64") * 86400 + secs.astype("int64")).astype("timedelta64[s]")


def _segment_matches(attrs: dict[str, np.ndarray], segment: dict[str, str], n: int) -> np.ndarray:
    mask = np.ones(n, dtype=bool)
    for dim, val in segment.items():
        if dim in attrs:
            mask &= attrs[dim] == val
    return mask


def _stage_prob(
    panel: DriverPanel, stage: str, entry_day: np.ndarray, attrs: dict[str, np.ndarray]
) -> np.ndarray:
    """Per-deal advance probability at stage entry, honoring segment overrides."""
    name = f"stage_conversion.{stage}"
    prob = panel.get(name)[entry_day]
    for key in panel.rates:
        if "|" not in key or not key.startswith(f"{name}|"):
            continue
        _, seg_str = key.split("|", 1)
        segment = dict(kv.split("=", 1) for kv in seg_str.split(","))
        seg_arr = panel.rates[driver_key(name, segment)]
        m = _segment_matches(attrs, segment, len(entry_day))
        prob[m] = seg_arr[entry_day[m]]
    return prob


def build_pipeline(
    cfg: ScenarioConfig,
    cal: Calendar,
    rng: RngHub,
    panel: DriverPanel,
    frames: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """Fill crm.* frames; returns the deals frame (with day columns for billing)."""
    gen = rng.stream("b2b_pipeline")
    last_day = cal.n_days - 1

    # --- deal creation from lead volumes ------------------------------------ #
    parts = []
    for src in cfg.leads.sources:
        counts = poisson_counts(gen, panel.get(f"leads.{src}"))
        day_idx = expand_day_index(counts)
        parts.append(pd.DataFrame({"day_index": day_idx, "source": src}))
    deals = pd.concat(parts, ignore_index=True)
    deals = deals.sort_values(["day_index", "source"], kind="stable").reset_index(drop=True)
    n = len(deals)
    if n == 0:
        raise ValueError("no leads generated; raise leads.sources baselines")
    day = deals["day_index"].to_numpy()

    # --- account attributes -------------------------------------------------- #
    ac = cfg.accounts
    industry = sample_labels(gen, list(ac.industry_mix), list(ac.industry_mix.values()), n)
    size_tier = sample_labels(gen, list(ac.size_tier_mix), list(ac.size_tier_mix.values()), n)
    region = sample_labels(gen, list(ac.region_mix), list(ac.region_mix.values()), n)

    # New account vs repeat business with an existing one.
    reuse = gen.random(n) < cfg.leads.existing_account_rate
    reuse[0] = False
    new_mask = ~reuse
    # account_no: for new-account deals, their (1-based) position among new accounts.
    new_rank = np.cumsum(new_mask)
    account_no = new_rank.copy()
    # Reused deals point at a uniformly random earlier new account.
    n_prior = np.maximum(new_rank - new_mask, 1)  # new accounts strictly before this deal
    reuse_pick = np.floor(gen.random(n) * n_prior).astype(np.int64) + 1
    account_no[reuse] = reuse_pick[reuse]
    # Reused deals inherit the account's attributes.
    new_idx_for_no = np.flatnonzero(new_mask)  # deal row of the k-th new account
    src_rows = new_idx_for_no[account_no - 1]
    industry = industry[src_rows]
    size_tier = size_tier[src_rows]
    region = region[src_rows]
    attrs = {"source": deals["source"].to_numpy(), "industry": industry, "size_tier": size_tier}

    created_secs = _business_seconds(gen, n)
    created_at = _timestamps(cal, day, created_secs)

    # --- stage machine (vectorized across deals; loop over ~4 stages) -------- #
    stages = cfg.pipeline.stages
    n_stages = len(stages)
    cycle_scale = panel.get("cycle_time_scale")
    entry_day = np.empty((n, n_stages), dtype=np.int64)
    resolve_day = np.empty((n, n_stages), dtype=np.int64)
    advanced = np.empty((n, n_stages), dtype=bool)

    entry = day.copy()
    for k, stage in enumerate(stages):
        entry_clipped = np.clip(entry, 0, last_day)
        dur = lognormal_around(gen, stage.duration_days_mean, stage.duration_sigma, n)
        dur = np.maximum(1, np.round(dur * cycle_scale[entry_clipped]).astype(np.int64))
        p = _stage_prob(panel, stage.name, entry_clipped, attrs)
        advanced[:, k] = gen.random(n) < p
        entry_day[:, k] = entry
        resolve_day[:, k] = entry + dur
        entry = resolve_day[:, k]

    reached = np.ones((n, n_stages), dtype=bool)  # advanced out of all prior stages
    for k in range(1, n_stages):
        reached[:, k] = reached[:, k - 1] & advanced[:, k - 1]
    won_all = reached[:, -1] & advanced[:, -1]

    # First stage a deal fails at (n_stages if it advances everywhere).
    fail_stage = np.where(~advanced & reached, np.arange(n_stages)[None, :], n_stages).min(axis=1)
    terminal_day = np.where(
        won_all, resolve_day[:, -1], resolve_day[np.arange(n), np.clip(fail_stage, 0, n_stages - 1)]
    )
    closed_observed = terminal_day <= last_day

    # Current stage: last stage entered within the timeline (open deals), or
    # the terminal state when the close is observed.
    stage_names = np.asarray([s.name for s in stages], dtype=object)
    entered = reached & (entry_day <= last_day)
    last_entered = np.maximum(entered.sum(axis=1) - 1, 0)
    stage_col = stage_names[last_entered]
    stage_col = np.where(
        closed_observed, np.where(won_all, "closed_won", "closed_lost"), stage_col
    ).astype(object)

    closed_secs = _business_seconds(gen, n)
    closed_at_all = _timestamps(cal, np.clip(terminal_day, 0, last_day), closed_secs)
    closed_at = pd.Series(closed_at_all).where(pd.Series(closed_observed), pd.NaT).to_numpy()

    # --- deal amounts --------------------------------------------------------- #
    tier_mult = np.asarray(
        [cfg.deals.size_tier_multipliers.get(t, 1.0) for t in size_tier], dtype=float
    )
    base_amount = lognormal_around(gen, cfg.deals.amount_mean, cfg.deals.amount_sigma, n)
    amount = np.round(base_amount * tier_mult * panel.get("deal_size")[day] / 100.0) * 100.0

    # --- identity frames ------------------------------------------------------ #
    fk = rng.stream("faker")
    first_pool, last_pool, company_pool = _pools(
        int(fk.integers(0, 2**32)), int(fk.integers(0, 2**32))
    )
    deal_ids = np.arange(1, n + 1, dtype=np.int64)
    account_id = account_no  # dense 1..n_accounts numbering in creation order
    contact_ids = deal_ids  # one contact per deal

    fi = gen.integers(0, len(first_pool), size=n)
    li = gen.integers(0, len(last_pool), size=n)
    first = np.asarray(first_pool, dtype=object)[fi]
    last = np.asarray(last_pool, dtype=object)[li]
    full_name = np.char.add(np.char.add(first.astype(str), " "), last.astype(str))

    new_rows = np.flatnonzero(new_mask)
    n_accounts = len(new_rows)
    ci = gen.integers(0, len(company_pool), size=n_accounts)
    account_names = np.asarray(company_pool, dtype=object)[ci]
    domains = np.asarray(
        [
            f"{''.join(c for c in str(a).lower() if c.isalnum())[:14]}.example.com"
            for a in account_names
        ],
        dtype=object,
    )
    email = [f"{f}.{l}@{domains[no - 1]}".lower() for f, l, no in zip(first, last, account_no)]

    frames["crm.accounts"] = pd.DataFrame(
        {
            "account_id": np.arange(1, n_accounts + 1, dtype=np.int64),
            "name": account_names,
            "industry": industry[new_rows],
            "size_tier": size_tier[new_rows],
            "region": region[new_rows],
            "created_at": created_at[new_rows],
            "_loaded_at": pd.NaT,
        }
    )
    frames["crm.contacts"] = pd.DataFrame(
        {
            "contact_id": contact_ids,
            "account_id": account_id,
            "full_name": full_name,
            "email": email,
            "title": sample_labels(gen, _TITLES, [1] * len(_TITLES), n),
            "created_at": created_at,
            "_loaded_at": pd.NaT,
        }
    )

    deals_out = pd.DataFrame(
        {
            "deal_id": deal_ids,
            "account_id": account_id,
            "contact_id": contact_ids,
            "source": deals["source"].to_numpy(),
            "industry": industry,
            "size_tier": size_tier,
            "region": region,
            "stage": stage_col,
            "amount": amount,
            "currency": cfg.company.currency,
            "created_at": created_at,
            "closed_at": closed_at,
            "_loaded_at": pd.NaT,
            # --- carried for billing (dropped on write) ---
            "day_index": day,
            "won": won_all & closed_observed,
            "closed_day": np.where(closed_observed, terminal_day, -1),
        }
    )
    frames["crm.deals"] = deals_out

    # --- stage events: entering each stage + observed terminal states -------- #
    ev_deal, ev_stage, ev_day = [], [], []
    for k, stage in enumerate(stages):
        m = entered[:, k]
        ev_deal.append(deal_ids[m])
        ev_stage.append(np.full(int(m.sum()), stage.name, dtype=object))
        ev_day.append(entry_day[m, k])
    m = closed_observed
    ev_deal.append(deal_ids[m])
    ev_stage.append(np.where(won_all[m], "closed_won", "closed_lost").astype(object))
    ev_day.append(terminal_day[m])

    ev = pd.DataFrame(
        {
            "deal_id": np.concatenate(ev_deal),
            "stage": np.concatenate(ev_stage),
            "day_index": np.concatenate(ev_day),
        }
    )
    ev["account_id"] = ev["deal_id"].map(pd.Series(account_id, index=deal_ids))
    ev_secs = _business_seconds(gen, len(ev))
    ev["occurred_at"] = _timestamps(cal, ev["day_index"].to_numpy(), ev_secs)
    ev = ev.sort_values(["occurred_at", "deal_id"], kind="stable").reset_index(drop=True)
    ev.insert(0, "event_id", np.arange(1, len(ev) + 1, dtype=np.int64))
    ev["_loaded_at"] = pd.NaT
    frames["crm.deal_stage_events"] = ev[
        ["event_id", "deal_id", "account_id", "stage", "occurred_at", "_loaded_at"]
    ]

    return deals_out
