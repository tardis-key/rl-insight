# 数据规格与格式说明

本文说明 RL-Insight 当前支持的 **离线 profiling 数据** 的目录布局与 JSON 形态，便于采集与对接。

流水线在校验阶段会使用 `rl_insight.data.DataChecker` 注册的规则；规则类定义在 [`rl_insight/data/rules.py`](../../rl_insight/data/rules.py)（例如 `PathExistsRule` 等）。**具体校验项以代码为准**，部分规则可能尚未接入 `DataChecker.rules`，文档仅描述数据侧约定。

## 一、Torch Profiler 数据

### 目录结构

```text
<profile-data-path>/
└── <role>/
    └── prof_*.json.gz
```

### 文件内容要点

- 解压/解析后的 JSON 需包含 **`distributedInfo`**（如 `rank`）与 **`traceEvents`**（Chrome Trace 风格事件列表）。
- 事件中用于绘制的区间一般为 `ph: "X"`，并带有 `ts`、`dur` 等字段（时间单位以文件内约定为准）。

### 内容示例（节选）

完整文件体积较大，此处仅保留与解析相关的关键字段示意：

```json
{
  "schemaVersion": 1,
  "distributedInfo": {
    "backend": "cpu:gloo,cuda:nccl",
    "rank": 0,
    "world_size": 2
  },
  "traceEvents": [
    {
      "ph": "X",
      "name": "cudaMemGetInfo",
      "pid": 369418,
      "tid": 1722878400,
      "ts": 4541015316353.111,
      "dur": 10083720.552,
      "args": {}
    },
    {
      "name": "process_name",
      "ph": "M",
      "pid": 369418,
      "tid": 0,
      "args": { "name": "ray::WorkerDict.actor_rollout_compute_log_prob" }
    }
  ]
}
```

## 二、MSTX（Ascend）Profiling 数据

### 目录结构

```text
<profile-data-path>/
└── <role>/
    └── *_ascend_pt/
        ├── profiler_info_*.json
        └── ASCEND_PROFILER_OUTPUT/
            └── trace_view.json
```

### trace_view.json 要点

- 为事件数组；解析侧会识别元数据事件（如 `ph: "M"`）以及 **`name` 为 `Overlap Analysis`** 的进程上下文，并消费其中的 **`ph: "X"`** 等区间事件。
- 区间事件通常带有 `ts`、`dur`（具体类型以采集导出为准）。

### 内容示例（节选）

```json
[
  {
    "name": "process_name",
    "pid": 3550586784,
    "tid": 0,
    "ph": "M",
    "args": { "name": "Overlap Analysis" }
  },
  {
    "name": "Computing",
    "pid": 3550586784,
    "tid": 2,
    "ts": "1773285899055563.748",
    "dur": 53.301,
    "ph": "X",
    "args": {}
  }
]
```
