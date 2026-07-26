"""Deterministic random-number management.

All randomness in the generator flows from the scenario ``seed`` through named
:class:`RngHub` streams. A named stream always maps to the same independent
``numpy.random.Generator`` for a given seed, so the same (seed, config) pair
yields byte-identical output regardless of call order across modules.

Never call ``np.random.*`` module functions or construct ad-hoc generators.
"""

from __future__ import annotations

import hashlib

import numpy as np


def _name_to_int(name: str) -> int:
    """Map a stream name to a stable 128-bit integer (order-independent)."""
    digest = hashlib.sha256(name.encode("utf-8")).digest()[:16]
    return int.from_bytes(digest, "big")


class RngHub:
    """Vends independent, reproducible generators keyed by name.

    Streams are derived by spawning child ``SeedSequence`` objects from the root
    seed combined with a stable hash of the stream name. Because the entropy is
    ``[seed, hash(name)]`` rather than a spawn counter, adding a new stream never
    perturbs existing streams.
    """

    def __init__(self, seed: int):
        self._seed = int(seed)
        self._cache: dict[str, np.random.Generator] = {}

    @property
    def seed(self) -> int:
        return self._seed

    def stream(self, name: str) -> np.random.Generator:
        """Return the generator for ``name`` (cached; stable across the process)."""
        gen = self._cache.get(name)
        if gen is None:
            seq = np.random.SeedSequence(entropy=[self._seed, _name_to_int(name)])
            gen = np.random.default_rng(seq)
            self._cache[name] = gen
        return gen

    def spawn(self, name: str, n: int) -> list[np.random.Generator]:
        """Return ``n`` independent generators under a namespace ``name``.

        Useful for per-partition parallel-safe draws (e.g. one generator per
        cohort) that must not depend on iteration order elsewhere.
        """
        seq = np.random.SeedSequence(entropy=[self._seed, _name_to_int(name)])
        return [np.random.default_rng(s) for s in seq.spawn(n)]
