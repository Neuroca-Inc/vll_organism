#!/usr/bin/env python3
"""Permutation and omnibus controls for VLL Rung-0 routing experiments."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import math
import random
import statistics

from rung0_common import (
    fraction_from_assignment, graph_degree, real_fraction, run_heat,
)
from rung0_stats import empirical_p_right, percentile

def stable_seed(seed: int, text: str) -> int:
    digest = hashlib.sha256(f"{seed}:{text}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def random_assignment(ids: tuple[str, ...], fixed: set[str], rng: random.Random) -> dict[str, str]:
    positions = [cid for cid in ids if cid not in fixed]
    identities = positions[:]
    rng.shuffle(identities)
    out = {cid: cid for cid in fixed}
    out.update(zip(positions, identities))
    return out


def degree_matched_assignment(corpus, fixed: set[str], rng: random.Random) -> dict[str, str]:
    out = {cid: cid for cid in fixed}
    strata = defaultdict(list)
    for cid in corpus.ids:
        if cid not in fixed:
            strata[graph_degree(corpus, cid)].append(cid)
    for positions in strata.values():
        identities = positions[:]
        rng.shuffle(identities)
        out.update(zip(positions, identities))
    return out


def degree_match_coverage(corpus, origin_ids: set[str], related_ids: set[str]) -> float:
    strata = defaultdict(list)
    for cid in corpus.ids:
        if cid not in origin_ids:
            strata[graph_degree(corpus, cid)].append(cid)
    movable = 0
    total = sum(len(v) for v in strata.values())
    for ids in strata.values():
        labels = {cid in related_ids for cid in ids}
        if len(labels) > 1:
            movable += len(ids)
    return 0.0 if total == 0 else movable / total


def legacy_provider(corpus, position_to_identity: dict[str, str]):
    identity_to_position = {identity: position for position, identity in position_to_identity.items()}

    def provider(memory_id: str, limit: int = 12):
        position = identity_to_position[memory_id]
        return [
            (position_to_identity[nbr], weight)
            for nbr, weight in corpus.graph.weighted_neighbors(position, limit)
            if nbr in position_to_identity
        ]

    return provider


def per_target_controls(corpus, auc, target, origin_ids, related_ids, count, seed):
    rng = random.Random(stable_seed(seed, target))
    values = []
    for _ in range(count):
        assignment = random_assignment(corpus.ids, {target}, rng)
        frac, _origin, _rel, _other = fraction_from_assignment(
            auc, assignment, origin_ids, related_ids
        )
        if frac is not None:
            values.append(frac)
    return values


def fidelity_sentinel(corpus, template, target, auc, origin_ids, related_ids, heat, horizon, seed, trials=3):
    rng = random.Random(stable_seed(seed, "fidelity:" + target))
    errors = []
    for _ in range(trials):
        assignment = random_assignment(corpus.ids, {target}, rng)
        optimized, *_ = fraction_from_assignment(auc, assignment, origin_ids, related_ids)
        legacy = run_heat(
            template, corpus, target, heat, [horizon],
            provider=legacy_provider(corpus, assignment),
        )
        actual, *_ = real_fraction(legacy.node_auc_by_horizon[horizon], origin_ids, related_ids)
        if optimized is None or actual is None:
            if optimized != actual:
                errors.append(float("inf"))
        else:
            errors.append(abs(optimized - actual))
    worst = max(errors or [0.0])
    if worst > 1e-12:
        raise RuntimeError(
            f"optimized permutation control failed runtime-equivalence sentinel: max error={worst}"
        )
    return {"trials": trials, "max_abs_error": worst, "pass": True}


def global_control_matrix(
    corpus, target_aucs, origin_ids, related_ids, count, seed, *,
    degree_matched=False, stream="default",
):
    targets = list(target_aucs)
    matrix = {target: [] for target in targets}
    kind = "degree" if degree_matched else "unmatched"
    rng = random.Random(stable_seed(seed, f"global:{kind}:{stream}"))
    for _ in range(count):
        assignment = (
            degree_matched_assignment(corpus, origin_ids, rng)
            if degree_matched else random_assignment(corpus.ids, origin_ids, rng)
        )
        for target in targets:
            frac, *_ = fraction_from_assignment(
                target_aucs[target], assignment, origin_ids, related_ids
            )
            matrix[target].append(float("nan") if frac is None else frac)
    return matrix


def omnibus_from_matrices(rows, calibration, evaluation, subset, label):
    """Whole-sweep statistic with independent null calibration/evaluation streams.

    Calibration permutations establish each target's null median/q95. A disjoint
    evaluation stream then supplies the null distribution for sweep-level
    statistics. This avoids using one permutation both to set and test its own
    threshold.
    """
    targets = [r["target"] for r in rows if subset(r) and r["target"] in calibration]
    if not targets:
        return {"label": label, "targets": 0, "status": "NOT_APPLICABLE"}
    cal = {t: [x for x in calibration[t] if math.isfinite(x)] for t in targets}
    ev = {t: [x for x in evaluation[t] if math.isfinite(x)] for t in targets}
    cal_n = min(len(cal[t]) for t in targets)
    eval_n = min(len(ev[t]) for t in targets)
    if cal_n == 0 or eval_n == 0:
        return {
            "label": label, "targets": len(targets), "status": "NO_CONTROLS",
            "calibration_controls": cal_n, "evaluation_controls": eval_n,
        }
    med = {t: statistics.median(cal[t]) for t in targets}
    q95 = {t: percentile(cal[t], 0.95) for t in targets}
    real_by_target = {r["target"]: r["real_fraction"] for r in rows if r["target"] in targets}
    real_delta = [real_by_target[t] - med[t] for t in targets]
    real_median_delta = statistics.median(real_delta)
    real_pass_count = sum(real_by_target[t] > q95[t] for t in targets)
    real_max_delta = max(real_delta)

    null_median_delta = []
    null_pass_count = []
    null_max_delta = []
    for j in range(eval_n):
        deltas = [ev[t][j] - med[t] for t in targets]
        null_median_delta.append(statistics.median(deltas))
        null_pass_count.append(sum(ev[t][j] > q95[t] for t in targets))
        null_max_delta.append(max(deltas))

    return {
        "label": label,
        "status": "OK",
        "targets": len(targets),
        "calibration_controls": cal_n,
        "evaluation_controls": eval_n,
        "real_median_delta": real_median_delta,
        "median_delta_null_q95": percentile(null_median_delta, 0.95),
        "median_delta_p_right": empirical_p_right(real_median_delta, null_median_delta),
        "real_pass_count": real_pass_count,
        "pass_count_null_q95": percentile(null_pass_count, 0.95),
        "pass_count_p_right": empirical_p_right(float(real_pass_count), [float(x) for x in null_pass_count]),
        "real_max_delta": real_max_delta,
        "max_delta_null_q95": percentile(null_max_delta, 0.95),
        "max_delta_p_right": empirical_p_right(real_max_delta, null_max_delta),
    }



__all__ = [
    "degree_match_coverage", "degree_matched_assignment", "fidelity_sentinel",
    "global_control_matrix", "legacy_provider", "omnibus_from_matrices",
    "per_target_controls", "random_assignment", "stable_seed",
]
