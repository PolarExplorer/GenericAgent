# Darwin GA Skill (融合版)

> 融合来源：vendor/darwin-skill/SKILL.md (方法论核心) + 本地安全约束 (GA适配层)
> 设计原则：vendor方法论为主体，GA安全约束为薄壳

## Struct Header

- Reader: GA 总控 / subagent
- When to read: 用户要求维护、优化、评估知识资产；或GA自动检测到低分资产
- Trigger: `/darwin`、`达尔文一下`、`优化skill`、`skill评分`、`skill质量检查`、`帮我改改`、`skill review`
- Inputs:
  - `asset`: 单个目标文件路径，默认只允许 `memory/` 内的 `.md` 或 `.py`
  - `mode`: `eval` / `tests` / `history` / `record` / `full-eval`
  - `authorization`: 是否获得用户对写入、索引变更、批量操作的明确授权
- Outputs: 评分报告、改进建议、test-prompts模板、results.tsv记录
- Tools: `darwin_eval_engine.py`、`file_read`、`file_patch`、`code_run`、`git`
- Side effects: 可写 `memory/.darwin/`；资产patch需用户确认
- Risk: R2 — 资产修改、L1/L2变更风险
- Schedule: 读取目标→基线评估→提出改进→执行+验证→记录
- Failure path: vendor不存在/目标越界/验证失败→降级只读报告
- Review: 资产patch/L1-L2变更/删除归档/批量运行/自动commit前必须用户确认

---

## Purpose

本skill是GA对达尔文评估方法论的安全适配层：保留上游vendor的8维度评分、hill-climbing棘轮、test-prompt实测验证等核心方法论，同时通过GA安全约束防止越权操作。

---

## Hard Deny（安全红线，不可覆盖）

1. 不执行上游Mac-only截图脚本或展示卡片链路
2. 不自动运行"优化所有skills"（必须逐个确认）
3. 不自动patch目标资产（展示diff+确认后才执行）
4. 不自动修改L1/L2/global rules
5. 不自动删除、合并或归档知识资产
6. 不自动git reset/stash/revert/commit（除非用户明确授权且已展示diff）
7. 不直接修改vendor内容
8. 不跨资产"顺手修复"（一次只处理一个主资产）

---

## 8维度评估Rubric（总分100）

### 结构维度（60分）— 静态分析

| # | 维度 | 权重 | 评分标准 |
|---|------|------|---------|
| 1 | Frontmatter质量 | 8 | name规范、description含触发词、≤1024字符 |
| 2 | 工作流清晰度 | 15 | 步骤明确可执行、有序号、每步有明确输入/输出 |
| 3 | 边界条件覆盖 | 10 | 处理异常、有fallback路径、错误恢复 |
| 4 | 检查点设计 | 7 | 关键决策前有用户确认、防自主失控 |
| 5 | 指令具体性 | 15 | 不模糊、有具体参数/格式/示例、可直接执行 |
| 6 | 资源整合度 | 5 | references引用正确、路径可达 |

### 效果维度（40分）— 需实测

| # | 维度 | 权重 | 评分标准 |
|---|------|------|---------|
| 7 | 整体架构 | 15 | 结构层次清晰、不冗余不遗漏 |
| 8 | 实测表现 | 25 | 用测试prompt跑，输出质量是否符合skill宣称的能力 |

### 评分规则
- 每个维度打1-10分，乘以权重
- 总分 = Σ(维度分 × 权重) / 10，满分100
- 改进后总分必须**严格高于**改进前才保留

### 实测表现评分方式
1. 为每个skill设计2-3个**典型用户prompt**
2. 子agent执行：with_skill vs without_skill(baseline)
3. 对比打分：意图完成度、质量提升度、负面副作用
4. 无法跑子agent时退化为干跑验证，标注`dry_run`

---

## 自主优化循环

### Phase 0: 初始化
1. 确认优化范围（指定资产 或 用户选择）
2. 创建git分支：`auto-optimize/YYYYMMDD-HHMM`
3. 初始化/读取 `memory/.darwin/results.tsv`

### Phase 0.5: 测试Prompt设计
为每个资产设计2-3个测试prompt：
- 覆盖最典型使用场景（happy path）
- 一个稍复杂或有歧义的场景
- 保存到 `memory/.darwin/test-prompts/{asset_name}.json`
- **展示给用户确认后再进入评估**

### Phase 1: 基线评估
1. 读取目标资产全文
2. 按维度1-7逐项打分（附简短理由）
3. 对每个测试prompt：spawn子agent跑with_skill和baseline
4. 对比打维度8分
5. 计算加权总分，记录到results.tsv
6. **展示评分卡，暂停等用户确认**

### Phase 2: 优化循环
按基线分数从低到高排序，先优化最弱的。MAX_ROUNDS=3。

