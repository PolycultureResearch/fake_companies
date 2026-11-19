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

from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class RootLabel:
    """Where a rate anomaly first shows up, in the consumer's terms.

    ``metric`` is the MetricFlow metric closest to the driver. ``slice`` maps
    semantic-layer dimension names to values and narrows the metric to the
    part the anomaly touches: the driver's own member (``spend.paid_social``
    -> ``channel: paid_social``) and then the anomaly's ``segment``. An empty
    slice means the metric's total.
    """

    metric: str
    slice: dict[str, str] = field(default_factory=dict)

    @property
    def dimension(self) -> str | None:
        """The slice's dimension(s), comma-joined (``channel,device``)."""
        return ",".join(self.slice) or None

    @property
    def value(self) -> str | None:
        """The slice's value(s), comma-joined in the same order."""
        return ",".join(self.slice.values()) or None


# One row of a vertical's driver -> root table:
# (driver or "prefix.", root metric, dimension of the driver's own member or
# None, {segment key: semantic dimension} for the segment dims its entity
# builder honors).
RootRule = tuple[str, str, str | None, dict[str, str]]


def resolve_root(rules: list[RootRule], driver: str, segment: dict[str, str] | None) -> RootLabel:
    """Look ``driver`` up in a vertical's root table and build its label."""
    for prefix, metric, member_dim, segment_dims in rules:
        if prefix.endswith("."):
            if not driver.startswith(prefix):
                continue
            member = driver[len(prefix) :]
        elif driver == prefix:
            member = None
        else:
            continue
        where: dict[str, str] = {}
        if member_dim is not None and member is not None:
            where[member_dim] = member
        for key in sorted(segment or {}):
            dim = segment_dims.get(key)
            if dim is not None and dim not in where:
                where[dim] = segment[key]  # type: ignore[index]
        return RootLabel(metric, where)
    raise KeyError(f"driver {driver!r} has no root metric")


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

    def root_label(self, driver: str, segment: dict[str, str] | None = None) -> RootLabel:
        """The metric (and slice of it) a rate anomaly on ``driver`` moves first."""
        ...

    def dbt_project(self) -> str:
        """Directory name of this vertical's dbt project under ``dbt/``."""
        ...
