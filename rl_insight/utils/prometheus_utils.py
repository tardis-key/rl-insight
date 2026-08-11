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

"""Prometheus metric registry, ``/metrics`` HTTP server, and target registration helpers."""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
import yaml
from omegaconf import DictConfig, OmegaConf
from prometheus_client import Counter, Gauge, Histogram, start_http_server

from ..server.network import format_host_port, local_addresses
from .constants import MonitorEnv, MonitorPaths, PrometheusScrape

logger = logging.getLogger(__file__)
logger.setLevel(logging.WARNING)


__all__ = [
    "MetricRegistry",
    "PrometheusTarget",
    "PrometheusTargetStore",
    "prometheus_export",
    "prometheus_import",
    "start_metrics_http_server",
    "update_prometheus_config",
]


@dataclass(frozen=True)
class PrometheusTarget:
    """One scrape target plus optional labels for Prometheus static_configs."""

    target: str
    labels: Mapping[str, Any] = field(default_factory=dict)


class PrometheusTargetStore:
    """Maintain Prometheus scrape targets in the runtime config file."""

    def __init__(self, config_file: str | Path, prometheus_port: int):
        self.config_file = Path(config_file).expanduser().resolve()
        self.prometheus_port = prometheus_port
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls, conf: DictConfig) -> "PrometheusTargetStore":
        runtime_dir = OmegaConf.select(conf, "server.runtime_dir")
        base = (
            Path(str(runtime_dir)).expanduser().resolve()
            if runtime_dir
            else (MonitorPaths.STATE_ROOT / "runtime").resolve()
        )
        prometheus_port = int(OmegaConf.select(conf, "prometheus.prometheus_port"))
        return cls(base / "prometheus.yml", prometheus_port)

    def register(
        self, job_name: str, targets: Sequence[PrometheusTarget]
    ) -> dict[str, Any]:
        incoming = {
            str(item.target): {str(k): str(v) for k, v in item.labels.items()}
            for item in targets
        }

        with self._lock:
            source = (
                self.config_file
                if self.config_file.exists()
                else MonitorPaths.PROMETHEUS_CONFIG_FILE
            )
            data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
            scrape_configs = data.setdefault("scrape_configs", [])

            job_config = next(
                (
                    config
                    for config in scrape_configs
                    if config.get("job_name") == job_name
                ),
                None,
            )
            if job_config is None:
                job_config = {"job_name": job_name}
                scrape_configs.append(job_config)

            target_map = {
                target: group.get("labels", {})
                for group in job_config.get("static_configs", [])
                for target in group.get("targets", [])
            }
            target_map.update(incoming)
            job_config["static_configs"] = [
                {"targets": [target], **({"labels": labels} if labels else {})}
                for target, labels in sorted(target_map.items())
            ]

            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            payload = yaml.safe_dump(data, sort_keys=False)
            tmp_path = self.config_file.with_name(
                f".{self.config_file.name}.{os.getpid()}.tmp"
            )
            tmp_path.write_text(payload, encoding="utf-8")
            os.replace(tmp_path, self.config_file)

        return {
            "job_name": job_name,
            "target_count": len(target_map),
            "config_file": str(self.config_file),
        }

    def reload(self) -> bool:
        url = (
            "http://"
            + format_host_port(local_addresses()["loopback"], self.prometheus_port)
            + "/-/reload"
        )

        with requests.Session() as session:
            # bypass http_proxy for localhost Prometheus reload
            session.trust_env = False
            response = session.post(url, timeout=5)

        response.raise_for_status()
        return True


def _merge_labels(
    defaults: Mapping[str, Any] | None, overrides: Mapping[str, Any] | None
) -> dict[str, str]:
    """Merge Prometheus label dicts with string keys/values; ``overrides`` wins on duplicate keys.

    Args:
        defaults: Base labels (optional).
        overrides: Labels applied after defaults (optional).
    """
    out: dict[str, str] = {}
    if defaults:
        out.update({str(k): str(v) for k, v in defaults.items()})
    if overrides:
        out.update({str(k): str(v) for k, v in overrides.items()})
    return out


def start_metrics_http_server(port: int, addr: str = "") -> None:
    """Start a background thread serving ``/metrics`` via ``prometheus_client``.

    Args:
        port: TCP port to listen on.
        addr: Bind address; empty may mean all interfaces depending on library defaults.
    """
    addr = addr.strip("[]") if addr else addr
    start_http_server(port, addr=addr)


