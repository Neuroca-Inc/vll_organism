"""Research compatibility subset of the VDM event schema.

The dataclass fields match the event types consumed/emitted by the vendored
HeatMap and HeatScout sources. This shim avoids importing unrelated VDM runtime
configuration into the isolated VLL research package.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Optional

@dataclass(frozen=True)
class BaseEvent:
    kind: str
    t: Optional[int] = None

@dataclass(frozen=True)
class VTTouchEvent(BaseEvent):
    token: Any = ""
    w: float = 1.0

@dataclass(frozen=True)
class EdgeOnEvent(BaseEvent):
    u: int = 0
    v: int = 0
    affinity: Optional[float] = None

@dataclass(frozen=True)
class SpikeEvent(BaseEvent):
    node: int = 0
    amp: float = 1.0
    sign: int = +1

@dataclass(frozen=True)
class DeltaWEvent(BaseEvent):
    node: int = 0
    dw: float = 0.0

__all__ = [
    "BaseEvent", "VTTouchEvent", "EdgeOnEvent", "SpikeEvent", "DeltaWEvent"
]
