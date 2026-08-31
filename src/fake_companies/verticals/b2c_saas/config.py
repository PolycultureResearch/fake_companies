"""B2C SaaS scenario config: the envelope plus the SaaS funnel sections."""

from __future__ import annotations

from pydantic import model_validator

from ...config.schema import BaseScenarioConfig, _Base, validate_segment_dims
from ...config.sections import MixConfig, TrafficConfig

# Segment dimensions a SaaS rate anomaly (or per-segment override) may filter on.
SEGMENT_DIMS = frozenset({"country", "device", "channel", "plan"})


class FunnelConfig(_Base):
    signup_rate: dict[str, float]  # per-channel P(session -> signup)
    signup_rate_default: float = 0.02  # channels absent from signup_rate
    trial_start_rate: float = 0.5  # P(signup starts a trial vs stays free)


class PlanConfig(_Base):
    name: str
    monthly_price: float
    annual_price: float | None = None


class PlansConfig(_Base):
    plans: list[PlanConfig]
    plan_mix: dict[str, float]  # distribution over paid plans for converting trials
    annual_share: float = 0.3  # fraction of paid subscriptions billed annually


class LifecycleConfig(_Base):
    trial_days: int = 14
    trial_convert: float = 0.15  # P(trial converts to paid), cohort mean
    monthly_churn: dict[str, float]  # per-plan monthly churn hazard
    monthly_upgrade: float = 0.02
    monthly_downgrade: float = 0.01
    monthly_resurrect: float = 0.01  # P(a churned user resurrects), per month
    # P(a free non-trial user subscribes directly), per month. The third way
    # into a paid plan: new_subscriptions = trial converts + resurrects + these.
    monthly_direct_convert: float = 0.0
    # Engagement -> conversion coupling. A trial user's conversion probability
    # is trial_convert * activation_boost^activated * days_boost^days_active,
    # renormalized by the cohort-wide mean multiplier so the configured
    # trial_convert stays the realized mean. 1.0 = uncoupled.
    activation_conversion_boost: float = 1.0
    days_active_conversion_boost: float = 1.0
    # Engagement -> churn coupling: hazard *= member_engagement[day]^-gamma_e
    # * frailty^-gamma_f, each analytically renormalized to preserve the
    # configured mean hazard. 0.0 = uncoupled.
    churn_engagement_gamma: float = 0.0
    churn_frailty_gamma: float = 0.0


class EngagementConfig(_Base):
    dau_over_active: dict[str, float]  # per-plan P(active-sub user active on a day)
    events_per_active_day: dict[str, float]  # per-plan mean events on an active day
    frailty_sigma: float = 0.6  # per-user lognormal frailty on event intensity
    feature_mix: dict[str, float]  # distribution over event_name
    weekend_uplift: float = 1.0  # B2C usage weekend multiplier (>1 = rises on weekends)
    # The event that counts as trial activation (the product's aha moment).
    trial_activation_event: str = "upload_work"
    # Trial-window intensity relative to the free-tier baseline: trialists are
    # deliberately trying the product, not idling on the free tier.
    trial_intensity_boost: float = 1.0
    # Sigma scale (x noise.day_sigma) of the two shared engagement drivers.
    # `trial_engagement` moves trial activity AND conversion together;
    # `member_engagement` moves paid-tier activity AND (inversely) churn.
    # 0.0 = flat driver = the coupling has no time variation to learn from.
    trial_engagement_sigma_scale: float = 0.0
    member_engagement_sigma_scale: float = 0.0


class B2CSaaSScenarioConfig(BaseScenarioConfig):
    traffic: TrafficConfig
    mix: MixConfig
    funnel: FunnelConfig
    plans: PlansConfig
    lifecycle: LifecycleConfig
    engagement: EngagementConfig

    @model_validator(mode="after")
    def _check_segments(self) -> B2CSaaSScenarioConfig:
        validate_segment_dims(self.anomalies, SEGMENT_DIMS)
        return self