class MetricRegistry:
    """Lazy registry of ``prometheus_client`` Counter/Gauge/Histogram objects keyed by metric name + label set."""

    def __init__(self, namespace: str = "", subsystem: str = "") -> None:
        """
        Args:
            namespace: Passed as Prometheus metric namespace (prefix segment).
            subsystem: Optional second prefix segment from ``prometheus_client`` API.
        """
        self._namespace = namespace
        self._subsystem = subsystem
        self._counters: dict[tuple[str, tuple[str, ...]], object] = {}
        self._gauges: dict[tuple[str, tuple[str, ...]], object] = {}
        self._histograms: dict[tuple[str, tuple[str, ...]], object] = {}

    def _get_or_create_counter(
        self, name: str, documentation: str, label_names: tuple[str, ...]
    ):
        """Return a cached or new ``Counter`` for ``(name, label_names)``."""
        key = (name, label_names)
        if key not in self._counters:
            self._counters[key] = Counter(
                name,
                documentation,
                labelnames=label_names,
                namespace=self._namespace,
                subsystem=self._subsystem,
            )
        return self._counters[key]

    def _get_or_create_gauge(
        self, name: str, documentation: str, label_names: tuple[str, ...]
    ):
        """Return a cached or new ``Gauge`` for ``(name, label_names)``."""
        key = (name, label_names)
        if key not in self._gauges:
            self._gauges[key] = Gauge(
                name,
                documentation,
                labelnames=label_names,
                namespace=self._namespace,
                subsystem=self._subsystem,
            )
        return self._gauges[key]

    def _get_or_create_histogram(
        self,
        name: str,
        documentation: str,
        label_names: tuple[str, ...],
        buckets: tuple[float, ...] | None,
    ):
        """Return a cached or new ``Histogram`` for ``(name, label_names)`` with optional bucket boundaries."""
        key = (name, label_names)
        if key not in self._histograms:
            kw = {}
            if buckets is not None:
                kw["buckets"] = buckets
            self._histograms[key] = Histogram(
                name,
                documentation,
                labelnames=label_names,
                namespace=self._namespace,
                subsystem=self._subsystem,
                **kw,
            )
        return self._histograms[key]

    def count(
        self,
        name: str,
        documentation: str,
        amount: float,
        defaults: Mapping[str, Any] | None = None,
        labels: Mapping[str, Any] | None = None,
    ) -> None:
        """Increment a counter, merging ``defaults`` and ``labels`` into the label set."""
        merged = _merge_labels(defaults, labels)
        names = tuple(sorted(merged.keys()))
        counter = self._get_or_create_counter(name, documentation, names)
        if merged:
            counter.labels(**merged).inc(amount)
        else:
            counter.inc(amount)

    def value(
        self,
        name: str,
        documentation: str,
        value: float,
        defaults: Mapping[str, Any] | None = None,
        labels: Mapping[str, Any] | None = None,
    ) -> None:
        """Set a gauge to ``value`` with merged labels."""
        merged = _merge_labels(defaults, labels)
        names = tuple(sorted(merged.keys()))
        gauge = self._get_or_create_gauge(name, documentation, names)
        if merged:
            gauge.labels(**merged).set(value)
        else:
            gauge.set(value)

    def distribution(
        self,
        name: str,
        documentation: str,
        value: float,
        defaults: Mapping[str, Any] | None = None,
        labels: Mapping[str, Any] | None = None,
        buckets: tuple[float, ...] | None = None,
    ) -> None:
        """Observe ``value`` on a histogram with merged labels and optional ``buckets``."""
        merged = _merge_labels(defaults, labels)
        names = tuple(sorted(merged.keys()))
        histogram = self._get_or_create_histogram(name, documentation, names, buckets)
        if merged:
            histogram.labels(**merged).observe(value)
        else:
            histogram.observe(value)


