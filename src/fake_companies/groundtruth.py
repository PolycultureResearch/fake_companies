"""Ground-truth records for injected anomalies.

Every injected event — rate or dq, scripted or surprise-sampled — emits exactly
one :class:`GroundTruthRecord`. These are written to ``meta.ground_truth`` and
``ground_truth.json`` and are the key that ``fake-companies score`` grades a
detector against. Baseline growth/seasonality/noise never produce a record.
"""

from __future__ import annotations

import datetime as dt

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

    def as_row(self) -> dict:
        """Flattened dict for the ``meta.ground_truth`` DuckDB table."""
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
