"""Explicit, traceable spell-chain design; no implicit OWL inference."""

from .engine import design
from .capabilities import infer_capabilities

__all__ = ["design", "infer_capabilities"]
