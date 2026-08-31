"""B2C SaaS vertical: sessions → signups → trials → subscriptions → MRR.

Currently delegates to the modules the pre-split generator used (latent.build,
entities, anomalies registries); those move into this package in the follow-up
migration PR. Method-level imports keep the registry import-time acyclic.
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


class B2CSaaSVertical:
    name = "b2c_saas"

    def config_model(self) -> type[BaseScenarioConfig]:
        from .config import B2CSaaSScenarioConfig

        return B2CSaaSScenarioConfig

    def tables(self) -> list[TableSpec]:
        from ...output.schemas import RAW_TABLES

        return list(RAW_TABLES)

    def segment_dims(self) -> frozenset[str]:
        from .config import SEGMENT_DIMS

        return SEGMENT_DIMS

    def known_drivers(self, cfg: BaseScenarioConfig) -> set[str]:
        from ...latent.build import known_drivers

        return known_drivers(cfg)

    def build_drivers(self, cfg: BaseScenarioConfig, cal: Calendar, rng: RngHub) -> DriverPanel:
        from ...latent.build import build_drivers

        return build_drivers(cfg, cal, rng)

    def build_entities(
        self,
        cfg: BaseScenarioConfig,
        cal: Calendar,
        rng: RngHub,
        panel: DriverPanel,
        frames: dict[str, pd.DataFrame],
    ) -> None:
        from ... import entities

        entities.build_all(cfg, cal, rng, panel, frames)

    def dq_targets(self) -> dict[str, DQTableMeta]:
        from ...anomalies import DQ_TABLE_META

        return DQ_TABLE_META

    def dq_surprise_tables(self) -> list[str]:
        from ...anomalies import _DQ_SURPRISE_TABLES

        return list(_DQ_SURPRISE_TABLES)

    def affected_metrics(self, driver: str) -> list[str]:
        from ...anomalies import affected_metrics_for_driver

        return affected_metrics_for_driver(driver)

    def dbt_project(self) -> str:
        return "b2c_saas"
