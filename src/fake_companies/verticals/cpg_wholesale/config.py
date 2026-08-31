"""CPG wholesale scenario config: envelope plus catalog/market/promo sections.

No traffic/mix sections — consumers buy at retail, not on the company's site.
Brand marketing exists (`marketing:`) but is causally decoupled from sales:
its spend drivers move no downstream metric (see metrics.py), which is the
defining property of this vertical's causal graph.
"""

from __future__ import annotations

from pydantic import Field, model_validator

from ...config.schema import BaseScenarioConfig, GrowthConfig, Window, _Base, validate_segment_dims

# Segment dimensions a CPG rate anomaly (or per-segment override) may filter on.
SEGMENT_DIMS = frozenset({"category", "banner", "channel_type"})


class MarketingChannel(_Base):
    spend_baseline: float  # $ per day at t0
    growth: GrowthConfig = Field(default_factory=GrowthConfig)
    cpc: float = 1.0  # cost per click for the platform-reported funnel


class MarketingConfig(_Base):
    channels: dict[str, MarketingChannel]


class CategoryConfig(_Base):
    n_products: int
    case_size: int  # consumer units per wholesale case
    case_price_min: float  # wholesale price per case
    case_price_max: float
    retail_markup: float = 1.65  # msrp = (case_price / case_size) * markup
    popularity: float = 1.0  # relative consumer-demand weight


class CatalogConfig(_Base):
    categories: dict[str, CategoryConfig]


class RetailerConfig(_Base):
    channel_type: str  # grocery | natural | mass | online | independents
    stores: int
    velocity: float  # baseline units / store / week for an average product


class DistributorConfig(_Base):
    share: float  # share of the independents banner's demand served
    region: str = "national"


class MarketConfig(_Base):
    retailers: dict[str, RetailerConfig]  # banner -> retailer
    distributors: dict[str, DistributorConfig] = Field(default_factory=dict)
    independents_banner: str = "independents"


class TradePromoConfig(_Base):
    name: str
    banner: str
    category: str
    window: Window
    discount_pct: float  # off-invoice discount to the retailer while active
    lift: float = 1.5  # consumer POS lift at the banner while active


class OrderingConfig(_Base):
    # Replenishment: accounts order against last week's POS, landing with a lag.
    reorder_lag_days_mean: float = 4.0
    reorder_lag_sigma: float = 0.4
    order_noise_sigma: float = 0.15  # lognormal over/under-ordering per line


class CPGWholesaleScenarioConfig(BaseScenarioConfig):
    marketing: MarketingConfig
    catalog: CatalogConfig
    market: MarketConfig
    promos: list[TradePromoConfig] = Field(default_factory=list)
    ordering: OrderingConfig = Field(default_factory=OrderingConfig)

    @model_validator(mode="after")
    def _cross_check(self) -> CPGWholesaleScenarioConfig:
        validate_segment_dims(self.anomalies, SEGMENT_DIMS)
        if self.market.distributors:
            if self.market.independents_banner not in self.market.retailers:
                raise ValueError(
                    f"distributors serve {self.market.independents_banner!r}, "
                    "which is missing from market.retailers"
                )
            total = sum(d.share for d in self.market.distributors.values())
            if abs(total - 1.0) > 1e-6:
                raise ValueError(f"distributor shares must sum to 1.0 (got {total})")
        for promo in self.promos:
            if promo.banner not in self.market.retailers:
                raise ValueError(f"promo {promo.name!r}: unknown banner {promo.banner!r}")
            if promo.category not in self.catalog.categories:
                raise ValueError(f"promo {promo.name!r}: unknown category {promo.category!r}")
            if not (0.0 <= promo.discount_pct < 1.0):
                raise ValueError(f"promo {promo.name!r}: discount_pct must be in [0, 1)")
        return self
