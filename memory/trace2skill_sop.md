# trace2skill_sop：轨迹驱动的经验蒸馏与合并治理

## Struct Header
- Trigger: 长任务(>=10 turns)结束后，需从执行轨迹提取可复用经验。
- Inputs: 执行轨迹(working memory + history)、任务类型、失败/成功标记。
- Outputs: trajectory_patch.json + merge_gate结果 + 收益追踪记录。
- Tools: typed_memory_sop(四格门控), memory_management_sop(层级规则)。
- Side effects: 向L1/L2/L3写入trajectory patch，可能改变召回优先级。
- Risk: 过度泛化单次经验导致噪声记忆。
- Failure path: merge_gate拒绝→保留trajectory_patch供人工审核。
- Review: 蒸馏后通过门禁检查+utility tracing验证收益。


> 来源：arXiv:2603.25158 Trace2Skill 阅读与 GA 实践抽象。
> 定位：L3 SOP。用于"任务结束后从执行轨迹提取经验 patch → 合并门禁 → 追踪收益"。
> 前置依赖：typed_memory_sop（写入四格门控）、memory_management_sop（层级规则）。
> 原则：No Execution, No Memory；轨迹证据优先于主观总结；先局部 patch，后层级归纳。

---

## 1. 适用场景

在以下任一条件满足时启用：

1. 一次长任务（≥10 turns）结束后，执行中产生了可复用的经验、教训或避坑。
2. 准备向 L1/L2/L3/SOP/脚本写入新内容前，需要做合并前检查。
3. 需要评估某条已有 SOP/规则是否在真实任务中产生收益或造成负担。

---

## 2. Stage 1：Trajectory Patch 提取

### 2.1 何时提取

- 长任务结束后（成功或失败均可）；
- 观察到重复模式（≥2 次类似失败或成功路径）；
- 用户明确要求"记住/固化/写入"。

### 2.2 Patch 模板

每次提取一个 trajectory_patch，填写以下字段：

```yaml
# --- trajectory_patch ---
id: TP-YYYY-MM-DD-NNN          # 日期+序号
source_task: <任务一句话描述>
trace_type: success | failure | mixed
turns: <大约轮数>

local_lesson: |
  <从本次轨迹中提取的局部经验，1-3句话>

proposed_patch:
  target_layer: L1 | L2 | L3-SOP | L3-script | no_change
  target_file: <目标文件名，无变更则留空>
  change_type: add | modify | prune | no_change
  content_sketch: |
    <拟写入的内容摘要，≤3行>
  evidence: |
    <支撑证据：工具输出/错误码/文件行号/git hash>

risk_assessment:
  overgeneralization: low | medium | high   # 单次样本泛化为通用规则的风险
  token_cost: low | medium | high           # 写入后常驻 token 增量
  volatility: low | medium | high           # 信息随时间失效的速度

merge_decision: accept | reject | defer
merge_reason: <一句话>
```

### 2.3 提取原则

1. **轨迹证据优先**：patch 的 evidence 必须来自真实工具调用结果，不可凭推理填写。
2. **局部优先**：一个 patch 只解决一个局部问题，不要在一个 patch 里改多件事。
3. **频率 + 风险双标准**：
   - 高频 pattern → 适合升为通用规则（L1/L2）；
   - 低频但高危 pattern → 进入红线/风险 SOP，不丢弃；
   - 低频低危 → defer，等观察到第 2 次再升层。
4. **失败样本禁直升**：失败 patch 先进 `failed_experiment_log`，满足 typed_memory_sop §4.2 的条件后才可升层。

---

## 3. Stage 2：Patch Merge Checklist

在将 trajectory_patch 写入 memory/SOP 前，**必须逐项检查**：

### 3.1 检查表

| # | 检查项 | 通过条件 | 不通过时处理 |
|---|--------|---------|-------------|
| 1 | **等价规则检查** | L1/L2/目标 SOP 中无语义等价内容 | 合并到已有条目，不新增 |
| 2 | **冲突检查** | 与现有规则无矛盾 | 解决冲突后再写入；若无法解决，defer |
| 3 | **样本量检查** | 非单次偶然（≥2次观察，或 1次但高危） | 单次低危 → defer，记入 patch log 等下次观察 |
| 4 | **适用域标注** | 有明确 boundary（版本/任务类型/前置条件） | 补充 boundary 后才写入 |
| 5 | **token 成本评估** | L1 不因此超 30 行；L2 增量 ≤ 5 行 | 压缩或降层 |
| 6 | **波动性评估** | 低波动直接写；中/高波动需 invalidates 条件 | 补 invalidates 或降为 L3/defer |
| 7 | **四格门控** | 满足 typed_memory_sop §4.1 的 fact/hypothesis/boundary/invalidates | 不满足则不写 |
| 8 | **实验日志回填** | 涉及记忆系统改动时，回填 memory_experiment_log | 不回填则不提交 |

### 3.2 快速判断流

```
patch → 等价? → 冲突? → 样本量够? → 有边界? → token可控? → 波动性OK? → 四格OK? → 写入
   ↓否     ↓是     ↓不够     ↓无      ↓超限      ↓高波动     ↓不满足
  合并   解决/defer  defer    补边界    压缩/降层   补invalidates  不写
```

---

## 4. Stage 3：Utility Tracing（轻量版）

### 4.1 目的

追踪已有 SOP/规则在真实任务中的实际使用与收益，为后续剪枝提供数据。

### 4.2 记录格式

长任务结束时，若有 SOP/规则被召回使用，可选附一条 utility trace：

```yaml
# --- utility_trace ---
date: YYYY-MM-DD
task: <任务一句话>
sop_or_rule: <被使用的 SOP/规则文件名或 L1/L2 条目>
was_recalled: true | false           # 是否被成功召回（L1 索引命中）
changed_action: true | false         # 是否实际改变了执行路径
avoided_failure: true | false        # 是否避免了一次已知失败
extra_cost: none | low | medium      # 是否造成额外 token/时间
prune_signal: none | candidate       # 是否产生剪枝信号
note: <可选备注>
```

### 4.3 使用原则

1. **不强制每次都填**：只在 SOP/规则确实被使用或明显未被使用时记录。
2. **累积观察**：当某条规则连续 5+ 次未被召回且无 avoided_failure，标记为剪枝候选。
3. **不自动删除**：剪枝候选需人工确认后才从 L1/L2/L3 移除。
4. **与 pattern_registry 互补**：pattern_registry 捕捉重复行为模式（≥3 次提议固化），utility tracing 评估已固化规则的持续价值。

---

## 5. 与现有机制的关系

| 现有机制 | Trace2Skill 补充 |
|---------|-----------------|
| typed_memory_sop 四格门控 | Merge Checklist 第 7 项直接引用 |
| memory_experiment_log | 记忆系统改动的 patch 必须回填 |
| failed_experiment_log | 失败 patch 先进此日志，不直升 |
| memory_volatility_sop | Merge Checklist 第 6 项引用波动性评估 |
| pattern_registry | 捕捉重复 → 提议固化；utility tracing 评估固化后收益 |
| memory_discovery_taxonomy | Patch 的 change_type 与 6 类 taxonomy 对齐 |

---

## 6. 最小启动方式

1. 不需要改 ga.py 源码；
2. 长任务结束后，在 working memory / 回复中生成 trajectory_patch yaml；
3. 写 memory 前过一遍 merge checklist；
4. 可选记录 utility trace；
5. Patch 和 trace 记录保存在任务产物或 session_event_log 中，不占 L1/L2 常驻空间。