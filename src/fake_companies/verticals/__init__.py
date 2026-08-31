"""Vertical registry: business models the generator can simulate."""

from __future__ import annotations

from .b2c_saas import B2CSaaSVertical
from .base import DQTableMeta, Vertical

REGISTRY: dict[str, Vertical] = {}


def register(vertical: Vertical) -> None:
    REGISTRY[vertical.name] = vertical


def get_vertical(name: str) -> Vertical:
    try:
        return REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown vertical {name!r}; known verticals: {sorted(REGISTRY)}") from None


register(B2CSaaSVertical())

__all__ = ["REGISTRY", "DQTableMeta", "Vertical", "get_vertical", "register"]
