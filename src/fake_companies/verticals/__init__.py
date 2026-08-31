"""Vertical registry: business models the generator can simulate."""

from __future__ import annotations

from .b2b_services import B2BServicesVertical
from .b2c_saas import B2CSaaSVertical
from .base import DQTableMeta, Vertical
from .cpg_wholesale import CPGWholesaleVertical
from .retail_dtc import RetailDTCVertical

REGISTRY: dict[str, Vertical] = {}


def register(vertical: Vertical) -> None:
    REGISTRY[vertical.name] = vertical


def get_vertical(name: str) -> Vertical:
    try:
        return REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown vertical {name!r}; known verticals: {sorted(REGISTRY)}") from None


register(B2CSaaSVertical())
register(RetailDTCVertical())
register(B2BServicesVertical())
register(CPGWholesaleVertical())

__all__ = ["REGISTRY", "DQTableMeta", "Vertical", "get_vertical", "register"]
