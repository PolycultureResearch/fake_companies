from __future__ import annotations

import json

from fake_companies.config import load_config
from fake_companies.generate import generate
from fake_companies.output.manifest import write_json_sidecars
from fake_companies.scoring import score_events


def _write_truth(tmp_path, r):
    write_json_sidecars(tmp_path, r.manifest, r.ground_truth)
    return tmp_path / "ground_truth.json"


def test_perfect_detector(tmp_path):
    cfg = load_config("configs/smoke_90d.yaml")
    r = generate(cfg)
    truth_path = _write_truth(tmp_path, r)

    # A detector that fires exactly on every ground-truth start date.
    events = [
        {"detected_at": rec.start.isoformat(), "metric": rec.target} for rec in r.ground_truth
    ]
    ev_path = tmp_path / "events.json"
    ev_path.write_text(json.dumps(events))

    report = score_events(truth_path, ev_path, tolerance_days=3)
    assert report["recall"] == 1.0
    assert report["precision"] == 1.0
    assert report["f1"] == 1.0
    assert report["missed"] == []


def test_tolerance_window(tmp_path):
    import datetime as dt

    cfg = load_config("configs/smoke_90d.yaml")
    r = generate(cfg)
    truth_path = _write_truth(tmp_path, r)
    rec = r.ground_truth[0]

    # Fire 2 days AFTER the window end: inside the +-3 bucket, outside +-1.
    end = rec.end or rec.start
    late = (end + dt.timedelta(days=2)).isoformat()
    ev_path = tmp_path / "events.json"
    ev_path.write_text(json.dumps([{"date": late, "metric": rec.target}]))

    assert score_events(truth_path, ev_path, tolerance_days=3)["true_positives"] == 1
    assert score_events(truth_path, ev_path, tolerance_days=1)["true_positives"] == 0


def test_false_positive_and_miss(tmp_path):
    cfg = load_config("configs/smoke_90d.yaml")
    r = generate(cfg)
    truth_path = _write_truth(tmp_path, r)

    # One spurious event far from any anomaly; nothing near real anomalies.
    ev_path = tmp_path / "events.json"
    ev_path.write_text(json.dumps([{"date": "2024-01-01"}]))

    report = score_events(truth_path, ev_path, tolerance_days=3)
    assert report["precision"] == 0.0  # the lone event matches nothing
    assert report["recall"] == 0.0
    assert len(report["false_positives"]) == 1
    assert set(report["missed"]) == {rec.id for rec in r.ground_truth}


def test_tremor_style_event_shape(tmp_path):
    cfg = load_config("configs/smoke_90d.yaml")
    r = generate(cfg)
    truth_path = _write_truth(tmp_path, r)
    dq = next(rec for rec in r.ground_truth if rec.kind == "dq")

    # Tremor AnomalyEvent-ish nested shape.
    events = [
        {
            "detected_at": dq.start.isoformat() + "T06:00:00Z",
            "monitor": {"metric": "volume", "entity": dq.target},
            "windows": {"anomaly": {"start": dq.start.isoformat()}},
        }
    ]
    ev_path = tmp_path / "events.json"
    ev_path.write_text(json.dumps(events))
    report = score_events(truth_path, ev_path, tolerance_days=3)
    assert report["true_positives"] >= 1
