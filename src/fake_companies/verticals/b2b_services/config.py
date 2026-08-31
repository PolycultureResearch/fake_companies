"""B2B services scenario config: the plain envelope plus CRM/pipeline sections.

Deliberately no traffic/mix sections — a sales-led B2B firm has no web
acquisition layer in this model, which is exactly why those sections are
vertical-owned rather than part of the envelope.
"""

from __future__ import annotations

from pydantic import Field, model_validator

from ...config.schema import BaseScenarioConfig, GrowthConfig, _Base, validate_segment_dims

# Segment dimensions a B2B rate anomaly (or per-segment override) may filter on.
SEGMENT_DIMS = frozenset({"source", "industry", "size_tier"})


class LeadSource(_Base):
    baseline: float  # leads per day at t0
    growth: GrowthConfig = Field(default_factory=GrowthConfig)


class LeadsConfig(_Base):
    sources: dict[str, LeadSource]
    # Share of leads attached to an already-known account (repeat business).
    existing_account_rate: float = 0.15


class AccountsConfig(_Base):
    industry_mix: dict[str, float]
    size_tier_mix: dict[str, float]  # e.g. smb / mid_market / enterprise
    region_mix: dict[str, float]


class PipelineStage(_Base):
    name: str
    advance_rate: float  # P(advance to the next stage vs closed_lost)
    duration_days_mean: float  # time spent in the stage before resolving
    duration_sigma: float = 0.6


class PipelineConfig(_Base):
    # Ordered funnel; a deal enters stages[0] at creation and is closed_won
    # after advancing out of the last stage.
    stages: list[PipelineStage]

    @model_validator(mode="after")
    def _check(self) -> PipelineConfig:
        if len(self.stages) < 2:
            raise ValueError("pipeline needs at least two stages")
        names = [s.name for s in self.stages]
        if len(names) != len(set(names)):
            raise ValueError("pipeline stage names must be unique")
        for reserved in ("closed_won", "closed_lost"):
            if reserved in names:
                raise ValueError(f"stage name {reserved!r} is reserved for terminal states")
        return self


class DealsConfig(_Base):
    amount_mean: float = 40000.0  # lognormal mean of a mid-market deal
    amount_sigma: float = 0.7
    size_tier_multipliers: dict[str, float] = Field(default_factory=dict)


class ContractsConfig(_Base):
    upfront_share: float = 0.4  # invoiced at signing; the rest at delivery
    delivery_days_mean: float = 60.0  # signing -> delivery (balance invoice)
    delivery_sigma: float = 0.4
    net_days: int = 30  # invoice payment terms
    late_rate: float = 0.25  # P(an invoice is paid late)
    late_days_mean: float = 18.0  # extra days past due when late
    late_sigma: float = 0.6


class B2BServicesScenarioConfig(BaseScenarioConfig):
    leads: LeadsConfig
    accounts: AccountsConfig
    pipeline: PipelineConfig
    deals: DealsConfig = Field(default_factory=DealsConfig)
    contracts: ContractsConfig = Field(default_factory=ContractsConfig)

    @model_validator(mode="after")
    def _check_segments(self) -> B2BServicesScenarioConfig:
        validate_segment_dims(self.anomalies, SEGMENT_DIMS)
        return self
