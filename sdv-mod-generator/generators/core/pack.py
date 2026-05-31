"""GamePack — a self-contained generator pack for one game.

Each pack contains:
- A manifest (game ID, display name, supported phases)
- Per-phase generator collections
- Game-specific knowledge base loader
- Registry of all generators in the pack

The pipeline imports packs dynamically by game ID.
"""
from abc import ABC
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()


@dataclass
class GameManifest:
    """Metadata for a game pack."""
    game_id: str
    display_name: str
    mod_format: str
    supported_phases: list[str]
    knowledge_dir: Path


@dataclass
class PhaseGenerators:
    """Generators available for a specific phase within a game pack."""
    phase: str
    generators: list[type["BaseGenerator"]]
    execution_order: list[str]


class GamePack(ABC):
    """Base class for a game-specific generator pack.

    Subclass this and implement `get_manifest()` and `list_phases()`.
    """

    manifest: GameManifest

    @classmethod
    def get_manifest(cls) -> GameManifest:
        """Return the game manifest. Override in subclass."""
        raise NotImplementedError

    @classmethod
    def list_phases(cls) -> list[str]:
        """Return list of phase IDs this pack supports."""
        raise NotImplementedError

    @classmethod
    def get_generators(cls, phase: str) -> PhaseGenerators:
        """Return generators for a specific phase, in execution order."""
        raise NotImplementedError

    @classmethod
    def load_knowledge(cls, name: str) -> dict[str, Any]:
        """Load a knowledge JSON file from the pack's knowledge directory."""
        manifest = cls.get_manifest()
        path = manifest.knowledge_dir / f"{name}.json"
        if not path.exists():
            logger.warning("game_pack.knowledge_missing", game=manifest.game_id, name=name)
            return {}
        import json
        with open(path) as f:
            return json.load(f)

    @classmethod
    def get_generator(cls, name: str, phase: str) -> type["BaseGenerator"] | None:
        """Get a specific generator by name and phase."""
        pg = cls.get_generators(phase)
        for gen_cls in pg.generators:
            if gen_cls.name == name:
                return gen_cls
        return None


_GAME_PACKS: dict[str, type[GamePack]] = {}


def register_game_pack(pack_cls: type[GamePack]) -> None:
    manifest = pack_cls.get_manifest()
    _GAME_PACKS[manifest.game_id] = pack_cls
    logger.info("game_pack.registered", game_id=manifest.game_id, phases=manifest.supported_phases)


def get_game_pack(game_id: str) -> type[GamePack] | None:
    return _GAME_PACKS.get(game_id)


def list_game_packs() -> list[str]:
    return list(_GAME_PACKS.keys())


from generators.core.base import BaseGenerator  # noqa: E402