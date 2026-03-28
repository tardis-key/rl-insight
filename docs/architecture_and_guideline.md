# Insight 框架概览和开发指导

<div align="center">
 <img src="https://raw.githubusercontent.com/verl-project/rl-insight/main/assets/rl_insight_framework.svg" width="600" alt="rl-insight-arch.png">
</div>

图中绿色为数据侧模块，蓝色为功能侧模块；上述部分规划在 RL-Insight 内落地。红色模块 **CollectController** / **Collector** 已由 [verl.DistProfiler](https://verl.readthedocs.io/en/latest/perf/verl_profiler_system.html) 提供，短期内本仓库不会关注。

- **InputData / OutputData**：描述流水线两端的数据形态与约束。输入侧不限定单一 RL 框架，也可来自其它框架或离线整理产物；输出侧为各分析能力的结构化结果，供后续 **Visualizer** 等步骤消费。在实现上与 **DataRule** 对齐：`DataEnum`、`ValidationRule`（`rules.py`）、`DataChecker`，以及 **Parser** / **Visualizer** 的 `input_type`。
- **Offline/Online Parser**：负责将特定数据进行进一步加工解析的过程。
- **Plugin**：在线监控场景下，便于在第三方监控栈上做二次开发（接入、展示、导出等）。
- **Metric**：提供各种指标计算等进阶分析能力，这些指标通常是在 RL 精度与性能调试过程中非常有用的关键特征。
- **Collector**：跨平台 / 跨工具的数据采集、上报能力。
- **CollectController**：决定 **Collector** 采集时机与采集内容，通常会和特定的强化学习流程高度耦合。

---

## 模块简介

| Concept | Location | Role |
|---------|----------|------|
| Entry | `rl_insight/main.py`, `rl_insight/pipeline/` | `main` 对接 CLI；`pipeline` 定义业务流程并选择 **Parser** / **Visualizer**。 |
| DataRule | `rl_insight/data/data_checker.py`, `rl_insight/data/rules.py` | `DataEnum` 区分数据阶段；`DataChecker` 按类型执行对应的 `ValidationRule`。 |
| Parser | `rl_insight/parser/parser.py`, `rl_insight/parser/*_parser.py` | 基于约定的 `input_type` 做解析；字段约定见 `rl_insight/utils/schema.py`（`DataMap`、`EventRow`、`Constant` 等）。 |
| Visualizer | `rl_insight/visualizer/visualizer.py`, `rl_insight/visualizer/timeline_visualizer.py`, … | 消费 **Parser** 输出，基于约定的 `input_type` 做可视化。 |

---

## 扩展指南

### 1. 扩展 **DataRule**

适用于：`InputData` / `OutputData` 的数据类型需要扩展，解析数据语义或字段发生变化，需要新的类型标识与 `ValidationRule`。

1. 在 `DataEnum` 中增加新值（字符串建议与 CLI 一致）。
2. 在 `DataChecker.rules` 中为新 `DataEnum` 挂载 `ValidationRule` 子类（`rules.py`），实现 `check()` / `error_message`。
3. 将能消费该数据的 **Parser** / **Visualizer** 的类属性 `input_type` 设为对应 `DataEnum`。
4. 在 `docs/data/data_specification.md` 中补充数据形态说明。

### 2. 扩展 **Parser** / **Visualizer**

适用于：在仍使用 **OfflineInsightPipeline** 的前提下，新增一种解析后端或一种可视化输出。

**Parser**

1. 新增模块，例如 `rl_insight/parser/my_parser.py`。
2. 继承 `BaseClusterParser`，实现 `run()` 方法。
3. `@register_cluster_parser("<name>")`，保证 `get_cluster_parser_cls("<name>")` 可用。
4. 更新 `main.py` 中 `--profiler-type` 的 help 与相关用户文档。

**Visualizer**

1. 新增模块，例如 `rl_insight/visualizer/my_visualizer.py`。
2. 继承 `BaseVisualizer`，实现 `run()` 方法。
3. `@register_cluster_visualizer("<name>")`，保证 `get_cluster_visualizer_cls("<name>")` 可用。
4. 更新 `main.py` 中 `--vis-type` 的 help 与相关用户文档。

若输入或中间数据形态变化，需同步按上一节扩展 **DataRule**。

### 3. 扩展 **Pipeline**

适用于：全新的处理范式（跳过步骤、插入预处理、多产物、在线多进程流程等）。

1. 在 `rl_insight/pipeline/` 新增类，实现 `__init__(self, config)`、`run(self)`，按需组合 `DataChecker`、`get_cluster_parser_cls`、`get_cluster_visualizer_cls` 等。
2. 在 `main.py` 的 `SUPPORTED_PIPELINE_TYPES` 中注册，例如 `{"MyPipeline": MyPipeline}`。
3. 更新 `--pipeline-type` 的 help，名称与 dict key 一致，并更新文档。
4. 若数据解析或数据类型发生变化，同步扩展 **DataRule** / **Parser** / **Visualizer**。
