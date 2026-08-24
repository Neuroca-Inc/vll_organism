"""Persistent file-fed knowledge organism orchestration."""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Optional, Sequence

from .dynamics import DynamicsConfig, KnowledgeDynamics
from .embedder import Embedder, EmbeddingError, EmbeddingInputTooLongError
from .graph import KnowledgeGraph
from .homeostasis import HomeostasisTracker
from .ingest import MIN_SPLIT_CHARS, chunk_id_for, chunk_text, embed_with_split_retry, read_corpus_file, sha256_hex
from .storage import ChunkRecord, Storage
from .writer_lock import WriterLock

logger = logging.getLogger("vll_organism")


@dataclass(frozen=True)
class OrganismConfig:
    db_path: str
    tick_interval_s: float = 3.0
    snapshot_every_ticks: int = 100
    max_chunk_chars: int = 1500
    similarity_threshold: float = 0.55
    similarity_candidate_cap: int = 200
    homeostasis_window: int = 8
    homeostasis_quiet_heat: float = 1e-3
    stimulus_batch: int = 256
    shutdown_grace_s: float = 5.0

    def validate(self) -> None:
        if self.tick_interval_s <= 0:
            raise ValueError("tick_interval_s must be > 0")
        if self.snapshot_every_ticks < 1:
            raise ValueError("snapshot_every_ticks must be >= 1")
        if self.max_chunk_chars < MIN_SPLIT_CHARS:
            raise ValueError(f"max_chunk_chars must be >= {MIN_SPLIT_CHARS}")
        if not -1.0 <= self.similarity_threshold <= 1.0:
            raise ValueError("similarity_threshold must be in [-1, 1]")
        if self.similarity_candidate_cap < 1:
            raise ValueError("similarity_candidate_cap must be >= 1")
        if self.homeostasis_window < 1 or self.homeostasis_quiet_heat < 0:
            raise ValueError("invalid homeostasis configuration")
        if self.stimulus_batch < 1:
            raise ValueError("stimulus_batch must be >= 1")
        if self.shutdown_grace_s <= 0:
            raise ValueError("shutdown_grace_s must be > 0")


@dataclass(frozen=True)
class PerturbResult:
    new_chunks: int
    retry_required: bool
    removed_stale_chunks: int = 0
    permanent_failures: int = 0

    @property
    def had_embedding_failure(self) -> bool:
        return self.retry_required

    def __bool__(self) -> bool:
        return self.new_chunks > 0 or self.removed_stale_chunks > 0


def ensure_watch_folder(path: str) -> str:
    resolved = os.path.abspath(path)
    if os.path.isdir(resolved):
        return resolved
    if os.path.exists(resolved):
        raise NotADirectoryError(f"--watch path {resolved!r} exists but is not a directory")
    os.makedirs(resolved, exist_ok=True)
    logger.info("Created watch folder: %s", resolved)
    return resolved


