"""Generator registry — legacy, superseded by GamePack system.

The pipeline now uses get_game_pack() from generators.core instead.
This module is kept for potential debugging/ad-hoc use.
"""
from generators.base import BaseGenerator

_GENERATOR_REGISTRY: dict[str, type[BaseGenerator]] = {}


def register(name: str, cls: type[BaseGenerator]) -> None:
    _GENERATOR_REGISTRY[name] = cls


def get(name: str) -> type[BaseGenerator] | None:
    return _GENERATOR_REGISTRY.get(name)


def list_generators() -> list[str]:
    return list(_GENERATOR_REGISTRY.keys())
