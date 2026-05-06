# memory_experiment_log.md — 记忆/知识底座实验轨迹表

> 定位：L3 实验记录。每次改动记忆系统（架构/检索/写入/prompt/元数据/配置）时，在此追加一条记录。  
> 来源：arXiv:2604.01007 OMNI-SIMPLEMEM 自动研究管线的实验轨迹思想。  
> 原则：No Execution, No Memory — 只记录已执行并有指标结果的实验。

## 字段说明

| 字段 | 含义 |
|---|---|
| ID | 自增序号 |
| 日期 | YYYY-MM-DD |
| 假设 | 改动前的预期（一句话） |
| 改动类型 | 见 `memory_discovery_taxonomy.md` 的 6 类 |
| 改动内容 | 改了什么文件/配置/prompt（极简） |
| 验证方式 | 如何评估（脚本/用例/指标/人工） |
| 结果 | 指标变化或定性结论 |
| 接受/回滚 | ✅ 接受 / ❌ 回滚 |
| commit | git short hash（接受时填） |
| 备注 | 回滚原因、后续禁忌、关联实验 ID |

## 实验记录

<!-- 按时间倒序追加 -->

| ID | 日期 | 假设 | 类型 | 改动 | 验证 | 结果 | 状态 | commit | 备注 |
|---|---|---|---|---|---|---|---|---|---|
| 004 | 2026-05-06 | 将 discovery taxonomy 前置到写入决策，可减少无法归类的候选直接升层 | ingestion | typed_memory_sop.md 写入策略加入 6 类 taxonomy 门控；memory_discovery_taxonomy.md 使用方式扩展到写入前归类 | pending | pending | ⏳ | pending | 原则3：taxonomy 接入写入决策 |
| 003 | 2026-05-06 | 将 invalidates 具体化为可观察触发器，可降低过期动态记忆长期污染 | write_rule | memory_volatility_sop.md 已含触发器表，确认复用；typed_memory_sop.md 补升层限制 | diff review + 关键字检查 + git status | 关键字与表格列数检查通过；新增 typed 升层限制；volatility 触发器规则已存在并复用 | ✅ | 5e29d81 | 原则2：失效/复核触发器 |
| 002 | 2026-05-06 | 写入前显式区分事实/假设/边界/失效条件，可降低未验证推断污染长期记忆 | write_rule | typed_memory_sop.md 增加四格写入门控；memory_volatility_sop.md 模板已含四格字段 | diff review + git status | typed SOP 增加13行门控说明；volatility 模板已具备字段无需新增差异 | ✅ | c23d0bf | 原则1：事实-假设-边界-失效条件 |
| 001 | 2026-05-06 | 建立实验轨迹表本身可提升改造可控性 | infra | 新建 memory_experiment_log.md | 文件存在+L1索引 | 已创建 | ✅ | — | 首条基线记录 |