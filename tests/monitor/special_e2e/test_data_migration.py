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

"""End-to-end test for the export / import data-migration pipeline.

Runs *after* the stress test, which leaves real Prometheus metrics and Tempo
traces behind.  This test

1. exports the stress-test data,
2. wipes the local data directories and restarts the server,
3. verifies the data is gone,
4. imports the exported bundle,
5. confirms the data reappears.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests
from rl_insight.utils.constants import MonitorPaths

pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="the managed server stack only runs on Linux"
)

SERVER_URL = os.environ.get("RL_INSIGHT_SERVER_URL", "http://127.0.0.1:18080")
PROMETHEUS_URL = "http://127.0.0.1:9090"
TEMPO_URL = "http://127.0.0.1:3200"
READY_TIMEOUT = 60

# Labels used by test_monitor_stress.py (must match exactly).
STRESS_PROJECT = "*"
STRESS_EXPERIMENT = "ppo-stress-test"

DATA_DIR = MonitorPaths.STATE_ROOT / "data"
EXPORT_DIR = Path("/tmp/rl-insight-migration-export")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wait_for_ready(url: str, *, timeout: int = READY_TIMEOUT) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = requests.get(url, timeout=3)
            if resp.ok:
                return
        except requests.RequestException:
            pass
        time.sleep(1)
    raise AssertionError(f"{url} not ready within {timeout}s")


def _run_cli(*args: str) -> int:
    """Invoke rl-insight CLI and return exit code."""
    result = subprocess.run(
        [sys.executable, "-m", "rl_insight.cli", *args],
        capture_output=True,
        text=True,
        timeout=300,
    )
    return result


def _check_cli_success(cmd: str, result: subprocess.CompletedProcess[str]) -> None:
    """Assert CLI success, printing stderr/stdout on failure."""
    assert result.returncode == 0, (
        f"{cmd} returned {result.returncode}\n"
        f"STDERR:\n{result.stderr}\n"
        f"STDOUT:\n{result.stdout}"
    )


def _prometheus_labels() -> dict[str, list[str]]:
    """Return current Prometheus label values."""
    result: dict[str, list[str]] = {}
    for label in ("project", "experiment_name"):
        try:
            resp = requests.get(
                f"{PROMETHEUS_URL}/api/v1/label/{label}/values", timeout=5
            )
            if resp.ok:
                result[label] = resp.json().get("data", [])
        except requests.RequestException:
            result[label] = []
    return result


def _prometheus_has_data(metric: str, extra_labels: dict[str, str]) -> bool:
    """Return True if Prometheus has at least one series matching."""
    pairs = ",".join(f'{k}="{v}"' for k, v in extra_labels.items())
    query = f"{metric}{{{pairs}}}"
    try:
        resp = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": query},
            timeout=5,
        )
        resp.raise_for_status()
        return bool(resp.json().get("data", {}).get("result"))
    except requests.RequestException:
        return False


# ---------------------------------------------------------------------------
# Test steps (run in order because each depends on the previous state)
# ---------------------------------------------------------------------------


class TestDataMigration:
    """End-to-end data migration: export, wipe, import, verify."""

    def test_01_export_stress_data(self) -> None:
        """Export the stress-test data into a portable bundle."""
        _wait_for_ready(f"{SERVER_URL}/healthz")
        _wait_for_ready(f"{PROMETHEUS_URL}/-/ready")

        result = _run_cli(
            "export",
            "--project",
            STRESS_PROJECT,
            "--experiment",
            STRESS_EXPERIMENT,
            "--output",
            str(EXPORT_DIR),
        )
        _check_cli_success("export", result)

        manifest = EXPORT_DIR / "manifest.json"
        assert manifest.exists(), f"manifest.json missing from {EXPORT_DIR}"
        assert (EXPORT_DIR / "prometheus").is_dir(), "prometheus/ missing"
        assert list((EXPORT_DIR / "prometheus").iterdir()), "no Prometheus blocks"

    def test_02_data_gone_after_wipe(self) -> None:
        """Wipe data and restart — Prometheus must not serve the stress labels."""
        subprocess.run(["rl-insight", "server", "stop"], timeout=30)
        time.sleep(3)

        for sub in ("prometheus", "tempo"):
            d = DATA_DIR / sub
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True, exist_ok=True)

        subprocess.run(["rl-insight", "server", "start", "--detach"], timeout=60)
        time.sleep(5)
        _wait_for_ready(f"{SERVER_URL}/healthz")
        _wait_for_ready(f"{PROMETHEUS_URL}/-/ready")

        labels = _prometheus_labels()
        assert STRESS_PROJECT not in labels.get("project", []), (
            f"Project {STRESS_PROJECT} still present after wipe"
        )
        assert STRESS_EXPERIMENT not in labels.get("experiment_name", []), (
            f"Experiment {STRESS_EXPERIMENT} still present after wipe"
        )

    def test_03_import_restores_data(self) -> None:
        """Import the exported bundle and verify metrics are back."""
        result = _run_cli("import", "--input", str(EXPORT_DIR), "--force")
        _check_cli_success("import", result)

        requests.post(f"{PROMETHEUS_URL}/-/reload", timeout=10)
        time.sleep(3)

        labels = _prometheus_labels()
        assert STRESS_PROJECT in labels.get("project", []), (
            f"Project {STRESS_PROJECT} not restored"
        )
        assert STRESS_EXPERIMENT in labels.get("experiment_name", []), (
            f"Experiment {STRESS_EXPERIMENT} not restored"
        )

        # Spot-check: the stress test uses worker="stress" label
        assert _prometheus_has_data(
            "rl_insight_monitor_train_step_total",
            {"worker": "stress"},
        ), "Imported metrics not queryable in Prometheus"

        # Cleanup
        if EXPORT_DIR.exists():
            shutil.rmtree(EXPORT_DIR, ignore_errors=True)
