"""Base generator class and data types."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TypedDict


class GeneratorInput(TypedDict):
    """Input passed to a generator."""
    prompt: str
    hint: dict
    request_id: str


@dataclass
class GeneratorOutput:
    """Output produced by a generator."""
    files: dict[str, dict] = field(default_factory=dict)
    assets: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def add_file(self, path: str, content: dict) -> None:
        self.files[path] = content

    def add_asset(self, path: str) -> None:
        self.assets.append(path)


class BaseGenerator(ABC):
    """Abstract base class for all generators."""

    name: str
    phase: str

    @abstractmethod
    def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        """Generate mod content."""
        ...

    @abstractmethod
    def validate_output(self, output: GeneratorOutput) -> list[str]:
        """Validate generator output. Returns list of error strings."""
        ...
