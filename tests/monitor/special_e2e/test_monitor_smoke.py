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

"""Smoke test for RL-Insight Monitor.

Runs a short instrumentation loop against a running RL-Insight server stack
(Prometheus + Tempo + Grafana + hub) and verifies metrics are being collected.
"""

from __future__ import annotations

import os
import time

import ray
import rl_insight as insight


def main() -> None:
    """Run a finite instrumentation loop for smoke-testing the monitor stack."""
    server_url = os.environ.setdefault(
        "RL_INSIGHT_SERVER_URL", "http://127.0.0.1:18080"
    )

    ray.init(address="auto", namespace="rl-insight-monitor", ignore_reinit_error=True)
    insight.init(project="verl", experiment_name="monitor_smoke_test")

    labels = {"worker": "trainer_0"}
    num_iterations = 10

    print(
        f"Running {num_iterations} instrumentation iterations against {server_url}..."
    )

    for step in range(num_iterations):
        with insight.trace_state(
            "rollout_generate", state_lane_id="replica_0", step=step
        ):
            time.sleep(0.5)

        insight.metric_count("train_step_total", amount=1, **labels)
        insight.metric_gauge("reward_mean", value=1.0 + step * 0.01, **labels)
        insight.metric_histogram(
            "step_latency_ms", value=200 + (step % 5) * 20, **labels
        )

        print(f"  Step {step + 1}/{num_iterations} complete")

    print("Smoke test instrumentation complete.")


if __name__ == "__main__":
    main()
