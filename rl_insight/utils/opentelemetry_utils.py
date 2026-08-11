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

"""OpenTelemetry OTLP/HTTP trace export used by the monitor hub."""

from __future__ import annotations

import logging
from typing import Any

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = logging.getLogger(__name__)

__all__ = ["OpenTelemetryTraceCollector"]

_OTEL_EXPORT_LOGGERS = (
    "opentelemetry.exporter.otlp.proto.http.trace_exporter",
    "opentelemetry.sdk.trace.export",
)


def _reduce_otel_export_log_noise() -> None:
    # Suppress WARNING retry spam; keep ERROR for real export failures.
    for name in _OTEL_EXPORT_LOGGERS:
        logging.getLogger(name).setLevel(logging.ERROR)


class OpenTelemetryTraceCollector:
    """Export closed root spans to Tempo via OTLP/HTTP."""

    def __init__(self, namespace: str = "", endpoint: str | None = None) -> None:
        self._tracer = None
        if not endpoint:
            logger.warning(
                "[rl-insight] OpenTelemetry trace export is disabled because no OTLP endpoint "
                "was returned by the RL-Insight server."
            )
            return

        _reduce_otel_export_log_noise()
        provider = TracerProvider(resource=Resource.create({SERVICE_NAME: namespace}))
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
        )
        self._tracer = provider.get_tracer(__name__)

    @property
    def enabled(self) -> bool:
        return self._tracer is not None

    def record_span(
        self,
        name: str,
        start_time_ns: int,
        end_time_ns: int,
        *,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        if self._tracer is None:
            return

        span = self._tracer.start_span(
            name,
            start_time=start_time_ns,
            attributes=attributes,
        )
        span.end(end_time=end_time_ns)


def tempo_export(
    project: str | None = None,
    experiment_name: str | None = None,
    output_dir: str | None = None,
    tempo_url: str = "http://127.0.0.1:3200",
) -> int:
    """Export Tempo traces filtered by project/experiment span attributes.

    Uses TraceQL search with pagination across the full time range (Unix epoch 0
    to now), fetches full trace JSON for each matching trace, and saves the combined
    result as ``traces.json`` under ``{output_dir}/tempo/``.

    Args:
        project: Filter by ``span.project`` attribute. ``None`` or ``"*"`` means no filter.
        experiment_name: Filter by ``span.experiment_name`` attribute. ``None`` or ``"*"`` means no filter.
        output_dir: Parent directory; a ``tempo/`` subdirectory is created inside it.
        tempo_url: Tempo HTTP query API base URL (default ``http://127.0.0.1:3200``).

    Returns:
        0 on success, non-zero on failure.
    """
    import json as _json
    import shutil as _shutil
    from pathlib import Path

    import requests

    if output_dir is None:
        logger.error("output_dir is required for tempo_export")
        return 1

    out = Path(output_dir)
    tempo_out = out / "tempo"
    if tempo_out.exists():
        _shutil.rmtree(tempo_out)
    tempo_out.mkdir(parents=True, exist_ok=True)

    # Build TraceQL query
    conditions: list[str] = []
    effective_project = project if project and project != "*" else None
    effective_experiment = (
        experiment_name if experiment_name and experiment_name != "*" else None
    )
    if effective_project:
        conditions.append(f'span.project = "{effective_project}"')
    if effective_experiment:
        conditions.append(f'span.experiment_name = "{effective_experiment}"')

    if conditions:
        query = "{ " + " && ".join(conditions) + " }"
    else:
        query = "{}"
    logger.info("Tempo TraceQL query: %s", query)

    # Search for matching trace IDs with pagination
    all_trace_ids: list[str] = []
    search_url = f"{tempo_url.rstrip('/')}/api/search"

    try:
        import time as _time
        resp = requests.get(
            search_url,
            params={
                "q": query,
                "limit": 10000,
                "start": int(_time.time()) - 7 * 86400,  # 7 days (Tempo max search range)
                "end": int(_time.time()),
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        traces = data.get("traces", [])
        for t in traces:
            all_trace_ids.append(t["traceID"])
    except requests.RequestException as exc:
        logger.error("Tempo search failed: %s", exc)
        return 1

    logger.info("Found %d matching traces", len(all_trace_ids))

    # Fetch full trace JSON for each trace ID
    all_batches: list[dict] = []
    trace_url_base = f"{tempo_url.rstrip('/')}/api/traces/"
    for tid in all_trace_ids:
        try:
            resp = requests.get(f"{trace_url_base}{tid}", timeout=30)
            resp.raise_for_status()
            trace_data = resp.json()
            batches = trace_data.get("batches", [])
            all_batches.extend(batches)
        except requests.RequestException as exc:
            logger.warning("Failed to fetch trace %s: %s", tid, exc)
            continue

    if not all_batches:
        logger.warning("No trace batches collected; writing empty traces.json")

    # Write combined output
    output_payload: dict[str, Any] = {"batches": all_batches}
    traces_file = tempo_out / "traces.json"
    traces_file.write_text(_json.dumps(output_payload, indent=2))
    logger.info("Exported %d batch(es) to %s", len(all_batches), traces_file)
    return 0


def tempo_import(
    input_dir: str,
    otlp_url: str = "http://127.0.0.1:4318/v1/traces",
) -> int:
    """Import Tempo traces from an exported ``traces.json`` by replaying to the target OTLP endpoint.

    Reads ``{input_dir}/tempo/traces.json`` (written by :func:`tempo_export`), reconstructs
    spans via the OpenTelemetry SDK, and exports them to the OTLP endpoint using
    protobuf serialization (same code path as normal trace reporting).

    Original timestamps are mapped to a recent time window (last 10 minutes) to ensure
    Tempo's ingester accepts and makes them immediately queryable.

    Args:
        input_dir: Directory containing ``tempo/traces.json``.
        otlp_url: Target Tempo OTLP HTTP endpoint (default ``http://127.0.0.1:4318/v1/traces``).

    Returns:
        0 on success, non-zero on failure.
    """
    import json as _json
    from pathlib import Path

    import time as _time

    input_parent = Path(input_dir) / "tempo"
    traces_file = input_parent / "traces.json"
    if not traces_file.exists():
        logger.error("Traces file not found: %s", traces_file)
        return 1

    try:
        payload = _json.loads(traces_file.read_text())
    except Exception as exc:
        logger.error("Failed to read traces file %s: %s", traces_file, exc)
        return 1

    batches = payload.get("batches", [])
    if not batches:
        logger.warning("No trace batches in %s; nothing to import", traces_file)
        return 0

    # Decode base64 traceId/spanId to hex for OTLP
    _decode_b64_ids(batches)

    # Compute timestamp remapping: original range -> last 10 minutes
    now_ns = int(_time.time() * 1e9)
    min_ts = float("inf")
    max_ts = 0.0
    for batch in batches:
        for ss in batch.get("scopeSpans", []):
            for span in ss.get("spans", []):
                start_ns = int(span["startTimeUnixNano"])
                end_ns = int(span["endTimeUnixNano"])
                if start_ns < min_ts:
                    min_ts = start_ns
                if end_ns > max_ts:
                    max_ts = end_ns

    target_min = now_ns - 10 * 60 * int(1e9)  # 10 minutes ago
    target_max = now_ns
    orig_range = max_ts - min_ts if max_ts > min_ts else 1
    target_range = target_max - target_min
    logger.info(
        "Remapping timestamps: orig [%d, %d] -> target [%d, %d]",
        int(min_ts // 1e9), int(max_ts // 1e9),
        int(target_min // 1e9), int(target_max // 1e9),
    )

    # Use OpenTelemetry SDK for protobuf serialization (same as normal trace reporting)
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.trace import SpanKind

    resource = Resource.create({"service.name": "rl_insight_monitor"})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=otlp_url)
    provider.add_span_processor(BatchSpanProcessor(
        exporter,
        schedule_delay_millis=500,
        max_export_batch_size=200,
    ))
    tracer = provider.get_tracer(__name__)

    kind_map = {
        "SPAN_KIND_UNSPECIFIED": SpanKind.INTERNAL,
        "SPAN_KIND_INTERNAL": SpanKind.INTERNAL,
        "SPAN_KIND_SERVER": SpanKind.SERVER,
        "SPAN_KIND_CLIENT": SpanKind.CLIENT,
        "SPAN_KIND_PRODUCER": SpanKind.PRODUCER,
        "SPAN_KIND_CONSUMER": SpanKind.CONSUMER,
        1: SpanKind.INTERNAL,
        2: SpanKind.SERVER,
        3: SpanKind.CLIENT,
        4: SpanKind.PRODUCER,
        5: SpanKind.CONSUMER,
    }

    imported = 0
    for batch in batches:
        for ss in batch.get("scopeSpans", []):
            for span_data in ss.get("spans", []):
                # Remap timestamps to recent window
                orig_start = int(span_data["startTimeUnixNano"])
                orig_end = int(span_data["endTimeUnixNano"])
                new_start = int(target_min + (orig_start - min_ts) * target_range / orig_range)
                new_end = int(target_min + (orig_end - min_ts) * target_range / orig_range)
                if new_end <= new_start:
                    new_end = new_start + 1000000  # 1ms minimum

                # Build attributes
                attrs: dict[str, Any] = {}
                for attr in span_data.get("attributes", []):
                    val = attr.get("value", {})
                    if "stringValue" in val:
                        attrs[attr["key"]] = val["stringValue"]
                    elif "intValue" in val:
                        attrs[attr["key"]] = int(val["intValue"])

                kind_raw = span_data.get("kind", "SPAN_KIND_INTERNAL")
                kind = kind_map.get(kind_raw, SpanKind.INTERNAL)

                span = tracer.start_span(
                    span_data["name"],
                    kind=kind,
                    attributes=attrs,
                    start_time=new_start,
                )
                span.end(end_time=new_end)
                imported += 1

    provider.force_flush()
    logger.info("Imported %d span(s) via OTLP (protobuf)", imported)
    return 0


def _decode_b64_ids(batches: list[dict]) -> None:
    """Convert base64 traceId/spanId in Tempo batches to hex for OTLP ingestion."""
    import base64

    for batch in batches:
        for ss in batch.get("scopeSpans", []):
            for span in ss.get("spans", []):
                for id_key in ("traceId", "spanId", "parentSpanId"):
                    raw = span.get(id_key)
                    if raw and isinstance(raw, str) and not _is_hex_id(raw):
                        try:
                            decoded = base64.b64decode(raw)
                            span[id_key] = decoded.hex()
                        except Exception:
                            pass


def _is_hex_id(value: str) -> bool:
    """Return True if *value* is already a hex-encoded ID (all hex chars)."""
    return len(value) >= 16 and all(c in "0123456789abcdefABCDEF" for c in value)
