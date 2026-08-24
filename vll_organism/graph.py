"""Sparse semantic graph over durable chunk ids."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Tuple

from .dynamics import cosine_similarity


@dataclass(frozen=True)
class Edge:
    target: str
    relation: str
    weight: float


class KnowledgeGraph:
    """Directed storage with O(local-degree) bidirectional traversal.

    Similarity edges are stored once, but a reverse adjacency index avoids the
    previous O(N) scan required to discover incoming neighbors.
    """

    def __init__(self, max_out_degree_similarity: int = 6):
        if max_out_degree_similarity < 1:
            raise ValueError("max_out_degree_similarity must be >= 1")
        self._adj: Dict[str, Dict[str, Edge]] = {}
        self._rev: Dict[str, Dict[str, Edge]] = {}
        self.max_out_degree_similarity = int(max_out_degree_similarity)

    def add_node(self, node_id: str) -> None:
        self._adj.setdefault(node_id, {})
        self._rev.setdefault(node_id, {})

    def add_edge(self, source: str, target: str, relation: str, weight: float = 1.0) -> None:
        if source == target:
            return
        self.add_node(source)
        self.add_node(target)
        edge = Edge(target=target, relation=relation, weight=float(weight))
        existing = self._adj[source].get(target)
        if existing is None or edge.weight > existing.weight:
            self._adj[source][target] = edge
            self._rev[target][source] = Edge(target=source, relation=relation, weight=edge.weight)

    def remove_node(self, node_id: str) -> None:
        for target in list(self._adj.get(node_id, {})):
            self._rev.get(target, {}).pop(node_id, None)
        for source in list(self._rev.get(node_id, {})):
            self._adj.get(source, {}).pop(node_id, None)
        self._adj.pop(node_id, None)
        self._rev.pop(node_id, None)

    def neighbors(self, node_id: str) -> List[str]:
        return [node for node, _ in self.weighted_neighbors(node_id)]

    def weighted_neighbors(self, node_id: str, limit: int = 12) -> List[Tuple[str, float]]:
        combined: Dict[str, float] = {}
        for target, edge in self._adj.get(node_id, {}).items():
            combined[target] = max(combined.get(target, 0.0), edge.weight)
        for source, edge in self._rev.get(node_id, {}).items():
            combined[source] = max(combined.get(source, 0.0), edge.weight)
        scored = sorted(combined.items(), key=lambda item: item[1], reverse=True)
        return scored[:max(0, int(limit))]

    def out_degree(self, node_id: str) -> int:
        return len(self._adj.get(node_id, {}))

    def edges_from(self, node_id: str) -> List[Edge]:
        return list(self._adj.get(node_id, {}).values())

    def all_edges(self) -> Iterable[Tuple[str, Edge]]:
        for source, edges in self._adj.items():
            for edge in edges.values():
                yield source, edge

    def node_count(self) -> int:
        return len(self._adj)

    def edge_count(self) -> int:
        return sum(len(edges) for edges in self._adj.values())

    def add_similarity_edges(
        self,
        node_id: str,
        node_emb: Sequence[float],
        candidate_ids: Sequence[str],
        candidate_embs: Sequence[Sequence[float]],
        threshold: float = 0.55,
    ) -> int:
        if self.out_degree(node_id) >= self.max_out_degree_similarity:
            return 0
        scored: List[Tuple[float, str]] = []
        for candidate_id, candidate_emb in zip(candidate_ids, candidate_embs):
            if candidate_id == node_id:
                continue
            similarity = cosine_similarity(node_emb, candidate_emb)
            if similarity >= threshold:
                scored.append((similarity, candidate_id))
        scored.sort(reverse=True)
        budget = self.max_out_degree_similarity - self.out_degree(node_id)
        for similarity, candidate_id in scored[:budget]:
            self.add_edge(node_id, candidate_id, "similar_to", similarity)
        return min(budget, len(scored))

    def to_edge_list(self) -> List[Tuple[str, str, str, float]]:
        return [(source, edge.target, edge.relation, edge.weight) for source, edge in self.all_edges()]

    def load_edge_list(self, rows: Iterable[Tuple[str, str, str, float]]) -> None:
        for source, target, relation, weight in rows:
            self.add_edge(source, target, relation, weight)


__all__ = ["KnowledgeGraph", "Edge"]
