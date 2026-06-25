#!/usr/bin/env python3
"""RL-Insight stress / concurrency API test.

Per concurrency level:
  - Client submission latency (avg, p50, p95, queue = p95-p50)
  - Hub delivery failure rate (submitted vs events_applied)
  - Track exact count and sum of all submitted values

Post-test verification:
  - Stress aggregate: Prometheus delta == submitted total (count + sum)
  - Grafana frontend health + panel data
  - End-to-end consistency: known values -> Prometheus exact match

Run from the repo root:
    python tests/monitor/test_monitor_stress.py
"""

from __future__ import annotations

import concurrent.futures
import json
import math
import os
import statistics
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

import ray
import rl_insight as insight
import rl_insight.api as _api


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

STRESS_DURATION_S = 1
CONCURRENCY_LEVELS = [50, 100, 200, 400, 800, 1600]
FAILURE_THRESHOLD = 0.05
PROMETHEUS_PORT = int(os.environ.get("RL_INSIGHT_PROMETHEUS_PORT", "9090"))
GRAFANA_PORT = 3000
SCRAPE_WAIT_S = 15
AGGREGATE_WAIT_S = 30
NS = "rl_insight_monitor"


def _server_ip() -> str:
    return os.environ.get("RL_INSIGHT_SERVICE_IP", "127.0.0.1").strip() or "127.0.0.1"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class LatencyStats:
    p50: float = 0.0
    p95: float = 0.0
    p99: float = 0.0
    avg: float = 0.0
    queue_ms: float = 0.0

    @classmethod
    def from_samples(cls, samples: list[float]) -> LatencyStats:
        if not samples:
            return cls()
        s = sorted(samples)
        n = len(s)
        p50 = _pct(s, 50)
        p95 = _pct(s, 95)
        return cls(
            p50=p50, p95=p95, p99=_pct(s, 99),
            avg=statistics.mean(s),
            queue_ms=max(0.0, (p95 - p50) * 1000),
        )


@dataclass
class ConcurrencyResult:
    api_name: str
    concurrency: int
    submitted: int
    submitted_sum: float = 0.0
    hub_delta: int = 0
    failure_rate: float = 0.0
    throughput_per_sec: float = 0.0
    latency: LatencyStats = field(default_factory=LatencyStats)


