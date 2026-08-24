"""CLI: run, perturb, query, and read-only status."""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading

from .dynamics import DynamicsConfig, KnowledgeDynamics
from .embedder import HashEmbedder, OllamaEmbedder
from .organism import Organism, OrganismConfig
from .retrieval import QueryConfig, RetrievalEngine
from .status import read_status
from .storage import Storage


def _build_embedder(args):
    if args.test_embedder:
        if not args.allow_test_embedder:
            print("HashEmbedder is test-only; pass --allow-test-embedder intentionally.", file=sys.stderr)
            raise SystemExit(2)
        return HashEmbedder(dim=args.embed_dim)
    return OllamaEmbedder(
        model=args.embed_model,
        base_url=args.ollama_url,
        dim=args.embed_dim,
        num_ctx=args.embed_num_ctx,
        timeout=args.embed_timeout,
    )


def _dynamics_config(args) -> DynamicsConfig:
    return DynamicsConfig(
        heat_half_life_ticks=args.heat_half_life,
        diffusion_fraction=args.diffusion_fraction,
        active_budget=args.active_budget,
        territory_similarity_threshold=args.territory_similarity_threshold,
        territory_max_members=args.territory_max_members,
    )


def cmd_run(args) -> None:
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    organism = Organism(
        OrganismConfig(
            db_path=args.db,
            tick_interval_s=args.tick_interval,
            snapshot_every_ticks=args.snapshot_every,
            similarity_threshold=args.similarity_threshold,
            shutdown_grace_s=args.shutdown_grace,
        ),
        _build_embedder(args),
        dynamics_config=_dynamics_config(args),
    )
    watch_path = None
    if args.watch:
        try:
            watch_path = organism.start_watching(args.watch, args.poll_interval)
        except NotADirectoryError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            organism.stop()
            raise SystemExit(2)
    organism.start()

    def _handle_sigterm(_signum, _frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _handle_sigterm)
    print(
        f"vll_organism running. db={args.db} watch={watch_path or '(none)'} "
        f"tick_interval={args.tick_interval}s. Ctrl-C (or SIGTERM) to stop."
    )
    try:
        while True:
            threading.Event().wait(3600)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        organism.stop()


def cmd_perturb(args) -> None:
    logging.basicConfig(level=logging.INFO)
    organism = Organism(
        OrganismConfig(db_path=args.db),
        _build_embedder(args),
        dynamics_config=_dynamics_config(args),
    )
    result = organism.perturb_file(args.file)
    print(
        f"Registered {result.new_chunks} new chunk(s), removed {result.removed_stale_chunks} stale chunk(s) "
        f"from {args.file}"
        + ("; transient failure requires retry" if result.retry_required else "")
        + (f"; {result.permanent_failures} chunk(s) could not be embedded" if result.permanent_failures else "")
    )
    organism.stop()
    if result.retry_required or result.permanent_failures:
        raise SystemExit(1)


def cmd_query(args) -> None:
    embedder = _build_embedder(args)
    storage = Storage(args.db, initialize=False)
    try:
        _check_query_embedder(storage, embedder)
        query_embedding = embedder.embed(args.text)
        recorded_dim = storage.get_meta("embedding_dim")
        if recorded_dim is not None and int(recorded_dim) != len(query_embedding):
            raise RuntimeError(
                f"query embedding dimension {len(query_embedding)} does not match database {recorded_dim}"
            )
        dynamics = _load_query_dynamics(storage)
        engine = RetrievalEngine(storage, dynamics)
        hits = engine.query(
            query_embedding,
            QueryConfig(
                top_k=args.top_k,
                territory_k=args.territories,
                candidate_cap=args.candidate_cap,
                graph_hops=args.graph_hops,
                graph_candidate_cap=args.graph_candidate_cap,
            ),
        )
        if not args.no_feedback:
            storage.enqueue_stimuli(
                [(hit.chunk_id, max(0.0, min(1.0, hit.semantic_similarity))) for hit in hits],
                stimulus_type="query",
            )
        if args.json:
            print(json.dumps([hit.__dict__ for hit in hits], indent=2, default=list))
        else:
            for index, hit in enumerate(hits, 1):
                sources = ", ".join(hit.sources) if hit.sources else "(unknown source)"
                print(f"[{index}] score={hit.score:.4f} semantic={hit.semantic_similarity:.4f}")
                print(f"source: {sources}")
                print(hit.text.strip())
                if index != len(hits):
                    print()
    finally:
        storage.close()


