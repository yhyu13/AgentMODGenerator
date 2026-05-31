"""Backward-compatibility shim — prefer generators.core."""
from generators.core import BaseGenerator, GeneratorInput, GeneratorOutput

__all__ = ["BaseGenerator", "GeneratorInput", "GeneratorOutput"]