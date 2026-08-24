#!/usr/bin/env python3
"""Create an immutable, transactionally consistent experiment snapshot of a live VLL DB."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys

from rung0_common import atomic_json, base_name, load_frozen_corpus, sha256_file, source_inventory


CRITICAL_CODE_PATHS = (
    "pyproject.toml",
    "README.md",
    "vll_organism/dynamics.py",
    "vll_organism/graph.py",
    "vll_organism/storage.py",
    "vll_organism/organism.py",
    "vll_organism/retrieval.py",
    "vll_organism/ingest.py",
    "vll_organism/cli.py",
    "research/rung0_battery/rung0_common.py",
    "research/rung0_battery/rung0_stats.py",
    "research/rung0_battery/rung0_controls.py",
    "research/rung0_battery/pair_battery.py",
    "research/rung0_battery/source_matrix.py",
    "research/rung0_battery/order_sensitivity.py",
    "research/rung0_battery/run_plan.py",
    "research/rung0_battery/freeze_snapshot.py",
    "research/rung0_battery/rung0_plan.json",
    "research/rung0_battery/README.md",
    "research/rung0_battery/tests/test_rung0_battery.py",
)


def stable_file_record(path: str) -> dict:
    p = Path(path)
    before = p.stat()
    digest = sha256_file(p)
    after = p.stat()
    stable = before.st_size == after.st_size and before.st_mtime_ns == after.st_mtime_ns
    return {
        "path": str(p.resolve()),
        "basename": p.name,
        "exists": True,
        "size": after.st_size,
        "mtime_ns": after.st_mtime_ns,
        "sha256": digest,
        "stable_during_hash": stable,
    }


def backup_sqlite(source: str, destination: str) -> None:
    src_uri = Path(source).resolve().as_uri() + "?mode=ro"
    src = sqlite3.connect(src_uri, uri=True, timeout=30.0)
    dest = sqlite3.connect(destination, timeout=30.0)
    try:
        src.execute("PRAGMA busy_timeout=30000")
        src.backup(dest, pages=256, sleep=0.01)
        dest.commit()
    finally:
        dest.close()
        src.close()


def integrity_report(db_path: str) -> dict:
    conn = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)
    try:
        quick = [row[0] for row in conn.execute("PRAGMA quick_check").fetchall()]
        fk = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check").fetchall()]
        tables = {
            row[0]: int(row[1])
            for row in conn.execute(
                "SELECT 'chunks',COUNT(*) FROM chunks UNION ALL "
                "SELECT 'edges',COUNT(*) FROM edges UNION ALL "
                "SELECT 'chunk_sources',COUNT(*) FROM chunk_sources UNION ALL "
                "SELECT 'stimuli',COUNT(*) FROM stimuli UNION ALL "
                "SELECT 'events',COUNT(*) FROM events UNION ALL "
                "SELECT 'energy_log',COUNT(*) FROM energy_log"
            ).fetchall()
        }
        return {"quick_check": quick, "foreign_key_violations": fk, "table_counts": tables}
    finally:
        conn.close()


def source_membership_records(corpus) -> list[dict]:
    rows = []
    for source in corpus.source_names:
        ids = sorted(cid for cid, sources in corpus.source_sets.items() if source in sources)
        membership_payload = "\n".join(ids).encode("utf-8")
        content_payload = "\n".join(
            f"{cid}:{corpus.records[cid].hash}" for cid in ids
        ).encode("utf-8")
        rows.append({
            "source": source,
            "basename": base_name(source),
            "chunks": len(ids),
            "chunk_membership_sha256": hashlib.sha256(membership_payload).hexdigest(),
            "chunk_content_hashes_sha256": hashlib.sha256(content_payload).hexdigest(),
        })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="Freeze a consistent VLL Rung-0 experiment snapshot")
    ap.add_argument("--db", default="./organism.db", help="live or stopped source SQLite DB")
    ap.add_argument("--out", default=None, help="new snapshot directory; default is timestamped")
    ap.add_argument("--repo-root", default=".", help="repository root used for code hashes")
    ap.add_argument("--note", default="Rung-0 routing characterization snapshot")
    args = ap.parse_args()

    source_db = str(Path(args.db).resolve())
    if not os.path.isfile(source_db):
        raise SystemExit(f"database not found: {source_db}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out or f"research/rung0_snapshots/rung0_{stamp}").resolve()
    if out.exists():
        raise SystemExit(f"refusing to overwrite existing snapshot directory: {out}")
    out.mkdir(parents=True)
    snapshot_db = out / "organism.db"

    try:
        backup_sqlite(source_db, str(snapshot_db))
        integrity = integrity_report(str(snapshot_db))
        if integrity["quick_check"] != ["ok"] or integrity["foreign_key_violations"]:
            raise RuntimeError(f"snapshot integrity failed: {integrity}")

        corpus = load_frozen_corpus(str(snapshot_db))
        source_files = []
        for source in corpus.source_names:
            if os.path.isfile(source):
                source_files.append(stable_file_record(source))
            else:
                source_files.append({
                    "path": source,
                    "basename": base_name(source),
                    "exists": False,
                    "stable_during_hash": None,
                })

        repo_root = Path(args.repo_root).resolve()
        code_hashes = []
        for rel in CRITICAL_CODE_PATHS:
            path = repo_root / rel
            if path.is_file():
                record = stable_file_record(str(path))
                record["relative_path"] = rel
                code_hashes.append(record)
            else:
                code_hashes.append({"relative_path": rel, "exists": False})

        manifest = {
            "schema_version": 1,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "note": args.note,
            "source_db": source_db,
            "snapshot_db": str(snapshot_db),
            "snapshot_sha256": sha256_file(snapshot_db),
            "integrity": integrity,
            "snapshot_tick": corpus.snapshot_tick,
            "dynamics_config": corpus.dynamics_config.__dict__,
            "meta": corpus.meta,
            "chunks": len(corpus.ids),
            "graph_nodes": corpus.graph.node_count(),
            "graph_edges": corpus.graph.edge_count(),
            "connected_graph_nodes": sum(1 for cid in corpus.ids if corpus.graph.weighted_neighbors(cid, 12)),
            "source_inventory": source_inventory(corpus),
            "db_source_membership": source_membership_records(corpus),
            "source_files": source_files,
            "source_file_note": (
                "Source-file hashes are contemporaneous references. The frozen SQLite database and "
                "db_source_membership records are authoritative for the exact experimental corpus state."
            ),
            "critical_code_hashes": code_hashes,
        }
        atomic_json(out / "manifest.json", manifest)

        unstable = [x["path"] for x in source_files if x.get("stable_during_hash") is False]
        if unstable:
            raise RuntimeError(
                "one or more corpus files changed while provenance was being hashed; retry freeze: "
                + ", ".join(unstable)
            )

        print("RUNG0_SNAPSHOT_READY")
        print(f"snapshot_dir: {out}")
        print(f"db: {snapshot_db}")
        print(f"sha256: {manifest['snapshot_sha256']}")
        print(
            f"tick={corpus.snapshot_tick} chunks={len(corpus.ids)} "
            f"nodes={corpus.graph.node_count()} edges={corpus.graph.edge_count()}"
        )
        print(f"sources={len(corpus.source_names)}")
    except Exception:
        shutil.rmtree(out, ignore_errors=True)
        raise


if __name__ == "__main__":
    main()