def _check_query_embedder(storage: Storage, embedder) -> None:
    recorded = storage.get_meta("embedding_model")
    current = getattr(embedder, "model_id", None)
    if storage.chunk_count() > 0 and recorded is None:
        raise RuntimeError(
            "legacy database has no embedding-model identity; start the recovered daemon once "
            "with the correct --embed-model/--embed-dim to bind it before querying"
        )
    if recorded is not None and current is not None and recorded != current:
        raise RuntimeError(
            f"query model {current!r} does not match database model {recorded!r}; use the same embedding model"
        )


def _load_query_dynamics(storage: Storage) -> KnowledgeDynamics:
    snapshot = storage.load_dynamics_snapshot()
    if snapshot is not None and int(snapshot.get("version", 0)) == KnowledgeDynamics.PERSISTENCE_VERSION:
        return KnowledgeDynamics.from_dict(snapshot)
    # Recovery-only fallback for an old database. Normal operation never scans
    # the whole corpus for a query; running the recovered daemon once writes v3.
    dynamics = KnowledgeDynamics()
    latest = storage.recent_energy(limit=1)
    if latest:
        dynamics.set_tick(int(latest[0][0]))
    for record in storage.all_chunks():
        if record.embedding is not None:
            dynamics.register_chunk(record.id, record.created_at, record.embedding, cold=True)
    return dynamics


def cmd_status(args) -> None:
    print(json.dumps(read_status(args.db), indent=2, default=str))


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="vll_organism")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_embedding(sp):
        sp.add_argument("--db", default="./organism.db")
        sp.add_argument("--embed-model", default="qwen3-embedding:4b")
        sp.add_argument("--embed-dim", type=int, default=2560)
        sp.add_argument("--embed-num-ctx", type=int, default=None)
        sp.add_argument("--embed-timeout", type=float, default=120.0)
        sp.add_argument("--ollama-url", default="http://127.0.0.1:11434")
        sp.add_argument("--test-embedder", action="store_true")
        sp.add_argument("--allow-test-embedder", action="store_true")

    def add_dynamics(sp):
        sp.add_argument("--heat-half-life", type=int, default=120)
        sp.add_argument("--diffusion-fraction", type=float, default=0.12)
        sp.add_argument("--active-budget", type=int, default=256)
        sp.add_argument("--territory-similarity-threshold", type=float, default=0.55)
        sp.add_argument("--territory-max-members", type=int, default=256)

    run = sub.add_parser("run", help="Start background dynamics and optional folder watcher")
    add_embedding(run)
    add_dynamics(run)
    run.add_argument("--watch", default=None)
    run.add_argument("--poll-interval", type=float, default=5.0)
    run.add_argument("--tick-interval", type=float, default=3.0)
    run.add_argument("--snapshot-every", type=int, default=100)
    run.add_argument("--similarity-threshold", type=float, default=0.55)
    run.add_argument("--shutdown-grace", type=float, default=5.0)
    run.add_argument("--verbose", action="store_true")
    run.set_defaults(func=cmd_run)

    perturb = sub.add_parser("perturb", help="Ingest/synchronize one file")
    add_embedding(perturb)
    add_dynamics(perturb)
    perturb.add_argument("file")
    perturb.set_defaults(func=cmd_perturb)

    query = sub.add_parser("query", help="Retrieve useful context without a vector database")
    add_embedding(query)
    query.add_argument("text")
    query.add_argument("--top-k", type=int, default=8)
    query.add_argument("--territories", type=int, default=3)
    query.add_argument("--candidate-cap", type=int, default=256)
    query.add_argument("--graph-hops", type=int, default=1)
    query.add_argument("--graph-candidate-cap", type=int, default=128)
    query.add_argument("--no-feedback", action="store_true")
    query.add_argument("--json", action="store_true")
    query.set_defaults(func=cmd_query)

    status = sub.add_parser("status", help="Print read-only current state")
    status.add_argument("--db", default="./organism.db")
    status.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
