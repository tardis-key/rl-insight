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

from __future__ import annotations

from datetime import datetime, timezone

from rl_insight import cli
from rl_insight.data_inspection import (
    ExperimentSummary,
    SourceRange,
    _parse_openmetrics,
    _tempo_pairs,
    format_summaries,
    inspect_data_directory,
)


def test_parse_openmetrics_extracts_project_experiment_and_timestamp() -> None:
    line = (
        'rl_insight_monitor_agent_loop_run_info{experiment_name="exp-1",'
        'global_steps="1",project="proj-1"} 1 1788319580.703'
    )

    parsed = list(_parse_openmetrics(line))

    assert len(parsed) == 1
    pair, timestamp = parsed[0]
    assert pair == ("proj-1", "exp-1")
    assert timestamp == datetime(2026, 9, 2, 3, 26, 20, 703000, tzinfo=timezone.utc)


def test_tempo_pairs_extracts_nested_span_attributes() -> None:
    row = {
        "rs": [
            {
                "Resource": {"Attrs": []},
                "ss": [
                    {
                        "Spans": [
                            {
                                "Attrs": [
                                    {"Key": "project", "Value": ["proj-1"]},
                                    {"Key": "experiment_name", "Value": ["exp-1"]},
                                ]
                            }
                        ]
                    }
                ],
            }
        ]
    }

    assert _tempo_pairs(row) == {("proj-1", "exp-1")}


def test_inspect_data_directory_returns_empty_for_empty_directory(tmp_path) -> None:
    assert inspect_data_directory(tmp_path) == []


def test_parser_accepts_log_dir(tmp_path) -> None:
    args = cli._build_parser().parse_args(
        ["data", "inspect", "--log-dir", str(tmp_path)]
    )

    assert args.log_dir == tmp_path
    assert args.format == "table"
    assert args.func.__name__ == "_handle_data_inspect"


def test_format_summaries_renders_project_experiment_and_ranges() -> None:
    summary = ExperimentSummary(
        project="proj-1",
        experiment="exp-1",
        prometheus=SourceRange(
            start=datetime(2026, 9, 2, 3, 26, 20, tzinfo=timezone.utc),
            end=datetime(2026, 9, 2, 3, 27, 20, tzinfo=timezone.utc),
            count=2,
        ),
        tempo=None,
    )

    output = format_summaries([summary])

    assert "proj-1" in output
    assert "exp-1" in output
    assert "2026-09-02 03:26～03:27" in output
    assert "-" in output


def test_format_summaries_renders_full_dates_when_range_crosses_days() -> None:
    summary = ExperimentSummary(
        project="proj-1",
        experiment="exp-1",
        prometheus=SourceRange(
            start=datetime(2026, 9, 2, 2, 26, tzinfo=timezone.utc),
            end=datetime(2026, 9, 3, 5, 12, tzinfo=timezone.utc),
            count=1,
        ),
        tempo=None,
    )

    output = format_summaries([summary])

    assert "2026-09-02 02:26～2026-09-03 05:12" in output
