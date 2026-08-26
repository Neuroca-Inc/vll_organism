"""Research adapters and mechanism execution for HeatMap + HeatScout only."""
from __future__ import annotations
from contextlib import contextmanager
import copy
from itertools import islice
import math
from pathlib import Path
import random
import sys
from typing import Mapping, Sequence

_VENDOR = Path(__file__).resolve().parent / "vendor"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from vdm_rt.core.cortex.maps.heatmap import HeatMap
from vdm_rt.core.cortex.void_walkers.void_heat_scout import HeatScout
from vdm_rt.core.proprioception.events import VTTouchEvent, EdgeOnEvent

from rung1_io import pulse, zero_heat
from rung1_stats import spearman


class ConnectomeAdapter:
    """Integer-handle, read-only local view over a frozen VLL KnowledgeGraph."""
    def __init__(self, corpus: object):
        self.ids = tuple(corpus.ids)
        self.id_to_idx = {cid: i for i, cid in enumerate(self.ids)}
        self.N = len(self.ids)
        self._graph = corpus.graph

    def neighbors(self, u: int) -> list[int]:
        cid = self.ids[int(u)]
        return [
            self.id_to_idx[nid]
            for nid, _weight in self._graph.weighted_neighbors(cid, 12)
            if nid in self.id_to_idx and nid != cid
        ]


class HeatScoreView:
    """Lazy local HeatMap read. No global HeatMap scan is performed by the scout."""
    def __init__(self, heatmap: HeatMap, tick: int):
        self._map = heatmap
        self._tick = int(tick)

    def get(self, node: int, default: float = 0.0) -> float:
        n = int(node)
        if n not in self._map._val:
            return float(default)
        self._map._decay_to(n, self._tick)
        return float(self._map._val.get(n, default))


class ZeroHeatView:
    def get(self, _node: int, default: float = 0.0) -> float:
        return float(default)


class PermutedHeatView:
    """Topology-label shuffle control over an existing lazy HeatMap view."""
    def __init__(self, base: HeatScoreView, permutation: Sequence[int]):
        self._base = base
        self._perm = tuple(int(x) for x in permutation)

    def get(self, node: int, default: float = 0.0) -> float:
        n = int(node)
        if n < 0 or n >= len(self._perm):
            return float(default)
        return self._base.get(self._perm[n], default)


@contextmanager
def seeded_native_softmax(seed: int):
    """Control legacy module-global RNG use without editing vendored HeatScout."""
    state = random.getstate()
    random.seed(int(seed))
    try:
        yield
    finally:
        random.setstate(state)


def _processed_activity_events(dynamics: object, id_to_idx: Mapping[str, int]) -> list[VTTouchEvent]:
    """Observe only the exact queue prefix that the next V0 tick can process."""
    next_tick = dynamics.tick_count + 1
    budget = min(dynamics.config.active_budget, len(dynamics._active_queue))
    events: list[VTTouchEvent] = []
    for cid in islice(dynamics._active_queue, 0, budget):
        if cid not in dynamics._active_set:
            continue
        state = dynamics.get_state(cid)
        if state is None:
            continue
        h = float(dynamics._effective_heat(state, next_tick))
        if h <= 0.0:
            continue
        events.append(
            VTTouchEvent(kind="vt_touch", t=next_tick, token=int(id_to_idx[cid]), w=h)
        )
    return events


def build_heat_field(
    template: dict,
    corpus: object,
    connectome: ConnectomeAdapter,
    target_id: str,
    plan: dict,
) -> dict:
    """Generate one controlled V0 field and its event-folded HeatMap projection."""
    from vll_organism.dynamics import KnowledgeDynamics

    dynamics = KnowledgeDynamics.from_dict(copy.deepcopy(template))
    zero_heat(dynamics)
    pulse(dynamics, target_id, float(plan["pulse_heat"]))
    hc = plan["heatmap"]
    heatmap = HeatMap(
        head_k=int(hc["head_k"]),
        half_life_ticks=int(hc["half_life_ticks"]),
        keep_max=int(hc["keep_max"]),
        seed=int(plan["seed"]),
        vt_touch_gain=float(hc["vt_touch_gain"]),
        spike_gain=float(hc["spike_gain"]),
        dW_gain=float(hc["dW_gain"]),
    )
    max_active = dynamics.active_heat()[1]
    event_count = 0
    for _ in range(int(plan["warmup_ticks"])):
        events = _processed_activity_events(dynamics, connectome.id_to_idx)
        event_count += len(events)
        next_tick = dynamics.tick_count + 1
        dynamics.advance(corpus.graph.weighted_neighbors)
        heatmap.fold(events, next_tick)
        max_active = max(max_active, dynamics.active_heat()[1])

    tick = dynamics.tick_count
    # Evaluator-only readout. This global projection never enters HeatScout.
    authoritative_heat = {
        connectome.id_to_idx[state.id]: float(dynamics._effective_heat(state, tick))
        for state in dynamics.iter_states()
    }
    return {
        "heatmap": heatmap,
        "heat_view": HeatScoreView(heatmap, tick),
        "authoritative_heat": authoritative_heat,
        "tick": tick,
        "event_count": event_count,
        "max_active": max_active,
        "total_heat": float(dynamics.active_heat()[0]),
    }


