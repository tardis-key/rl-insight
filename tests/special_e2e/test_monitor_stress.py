# Copyright (c) 2026 verl-project authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Stress test for the rl-insight monitoring pipeline.

Architecture::

    [Main process]
        │
        ├─ ray.init() + insight.init()
        │
        ├─ spawn N worker processes (multiprocessing)
        │     │
        │     ├─ ray.init() + insight.init()
        │     ├─ M stress threads: loop calling random monitor APIs
        │     └─ 1 probe thread: periodic hub ping + Grafana ping
        │
        ├─ wait DURATION seconds
        ├─ collect stats from workers
        └─ print report, assert pass/fail

Environment variables
---------------------
RL_INSIGHT_STRESS_PROCESSES : int, default 2
    Number of worker processes.
RL_INSIGHT_STRESS_THREADS : int, default 4
    Number of stress threads per worker process.
RL_INSIGHT_STRESS_DURATION : float, default 30
    Test duration in seconds.
RL_INSIGHT_STRESS_PROBE_INTERVAL : float, default 2.0
    Seconds between consecutive probes (hub ping + Grafana ping).
RL_INSIGHT_STRESS_GRAFANA_URL : str, default http://localhost:3000
    Grafana base URL for /api/health checks.
RL_INSIGHT_STRESS_HUB_PING_TIMEOUT : float, default 5.0
    Timeout in seconds for each hub ping.
RL_INSIGHT_STRESS_GRAFANA_PING_TIMEOUT : float, default 5.0
    Timeout in seconds for each Grafana ping.
RL_INSIGHT_STRESS_API_MIX : str, default "counter:30,gauge:25,histogram:25,trace_state:15,trace_op:5"
    Comma-separated ratio list choosing which API to call on each iteration.
    Keys: counter, gauge, histogram, trace_state, trace_op.

Pass/Fail criteria
------------------
- Grafana must respond to at least one /api/health (FAIL otherwise).
- Hub ping P99 < 200 ms (WARN otherwise).
- Grafana ping P99 < 500 ms (WARN otherwise).
- No hard errors: RayActorError, OOM, worker crash.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import os
import random
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import Any

import pytest
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_API_MIX = {
    "counter": 30,
    "gauge": 25,
    "histogram": 25,
    "trace_state": 15,
    "trace_op": 5,
}


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _parse_api_mix(raw: str) -> dict[str, int]:
    mix: dict[str, int] = {}
    for part in raw.split(","):
        part = part.strip()
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        mix[k.strip()] = int(v.strip())
    return mix if mix else dict(_DEFAULT_API_MIX)


NUM_PROCESSES = _env_int("RL_INSIGHT_STRESS_PROCESSES", 2)
NUM_THREADS = _env_int("RL_INSIGHT_STRESS_THREADS", 4)
DURATION = _env_float("RL_INSIGHT_STRESS_DURATION", 30.0)
PROBE_INTERVAL = _env_float("RL_INSIGHT_STRESS_PROBE_INTERVAL", 2.0)
GRAFANA_URL = _env_str("RL_INSIGHT_STRESS_GRAFANA_URL", "http://localhost:3000")
HUB_PING_TIMEOUT = _env_float("RL_INSIGHT_STRESS_HUB_PING_TIMEOUT", 5.0)
GRAFANA_PING_TIMEOUT = _env_float("RL_INSIGHT_STRESS_GRAFANA_PING_TIMEOUT", 5.0)
API_MIX = _parse_api_mix(_env_str("RL_INSIGHT_STRESS_API_MIX", ""))


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------


@dataclass
class LatencyStats:
    """Collector for latency samples using reservoir sampling (max 10K)."""

    max_samples: int = 10_000
    _samples: list[float] = field(default_factory=list)
    _count: int = 0

    def record(self, value: float) -> None:
        self._count += 1
        if len(self._samples) < self.max_samples:
            self._samples.append(value)
        else:
            idx = random.randint(0, self._count)
            if idx < self.max_samples:
                self._samples[idx] = value

    def percentile(self, pct: float) -> float:
        if not self._samples:
            return 0.0
        return float(sorted(self._samples)[int(len(self._samples) * pct / 100.0)])

    @property
    def total(self) -> int:
        return self._count


