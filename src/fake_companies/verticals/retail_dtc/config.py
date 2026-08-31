"""Retail DTC scenario config: the web envelope plus shop sections."""

from __future__ import annotations

from pydantic import Field, model_validator

from ...config.schema import Window, _Base, validate_segment_dims
from ...config.sections import WebScenarioConfig

# Segment dimensions a retail rate anomaly (or per-segment override) may filter on.
SEGMENT_DIMS = frozenset({"country", "device", "channel", "category"})

_DEFAULT_COLORS = ["black", "navy", "moss", "sand", "rust"]
_DEFAULT_SIZES = ["xs", "s", "m", "l", "xl"]


class CategoryConfig(_Base):
    n_skus: int
    price_min: float
    price_max: float
    unit_cost_ratio: float = 0.45  # unit_cost = price * ratio
    popularity: float = 1.0  # relative weight in basket category sampling
    colors: list[str] = Field(default_factory=lambda: list(_DEFAULT_COLORS))
    sizes: list[str] = Field(default_factory=lambda: list(_DEFAULT_SIZES))


class CatalogConfig(_Base):
    categories: dict[str, CategoryConfig]


class ConversionConfig(_Base):
    order_rate: dict[str, float]  # per-channel P(session -> order)
    order_rate_default: float = 0.015  # channels absent from order_rate
    # Static per-device multipliers on the session->order probability
    # (checkout friction differs by device; keep the mix-weighted mean ~1).
    device_multipliers: dict[str, float] = Field(default_factory=dict)


class BasketConfig(_Base):
    items_per_order_mean: float = 1.6  # mean line items per order (>= 1)
    extra_quantity_rate: float = 0.12  # P(a line has quantity 2 instead of 1)
    free_shipping_threshold: float | None = 75.0  # None = shipping always charged
    shipping_fee: float = 6.95


class RepeatConfig(_Base):
    monthly_repeat_rate: float = 0.10  # P(an existing customer orders in a month)
    frailty_sigma: float = 0.7  # per-customer lognormal frailty on repeat hazard
    channel_mix: dict[str, float]  # channel attribution of repeat orders


class ReturnsConfig(_Base):
    rate: dict[str, float]  # per-category P(a delivered line is returned)
    rate_default: float = 0.06
    reason_mix: dict[str, float] = Field(
        default_factory=lambda: {
            "fit": 0.45,
            "changed_mind": 0.30,
            "damaged": 0.15,
            "quality": 0.10,
        }
    )
    request_delay_days_mean: float = 9.0  # days from delivery to return request
    request_delay_sigma: float = 0.6
    processing_days_mean: float = 4.0  # days from request to refund
    processing_sigma: float = 0.4


class FulfillmentConfig(_Base):
    handling_days_mean: float = 1.2  # order placed -> shipped
    handling_sigma: float = 0.5
    transit_days_mean: float = 3.5  # shipped -> delivered
    transit_sigma: float = 0.4
    carrier_mix: dict[str, float] = Field(
        default_factory=lambda: {"ups": 0.45, "usps": 0.35, "fedex": 0.20}
    )


class RetailPaymentsConfig(_Base):
    failure_rate: float = 0.02  # P(first charge attempt fails)
    method_mix: dict[str, float] = Field(
        default_factory=lambda: {"card": 0.72, "paypal": 0.16, "apple_pay": 0.12}
    )
    failure_codes: dict[str, float] = Field(
        default_factory=lambda: {"card_declined": 0.7, "insufficient_funds": 0.3}
    )


class PromoConfig(_Base):
    code: str
    window: Window
    discount_pct: float  # 0.2 = 20% off the merchandise subtotal
    uptake: float = 0.3  # P(an in-window order uses the code)


class RetailDTCScenarioConfig(WebScenarioConfig):
    catalog: CatalogConfig
    conversion: ConversionConfig
    basket: BasketConfig = Field(default_factory=BasketConfig)
    repeat: RepeatConfig
    returns: ReturnsConfig
    fulfillment: FulfillmentConfig = Field(default_factory=FulfillmentConfig)
    payments: RetailPaymentsConfig = Field(default_factory=RetailPaymentsConfig)
    promos: list[PromoConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_segments(self) -> RetailDTCScenarioConfig:
        validate_segment_dims(self.anomalies, SEGMENT_DIMS)
        for promo in self.promos:
            if not (0.0 < promo.discount_pct < 1.0):
                raise ValueError(f"promo {promo.code!r}: discount_pct must be in (0, 1)")
        return self
