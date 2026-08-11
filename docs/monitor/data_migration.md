# Data Migration (Export / Import)

`rl-insight export` and `rl-insight import` move Prometheus metrics and Tempo traces between
RL-Insight instances. This is useful for archiving experiment results, sharing data with
collaborators, or migrating to a new server.

**The RL-Insight server stack must be running** before you export or import. Both commands
need Prometheus (for metrics) and Tempo (for traces) to be reachable. Start it with:

```bash
rl-insight server start
```

## Export

Export pulls Prometheus TSDB blocks and Tempo traces filtered by `project` and
`experiment_name` labels, then writes them into a portable directory.

```bash
rl-insight export --project my-project --experiment exp-001 --output /tmp/my-export
```

### Arguments

| Flag | Default | Description |
|---|---|---|
| `--project` | `*` (all) | Filter by `project` label |
| `--experiment` | `*` (all) | Filter by `experiment_name` label |
| `--output` | *(required)* | Output directory for exported data |
| `--prometheus-url` | `http://127.0.0.1:9090` | Prometheus HTTP API base URL |
| `--tempo-url` | `http://127.0.0.1:3200` | Tempo HTTP query API base URL |
| `--data-dir` | auto-detected | Prometheus TSDB on-disk path |
| `--promtool-bin` | `promtool` | Path to `promtool` binary |

### Pre-flight checks

Before exporting any data, the command verifies that the requested `project` /
`experiment_name` combination actually exists:

- **Prometheus**: queries the `/api/v1/series` endpoint. If no matching series are found,
  export exits with an error.
- **Tempo**: queries the `/api/search` endpoint with a TraceQL query. If no matching
  traces are found, export exits with an error.

This prevents you from exporting an empty directory by mistake.

### Output structure

```
{output}/
├── manifest.json          # project, experiment_name, export timestamp, source path
├── prometheus/            # TSDB blocks (filtered or full copy)
└── tempo/
    └── traces.json        # Full trace JSON with all batches
```

- When both `--project` and `--experiment` are `*` (or omitted), Prometheus blocks are
  copied directly from the TSDB directory — this is fast and preserves all data.
- When specific labels are given, Prometheus uses `promtool dump-openmetrics` + label
  filtering + `create-blocks-from` to rebuild filtered blocks.

### Example: export everything

```bash
rl-insight export --output /tmp/full-backup
```

This is equivalent to `--project "*" --experiment "*"`.

### Example: export a specific experiment

```bash
rl-insight export --project rl-training --experiment run-042 --output /tmp/run-042-export
```

## Import

Import reads previously exported data and writes it into the target RL-Insight instance.

```bash
rl-insight import --input /tmp/my-export
```

### Arguments

| Flag | Default | Description |
|---|---|---|
| `--input` | *(required)* | Directory containing exported data |
| `--prometheus-url` | `http://127.0.0.1:9090` | Target Prometheus HTTP API base URL |
| `--tempo-otlp-url` | `http://127.0.0.1:4318/v1/traces` | Target Tempo OTLP HTTP endpoint |
| `--data-dir` | auto-detected | Target Prometheus TSDB on-disk path |
| `--force` | off | Skip conflict checks and overwrite existing data |

### Conflict detection and `--force`

Before importing, the command reads the `experiment_name` from `manifest.json` and checks
whether that experiment already exists in the target Prometheus instance. If it does, the
import is **blocked** with an error message:

```
[rl-insight] experiment_name 'exp-001' already exists in target Prometheus.
Use --force to overwrite.
```

Pass `--force` to skip this check. When `--force` is used:

- Existing Prometheus blocks with the same name are **deleted and replaced** (via `rmtree` + `copytree`).
- Blocks that do not already exist are copied normally.
- The conflict detection step is skipped entirely.

> **Caution**: `--force` is destructive. It will replace matching TSDB blocks on disk.

### How import works

**Prometheus**: TSDB blocks are copied into the target Prometheus data directory. After
copying, the command calls Prometheus's `/-/reload` endpoint so the new blocks become
queryable without a restart.

**Tempo**: The `traces.json` file is read and replayed to the target OTLP endpoint
(`:4318/v1/traces`) using the OpenTelemetry SDK with protobuf serialization — the same
code path used by normal trace reporting. Original timestamps are remapped to the last
10 minutes so that Tempo's ingester accepts and surfaces them immediately.

A TCP connectivity check runs before the Tempo import. If the OTLP endpoint is
unreachable, the command reports the error clearly and exits.

### Example: import with force overwrite

```bash
rl-insight import --input /tmp/run-042-export --force
```

## Server restart is not required

You do **not** need to restart `rl-insight server` after import. Prometheus data becomes
available after the automatic `/-/reload` call. Tempo data appears in Grafana once the
OTLP spans are ingested (typically within seconds).

Note: if the RL-Insight server is *not* running at all, the import will fail because
Prometheus and Tempo endpoints are unreachable.

## Common scenarios

### Migrating from one machine to another

```bash
# On the source machine
rl-insight export --output /tmp/backup
scp -r /tmp/backup target-host:/tmp/backup

# On the target machine
rl-insight import --input /tmp/backup
```

### Archiving a finished experiment

```bash
rl-insight export --project my-project --experiment exp-042 --output ~/archives/exp-042
```

### Restoring an archived experiment

```bash
rl-insight import --input ~/archives/exp-042 --force
```

## Troubleshooting

### "No metrics found for project=X experiment_name=Y"

The project/experiment combination does not exist in the source Prometheus. Check your
labels — use the Grafana dashboard or Prometheus UI (`:9090`) to verify the correct names.

### "Cannot reach OTLP endpoint"

The RL-Insight server (and Tempo) is not running, or the OTLP port (`4318`) is
blocked. Verify with `rl-insight server start`.

### "experiment_name already exists"

The target already has data for this experiment. Either use `--force` to overwrite, or
import into a fresh instance.