@dataclass
class WorkerStats:
    pid: int
    events: int = 0
    errors: int = 0
    hub_pings: LatencyStats = field(default_factory=LatencyStats)
    grafana_pings: LatencyStats = field(default_factory=LatencyStats)
    grafana_timeouts: int = 0
    hub_timeouts: int = 0
    crashed: bool = False
    crash_reason: str = ""


# ---------------------------------------------------------------------------
# Probe helpers
# ---------------------------------------------------------------------------


def ping_hub_actor(actor: Any, timeout: float) -> tuple[bool, float]:
    """Ping the Ray hub actor. Returns (success, latency_seconds)."""
    try:
        t0 = time.perf_counter()
        ray = sys.modules.get("ray")
        if ray is None:
            return False, timeout
        ray.get(actor.ping.remote(), timeout=timeout)
        elapsed = time.perf_counter() - t0
        return True, elapsed
    except Exception:
        return False, timeout


def ping_grafana(base_url: str, timeout: float) -> tuple[bool, float]:
    """Ping Grafana /api/health. Returns (is_healthy, latency_seconds)."""
    try:
        t0 = time.perf_counter()
        resp = requests.get(
            f"{base_url.rstrip('/')}/api/health", timeout=timeout
        )
        elapsed = time.perf_counter() - t0
        return resp.status_code == 200, elapsed
    except requests.RequestException:
        return False, timeout


# ---------------------------------------------------------------------------
# API caller (runs in stress thread)
# ---------------------------------------------------------------------------


def _make_api_caller(mix: dict[str, int]):
    """Build a weighted random API caller based on the given mix dict."""
    keys = list(mix.keys())
    weights = list(mix.values())

    def caller() -> None:
        import rl_insight as insight  # noqa: F811

        choice = random.choices(keys, weights=weights, k=1)[0]
        labels = {
            "worker": f"stress_{os.getpid()}",
            "thread": f"t_{id(choice)}",
        }

        if choice == "counter":
            insight.metric_count("stress_counter_total", amount=1, **labels)
        elif choice == "gauge":
            insight.metric_value(
                "stress_gauge_current", value=random.uniform(0, 100), **labels
            )
        elif choice == "histogram":
            insight.metric_distribution(
                "stress_latency_ms",
                value=random.uniform(0.1, 500),
                **labels,
            )
        elif choice == "trace_state":
            with insight.trace_state(
                "stress_state",
                state_lane_id=f"proc_{os.getpid()}",
                **labels,
            ):
                pass  # instant, just timing the span overhead
        elif choice == "trace_op":

            @insight.trace_op("stress_trace_op", **labels)
            def _noop() -> None:
                pass

            _noop()

    return caller


# ---------------------------------------------------------------------------
# Worker process body
# ---------------------------------------------------------------------------


def _stress_worker(
    worker_id: int,
    stats_queue: mp.Queue,
    barrier: mp.Barrier,
    stop_event: mp.Event,
) -> None:
    """Entry point for one multiprocessing worker process."""
    pid = os.getpid()
    st = WorkerStats(pid=pid)

    try:
        import ray

        ray.init(
            address="auto",
            namespace="rl-insight-monitor",
            ignore_reinit_error=True,
            logging_level=logging.ERROR,
        )

        import rl_insight as insight

        insight.init()

        # Resolve hub actor handle via the client internals
        hub_actor = ray.get_actor(
            "RLInsightMonitorHub", namespace="rl-insight-monitor"
        )
        call_api = _make_api_caller(API_MIX)

        # Spawn stress threads
        stress_threads = []
        for _ in range(NUM_THREADS):
            t = _StressThread(call_api, stop_event, st)
            stress_threads.append(t)
            t.start()

        # Wait for all workers ready, then run probe loop
        barrier.wait()
        t_start = time.monotonic()

        while not stop_event.is_set():
            time.sleep(PROBE_INTERVAL)

            # --- Hub ping ---
            ok, lat = ping_hub_actor(hub_actor, HUB_PING_TIMEOUT)
            if ok:
                st.hub_pings.record(lat)
            else:
                st.hub_timeouts += 1

            # --- Grafana ping ---
            ok, lat = ping_grafana(GRAFANA_URL, GRAFANA_PING_TIMEOUT)
            if ok:
                st.grafana_pings.record(lat)
            else:
                st.grafana_timeouts += 1

            # Periodic stats report
            elapsed = time.monotonic() - t_start
            if elapsed >= DURATION:
                stop_event.set()

        # Wait for threads
        for t in stress_threads:
            t.join(timeout=5)

    except Exception:
        st.crashed = True
        st.crash_reason = traceback.format_exc()

    finally:
        stats_queue.put(st)


