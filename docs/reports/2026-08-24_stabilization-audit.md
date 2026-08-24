# vll_organism Stabilization Audit

**Date:** 2026-08-24  
**Scope:** Runtime failure closure observed on the real Ollama/CPU daemon path, plus architecture-fit audit against the clarified project target.  
**Target authority used for this audit:** a persistent local background knowledge organism that accepts files over time, retains/evolves internal knowledge state while idle, and selectively uses smaller VDM_RT-inspired dynamics/mechanisms to become a useful tool. It is not required to reproduce VDM_RT literally.

## Verified runtime evidence

The supplied `organism.db` contained:

- 33 persisted chunks;
- 122 persisted edges;
- only 2 energy samples (ticks 1 and 28);
- one perturbation event at tick 27 for 26 chunks;
- a `void_snapshot` at tick 0 with zero memory states;
- the last energy sample at tick 28 with total energy `40.71088463309714`.

The timestamps show the daemon logged energy tick 1 at 09:28:14 local-run time, then did not log the next energy sample until immediately after the 26-chunk perturbation completed around 09:35:42. This matches the source-level lock path: `perturb_file()` held `Organism._lock` across every synchronous Ollama request, preventing `idle_tick()` from acquiring the same lock for the entire multi-minute embedding run.

The real Ollama log also showed successful 4B CPU embedding requests taking roughly 8-26 seconds after model load and the first request exceeding the original fixed 30-second client timeout. The fixed 30-second timeout was therefore not a valid readiness boundary for this hardware/model combination.

## Stabilization defects repaired

### 1. `status` mutated authoritative organism persistence

Original path:

```text
cmd_status
-> construct Organism from persisted snapshot
-> print organism.status()
-> organism.stop()
-> _save_snapshot()
-> overwrite void_snapshot with this status process's in-memory state
```

A status process had no right to become a writer. This is the direct cause of the observed tick-0/zero-memory persisted snapshot while chunks and edges continued to accumulate.

**Repair:** `status` now uses a dedicated read-only status projection and opens SQLite in `mode=ro`. Regression coverage hashes the complete database before/after the CLI command and requires byte identity.

### 2. Slow embeddings starved the background organism

Original path:

```text
watch thread
-> perturb_file()
-> acquire Organism._lock
-> for every chunk:
   -> synchronous Ollama HTTP embedding (8-30+ seconds each)
   -> register state
-> release lock

idle thread
-> idle_tick()
-> waits on same lock for the whole file
```

This contradicts the product's central behavior: the organism should continue idling/evolving while ingestion performs expensive external work.

**Repair:** embedding runs outside the organism lock. Only short dedup/state/storage mutations are serialized. A regression uses an intentionally slow embedder and verifies energy samples continue advancing during the external embedding calls.

### 3. Ollama timeout and API path were mismatched to real execution

Original code hard-coded `timeout=30.0` and used legacy `/api/embeddings` request/response shape.

**Repair:** production embedding uses `/api/embed`, passes `truncate=false` so oversized text is not silently discarded, and exposes `--embed-timeout` with a 120-second default. The context-length split/retry path remains meaningful because truncation is explicitly disabled.

### 4. `status.settled` was structurally false

Original `status` constructed a fresh `HomeostasisTracker`; its history was empty, so `is_settled()` could not represent the daemon's persisted state.

**Repair:** lightweight live runtime status is persisted by the daemon. Fallback status derives settlement from persisted energy evidence rather than an empty tracker.

### 5. Isolated chunks disappeared from graph node counts

The graph persisted only edges. A chunk with zero edges was absent from the in-memory adjacency map after restore and therefore did not count as a graph node.

**Repair:** every persisted chunk is restored/registered as a graph node. `status` also exposes `connected_graph_nodes` separately so isolation is visible without misreporting the node as nonexistent.

### 6. Dynamics configuration silently reverted on restart

`VoidMemoryManager.from_dict()` previously built `cls()` with defaults and restored state values only. Non-default capacity, TTL, decay, diffusion, and related dynamics were therefore lost across restart.

**Repair:** snapshots now persist and restore the dynamics configuration (backward-compatible with older snapshots). Exploration temperature and pending condensation state are also persisted.

## Verification after repair

- Original suite: 39 tests passed but did not cover the observed destructive-status or slow-embedding starvation paths.
- Stabilized suite: **43 tests passed**.
- New regressions include:
  - byte-for-byte non-destructive status CLI;
  - idle ticks continue while embedding is slow;
  - isolated chunks remain graph nodes;
  - non-default dynamics configuration survives snapshot round-trip.
