"""Game-agnostic core generator base.

Each generator belongs to a GamePack and produces output for a specific game.
The pipeline orchestrates across packs, but each pack is self-contained.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, TypedDict


@dataclass
class GeneratorOutput:
    """Output produced by a generator — game-agnostic."""
    files: dict[str, dict | list | str] = field(default_factory=dict)
    assets: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def add_file(self, path: str, content: dict | list | str | bytes) -> None:
        """Add a file to the generator output.

        ``content`` may be a dict/list (JSON-serialised in the final zip),
        a ``str`` (written as-is, UTF-8), or ``bytes`` (written raw — used
        for generated PNGs and other binary assets). The packager at
        ``generators/packager.py`` already handles all four types.
        """
        self.files[path] = content

    def add_asset(self, path: str) -> None:
        self.assets.append(path)


class GeneratorInput(TypedDict):
    """Input passed to a generator."""
    prompt: str
    # v42 Blue: mirror of storage.queries.create_mod_request — the ``hint``
    # value is serialised through ``ModRequest.hint: Mapped[dict[str, Any]]``
    # in storage/models/models.py, so the strict shape here matches the
    # downstream storage contract.
    hint: dict[str, Any]
    request_id: str
    game: str
    prior_outputs: dict[str, GeneratorOutput]
    t2_feedback: str


class BaseGenerator(ABC):
    """Abstract base class for all game-specific generators."""

    name: str
    phase: str
    game: str

    @abstractmethod
    async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        """Generate mod content (async to support LLM calls)."""
        ...

    def validate_output(self, output: GeneratorOutput) -> list[str]:
        """Validate generator output. Returns list of error strings."""
        return []
