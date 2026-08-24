#!/usr/bin/env python3
"""Small, dependency-free statistical helpers for permutation experiments."""
from __future__ import annotations

import math
import statistics
from typing import Iterable, Sequence


def percentile(values: Sequence[float], p: float) -> float:
    if not values:
        return float("nan")
    xs = sorted(float(x) for x in values)
    if p <= 0:
        return xs[0]
    if p >= 1:
        return xs[-1]
    k = (len(xs) - 1) * p
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return xs[lo]
    return xs[lo] * (hi - k) + xs[hi] * (k - lo)


def empirical_p_right(real: float, controls: Sequence[float]) -> float:
    return (1 + sum(float(x) >= real for x in controls)) / (len(controls) + 1)


def empirical_p_left(real: float, controls: Sequence[float]) -> float:
    return (1 + sum(float(x) <= real for x in controls)) / (len(controls) + 1)


def empirical_p_two_sided(real: float, controls: Sequence[float]) -> float:
    if not controls:
        return float("nan")
    p = 2.0 * min(empirical_p_right(real, controls), empirical_p_left(real, controls))
    return min(1.0, p)


def null_summary(real: float | None, controls: Sequence[float]) -> dict:
    if real is None or not controls:
        return {
            "null_median": None,
            "null_q05": None,
            "null_q95": None,
            "delta": None,
            "effect_ratio": None,
            "p_right": None,
            "p_left": None,
            "p_two_sided": None,
        }
    med = statistics.median(controls)
    effect = None if med == 0.0 else real / med
    return {
        "null_median": med,
        "null_q05": percentile(controls, 0.05),
        "null_q95": percentile(controls, 0.95),
        "delta": real - med,
        "effect_ratio": effect,
        "p_right": empirical_p_right(real, controls),
        "p_left": empirical_p_left(real, controls),
        "p_two_sided": empirical_p_two_sided(real, controls),
    }


def benjamini_hochberg(p_values: Sequence[float | None]) -> list[float | None]:
    valid = [(i, float(p)) for i, p in enumerate(p_values) if p is not None and math.isfinite(float(p))]
    result: list[float | None] = [None] * len(p_values)
    if not valid:
        return result
    ordered = sorted(valid, key=lambda item: item[1])
    m = len(ordered)
    running = 1.0
    adjusted = [0.0] * m
    for rev_i in range(m - 1, -1, -1):
        _idx, p = ordered[rev_i]
        rank = rev_i + 1
        running = min(running, p * m / rank)
        adjusted[rev_i] = min(1.0, running)
    for (idx, _p), q in zip(ordered, adjusted):
        result[idx] = q
    return result


def holm_bonferroni(p_values: Sequence[float | None]) -> list[float | None]:
    valid = [(i, float(p)) for i, p in enumerate(p_values) if p is not None and math.isfinite(float(p))]
    result: list[float | None] = [None] * len(p_values)
    if not valid:
        return result
    ordered = sorted(valid, key=lambda item: item[1])
    m = len(ordered)
    running = 0.0
    for rank0, (idx, p) in enumerate(ordered):
        adjusted = min(1.0, (m - rank0) * p)
        running = max(running, adjusted)
        result[idx] = running
    return result


def finite_median(values: Iterable[float | None]) -> float | None:
    xs = [float(x) for x in values if x is not None and math.isfinite(float(x))]
    return statistics.median(xs) if xs else None


def finite_mean(values: Iterable[float | None]) -> float | None:
    xs = [float(x) for x in values if x is not None and math.isfinite(float(x))]
    return sum(xs) / len(xs) if xs else None


def format_optional(value: float | int | None, digits: int = 6) -> str:
    if value is None:
        return "NA"
    if isinstance(value, int):
        return str(value)
    if not math.isfinite(float(value)):
        return str(value)
    return f"{float(value):.{digits}f}"
