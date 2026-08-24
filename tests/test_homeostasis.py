from vll_organism.dynamics import DynamicsConfig, KnowledgeDynamics
from vll_organism.homeostasis import HomeostasisTracker


def _same_topic_embedding(x: float):
    return [x, 1.0 - x, 0.0]


def test_idle_ticks_decay_activation_without_deleting_knowledge():
    dynamics = KnowledgeDynamics(DynamicsConfig(heat_half_life_ticks=4, heat_floor=1e-3))
    dynamics.register_chunk("a", 0.0, _same_topic_embedding(1.0))
    initial_heat, _ = dynamics.active_heat()
    for _ in range(8):
        dynamics.advance(lambda _node, _limit: [])
    later_heat, _ = dynamics.active_heat()

    assert dynamics.get_state("a") is not None
    assert later_heat < initial_heat * 0.3
    assert dynamics.tick_count == 8


def test_diffusion_moves_heat_only_over_graph_neighbors_and_conserves_before_decay():
    cfg = DynamicsConfig(
        heat_half_life_ticks=10_000,
        diffusion_fraction=0.25,
        heat_floor=1e-8,
    )
    dynamics = KnowledgeDynamics(cfg)
    dynamics.register_chunk("a", 0.0, [1.0, 0.0])
    dynamics.register_chunk("b", 0.0, [1.0, 0.0], cold=True)
    dynamics.register_chunk("c", 0.0, [0.0, 1.0], cold=True)
    heat_before, _ = dynamics.active_heat()

    def neighbors(node, _limit):
        return [("b", 1.0)] if node == "a" else []

    dynamics.advance(neighbors)
    a = dynamics.get_state("a")
    b = dynamics.get_state("b")
    c = dynamics.get_state("c")
    assert a.heat < heat_before
    assert b.heat > 0.0
    assert c.heat == 0.0
    heat_after, _ = dynamics.active_heat()
    assert heat_after < heat_before  # only the global exponential decay loses heat
    assert heat_after > heat_before * 0.99


def test_settle_detector_requires_consecutive_empty_active_frontier():
    tracker = HomeostasisTracker(window=3, quiet_heat=1e-3)
    tracker.record(1, total_heat=0.5, active_count=2)
    tracker.record(2, total_heat=0.0, active_count=0)
    tracker.record(3, total_heat=0.0, active_count=0)
    assert tracker.is_settled() is False
    tracker.record(4, total_heat=0.0, active_count=0)
    assert tracker.is_settled() is True


def test_registration_and_feedback_do_not_advance_endogenous_time():
    dynamics = KnowledgeDynamics()
    dynamics.register_chunk("a", 0.0, [1.0, 0.0])
    dynamics.register_chunk("b", 0.0, [0.0, 1.0])
    assert dynamics.tick_count == 0
    dynamics.apply_stimuli([("a", 0.9), ("b", 0.4)])
    assert dynamics.tick_count == 0
    dynamics.advance(lambda _node, _limit: [])
    assert dynamics.tick_count == 1
