"""Compatibility import for the pre-recovery module name.

The earlier project port put ingestion, TTL pruning, dormant retrieval hooks,
territory mutation, and copied VDM experiments in this module.  The live
implementation now resides in :mod:`vll_organism.dynamics`; this shim keeps
existing imports working without preserving the old mechanisms.
"""
from .dynamics import (
    DynamicsConfig,
    KnowledgeDynamics,
    MemoryState,
    TerritoryAssignment,
    VoidMemoryManager,
    cosine_similarity,
)

__all__ = [
    "DynamicsConfig",
    "KnowledgeDynamics",
    "MemoryState",
    "TerritoryAssignment",
    "VoidMemoryManager",
    "cosine_similarity",
]
