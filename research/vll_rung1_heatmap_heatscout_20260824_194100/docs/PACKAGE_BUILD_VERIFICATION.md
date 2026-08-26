# Package Build Verification

This record verifies the experiment package as software. It is **not** a scientific Rung-1 result for the user's current organism database.

- Package regression tests: **6/6 PASS**.
- Full orchestration path exercised end-to-end against the supplied earlier `vll_organism.zip` repository snapshot.
- That engineering test created a transactionally consistent frozen SQLite database, ran synthetic and reciprocal static-attention stages, generated data/figures/results, and finalized a checksum manifest.
- `RUN_SHA256SUMS.txt` verified cleanly after the completed test run.
- Frozen SQLite output was reduced to one database file with no persistent `-wal`/`-shm` sidecars.
- The package run path did not leave Python `__pycache__` / `.pyc` artifacts inside the research package.
- The full engineering exercise completed in roughly 15 seconds on the tool environment; runtime on the target machine and larger current organism may differ.

The engineering exercise used an older attached organism snapshot (tick 1600, 89 chunks, 308 edges), so its scientific gate outcome is deliberately not copied into this pristine package. The decisive evidence is the run produced after extraction into the user's current `vll_organism/research/` tree.
