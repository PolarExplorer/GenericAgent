# Pipeline Execution SOP — 分阶段流水线执行

## Struct Header
- Trigger: 任务含 ≥3 个有依赖关系的阶段，且产物在阶段间传递。
- Inputs: 各阶段输入 schema、Gate 验收指标、成本预算。
- Outputs: 每阶段产物 + stage_state.json + 项目完成报告。
- Tools: preflight_check.py, batch_runner.py, gate_runner.py, script_gate_runner.py, integration_smoke.py。
- Side effects: 中间产物文件落盘；Gate 不通过进入增量补缺收敛流程。
- Risk: Gate 指标定义不清导致验收流于形式；单阶段失败阻塞全流水线；成本超预算。
- Failure path: Gate FAIL → incremental_converge_sop 增量补缺；模型不可达 → 重试/降级；超时 → checkpoint 回滚。
- Review: 每阶段 Gate PASS 后才可进入下一阶段；外部集成必须跑 integration smoke；所有 Gate PASS 后生成完成报告。

> 来源：知识底座 Stage 0→6 项目复盘
> 适用：任何多阶段数据处理/内容生产项目

> Reuse gate: before building new pipeline stages, gate scripts, or external integration glue, check `glue_coding_gate_sop`; prefer existing batch/gate/smoke utilities and define replacement/rollback seams for new adapters.

## 触发条件
任务含 ≥3 个有依赖关系的阶段，且产物在阶段间传递。

## 流程

```
1. 开工包（一次性）
   ├─ 定义所有阶段 + 每阶段的输入/输出 schema
   ├─ 为每阶段编写 Gate 验收脚本（与 schema 同步，禁 placeholder）
   ├─ 锁定各阶段 Gate 指标（开工后不改，改则走变更记录）
   └─ 成本预估：试跑 5-10 条 → 平均 tokens × 总条数 → 写入预算

2. 每阶段执行
   ├─ preflight_check（→ skill: preflight_check.py）
   │   检查：上一阶段 Gate PASS + 输入文件存在 + schema 合规 + 模型可达
   ├─ 批量生产（→ skill: batch_runner.py）
   │   必须带：retry(3次) + checkpoint(每50条) + 单条超时 + rollback
   ├─ Gate 验收（→ skill: gate_runner.py；脚本型 Gate → script_gate_runner.py）
   │   ├─ PASS → 进入下一阶段
   │   ├─ FAIL → 进入「增量补缺收敛流程」(→ incremental_converge_sop)
   │   └─ PASS_WITH_WARNINGS → 记录豁免理由文件 → 继续
   └─ 写 stage_state.json（→ session_handoff_sop）

2b. 外部集成验收（凡接入飞书/定时器/支付/API/浏览器/移动端等真实外部系统必跑）
   ├─ Local Gate：本地脚本、schema、mock/dry-run 全 PASS；不得宣称真实可用
   ├─ Sandbox Integration Gate：真实调用外部 API，但只打测试群/测试用户/测试资源
   └─ Production Smoke Gate：真实目标小流量冒烟，至少一次端到端真实触发和证据记录
   状态必须显式标注：PASS_LOCAL / PASS_DRY_RUN / PASS_SANDBOX / PASS_REAL_SMOKE / PASS_PRODUCTION / PARTIAL / BLOCKED_BY_USER_CONFIG

3. 收口
   └─ 所有 Gate PASS 后，生成项目完成报告
```

## 关键约束（嵌入自 15 条规则）
- **单文件单职责**：每个脚本只做一件事，gate 脚本不混业务逻辑
- **大文件预分割**：>150 页或 >150MB 的文件在阶段 1 之前自动分割
- **去重前置**：切片阶段做内容指纹去重（simhash），不等抽完再去重
- **公共工具抽取**：`scripts/common/` 存放 file_checks / jsonl_io / progress_tracker
- **Gate 脚本版本管理**：Gate 脚本有版本号，禁止 SKIP_NEEDS_REWRITE

## 典型坑
1. Gate 脚本跟不上产物格式变化 → 产物 schema 变更时**必须同步改 Gate 脚本**
2. 阶段间 schema 不一致导致下游白跑 → 开工时用 JSON Schema 锁定，运行时校验
3. 忘记写 checkpoint，中断后只能从头 → batch_runner.py 强制内置
4. 预算失控（实际花费 3x 预估）→ 每 100 条打印累计消耗，超 150% 自动暂停