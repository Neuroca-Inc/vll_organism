from __future__ import annotations

from dataclasses import dataclass
import json
import random
from pathlib import Path

import pytest

from vll_organism.dynamics import KnowledgeDynamics
from vll_organism.graph import KnowledgeGraph
from vll_organism.storage import Storage

from rung0_common import (
    clean_template, fraction_from_assignment, load_frozen_corpus, real_fraction,
    resolve_source, run_heat, shortest_hops,
)
from rung0_stats import benjamini_hochberg, holm_bonferroni, null_summary
from rung0_controls import degree_matched_assignment, legacy_provider, random_assignment
from freeze_snapshot import backup_sqlite, integrity_report


def make_db(tmp_path: Path) -> Path:
    db = tmp_path / "fixture.db"
    storage = Storage(str(db))
    try:
        embeddings = {
            "a": [1.0, 0.0, 0.0],
            "b": [0.9, 0.1, 0.0],
            "c": [0.8, 0.2, 0.0],
            "d": [0.0, 1.0, 0.0],
            "e": [0.0, 0.9, 0.1],
        }
        sources = {
            "a": "/corpus/origin.md",
            "b": "/corpus/origin.md",
            "c": "/corpus/related.md",
            "d": "/corpus/other.md",
            "e": "/corpus/other.md",
        }
        dynamics = KnowledgeDynamics()
        for i, cid in enumerate(embeddings):
            emb = embeddings[cid]
            assignment = dynamics.choose_territory(emb)
            storage.put_chunk(cid, cid, f"hash-{cid}", sources[cid], emb, territory=assignment.territory)
            dynamics.register_chunk(cid, float(i + 1), emb, assignment=assignment, cold=True)
        edges = [
            ("a", "b", 0.9),
            ("b", "c", 0.8),
            ("b", "d", 0.6),
            ("d", "e", 0.8),
        ]
        for source, target, weight in edges:
            storage.put_edge(source, target, "similar_to", weight)
        storage.save_dynamics_snapshot(dynamics.tick_count, dynamics.to_dict())
        storage.set_meta("embedding_dim", "3")
        storage.set_meta("embedding_model", "test:fixture")
    finally:
        storage.close()
    return db


def test_optimized_identity_relabel_matches_runtime_provider(tmp_path):
    db = make_db(tmp_path)
    corpus = load_frozen_corpus(str(db))
    template = clean_template(corpus)
    origin = resolve_source(corpus, "origin.md")
    related = resolve_source(corpus, "related.md")
    origin_ids = {cid for cid, srcs in corpus.source_sets.items() if origin in srcs}
    related_ids = {cid for cid, srcs in corpus.source_sets.items() if related in srcs}

    real = run_heat(template, corpus, "a", 10.0, [20])
    auc = real.node_auc_by_horizon[20]
    rng = random.Random(19)
    for _ in range(20):
        mapping = random_assignment(corpus.ids, {"a"}, rng)
        expected, *_ = fraction_from_assignment(auc, mapping, origin_ids, related_ids)
        legacy = run_heat(
            template, corpus, "a", 10.0, [20], provider=legacy_provider(corpus, mapping)
        )
        actual, *_ = real_fraction(legacy.node_auc_by_horizon[20], origin_ids, related_ids)
        assert expected == pytest.approx(actual, abs=1e-12)


def test_degree_matched_assignment_preserves_exact_degree(tmp_path):
    db = make_db(tmp_path)
    corpus = load_frozen_corpus(str(db))
    mapping = degree_matched_assignment(corpus, {"a", "b"}, random.Random(7))
    for position, identity in mapping.items():
        if position in {"a", "b"}:
            assert identity == position
        else:
            assert len(corpus.graph.weighted_neighbors(position, 12)) == len(
                corpus.graph.weighted_neighbors(identity, 12)
            )


def test_disconnected_related_is_unreachable(tmp_path):
    db = make_db(tmp_path)
    corpus = load_frozen_corpus(str(db))
    assert shortest_hops(corpus, "e", {"c"}) == 3
    assert shortest_hops(corpus, "a", {"missing"}) is None


def test_zero_over_zero_effect_is_not_infinite():
    summary = null_summary(0.0, [0.0, 0.0, 0.0])
    assert summary["effect_ratio"] is None
    assert summary["delta"] == 0.0


