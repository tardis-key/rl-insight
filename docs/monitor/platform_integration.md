# Platform Integration Without the RL-Insight Server

RL-Insight supports running with an external trace backend when no RL-Insight
server is started. This mode is intended for platforms that already provide an
OTLP-compatible trace pipeline and have their own convention for discovering
and scraping Prometheus metrics.

## Select a mode

RL-Insight uses two environment variables:

| Variable | Meaning |
| --- | --- |
| `RL_INSIGHT_SERVER_URL` | URL of an RL-Insight server, such as `http://server:18080`. |
| `RL_INSIGHT_OTLP_ENDPOINT` | Full OTLP/HTTP trace endpoint, such as `https://collector:4318/v1/traces`. |

The precedence is:

1. If `RL_INSIGHT_SERVER_URL` is set, use the existing managed-server mode.
2. Otherwise, if `RL_INSIGHT_OTLP_ENDPOINT` is set, use external-OTLP mode.
3. If neither is set, monitoring is disabled.

When both variables are set, `RL_INSIGHT_SERVER_URL` wins and
`RL_INSIGHT_OTLP_ENDPOINT` is ignored. RL-Insight logs a warning in that case.

## External OTLP mode

Start training with only the external OTLP endpoint:

```bash
export RL_INSIGHT_OTLP_ENDPOINT=https://collector.platform.example:4318/v1/traces
```

The value must be the complete endpoint used by the OpenTelemetry HTTP
exporter. RL-Insight does not append `/v1/traces` automatically. For a normal
OTLP HTTP collector, use a URL ending in `/v1/traces`.

In this mode:

```text
rl_insight.init()
  -> Ray MonitorHubActor
     -> emits trainer metrics on /metrics
     -> exports traces directly to RL_INSIGHT_OTLP_ENDPOINT
```

RL-Insight does not contact an RL-Insight server, does not discover its OTLP
port, and does not register Prometheus targets. The platform must scrape the
MonitorHub `/metrics` endpoint using its own pre-agreed discovery rule.

The existing `prometheus.metrics_report_port` setting remains available for
deployments that need to change the local metrics port. Its default is `9092`.

## Compatibility

The managed mode remains unchanged:

```bash
export RL_INSIGHT_SERVER_URL=http://server:18080
```

In managed mode, RL-Insight continues to discover the OTLP port through
`/api/v1/services`, register Prometheus targets through
`/api/v1/prometheus/targets`, and use the Prometheus, Tempo, and Grafana stack
managed by `rl-insight server start`.
