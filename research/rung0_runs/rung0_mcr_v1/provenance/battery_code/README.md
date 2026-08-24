# VLL Rung-0 Routing Battery

This package closes the current **Rung-0** routing characterization without changing the VLL runtime architecture.
It treats the existing organism as the experimental object and keeps research machinery outside the live runtime.

## What this battery tests

The primary pairwise question is:

> When one source is pulsed, does heat accumulate in a predeclared related source more strongly than expected when semantic identities are detached from graph position while the weighted topology is preserved?

The battery separates several burdens rather than using one PASS/FAIL:

1. **Node-level routing** across every chunk in the source. No best-bridge selection.
2. **Natural multi-hop routing** where the related source is absent from the target's direct top-12 diffusion neighborhood.
3. **Whole-source omnibus significance** using source-fixed identity permutations with independent calibration and evaluation null streams.
4. **Multiple-testing correction** with Benjamini-Hochberg and Holm correction for node-level directional tests.
5. **Selection-risk control** via the omnibus maximum-delta statistic.
6. **Degree-matched permutation** to check whether source enrichment can be explained simply by diffusion-degree placement.
7. **Horizon robustness** at several predeclared time horizons.
8. **Pulse-scale robustness** across several heat amplitudes.
9. **Zero-diffusion negative control**: foreign heat must remain exactly zero when diffusion is disabled.
10. **Uniform-weight diagnostic** to distinguish adjacency/topology contribution from similarity-weight contribution.
11. **Direct-edge lesion diagnostic** for one-hop targets: remove only target-to-related direct neighbors and observe whether a longer path still carries signal.
12. **Source routing matrix** across a declared source family, with source-fixed permutation controls, BH correction across matrix cells, and an explicit `OTHER_FOREIGN` residual so unselected destinations cannot disappear from view.
13. **Document-ingestion-order sensitivity**: faithfully replay the frozen graph, verify that replay reproduces the stored topology, then randomize document arrival order and remeasure routing. Candidate cosine evaluation follows the runtime's bounded candidate path and uses a bounded LRU cache rather than a dense all-pairs similarity matrix.
14. **Active-budget diagnostics** report whether the runtime's bounded active queue becomes a limiting factor during any target pulse.

The order item is deliberately a **sensitivity analysis**, not a semantic null test. It exists because VLL graph formation is online and source arrival order can be confounded with source identity.

The machine-readable plan also carries stable Rung-0 claim IDs (`R0-C1` through `R0-C5`), predeclared decision rules, and falsifiers. A node-level anecdote cannot satisfy a source-level claim whose declared burden is omnibus.

## Why the controls are faster than the old sweep

The live heat dynamics do not inspect source labels. For a fixed target and fixed graph, the heat field therefore needs to be simulated only once. Identity-shuffle controls can be calculated by relabeling the resulting per-node integrated heat field rather than rerunning 5,000-10,000 identical diffusion simulations.

Whole-sweep omnibus testing uses **two independent permutation streams**: one calibrates each target's null median/95th percentile, and a disjoint stream evaluates sweep-level median excess, pass count, and maximum excess. This avoids letting the same null realization set and test its own threshold.

A runtime-equivalence sentinel verifies this optimization against the old identity-remapped `KnowledgeDynamics.advance()` construction before the result is accepted. If a future VLL mechanism makes the optimization invalid, the battery fails rather than silently using a stale assumption.

## Snapshot rule

Do not run the final battery against the growing live database.

`freeze_snapshot.py` uses SQLite's online backup API to create a transactionally consistent database while the daemon may remain running. It then performs integrity checks and records:

- database SHA-256;
- snapshot tick and dynamics configuration;
- chunk/node/edge counts;
- embedding identity;
- source inventory;
- source-file hashes when the original files are present;
- database-native per-source chunk-membership/content digests;
- critical runtime/research code hashes.

The database-native membership is the authority for the exact frozen experimental corpus. Source-file hashes are contemporaneous references because a live watched file can change independently of the database snapshot.

All later experiments read only the frozen copy.

## One-command execution

From the repository root, after placing this package at `research/rung0_battery/`:

```bash
PYTHONPATH=.:research/rung0_battery python research/rung0_battery/run_plan.py \
  --db ./organism.db \
  --plan research/rung0_battery/rung0_plan.json \
  --out research/rung0_runs/rung0_mcr_v1 \
  --repo-root .
```

The plan runner checkpoints every stage in `RUN_LEDGER.json`, snapshots the exact plan and battery source into `provenance/`, and writes a final `RUN_MANIFEST.json` of result bytes. If a later long-running stage fails, prior evidence remains intact. Resume with:

```bash
PYTHONPATH=.:research/rung0_battery python research/rung0_battery/run_plan.py \
  --db ./organism.db \
  --plan research/rung0_battery/rung0_plan.json \
  --out research/rung0_runs/rung0_mcr_v1 \
  --repo-root . \
  --resume
```

The live DB is consulted only by the freeze stage. A resumed run continues from the already-frozen sibling.

## Planned stages