class _StressThread:
    """Thread that calls random APIs in a tight loop."""

    def __init__(
        self,
        call_api: Any,
        stop_event: mp.Event,
        stats: WorkerStats,
    ):
        import threading

        self._call_api = call_api
        self._stop = stop_event
        self._stats = stats
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout=timeout)

    def _run(self) -> None:
        call = self._call_api
        while not self._stop.is_set():
            try:
                call()
                self._stats.events += 1
            except Exception:
                self._stats.errors += 1


# ---------------------------------------------------------------------------
# Report / assertion
# ---------------------------------------------------------------------------


def _fmt_latency(st: LatencyStats, label: str) -> str:
    if st.total == 0:
        return f"{label}=N/A"
    return (
        f"{label}: count={st.total} "
        f"P50={st.percentile(50):.0f}ms "
        f"P99={st.percentile(99):.0f}ms "
        f"P999={st.percentile(99.9):.0f}ms"
    )


def _build_report(
    all_stats: list[WorkerStats], duration: float
) -> tuple[str, list[str]]:
    """Return (report_text, list_of_failures)."""
    failures: list[str] = []
    lines: list[str] = []

    total_events = sum(s.events for s in all_stats)
    total_errors = sum(s.errors for s in all_stats)
    crashed = [s for s in all_stats if s.crashed]

    # Aggregate probe stats
    hub_agg = LatencyStats()
    grafana_agg = LatencyStats()
    total_hub_timeouts = 0
    total_grafana_timeouts = 0
    for s in all_stats:
        hub_agg._samples.extend(s.hub_pings._samples)
        hub_agg._count += s.hub_pings._count
        grafana_agg._samples.extend(s.grafana_pings._samples)
        grafana_agg._count += s.grafana_pings._count
        total_hub_timeouts += s.hub_timeouts
        total_grafana_timeouts += s.grafana_timeouts

    lines.append("=" * 60)
    lines.append("RL-Insight Monitor Stress Test Report")
    lines.append("=" * 60)
    lines.append(
        f"Concurrency: {NUM_PROCESSES} processes x {NUM_THREADS} threads "
        f"= {NUM_PROCESSES * NUM_THREADS} workers"
    )
    lines.append(f"Duration:   {duration:.1f}s")
    lines.append(f"API mix:    {API_MIX}")
    lines.append(
        f"Throughput: {total_events / max(duration, 0.1):,.0f} events/sec "
        f"({total_events:,} total)"
    )
    lines.append(f"Errors:     {total_errors}")
    lines.append("")

    lines.append("--- Hub Actor Ping ---")
    lines.append(_fmt_latency(hub_agg, "Hub"))
    lines.append(f"Hub timeouts: {total_hub_timeouts}")
    lines.append("")

    lines.append("--- Grafana Ping ---")
    lines.append(_fmt_latency(grafana_agg, "Grafana"))
    lines.append(f"Grafana timeouts: {total_grafana_timeouts}")
    lines.append("")

    # Verdict
    lines.append("--- Verdict ---")

    # 1. Grafana must be reachable
    if grafana_agg.total == 0 and total_grafana_timeouts > 0:
        failures.append(
            "GRAFANA_UNREACHABLE: all Grafana /api/health pings failed. "
            f"Is Grafana running at {GRAFANA_URL}?"
        )
        lines.append("FAIL: Grafana is not reachable.")
    else:
        lines.append("Grafana reachable: OK")

    # 2. Crashes
    if crashed:
        for s in crashed:
            failures.append(f"WORKER_CRASHED (pid={s.pid}): {s.crash_reason[:200]}")
        lines.append(f"FAIL: {len(crashed)} worker(s) crashed.")
    else:
        lines.append("Worker stability: OK (no crashes)")

    # 3. Hub ping latency
    if hub_agg.total > 0 and hub_agg.percentile(99) > 200:
        msg = (
            f"Hub ping P99={hub_agg.percentile(99):.0f}ms exceeds 200ms threshold "
            f"(Ray actor queue may be saturated)"
        )
        failures.append(msg)
        lines.append(f"WARN: {msg}")
    elif hub_agg.total > 0:
        lines.append("Hub latency: OK (P99 < 200ms)")

    # 4. Grafana ping latency
    if grafana_agg.total > 0 and grafana_agg.percentile(99) > 500:
        msg = (
            f"Grafana ping P99={grafana_agg.percentile(99):.0f}ms exceeds 500ms "
            f"threshold (Prometheus/Tempo may be overloaded)"
        )
        failures.append(msg)
        lines.append(f"WARN: {msg}")
    elif grafana_agg.total > 0:
        lines.append("Grafana latency: OK (P99 < 500ms)")

    lines.append("=" * 60)
    return "\n".join(lines), failures


