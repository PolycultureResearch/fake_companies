"""B2B services vertical: leads → pipeline stages → contracts → invoices.

A sales-led professional-services firm: no web layer, no subscriptions —
deals progress through stage gates with lognormal durations, wins become
project contracts billed upfront/on-delivery. The generic engine drives it
through the :class:`fake_companies.verticals.base.Vertical` protocol.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

    from ...config.schema import BaseScenarioConfig
    from ...core.calendar import Calendar
    from ...core.rng import RngHub
    from ...latent.panel import DriverPanel
    from ...output.schemas import TableSpec
    from ..base import DQTableMeta


class B2BServicesVertical:
    name = "b2b_services"

    def config_model(self) -> type[BaseScenarioConfig]:
        from .config import B2BServicesScenarioConfig

        return B2BServicesScenarioConfig

    def tables(self) -> list[TableSpec]:
        from .tables import RAW_TABLES

        return list(RAW_TABLES)

    def segment_dims(self) -> frozenset[str]:
        from .config import SEGMENT_DIMS

        return SEGMENT_DIMS

    def known_drivers(self, cfg: BaseScenarioConfig) -> set[str]:
        from .drivers import known_drivers

        return known_drivers(cfg)

    def build_drivers(self, cfg: BaseScenarioConfig, cal: Calendar, rng: RngHub) -> DriverPanel:
        from .drivers import build_drivers

        return build_drivers(cfg, cal, rng)

    def build_entities(
        self,
        cfg: BaseScenarioConfig,
        cal: Calendar,
        rng: RngHub,
        panel: DriverPanel,
        frames: dict[str, pd.DataFrame],
    ) -> None:
        from . import entities

        entities.build_all(cfg, cal, rng, panel, frames)

    def dq_targets(self) -> dict[str, DQTableMeta]:
        from .dq import DQ_TABLE_META

        return DQ_TABLE_META

    def dq_surprise_tables(self) -> list[str]:
        from .dq import DQ_SURPRISE_TABLES

        return list(DQ_SURPRISE_TABLES)

    def affected_metrics(self, driver: str) -> list[str]:
        from .metrics import affected_metrics_for_driver

        return affected_metrics_for_driver(driver)

    def dbt_project(self) -> str:
        return "b2b_services"
