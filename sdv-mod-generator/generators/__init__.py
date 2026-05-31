"""Generators package.

Packs are imported first to self-register with the core registry.
"""
from generators.core import (
    BaseGenerator,
    GeneratorInput,
    GeneratorOutput,
    GamePack,
    get_game_pack,
    list_game_packs,
)
import generators.packs

__all__ = [
    "BaseGenerator",
    "GeneratorInput",
    "GeneratorOutput",
    "GamePack",
    "get_game_pack",
    "list_game_packs",
    "generators",
]