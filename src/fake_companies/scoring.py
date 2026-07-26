"""Score detector events against ground truth. Fully implemented in M7."""

from __future__ import annotations

from pathlib import Path


def score_events(truth_path: str | Path, events_path: str | Path, tolerance_days: int = 3) -> dict:
    raise NotImplementedError("scoring is implemented in milestone M7")
