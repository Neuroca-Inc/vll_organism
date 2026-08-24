# VLL Rung-1 Mechanism Decision v0.1
## From completed Rung-0 battery to the first native VDM addition

### Executive decision

The first capability-changing VDM mechanism to test against frozen VLL Rung 0 should be **CF04 Telegraph–Fisher finite-speed transport**, not SIE, GDSP, RE-VGSP, ADC, or Orthad.

This is not a claim that CF04 is the most important VDM mechanism. It is the first mechanism found in the audited lineage that:

1. has a complete formal statement and falsifiers;
2. maps directly onto a capability already present in V0 (heat transport over a sparse graph);
3. can be introduced without first requiring SIE reward, causal spike timing, ADC territory state, or GDSP structural decisions;
4. has a clean diffusive limit, so V1 can be compared continuously against V0 rather than replacing the system wholesale;
5. predicts specific observables before implementation.

### Why the other obvious candidates are not first

#### RE-VGSP
The retained VDM material describes RE-VGSP as a timing-dependent three-factor plasticity law gated by SIE composite reward. Its meaningful operation requires causal timing differentials and SIE/valence input. Adding it to VLL first would either require inventing substitutes for those prerequisites or importing several mechanisms at once.

Status: **not causally admissible as M1 by itself**.

#### GDSP
GDSP is the structural actuator. The current runtime architecture expects structural evidence, territory/boundary information, and higher-level modulation before ordered structural actions are dispatched. Adding GDSP first would either sever it from its causal inputs or force a multi-mechanism rung.

Status: **downstream; not M1**.

#### ADC / SIE
ADC is a reducer/cartography layer and SIE is a modulation layer. Neither is the primitive transport law. Their current architectural role sits downstream of local traversal/observations and upstream of structural action.

Status: **later rungs**.

#### Orthad / Phase custody
Orthad is relevant to retained state, relation structure, and later VDM/Cortex authority, but it is not justified as a drop-in VLL mechanism from the current evidence. Using it now would conflate a retained-state architecture with a transport mechanism.

Status: **important ancestry constraint, not M1**.

### The actual ancestry recovered

The lineage is not a random list of modules.

**AMN**
- temporal spike structure;
- STDP causality matrix;
- connection structure changed by timing.

**FUM**
- recurrence:
  `W(t+1) = W(t) + ΔW_RE-VGSP + ΔW_GDSP`;
- RE-VGSP changes local coupling from retained timing/eligibility;
- GDSP changes structural connectivity.

**VDM**
- canonical field transport was later separated from plasticity;
- CF04 explicitly replaces purely diffusive transport with finite-speed telegraphic transport;
- RE-VGSP remains dependent on causal timing and SIE reward;
- structural plasticity remains downstream.

**Cortex target**
- world state becomes retained neuron/edge/walker custody;
- local active frontiers and event-folded state replace global scans;
- maps → ADC → SIE → GDSP remains a downstream causal chain;
- exact payload/closure questions remain open.

This makes CF04 the cleanest bridge from the already-characterized V0 transport behavior into native VDM dynamics.

---

# V1 definition

Let the frozen VLL baseline be:

`V0 = embeddings + sparse semantic graph + current KnowledgeDynamics heat propagation`

Define:

`V1 = V0 + CF04 finite-τ transport`

No topology plasticity.
No SIE.
No ADC.
No GDSP.
No RE-VGSP.
No new semantic labels.
No graph rebuild.
No changed embedding model.

The graph and corpus remain exactly the Rung-0 frozen checkpoint.

The only causal difference is the transport law.

---

# Required implementation form

CF04 gives the first-order form:

\[
u_t = v
\]

\[
\tau v_t = D L u - v
\]

where \(L\) is the graph Laplacian.

The implementation should therefore add the minimum retained transport state required for \(v\), while keeping the existing sparse neighbor interface. Do not introduce dense graph scans.

The diffusive limit must be recoverable as \(\tau \to 0\).

---

# Predeclared V1 predictions

These are mechanism predictions, not desired outcomes.

## P1: finite propagation cone

A localized pulse should no longer produce the same diffusive temporal support profile as V0.

Measured activation radius/support must be bounded by a finite characteristic transport speed consistent with:

\[
c=\sqrt{D/\tau}.
\]

## P2: transport timing changes before semantic topology changes

Because V1 leaves the graph fixed, the earliest measurable change should be in **when** heat reaches graph regions, not in which semantic edges exist.

If semantic routing changes, that is a secondary consequence of altered transport timing over the same topology.

## P3: nontrivial transient branch

V1 should exhibit a transient/inertial response absent from pure diffusion. Depending on the discrete graph implementation, this may appear as delayed fronts, overshoot, damped oscillation, or a measurable second transport mode.

## P4: diffusive-limit recovery

As \(\tau\) is reduced toward the diffusive regime, V1 should converge toward V0 on the same battery.

Failure to recover V0 invalidates the implementation before any cognitive interpretation.

## P5: no topology claim

V1 is **not predicted** to fix ingestion-order sensitivity, create new edges, or solve the 3+ hop regime merely because those are R0 weaknesses.

If any of those change, record them. They are not the admission criterion.

---

# Minimal experiment battery for V1

Run the existing frozen Rung-0 organism under both transport laws.

### Transport-law validation
1. Compact-support pulse.
2. Support-radius versus tick.
3. Fit measured front speed against \(c=\sqrt{D/\tau}\).
4. Sweep \(\tau\).
5. Confirm diffusive-limit convergence.

### Existing R0 battery replay
Replay, unchanged:
- Origin → Transition;
- Transition → Origin;
- 1-hop / 2-hop / 3+ strata;
- source routing matrix;
- amplitude robustness;
- temporal horizons;
- zero-transport control.

Do not retune thresholds to make V1 pass.

### New comparison outputs
For every target:
- first-arrival tick by source;
- peak-arrival tick;
- AUC by source;
- support radius by tick;
- peak front velocity;
- V1−V0 routing fraction delta;
- V1−V0 arrival-time delta.

The most important comparison is not “did V1 score higher?” It is:

\[
\Delta_1 = \text{observable signature caused by finite-}\tau\text{ transport}.
\]

---

# Falsifiers

Reject the V1 implementation as a valid CF04 rung if any of these occur:

1. \(\tau\to0\) does not approach V0 behavior.
2. propagation outruns the declared finite-speed envelope without a documented discrete-graph reason;
3. the implementation requires dense/global graph work;
4. graph topology changes despite no plasticity mechanism being admitted;
5. results depend on host wall-clock timing rather than model ticks;
6. the implementation silently introduces SIE, GDSP, RE-VGSP, ADC, or new semantic scoring.

---

# What happens after V1

Only after CF04 is characterized should the mechanism graph be reconsidered.

At that point the next branch-sensitive question becomes whether the next admissible addition is:

- causal timing input required by RE-VGSP;
- the RE-VGSP eligibility/plasticity law itself;
- retained local traversal/map state;
- ADC/SIE modulation;
- GDSP structural action;
- Phase/Orthad custody integration.

The answer should be re-derived from the resulting V1 state and the mechanism prerequisites, not predetermined now.

---

# Immediate engineering task

Build a **research-only CF04 transport adapter** beside the current `KnowledgeDynamics` implementation.

Do not modify the live VLL daemon path.

Required first checkpoint:

- same frozen graph;
- same pulse;
- V0 and V1 run side by side;
- \(\tau\to0\) replay approaches V0;
- finite-\(\tau\) support obeys the CF04 causal envelope;
- no topology mutation.

Only after that checkpoint should the full Rung-0 battery be replayed against V1.
