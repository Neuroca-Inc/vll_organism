# Quickstart (assumes installation is complete)

## Run Ollama

### Terminal 1: Ollama

```bash
ollama serve
ollama pull qwen3-embedding:4b
```

### Terminal 2: VLL Organism

```bash
source venv/bin/activate
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

### Terminal 3: VLL Status

```bash
watch python -m vll_organism status --db ./organism.db
```

### Query the model

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