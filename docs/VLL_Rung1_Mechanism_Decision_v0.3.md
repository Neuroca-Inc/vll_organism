# VLL Rung-1 Mechanism Decision v0.3
## Cortex audit, CF04 provenance correction, and the graph-transport admission gate

### Executive decision

**Do not implement `τ v_t = D L u - v` on the VLL semantic graph and call it CF04 yet.**

CF04 remains the strongest first *candidate family* for a capability-changing Rung-1 experiment because it introduces retained transport memory while leaving semantics and topology fixed. But the Complete Formalism establishes a continuum Cattaneo–Vernotte / telegrapher closure on a spatial field. VLL Rung 0 is an irregular sparse semantic graph. Replacing the continuum Laplacian `Δ` with a graph operator `L` is an additional derivation/discretization choice that the current Rung-1 plan had silently promoted to established structure.

That bridge is load-bearing. It must be made explicit and validated before the Rung-0 battery is replayed.

The immediate next rung is therefore split into two checkpoints:

1. **R1a: CF04-derived graph transport bridge** — derive and validate the smallest sparse/local graph analogue of finite-relaxation transport.
2. **R1b: VLL capability replay** — only after R1a passes, compare that admitted transport law against frozen V0 on the unchanged Rung-0 battery.

This preserves the mechanism-isolation program without pretending the continuum-to-graph transfer is already canonical.

---

# 1. Evidence lock from the current Cortex snapshot

The supplied `cortex.zip` contains 27 files. All 27 match the supplied repository `PROVENANCE_manifest.json` by both SHA-256 and byte size. No Cortex file is missing from the manifest and no file differs.

This resolves several placement questions.

## 1.1 Cortex is not the field-transport layer

The current Cortex subtree is organized around:

- bounded, read-only local walkers/scouts;
- foldable events such as `vt_touch` and `edge_on`;
- bounded event-reducer maps;
- per-tick local traversal with TTL / visit / edge budgets;
- no global scans or dense conversions.

The scout runner is explicitly stateless and called once per model tick. It does not own a background scheduler or a transport PDE.

Therefore a CF04-derived field/flux law should **not** be inserted into Cortex merely because Cortex contains a class named `HeatMap`.

## 1.2 Cortex `HeatMap` is not VLL heat transport

`core/cortex/maps/heatmap.py` is a recency-weighted activity reducer. It folds `VTTouchEvent`, `SpikeEvent`, and `DeltaWEvent` into a bounded decaying map. It does not diffuse a conserved scalar over the connectome and it does not implement a Laplacian or flux law.

Treating it as the insertion point for CF04 would conflate telemetry/cartography with transport dynamics.

## 1.3 RE-VGSP and GDSP are compatibility exports here

`core/cortex/scouts.py` explicitly labels `GDSPActuator` and `RevGSP` as **“re-exported from core.neuroplasticity (for legacy imports)”**. The imports are optional and degrade to `None` when unavailable.

This is direct architectural evidence that their appearance in the Cortex facade does not give them current Cortex custody. They remain historical/proxy mechanisms unless separately revalidated.

---

# 2. The provenance defect in v0.1/v0.2

CF04 establishes, in the continuum,

\[
\partial_t u + \nabla\cdot J = 0,
\]

\[
\tau\,\partial_t J + J = -D\nabla u,
\]

which implies

\[
\tau\,\partial_{tt}u + \partial_t u = D\Delta u.
\]

The previous Rung-1 document wrote instead

\[
\tau v_t = D L u-v
\]

on the VLL graph.

That step contains at least three unstated choices:

1. what graph operator replaces `Δ`;
2. what metric gives graph distance the meaning needed for a characteristic speed;
3. what time discretization preserves the causal/local property being imported from CF04.

Those choices are not cosmetic implementation details. They determine what mechanism is actually being tested.

The old statement is therefore withdrawn as an established implementation requirement.

---

# 3. R1a: derive from local edge flux, not from a guessed matrix substitution

The least assumptive graph analogue begins at the same level as CF04: **continuity plus relaxing flux**.

For every undirected retained edge `{i,j}`, choose one orientation for storage and maintain an antisymmetric edge flux

\[
J_{ij}=-J_{ji}.
\]

For node state `u_i`, use local balance

\[
\dot u_i=-\sum_{j\in N(i)}J_{ij}.
\]

For each retained edge, test the relaxation law

\[
\tau\dot J_{ij}+J_{ij}
= D\,a_{ij}(u_i-u_j),
\]

