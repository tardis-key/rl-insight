from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rl_insight import agent_loop


def test_agent_loop_helpers_should_be_public_exports() -> None:
    import rl_insight

    assert callable(rl_insight.agent_loop_lane_id)
    assert callable(rl_insight.publish_agent_loop_session)


@dataclass
class Trajectory:
    chain_id: int | None
    reward_score: float | None
    num_turns: int | None


def test_agent_loop_lane_id_should_use_canonical_format() -> None:
    lane_id = agent_loop.agent_loop_lane_id("run-a", 1, 0, 2)

    assert lane_id == "run=run-a/sample=1/session=0/traj=2"


def test_publish_agent_loop_session_should_emit_dashboard_hierarchy(
    monkeypatch: Any,
) -> None:
    events: list[tuple[str, float, dict[str, Any]]] = []

    def metric_gauge(name: str, value: float, **labels: Any) -> None:
        events.append((name, value, labels))

    monkeypatch.setattr(agent_loop, "metric_gauge", metric_gauge)

    agent_loop.publish_agent_loop_session(
        run_id="run-a",
        sample=1,
        session=0,
        trajectories=[Trajectory(chain_id=2, reward_score=0.75, num_turns=3)],
        start_time_ns=1_000_000_000,
        end_time_ns=2_000_000_000,
    )

    assert [(name, value) for name, value, _ in events] == [
        ("agent_loop_run_info", 1.0),
        ("agent_loop_sample_info", 1.0),
        ("agent_loop_session_info", 1.0),
        ("agent_loop_traj_info", 1.0),
        ("agent_loop_first_turn_unixtime", 1.0),
        ("agent_loop_last_turn_unixtime", 2.0),
    ]
    assert events[3][2]["traj"] == "1"
    assert events[3][2]["leaf"] == "sample=1/session=0/traj=1"
    assert events[3][2]["title"] == "Trajectory #1 · reward 0.75 · 3 turns"


def test_publish_agent_loop_session_should_not_raise_when_gauge_fails(
    monkeypatch: Any,
) -> None:
    def metric_gauge(name: str, value: float, **labels: Any) -> None:
        raise RuntimeError("monitor unavailable")

    monkeypatch.setattr(agent_loop, "metric_gauge", metric_gauge)

    agent_loop.publish_agent_loop_session(
        run_id="run-a",
        sample=1,
        session=0,
        trajectories=[],
    )
