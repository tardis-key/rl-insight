# Quick Start

This guide starts RL-Insight Monitor from a fresh checkout, runs the local server stack, and adds the first metric and trace calls to training code.

For service version requirements and Linux platform support, see [Server Installation](./server_installation.md).

## 1. Install RL-Insight

From the repository root:

```bash
pip install -r requirements.txt
pip install -e .
```

Verify the CLI entry point:

```bash
rl-insight --help
```

## 2. Install Server Services

RL-Insight depends on Prometheus, Tempo, and Grafana for online monitoring. This section shows the direct install path. For supported platforms, offline installation, or using existing service binaries, see [Server Installation](./server_installation.md). The easiest Linux path is to let RL-Insight install the supported versions into `~/.rl-insight/services`:

```bash
rl-insight server install
```

The installer uses these versions:

| Service | Installer version | Requirement |
|---|---:|---:|
| Prometheus | `2.54.1` | `>= 2.30.0` |
| Tempo | `2.6.1` | `>= 2.0.0` |
| Grafana | `13.0.0` | `>= 13.0.0` |

If your environment already provides compatible system packages, `server start` can use them directly. The detailed options and troubleshooting notes are covered in [Server Installation](./server_installation.md).

## 3. Start The Stack

Start Prometheus, Tempo, and Grafana:

```bash
rl-insight server start
```

The command prints the detected server IP, Grafana URL, and trainer-facing OTLP endpoint. Foreground mode keeps logs attached and stops the services when you press `Ctrl+C`.

Common variants:

```bash
rl-insight server start --detach
rl-insight server start --attach-logs
rl-insight server start --config path/to/config.yaml
rl-insight server stop
```

Default endpoints:

| Endpoint | Default |
|---|---|
| Grafana | `http://<server-ip>:3000` |
| Prometheus | `http://<server-ip>:9090` |
| Tempo query API | `http://<server-ip>:3200` |
| OTLP HTTP traces | `http://<server-ip>:4318/v1/traces` |

## 4. Instrument Training Code

Set the RL-Insight server IP before launching or initializing training workers. Use the server IP printed by `rl-insight server start`:

```bash
export RL_INSIGHT_SERVICE_IP=<server-ip>
```

Then run a small continuous demo. It uses the three metric helpers and one `trace_state` span inside a loop, so Prometheus and Grafana keep receiving representative live samples while it runs:

```python
import time

import ray
import rl_insight as insight

ray.init(namespace="rl-insight-monitor")
insight.init(project="verl", experiment_name="quick_start_demo")

step = 0
labels = {"worker": "trainer_0"}
while True:
    with insight.trace_state("rollout_generate", state_lane_id="replica_0", step=step):
        time.sleep(2)

    insight.metric_count("train_step_total", amount=1, **labels)
    insight.metric_value("reward_mean", value=1.0 + step * 0.01, **labels)
    insight.metric_distribution(
        "step_latency_ms", value=200 + (step % 5) * 20, **labels
    )

    step += 1
    time.sleep(0.5)
```

The demo starts a local Ray runtime. If you already have a Ray cluster for a real training job, connect to it instead, for example `ray.init(address="auto", namespace="rl-insight-monitor")` or by setting `RAY_ADDRESS`. If your RL framework already integrates RL-Insight, you can start the corresponding RL training job after the server stack is running and `RL_INSIGHT_SERVICE_IP` is set. The demo above is for quickly checking custom metric reporting.

## 5. Open Grafana

Open the Grafana URL printed by `rl-insight server start`. By default, Grafana listens at:

```text
http://<server-ip>:3000
```

The default login is:

```text
username: admin
password: admin
```

After login, open **Dashboards** from the left navigation and choose `RL-Insight`. For the sample script in this guide, select the `quick_start_demo` dashboard and set the time range to a recent window such as **Last 5 minutes** while the script is still running. For framework-specific runs, open the dashboard that matches that integration or experiment.

Bundled dashboard JSON files live in the package directory:

```text
rl_insight/config/services/grafana/dashboards
```

At startup, RL-Insight copies them into the runtime dashboards directory and provisions Grafana from there:

```text
~/.rl-insight/runtime/dashboards
```

If you add or update a dashboard JSON file such as `quick_start_demo.json`, place it in the bundled dashboards directory before starting Grafana, or restart the stack so RL-Insight copies the latest file into the runtime directory and Grafana provisions it. Prometheus metrics and Tempo traces are persisted under `~/.rl-insight/data` by default. Stopping the server does not delete collected data.

## 6. Stop Services

Foreground mode:

```bash
Ctrl+C
```

Detached mode or another terminal:

```bash
rl-insight server stop
```

## Configuration Shortcuts

Pass overrides through `insight.init(config=...)`:

```python
insight.init(
    project="verl",
    experiment_name="ppo-smoke-test",
    config={
        "server": {
            "namespace": "rl_insight_monitor",
            "backend": "ray",
            "service_ip": "10.0.0.8",
        },
        "prometheus": {
            "metrics_report_port": 9092,
            "prometheus_port": 9090,
        },
        "otel": {
            "otel_port": 4318,
        },
    },
)
```

Environment variables take precedence for common deployment settings:

| Variable | Purpose |
|---|---|
| `RL_INSIGHT_SERVICE_IP` | Server IP used by training workers. |
| `RL_INSIGHT_OTEL_PORT` | OTLP HTTP port, default `4318`. |
| `RL_INSIGHT_PROMETHEUS_PORT` | Prometheus HTTP port, default `9090`. |
| `RL_INSIGHT_PROMETHEUS_CONFIG_FILE` | Prometheus config path used by target registration logic. |

## Troubleshooting

If `server start` reports missing or incompatible services, run:

```bash
rl-insight server install
```

If training workers emit no traces, check that `RL_INSIGHT_SERVICE_IP` points to the node running Tempo and that workers can reach `http://<server-ip>:4318/v1/traces`.

If metrics do not appear, check that the monitor hub process is reachable from Prometheus and that the Prometheus configuration points to the hub `/metrics` endpoint.


