# VLL Rung 1A — HeatMap + HeatScout

This package is the first bounded Rung-1 experiment after the frozen Rung-0 routing battery.

## Scope lock

Only **HeatMap + HeatScout** are admitted here.

This package does **not** add TrailMap, MemoryMap, another walker, topology mutation, CF04, plasticity, ADC/SIE/GDSP, or any other mechanism. The run stops after adjudicating this square so the next move can be chosen by MCR from the result rather than by roadmap momentum.

## Primary question

Under matched bounded local work, does correctly situated event-folded HeatMap information let the native HeatScout choose destinations carrying more authoritative V0 heat than:

1. the same HeatScout with no heat information; and
2. the same HeatMap values shuffled onto the wrong graph positions?

Semantic cross-source routing is recorded as **secondary characterization only**. It cannot make Rung 1A pass or fail.

## Package placement

Extract this directory directly under:

```text
vll_organism/research/
```

The expected path is therefore:

```text
vll_organism/research/vll_rung1_heatmap_heatscout_20260824_194100/
```

The experiment imports the live repository's `vll_organism` implementation, transactionally freezes `organism.db` into this package, and never writes to the live database.

## Run

From the repository root:

```bash
PYTHONPATH=. python research/vll_rung1_heatmap_heatscout_20260824_194100/scripts/run_rung1.py
```

A short non-decisive smoke run is available as:

```bash
PYTHONPATH=. python research/vll_rung1_heatmap_heatscout_20260824_194100/scripts/run_rung1.py --quick
```

`--quick` always reports `EXPLORATORY`; it cannot earn the Rung-1 PASS.

## What the full run does

1. Runs the package regression tests.
2. Creates a transactionally consistent SQLite backup under `analysis_data/<UTC timestamp>/frozen_organism.db`.
3. Verifies SQLite integrity and records SHA-256.
4. Runs a synthetic fork choice-law gate against the native HeatScout softmax.
5. Rebuilds a clean V0 dynamics template from the frozen corpus.
6. For every chunk in the Origin source and every chunk in the reciprocal Transition source:
   - injects the same V0 pulse;
   - advances V0 for the declared warmup ticks;
   - folds **only locally processed V0 activity** into the native HeatMap through weighted `vt_touch` events;
   - checks whether the resulting HeatMap preserves the local authoritative V0 activity ordering;
   - runs equal-budget HeatScout counterfactuals: blind, real HeatMap, and heat-position shuffle.
7. Scores all three arms only on the **matched realized edge prefix**, so a route cannot win by doing more work.
8. Performs target-level one-sided sign-flip tests with Holm correction across the four reciprocal comparisons.
9. Records semantic related-source visitation separately as a non-gating diagnostic.
10. Re-hashes the frozen DB to verify no state/topology mutation.
11. Produces data, figures, a result document, findings, logs, and a run manifest entirely inside this package.
12. Stops. It does not select or run the next mechanism.

## Native mechanism custody

The package vendors the exact `HeatMap`, `BaseDecayMap`, `HeatScout`, and `BaseScout` source files from the supplied Cortex package. Their source bytes are preserved unchanged.

A small research-only compatibility event-schema module provides only the event dataclasses those files require, avoiding unrelated VDM runtime dependencies. The source provenance and hashes are recorded in `docs/reference/CORTEX_SOURCE_PROVENANCE.json`.

The native HeatScout currently uses Python's module-global `random` generator inside its softmax helper even though the scout also owns a seeded RNG. The experiment **does not edit the native source**. Instead, the harness saves, seeds, and restores the module-global RNG around each bounded scout call. Exact replay is regression-tested and this intervention is recorded as part of the measuring instrument.

The native `HeatMap.snapshot()` exposes bounded head/percentile summaries but not the per-node `heat_dict` that `HeatScout` expects for local neighbor scoring. The experiment does **not** patch either native source. The harness supplies a lazy map-like `.get(node)` view over the native HeatMap accumulator, decaying only the node actually requested by the local scout. This is an explicit research boundary adapter and performs no global HeatMap scan on the walker path.

## V0 → HeatMap research bridge

VLL R0 does not emit Cortex `vt_touch` events. This package therefore uses one explicit adapter:

```text
V0 node selected by the existing active queue for this tick
    -> read that node's authoritative lazily-decayed V0 heat
    -> VTTouchEvent(token=node_handle, w=current_V0_heat)
    -> native HeatMap.fold(...)
```

The adapter observes only the exact active-queue prefix the existing `KnowledgeDynamics.advance()` is about to process. It does not scan cold nodes and does not change V0 dynamics. The HeatMap is derived state, never an authority over V0 heat.

## Outputs

Each run creates its own timestamped output set:

```text
analysis_data/<run>/
    frozen_organism.db
    synthetic_choice_gate.csv
    heatmap_signal_targets.csv
    target_summary.csv
    walker_trials.csv.gz
    gate_results.json
    RUN_MANIFEST.json
    RUN_SHA256SUMS.txt

figures/<run>/
    synthetic_choice_law.svg
    heatmap_signal_gate.svg
    static_attention_gain.svg
    semantic_secondary.svg

docs/<run>_RESULTS.md
trace_logs/<run>.log
```

`FINDINGS.md` points to the latest completed result.

## Decision rule

The exact preregistered parameters, falsifiers, controls, and decision rule are in `EXPERIMENT_PLAN.json`. Do not change them after seeing a decisive result and then call the modified run confirmatory. Any changed plan is a successor experiment package.
