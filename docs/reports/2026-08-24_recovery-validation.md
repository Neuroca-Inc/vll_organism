# vll_organism Recovery Validation

**Date:** 2026-08-24  
**Scope:** Final stabilization/recovery pass after architecture reconstruction.  
**Authority:** The tool is a persistent local file-fed knowledge organism that selectively uses compact VDM_RT-inspired dynamics where they provide direct utility; it is not a literal VDM_RT port.

## Recovered architecture

- SQLite is authoritative for durable chunks, provenance, and sparse edges.
- `KnowledgeDynamics` is a rebuildable evolving projection: territories, transient heat, local diffusion, familiarity, and mass/salience.
- Only background idle steps advance endogenous tick time.
- Embedding is an external boundary and runs outside the organism lock.
- File replacement is source-atomic: incomplete replacements roll back.
- Exact-content dedup preserves shared source provenance.
- One organism writer owns dynamics; status is read-only; query feedback is queued for daemon ownership.
- Query candidate scoring is bounded by territory/candidate budgets and graph expansion uses indexed local-neighbor reads rather than a full edge-table load.

## Removed non-serving mechanisms

The earlier implementation contained lifecycle machinery that had no coherent executing path or changed knowledge lifetime for reasons unrelated to the product. Recovery removed TTL knowledge deletion, boredom/churn bookkeeping, dormant frontier/split machinery, autonomous condensation, engrams, and imported Hellinger/Fisher homeostasis logic that did not correspond to live state transitions in this tool.

## Failure classes closed

1. Destructive `status` overwriting persisted organism state.
2. Slow Ollama embedding starving idle ticks behind the organism lock.
3. Fixed 30-second embedding timeout on CPU inference.
4. Legacy Ollama request shape and silent-truncation risk.
5. Isolated chunks omitted from graph-node accounting.
6. Runtime status reporting stale/fresh state incorrectly.
7. Dynamics configuration lost across restart.
8. Ingestion cardinality incorrectly advancing endogenous time.
9. Durable knowledge expiring under TTL.
10. Territory assignment learning self-distance and producing pathological singleton growth.
11. Territory capacity selection repeatedly choosing a full centroid.
12. Query/retrieval dynamics existing as unreachable code.
13. Query-time full graph reconstruction despite bounded-retrieval claims.
14. Source replacement exposing mixed old/partial-new state after mid-file embedding failure.
15. Fixed split depth and child-fragment dropping during context recovery.
16. Long sentence/unbroken input truncation.
17. Arbitrary binary bytes silently accepted through Latin-1 fallback.
18. Shutdown closing SQLite beneath an in-flight watcher.
19. Multiple organism writers independently owning the same DB.
20. Legacy DBs lacking embedding-model identity.
21. Query path creating/initializing a database when pointed at a missing path.
22. Package metadata falsely claiming Python 3.9 compatibility while using Python 3.10 syntax.

## Verification

### Source/regression suite

Final source suite: **60 passed**.

Coverage includes real daemon subprocess start/stop, SIGTERM, concurrent tick/ingest, writer locking, read-only status, source-atomic replacement, recursive context split/retry, binary rejection, persistence restore, legacy migration, territory capacity, local diffusion, bounded query retrieval, query feedback, and shutdown safety.

### Wheel/package path

A wheel was built successfully from the recovered source with the installed build backend. A fresh virtual environment then exercised the installed console entrypoint through:

```text
wheel install
-> perturb
-> status
-> query
-> run daemon
-> live file drop
-> idle ticks
-> SIGTERM shutdown
-> status
```

Observed E2E checkpoint:

- before daemon: tick 0, 1 chunk;
- after live run: tick 7, 2 chunks;
- daemon state: stopped cleanly;
- dynamics count matched durable chunk count;
- no traceback or SQLite `OperationalError` appeared.

The isolated build environment had no outbound package-index access, so the first PEP-517 build-isolation attempt could not download `setuptools`. Rebuilding with `--no-build-isolation` used the already-installed backend and succeeded; this was an environment dependency-fetch limitation, not a source/package failure.

### Supplied legacy database

Recovery was run against a copy of the supplied legacy `organism.db` without calling Ollama during migration.

Preserved/rebuilt state:

- durable chunks: **33**;
- sparse edges: **122**;
- endogenous tick: **28**;
- recovered dynamics states: **33**;
- semantic territories: **3**;
- embedding dimension: **2560**;
- bound embedding model: **`ollama:qwen3-embedding:4b`**.

A real stored 2560-dimensional embedding was then used as the query vector. Bounded retrieval returned:

```text
1.000000  exact source chunk
0.688378  related chunk
0.677723  related chunk
0.645801  related chunk
0.639759  related chunk
```

All top-five chunks came from the same related source document in this check. Query expansion was guarded so any attempted global `all_edges()` load would fail; it was not called.

## Remaining target-specific verification

The only meaningful verification intentionally left to the target machine is a live request through the recovered `/api/embed` client to the user's running CPU-hosted `qwen3-embedding:4b` Ollama service. The original supplied runtime logs already establish that service/model availability and CPU request latency; the recovered client path is covered by tests and the user-machine run is now a confirmation of the stabilized integration rather than discovery of another known prerequisite.