where `a_ij` is an explicitly declared nonnegative edge conductance. If the adjacency-only Rung-0 result is to remain isolated from exact cosine magnitudes, the first admissible bridge should also include a **uniform-conductance control** rather than silently identifying embedding similarity with physical conductance.

Eliminating the edge flux gives

\[
\tau\ddot u_i+\dot u_i
= D\sum_{j\in N(i)}a_{ij}(u_j-u_i).
\]

Define the graph diffusion operator explicitly as

\[
(\Delta_Gu)_i
=\sum_{j\in N(i)}a_{ij}(u_j-u_i).
\]

Then

\[
\tau\ddot u+\dot u=D\Delta_Gu.
\]

This earns the graph operator from the local flux law rather than inserting a symbol `L` by analogy. If the positive-semidefinite combinatorial Laplacian `L=D_g-A` is used in code, the equivalent equation is

\[
\tau\ddot u+\dot u=-D Lu.
\]

The sign convention must be explicit in tests and documentation.

### Status

This is a **scoped graph-transport derivation for the VLL experiment**, not a claim that the continuum CF04 finite-domain theorem automatically transfers unchanged to arbitrary graphs.

---

# 4. What survives cleanly on a graph

## 4.1 Local flux memory

The new state variable `J_ij` carries finite relaxation memory at retained edges. This is the direct mechanism-level addition relative to ordinary instantaneous graph diffusion.

## 4.2 Diffusive limit

As `τ` becomes small relative to the observed time scale,

\[
J_{ij}\rightarrow D a_{ij}(u_i-u_j),
\]

and the node dynamics approach the declared graph-diffusion reference.

This is a valid R1a admission target.

## 4.3 Spectral analogue

For an eigenmode of the positive graph Laplacian with eigenvalue `λ`, the graph equation gives the mode relation

\[
\tau\omega^2+i\omega-D\lambda=0.
\]

Thus the continuum CF04 discriminator `|k|^2` has a clean graph spectral analogue `λ` once the operator convention is fixed. R1a can test whether the retained relaxation state produces the predicted slow diffusive branch plus the fast/transient branch.

This is a stronger validation target than merely observing that propagation “looks delayed.”

---

# 5. What must NOT be imported unchanged from the continuum theorem

## 5.1 `c = sqrt(D/τ)` is not yet a graph speed in hop/tick units

The continuum speed

\[
c=\sqrt{D/\tau}
\]

has meaning relative to a spatial metric. The semantic graph currently supplies connectivity and weights, but no established physical edge lengths.

Therefore R1a must not report “measured graph front speed agrees with `sqrt(D/τ)`” until a graph-length convention has been justified independently.

The first graph experiment may report:

- hop-wise first arrival;
- weighted-path first arrival under a declared metric;
- spectral relaxation behavior;
- support expansion per discrete update;
- convergence to the graph-diffusion reference.

But it must not silently equate hops with continuum distance.

## 5.2 Continuous-time graph coupling does not automatically inherit the continuum sharp cone

A semidiscrete graph wave/telegraph ODE generated by a graph Laplacian does not, by the continuum theorem alone, earn exact compact support at every positive real time. Matrix functions of a connected graph operator can have nonzero long-range entries even though influence is strongly local in structure.

A discrete local update can enforce an **algorithmic hop cone** because one update reads only the current node and its immediate neighbors. That is useful and testable, but it is a property of the chosen discrete execution rule, not a free transfer of the continuum domain-of-dependence theorem.

R1a must distinguish these two claims.

---

# 6. R1a admission gates

The graph bridge is admitted only if all of the following pass before any semantic/cognitive interpretation.

## G0 — Mechanism isolation

Frozen VLL graph and corpus. No edge creation/deletion. No embedding change. No semantic scoring change. No Cortex map/scout insertion. No RE-VGSP, GDSP, ADC, or SIE dependency.

## G1 — Locality audit

Every state update must be explainable from the node, incident retained edges, and retained local flux state. No dense/global graph pass may be required by the transport step.

## G2 — Balance / conservation test

With decay/reaction disabled and closed boundaries, edge transfers must conserve total transported scalar to numerical tolerance:

\[
\sum_i u_i(t)=\text{constant}.
\]

If the intended VLL heat law includes explicit dissipation, test transport conservation separately from the declared sink term.

## G3 — Diffusive-limit recovery

For decreasing `τ`, compare against the **declared graph-diffusion reference on the same graph**, not vaguely against historical V0 behavior. Required outputs should include trajectory error and source-level AUC error over a fixed horizon.

