"""Anomaly selection on ``generate`` (eval-worlds spec 4.1).

``all`` is today's behaviour and is pinned by the golden and determinism
suites; these tests cover ``none`` / ``only:`` and the claim that selection
cannot disturb any other random stream.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from fake_companies.anomalies import resolve_anomalies, select_anomalies
from fake_companies.config import load_config
from fake_companies.config.schema import MinMax, SurpriseConfig
from fake_companies.core import RngHub, build_calendar
from fake_companies.generate import generate
from fake_companies.verticals import get_vertical

REPO_ROOT = Path(__file__).resolve().parents[1]
SMOKES = ["smoke_90d", "smoke_retail_90d", "smoke_b2b_90d", "smoke_cpg_90d"]


def _cfg(name: str):
    return load_config(REPO_ROOT / "configs" / f"{name}.yaml")


def _frames_equal(a, b) -> bool:
    return set(a.frames) == set(b.frames) and all(a.frames[k].equals(b.frames[k]) for k in a.frames)


def test_all_returns_the_config_itself(smoke_cfg):
    # The default path must not even copy the config: byte-identity by construction.
    assert select_anomalies(smoke_cfg, "all") is smoke_cfg


@pytest.mark.parametrize("name", SMOKES)
def test_none_is_a_null_world(name):
    cfg = _cfg(name)
    result = generate(cfg, anomalies="none")
    assert result.ground_truth == []
    assert result.manifest["n_ground_truth"] == "0"
    assert result.manifest["anomaly_selection"] == "none"
    assert result.cfg.anomalies.scripted == [] and result.cfg.anomalies.surprise is None
    assert cfg.anomalies.scripted, "selection must not mutate the caller's config"


def test_only_keeps_the_named_anomaly(smoke_cfg):
    first, second = (a.name for a in smoke_cfg.anomalies.scripted[:2])
    result = generate(smoke_cfg, anomalies=f"only:{second}")
    assert [r.id for r in result.ground_truth] == [second]
    both = generate(smoke_cfg, anomalies=f"only:{second},{first}")
    assert sorted(r.id for r in both.ground_truth) == sorted([first, second])


@pytest.mark.parametrize("selection", ["only:", "only:not_an_anomaly", "some", ""])
def test_bad_selection_is_refused(smoke_cfg, selection):
    with pytest.raises(ValueError, match="anomal"):
        select_anomalies(smoke_cfg, selection)


def test_default_manifest_does_not_mention_selection(smoke_cfg):
    manifest = generate(smoke_cfg).manifest
    assert "anomaly_selection" not in manifest
    assert "overrides" not in manifest and "regime" not in manifest


@pytest.mark.parametrize("name", SMOKES)
def test_selecting_every_scripted_anomaly_equals_all(name):
    """No smoke config samples surprises, so ``only:<every name>`` is ``all``."""
    cfg = _cfg(name)
    assert cfg.anomalies.surprise is None
    names = ",".join(a.name for a in cfg.anomalies.scripted)
    assert _frames_equal(generate(cfg), generate(cfg, anomalies=f"only:{names}"))


def test_skipping_surprise_sampling_leaves_every_other_stream_untouched(smoke_cfg):
    """Surprise sampling draws only from its own stream.

    Run A samples two surprises. Run B never touches the surprise stream: the
    anomalies A sampled are handed to it as scripted ones and selected by
    name. If sampling perturbed any other stream, B's rows would differ.
    """
    surprise = SurpriseConfig(count=2, magnitude=MinMax(min=0.5, max=0.8), min_gap_days=10)
    with_surprise = smoke_cfg.model_copy(
        update={"anomalies": smoke_cfg.anomalies.model_copy(update={"surprise": surprise})}
    )
    run_a = generate(with_surprise)
    sampled = resolve_anomalies(
        with_surprise,
        build_calendar(with_surprise),
        RngHub(with_surprise.seed),
        get_vertical(with_surprise.company.vertical),
    )
    assert sum(r.origin == "surprise" for r in sampled) == 2

    scripted = [r.spec for r in sampled]
    as_scripted = smoke_cfg.model_copy(
        update={"anomalies": smoke_cfg.anomalies.model_copy(update={"scripted": scripted})}
    )
    run_b = generate(as_scripted, anomalies="only:" + ",".join(a.name for a in scripted))
    assert [r.id for r in run_a.ground_truth] == [r.id for r in run_b.ground_truth]
    assert _frames_equal(run_a, run_b)


def test_streams_are_independent_by_name():
    used, fresh = RngHub(7), RngHub(7)
    used.stream("anomalies.surprise").random(1000)
    used.stream("dq").random(1000)
    for name in ("traffic", "loading", "noise.spend.paid_search"):
        assert np.array_equal(used.stream(name).random(50), fresh.stream(name).random(50))