- The repaired status command was run against a copy of the supplied database; SHA-256 was identical before and after execution.

## Architecture findings not silently repaired

These are not cosmetic defects. They affect the meaning of the small evolving organism and should be resolved from project invariants before implementation changes.

### A. Ingestion count currently advances organism time and ages all memories

`VoidMemoryManager.register_chunks()` increments `_tick` and runs `_decay_pass()` after registration. `Organism` calls it once per chunk.

Therefore:

```text
one 260-chunk ingestion
-> ~260 organism ticks immediately
-> every existing MemoryState TTL decremented ~260 times
```

A direct reproduction with `base_ttl=240` registered 260 chunks sequentially and left only 239 live states; the first 21 had expired even though essentially no idle time elapsed.

This conflates **perturbation cardinality** with **endogenous time**. For a long-lived background organism, the required time semantics should be established before changing it.

### B. Retrieval-driven dynamics are unreachable in the current application

`VoidMemoryManager.tick_post_retrieval()` drives:

- inhibition/churn;
- frontier counters and territory splitting;
- condensation scheduling;
- reward EMA;
- exploration temperature.

No code outside `void_memory.py` calls this method. No retrieval/query loop exists in the repository. As shipped, those status fields are not warming up; they are unreachable behavior and remain zero unless an external caller is added later.

The actual idle dynamics currently consist mostly of heat decay, pruning, periodic mass diffusion, and conservative territory merges.

### C. Adaptive territory warmup is self-referential

For the first warmup phase, `_assign_to_territory()` creates a new territory for each new embedding. Immediately afterward registration records the distance from that embedding to the centroid of the territory it just created. That centroid is the embedding itself, so the recorded distance is approximately zero.

A 60-vector reproduction produced:

- 60 live memories;
- 60 territories;
- adaptive tau clamped to `0.05`;
- nearest-neighbor samples approximately all zero during warmup.

The adaptation is therefore not learning nearest-neighbor structure from the corpus; it is learning self-distance. This should be redesigned from an explicit territory-formation invariant rather than threshold-tuned locally.

### D. Territory splitting does not maintain centroid ownership

`_split_territory()` changes `MemoryState.territory` values but does not create/recompute a centroid/count/distance model for the new territory or decrement the source territory's centroid/count state.

If retrieval-driven splitting is wired in later, memory-state territory identity and centroid state can immediately diverge. This is currently dormant because the retrieval lifecycle is unreachable.

### E. The similarity degree cap does not actually prevent hubs

`max_out_degree_similarity` caps only edges created *from* a new chunk. A generic old chunk can still become the target of arbitrarily many later similarity edges. `neighbors()` traverses incoming plus outgoing edges, so an incoming hub can still dominate future graph traversal.

If hub prevention is a real invariant, total/incoming degree or retrieval weighting must enforce it, not outbound degree alone.

### F. Bounded candidate count is not bounded candidate discovery

`_candidate_pool()` returns at most `similarity_candidate_cap` candidates, but it iterates the live memory set to collect same-territory members and again constructs a list of the remaining states. The comment claiming the path never scans the full set is therefore inaccurate.

For the present 5,000-state soft capacity this may be operationally acceptable, but it is not a locality guarantee.

### G. The repository is missing the authority artifacts its README cites

The README repeatedly cites `input.md` and an `initial_VLL_convo.md`, but neither artifact is present in the supplied repository. The implementation therefore contains strong statements such as “matching what was asked for” without shipping the source that lets a future agent independently check that claim.

Before deeper mechanism work, preserve the actual vll_organism target/invariants in a durable architecture document rather than allowing the current implementation or README to self-authorize.

## Recommended next architecture pass

Do not add more mechanisms yet. Reconstruct the compact organism around five explicit invariants:

1. **Persistent knowledge authority:** what survives indefinitely, what may decay, and how dormant knowledge re-enters active dynamics.
2. **Endogenous time:** what advances an organism tick and what a perturbation is allowed to change without advancing time.
3. **Endogenous activity:** what the organism actually does while idle besides decaying toward zero.
4. **Territory semantics:** what a territory represents, how membership is earned, and how split/merge operations preserve centroid/index consistency.
5. **Utility boundary:** the concrete query/inspection behaviors that turn the organism from a background simulation into a knowledge tool.

Only after those are explicit should retrieval/exploration, reactivation, consolidation, territory formation, and graph traversal be connected. That prevents another round of importing VDM_RT-shaped mechanisms merely because they already exist.
