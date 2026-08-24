"""Compact endogenous dynamics for the persistent knowledge organism.

Durable corpus content is owned by :mod:`storage`; this module owns only the
rebuildable, evolving projection over that content.  A dynamics tick is an
idle/background evolution step.  Ingestion and query events perturb the
current state but never advance time themselves.

The live mechanisms are intentionally small:

* semantic territories: online embedding-centroid clusters;
* transient heat: perturbations activate chunks and decay over idle ticks;
* local diffusion: a bounded fraction of heat spreads over existing graph
  edges each idle tick;
* slow salience/familiarity: query feedback strengthens repeatedly useful
  chunks without changing their source text or semantic edge weights.

There is no TTL deletion, autonomous summarization, frontier splitting,
boredom/churn bookkeeping, or hidden pruning of durable knowledge.  Those
mechanisms existed in the earlier port but either had no caller or changed
knowledge lifetime for reasons unrelated to the user's corpus.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
import math
from typing import Callable, Deque, Dict, Iterable, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class DynamicsConfig:
    heat_half_life_ticks: int = 120
    registration_heat_gain: float = 3.0
    retrieval_heat_gain: float = 0.75
    familiarity_gain: float = 0.08
    mass_gain: float = 0.15
    diffusion_fraction: float = 0.12
    active_budget: int = 256
    heat_floor: float = 1e-4
    territory_similarity_threshold: float = 0.55
    territory_max_members: int = 256

    def validate(self) -> None:
        if self.heat_half_life_ticks < 1:
            raise ValueError("heat_half_life_ticks must be >= 1")
        if self.registration_heat_gain < 0 or self.retrieval_heat_gain < 0:
            raise ValueError("heat gains must be >= 0")
        if self.familiarity_gain < 0 or self.mass_gain < 0:
            raise ValueError("familiarity_gain and mass_gain must be >= 0")
        if not 0.0 <= self.diffusion_fraction < 1.0:
            raise ValueError("diffusion_fraction must be in [0, 1)")
        if self.active_budget < 1:
            raise ValueError("active_budget must be >= 1")
        if self.heat_floor <= 0:
            raise ValueError("heat_floor must be > 0")
        if not -1.0 <= self.territory_similarity_threshold <= 1.0:
            raise ValueError("territory_similarity_threshold must be in [-1, 1]")
        if self.territory_max_members < 1:
            raise ValueError("territory_max_members must be >= 1")


@dataclass
class MemoryState:
    id: str
    created: float
    territory: int
    novelty: float
    familiarity: float = 0.0
    mass: float = 1.0
    heat: float = 0.0
    last_touch_tick: int = 0
    last_decay_tick: int = 0
    use_count: int = 0


@dataclass(frozen=True)
class TerritoryAssignment:
    territory: int
    novelty: float
    nearest_similarity: Optional[float]
    is_new: bool


NeighborProvider = Callable[[str, int], Iterable[Tuple[str, float]]]


class KnowledgeDynamics:
    """Sparse/local evolving state layered over durable chunks."""

    PERSISTENCE_VERSION = 3

    def __init__(self, config: Optional[DynamicsConfig] = None):
        self.config = config or DynamicsConfig()
        self.config.validate()
        self._tick = 0
        self._mem: Dict[str, MemoryState] = {}
        self._territory_centroids: Dict[int, List[float]] = {}
        self._territory_counts: Dict[int, int] = {}
        self._next_territory_id = 1
        self._active_queue: Deque[str] = deque()
        self._active_set: set[str] = set()
        self._total_mass = 0.0
        self._total_familiarity = 0.0
        self._total_heat = 0.0

    @property
    def tick_count(self) -> int:
        return self._tick

    def set_tick(self, tick: int) -> None:
        self._tick = max(0, int(tick))
        for state in self._mem.values():
            state.last_decay_tick = min(state.last_decay_tick, self._tick)
            state.last_touch_tick = min(state.last_touch_tick, self._tick)

    def ids(self) -> set[str]:
        return set(self._mem)

    def get_state(self, memory_id: str) -> Optional[MemoryState]:
        return self._mem.get(memory_id)

    def iter_states(self) -> Iterable[MemoryState]:
        return self._mem.values()

    def choose_territory(self, embedding: Sequence[float]) -> TerritoryAssignment:
        vec = _normalize(embedding)
        if not self._territory_centroids:
            return TerritoryAssignment(self._next_territory_id, 1.0, None, True)

        best_similarity = -2.0
        best_available_tid = -1
        best_available_similarity = -2.0
        for tid, centroid in self._territory_centroids.items():
            sim = cosine_similarity(vec, centroid)
            best_similarity = max(best_similarity, sim)
            if (
                self._territory_counts.get(tid, 0) < self.config.territory_max_members
                and sim > best_available_similarity
            ):
                best_available_similarity = sim
                best_available_tid = tid

        novelty = max(0.0, min(1.0, 1.0 - max(-1.0, min(1.0, best_similarity))))
        if (
            best_available_tid >= 0
            and best_available_similarity >= self.config.territory_similarity_threshold
        ):
            return TerritoryAssignment(
                best_available_tid, novelty, best_available_similarity, False
            )
        return TerritoryAssignment(
            self._next_territory_id, novelty, best_similarity, True
        )

    def nearest_territories(self, embedding: Sequence[float], k: int = 3) -> List[Tuple[int, float]]:
        if k <= 0 or not self._territory_centroids:
            return []
        vec = _normalize(embedding)
        scored = [
            (tid, cosine_similarity(vec, centroid))
            for tid, centroid in self._territory_centroids.items()
        ]
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:k]

    def register_chunk(
        self,
        memory_id: str,
        created: float,
        embedding: Sequence[float],
        *,
        assignment: Optional[TerritoryAssignment] = None,
        cold: bool = False,
    ) -> MemoryState:
        existing = self._mem.get(memory_id)
        if existing is not None:
            return existing

        chosen = assignment or self.choose_territory(embedding)
        vec = _normalize(embedding)
        if chosen.is_new:
            tid = chosen.territory
            self._territory_centroids[tid] = list(vec)
            self._territory_counts[tid] = 1
            self._next_territory_id = max(self._next_territory_id, tid + 1)
        else:
            self._add_to_centroid(chosen.territory, vec)

        heat = 0.0 if cold else self.config.registration_heat_gain * chosen.novelty
        state = MemoryState(
            id=memory_id,
            created=float(created),
            territory=chosen.territory,
            novelty=chosen.novelty,
            heat=heat,
            last_touch_tick=self._tick,
            last_decay_tick=self._tick,
        )
        self._mem[memory_id] = state
        self._total_mass += state.mass
        self._total_heat += heat
        if heat > self.config.heat_floor:
            self._activate(memory_id)
        return state

    def remove_chunk(self, memory_id: str, embedding: Optional[Sequence[float]] = None) -> bool:
        state = self._mem.get(memory_id)
        if state is None:
            return False
        self._decay_state(state, self._tick)
        self._mem.pop(memory_id, None)
        self._active_set.discard(memory_id)
        self._total_mass -= state.mass
        self._total_familiarity -= state.familiarity
        self._total_heat = max(0.0, self._total_heat - state.heat)
        if embedding is not None:
            self._remove_from_centroid(state.territory, _normalize(embedding))
        return True

    def apply_stimuli(self, stimuli: Iterable[Tuple[str, float]]) -> int:
        applied = 0
        for memory_id, raw_strength in stimuli:
            state = self._mem.get(memory_id)
            if state is None:
                continue
            strength = max(0.0, min(1.0, float(raw_strength)))
            if strength <= 0.0:
                continue
            self._decay_state(state, self._tick)
            old_familiarity = state.familiarity
            state.familiarity = min(
                1.0,
                state.familiarity + (1.0 - state.familiarity) * self.config.familiarity_gain * strength,
            )
            self._total_familiarity += state.familiarity - old_familiarity
            mass_delta = self.config.mass_gain * strength
            state.mass += mass_delta
            self._total_mass += mass_delta
            heat_delta = self.config.retrieval_heat_gain * strength
            state.heat += heat_delta
            self._total_heat += heat_delta
            state.use_count += 1
            state.last_touch_tick = self._tick
            self._activate(memory_id)
            applied += 1
        return applied

    def advance(self, neighbor_provider: NeighborProvider) -> None:
        """Advance exactly one endogenous idle tick.

        Work is bounded by ``active_budget``.  Cold chunks are never scanned.
        Heat on active chunks is lazily decayed to the current tick, then a
        bounded fraction is redistributed over strongest graph neighbors.
        """
        self._tick += 1
        self._total_heat *= 0.5 ** (1.0 / self.config.heat_half_life_ticks)
        if not self._active_set:
            return

        budget = min(self.config.active_budget, len(self._active_queue))
        processed: List[str] = []
        incoming: Dict[str, float] = {}

        for _ in range(budget):
            memory_id = self._active_queue.popleft()
            if memory_id not in self._active_set:
                continue
            self._active_set.discard(memory_id)
            state = self._mem.get(memory_id)
            if state is None:
                continue
            self._decay_state(state, self._tick)
            if state.heat <= self.config.heat_floor:
                self._total_heat = max(0.0, self._total_heat - state.heat)
                state.heat = 0.0
                continue

            neighbors = [
                (nid, max(0.0, float(weight)))
                for nid, weight in neighbor_provider(memory_id, 12)
                if nid in self._mem and nid != memory_id and weight > 0.0
            ]
            weight_sum = sum(weight for _, weight in neighbors)
            if weight_sum > 0.0 and self.config.diffusion_fraction > 0.0:
                sent = state.heat * self.config.diffusion_fraction
                state.heat -= sent
                for nid, weight in neighbors:
                    incoming[nid] = incoming.get(nid, 0.0) + sent * (weight / weight_sum)
            processed.append(memory_id)

        for memory_id, delta in incoming.items():
            state = self._mem.get(memory_id)
            if state is None:
                continue
            self._decay_state(state, self._tick)
            state.heat += delta
            if state.heat > self.config.heat_floor:
                self._activate(memory_id)

        for memory_id in processed:
            state = self._mem.get(memory_id)
            if state is not None and state.heat > self.config.heat_floor:
                self._activate(memory_id)

    def rebuild_territories(self, entries: Iterable[Tuple[str, Sequence[float]]]) -> None:
        """Rebuild the derived centroid projection while preserving node dynamics."""
        self._territory_centroids.clear()
        self._territory_counts.clear()
        self._next_territory_id = 1
        for memory_id, embedding in entries:
            state = self._mem.get(memory_id)
            if state is None:
                continue
            assignment = self.choose_territory(embedding)
            vec = _normalize(embedding)
            state.territory = assignment.territory
            if assignment.is_new:
                self._territory_centroids[assignment.territory] = list(vec)
                self._territory_counts[assignment.territory] = 1
                self._next_territory_id = max(self._next_territory_id, assignment.territory + 1)
            else:
                self._add_to_centroid(assignment.territory, vec)

    def state_score(self, memory_id: str) -> float:
        state = self._mem.get(memory_id)
        if state is None:
            return 0.0
        heat = self._effective_heat(state, self._tick)
        mass = state.mass / (state.mass + 5.0)
        heat_term = heat / (heat + 3.0)
        return 0.55 * mass + 0.30 * state.familiarity + 0.15 * heat_term

    def stats(self) -> Dict[str, float]:
        count = len(self._mem)
        total_heat, active_count = self.active_heat()
        return {
            "count": float(count),
            "territories": float(len(self._territory_centroids)),
            "active": float(active_count),
            "total_heat": total_heat,
            "avg_mass": self._total_mass / count if count else 0.0,
            "avg_familiarity": self._total_familiarity / count if count else 0.0,
            "tick": float(self._tick),
        }

    def active_heat(self) -> Tuple[float, int]:
        return max(0.0, self._total_heat), len(self._active_set)

    def to_dict(self) -> dict:
        return {
            "version": self.PERSISTENCE_VERSION,
            "config": asdict(self.config),
            "tick": self._tick,
            "next_territory": self._next_territory_id,
            "mem": {
                memory_id: {
                    "created": state.created,
                    "territory": state.territory,
                    "novelty": state.novelty,
                    "familiarity": state.familiarity,
                    "mass": state.mass,
                    "heat": self._effective_heat(state, self._tick),
                    "last_touch_tick": state.last_touch_tick,
                    "last_decay_tick": self._tick,
                    "use_count": state.use_count,
                }
                for memory_id, state in self._mem.items()
            },
            "territory_centroids": {str(tid): centroid for tid, centroid in self._territory_centroids.items()},
            "territory_counts": {str(tid): count for tid, count in self._territory_counts.items()},
            "active": list(self._active_set),
            "total_heat": self._total_heat,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "KnowledgeDynamics":
        if int(data.get("version", 0)) != cls.PERSISTENCE_VERSION:
            raise ValueError("legacy or unsupported dynamics snapshot")
        config = DynamicsConfig(**dict(data.get("config", {})))
        inst = cls(config)
        inst._tick = max(0, int(data.get("tick", 0)))
        inst._next_territory_id = max(1, int(data.get("next_territory", 1)))
        inst._territory_centroids = {
            int(tid): [float(x) for x in centroid]
            for tid, centroid in dict(data.get("territory_centroids", {})).items()
        }
        inst._territory_counts = {
            int(tid): int(count)
            for tid, count in dict(data.get("territory_counts", {})).items()
        }
        for memory_id, raw in dict(data.get("mem", {})).items():
            state = MemoryState(
                id=memory_id,
                created=float(raw.get("created", 0.0)),
                territory=int(raw.get("territory", 0)),
                novelty=float(raw.get("novelty", 0.0)),
                familiarity=float(raw.get("familiarity", 0.0)),
                mass=float(raw.get("mass", 1.0)),
                heat=float(raw.get("heat", 0.0)),
                last_touch_tick=int(raw.get("last_touch_tick", inst._tick)),
                last_decay_tick=int(raw.get("last_decay_tick", inst._tick)),
                use_count=int(raw.get("use_count", 0)),
            )
            inst._mem[memory_id] = state
            inst._total_mass += state.mass
            inst._total_familiarity += state.familiarity
            inst._total_heat += state.heat
        inst._total_heat = float(data.get("total_heat", inst._total_heat))
        for memory_id in list(data.get("active", [])):
            if memory_id in inst._mem and inst._mem[memory_id].heat > inst.config.heat_floor:
                inst._activate(memory_id)
        for memory_id, state in inst._mem.items():
            if state.heat > inst.config.heat_floor and memory_id not in inst._active_set:
                inst._activate(memory_id)
        return inst

    def _activate(self, memory_id: str) -> None:
        if memory_id in self._active_set:
            return
        self._active_set.add(memory_id)
        self._active_queue.append(memory_id)

    def _effective_heat(self, state: MemoryState, tick: int) -> float:
        dt = max(0, int(tick) - int(state.last_decay_tick))
        if dt == 0 or state.heat <= 0.0:
            return max(0.0, state.heat)
        factor = 0.5 ** (dt / self.config.heat_half_life_ticks)
        return max(0.0, state.heat * factor)

    def _decay_state(self, state: MemoryState, tick: int) -> None:
        state.heat = self._effective_heat(state, tick)
        state.last_decay_tick = int(tick)

    def _add_to_centroid(self, territory: int, vec: Sequence[float]) -> None:
        count = self._territory_counts[territory]
        centroid = self._territory_centroids[territory]
        if len(centroid) != len(vec):
            raise ValueError("embedding dimension mismatch inside territory centroid")
        self._territory_centroids[territory] = [
            (centroid[i] * count + float(vec[i])) / (count + 1)
            for i in range(len(centroid))
        ]
        self._territory_counts[territory] = count + 1

    def _remove_from_centroid(self, territory: int, vec: Sequence[float]) -> None:
        count = self._territory_counts.get(territory, 0)
        centroid = self._territory_centroids.get(territory)
        if count <= 1 or centroid is None:
            self._territory_counts.pop(territory, None)
            self._territory_centroids.pop(territory, None)
            return
        if len(centroid) != len(vec):
            return
        self._territory_centroids[territory] = [
            (centroid[i] * count - float(vec[i])) / (count - 1)
            for i in range(len(centroid))
        ]
        self._territory_counts[territory] = count - 1


def _normalize(values: Sequence[float]) -> List[float]:
    vec = [float(x) for x in values]
    norm_sq = sum(x * x for x in vec)
    if norm_sq <= 0.0:
        return vec
    inv = 1.0 / math.sqrt(norm_sq)
    return [x * inv for x in vec]


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"embedding dimension mismatch: {len(a)} != {len(b)}")
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        xf = float(x)
        yf = float(y)
        dot += xf * yf
        na += xf * xf
        nb += yf * yf
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / math.sqrt(na * nb)


# Compatibility names for code/tests that imported the old port directly.
VoidMemoryManager = KnowledgeDynamics


__all__ = [
    "DynamicsConfig",
    "KnowledgeDynamics",
    "MemoryState",
    "TerritoryAssignment",
    "VoidMemoryManager",
    "cosine_similarity",
]