def heatmap_signal_for_target(
    target_idx: int,
    connectome: ConnectomeAdapter,
    field: dict,
) -> dict:
    neighbors = connectome.neighbors(target_idx)
    view = field["heat_view"]
    truth = field["authoritative_heat"]
    map_values = [view.get(n, 0.0) for n in neighbors]
    true_values = [float(truth.get(n, 0.0)) for n in neighbors]
    rho = spearman(map_values, true_values)
    spread = max(map_values) - min(map_values) if map_values else 0.0
    return {
        "neighbor_count": len(neighbors),
        "spearman": rho,
        "map_spread": spread,
        "informative": bool(len(neighbors) >= 2 and spread > 1e-12 and rho is not None),
    }


def make_permutation(n: int, seed: int) -> list[int]:
    p = list(range(int(n)))
    random.Random(int(seed)).shuffle(p)
    return p


def run_heat_scout(
    connectome: ConnectomeAdapter,
    start_idx: int,
    heat_view: object,
    plan: dict,
    seed: int,
) -> dict:
    sc = plan["heatscout"]
    budget = plan["walker_budget"]
    scout = HeatScout(
        budget_visits=int(budget["visits"]),
        budget_edges=int(budget["edges"]),
        ttl=int(budget["ttl"]),
        seed=int(seed),
        theta_mem=0.0,
        rho_trail=0.0,
        gamma_heat=float(sc["gamma_heat"]),
        tau=float(sc["tau"]),
    )
    maps = {"heat_head": [], "heat_dict": heat_view}
    with seeded_native_softmax(seed):
        events = scout.step(
            connectome,
            maps=maps,
            budget={
                "visits": int(budget["visits"]),
                "edges": int(budget["edges"]),
                "ttl": int(budget["ttl"]),
                "tick": 0,
                "seeds": [int(start_idx)],
            },
        )
    edges = [(int(e.u), int(e.v)) for e in events if isinstance(e, EdgeOnEvent)]
    touches = [int(e.token) for e in events if isinstance(e, VTTouchEvent)]
    return {"edges": edges, "touches": touches, "events": events}


def matched_path_metrics(
    paths: dict[str, dict],
    truth: Mapping[int, float],
    corpus: object,
    connectome: ConnectomeAdapter,
    origin_source: str,
    related_sources: set[str],
) -> dict[str, dict]:
    edge_counts = [len(paths[name]["edges"]) for name in ("blind", "real", "shuffled")]
    matched_edges = min(edge_counts) if edge_counts else 0
    out: dict[str, dict] = {}
    for name, path in paths.items():
        destinations = [v for _u, v in path["edges"][:matched_edges]]
        heats = [float(truth.get(v, 0.0)) for v in destinations]
        unique = set(destinations)
        unique_heat = sum(float(truth.get(v, 0.0)) for v in unique)
        related = 0
        other_foreign = 0
        same_source = 0
        for v in destinations:
            cid = connectome.ids[v]
            sources = corpus.source_sets.get(cid, frozenset())
            if origin_source in sources:
                same_source += 1
            elif related_sources.intersection(sources):
                related += 1
            else:
                other_foreign += 1
        foreign = related + other_foreign
        out[name] = {
            "matched_edges": matched_edges,
            "realized_edges": len(path["edges"]),
            "mean_destination_heat": (sum(heats) / matched_edges) if matched_edges else None,
            "unique_heat_per_edge": (unique_heat / matched_edges) if matched_edges else None,
            "related_fraction_foreign": (related / foreign) if foreign else None,
            "related_edges": related,
            "other_foreign_edges": other_foreign,
            "same_source_edges": same_source,
        }
    return out


class ForkConnectome:
    N = 3
    _adj = {0: (1, 2), 1: (0,), 2: (0,)}
    def neighbors(self, u: int) -> list[int]:
        return list(self._adj.get(int(u), ()))


class DictView:
    def __init__(self, values: Mapping[int, float]):
        self.values = dict(values)
    def get(self, node: int, default: float = 0.0) -> float:
        return float(self.values.get(int(node), default))


def synthetic_choice_gate(plan: dict) -> tuple[list[dict], dict]:
    trials = int(plan["synthetic"]["trials_per_condition"])
    deltas = [float(x) for x in plan["synthetic"]["heat_deltas"]]
    gamma = float(plan["heatscout"]["gamma_heat"])
    tau = float(plan["heatscout"]["tau"])
    rows = []
    max_error = 0.0
    for ci, delta in enumerate(deltas):
        # H_A - H_B = delta with nonnegative HeatMap-like scores.
        ha = max(delta, 0.0)
        hb = max(-delta, 0.0)
        choose_a = 0
        for trial in range(trials):
            seed = int(plan["seed"]) + ci * 10_000_000 + trial
            result = run_heat_scout(ForkConnectome(), 0, DictView({1: ha, 2: hb}), plan, seed)
            if result["edges"] and result["edges"][0][1] == 1:
                choose_a += 1
        observed = choose_a / trials
        predicted = 1.0 / (1.0 + math.exp(-(gamma * delta) / tau))
        error = abs(observed - predicted)
        max_error = max(max_error, error)
        rows.append({
            "delta_heat": delta, "predicted_p_A": predicted, "observed_p_A": observed,
            "abs_error": error, "trials": trials,
        })
    tol = float(plan["synthetic"]["max_abs_error"])
    return rows, {"max_abs_error": max_error, "tolerance": tol, "pass": max_error <= tol}