The supplied plan runs:

1. the repository tests plus this battery's regression tests;
2. a frozen SQLite snapshot;
3. `00_MCR_Origin.md -> 01_MCR_Transition-to-Formal.md`;
4. the reciprocal characterization on the exact same snapshot;
5. a source-routing matrix over Origin, Transition, Formal Plan, the MCR method, and Analogistical Constructivism;
6. a 100-order ingestion-order sensitivity audit for Origin -> Transition.

No VDM feature is added by this package. This is Rung-0 characterization only.

## Output semantics

### Node-level statistics

`targets.csv` includes:

- shortest related-source hop distance;
- direct related / other-foreign neighborhood counts and weights;
- real related share of integrated foreign heat;
- target-fixed identity-shuffle null median, 5th and 95th percentiles;
- directional right/left empirical p-values;
- BH and Holm adjusted right-tail values;
- top foreign source;
- status and target preview.

A disconnected target is `UNREACHABLE`; it is not assigned a fake zero effect. A `0/0` effect ratio is `NA`, never infinity.

### Omnibus statistics

For all reachable targets and important hop subsets, independent source-fixed calibration/evaluation nulls report:

- real median excess over each target's null median;
- null 95th percentile and empirical right-tail p-value;
- count of targets exceeding their target-specific null 95th percentile, with a whole-sweep null for that count;
- maximum target excess with a whole-sweep null, protecting against best-target selection.

### Degree-matched control

Non-origin identities are shuffled only among graph positions with the exact same top-12 diffusion degree. The output reports the fraction of foreign positions that can actually exchange related/other labels under this constraint. Low movable coverage means this control is limited and should not be overinterpreted.

### Order sensitivity

The script first replays graph construction using frozen embeddings and the supplied graph-construction parameters. Random-order work is refused unless the replay reproduces every stored edge endpoint/relation. Float32 persistence can cause tiny weight differences; the default acceptance bound is `1e-6` and the observed error is reported. Similarity is evaluated only for the bounded candidate set that the runtime would inspect; a configurable LRU cache prevents research tooling from introducing an unbounded dense similarity store.

It then randomizes **document arrival order while preserving chunk order within each document**. The resulting distribution tells us whether the routing signature is robust to online ingestion sequence. It does not prove or disprove semantic causation by itself.

## Important interpretation boundaries

- Source labels are an experimental proxy for a designated relationship, not a universal definition of semantic relatedness.
- Identity permutations test semantic/source placement on the existing graph. They do not establish that embeddings, ingestion order, and graph construction are independent causes.
- Multi-hop enrichment establishes propagation through at least one intermediate graph node. It does not by itself establish symbolic reasoning through that intermediate node.
- The order audit directly addresses a major online-construction confound, but it remains a sensitivity experiment rather than a null model of meaning.
- This package characterizes the current mechanism. It does not change the VLL/VDM roadmap or admit another VDM mechanism.

## Files

```text
research/rung0_battery/
├── README.md
├── rung0_plan.json
├── run_plan.py
├── freeze_snapshot.py
├── pair_battery.py
├── source_matrix.py
├── order_sensitivity.py
├── rung0_common.py
├── rung0_stats.py
├── rung0_controls.py
└── tests/
    └── test_rung0_battery.py
```

## Manual stage commands

The orchestrator is preferred. Individual scripts remain callable so a failed stage can be isolated without changing the plan.

```bash
PYTHONPATH=.:research/rung0_battery python research/rung0_battery/freeze_snapshot.py \
  --db ./organism.db \
  --out research/rung0_snapshots/manual \
  --repo-root .
```

```bash
PYTHONPATH=.:research/rung0_battery python research/rung0_battery/pair_battery.py \
  --db research/rung0_snapshots/manual/organism.db \
  --source 00_MCR_Origin.md \
  --related 01_MCR_Transition-to-Formal.md \
  --heat 10 \
  --horizon 20 --horizon 60 --horizon 120 --horizon 240 \
  --primary-horizon 120 \
  --node-controls 5000 \
  --omnibus-controls 10000 \
  --degree-controls 10000 \
  --out research/rung0_results/origin_to_transition
```

```bash
PYTHONPATH=.:research/rung0_battery python research/rung0_battery/source_matrix.py \
  --db research/rung0_snapshots/manual/organism.db \
  --source 00_MCR_Origin.md \
  --source 01_MCR_Transition-to-Formal.md \
  --source 02_MCR_Formal-Plan.md \
  --source Monotonic-Causal-Resolution-MCR_Problem-Solving-Method-v0.1.md \
  --source Analogistical-Constructivism-Deep-Research.md \
  --controls 5000 \
  --out research/rung0_results/source_matrix
```

```bash
PYTHONPATH=.:research/rung0_battery python research/rung0_battery/order_sensitivity.py \
  --db research/rung0_snapshots/manual/organism.db \
  --source 00_MCR_Origin.md \
  --related 01_MCR_Transition-to-Formal.md \
  --controls 100 \
  --out research/rung0_results/order_sensitivity
```