Only after the graph transport converges to its own diffusion reference should equivalence or non-equivalence to V0 be measured.

## G4 — Spectral relaxation signature

On synthetic sparse graphs with known Laplacian eigenpairs, excite selected modes and fit the measured complex response against

\[
\tau\omega^2+i\omega-D\lambda=0.
\]

Predeclare fit tolerance before the run.

## G5 — Discrete support/local-front audit

Under the actual one-step implementation, verify the maximum newly reachable hop shell per model update. If the integrator performs multiple internal substeps, record the corresponding maximum support expansion explicitly.

Call this a **discrete hop cone**, not the continuum CF04 cone.

## G6 — Stability sweep

Sweep `τ`, `D`, update interval, graph degree, and edge-weight scale across the intended operating range. Reject parameter regions that create numerical instability or require dense rescue logic.

## G7 — Conductance ablation

Run at least:

- uniform `a_ij=1` on retained adjacency;
- declared weighted `a_ij` if weighted transport is later desired.

This prevents embedding-cosine magnitude from being silently reintroduced as a new causal variable after Rung 0 already showed adjacency dominates exact weight magnitude.

---

# 7. R1b: only then replay the frozen VLL battery

After R1a passes, define

\[
V_1 = V_0 + M_1,
\]

where `M1` is named precisely as the **admitted CF04-derived local flux-relaxation graph transport**, not merely “CF04.”

Replay the frozen R0 battery without retuning:

- Origin → Transition;
- Transition → Origin;
- 1-hop / 2-hop / 3+ strata;
- source routing matrix;
- amplitude robustness;
- temporal horizons;
- zero-transport control;
- ingestion-order sensitivity checkpoints where relevant.

Primary comparison outputs:

- first arrival by source;
- peak-arrival time;
- source AUC;
- hop-shell support by tick;
- spectral/transient diagnostics;
- V1−V0 routing-fraction delta;
- V1−V0 arrival-time delta.

The admission question is not “did V1 get smarter?”

It is:

\[
\boxed{
M_1\mid V_0 \;\longrightarrow\; \Delta_1
}
\]

What observable signature is causally attributable to retained finite-relaxation transport, and what previously measured behaviors remain unchanged?

---

# 8. Cortex placement after this audit

The current Cortex architecture should remain untouched for R1a/R1b.

Its current role is downstream/local observation and traversal:

\[
\text{persistent state/topology}
\rightarrow
\text{local walker reads}
\rightarrow
\text{events}
\rightarrow
\text{bounded maps}
\rightarrow
\text{later modulation/action}
\]

A later rung can test what happens when an admitted transport field becomes readable by scouts/maps, but doing that now would add two mechanisms at once: a new transport law and a new observation/coupling path.

---

# 9. Current branch status

### Resolved

- Rung 0 is characterized sufficiently to leave the baseline rung.
- RE-VGSP/GDSP are not default forward-ladder authority.
- Current Cortex is a bounded local traversal/event-reducer subsystem, not the CF04 transport owner.
- Cortex `HeatMap` is not the VLL transport field.
- The continuum-to-semantic-graph transfer was not earned in v0.1/v0.2.

### Active boundary

Derive and validate the smallest graph-local flux-relaxation mechanism that preserves the part of CF04 actually needed for mechanism isolation: retained flux memory, local transport, spectral relaxation, and a controlled diffusion limit.

### Deferred

- exact physical metric interpretation of semantic edges;
- claiming continuum characteristic speed on the semantic graph;
- coupling the new field into Cortex scouts/maps;
- topology plasticity;
- ADC/SIE/Phase/Orthad additions;
- replacement mechanisms for historical RE-VGSP/GDSP responsibilities.

These remain open until they can change the next justified move.

---

# 10. Immediate engineering checkpoint

Before editing the live VLL daemon, build a **research-only sparse edge-flux adapter** beside `KnowledgeDynamics` with:

1. frozen graph input;
2. node scalar `u`;
3. retained per-edge antisymmetric flux `J`;
4. explicit `D`, `τ`, update interval, and conductance convention;
5. no topology mutation;
6. local incident-edge updates only;
7. deterministic seed/config receipt;
8. conservation test;
9. diffusion-reference test;
10. eigenmode/spectral test on synthetic graphs;
11. hop-support audit;
12. stability sweep.

Only when this adapter passes R1a should it touch the frozen semantic routing battery.

That is the next earned move.
