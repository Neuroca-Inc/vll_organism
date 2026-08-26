"""Dependency-free statistics for the Rung-1 HeatMap/HeatScout package."""
from __future__ import annotations
import math
import random
from statistics import median
from typing import Iterable, Sequence


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def quantile(xs: Sequence[float], q: float) -> float:
    if not xs:
        return float("nan")
    ys = sorted(float(x) for x in xs)
    if len(ys) == 1:
        return ys[0]
    pos = max(0.0, min(1.0, q)) * (len(ys) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ys[lo]
    frac = pos - lo
    return ys[lo] * (1.0 - frac) + ys[hi] * frac


def _ranks(xs: Sequence[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: (float(xs[i]), i))
    ranks = [0.0] * len(xs)
    k = 0
    while k < len(order):
        j = k + 1
        value = float(xs[order[k]])
        while j < len(order) and float(xs[order[j]]) == value:
            j += 1
        avg_rank = 0.5 * ((k + 1) + j)
        for p in range(k, j):
            ranks[order[p]] = avg_rank
        k = j
    return ranks


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    rx = _ranks(xs)
    ry = _ranks(ys)
    mx = mean(rx)
    my = mean(ry)
    dx = [x - mx for x in rx]
    dy = [y - my for y in ry]
    vx = sum(x * x for x in dx)
    vy = sum(y * y for y in dy)
    if vx <= 0.0 or vy <= 0.0:
        return None
    return sum(a * b for a, b in zip(dx, dy)) / math.sqrt(vx * vy)


def sign_flip_p_one_sided(
    deltas: Sequence[float], *, controls: int, seed: int
) -> tuple[float, float, int]:
    vals = [float(x) for x in deltas if math.isfinite(float(x))]
    if not vals:
        return 1.0, float("nan"), 0
    observed = median(vals)
    rng = random.Random(int(seed))
    exceed = 0
    for _ in range(int(controls)):
        null = median([x if rng.random() < 0.5 else -x for x in vals])
        if null >= observed:
            exceed += 1
    p = (exceed + 1.0) / (int(controls) + 1.0)
    return p, observed, exceed


def holm_adjust(named_p: Iterable[tuple[str, float]]) -> dict[str, float]:
    items = sorted((str(name), float(p)) for name, p in named_p)
    items.sort(key=lambda x: x[1])
    m = len(items)
    out: dict[str, float] = {}
    running = 0.0
    for i, (name, p) in enumerate(items):
        adjusted = min(1.0, (m - i) * p)
        running = max(running, adjusted)
        out[name] = running
    return out
