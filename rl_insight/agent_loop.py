"""Agent-loop dashboard protocol helpers.

This module owns the metric names, lane IDs, and hierarchy labels consumed by
the Agent Loop Trajectory dashboard. Training frameworks should adapt their
domain objects to these helpers instead of reimplementing the protocol.
"""

from __future__ import annotations

import logging
from typing import Any

from .api import metric_gauge

logger = logging.getLogger(__name__)


def _agent_loop_sess_key(sample: Any, session: Any) -> str:
    return f"sample={sample}/session={session}"


def _agent_loop_leaf(sample: Any, session: Any, traj: Any) -> str:
    return f"sample={sample}/session={session}/traj={traj}"


def agent_loop_lane_id(run_id: Any, sample: Any, session: Any, traj: Any) -> str:
    """Return the canonical lane ID for one agent-loop trajectory."""
    return f"run={run_id}/{_agent_loop_sess_key(sample, session)}/traj={traj}"


def _metric_gauge(name: str, value: float, **labels: Any) -> None:
    """Publish one dashboard gauge without breaking the training process."""
    try:
        metric_gauge(
            str(name),
            float(value),
            **{str(key): str(label_value) for key, label_value in labels.items()},
        )
    except Exception:
        logger.exception("[rl-insight] failed to publish agent-loop gauge %s", name)


def _agent_loop_reward(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def publish_agent_loop_session(
    *,
    run_id: Any,
    sample: Any,
    session: Any,
    trajectories: list[Any],
    start_time_ns: int | None = None,
    end_time_ns: int | None = None,
) -> None:
    """Publish dashboard hierarchy gauges for one finalized agent session."""
    run_id = str(run_id)
    sample = str(sample)
    session = str(session)

    _metric_gauge(
        "agent_loop_run_info",
        1.0,
        run_id=run_id,
        title=f"Run · {run_id}",
    )
    _metric_gauge(
        "agent_loop_sample_info",
        1.0,
        run_id=run_id,
        sample=sample,
        title=f"Sample {sample}",
    )
    sess_key = _agent_loop_sess_key(sample, session)
    _metric_gauge(
        "agent_loop_session_info",
        1.0,
        run_id=run_id,
        sample=sample,
        session=session,
        sess_key=sess_key,
        title=f"Session {session}",
    )

    for index, traj in enumerate(trajectories):
        chain_id = getattr(traj, "chain_id", None)
        traj_id = str(int(chain_id) - 1) if chain_id is not None else str(index)
        reward = _agent_loop_reward(getattr(traj, "reward_score", 0))
        turns = int(getattr(traj, "num_turns", 0) or 0)
        leaf = _agent_loop_leaf(sample, session, traj_id)
        _metric_gauge(
            "agent_loop_traj_info",
            1.0,
            run_id=run_id,
            sample=sample,
            session=session,
            traj=traj_id,
            leaf=leaf,
            title=f"Trajectory #{traj_id} · reward {reward:g} · {turns} turns",
        )

    if start_time_ns is not None:
        _metric_gauge(
            "agent_loop_first_turn_unixtime",
            float(start_time_ns) / 1_000_000_000.0,
            run_id=run_id,
        )
    if end_time_ns is not None:
        _metric_gauge(
            "agent_loop_last_turn_unixtime",
            float(end_time_ns) / 1_000_000_000.0,
            run_id=run_id,
        )
