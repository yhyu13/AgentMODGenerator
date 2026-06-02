"""Game-agnostic core generator base.

Each generator belongs to a GamePack and produces output for a specific game.
The pipeline orchestrates across packs, but each pack is self-contained.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TypedDict


@dataclass
class GeneratorOutput:
    """Output produced by a generator — game-agnostic."""
    files: dict[str, dict | str] = field(default_factory=dict)
    assets: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def add_file(self, path: str, content: dict) -> None:
        self.files[path] = content

    def add_asset(self, path: str) -> None:
        self.assets.append(path)


class GeneratorInput(TypedDict):
    """Input passed to a generator."""
    prompt: str
    hint: dict
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