def test_multiple_testing_adjustments_are_monotone():
    p = [0.001, 0.01, 0.03, 0.2]
    bh = benjamini_hochberg(p)
    holm = holm_bonferroni(p)
    assert all(0 <= x <= 1 for x in bh if x is not None)
    assert all(0 <= x <= 1 for x in holm if x is not None)
    assert bh[0] <= bh[1] <= bh[2] <= bh[3]
    assert holm[0] <= holm[1] <= holm[2] <= holm[3]


def test_online_backup_is_consistent_and_source_survives(tmp_path):
    source = make_db(tmp_path)
    before = source.read_bytes()
    frozen = tmp_path / "frozen.db"
    backup_sqlite(str(source), str(frozen))
    report = integrity_report(str(frozen))
    assert report["quick_check"] == ["ok"]
    assert report["foreign_key_violations"] == []
    assert report["table_counts"]["chunks"] == 5
    assert source.read_bytes() == before


def test_ambiguous_basename_is_rejected(tmp_path):
    db = tmp_path / "ambiguous.db"
    storage = Storage(str(db))
    try:
        dynamics = KnowledgeDynamics()
        for i, source in enumerate(("/x/same.md", "/y/same.md")):
            cid = f"x{i}"
            emb = [1.0, float(i), 0.0]
            assignment = dynamics.choose_territory(emb)
            storage.put_chunk(cid, cid, cid, source, emb, territory=assignment.territory)
            dynamics.register_chunk(cid, i + 1, emb, assignment=assignment, cold=True)
        storage.save_dynamics_snapshot(0, dynamics.to_dict())
        storage.set_meta("embedding_dim", "3")
    finally:
        storage.close()
    corpus = load_frozen_corpus(str(db))
    with pytest.raises(ValueError, match="ambiguous"):
        resolve_source(corpus, "same.md")

from order_sensitivity import SimilarityOracle, replay_graph, structural_edges
from source_matrix import target_destination_shares


def test_replay_graph_is_deterministic_for_fixed_order(tmp_path):
    db = make_db(tmp_path)
    corpus = load_frozen_corpus(str(db))
    order = sorted(corpus.ids, key=lambda cid: (corpus.records[cid].created_at, cid))
    similarity = SimilarityOracle(corpus, max_entries=16)
    g1 = replay_graph(
        corpus, order, similarity,
        similarity_threshold=0.55, similarity_candidate_cap=200, max_out_degree=6,
    )
    g2 = replay_graph(
        corpus, order, similarity,
        similarity_threshold=0.55, similarity_candidate_cap=200, max_out_degree=6,
    )
    assert structural_edges(g1) == structural_edges(g2)


def test_source_matrix_destination_share_uses_all_foreign_heat_denominator(tmp_path):
    db = make_db(tmp_path)
    corpus = load_frozen_corpus(str(db))
    source_ids = {
        source: {cid for cid, srcs in corpus.source_sets.items() if source in srcs}
        for source in corpus.source_names
    }
    origin = resolve_source(corpus, "origin.md")
    related = resolve_source(corpus, "related.md")
    other = resolve_source(corpus, "other.md")
    auc = {"a": 5.0, "b": 3.0, "c": 2.0, "d": 6.0, "e": 2.0}
    identity = {cid: cid for cid in auc}
    shares = target_destination_shares(auc, identity, source_ids[origin], source_ids)
    assert shares[related] == pytest.approx(2.0 / 10.0)
    assert shares[other] == pytest.approx(8.0 / 10.0)


def test_similarity_oracle_is_bounded(tmp_path):
    db = make_db(tmp_path)
    corpus = load_frozen_corpus(str(db))
    oracle = SimilarityOracle(corpus, max_entries=2)
    pairs = [("a", "b"), ("a", "c"), ("a", "d"), ("a", "b")]
    for left, right in pairs:
        oracle(left, right)
    stats = oracle.stats()
    assert stats["peak_entries"] <= 2
    assert stats["evictions"] >= 1


def test_heat_run_reports_active_budget_diagnostics(tmp_path):
    db = make_db(tmp_path)
    corpus = load_frozen_corpus(str(db))
    run = run_heat(clean_template(corpus), corpus, "a", 10.0, [20])
    assert run.max_active_nodes >= 1
    assert run.budget_saturation_ticks >= 0
    assert run.max_active_nodes <= len(corpus.ids)