class Organism:
    def __init__(
        self,
        config: OrganismConfig,
        embedder: Embedder,
        void_memory: Optional[KnowledgeDynamics] = None,
        dynamics_config: Optional[DynamicsConfig] = None,
    ):
        config.validate()
        self.config = config
        self.embedder = embedder
        self._writer_lock = WriterLock(config.db_path)
        self._writer_lock.acquire()
        self.storage = Storage(config.db_path)
        self.graph = KnowledgeGraph()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._watch_thread: Optional[threading.Thread] = None
        self._closed = False
        self._known_files: dict[str, float] = {}
        self.watch_folder_path: Optional[str] = None
        self.watch_folder_ok: Optional[bool] = None
        self._watch_folder_missing_warned = False
        self.homeostasis = HomeostasisTracker(
            window=config.homeostasis_window,
            quiet_heat=config.homeostasis_quiet_heat,
        )
        self._was_settled = False

        self._restore_graph()
        if void_memory is not None:
            self.void = void_memory
            self._reconcile_current_dynamics(force_rebuild=False)
        else:
            self.void = self._restore_dynamics(dynamics_config)
        self._restore_watch_meta()
        self._backfill_embedding_dim_meta()
        self._bind_legacy_embedding_model()

    # ---------------- Startup / recovery ----------------
    def _restore_graph(self) -> None:
        self.graph.load_edge_list(self.storage.all_edges())
        for chunk_id, _created, _territory in self.storage.chunk_headers():
            self.graph.add_node(chunk_id)

    def _restore_dynamics(self, dynamics_config: Optional[DynamicsConfig]) -> KnowledgeDynamics:
        snapshot = self.storage.load_dynamics_snapshot()
        if snapshot is not None and int(snapshot.get("version", 0)) == KnowledgeDynamics.PERSISTENCE_VERSION:
            dynamics = KnowledgeDynamics.from_dict(snapshot)
            self.void = dynamics
            self._reconcile_current_dynamics(force_rebuild=False)
            return self.void

        legacy = self.storage.load_void_snapshot()
        legacy_tick = int(legacy.get("tick", 0)) if isinstance(legacy, dict) else 0
        latest = self.storage.recent_energy(limit=1)
        latest_tick = int(latest[0][0]) if latest else 0
        dynamics = KnowledgeDynamics(dynamics_config)
        dynamics.set_tick(max(legacy_tick, latest_tick))
        self.void = dynamics
        self._reconcile_current_dynamics(force_rebuild=self.storage.chunk_count() > 0)
        return self.void

    def _reconcile_current_dynamics(self, force_rebuild: bool) -> None:
        headers = self.storage.chunk_headers()
        chunk_ids = {chunk_id for chunk_id, _created, _territory in headers}
        state_ids = self.void.ids()
        mismatch = state_ids != chunk_ids
        missing_territory = any(territory is None for _cid, _created, territory in headers)
        if not force_rebuild and not mismatch and not missing_territory:
            return

        if force_rebuild:
            tick = self.void.tick_count
            self.void = KnowledgeDynamics(self.void.config)
            self.void.set_tick(tick)
            for chunk_id, created, _territory in headers:
                record = self.storage.get_chunk(chunk_id)
                if record is None or record.embedding is None:
                    continue
                state = self.void.register_chunk(chunk_id, created, record.embedding, cold=True)
                self.storage.set_chunk_territory(chunk_id, state.territory)
            reason = "legacy_or_missing_snapshot"
        else:
            for extra_id in state_ids - chunk_ids:
                self.void.remove_chunk(extra_id)
            for chunk_id, created, _territory in headers:
                if chunk_id in self.void.ids():
                    continue
                record = self.storage.get_chunk(chunk_id)
                if record is not None and record.embedding is not None:
                    self.void.register_chunk(chunk_id, created, record.embedding, cold=True)

            def entries():
                for chunk_id, _created, _territory in headers:
                    record = self.storage.get_chunk(chunk_id)
                    if record is not None and record.embedding is not None:
                        yield chunk_id, record.embedding

            self.void.rebuild_territories(entries())
            for chunk_id, _created, _territory in headers:
                state = self.void.get_state(chunk_id)
                if state is not None:
                    self.storage.set_chunk_territory(chunk_id, state.territory)
            reason = "state_storage_mismatch"

        self._save_snapshot()
        self.storage.log_event(
            self.void.tick_count,
            "dynamics_rebuilt",
            {"reason": reason, "chunks": self.storage.chunk_count()},
        )
        logger.warning("Rebuilt dynamics projection from %d durable chunks (%s)", len(headers), reason)

    def _backfill_embedding_dim_meta(self) -> None:
        if self.storage.get_meta("embedding_dim") is not None:
            return
        for chunk_id, _created, _territory in self.storage.chunk_headers():
            record = self.storage.get_chunk(chunk_id)
            if record is not None and record.embedding is not None:
                self.storage.set_meta("embedding_dim", str(len(record.embedding)))
                return

    def _bind_legacy_embedding_model(self) -> None:
        """Bind pre-model-metadata corpora to the explicitly selected backend."""
        if self.storage.chunk_count() == 0 or self.storage.get_meta("embedding_model") is not None:
            return
        recorded_dim = self.storage.get_meta("embedding_dim")
        declared_dim = getattr(self.embedder, "dim", None)
        if recorded_dim is not None and declared_dim is not None and int(recorded_dim) != int(declared_dim):
            raise RuntimeError(
                f"legacy database embedding dimension is {recorded_dim}, but configured embedder declares {declared_dim}"
            )
        model_id = getattr(self.embedder, "model_id", None)
        if model_id is None:
            raise RuntimeError("legacy database requires an embedder with a stable model_id before it can be migrated")
        self.storage.set_meta("embedding_model", str(model_id))
        self.storage.log_event(
            self.void.tick_count,
            "legacy_embedding_model_bound",
            {"model": str(model_id), "dimension": None if recorded_dim is None else int(recorded_dim)},
        )

    def _restore_watch_meta(self) -> None:
        self.watch_folder_path = self.storage.get_meta("watch_folder_path")
        value = self.storage.get_meta("watch_folder_ok")
        self.watch_folder_ok = None if value is None else value == "1"

    def _save_snapshot(self) -> None:
        self.storage.save_dynamics_snapshot(self.void.tick_count, self.void.to_dict())

    def _publish_runtime_status(self) -> None:
        self.storage.save_runtime_status(
            self.void.tick_count,
            {
                "dynamics_stats": self.void.stats(),
                "settled": self.homeostasis.is_settled(),
                "state_version": KnowledgeDynamics.PERSISTENCE_VERSION,
                "pending_stimuli": self.storage.pending_stimuli_count(),
            },
        )

    # ---------------- Ingestion ----------------
    def perturb_file(self, path: str) -> PerturbResult:
        if self._stop.is_set():
            return PerturbResult(0, False)
        source = os.path.abspath(path)
        try:
            text = read_corpus_file(source)
        except Exception as exc:
            logger.warning("Could not read %s: %s -- retrying next scan", source, exc)
            return PerturbResult(0, True)

        new_count = 0
        had_failure = False
        permanent_failures = 0
        current_ids: set[str] = set()
        invalidated = False
        with self._lock:
            previous_ids = self.storage.source_chunk_ids(source)

        for chunk in chunk_text(text, max_chars=self.config.max_chunk_chars):
            if self._stop.is_set():
                break
            with self._lock:
                existing = self.storage.find_chunk_by_hash(chunk.hash)
                if existing is not None:
                    self.storage.attach_source(existing.id, source)
                    current_ids.add(existing.id)
                    continue
            try:
                pieces = embed_with_split_retry(self.embedder, chunk.text)
            except EmbeddingInputTooLongError as exc:
                permanent_failures += 1
                logger.error("Permanently skipping oversized chunk from %s: %s", source, exc)
                continue
            except EmbeddingError as exc:
                logger.error("Embedding failed for %s: %s -- retrying next scan", source, exc)
                had_failure = True
                continue

            for subtext, embedding in pieces:
                if self._stop.is_set():
                    break
                subhash = sha256_hex(subtext)
                with self._lock:
                    existing = self.storage.find_chunk_by_hash(subhash)
                    if existing is not None:
                        self.storage.attach_source(existing.id, source)
                        current_ids.add(existing.id)
                        continue
                    if not invalidated:
                        self.homeostasis.invalidate()
                        self._was_settled = False
                        invalidated = True
                    chunk_id = chunk_id_for(source, subhash)
                    self._register_chunk(chunk_id, subtext, subhash, source, embedding)
                    current_ids.add(chunk_id)
                    new_count += 1

        incomplete = had_failure or permanent_failures > 0 or self._stop.is_set()
        if incomplete:
            # File-version ingestion is source-atomic. Chunks/attachments added
            # before a later embedding failure are rolled back so retrieval
            # never sees a mixed old/partial-new representation of one source.
            with self._lock:
                added_ids = self.storage.source_chunk_ids(source) - previous_ids
                added_records = {r.id: r for r in self.storage.get_chunks(list(added_ids))}
                orphaned = self.storage.sync_source(source, previous_ids)
                for chunk_id in orphaned:
                    record = added_records.get(chunk_id)
                    self.graph.remove_node(chunk_id)
                    self.void.remove_chunk(chunk_id, record.embedding if record else None)
                if orphaned:
                    self._save_snapshot()
                    self._publish_runtime_status()
                if had_failure or permanent_failures:
                    self.storage.log_event(
                        self.void.tick_count,
                        "ingestion_incomplete",
                        {
                            "file": source,
                            "transient_failure": had_failure,
                            "permanent_failures": permanent_failures,
                            "rolled_back_chunks": len(orphaned),
                        },
                    )
            return PerturbResult(0, had_failure, 0, permanent_failures)

        removed = 0
        with self._lock:
            stale_ids = self.storage.source_chunk_ids(source) - current_ids
            stale_records = {record.id: record for record in self.storage.get_chunks(list(stale_ids))}
            orphaned = self.storage.sync_source(source, current_ids)
            for chunk_id in orphaned:
                record = stale_records.get(chunk_id)
                self.graph.remove_node(chunk_id)
                self.void.remove_chunk(chunk_id, record.embedding if record else None)
            removed = len(orphaned)

        if new_count or removed:
            with self._lock:
                self._save_snapshot()
                self.storage.log_event(
                    self.void.tick_count,
                    "perturbation",
                    {
                        "file": source,
                        "new_chunks": new_count,
                        "removed_stale_chunks": removed,
                        "permanent_failures": permanent_failures,
                    },
                )
                self._publish_runtime_status()
            logger.info("Perturbation: %s (+%d, -%d stale)", source, new_count, removed)
        return PerturbResult(new_count, had_failure, removed, permanent_failures)

    def _register_chunk(
        self,
        chunk_id: str,
        text: str,
        chunk_hash: str,
        source: str,
        embedding: Sequence[float],
    ) -> None:
        self._check_embedding_compatibility(len(embedding))
        assignment = self.void.choose_territory(embedding)
        self.storage.put_chunk(
            chunk_id, text, chunk_hash, source, embedding, territory=assignment.territory
        )
        self.void.register_chunk(chunk_id, time.time(), embedding, assignment=assignment)
        self.graph.add_node(chunk_id)

        nearest = [tid for tid, _sim in self.void.nearest_territories(embedding, k=3)]
        candidates = self.storage.candidate_chunks(nearest, self.config.similarity_candidate_cap + 1)
        candidate_ids = [record.id for record in candidates if record.id != chunk_id and record.embedding is not None]
        candidate_embeddings = [record.embedding for record in candidates if record.id != chunk_id and record.embedding is not None]
        self.graph.add_similarity_edges(
            chunk_id,
            embedding,
            candidate_ids,
            candidate_embeddings,
            threshold=self.config.similarity_threshold,
        )
        for edge in self.graph.edges_from(chunk_id):
            self.storage.put_edge(chunk_id, edge.target, edge.relation, edge.weight)

    def _check_embedding_compatibility(self, dim: int) -> None:
        recorded_dim = self.storage.get_meta("embedding_dim")
        if recorded_dim is not None and int(recorded_dim) != int(dim):
            raise RuntimeError(f"embedding dimension changed: database={recorded_dim}, current={dim}")
        model_id = getattr(self.embedder, "model_id", None)
        recorded_model = self.storage.get_meta("embedding_model")
        if recorded_model is not None and model_id is not None and recorded_model != model_id:
            raise RuntimeError(
                f"embedding model changed: database={recorded_model!r}, current={model_id!r}; re-embed into a new database"
            )
        if recorded_dim is None:
            self.storage.set_meta("embedding_dim", str(int(dim)))
        if recorded_model is None and model_id is not None:
            self.storage.set_meta("embedding_model", str(model_id))

    # ---------------- Endogenous idle loop ----------------
    def idle_tick(self) -> None:
        with self._lock:
            pending = self.storage.pending_stimuli(self.config.stimulus_batch)
            if pending:
                self.homeostasis.invalidate()
                self._was_settled = False
                self.void.apply_stimuli([(chunk_id, strength) for _sid, _type, chunk_id, strength in pending])
                self.storage.delete_stimuli([sid for sid, _type, _chunk, _strength in pending])

            self.void.advance(self.graph.weighted_neighbors)
            total_heat, active_count = self.void.active_heat()
            sample = self.homeostasis.record(self.void.tick_count, total_heat, active_count)
            self.storage.log_energy(
                sample.tick, sample.heat_energy, 0.0, sample.total_energy,
                sample.delta_frac, None, 0, sample.settled,
            )
            settled = self.homeostasis.is_settled()
            if settled and not self._was_settled:
                self.storage.log_event(sample.tick, "restabilized", {"total_heat": total_heat})
                logger.info("Restabilized at tick %d", sample.tick)
            self._was_settled = settled
            self._publish_runtime_status()
            if self.void.tick_count % self.config.snapshot_every_ticks == 0:
                self._save_snapshot()

    def _tick_once_safely(self) -> None:
        try:
            self.idle_tick()
        except Exception:
            logger.exception("idle_tick failed; tick loop continues")

    def run_forever(self) -> None:
        while not self._stop.is_set():
            self._tick_once_safely()
            self._stop.wait(self.config.tick_interval_s)

    def start(self) -> None:
        if self._thread is not None:
            return
        with self._lock:
            self.storage.set_meta("daemon_state", "running")
            self.storage.set_meta("tick_interval_s", str(self.config.tick_interval_s))
            self._publish_runtime_status()
        self._thread = threading.Thread(target=self.run_forever, name="vll-organism-tick", daemon=True)
        self._thread.start()

    # ---------------- Folder watcher ----------------
    def start_watching(self, folder: str, poll_interval_s: float = 5.0) -> str:
        if self._watch_thread is not None:
            return self.watch_folder_path or os.path.abspath(folder)
        resolved = ensure_watch_folder(folder)
        self.watch_folder_path = resolved
        with self._lock:
            self.storage.set_meta("watch_folder_path", resolved)
        self._watch_thread = threading.Thread(
            target=self.watch_forever,
            args=(resolved, poll_interval_s),
            name="vll-organism-watch",
            daemon=True,
        )
        self._watch_thread.start()
        return resolved

    def scan_watch_folder(self, folder: str) -> int:
        if not os.path.isdir(folder):
            self.watch_folder_ok = False
            with self._lock:
                self.storage.set_meta("watch_folder_ok", "0")
            if not self._watch_folder_missing_warned:
                logger.warning("Watch folder %s is missing", os.path.abspath(folder))
                self._watch_folder_missing_warned = True
            return 0
        self.watch_folder_ok = True
        self._watch_folder_missing_warned = False
        with self._lock:
            self.storage.set_meta("watch_folder_ok", "1")

        perturbed = 0
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if not os.path.isfile(path):
                continue
            mtime = os.path.getmtime(path)
            if self._known_files.get(path) == mtime:
                continue
            try:
                result = self.perturb_file(path)
            except Exception:
                logger.exception("Unexpected error perturbing %s -- retrying next scan", path)
                continue
            if result.retry_required:
                continue
            self._known_files[path] = mtime
            if result:
                perturbed += 1
        return perturbed

    def watch_forever(self, folder: str, poll_interval_s: float = 5.0) -> None:
        while not self._stop.is_set():
            try:
                self.scan_watch_folder(folder)
            except Exception:
                logger.exception("scan_watch_folder failed; watch loop continues")
            self._stop.wait(poll_interval_s)

    # ---------------- Shutdown / introspection ----------------
    def stop(self) -> None:
        if self._closed:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.config.tick_interval_s * 2)
        if self._watch_thread is not None:
            embed_timeout = max(0.0, float(getattr(self.embedder, "timeout", 0.0)))
            self._watch_thread.join(timeout=self.config.shutdown_grace_s + embed_timeout)
            if self._watch_thread.is_alive():
                # Do not close SQLite or release the writer lease underneath an
                # in-flight ingestion thread. The caller can retry stop after
                # the bounded embedding operation returns.
                raise RuntimeError(
                    "watcher did not stop within the configured shutdown grace; "
                    "organism state remains open and writer-owned to avoid shutdown corruption"
                )
        with self._lock:
            self._save_snapshot()
            self._publish_runtime_status()
            self.storage.set_meta("daemon_state", "stopped")
            self.storage.close()
            self._writer_lock.release()
            self._closed = True

    def status(self) -> dict:
        latest = self.storage.recent_energy(limit=1)
        return {
            "tick": self.void.tick_count,
            "chunks": self.storage.chunk_count(),
            "graph_nodes": self.graph.node_count(),
            "graph_edges": self.graph.edge_count(),
            "dynamics_stats": self.void.stats(),
            "latest_energy": latest[0] if latest else None,
            "settled": self.homeostasis.is_settled(),
            "watch_folder_path": self.watch_folder_path,
            "watch_folder_ok": self.watch_folder_ok,
        }


__all__ = ["Organism", "OrganismConfig", "PerturbResult", "ensure_watch_folder"]
