import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from vll_organism.graph import KnowledgeGraph
from vll_organism.embedder import HashEmbedder


def test_similarity_edges_respect_degree_cap():
    g = KnowledgeGraph(max_out_degree_similarity=3)
    emb = HashEmbedder(dim=64)
    center = emb.embed("the quick brown fox jumps over the lazy dog")
    # 10 near-duplicate candidates, all above threshold -- should still cap at 3.
    cand_ids = [f"c{i}" for i in range(10)]
    cand_embs = [emb.embed("the quick brown fox jumps over the lazy dog " + "x" * i) for i in range(10)]

    added = g.add_similarity_edges("center", center, cand_ids, cand_embs, threshold=0.1)
    assert added <= 3
    assert g.out_degree("center") <= 3


def test_dissimilar_candidates_get_no_edge():
    g = KnowledgeGraph(max_out_degree_similarity=5)
    emb = HashEmbedder(dim=64)
    a = emb.embed("quantum field theory and gauge symmetry")
    b = emb.embed("recipe for sourdough bread with rye starter")

    added = g.add_similarity_edges("a", a, ["b"], [b], threshold=0.9)
    assert added == 0
    assert g.neighbors("a") == [] or "b" not in g.neighbors("a")


def test_neighbors_are_bidirectional_view():
    g = KnowledgeGraph()
    g.add_edge("x", "y", relation="similar_to", weight=0.8)
    assert "y" in g.neighbors("x")
    assert "x" in g.neighbors("y")
