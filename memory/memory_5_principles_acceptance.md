# memory_5_principles_acceptance.md — 知识底座 5 原则端到端验收

> 日期：2026-05-06  
> 范围：本轮从 arXiv:2604.01007 吸收并落地的知识底座 5 条治理原则。  
> 原则：No Execution, No Memory；只记录已由工具调用、文件内容、git 状态验证的结论。

## 1. 验收对象

| 原则 | 落地点 | 预期效果 |
|---|---|---|
| P1 事实/假设/边界/失效条件 | `typed_memory_sop.md`、`memory_volatility_sop.md` | 防止把推断写成长期事实 |
| P2 失效触发器/新鲜度复核 | `memory_volatility_sop.md`、`typed_memory_sop.md` | 高/中波动信息有可观察复核条件 |
| P3 discovery taxonomy | `memory_discovery_taxonomy.md`、`memory_experiment_log.md` | 记忆系统改动有统一归类 |
| P4 失败/负样本先入 failed log | `failed_experiment_log.md`、`typed_memory_sop.md` | 防止单次失败被泛化成正向规则 |
| P5 召回预算/Token 成本门控 | `typed_memory_sop.md`、`budget_tracker.py` | 避免知识底座增长导致默认上下文膨胀 |

## 2. 证据清单

| 检查项 | 证据 | 结论 |
|---|---|---|
| 写入总原则 | `memory_management_sop.md` lines 1-5：No Execution, No Memory | PASS |
| 记忆写入需提交 | `memory_management_sop.md` lines 94-99：memory 变更需 git commit | PASS |
| P1 四格门控 | `typed_memory_sop.md` lines 66-85；`memory_volatility_sop.md` 模板含 `fact/hypothesis/boundary/invalidates` | PASS |
| P2 可观察失效 | `memory_volatility_sop.md` lines 65-86；最小验收 lines 132-140 | PASS |
| P3 taxonomy | `memory_discovery_taxonomy.md` lines 87-92：6 类标签与写入前归类要求 | PASS |
| P4 failed log | `failed_experiment_log.md` lines 22-28：升层限制、复用要求、转约束条件 | PASS |
| P5 召回预算 | `typed_memory_sop.md` lines 43-53：默认上限、扩展条件、预算信号、保真红线 | PASS |
| L1 极简索引 | `global_mem_insight.txt` line 13 已只保留 L3 文件组导航，不搬运方法细节 | PASS |
| 实验轨迹 | `memory_experiment_log.md` 002-006 均为 ✅，且表格列数为 10 列 | PASS |
| 提交证据 | `c23d0bf`、`5e29d81`、`4bb18c8`、`2cb4403`、`51e022e` 均可被 git 解析 | PASS |

## 3. 回测样本

| 样本 | 模拟输入 | 应命中原则 | 期望处理 | 结果 |
|---|---|---|---|---|
| S1 写入偏好 | “以后疑问句只是在问意见” | P1/P2 | 拆 `fact/boundary/invalidates`，低波动偏好可入 L2，覆盖条件需清楚 | PASS |
| S2 API 故障 | “某 API 今天 429” | P1/P2/P4 | 不写成永久故障；若是失败样本先留 failed log/L4，复现后再升层 | PASS |
| S3 新 SOP | “新增一个可复用工具 SOP” | P3/L1 | 分类为 pipeline/infra 等主类；L3 写细节，L1 只加导航索引 | PASS |
| S4 读记忆 | “查一个已知 SOP 怎么做” | P5 | 先 L1 定位；L1+目标 L3 足够则停止，不泛读 | PASS |
| S5 失败教训 | “某修复尝试失败一次” | P4 | 不直接写正向规则；先记录失败表现、根因、教训、转约束状态 | PASS |

## 4. 验收脚本结果

第一次脚本检查为 23/24：唯一失败项是 `Layer rule L1 minimal`。复核文件后判定为脚本判据过严：

- `typed_memory_sop.md` lines 87-92 已规定 L1 只写导航索引和一句话红线；
- `global_mem_insight.txt` line 13 已用一行索引 `typed_memory_sop+...` 与 `memory_experiment_log+...`，未搬运 SOP 细节。

因此该项人工复核结论为 PASS，后续验收脚本不应要求 L1 文本必须包含“极简”二字，而应检查“是否存在导航索引且未搬运细节”。

## 5. 结论

本轮知识底座 5 原则已达到“初步完成 / 可暂停”状态：

1. 规则已落入可复用 L3 SOP；
2. L1 已保持导航索引，不搬运细节；
3. 实验 002-006 有轨迹、状态和 commit 证据；
4. 失败样本、波动性、taxonomy、召回预算已能形成端到端链路；
5. 未发现必须立即修订 SOP 的阻塞缺陷。

后续建议：不要继续扩原则；进入观察期。只有当真实任务触发冲突、过期、失败泛化或召回成本异常时，再按实验日志新增下一轮改造。