def update_prometheus_config(
    server_addresses: list[str],
    job_name: str | None = None,
    labels: list[Mapping[str, Any] | None] | None = None,
) -> None:
    """Register trainer metrics endpoints with the RL-Insight server.

    The RL-Insight server writes these targets into the runtime Prometheus
    config and reloads the managed Prometheus process. ``server_addresses``
    should contain scrape targets in ``host:port`` or ``[ipv6]:port`` form, not full URLs.

    Args:
        server_addresses: Prometheus scrape targets exposed by trainer-side
            metric HTTP servers.
        job_name: Optional Prometheus scrape job name. Defaults to the managed
            trainer metrics job.
        labels: Optional per-target labels. When provided, its length must match
            ``server_addresses``.
    """
    if not server_addresses:
        logger.warning("[rl-insight] No server addresses available to register")
        return
    if labels is not None and len(labels) != len(server_addresses):
        raise ValueError(
            "labels length must match server_addresses length: "
            f"{len(labels)} != {len(server_addresses)}"
        )

    base_url = str(os.environ.get(MonitorEnv.SERVER_URL, "")).strip().rstrip("/")
    if not base_url:
        logger.error(
            "[rl-insight] RL-Insight server URL is required; "
            "set %s to register Prometheus targets",
            MonitorEnv.SERVER_URL,
        )
        return

    payload = {
        "job_name": job_name or PrometheusScrape.TRAINER_METRICS_JOB,
        "targets": _build_target_payload(server_addresses, labels),
    }
    url = f"{base_url}/api/v1/prometheus/targets"
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        print(
            f"[rl-insight] Registered {len(server_addresses)} Prometheus targets "
            f"with RL-Insight server (job_name={payload['job_name']})"
        )
    except requests.RequestException as exc:
        logger.error(
            "[rl-insight] Failed to register Prometheus targets at %s: %s", url, exc
        )


def _build_target_payload(
    server_addresses: list[str],
    labels: list[Mapping[str, Any] | None] | None = None,
) -> list[dict[str, Any]]:
    if labels is None:
        labels = [None] * len(server_addresses)

    targets: list[dict[str, Any]] = []
    for address, target_labels in zip(server_addresses, labels):
        item: dict[str, Any] = {"target": str(address)}
        if target_labels:
            item["labels"] = {
                str(key): str(value) for key, value in target_labels.items()
            }
        targets.append(item)
    return targets