每轮：
1. **诊断** — 找得分最低维度
2. **提出改进** — 针对最低维度，生成1个方案（改什么、为什么、预期提升）
3. **执行** — 编辑SKILL.md，git commit
4. **重新评估** — 结构重打分 + 效果重跑测试（独立子agent）
5. **决策** — 新分 > 旧分 → keep；否则 → revert，break
6. **日志** — results.tsv追加

每个skill优化完后**暂停**：展示git diff + 分数变化 + 测试对比，等用户确认。用户说"不好"则回滚。

### Phase 2.5: 探索性重写（可选）
当hill-climbing连续2个skill在round 1就break时提议：
1. 选瓶颈skill，git stash保存当前最优
2. 从头重写（不是微调，是重新组织）
3. 重新评估：重写版 > stash版 → 采用；否则 → stash pop恢复
4. **必须征得用户同意**

### Phase 3: 汇总报告
展示：优化数、实验次数、保留率、回滚率、分数变化表。

---

## Routing Policy

| Intent | 行为 | 风险等级 |
|--------|------|---------|
| `/darwin eval <asset>` | 8维度只读基线评估 | 只读 |
| `/darwin tests <asset>` | 在 `.darwin/test-prompts/` 新建模板 | 低 |
| `/darwin history [asset]` | 读取 `.darwin/results.tsv` | 只读 |
| `/darwin record ...` | 追加 `.darwin/results.tsv` | 低 |
| `/darwin full-eval <asset>` | D1-D8全评（含LLM维度） | 只读 |

---

## Boundary / Stop Conditions

1. 每轮只允许一个主资产；用户改题时先纠偏
2. 未经明确授权，不删除/移动/归档/批量修改/自动提交任何资产
3. 涉及L1/L2/global memory时，先读SOP，展示影响面，请求确认
4. diff超150%体积、验收不可复现或证据不足 → 降级为只读建议
5. 目标路径越界/脏文件来源不明/验证失败 → 停止apply，只输出报告

---

## Error Handling

| 场景 | 触发条件 | 处理方式 |
|------|---------|---------|
| results.tsv损坏 | 列数不匹配/非TSV | 备份为`.bak.YYYYMMDD-HHMM`后重建 |
| 分支已存在 | git checkout -b失败 | 名末加`-2`/`-3`；第3次失败切回现有分支 |
| git revert失败 | 冲突/工作树脏 | 先git stash重试；仍失败手动恢复 |
| MAX_ROUNDS触顶 | 已跑3轮仍有短板 | 展示最弱维度，问用户继续/Phase 2.5/收工 |
| 优化后超150%体积 | 新文件 > 原×1.5 | 拒绝提交，精简后再评 |
| test-prompts.json已存在 | 文件已在目录 | 默认复用，问用户复用/重写/追加 |
| SKILL.md找不到 | 目录存在但无SKILL.md | 记error，继续下一个 |
| 分数精度漂移 | 浮点 | 保留1位小数，改进需严格>旧分 |

**原则**：异常先告知用户，再按规则处理；绝不静默跳过或静默失败。

---

## Constraints（约束规则）

1. **不改变skill核心功能** — 只优化"怎么写"和"怎么执行"，不改"做什么"
2. **不引入新依赖** — 不添加skill原本没有的scripts或references
3. **每轮只改一个维度** — 避免多变更导致无法归因
4. **保持文件大小合理** — 优化后不超过原始150%
5. **可回滚** — 用git revert而非reset --hard
6. **评分独立性** — 效果维度必须用独立子agent评，不能自己改完自己评
7. **棘轮机制** — 只保留改进，自动回滚退步

---

## results.tsv 格式

```
timestamp\tcommit\tskill\told_score\tnew_score\tstatus\tdimension\tnote\teval_mode
```
- `eval_mode`: `full_test`(跑子agent) 或 `dry_run`(模拟推演)
- 文件位置：`memory/.darwin/results.tsv`

---

## Runner CLI

```bash
# 只读评估
python memory/darwin_eval_engine.py eval --asset memory/foo.md
# 全量评估(D1-D8)
python memory/darwin_eval_engine.py full-eval --asset memory/foo.md --config native_claude_config_minimax
# 查看历史
python memory/darwin_eval_engine.py history
# 记录结果
python memory/darwin_eval_engine.py record --asset memory/foo.md --baseline 70 --new-score 80 --decision keep --evidence "D2+D5提升"
```

---

## Design Inspiration

> "You write the goals and constraints in program.md; let an agent generate and test code deltas indefinitely; keep only what measurably improves the objective." — Karpathy, autoresearch

对应关系：
- program.md → 本文件（评估rubric和约束规则）
- train.py → 每个目标资产
- val_bpb → 8维加权总分
- git ratchet → 只保留有改进的commit
- test set → test-prompts/{asset}.json
