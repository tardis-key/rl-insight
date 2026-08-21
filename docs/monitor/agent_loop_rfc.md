# RFC: Three-Repository Agent Loop Observability

## Summary

Agent-loop observability is delivered by three coordinated repositories. The
design keeps the dashboard protocol in RL-Insight, keeps trainer integration
thin in verl, and keeps business instrumentation in Uni-Agent.

## Repositories and responsibilities

| Repository | Responsibility | Branch / PR target |
|---|---|---|
| [rl-insight](https://github.com/verl-project/rl-insight) | Own `agent_loop_session`, identity/lane protocol, span contract, Tempo/Prometheus emission, and Agent Loop Trajectory dashboard. | `main`; PR adds protocol API, dashboard, docs, and demo generator. |
| [verl](https://github.com/verl-project/verl) | Initialize RL-Insight from trainer config and forward completed spans and agent-loop sessions. Add no agent business semantics. | `rlinsight` → `main`; PR updates `RLInsightLogger`. |
| [uni-agent](https://github.com/verl-project/uni-agent) | Instrument session, task, gateway generation, and sandbox lifecycle; adapt framework objects to the RL-Insight protocol. | `rlinsight` → `main`; PR adds adapter, instrumentation, tests, and integration guide. |

## Interface contract

1. Uni-Agent creates one `agent_loop_session` per agent session.
2. Uni-Agent emits `agent_task`, `gateway_generation`, and `agent_sandbox`
   completed spans through verl's `RLInsightLogger.trace_span`.
3. Uni-Agent calls `RLInsightLogger.agent_loop_session` through the same
   trainer-side adapter and finishes the session with trajectory summaries.
4. verl derives project/experiment identity from trainer config and forwards
   calls to RL-Insight.
5. RL-Insight validates nothing framework-specific; it publishes the standard
   identity, spans, hierarchy gauges, and dashboard.

## Non-goals

- RL-Insight does not import Uni-Agent or verl types.
- verl does not own lane IDs or dashboard semantics.
- Uni-Agent does not emit private `agent_loop_*` dashboard metrics.
