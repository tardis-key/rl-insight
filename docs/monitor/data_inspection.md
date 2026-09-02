# Data Inspection

`rl-insight data inspect` reads a persisted RL-Insight data directory and lists the projects and experiments it contains. It reports the first and last timestamps found in Prometheus and Tempo for each project/experiment pair.

The command works directly against the data directory and does not require the server stack to be running.

## Inspect the default data directory

```bash
rl-insight data inspect
```

## Inspect a custom data directory

```bash
rl-insight data inspect --log-dir /path/to/rl-insight-data
```

`--log-dir` points to the same data directory used by `rl-insight server start --log-dir`.

## Output

The default output is a compact table. When a range stays within one day, the
end date is omitted; when it crosses days, both dates are shown:

```text
Project | Experiment | Prometheus | Tempo
```

Use JSON for machine-readable output:

```bash
rl-insight data inspect --log-dir /path/to/rl-insight/data --format json
```

## Notes

- Prometheus samples are read from the local TSDB using `promtool`.
- Tempo traces are read from the local Parquet files using PyArrow.
- The default data directory is `~/.rl-insight/data`.
- If a project/experiment appears in only one source, the other source columns are shown as `-`.
