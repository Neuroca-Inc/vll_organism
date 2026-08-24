import os
import tempfile

from vll_organism.dynamics import DynamicsConfig, KnowledgeDynamics
from vll_organism.embedder import HashEmbedder
from vll_organism.organism import Organism, OrganismConfig


def _write(directory, name, text):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def test_perturb_then_restabilize_end_to_end():
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "organism.db")
        dynamics = KnowledgeDynamics(DynamicsConfig(
            heat_half_life_ticks=3,
            heat_floor=0.02,
            diffusion_fraction=0.10,
        ))
        organism = Organism(
            OrganismConfig(db_path=db_path, homeostasis_window=3),
            HashEmbedder(dim=64),
            void_memory=dynamics,
        )
        path = _write(
            td,
            "corpus1.txt",
            "Void dynamics treats memory as a physical field rather than a cache.\n\n"
            "A persistent knowledge organism keeps source truth while transient activation relaxes.\n\n"
            "Related chunks exchange activation over sparse semantic edges.",
        )
        result = organism.perturb_file(path)
        assert result.new_chunks > 0
        assert organism.void.tick_count == 0, "ingestion cardinality must not advance endogenous time"

        for _ in range(80):
            organism.idle_tick()
            if organism.homeostasis.is_settled():
                break
        assert organism.homeostasis.is_settled()
        assert organism.storage.chunk_count() == result.new_chunks
        assert len(list(organism.void.iter_states())) == result.new_chunks

        again = organism.perturb_file(path)
        assert again.new_chunks == 0
        assert again.removed_stale_chunks == 0
        organism.stop()


def test_restart_restores_state_from_disk_without_expiring_chunks():
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "organism.db")
        path = _write(td, "a.txt", "A perturbation about a sparse knowledge graph and local dynamics.")
        organism = Organism(OrganismConfig(db_path=db_path), HashEmbedder(dim=64))
        count = organism.perturb_file(path).new_chunks
        for _ in range(20):
            organism.idle_tick()
        organism.stop()

        restored = Organism(OrganismConfig(db_path=db_path), HashEmbedder(dim=64))
        assert restored.storage.chunk_count() == count
        assert len(restored.void.ids()) == count
        assert restored.void.tick_count >= 20
        restored.stop()


def test_related_chunks_share_territory_and_dissimilar_chunk_can_separate():
    dynamics = KnowledgeDynamics(DynamicsConfig(territory_similarity_threshold=0.5))
    a = dynamics.register_chunk("a", 0.0, [1.0, 0.0, 0.0], cold=True)
    b = dynamics.register_chunk("b", 0.0, [0.95, 0.05, 0.0], cold=True)
    c = dynamics.register_chunk("c", 0.0, [0.0, 0.0, 1.0], cold=True)
    assert a.territory == b.territory
    assert c.territory != a.territory
