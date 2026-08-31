"""Shared, optional config sections used by more than one vertical.

Traffic and audience mix describe a web/marketing acquisition layer. Verticals
that have one (b2c_saas, retail_dtc) declare these as fields on their scenario
config; verticals without a web layer simply don't.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .schema import BaseScenarioConfig, GrowthConfig, _Base


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


class WebScenarioConfig(BaseScenarioConfig):
    """Envelope for verticals with a web/marketing acquisition layer.

    The shared traffic and marketing entity builders (``fake_companies.shared``)
    are written against this: any vertical whose config subclasses it can reuse
    them as-is.
    """

    traffic: TrafficConfig
    mix: MixConfig
