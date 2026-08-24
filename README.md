![VLL Organism Github Banner](/docs/assets/vll-banner.png)

`vll_organism` is a local, persistent knowledge organism for a folder of text/Markdown files.
It is meant to stay running in the background, ingest changed files, maintain a sparse semantic
structure, continue low-cost endogenous dynamics while external embedding work is slow, and expose
bounded retrieval without requiring a vector database or an LLM generation loop.

The project borrows a **small set of useful dynamics** from the larger [VDM_RT](https://github.com/Neuroca-Inc/vdm_rt) line of work. It is
not a miniature [VDM_RT](https://github.com/Neuroca-Inc/vdm_rt) runtime. Every live mechanism here has a direct job in this tool.

## Current invariants

- **SQLite owns durable knowledge.** Source text chunks and their embeddings do not expire because
  time passed.
- **Dynamics are a projection over durable knowledge.** Heat, salience/familiarity, territory
  membership, and settlement state may evolve; source knowledge remains durable.
- **Only idle/background steps advance organism time.** Ingesting 300 chunks does not advance 300
  ticks.
- **External embedding never owns the organism lock.** A slow CPU Ollama request cannot freeze
  idle ticks.
- **File replacement is source-atomic.** If embedding fails halfway through a changed file, the
  partial new version is rolled back and the previous complete representation remains active.
- **One process owns organism state.** A writer lease prevents two daemons/perturb commands from
  independently mutating dynamics. `status` stays read-only; `query` may only enqueue feedback.
- **Retrieval is bounded.** Query scoring starts from nearest semantic territories and expands over
  indexed local graph neighbors; it does not scan the full embedding corpus or rebuild the entire
  edge graph per query.
- **Exact-content dedup is global.** Identical knowledge can be represented once while retaining
  all source provenance.

## Live mechanisms

```text
source files
   -> deterministic text chunks
   -> local Ollama embeddings
   -> durable SQLite chunks/provenance
   -> bounded semantic territories
   -> sparse similarity graph

new knowledge / query feedback
   -> transient heat
   -> local graph diffusion
   -> decay toward quiet

repeated retrieval
   -> slow familiarity + mass/salience
   -> small ranking influence on future queries
```

The recovered implementation deliberately does **not** contain the old TTL knowledge deletion,
boredom/churn bookkeeping, frontier splitting, fake territory split/merge lifecycle, autonomous
condensation, engrams, or copied Hellinger/Fisher machinery that had no coherent executing path in
this application.

## Installation

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e .
pip install -e ".[dev]"   # optional: pytest
```

Python 3.10+ is required.

Ollama is a separate local service:

```bash
ollama serve
ollama pull qwen3-embedding:4b
```

The CLI defaults to `qwen3-embedding:4b` with a declared 2560-dimensional embedding. The client
uses Ollama's `/api/embed` endpoint with `truncate=false`; input that exceeds the served context is
split and retried instead of being silently truncated. The default embedding read timeout is 120s
because CPU inference can legitimately exceed 30s on larger chunks.

## Run the organism

```bash
python -m vll_organism run \
  --db ./organism.db \
  --watch ./corpus_drop \
  --embed-model qwen3-embedding:4b \
  --embed-dim 2560
```

Useful runtime controls:

```text
--tick-interval 3
--poll-interval 5
--embed-timeout 120
--snapshot-every 100
--similarity-threshold 0.55
--territory-similarity-threshold 0.55
--territory-max-members 256
--active-budget 256
--heat-half-life 120
--diffusion-fraction 0.12
```

Operational values are CLI/config data rather than buried inside the runtime path.

## Status

`status` never constructs an `Organism` and opens SQLite read-only:

```bash
python -m vll_organism status --db ./organism.db
```

Important fields include the endogenous tick, durable chunk count, graph connectivity, dynamics
statistics, pending query stimuli, embedding identity, watch-folder health, and whether live runtime
telemetry is fresh.

## Query

Use the **same embedding model** that created the database:

```bash
python -m vll_organism query \
  --db ./organism.db \
  --embed-model qwen3-embedding:4b \
  --embed-dim 2560 \
  "semantic territory retrieval"
```

Query path:

```text
query embedding
-> nearest territory centroids
-> bounded per-territory candidates
-> cosine scoring
-> bounded indexed graph expansion
-> small dynamic-state ranking term
-> top-k results
```

By default, query results enqueue lightweight feedback for the running daemon. The daemon applies
that feedback on its next endogenous tick. Use `--no-feedback` for a strictly observational query.

## Legacy database recovery

Databases created by the earlier implementation can be reused. Before first use, keep a backup:

```bash
cp organism.db organism.pre-recovery.db
```

The first recovered writer start will:

1. migrate the SQLite schema without discarding chunks/edges;
2. rebuild the new dynamics projection from durable embeddings when the old snapshot is missing or
   incompatible;
3. preserve the strongest known prior tick;
4. bind a legacy corpus to the explicitly configured embedding model after checking its stored
   embedding dimension.

A legacy corpus that has not yet been model-bound is intentionally rejected by `query`; start the
recovered daemon once with the model that actually produced those embeddings first. This prevents
a same-dimension but semantically different model from silently corrupting retrieval.

Do **not** change embedding models inside an existing database. Use a fresh database and re-embed
if you want to change models.

## Text ingestion behavior

- UTF-8/UTF-8-BOM and BOM-marked UTF-16 are supported directly.
- Latin-1 fallback is allowed only after binary-looking input is rejected.
- Paragraph-aware chunking never truncates a long sentence or unbroken run.
- Model context rejection recursively splits a logical chunk until all descendants embed or the
  chunk is explicitly classified incomplete.
- A transient or permanent embedding failure cannot make a partial replacement version become the
  source's active durable representation.

## Persistence ownership

```text
chunks / chunk_sources / edges   authoritative durable corpus structure
meta                              durable configuration/identity metadata
dynamics_snapshot                 rebuildable evolving projection
runtime_status                    lightweight current daemon projection
energy_log / events               history/evidence
stimuli                           query -> daemon feedback queue
```

The legacy `void_snapshot` table and `void_memory.py` import shim exist only for migration/
compatibility. They do not define the recovered architecture.

## Tests

```bash
python -m pytest -q
```

The regression suite exercises the real daemon subprocess path, destructive-status prevention,
concurrent ticking/ingestion, source-atomic replacement, context split/retry, legacy migration,
writer ownership, territory capacity, local graph diffusion, bounded retrieval, query feedback,
binary rejection, shutdown safety, and persistence restoration.

## Scope after stabilization

The stabilized core is intentionally small. Future features should be admitted only when they have
a concrete utility/invariant and a clear owner. Possible additions such as richer relation extraction,
user-facing inspection/visualization, autonomous exploration, or higher-order consolidation should
not be copied from VDM_RT merely because an analogous mechanism exists there.
