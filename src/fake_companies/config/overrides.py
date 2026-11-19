"""Config overrides by dotted path, and named regimes built from them.

An override replaces one value of the scenario mapping after the YAML is
parsed and before it is validated, so an overridden config goes through
exactly the checks a hand-edited file would (unknown keys and out-of-range
dates are still load-time errors). ``--set`` on the CLI and the per-vertical
regime files (``configs/regimes/<vertical>.yaml``) are both this mechanism.

Path grammar: keys joined by dots (``timeline.days``); a list is indexed by
position (``promos.0.uptake``); ``*`` stands for every key of a mapping or
every item of a list that exists at that point
(``traffic.channels.*.growth``). The last key of a path may be new — pydantic
decides whether it is allowed — but every key before it must exist.

A regime is a statement about the baseline shape. It may not touch
``anomalies``: which anomalies a world carries is the job of anomaly
selection, and a regime that changed them would make "the same anomaly under
another regime" mean two things.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

BASELINE_REGIME = "baseline"  # the config as written: no overrides
REGIMES_DIR = Path(__file__).resolve().parents[3] / "configs" / "regimes"


def parse_override(item: str) -> tuple[str, Any]:
    """``"timeline.days=1095"`` -> ``("timeline.days", 1095)`` (value read as YAML)."""
    path, sep, raw = item.partition("=")
    path = path.strip()
    if not sep or not path:
        raise ValueError(f"override {item!r} is not of the form dotted.path=value")
    return path, yaml.safe_load(raw)


def parse_overrides(items: list[str] | None) -> dict[str, Any]:
    return dict(parse_override(i) for i in items or [])


def apply_overrides(data: dict, overrides: dict[str, Any]) -> dict:
    """Return a deep copy of ``data`` with every override applied, in order."""
    out = copy.deepcopy(data)
    for path, value in overrides.items():
        if not _set(out, path.split("."), value, path):
            raise ValueError(f"override {path!r}: the wildcard matched nothing")
    return out


def _set(node: Any, keys: list[str], value: Any, path: str) -> bool:
    """Set ``value`` at ``keys`` under ``node``; return whether anything was set."""
    key, rest = keys[0], keys[1:]
    if key == "*":
        if isinstance(node, dict):
            children: list[Any] = list(node)
        elif isinstance(node, list):
            children = list(range(len(node)))
        else:
            raise ValueError(f"override {path!r}: '*' needs a mapping or a list")
        if not rest:
            for child in children:
                node[child] = copy.deepcopy(value)
            return bool(children)
        hits = [_set(node[child], rest, value, path) for child in children]
        return any(hits)
    if isinstance(node, list):
        try:
            idx = int(key)
            target = node[idx]
        except (ValueError, IndexError):
            raise ValueError(f"override {path!r}: {key!r} is not an index of the list") from None
        if not rest:
            node[idx] = copy.deepcopy(value)
            return True
        return _set(target, rest, value, path)
    if not isinstance(node, dict):
        # ValueError, not TypeError: the caller's path is wrong, and every
        # other bad path raises ValueError too.
        raise ValueError(  # noqa: TRY004
            f"override {path!r}: cannot descend into a scalar at {key!r}"
        )
    if not rest:
        node[key] = copy.deepcopy(value)
        return True
    if node.get(key) is None:
        raise ValueError(f"override {path!r}: no section {key!r} in the config")
    return _set(node[key], rest, value, path)


def load_regimes(vertical: str, regimes_dir: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """The named regimes of a vertical: ``{regime: {dotted.path: value}}``."""
    path = Path(regimes_dir or REGIMES_DIR) / f"{vertical}.yaml"
    regimes: dict[str, dict[str, Any]] = {BASELINE_REGIME: {}}
    if path.exists():
        for name, overrides in (yaml.safe_load(path.read_text()) or {}).items():
            if name == BASELINE_REGIME:
                raise ValueError(
                    f"{path}: {BASELINE_REGIME!r} is reserved for the unmodified config"
                )
            bad = [p for p in overrides if p.split(".")[0] == "anomalies"]
            if bad:
                raise ValueError(
                    f"{path}: regime {name!r} overrides {bad}; a regime may not touch anomalies"
                )
            regimes[name] = dict(overrides)
    return regimes


def regime_overrides(
    vertical: str, regime: str, regimes_dir: str | Path | None = None
) -> dict[str, Any]:
    regimes = load_regimes(vertical, regimes_dir)
    if regime not in regimes:
        raise ValueError(
            f"unknown regime {regime!r} for vertical {vertical!r}; known: {sorted(regimes)}"
        )
    return regimes[regime]
