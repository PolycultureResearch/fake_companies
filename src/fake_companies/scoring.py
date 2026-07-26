"""Score detector events against ground truth.

Grades a detector (Breakdown RCA hits, Tremor anomaly events, or any list of
timestamped findings) against the ``ground_truth.json`` a run emitted. Matching
is time-based with a +-k day bucket tolerance; when both sides carry a
metric/target label, the label must also be relevant (the detector's metric is
one of the anomaly's affected metrics/signals, or names its target).

Detector events are read leniently: each event contributes a date (from any of
``detected_at`` / ``timestamp`` / ``occurred_at`` / ``date`` / nested
``windows.anomaly.start`` / ``observation.timestamp``) and an optional label
(``metric`` / ``target`` / ``signal`` / nested ``monitor.metric`` / ``entity``).
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

_DATE_KEYS = ("detected_at", "timestamp", "occurred_at", "date", "start", "time")
_LABEL_KEYS = ("metric", "target", "signal", "entity", "name")


def score_events(truth_path: str | Path, events_path: str | Path, tolerance_days: int = 3) -> dict:
    truth = _load_truth(truth_path)
    events = _load_events(events_path)

    matched_gt: set[str] = set()
    matched_ev: set[int] = set()
    matches: list[dict] = []

    for gt in truth:
        for i, ev in enumerate(events):
            if i in matched_ev:
                continue
            if _within(gt, ev, tolerance_days) and _label_ok(gt, ev):
                matched_gt.add(gt["id"])
                matched_ev.add(i)
                matches.append({"gt_id": gt["id"], "event_date": ev["date"].isoformat()})
                break

    n_truth = len(truth)
    n_events = len(events)
    tp = len(matched_gt)
    recall = tp / n_truth if n_truth else 0.0
    precision = len(matched_ev) / n_events if n_events else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    return {
        "tolerance_days": tolerance_days,
        "n_ground_truth": n_truth,
        "n_events": n_events,
        "true_positives": tp,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "missed": sorted({g["id"] for g in truth} - matched_gt),
        "false_positives": [
            events[i]["date"].isoformat() for i in range(n_events) if i not in matched_ev
        ],
        "matches": matches,
    }


def _load_truth(path: str | Path) -> list[dict]:
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict):
        data = data.get("ground_truth", data.get("records", []))
    out = []
    for r in data:
        start = _parse_date(r.get("start") or r.get("start_date"))
        end = _parse_date(r.get("end") or r.get("end_date")) or start
        if start is None:
            continue
        labels = set()
        for key in ("target", "affected_metrics", "affected_signals"):
            v = r.get(key)
            if isinstance(v, str):
                labels.add(v)
            elif isinstance(v, list):
                labels.update(v)
        out.append({"id": r.get("id", str(len(out))), "start": start, "end": end, "labels": labels})
    return out


def _load_events(path: str | Path) -> list[dict]:
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict):
        data = data.get("events", data.get("findings", data.get("anomalies", [])))
    out = []
    for e in data:
        date = _extract_date(e)
        if date is None:
            continue
        out.append({"date": date, "label": _extract_label(e)})
    return out


def _within(gt: dict, ev: dict, k: int) -> bool:
    lo = gt["start"] - dt.timedelta(days=k)
    hi = gt["end"] + dt.timedelta(days=k)
    return lo <= ev["date"] <= hi


def _label_ok(gt: dict, ev: dict) -> bool:
    label = ev.get("label")
    if not label or not gt["labels"]:
        return True  # no label info on one side -> match on time alone
    label = label.lower()
    for gl in gt["labels"]:
        gl = str(gl).lower()
        if label == gl or label in gl or gl in label or label.split(":")[0] in gl:
            return True
    return False


def _extract_date(e: Any) -> dt.date | None:
    if not isinstance(e, dict):
        return None
    for k in _DATE_KEYS:
        if e.get(k):
            d = _parse_date(e[k])
            if d:
                return d
    for path in (("windows", "anomaly", "start"), ("observation", "timestamp")):
        cur: Any = e
        for key in path:
            cur = cur.get(key) if isinstance(cur, dict) else None
        if cur:
            d = _parse_date(cur)
            if d:
                return d
    return None


def _extract_label(e: dict) -> str | None:
    for k in _LABEL_KEYS:
        if k in e and isinstance(e[k], str):
            return e[k]
    monitor = e.get("monitor")
    if isinstance(monitor, dict):
        return monitor.get("metric") or monitor.get("entity")
    return None


def _parse_date(v: Any) -> dt.date | None:
    if v is None:
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    s = str(v).strip().replace("Z", "+00:00")
    try:
        return dt.datetime.fromisoformat(s).date()
    except ValueError:
        try:
            return dt.date.fromisoformat(s[:10])
        except ValueError:
            return None
