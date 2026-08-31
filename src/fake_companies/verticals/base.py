"""The ``Vertical`` protocol: seam between the generic engine and a business model.

The generic engine (calendar, RNG, latent panel + rate anomalies, corruption,
output, ground truth) is vertical-agnostic. Everything business-model-specific
— config sections, table schemas, the latent driver catalog, entity builders,
DQ targets, and the driver→metric impact map — is owned by a vertical package
under ``fake_companies.verticals.<name>`` and exposed through this protocol.

``generate.py`` resolves the vertical from ``company.vertical`` and drives the
whole pipeline through these methods; nothing outside a vertical package may
hardcode business concepts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import pandas as pd

    from ..config.schema import BaseScenarioConfig
    from ..core.calendar import Calendar
    from ..core.rng import RngHub
    from ..latent.panel import DriverPanel
    from ..output.schemas import TableSpec

# Which raw columns a dq event can target, per table.
# Keys: "categorical" | "nullable" | "numeric".
DQTableMeta = dict[str, list[str]]


class Vertical(Protocol):
    """A simulated business model (b2c_saas, retail_dtc, ...)."""

    name: str

    def config_model(self) -> type[BaseScenarioConfig]:
        """The scenario-config subclass this vertical's YAML validates against."""
        ...

    def tables(self) -> list[TableSpec]:
        """Raw table specs, in loading order (order feeds RNG draws — stable!)."""
        ...

    def segment_dims(self) -> frozenset[str]:
        """Dimensions a rate anomaly's ``segment:`` filter may use."""
        ...

    def known_drivers(self, cfg: BaseScenarioConfig) -> set[str]:
        """Every latent driver name ``build_drivers`` will emit (anomaly targets)."""
        ...

    def build_drivers(self, cfg: BaseScenarioConfig, cal: Calendar, rng: RngHub) -> DriverPanel:
        """Layer 1: the daily latent rate panel for this business model."""
        ...

    def build_entities(
        self,
        cfg: BaseScenarioConfig,
        cal: Calendar,
        rng: RngHub,
        panel: DriverPanel,
        frames: dict[str, pd.DataFrame],
    ) -> None:
        """Layer 2: populate raw entity frames (one per table fqn) from the panel."""
        ...

    def dq_targets(self) -> dict[str, DQTableMeta]:
        """Per-table column classes a dq anomaly may corrupt."""
        ...

    def dq_surprise_tables(self) -> list[str]:
        """Tables eligible as surprise-dq targets."""
        ...

    def affected_metrics(self, driver: str) -> list[str]:
        """Downstream MetricFlow metrics a rate anomaly on ``driver`` moves."""
        ...

    def dbt_project(self) -> str:
        """Directory name of this vertical's dbt project under ``dbt/``."""
        ...
