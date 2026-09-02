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
"""Offline inspection of persisted Prometheus and Tempo data."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from prometheus_client.parser import text_string_to_metric_families
from pyarrow import parquet

from .server.display import format_table
from .utils.constants import MonitorPaths

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class SourceRange:
    """Time range and object count for one data source."""

    start: datetime
    end: datetime
    count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "count": self.count,
        }


@dataclass(frozen=True)
class ExperimentSummary:
    """One project/experiment pair found in persisted data."""

    project: str
    experiment: str
    prometheus: SourceRange | None
    tempo: SourceRange | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "experiment": self.experiment,
            "prometheus": self.prometheus.as_dict() if self.prometheus else None,
            "tempo": self.tempo.as_dict() if self.tempo else None,
        }


def inspect_data_directory(
    data_dir: str | Path,
    *,
    promtool_bin: str | Path | None = None,
) -> list[ExperimentSummary]:
    """Return project/experiment summaries from a persisted RL-Insight data directory."""
    resolved_dir = Path(data_dir).expanduser().resolve()
    if not resolved_dir.is_dir():
        raise FileNotFoundError(f"data directory does not exist: {resolved_dir}")

    prometheus_ranges = _inspect_prometheus(resolved_dir, promtool_bin=promtool_bin)
    tempo_ranges = _inspect_tempo(resolved_dir)
    keys = sorted(set(prometheus_ranges) | set(tempo_ranges))
    return [
        ExperimentSummary(
            project=project,
            experiment=experiment,
            prometheus=prometheus_ranges.get((project, experiment)),
            tempo=tempo_ranges.get((project, experiment)),
        )
        for project, experiment in keys
    ]


def format_summaries(summaries: Sequence[ExperimentSummary]) -> str:
    """Render experiment summaries as a terminal table."""
    headers = ["Project", "Experiment", "Prometheus", "Tempo"]
    rows = [
        [
            summary.project,
            summary.experiment,
            _format_range(
                summary.prometheus.start if summary.prometheus else None,
                summary.prometheus.end if summary.prometheus else None,
            ),
            _format_range(
                summary.tempo.start if summary.tempo else None,
                summary.tempo.end if summary.tempo else None,
            ),
        ]
        for summary in summaries
    ]
    return format_table(headers, rows)


def summaries_to_json(summaries: Sequence[ExperimentSummary]) -> str:
    """Render experiment summaries as pretty-printed JSON."""
    return json.dumps(
        [summary.as_dict() for summary in summaries],
        indent=2,
        ensure_ascii=False,
    )


def _inspect_prometheus(
    data_dir: Path,
    *,
    promtool_bin: str | Path | None,
) -> dict[tuple[str, str], SourceRange]:
    prometheus_dir = data_dir / "prometheus"
    if not prometheus_dir.is_dir():
        return {}

    output = _run_promtool(prometheus_dir, promtool_bin=promtool_bin)
    ranges: dict[tuple[str, str], SourceRange] = {}
    for pair, timestamp in _parse_openmetrics(output):
        _update_range(ranges, pair, timestamp)
    return ranges


def _run_promtool(
    prometheus_dir: Path,
    *,
    promtool_bin: str | Path | None,
) -> str:
    binary = _find_promtool(promtool_bin)
    with tempfile.TemporaryDirectory(prefix="rl-insight-promtool-") as sandbox:
        result = subprocess.run(
            [
                str(binary),
                "--experimental",
                "tsdb",
                "dump-openmetrics",
                f"--sandbox-dir-root={sandbox}",
                str(prometheus_dir),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    return result.stdout


def _find_promtool(explicit: str | Path | None) -> Path:
    if explicit is not None:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"promtool binary does not exist: {path}")
        return path

    found = shutil.which("promtool")
    if found:
        return Path(found).resolve()

    service_root = MonitorPaths.STATE_ROOT / "services" / "prometheus"
    candidates = sorted(
        path for path in service_root.rglob("promtool") if path.is_file()
    )
    if candidates:
        return candidates[0]
    raise FileNotFoundError(
        "promtool was not found on PATH or under ~/.rl-insight/services/prometheus"
    )


def _parse_openmetrics(output: str) -> Iterator[tuple[tuple[str, str], datetime]]:
    for line in output.splitlines():
        if not line or line.startswith("#"):
            continue
        raw_timestamp = float(line.rsplit(None, 1)[-1])
        for family in text_string_to_metric_families(line):
            for sample in family.samples:
                project = sample.labels.get("project")
                experiment = sample.labels.get("experiment_name")
                if not project or not experiment:
                    continue
                yield (project, experiment), _timestamp_to_datetime(raw_timestamp)


def _inspect_tempo(data_dir: Path) -> dict[tuple[str, str], SourceRange]:
    tempo_dir = data_dir / "tempo"
    if not tempo_dir.is_dir():
        return {}

    ranges: dict[tuple[str, str], SourceRange] = {}
    columns = ["StartTimeUnixNano", "EndTimeUnixNano", "rs"]
    for parquet_file in sorted(tempo_dir.rglob("data.parquet")):
        parquet_file_handle = parquet.ParquetFile(parquet_file)
        for batch in parquet_file_handle.iter_batches(batch_size=256, columns=columns):
            for row in batch.to_pylist():
                start = _nanoseconds_to_datetime(row["StartTimeUnixNano"])
                end = _nanoseconds_to_datetime(row["EndTimeUnixNano"])
                for pair in _tempo_pairs(row):
                    _update_range(ranges, pair, start, end=end)
    return ranges


def _tempo_pairs(row: Mapping[str, Any]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for resource_spans in row.get("rs") or []:
        resource_attributes = resource_spans.get("Resource", {}).get("Attrs") or []
        pairs.update(_attributes_to_pairs(resource_attributes))
        for scope_spans in resource_spans.get("ss") or []:
            for span in scope_spans.get("Spans") or []:
                pairs.update(_attributes_to_pairs(span.get("Attrs") or []))
    return pairs


def _attributes_to_pairs(
    attributes: Iterable[Mapping[str, Any]],
) -> set[tuple[str, str]]:
    values: dict[str, str] = {}
    for attribute in attributes:
        key = attribute.get("Key")
        value = attribute.get("Value")
        if key and value:
            values[str(key)] = str(value[0])
    project = values.get("project")
    experiment = values.get("experiment_name")
    return {(project, experiment)} if project and experiment else set()


def _update_range(
    ranges: dict[tuple[str, str], SourceRange],
    pair: tuple[str, str],
    start: datetime,
    *,
    end: datetime | None = None,
) -> None:
    finish = end if end is not None else start
    existing = ranges.get(pair)
    if existing is None:
        ranges[pair] = SourceRange(start=start, end=finish, count=1)
        return
    ranges[pair] = SourceRange(
        start=min(existing.start, start),
        end=max(existing.end, finish),
        count=existing.count + 1,
    )


def _timestamp_to_datetime(timestamp: float) -> datetime:
    return _EPOCH + timedelta(seconds=timestamp)


def _nanoseconds_to_datetime(timestamp: int) -> datetime:
    return _EPOCH + timedelta(microseconds=timestamp // 1_000)


def _format_range(start: datetime | None, end: datetime | None) -> str:
    if start is None or end is None:
        return "-"
    if start.date() == end.date():
        return f"{start:%Y-%m-%d %H:%M}～{end:%H:%M}"
    return f"{start:%Y-%m-%d %H:%M}～{end:%Y-%m-%d %H:%M}"