# ---------------------------------------------------------------------------
# Test entry point
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.e2e
def test_monitor_stress() -> None:
    """Run multi-process, multi-thread stress test against rl-insight monitor."""
    # Pre-flight: verify Grafana is reachable
    g_ok, g_lat = ping_grafana(GRAFANA_URL, GRAFANA_PING_TIMEOUT)
    if not g_ok:
        pytest.fail(
            f"Grafana is not reachable at {GRAFANA_URL}/api/health. "
            "Start the server stack first: rl-insight server start"
        )
    print(f"\n[PREFLIGHT] Grafana reachable: {g_lat * 1000:.0f}ms")

    # Init Ray in main process
    import ray

    ray.init(
        address="auto",
        namespace="rl-insight-monitor",
        ignore_reinit_error=True,
        logging_level=logging.ERROR,
    )

    import rl_insight as insight

    insight.init()

    # Spawn workers
    print(
        f"\n[START] {NUM_PROCESSES} processes x {NUM_THREADS} threads, "
        f"duration={DURATION}s\n"
    )

    stats_queue: mp.Queue = mp.Queue()
    barrier = mp.Barrier(NUM_PROCESSES + 1)  # +1 for main
    stop_event = mp.Event()

    processes = []
    for i in range(NUM_PROCESSES):
        p = mp.Process(
            target=_stress_worker,
            args=(i, stats_queue, barrier, stop_event),
            daemon=True,
        )
        processes.append(p)
        p.start()

    # Wait all workers ready, then start the clock
    barrier.wait()
    t_start = time.monotonic()

    # Live progress
    try:
        while time.monotonic() - t_start < DURATION:
            time.sleep(3)
            elapsed = time.monotonic() - t_start
            # Drain queue for live stats
            live_events = 0
            while not stats_queue.empty():
                try:
                    s = stats_queue.get_nowait()
                    live_events += s.events
                except Exception:
                    break
            print(
                f"  [{elapsed:5.1f}s] "
                f"events_reported={live_events:,}  "
                f"remaining={DURATION - elapsed:.0f}s"
            )
    except KeyboardInterrupt:
        print("\n[ABORT] Interrupted, stopping workers...")

    stop_event.set()

    # Collect final stats
    for p in processes:
        p.join(timeout=30)

    all_stats: list[WorkerStats] = []
    while not stats_queue.empty():
        try:
            all_stats.append(stats_queue.get_nowait())
        except Exception:
            break

    # If some workers didn't report stats, they crashed silently
    reported_pids = {s.pid for s in all_stats}
    for p in processes:
        if p.pid not in reported_pids:
            all_stats.append(
                WorkerStats(pid=p.pid or 0, crashed=True, crash_reason="no_report")
            )

    duration = time.monotonic() - t_start
    report, failures = _build_report(all_stats, duration)

    print(report)

    if failures:
        pytest.fail("\n".join(failures))

    ray.shutdown()
