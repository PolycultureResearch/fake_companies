from __future__ import annotations

import numpy as np
import pandas as pd


def _dedup(df: pd.DataFrame, pk: str) -> pd.DataFrame:
    return df.drop_duplicates(pk)


def test_deal_volume_tracks_lead_drivers(b2b_cfg, b2b_result):
    deals = _dedup(b2b_result.frames["crm.deals"], "deal_id")
    # Expected deals: sum of source baselines x growth over the timeline
    # (seasonality and noise are mean ~1). Weekly shape means the realized mean
    # differs day to day but not in total.
    n_days = b2b_result.calendar.n_days
    expected = 0.0
    for spec in b2b_cfg.leads.sources.values():
        growth_rate = spec.growth.rate if spec.growth.kind == "linear" else 0.0
        expected += sum(spec.baseline * (1 + growth_rate * t) for t in range(n_days))
    # The scripted outbound level_shift removes ~0.6 x 14 days x baseline.
    sd = np.sqrt(expected)
    assert abs(len(deals) - expected) < 0.12 * expected + 4 * sd, (len(deals), expected)


def test_stage_events_ordered_and_complete(b2b_cfg, b2b_result):
    deals = _dedup(b2b_result.frames["crm.deals"], "deal_id")
    events = _dedup(b2b_result.frames["crm.deal_stage_events"], "event_id")
    stage_names = [s.name for s in b2b_cfg.pipeline.stages]

    # Every deal has a first-stage event; events per deal are time-ordered and
    # follow the funnel order.
    per_deal = events.groupby("deal_id")
    first_stage = per_deal["stage"].first()
    assert (first_stage == stage_names[0]).all()
    order = {s: i for i, s in enumerate(stage_names + ["closed_lost", "closed_won"])}
    for _, grp in list(per_deal)[:200]:  # spot-check a couple hundred deals
        occurred = grp.sort_values("occurred_at")
        ranks = occurred["stage"].map(order).to_numpy()
        assert (np.diff(ranks) > 0).all(), occurred[["stage", "occurred_at"]]

    # Won deals entered every stage.
    won_ids = set(deals.loc[deals["stage"] == "closed_won", "deal_id"])
    won_events = events[events["deal_id"].isin(won_ids)]
    counts = won_events.groupby("deal_id").size()
    assert (counts == len(stage_names) + 1).all()  # all stages + closed_won


def test_win_rate_within_ci(b2b_cfg, b2b_result):
    deals = _dedup(b2b_result.frames["crm.deals"], "deal_id")
    closed = deals[deals["stage"].isin(["closed_won", "closed_lost"])]
    won = (closed["stage"] == "closed_won").sum()
    p_theory = float(np.prod([s.advance_rate for s in b2b_cfg.pipeline.stages]))
    n = len(closed)
    se = np.sqrt(p_theory * (1 - p_theory) / n)
    # Censoring biases observed win rate down (wins take longest to resolve),
    # so allow a wider band below than above.
    assert p_theory - 6 * se - 0.03 < won / n < p_theory + 4 * se, (won / n, p_theory)


def test_amounts_positive_and_tier_ordered(b2b_result):
    deals = _dedup(b2b_result.frames["crm.deals"], "deal_id")
    assert (deals["amount"] > 0).all()
    means = deals.groupby("size_tier")["amount"].mean()
    assert means["smb"] < means["mid_market"] < means["enterprise"]


def test_censoring_no_timestamps_past_end(b2b_result):
    cal = b2b_result.calendar
    cutoff = pd.Timestamp(cal.end) + pd.Timedelta(days=1)
    deals = _dedup(b2b_result.frames["crm.deals"], "deal_id")
    events = _dedup(b2b_result.frames["crm.deal_stage_events"], "event_id")
    assert (pd.to_datetime(deals["closed_at"]).dropna() < cutoff).all()
    assert (pd.to_datetime(events["occurred_at"]) < cutoff).all()
    open_deals = deals[~deals["stage"].isin(["closed_won", "closed_lost"])]
    assert open_deals["closed_at"].isna().all()


def test_accounts_and_contacts_link(b2b_result):
    deals = _dedup(b2b_result.frames["crm.deals"], "deal_id")
    accounts = b2b_result.frames["crm.accounts"]
    contacts = b2b_result.frames["crm.contacts"]
    assert set(deals["account_id"]) <= set(accounts["account_id"])
    assert set(deals["contact_id"]) <= set(contacts["contact_id"])
    # Deals on the same account share the account's industry.
    joined = deals.merge(accounts, on="account_id", suffixes=("", "_acct"))
    assert (joined["industry"] == joined["industry_acct"]).all()