def _pct(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    k = (p / 100.0) * (len(data) - 1)
    f, c = math.floor(k), math.ceil(k)
    if f == c:
        return data[int(k)]
    return data[int(f)] * (c - k) + data[int(c)] * (k - f)


# ---------------------------------------------------------------------------
# Exact-sum helper for histogram values
# ---------------------------------------------------------------------------


def _histogram_sum_for(n: int) -> float:
    """Exact sum of N histogram values: each call emits 200 + (seq % 100)."""
    k, r = divmod(n, 100)
    # sum of i%100 for i in [0, n-1] = k * sum(0..99) + sum(0..r-1)
    cycle_sum = k * 4950 + r * (r - 1) // 2
    return float(n * 200 + cycle_sum)


# ---------------------------------------------------------------------------
# Hub / Prometheus helpers
# ---------------------------------------------------------------------------


def _hub_events_count() -> int:
    try:
        actor = ray.get_actor("RLInsightMonitorHub", namespace="rl-insight-monitor")
        status = ray.get(actor.get_status.remote())
        return int(status.get("events_applied", 0))
    except Exception:
        return -1


def _promql_value(service_ip: str, metric: str) -> float | None:
    import urllib.parse

    url = (
        f"http://{service_ip}:{PROMETHEUS_PORT}/api/v1/query"
        f"?query={urllib.parse.quote(metric)}"
    )
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        if data.get("status") != "success":
            return None
        results = data.get("data", {}).get("result", [])
        if not results:
            return None
        return float(results[0].get("value", [None, 0])[1])
    except Exception:
        return None


def _prometheus_has_data(service_ip: str, query: str) -> bool:
    import urllib.parse

    url = (
        f"http://{service_ip}:{PROMETHEUS_PORT}/api/v1/query"
        f"?query={urllib.parse.quote(query)}"
    )
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        return data.get("status") == "success" and bool(data.get("data", {}).get("result"))
    except Exception:
        return False


def _grafana_healthy(service_ip: str) -> bool:
    try:
        url = f"http://{service_ip}:{GRAFANA_PORT}/api/health"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Single-event emitters
# ---------------------------------------------------------------------------


def _emit_counter() -> None:
    insight.metric_count(
        "train_step_total", amount=1,
        documentation="Counter: total training steps", worker="stress",
    )


def _emit_gauge(i: int) -> None:
    insight.metric_value(
        "reward_mean", value=float(i % 1000),
        documentation="Gauge: mean reward value", worker="stress",
    )


def _emit_histogram(i: int) -> None:
    insight.metric_distribution(
        "step_latency_ms", value=float(200 + i % 100),
        documentation="Histogram: step latency in ms", worker="stress",
    )


def _emit_trace(i: int) -> None:
    with insight.trace_state("rollout_generate", state_lane_id="stress", step=i):
        pass


API_EMITTERS: dict[str, Callable[[int], None]] = {
    "counter": lambda i: _emit_counter(),
    "gauge": _emit_gauge,
    "histogram": _emit_histogram,
    "trace": _emit_trace,
}


# ---------------------------------------------------------------------------
# Stress runner (tracks both count and sum)
# ---------------------------------------------------------------------------


def run_concurrency_test(
    api_name: str,
    emit_fn: Callable[[int], None],
    concurrency: int,
    track_sum: bool = False,
) -> ConcurrencyResult:
    hub_before = _hub_events_count()
    stop_event = threading.Event()
    worker_counts: list[int] = [0] * concurrency
    all_latencies: list[float] = []
    latencies_lock = threading.Lock()

    def worker(worker_id: int) -> None:
        local_lat: list[float] = []
        seq = 0
        while not stop_event.is_set():
            t0 = time.perf_counter()
            try:
                emit_fn(seq)
            except Exception:
                pass
            else:
                local_lat.append(time.perf_counter() - t0)
            seq += 1
        worker_counts[worker_id] = len(local_lat)
        with latencies_lock:
            all_latencies.extend(local_lat)

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(worker, wid) for wid in range(concurrency)]
        try:
            time.sleep(STRESS_DURATION_S)
        except KeyboardInterrupt:
            print("\n        Interrupted. Stopping workers ...")
        finally:
            stop_event.set()
        concurrent.futures.wait(futures, timeout=10)

    hub_after = _hub_events_count()
    hub_delta = hub_after - hub_before if hub_before >= 0 and hub_after >= 0 else -1
    submitted = sum(worker_counts)

    # Compute exact sum for histogram
    submitted_sum = 0.0
    if track_sum:
        submitted_sum = sum(_histogram_sum_for(n) for n in worker_counts)

    if hub_delta >= 0 and submitted > 0:
        fail_rate = max(0.0, 1.0 - hub_delta / submitted)
    else:
        fail_rate = 1.0

    return ConcurrencyResult(
        api_name=api_name, concurrency=concurrency,
        submitted=submitted, submitted_sum=submitted_sum,
        hub_delta=hub_delta, failure_rate=fail_rate,
        throughput_per_sec=submitted / max(STRESS_DURATION_S, 0.001),
        latency=LatencyStats.from_samples(all_latencies),
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _fmt_lat(lat: LatencyStats) -> str:
    return (
        f"avg={lat.avg*1000:.1f}ms  "
        f"p50={lat.p50*1000:.1f}ms  "
        f"p95={lat.p95*1000:.1f}ms  "
        f"queue={lat.queue_ms:.0f}ms"
    )


def print_header() -> None:
    print(f"\n{'API':<12} {'Conc':>5}  {'Submitted':>10} {'HubRcvd':>8}  {'Fail%':>6}  {'AvgLat':>8}  {'Queue(ms)':>10}")
    print("-" * 85)


def print_row(r: ConcurrencyResult) -> None:
    tag = "" if r.failure_rate <= FAILURE_THRESHOLD else " <<<"
    print(
        f"{r.api_name:<12} {r.concurrency:>5}  {r.submitted:>10} {r.hub_delta:>8}  "
        f"{r.failure_rate:>5.1%}  {r.latency.avg*1000:>7.1f}ms  "
        f"{r.latency.queue_ms:>9.0f}{tag}"
    )


def print_api_summary(results: dict[str, list[ConcurrencyResult]]) -> None:
    print(f"\n{'=' * 70}")
    print("  Per-API Latency Breakdown (highest safe concurrency)")
    print(f"{'=' * 70}")
    print(f"{'API':<14} {'Conc':>6} {'Avg(ms)':>9} {'p50(ms)':>9} {'p95(ms)':>9} {'Queue(ms)':>10}")
    print("-" * 64)
    for api_name in ["counter", "gauge", "histogram", "trace"]:
        levels = results.get(api_name, [])
        if not levels:
            continue
        best = max(levels, key=lambda r: r.concurrency)
        lat = best.latency
        print(
            f"{api_name:<14} {best.concurrency:>6} "
            f"{lat.avg*1000:>8.1f} {lat.p50*1000:>8.1f} "
            f"{lat.p95*1000:>8.1f} {lat.queue_ms:>9.0f}"
        )


def print_analysis(results: dict[str, list[ConcurrencyResult]]) -> None:
    print(f"\n{'=' * 70}")
    print("  Analysis")
    print(f"{'=' * 70}")
    all_r = [r for levels in results.values() for r in levels]
    mq = max((r.latency.queue_ms for r in all_r if r.latency.queue_ms > 0), default=0)
    print(f"""
  Queue time = p95 - p50: wait time in Ray actor mailbox.
  Max observed queue: {mq:.0f}ms.  Grows with concurrency; inherent to
  single-actor design, not a bug.  The checks below verify that
  despite queue delays, all data reaches Prometheus intact.
""")


# ---------------------------------------------------------------------------
# Grafana
# ---------------------------------------------------------------------------


def _verify_grafana_frontend(service_ip: str) -> None:
    print(f"\n{'=' * 70}")
    print("  Grafana Frontend")
    print(f"{'=' * 70}")
    ok = _grafana_healthy(service_ip)
    print(f"  http://{service_ip}:{GRAFANA_PORT}  {'OK' if ok else 'UNREACHABLE'}")
    if not ok:
        return
    for panel, query in [
        ("metric_count", f"{NS}_train_step_total"),
        ("metric_value", f"{NS}_reward_mean"),
        ("metric_distribution", f"{NS}_step_latency_ms_bucket"),
    ]:
        has = _prometheus_has_data(service_ip, query)
        print(f"    [{'PASS' if has else 'FAIL'}] {panel}")


# ---------------------------------------------------------------------------
# Stress aggregate: exact count + sum verification
# ---------------------------------------------------------------------------


def _verify_stress_aggregate(
    service_ip: str,
    results: dict[str, list[ConcurrencyResult]],
    prometheus_before: dict[str, float],
) -> None:
    print(f"\n{'=' * 70}")
    print("  Stress Data Aggregate Verification  (exact match)")
    print(f"{'=' * 70}")

    counter_total = sum(r.submitted for r in results.get("counter", []))
    hist_total = sum(r.submitted for r in results.get("histogram", []))
    hist_sum = sum(r.submitted_sum for r in results.get("histogram", []))

    print(f"  Counter submitted      : {counter_total}")
    print(f"  Histogram submitted     : {hist_total}")
    print(f"  Histogram expected sum  : {hist_sum:.0f}")
    print(f"  Waiting for Prometheus ({AGGREGATE_WAIT_S}s) ...")
    time.sleep(AGGREGATE_WAIT_S)

    checks: list[tuple[str, bool, str, str]] = []
    L = '{worker="stress"}'

    # Counter: exact count
    after_counter = _promql_value(service_ip, f"{NS}_train_step_total" + L)
    base_counter = prometheus_before.get("counter", 0.0)
    if after_counter is not None:
        delta = after_counter - base_counter
        ok = abs(delta - counter_total) < 0.5
        checks.append(("counter delta == submitted", ok, str(counter_total), f"{delta:.0f}"))
    else:
        checks.append(("counter delta", False, str(counter_total), "no data"))

    # Histogram: exact count
    after_hist_count = _promql_value(service_ip, f"{NS}_step_latency_ms_count" + L)
    base_hist_count = prometheus_before.get("hist_count", 0.0)
    if after_hist_count is not None:
        delta = after_hist_count - base_hist_count
        ok = abs(delta - hist_total) < 0.5
        checks.append(("histogram count == submitted", ok, str(hist_total), f"{delta:.0f}"))
    else:
        checks.append(("histogram count", False, str(hist_total), "no data"))

    # Histogram: exact sum
    after_hist_sum = _promql_value(service_ip, f"{NS}_step_latency_ms_sum" + L)
    base_hist_sum = prometheus_before.get("hist_sum", 0.0)
    if after_hist_sum is not None:
        delta = after_hist_sum - base_hist_sum
        expected = hist_sum
        ok = abs(delta - expected) < 1.0
        checks.append(("histogram sum == expected", ok, f"{expected:.0f}", f"{delta:.0f}"))
    else:
        checks.append(("histogram sum", False, f"{hist_sum:.0f}", "no data"))

    passed = 0
    for label, ok, exp, act in checks:
        mark = "PASS" if ok else "FAIL"
        print(f"    [{mark}] {label}: expected={exp}, actual={act}")
        if ok:
            passed += 1

    print()
    if passed == len(checks):
        print(f"  All {passed} checks passed.  Every event accounted for.")
    else:
        print(f"  {len(checks) - passed} checks FAILED.")


# ---------------------------------------------------------------------------
# Data consistency (known values -> Prometheus, count + content)
# ---------------------------------------------------------------------------


def _verify_data_consistency(service_ip: str, prometheus_before: dict[str, float]) -> None:
    print(f"\n{'=' * 70}")
    print("  End-to-End Data Consistency (known values)")
    print(f"{'=' * 70}")

    checks: list[tuple[str, bool, str, str]] = []
    LC = '{worker="consistency_check"}'

    # --- Counter: delta AND exact value ---
    base = prometheus_before.get("counter_ck", _promql_value(service_ip, f"{NS}_train_step_total" + LC) or 0.0)
    N, AMOUNT = 5, 1
    for _ in range(N):
        insight.metric_count(
            "train_step_total", amount=AMOUNT,
            documentation="Counter: total training steps",
            worker="consistency_check",
        )
    time.sleep(SCRAPE_WAIT_S)
    after = _promql_value(service_ip, f"{NS}_train_step_total" + LC)
    if after is not None:
        delta = after - base
        checks.append(("counter +" + str(N), abs(delta - N) < 0.5, str(N), f"{delta:.1f}"))
        expected = base + N * AMOUNT
        checks.append(("counter value = " + str(int(expected)),
                       abs(after - expected) < 0.5, str(int(expected)), f"{after:.0f}"))
    else:
        checks.append(("counter", False, str(N), "no data"))

    # --- Gauge ---
    GAUGE_VALUES = [1.23, 4.56, 7.89]
    for v in GAUGE_VALUES:
        insight.metric_value(
            "reward_mean", value=v,
            documentation="Gauge: mean reward value",
            worker="consistency_check",
        )
    time.sleep(SCRAPE_WAIT_S)
    after = _promql_value(service_ip, f"{NS}_reward_mean" + LC)
    expected = GAUGE_VALUES[-1]
    ok = after is not None and abs(after - expected) < 0.01
    checks.append(("gauge = " + str(expected), ok, str(expected),
                   f"{after:.4f}" if after is not None else "no data"))

    # --- Histogram: count AND sum ---
    base_count = prometheus_before.get("hist_ck_count",
        _promql_value(service_ip, f"{NS}_step_latency_ms_count" + LC) or 0.0)
    base_sum = prometheus_before.get("hist_ck_sum",
        _promql_value(service_ip, f"{NS}_step_latency_ms_sum" + LC) or 0.0)
    HIST_VALUES = [100.0, 200.0, 300.0]
    for v in HIST_VALUES:
        insight.metric_distribution(
            "step_latency_ms", value=v,
            documentation="Histogram: step latency in ms",
            worker="consistency_check",
        )
    time.sleep(SCRAPE_WAIT_S)
    after_count = _promql_value(service_ip, f"{NS}_step_latency_ms_count" + LC)
    after_sum_val = _promql_value(service_ip, f"{NS}_step_latency_ms_sum" + LC)
    M = len(HIST_VALUES)

    if after_count is not None:
        delta = after_count - base_count
        checks.append(("histogram count +" + str(M), abs(delta - M) < 0.5, str(M), f"{delta:.1f}"))
    else:
        checks.append(("histogram count", False, str(M), "no data"))

    if after_sum_val is not None:
        expected_sum = base_sum + sum(HIST_VALUES)
        ok = abs(after_sum_val - expected_sum) < 1.0
        checks.append(("histogram sum = " + str(int(expected_sum)), ok,
                       str(int(expected_sum)), f"{after_sum_val:.0f}"))
    else:
        checks.append(("histogram sum", False, str(int(sum(HIST_VALUES))), "no data"))

    passed = sum(1 for _, ok, _, _ in checks if ok)
    for label, ok, exp, act in checks:
        print(f"    [{'PASS' if ok else 'FAIL'}] {label}: expected={exp}, actual={act}")
    print()
    if passed == len(checks):
        print(f"  All {passed} checks passed.  Count AND content exact match.")
    else:
        print(f"  {len(checks) - passed} checks FAILED.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------



def print_full_results(results: dict[str, list[ConcurrencyResult]]) -> None:
    """Reprint every concurrency result for record-keeping."""
    print(f"\n{'=' * 70}")
    print("  Full Results (all levels)")
    print(f"{'=' * 70}")
    for api_name in ["counter", "gauge", "histogram", "trace"]:
        levels = results.get(api_name, [])
        if not levels:
            continue
        print(f"\n  -- {api_name} --")
        hdr = f"  {'Conc':>5}  {'Submitted':>10} {'HubRcvd':>8}  {'Fail%':>6}  {'Avg(ms)':>8} {'p50(ms)':>8} {'p95(ms)':>8} {'Queue(ms)':>10}"
        print(hdr)
        print("  " + "-" * 83)
        for r in levels:
            lat = r.latency
            print(
                f"  {r.concurrency:>5}  {r.submitted:>10} {r.hub_delta:>8}  "
                f"{r.failure_rate:>5.1%}  {lat.avg*1000:>7.1f} {lat.p50*1000:>7.1f} "
                f"{lat.p95*1000:>7.1f} {lat.queue_ms:>9.0f}"
            )

def main() -> int:
    service_ip = _server_ip()

    print("=" * 70)
    print("  RL-Insight Monitor  --  Stress Test Report")
    print("=" * 70)
    print(f"  {time.strftime('%Y-%m-%d %H:%M:%S')}    server: {service_ip}")
    print(f"  {len(CONCURRENCY_LEVELS)} levels {CONCURRENCY_LEVELS}, {STRESS_DURATION_S}s each")
    print()

    # -- Setup --
    try:
        ray.init(address="auto", namespace="rl-insight-monitor", ignore_reinit_error=True)
    except ConnectionError:
        ray.init(namespace="rl-insight-monitor", ignore_reinit_error=True)

    insight.init(
        project="verl", experiment_name="ppo-stress-test",
        config={"server": {"service_ip": service_ip}},
    )
    if not _api._STATE.enabled:
        print("  FAIL: monitoring not enabled.")
        return 1
    print(f"  Ray: OK  |  Monitor: OK  |  Hub events: {_hub_events_count()}")

    # -- Prometheus baseline snapshot (before stress) --
    LS = '{worker="stress"}'
    LC = '{worker="consistency_check"}'
    prom_before = {
        "counter": _promql_value(service_ip, f"{NS}_train_step_total" + LS) or 0.0,
        "hist_count": _promql_value(service_ip, f"{NS}_step_latency_ms_count" + LS) or 0.0,
        "hist_sum": _promql_value(service_ip, f"{NS}_step_latency_ms_sum" + LS) or 0.0,
        "counter_ck": _promql_value(service_ip, f"{NS}_train_step_total" + LC) or 0.0,
        "hist_ck_count": _promql_value(service_ip, f"{NS}_step_latency_ms_count" + LC) or 0.0,
        "hist_ck_sum": _promql_value(service_ip, f"{NS}_step_latency_ms_sum" + LC) or 0.0,
    }

    # -- Stress tests --
    print(f"\n{'=' * 70}")
    print("  Stress Test Results  (queue = p95 - p50)")
    print(f"{'=' * 70}")

    all_results: dict[str, list[ConcurrencyResult]] = {}

    for api_name, emit_fn in API_EMITTERS.items():
        track = (api_name == "histogram")
        levels: list[ConcurrencyResult] = []
        print(f"\n  -- {api_name} --")
        print_header()
        for concurrency in CONCURRENCY_LEVELS:
            result = run_concurrency_test(api_name, emit_fn, concurrency, track_sum=track)
            levels.append(result)
            print_row(result)
            if result.failure_rate > FAILURE_THRESHOLD:
                print(f"        Stopped: failure > {FAILURE_THRESHOLD:.0%}")
                break
            time.sleep(1)
        all_results[api_name] = levels

    # -- Summary --
    print_api_summary(all_results)

    # -- Analysis --
    print_analysis(all_results)

    # -- Stress aggregate (exact count + sum) --
    _verify_stress_aggregate(service_ip, all_results, prom_before)

    # -- Grafana --
    _verify_grafana_frontend(service_ip)

    # -- Data consistency (known values) --
    _verify_data_consistency(service_ip, prom_before)

    # -- Full results dump --
    print_full_results(all_results)

    print(f"\n{'=' * 70}")
    print("  Done")
    print(f"{'=' * 70}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
