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

"""Command-line entry point for RL-Insight."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Sequence

from .server.commands import ServerCommands


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry for ``rl-insight``."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, str(args.log_level).upper()))
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        return 130


def _build_parser() -> argparse.ArgumentParser:
    """Construct the root argument parser."""
    parser = argparse.ArgumentParser(prog="rl-insight")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Python logging level.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_server_parser(subparsers)
    _add_data_parser(subparsers)
    return parser


def _add_server_parser(subparsers: argparse._SubParsersAction) -> None:
    commands = ServerCommands()
    server = subparsers.add_parser(
        "server",
        help="Install and manage the RL-Insight server stack.",
    )
    server_subparsers = server.add_subparsers(dest="server_command", required=True)

    install = server_subparsers.add_parser(
        "install",
        help="Download missing Prometheus, Tempo, and Grafana binaries.",
    )
    _add_common_config_args(install)
    install.add_argument(
        "--install-dir",
        type=Path,
        default=None,
        help="Managed install directory used by this installer; default is ~/.rl-insight/services.",
    )
    install.add_argument(
        "--force",
        action="store_true",
        help="Download and reinstall enabled services even when binaries exist.",
    )
    install.add_argument(
        "--local-archive",
        type=Path,
        default=None,
        help="Directory with pre-downloaded .tar.gz archives; skip download when archive matches.",
    )

    install.set_defaults(func=commands.install)

    start = server_subparsers.add_parser(
        "start",
        help="Start the RL-Insight server stack.",
    )
    _add_common_config_args(start)
    mode_group = start.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--detach",
        action="store_true",
        help="Start in background and return immediately.",
    )
    mode_group.add_argument(
        "--attach-logs",
        action="store_true",
        help="Run in foreground and stream service logs.",
    )
    start.set_defaults(func=commands.start)

    stop = server_subparsers.add_parser(
        "stop",
        help="Stop the RL-Insight server stack.",
    )
    _add_common_config_args(stop)
    stop.set_defaults(func=commands.stop)

    targets = server_subparsers.add_parser(
        "targets",
        help="Manage Prometheus scrape targets.",
    )
    target_subparsers = targets.add_subparsers(dest="targets_command", required=True)
    add_targets = target_subparsers.add_parser(
        "add",
        help="Add scrape targets from a YAML file.",
    )
    add_targets.add_argument(
        "target_file",
        type=Path,
        help="YAML file containing Prometheus jobs and targets.",
    )
    _add_common_config_args(add_targets)
    add_targets.set_defaults(func=commands.add_targets)


def _add_common_config_args(parser: argparse.ArgumentParser) -> None:
    """Attach ``--config`` shared by subcommands that read stack YAML."""
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Server YAML; default is bundled rl_insight/config/config.yaml.",
    )


if __name__ == "__main__":
    raise SystemExit(main())


def _add_data_parser(subparsers: argparse._SubParsersAction) -> None:
    """Attach ``export`` and ``import`` subcommands for data migration."""
    export_parser = subparsers.add_parser(
        "export",
        help="Export Prometheus metrics and Tempo traces by project/experiment.",
    )
    export_parser.add_argument(
        "--project",
        type=str,
        default=None,
        help="Filter by project label (default: *, match all).",
    )
    export_parser.add_argument(
        "--experiment",
        type=str,
        default=None,
        help="Filter by experiment_name label (default: *, match all).",
    )
    export_parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output directory for exported data.",
    )
    export_parser.add_argument(
        "--prometheus-url",
        type=str,
        default="http://127.0.0.1:9090",
        help="Prometheus HTTP API URL (default: http://127.0.0.1:9090).",
    )
    export_parser.add_argument(
        "--tempo-url",
        type=str,
        default="http://127.0.0.1:3200",
        help="Tempo HTTP query API URL (default: http://127.0.0.1:3200).",
    )
    export_parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Prometheus TSDB data directory. Auto-detected if not set.",
    )
    export_parser.add_argument(
        "--promtool-bin",
        type=str,
        default="promtool",
        help="Path to promtool binary (default: promtool from PATH).",
    )
    export_parser.set_defaults(func=_handle_export)

    import_parser = subparsers.add_parser(
        "import",
        help="Import previously exported data into the current RL-Insight instance.",
    )
    import_parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Directory containing exported data.",
    )
    import_parser.add_argument(
        "--prometheus-url",
        type=str,
        default="http://127.0.0.1:9090",
        help="Target Prometheus HTTP API URL (default: http://127.0.0.1:9090).",
    )
    import_parser.add_argument(
        "--tempo-otlp-url",
        type=str,
        default="http://127.0.0.1:4318/v1/traces",
        help="Target Tempo OTLP HTTP endpoint (default: http://127.0.0.1:4318/v1/traces).",
    )
    import_parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Target Prometheus TSDB data directory. Auto-detected if not set.",
    )
    import_parser.add_argument(
        "--force",
        action="store_true",
        help="Skip conflict detection and overwrite existing data.",
    )
    import_parser.set_defaults(func=_handle_import)


def _handle_export(args: argparse.Namespace) -> int:
    """Run data export for Prometheus and Tempo."""
    from .utils.prometheus_utils import prometheus_export as _prom_export
    from .utils.opentelemetry_utils import tempo_export as _tempo_export

    project = args.project or "*"
    experiment = args.experiment or "*"

    print(f"Exporting project={project}, experiment={experiment}")
    print()

    # Prometheus export
    print("--- Prometheus Export ---")
    ret = _prom_export(
        project=project,
        experiment_name=experiment,
        output_dir=str(args.output),
        prometheus_url=args.prometheus_url,
        data_dir=str(args.data_dir) if args.data_dir else None,
        promtool_bin=args.promtool_bin,
    )
    if ret != 0:
        print("Prometheus export FAILED")
        return ret

    # Tempo export
    print()
    print("--- Tempo Export ---")
    ret = _tempo_export(
        project=project,
        experiment_name=experiment,
        output_dir=str(args.output),
        tempo_url=args.tempo_url,
    )
    if ret != 0:
        print("Tempo export FAILED")
        return ret

    print()
    print(f"Export complete: {args.output}")
    return 0


def _handle_import(args: argparse.Namespace) -> int:
    """Run data import for Prometheus and Tempo."""
    from .utils.prometheus_utils import prometheus_import as _prom_import
    from .utils.opentelemetry_utils import tempo_import as _tempo_import

    print(f"Importing from {args.input}")
    print()

    # Prometheus import
    print("--- Prometheus Import ---")
    ret = _prom_import(
        input_dir=str(args.input),
        prometheus_url=args.prometheus_url,
        data_dir=str(args.data_dir) if args.data_dir else None,
        force=args.force,
    )
    if ret != 0:
        print("Prometheus import FAILED")
        return ret

    # Tempo import
    print()
    print("--- Tempo Import ---")
    ret = _tempo_import(
        input_dir=str(args.input),
        otlp_url=args.tempo_otlp_url,
    )
    if ret != 0:
        print("Tempo import FAILED")
        return ret

    print()
    print(f"Import complete from {args.input}")
    return 0