def prometheus_export(
    project: str | None = None,
    experiment_name: str | None = None,
    output_dir: str | None = None,
    prometheus_url: str = "http://127.0.0.1:9090",
    data_dir: str | None = None,
    promtool_bin: str = "promtool",
) -> int:
    """Export Prometheus metrics filtered by project/experiment_name labels.

    Uses snapshot API + promtool dump-openmetrics + label filtering + create-blocks-from.

    Args:
        project: Filter by project label (None or "*" means no filter).
        experiment_name: Filter by experiment_name label (None or "*" means no filter).
        output_dir: Directory to write the exported block.
        prometheus_url: Prometheus HTTP API base URL.
        data_dir: Prometheus TSDB data directory (--storage.tsdb.path). Auto-detected if None.
        promtool_bin: Path to promtool binary.

    Returns:
        0 on success, non-zero on failure.
    """
    import shutil as _shutil
    import subprocess as _subprocess
    import tempfile as _tempfile
    import datetime as _datetime
    from pathlib import Path as _Path

    project = _wild_to_none(project)
    experiment_name = _wild_to_none(experiment_name)

    if output_dir is None:
        logger.error("[rl-insight] output_dir is required")
        return 1

    out = _Path(output_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    # Find promtool binary
    _promtool = _find_promtool()
    if _promtool is None:
        logger.error("promtool binary not found, cannot import")
        return 1

    if data_dir is None:
        data_dir = str(MonitorPaths.STATE_ROOT / "data" / "prometheus")
    tsdb_path = _Path(data_dir).expanduser().resolve()
    if not tsdb_path.exists():
        logger.error("Prometheus data directory not found: %s", tsdb_path)
        return 1

    # Pre-flight: check if metrics exist for this project/experiment
    if project is not None or experiment_name is not None:
        _series_url = prometheus_url.rstrip("/") + "/api/v1/series"
        _match_labels = []
        if project is not None:
            _match_labels.append(f'project="{project}"')
        if experiment_name is not None:
            _match_labels.append(f'experiment_name="{experiment_name}"')
        _match_str = "{" + ",".join(_match_labels) + "}"
        try:
            import requests as _requests

            _series_resp = _requests.get(
                _series_url,
                params={"match[]": _match_str},
                timeout=10,
            )
            _series_resp.raise_for_status()
            _series_data = _series_resp.json()
            if _series_data.get("status") != "success" or not _series_data.get("data"):
                logger.error(
                    "[rl-insight] No metrics found for project=%s experiment_name=%s. "
                    "Check that the project/experiment exists and has metric data.",
                    project or "*",
                    experiment_name or "*",
                )
                return 1
        except Exception as exc:
            logger.error(
                "[rl-insight] Pre-flight Prometheus series check failed: %s", exc
            )
            return 1

    prom_out = out / "prometheus"
    prom_out.mkdir(parents=True, exist_ok=True)

    # No filter: copy all blocks directly
    if project is None and experiment_name is None:
        logger.info("No label filter specified, copying all blocks...")
        _copy_items = [
            item
            for item in tsdb_path.iterdir()
            if item.is_dir()
            and item.name not in ("chunks_head", "wal", "snapshots")
            and not item.name.startswith("tmp_dbro_sandbox")
        ]
        import tqdm as _tqdm

        for item in _tqdm.tqdm(_copy_items, desc="  Copying blocks", unit="block"):
            _shutil.copytree(item, prom_out / item.name, dirs_exist_ok=True)
        logger.info("Exported Prometheus blocks to %s", prom_out)

        # Write manifest
        import json as _json

        manifest = {
            "project": project or "*",
            "experiment_name": experiment_name or "*",
            "exported_at": _datetime.datetime.now().isoformat(),
            "source": str(tsdb_path),
        }
        (out / "manifest.json").write_text(_json.dumps(manifest, indent=2))
        return 0

    # Filtered export: dump -> filter -> rebuild
    logger.info(
        "Filtering by project=%s experiment_name=%s, dumping TSDB...",
        project,
        experiment_name,
    )
    with _tempfile.TemporaryDirectory() as tmpdir:
        tmp = _Path(tmpdir)
        dump_file = tmp / "dump.txt"

        result = _subprocess.run(
            [
                promtool_bin,
                "tsdb",
                "dump-openmetrics",
                "--sandbox-dir-root=" + str(tsdb_path),
                tsdb_path.name,
            ],
            cwd=str(tsdb_path.parent),
            stdout=_subprocess.PIPE,
            stderr=_subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            logger.error("promtool dump-openmetrics failed: %s", result.stderr)
            return 1

        logger.info("Filtering %d bytes of OpenMetrics data...", len(result.stdout))
        filtered = _filter_openmetrics(result.stdout, project, experiment_name)
        dump_file.write_text(filtered, encoding="utf-8")

        logger.info(
            "Creating TSDB blocks from filtered data (%d bytes)...", len(filtered)
        )
        blocks_dir = tmp / "blocks"
        blocks_dir.mkdir()

        result = _subprocess.run(
            [
                promtool_bin,
                "tsdb",
                "create-blocks-from",
                "openmetrics",
                str(dump_file),
                str(blocks_dir),
            ],
            stdout=_subprocess.PIPE,
            stderr=_subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            logger.error("promtool create-blocks-from failed: %s", result.stderr)
            return 1

        _filtered_items = [
            item
            for item in blocks_dir.iterdir()
            if item.is_dir() and not item.name.startswith("tmp_dbro_sandbox")
        ]
        import tqdm as _tqdm

        for item in _tqdm.tqdm(_filtered_items, desc="  Copying blocks", unit="block"):
            _shutil.copytree(item, prom_out / item.name, dirs_exist_ok=True)

    # Write manifest
    import json as _json

    manifest = {
        "project": project or "*",
        "experiment_name": experiment_name or "*",
        "exported_at": _datetime.datetime.now().isoformat(),
        "source": str(tsdb_path),
    }
    (out / "manifest.json").write_text(_json.dumps(manifest, indent=2))
    logger.info("Exported Prometheus blocks to %s", prom_out)
    return 0


def prometheus_import(
    input_dir: str,
    prometheus_url: str = "http://127.0.0.1:9090",
    data_dir: str | None = None,
    force: bool = False,
) -> int:
    """Import Prometheus TSDB blocks into a target instance.

    Detects experiment_name conflicts and appends a timestamp suffix if needed.

    Args:
        input_dir: Directory containing prometheus/ subdirectory with TSDB blocks.
        prometheus_url: Target Prometheus HTTP API base URL.
        data_dir: Target Prometheus TSDB data directory. Auto-detected if None.

    Returns:
        0 on success, non-zero on failure.
    """
    import shutil as _shutil
    from pathlib import Path as _Path

    src = _Path(input_dir).expanduser().resolve() / "prometheus"
    if not src.exists():
        logger.error("[rl-insight] Prometheus data not found in input: %s", src)
        return 1

    if data_dir is None:
        data_dir = str(MonitorPaths.STATE_ROOT / "data" / "prometheus")
    dst = _Path(data_dir).expanduser().resolve()
    dst.mkdir(parents=True, exist_ok=True)

    _promtool = _find_promtool()
    if _promtool is None:
        logger.error("promtool binary not found, cannot import")
        return 1

    blocks = [
        d
        for d in src.iterdir()
        if d.is_dir() and not d.name.startswith("tmp_dbro_sandbox")
    ]
    if not blocks:
        logger.error("[rl-insight] No TSDB blocks found in %s", src)
        return 1

    # Detect experiment_name conflicts via manifest
    experiment_name = _read_manifest_experiment(src.parent)
    if experiment_name and experiment_name != "*" and not force:
        existing = _get_existing_experiments(prometheus_url)
        if experiment_name in existing:
            logger.error(
                "[rl-insight] experiment_name %r already exists in target Prometheus. "
                "Use --force to overwrite.",
                experiment_name,
            )
            return 1

    _total_blocks = len(blocks)
    logger.info("Importing %d block(s) into %s", _total_blocks, dst)
    import tqdm as _tqdm

    for block in _tqdm.tqdm(blocks, desc="  Importing blocks", unit="block"):
        dest_block = dst / block.name
        if dest_block.exists():
            if not force:
                logger.warning(
                    "[rl-insight] Block %s already exists, skipping", block.name
                )
                continue
            logger.info("Block %s already exists, replacing (--force)", block.name)
            _shutil.rmtree(dest_block)
        _shutil.copytree(block, dest_block)

    # Reload Prometheus
    reload_url = prometheus_url.rstrip("/") + "/-/reload"
    try:
        import requests as _requests

        with _requests.Session() as session:
            session.trust_env = False
            resp = session.post(reload_url, timeout=10)
            resp.raise_for_status()
        logger.info("Prometheus reloaded successfully")
    except Exception as e:
        logger.warning(
            "[rl-insight] Failed to reload Prometheus: %s (blocks in place, may need manual restart)",
            e,
        )

    return 0


def _wild_to_none(value: str | None) -> str | None:
    """Treat None and "*" as no filter."""
    if value is None or value.strip() == "*":
        return None
    return value.strip()


def _filter_openmetrics(
    text: str, project: str | None, experiment_name: str | None
) -> str:
    """Filter OpenMetrics text, keeping only series matching the given labels."""
    lines = text.splitlines()
    result: list[str] = []

    for line in lines:
        if line.startswith("#"):
            result.append(line)
            continue
        if not line.strip():
            result.append(line)
            continue

        if project is None and experiment_name is None:
            result.append(line)
            continue

        label_part = _extract_labels(line)
        if label_part is None:
            result.append(line)
            continue

        match = True
        if project is not None and f'project="{project}"' not in label_part:
            match = False
        if (
            experiment_name is not None
            and f'experiment_name="{experiment_name}"' not in label_part
        ):
            match = False

        if match:
            result.append(line)

    return "\n".join(result) + "\n"


def _extract_labels(line: str) -> str | None:
    """Extract the label portion {key="value",...} from a metric line."""
    start = line.find("{")
    end = line.find("}")
    if start >= 0 and end > start:
        return line[start : end + 1]
    return None


def _get_existing_experiments(prometheus_url: str) -> set[str]:
    """Query Prometheus for existing experiment_name label values."""
    import requests as _requests

    try:
        url = prometheus_url.rstrip("/") + "/api/v1/label/experiment_name/values"
        resp = _requests.get(url, timeout=5)
        resp.raise_for_status()
        return set(resp.json().get("data", []))
    except Exception:
        logger.warning(
            "Could not query existing experiment_names, assuming no conflicts"
        )
        return set()


def _find_promtool() -> str | None:
    """Find promtool binary in common locations."""
    import shutil as _shutil
    from pathlib import Path as _Path

    # Check PATH
    found = _shutil.which("promtool")
    if found:
        return found
    # Check standard rl-insight install location
    home = _Path.home()
    for root in [home / ".rl-insight", _Path("/root/.rl-insight")]:
        if root.exists():
            for p in root.rglob("promtool"):
                if p.is_file():
                    return str(p)
    return None


def _read_manifest_experiment(input_parent) -> str | None:
    """Read experiment_name from manifest.json in the input directory."""
    import json as _json

    manifest_file = input_parent / "manifest.json"
    if manifest_file.exists():
        try:
            manifest = _json.loads(manifest_file.read_text())
            return manifest.get("experiment_name")
        except Exception:
            pass
    return None
