"""Ground-truth records for injected anomalies.

Every injected event — rate or dq, scripted or surprise-sampled — emits exactly
one :class:`GroundTruthRecord`. These are written to ``meta.ground_truth`` and
``ground_truth.json`` and are the key that ``fake-companies score`` grades a
detector against. Baseline growth/seasonality/noise never produce a record.
"""

from __future__ import annotations

import datetime as dt
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field


class GroundTruthRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str  # "rate" | "dq"
    type: str  # spike | level_shift | volume_dropout | ...
    origin: str = "scripted"  # "scripted" | "surprise"
    target: str  # driver name (rate) or raw table (dq)
    segment: dict[str, str] | None = None
    start: dt.date
    end: dt.date | None = None
    magnitude: float = 1.0
    # Downstream metrics / dataflow signals the event is expected to move.
    affected_metrics: list[str] = Field(default_factory=list)
    affected_signals: list[str] = Field(default_factory=list)
    params: dict = Field(default_factory=dict)

    # --- Root label in consumer terms (rate anomalies; null for dq) --------- #
    # The MetricFlow metric closest to the driver, and the slice of it the
    # anomaly touches. ``root_dimension`` / ``root_value`` are comma-joined
    # when the slice has more than one dimension (``channel,device`` /
    # ``paid_search,mobile``), which is how the measured-effects table spells
    # the same slice; ``root_slice`` is the same thing as a mapping.
    root_metric: str | None = None
    root_dimension: str | None = None
    root_value: str | None = None
    root_slice: dict[str, str] | None = None

    # --- Measured truth (single worlds of a world set only) ----------------- #
    # Filled from the difference between a single world and its null twin; see
    # ``fake_companies.effects``. Absent from an ordinary run's ground truth.
    measured_effects: dict[str, dict] | None = None
    # Every metric whose total moved, declared or not. An empty list is a
    # finding: the anomaly moved no metric (a dq anomaly whose rows staging
    # deduplicates, or one that only delays loading — its truth is in
    # ``affected_signals``).
    metrics_moved: list[str] | None = None
    declared_affected_metrics_confirmed: dict[str, list[str]] | None = None
    root_effect: dict | None = None
    root_metric_confirmed: bool | None = None

    # JSON keys left out of ``ground_truth.json`` while they are None, so an
    # ordinary run's file gains the root label and nothing else.
    MEASURED_FIELDS: ClassVar[tuple[str, ...]] = (
        "measured_effects",
        "metrics_moved",
        "declared_affected_metrics_confirmed",
        "root_effect",
        "root_metric_confirmed",
    )

    def as_json(self) -> dict:
        """The record as written to ``ground_truth.json``."""
        data = self.model_dump(mode="json")
        for key in self.MEASURED_FIELDS:
            if data[key] is None:
                del data[key]
        return data

    def as_row(self) -> dict:
        """Flattened dict for the ``meta.ground_truth`` DuckDB table.

        Deliberately the original twelve columns: the table's bytes are pinned
        by the golden hashes, so the root label and the measured effects live
        in ``ground_truth.json`` only.
        """
        import json

        return {
            "id": self.id,
            "kind": self.kind,
            "type": self.type,
            "origin": self.origin,
            "target": self.target,
            "segment": json.dumps(self.segment) if self.segment else None,
            "start_date": self.start,
            "end_date": self.end,
            "magnitude": self.magnitude,
            "affected_metrics": json.dumps(self.affected_metrics),
            "affected_signals": json.dumps(self.affected_signals),
            "params": json.dumps(self.params),
        }
