"""Pydantic models mirroring the scenario YAML 1:1.

The config *is* the API: every tunable behavior lives here, and the YAML is
validated against these models at load time (unknown keys, out-of-range dates,
bad segment dims → load-time errors). See ``docs/plan.md`` "Config YAML shape".
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Segment dimensions a rate anomaly (or per-segment override) may filter on.
SEGMENT_DIMS = frozenset({"country", "device", "channel", "plan"})

RATE_ANOMALY_TYPES = frozenset(
    {"spike", "drop", "level_shift", "trend_change", "seasonality_change", "ramp"}
)
DQ_ANOMALY_TYPES = frozenset(
    {"volume_dropout", "null_spike", "distribution_shift", "loading_delay", "duplicate_rows"}
)


class _Base(BaseModel):
    """Strict base: unknown keys are load-time errors (catches YAML typos)."""

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- #
# Growth curves
# --------------------------------------------------------------------------- #
class PiecewiseSegment(_Base):
    start: dt.date = Field(..., alias="from")
    kind: Literal["flat", "linear", "exponential"] = "flat"
    rate: float = 0.0

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class GrowthConfig(_Base):
    """Multiplicative baseline shape, normalized to 1.0 at the timeline start.

    - flat:        1
    - linear:      1 + rate * day_index
    - exponential: (1 + rate) ** day_index
    - logistic:    sigmoid from 1 up to ``capacity`` (multiplier of baseline)
    - piecewise:   chained continuous segments (structural growth breakpoints)
    """

    kind: Literal["flat", "linear", "exponential", "logistic", "piecewise"] = "flat"
    rate: float = 0.0
    capacity: float | None = None  # logistic ceiling, as a multiplier of the start level
    midpoint: dt.date | None = None  # logistic inflection date
    steepness: float | None = None  # logistic steepness (per day)
    segments: list[PiecewiseSegment] | None = None

    @model_validator(mode="after")
    def _check_kind(self) -> GrowthConfig:
        if self.kind == "piecewise" and not self.segments:
            raise ValueError("piecewise growth requires non-empty `segments`")
        if self.kind == "logistic" and self.capacity is None:
            raise ValueError("logistic growth requires `capacity`")
        return self


# --------------------------------------------------------------------------- #
# Top-level sections
# --------------------------------------------------------------------------- #
class CompanyConfig(_Base):
    name: str
    slug: str
    currency: str = "USD"
    industry: str | None = None


class TimelineConfig(_Base):
    start: dt.date
    end: dt.date | None = None
    days: int | None = None

    @model_validator(mode="after")
    def _resolve(self) -> TimelineConfig:
        if self.end is None and self.days is None:
            raise ValueError("timeline needs either `end` or `days`")
        if self.end is not None and self.end < self.start:
            raise ValueError("timeline.end precedes timeline.start")
        return self

    @property
    def end_date(self) -> dt.date:
        if self.end is not None:
            return self.end
        assert self.days is not None
        return self.start + dt.timedelta(days=self.days - 1)

    @property
    def n_days(self) -> int:
        return (self.end_date - self.start).days + 1


class CalendarConfig(_Base):
    holiday_country: str | None = None
    holiday_effect: float = 1.0  # multiplier applied on holidays (e.g. 0.8 = -20%)
    annual_amp: float = 0.0  # amplitude of the sinusoidal annual cycle
    annual_peak_doy: int = 1  # day-of-year at which the annual cycle peaks


class NoiseConfig(_Base):
    day_sigma: float = 0.05  # lognormal sigma of the shared daily driver noise
    ar1: float = 0.0  # AR(1) autocorrelation of the log day-effect


class SessionChannel(_Base):
    """A traffic channel. Paid channels derive sessions from spend / cpc."""

    kind: Literal["organic", "paid", "fixed"] = "fixed"
    baseline: float | None = None  # organic/fixed: sessions per day at t0
    growth: GrowthConfig = Field(default_factory=GrowthConfig)
    spend_baseline: float | None = None  # paid: ad spend per day at t0
    spend_growth: GrowthConfig | None = None
    cpc: float | None = None  # paid: cost per click (~ cost per session)

    @model_validator(mode="after")
    def _check(self) -> SessionChannel:
        if self.kind == "paid":
            if self.spend_baseline is None or self.cpc is None:
                raise ValueError("paid channel requires `spend_baseline` and `cpc`")
        elif self.baseline is None:
            raise ValueError(f"{self.kind} channel requires `baseline`")
        return self


class TrafficConfig(_Base):
    channels: dict[str, SessionChannel]
    duration_seconds_mean: float = 180.0
    page_views_mean: float = 4.0


class MixConfig(_Base):
    country: dict[str, float]
    device: dict[str, float]


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
    trial_convert: float = 0.15  # P(trial converts to paid)
    monthly_churn: dict[str, float]  # per-plan monthly churn hazard
    monthly_upgrade: float = 0.02
    monthly_downgrade: float = 0.01
    monthly_resurrect: float = 0.01  # P(a churned user resurrects), per month


class EngagementConfig(_Base):
    dau_over_active: dict[str, float]  # per-plan P(active-sub user active on a day)
    events_per_active_day: dict[str, float]  # per-plan mean events on an active day
    frailty_sigma: float = 0.6  # per-user lognormal frailty on event intensity
    feature_mix: dict[str, float]  # distribution over event_name
    weekend_uplift: float = 1.0  # B2C usage weekend multiplier (>1 = rises on weekends)


class SourceLoading(_Base):
    cadence: Literal["daily", "hourly", "micro_batch", "streaming"] = "daily"
    batch_minutes: float | None = None  # micro_batch: minutes between batches
    lag_median_minutes: float = 30.0  # median connector lag (lognormal)
    lag_sigma: float = 0.5


class LoadingConfig(_Base):
    sources: dict[str, SourceLoading]


# --------------------------------------------------------------------------- #
# Anomalies
# --------------------------------------------------------------------------- #
class Window(_Base):
    start: dt.date
    end: dt.date | None = None

    @model_validator(mode="after")
    def _order(self) -> Window:
        if self.end is not None and self.end < self.start:
            raise ValueError("window.end precedes window.start")
        return self


class MinMax(_Base):
    min: float
    max: float

    @model_validator(mode="after")
    def _order(self) -> MinMax:
        if self.max < self.min:
            raise ValueError("magnitude.max < magnitude.min")
        return self


class ScriptedAnomaly(_Base):
    name: str
    kind: Literal["rate", "dq"]
    type: str
    target: str  # rate: driver name; dq: raw table name (schema.table)
    window: Window
    magnitude: float = 1.0
    segment: dict[str, str] | None = None
    params: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _check(self) -> ScriptedAnomaly:
        allowed = RATE_ANOMALY_TYPES if self.kind == "rate" else DQ_ANOMALY_TYPES
        if self.type not in allowed:
            raise ValueError(f"{self.kind} anomaly type {self.type!r} not in {sorted(allowed)}")
        _check_segment(self.segment)
        return self


class SurpriseConfig(_Base):
    count: int = 0
    kinds: list[Literal["rate", "dq"]] = Field(default_factory=lambda: ["rate", "dq"])
    types: list[str] | None = None  # None => all types allowed for the chosen kinds
    magnitude: MinMax
    min_gap_days: int = 14
    exclude_windows: list[Window] = Field(default_factory=list)


class AnomaliesConfig(_Base):
    scripted: list[ScriptedAnomaly] = Field(default_factory=list)
    surprise: SurpriseConfig | None = None


def _check_segment(segment: dict[str, str] | None) -> None:
    if not segment:
        return
    bad = set(segment) - SEGMENT_DIMS
    if bad:
        raise ValueError(f"segment dims {sorted(bad)} not in {sorted(SEGMENT_DIMS)}")


# --------------------------------------------------------------------------- #
# Root
# --------------------------------------------------------------------------- #
class ScenarioConfig(_Base):
    company: CompanyConfig
    seed: int = 0
    timeline: TimelineConfig
    calendar: CalendarConfig = Field(default_factory=CalendarConfig)
    weekly_shape: list[float] = Field(default_factory=lambda: [1.0] * 7)
    noise: NoiseConfig = Field(default_factory=NoiseConfig)
    traffic: TrafficConfig
    mix: MixConfig
    funnel: FunnelConfig
    plans: PlansConfig
    lifecycle: LifecycleConfig
    engagement: EngagementConfig
    loading: LoadingConfig
    anomalies: AnomaliesConfig = Field(default_factory=AnomaliesConfig)

    @model_validator(mode="after")
    def _cross_checks(self) -> ScenarioConfig:
        if len(self.weekly_shape) != 7:
            raise ValueError("weekly_shape must have exactly 7 entries (Mon..Sun)")
        start, end = self.timeline.start, self.timeline.end_date
        for a in self.anomalies.scripted:
            w = a.window
            if not (start <= w.start <= end):
                raise ValueError(f"anomaly {a.name!r} window.start {w.start} outside timeline")
            if w.end is not None and not (start <= w.end <= end):
                raise ValueError(f"anomaly {a.name!r} window.end {w.end} outside timeline")
        return self
