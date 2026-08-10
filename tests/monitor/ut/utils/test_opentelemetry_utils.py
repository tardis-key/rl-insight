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

"""Unit tests for OpenTelemetry trace collection."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from opentelemetry.sdk.resources import SERVICE_NAME
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from rl_insight.utils import opentelemetry_utils as otel_module


def test_init_should_disable_collection_when_endpoint_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = MagicMock()
    monkeypatch.setattr(otel_module, "OTLPSpanExporter", exporter)

    collector = otel_module.OpenTelemetryTraceCollector(
        namespace="trainer", endpoint=None
    )
    collector.record_span("ignored", 10, 20, attributes={"step": 1})

    assert collector.enabled is False
    exporter.assert_not_called()


def test_record_span_should_export_timing_attributes_and_resource_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = InMemorySpanExporter()
    monkeypatch.setattr(otel_module, "OTLPSpanExporter", lambda endpoint: exporter)
    monkeypatch.setattr(otel_module, "BatchSpanProcessor", SimpleSpanProcessor)
    collector = otel_module.OpenTelemetryTraceCollector(
        namespace="trainer-monitor", endpoint="http://tempo:4318/v1/traces"
    )

    collector.record_span(
        "rollout", 1_000_000, 2_500_000, attributes={"step": 7, "worker": "w0"}
    )

    spans = exporter.get_finished_spans()
    assert collector.enabled is True
    assert len(spans) == 1
    assert spans[0].name == "rollout"
    assert (spans[0].start_time, spans[0].end_time) == (1_000_000, 2_500_000)
    assert spans[0].attributes == {"step": 7, "worker": "w0"}
    assert spans[0].resource.attributes[SERVICE_NAME] == "trainer-monitor"


# --- tempo_export / tempo_import unit tests ---

import base64
import json
import requests as _requests


class TestTempoExport:
    def test_should_build_traceql_and_fetch_traces_when_project_and_experiment_set(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path,
    ) -> None:
        """Export with both filters produces correct TraceQL and output file."""
        mock_get = MagicMock()
        # First call: search returns trace IDs
        search_resp = MagicMock()
        search_resp.json.return_value = {
            "traces": [{"traceID": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"}]
        }
        # Second call: trace by ID returns batches
        trace_resp = MagicMock()
        trace_resp.json.return_value = {
            "batches": [{
                "resource": {"attributes": []},
                "scopeSpans": [{
                    "scope": {"name": "test"},
                    "spans": [{
                        "traceId": "AADwKw==",
                        "spanId": "AAEAAA==",
                        "name": "rollout",
                    }],
                }],
            }]
        }
        mock_get.side_effect = [search_resp, trace_resp]
        monkeypatch.setattr(_requests, "get", mock_get)

        out = tmp_path / "export-out"
        ret = otel_module.tempo_export(
            project="demo", experiment_name="exp1", output_dir=str(out),
        )

        assert ret == 0
        # Verify correct TraceQL query
        search_call = mock_get.call_args_list[0]
        assert search_call[1]["params"]["q"] == (
            '{ span.project = "demo" && span.experiment_name = "exp1" }'
        )
        # Verify output file
        traces_file = out / "tempo" / "traces.json"
        assert traces_file.exists()
        data = json.loads(traces_file.read_text())
        assert len(data["batches"]) == 1
        assert data["batches"][0]["scopeSpans"][0]["spans"][0]["name"] == "rollout"

    def test_should_emit_wildcard_query_when_filters_are_none(self, monkeypatch, tmp_path) -> None:
        """None or '*' project/experiment produce a query without those filters."""
        mock_get = MagicMock()
        search_resp = MagicMock()
        search_resp.json.return_value = {"traces": []}
        mock_get.return_value = search_resp
        monkeypatch.setattr(_requests, "get", mock_get)

        out = tmp_path / "export-wildcard"
        ret = otel_module.tempo_export(
            project="*", experiment_name="*", output_dir=str(out),
        )

        assert ret == 0
        search_call = mock_get.call_args_list[0]
        assert search_call[1]["params"]["q"] == "{}"

    def test_should_return_error_when_search_fails(self, monkeypatch, tmp_path) -> None:
        """A 500 from the search API should return non-zero."""
        

        mock_get = MagicMock()
        mock_get.side_effect = _requests.RequestException("boom")
        monkeypatch.setattr(_requests, "get", mock_get)

        out = tmp_path / "export-fail"
        ret = otel_module.tempo_export(
            project="p", experiment_name="e", output_dir=str(out),
        )

        assert ret == 1

    def test_should_return_error_when_output_dir_is_none(self) -> None:
        """Calling export without output_dir should fail fast."""
        ret = otel_module.tempo_export(project="p", output_dir=None)
        assert ret == 1


class TestTempoImport:
    def test_should_post_resource_spans_to_otlp_when_traces_file_is_valid(
        self, monkeypatch, tmp_path,
    ) -> None:
        """Valid traces.json is converted and POSTed to the OTLP endpoint."""
        traces_dir = tmp_path / "tempo"
        traces_dir.mkdir()
        traces_dir.joinpath("traces.json").write_text(json.dumps({
            "batches": [{
                "resource": {"attributes": []},
                "scopeSpans": [{
                    "scope": {"name": "test"},
                    "spans": [{
                        "traceId": base64.b64encode(bytes.fromhex("a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6")).decode(),
                        "spanId": base64.b64encode(bytes.fromhex("1f2e3d4c5b6a7980")).decode(),
                        "name": "step",
                    }],
                }],
            }]
        }))

        mock_post = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp
        monkeypatch.setattr(_requests, "post", mock_post)

        ret = otel_module.tempo_import(
            input_dir=str(tmp_path),
            otlp_url="http://127.0.0.1:4318/v1/traces",
        )

        assert ret == 0
        mock_post.assert_called_once()
        call_args, call_kwargs = mock_post.call_args
        payload = call_kwargs["json"]
        assert "resourceSpans" in payload
        span = payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
        # IDs should be converted from base64 to hex
        assert span["traceId"] == "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
        assert span["spanId"] == "1f2e3d4c5b6a7980"

    def test_should_return_error_when_traces_file_missing(self, tmp_path) -> None:
        """Missing traces.json should return non-zero."""
        ret = otel_module.tempo_import(input_dir=str(tmp_path))
        assert ret == 1

    def test_should_return_error_when_otlp_endpoint_rejects(self, monkeypatch, tmp_path) -> None:
        """A 400 from the OTLP endpoint should return non-zero."""
        traces_dir = tmp_path / "tempo"
        traces_dir.mkdir()
        traces_dir.joinpath("traces.json").write_text(json.dumps({
            "batches": [{
                "resource": {"attributes": []},
                "scopeSpans": [{
                    "scope": {"name": "test"},
                    "spans": [{
                        "traceId": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
                        "spanId": "1f2e3d4c5b6a7980",
                        "name": "step",
                    }],
                }],
            }]
        }))

        mock_post = MagicMock()
        bad_resp = MagicMock()
        bad_resp.status_code = 400
        bad_resp.text = "bad request"
        mock_post.return_value = bad_resp
        monkeypatch.setattr(_requests, "post", mock_post)

        ret = otel_module.tempo_import(input_dir=str(tmp_path))
        assert ret == 1

    def test_should_not_alter_already_hex_ids_during_import(self, monkeypatch, tmp_path) -> None:
        """Spans with already-hex IDs are left unchanged."""
        traces_dir = tmp_path / "tempo"
        traces_dir.mkdir()
        traces_dir.joinpath("traces.json").write_text(json.dumps({
            "batches": [{
                "resource": {"attributes": []},
                "scopeSpans": [{
                    "scope": {"name": "test"},
                    "spans": [{
                        "traceId": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
                        "spanId": "1f2e3d4c5b6a7980",
                        "name": "step",
                    }],
                }],
            }]
        }))

        mock_post = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp
        monkeypatch.setattr(_requests, "post", mock_post)

        ret = otel_module.tempo_import(input_dir=str(tmp_path))

        assert ret == 0
        payload = mock_post.call_args.kwargs["json"]
        span = payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
        assert span["traceId"] == "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
        assert span["spanId"] == "1f2e3d4c5b6a7980"
