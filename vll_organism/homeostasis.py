"""Settle detection for the compact organism.

The earlier build imported a Hellinger/territory-mass signal from VDM_RT even
though this tool did not have live territory-mass dynamics.  Homeostasis here
tracks the mechanism that actually exists: transient activation.  A system is
settled only after the active frontier has drained for a consecutive window.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, List, Optional


@dataclass(frozen=True)
class HomeostasisSample:
    tick: int
    heat_energy: float
    mass_variance_energy: float
    total_energy: float
    delta_frac: Optional[float]
    structural_speed: Optional[float]
    topo_events: int
    settled: bool
    active_count: int = 0


class HomeostasisTracker:
    def __init__(self, window: int = 8, quiet_heat: float = 1e-3):
        if window < 1:
            raise ValueError("homeostasis window must be >= 1")
        if quiet_heat < 0:
            raise ValueError("quiet_heat must be >= 0")
        self.window = int(window)
        self.quiet_heat = float(quiet_heat)
        self._history: Deque[HomeostasisSample] = deque(maxlen=self.window)
        self._last_energy: Optional[float] = None

    def record(self, tick: int, total_heat: float, active_count: int) -> HomeostasisSample:
        heat = max(0.0, float(total_heat))
        energy = 0.5 * heat * heat
        delta = None
        if self._last_energy is not None:
            delta = abs(energy - self._last_energy)
        self._last_energy = energy
        settled = int(active_count) == 0 and heat <= self.quiet_heat
        sample = HomeostasisSample(
            tick=int(tick),
            heat_energy=energy,
            mass_variance_energy=0.0,
            total_energy=energy,
            delta_frac=delta,
            structural_speed=None,
            topo_events=0,
            settled=settled,
            active_count=int(active_count),
        )
        self._history.append(sample)
        return sample

    def is_settled(self) -> bool:
        return len(self._history) == self.window and all(sample.settled for sample in self._history)

    def invalidate(self) -> None:
        self._history.clear()
        self._last_energy = None

    def history(self) -> List[HomeostasisSample]:
        return list(self._history)


__all__ = ["HomeostasisTracker", "HomeostasisSample"]
