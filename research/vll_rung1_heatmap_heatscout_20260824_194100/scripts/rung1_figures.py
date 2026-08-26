"""Small dependency-free SVG figures for Rung-1 decision outputs."""
from __future__ import annotations
from pathlib import Path
from typing import Sequence

W, H = 900, 560
L, R, T, B = 90, 35, 45, 75


def _esc(text: object) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _write(path: Path, body: list[str], title: str, xlabel: str, ylabel: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{W/2}" y="25" text-anchor="middle" font-size="18">{_esc(title)}</text>',
        f'<line x1="{L}" y1="{H-B}" x2="{W-R}" y2="{H-B}" stroke="black"/>',
        f'<line x1="{L}" y1="{T}" x2="{L}" y2="{H-B}" stroke="black"/>',
        f'<text x="{W/2}" y="{H-20}" text-anchor="middle" font-size="14">{_esc(xlabel)}</text>',
        f'<text x="20" y="{H/2}" text-anchor="middle" font-size="14" transform="rotate(-90 20 {H/2})">{_esc(ylabel)}</text>',
    ]
    parts.extend(body)
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


def _xy(x: float, y: float, xmin: float, xmax: float, ymin: float, ymax: float) -> tuple[float, float]:
    px = L + (x - xmin) / max(1e-12, xmax - xmin) * (W - L - R)
    py = H - B - (y - ymin) / max(1e-12, ymax - ymin) * (H - T - B)
    return px, py


def synthetic_choice(rows: Sequence[dict], path: Path) -> None:
    xs = [float(r["delta_heat"]) for r in rows]
    xmin, xmax = min(xs), max(xs)
    body = []
    pred_points = []
    for r in rows:
        x = float(r["delta_heat"])
        p = float(r["predicted_p_A"])
        o = float(r["observed_p_A"])
        px, py = _xy(x, p, xmin, xmax, 0.0, 1.0)
        pred_points.append(f"{px:.2f},{py:.2f}")
        ox, oy = _xy(x, o, xmin, xmax, 0.0, 1.0)
        body.append(f'<circle cx="{ox:.2f}" cy="{oy:.2f}" r="5" fill="black"/>')
    body.append(f'<polyline points="{" ".join(pred_points)}" fill="none" stroke="black" stroke-dasharray="8,5"/>')
    body.append(f'<text x="{W-R-230}" y="{T+20}" font-size="13">circles: observed; dashed: predicted</text>')
    _write(path, body, "Synthetic HeatScout choice-law gate", "H_A - H_B", "P(choose A)")


def signal_gate(rows: Sequence[dict], path: Path) -> None:
    usable = [r for r in rows if r.get("spearman") is not None]
    n = max(1, len(usable))
    body = []
    for i, r in enumerate(usable):
        x = float(i + 1)
        y = float(r["spearman"])
        px, py = _xy(x, y, 1, n, -1.0, 1.0)
        body.append(f'<circle cx="{px:.2f}" cy="{py:.2f}" r="3.5" fill="black"/>')
    _, z = _xy(1, 0.0, 1, n, -1.0, 1.0)
    body.append(f'<line x1="{L}" y1="{z:.2f}" x2="{W-R}" y2="{z:.2f}" stroke="black" stroke-dasharray="3,5"/>')
    _write(path, body, "V0 activity retained by HeatMap at local neighbor decisions", "target index", "Spearman rho")


def attention_deltas(rows: Sequence[dict], path: Path) -> None:
    body = []
    n = max(1, len(rows))
    ys = []
    for r in rows:
        ys.extend([float(r["delta_real_blind"]), float(r["delta_real_shuffled"])])
    lim = max(1e-9, max(abs(y) for y in ys)) if ys else 1.0
    for i, r in enumerate(rows):
        x = float(i + 1)
        for offset, key, shape in ((-0.18, "delta_real_blind", "circle"), (0.18, "delta_real_shuffled", "square")):
            y = float(r[key])
            px, py = _xy(x + offset, y, 0.5, n + 0.5, -lim, lim)
            if shape == "circle":
                body.append(f'<circle cx="{px:.2f}" cy="{py:.2f}" r="3.3" fill="black"/>')
            else:
                body.append(f'<rect x="{px-3:.2f}" y="{py-3:.2f}" width="6" height="6" fill="none" stroke="black"/>')
    _, z = _xy(1, 0.0, 0.5, n + 0.5, -lim, lim)
    body.append(f'<line x1="{L}" y1="{z:.2f}" x2="{W-R}" y2="{z:.2f}" stroke="black" stroke-dasharray="4,5"/>')
    body.append(f'<text x="{W-R-260}" y="{T+20}" font-size="13">filled: real-blind; open square: real-shuffled</text>')
    _write(path, body, "Target-level counterfactual local-attention gain", "target index", "delta mean destination V0 heat")


def semantic_secondary(rows: Sequence[dict], path: Path) -> None:
    usable = [r for r in rows if r.get("semantic_delta_real_blind") is not None]
    n = max(1, len(usable))
    body = []
    for i, r in enumerate(usable):
        y = float(r["semantic_delta_real_blind"])
        px, py = _xy(i + 1, y, 1, n, -1.0, 1.0)
        body.append(f'<circle cx="{px:.2f}" cy="{py:.2f}" r="3.5" fill="black"/>')
    _, z = _xy(1, 0.0, 1, n, -1.0, 1.0)
    body.append(f'<line x1="{L}" y1="{z:.2f}" x2="{W-R}" y2="{z:.2f}" stroke="black" stroke-dasharray="4,5"/>')
    _write(path, body, "Secondary semantic characterization (not an admission gate)", "target index", "delta related fraction: real - blind")
