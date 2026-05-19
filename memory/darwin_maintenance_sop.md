# Darwin Maintenance SOP

## Struct Header
- Reader: GA 总控 / subagent / 审查者
- When to read: 需要维护 SOP、Skill、规则、约束、L1/L2/L3 索引或相关说明资产前先读。
- Trigger: 用户要求“维护/优化/收敛/清理/升级”系统知识资产；或发现规则误触发、SOP 老化、Skill 冲突、索引膨胀、执行失败反复出现。
- Inputs: 目标资产路径、用户授权范围、相关失败证据、当前 git diff、必要的上游 SOP。
- Outputs: 只读评估报告、最小 patch、验证证据、回滚/保留决策、必要的 L1 同步与 git commit。
- Tools: file_read / file_patch / code_run / git；必要时 LLM1 或 subagent 独立评审。
- Side effects: 可能修改 L3 SOP/Skill、L1/L2 索引、规则文件或约束说明；可能产生 git commit。
- Risk: R2；涉及全局规则、约束、自身源码、守护机制时升为 R3，并必须请求用户确认。
- Schedule: 资产界定 → 只读基线 → 候选排序 → 最小 patch → 验证与棘轮 → 人类确认 → 提交/回滚。
- Failure path: 读取 debugging_sop；出现 2 次失败后停止同类改动，改为只读报告或请求用户决策。
- Review: 修改规则/约束/L1/全局高频 SOP 时需要独立审查；无法独立审查时必须展示 diff 与验证证据给用户确认。

## 1. 目标

把达尔文式“单一资产、双重评估、棘轮机制、独立评分、人在回路”迁移到 GA 的知识资产维护中，使 SOP、Skill、规则、约束和索引持续进化，但不扩大失败半径。

本 SOP 是维护型元 Skill，不是批量重写器。

## 2. 适用范围

适用：

- SOP / Skill 的老化、重复、冲突、不可执行；
- 规则或约束的误触发、漏触发、触发语义过宽；
- L1/L2/L3 索引不一致、膨胀或缺失；
- 新增高复用 SOP 后需要结构头、验收和索引同步；
- 多次任务失败后沉淀可复用避坑规则；
- 用户明确要求“优化现有规范/维护知识底座”。

不适用：

- 用户只是要一次性任务交付；
- 资产问题没有证据，只是模型主观觉得“可以更好”；
- 需要读取或移动密钥；
- 涉及自身源码修改但未获得用户确认；
- 需要批量修改多个高风险文件且无法逐个验证。

## 3. 五条核心原则

### 3.1 单一可编辑资产

一次只处理一个主要对象。

允许：

```text
只优化 debugging_sop 的失败升级章节
只修正某条规则的误触发条件
只给一个新 SOP 增加结构头
```

禁止：

```text
一次性优化全部 SOP
一轮同时改 L1、多个 SOP、规则引擎和源码
```

若必须联动，只允许“主资产 + 必需索引同步”，并在报告中说明。

### 3.2 双重评估

维护前后都要评估。

结构评估关注：

- 是否有 Struct Header；
- Trigger / Inputs / Outputs 是否清楚；
- Side effects / Risk / Failure path 是否明确；
- 是否有验收标准；
- 是否和上游 SOP 冲突；
- 是否符合 memory_management_sop。

效果评估关注：

- 是否能减少真实任务中的误用；
- 是否降低失败半径；
- 是否减少重复推理；
- 是否让下一次执行更可验证；
- 是否改善召回与定位。

### 3.3 棘轮机制

只保留明确改进。

基本流程：

```text
记录基线 → 提出最小 patch → 验证 → 对比 → 保留或回滚
```

保留条件至少满足一项：

- 结构字段补全；
- 触发条件更精准；
- 风险边界更明确；
- 验收更可执行；
- 已证实误触发/漏触发被修正；
- L1/L2/L3 定位更一致且未膨胀。

若无法证明变好，默认不改或回滚。

### 3.4 独立评分

修改者不能只靠自评宣布成功。

优先级：

1. 规则/脚本/测试的自动验证；
2. git diff 与静态检查；
3. LLM1 / subagent 独立审查；
4. 用户审查确认。

涉及规则、约束、全局高频 SOP 时，至少展示 diff、风险、验证证据。

### 3.5 人在回路

以下变更必须先获得用户确认：

- 自身源码；
- 全局约束和红线规则；
- 高风险自动化守护机制；
- 批量修改；
- 删除已验证信息；
- 可能影响多个任务路径的规范重构。

## 4. Darwin 评分与证据表

本 SOP 吸收 `alchaincyf/darwin-skill` 的 8 维评分、测试 prompt、结果日志、棘轮机制思想；外部仓库当前隔离核验 commit 为 `2056abfccd924d68ae6baa9193cafff0f666260b`。因该仓库 README 有 MIT badge/link 但未提供实际 `LICENSE` 文件，GA 内只做流程适配，不整仓搬运脚本、模板或素材。

截至 2026-05-18，外部 `alchaincyf/darwin-skill` 已以 vendor 形式只读保存在 `vendor/darwin-skill/`。GA 不直接主动召回 vendor `SKILL.md`，而是通过 `skills/maintenance/darwin_ga_router_skill.md` 与 `darwin_ga_router.py` 白名单调用其对 GA 有利的部分：只读摘要、单资产评估、test-prompts、results 记录。运行状态写入 `.darwin/`。禁止全量自动 patch、直接改 L1/L2/规则、自动 git reset/stash/revert、启用 Mac-only 成果卡片脚本；高风险变更仍按本 SOP 先请求用户确认。

**100 分评分引擎**：`darwin_eval_engine.py` 实现 8 维度 100 分评估（D1-D6 静态机械检查 + D7-D8 LLM 效果干跑）。引擎位于 `D:\AI\GenericAgent\memory\darwin_eval_engine.py`，CLI 支持 `eval`（单文件）、`batch`（D1-D6 批量）、`full-batch`（D1-D8 全量）。批量全量评估约需 10 分钟（每资产 2 次 LLM 调用）。

| 维度 | 权重 | 满分 | 问题 |
|------|------|------|------|
| D1 Frontmatter 质量 | 8 | 8 | header/trigger/io/aux 字段完整性 |
| D2 工作流清晰度 | 10 | 10 | 步骤是否可执行、有序 |
| D3 边界条件 | 8 | 8 | 正反例、排除、误触发控制 |
| D4 检查点 | 8 | 8 | 阶段验收、ask_user、git checkpoint |
| D5 指令具体性 | 12 | 12 | 精确工具调用 vs 泛泛而谈 |
| D6 资源整合 | 8 | 8 | 上下游引用、路径、工具链 |
| D7 架构干跑 | 1.5×raw | 最高15 | LLM: 步骤是否自洽可执行 |
| D8 测试执行 | 1.5×raw | 最高15 | LLM: 是否有真实测试证据 |
| 基础分 | - | 2.5 | 存在即得分 |
| **总计** | - | **~100** | |

建议证据表：

```text
目标资产：memory/xxx.md
基线分：D1/D2/D3/D4/D5/D6/D7/D8 = .../100
修改后分：D1/D2/D3/D4/D5/D6/D7/D8 = .../100
变化：+N.N / 0 / -N.N
实测 prompt：有/无，路径或内容摘要
结果日志：results.tsv 行摘要或本轮报告
棘轮判断：保留/回滚/仅记录候选
```

对于高频 Skill/SOP，优先建立 2-5 条 `test-prompts` 风格的验收问题；对于规则/约束，优先建立正例、反例和误触发样例。

## 5. 标准流程

### Phase 0：授权与资产界定

确认：

- 用户是否要求修改，还是只读评估；
- 目标资产是什么；
- 是否涉及 R3 风险；
- 是否需要先读上游 SOP。

若边界不清，先问用户。

### Phase 1：只读基线

执行：

- file_read 目标资产；
- 查找相关 L1/L2/L3 索引；
- 检查 git status；
- 收集失败证据或误触发证据；
- **100 分引擎基线评估**（D1-D6 静态 + D7-D8 LLM 效果）：

```python
# Phase 1 标准基线评估
import sys
sys.path.insert(0, r"D:\AI\GenericAgent")
sys.path.insert(0, r"D:\AI\GenericAgent\memory")
from pathlib import Path
from darwin_eval_engine import evaluate_asset, evaluate_d7_d8_llm, _apply_llm_scores

target = Path(r"D:\AI\GenericAgent\memory\target_sop.md")
text = target.read_text(encoding="utf-8")
result = evaluate_asset(target)                    # D1-D6: 60分
llm = evaluate_d7_d8_llm(text, "target_sop")      # D7-D8: 40分
_apply_llm_scores(result, llm)                     # → total_score/100
baseline = result["total_score"]
```

- 若是高频 Skill/SOP，补充或引用 2-5 条 `test-prompts` 风格的验收问题；若是规则/约束，补充正例、反例、误触发或漏触发样例。

只读阶段不得修改文件。

### Phase 2：候选改进

只提出 1-3 个候选，按收益/风险排序。

每个候选必须包含：

```text
问题证据
最小改法
影响范围
验证方式
回滚方式
是否需要用户确认
```

### Phase 3：最小 patch

执行一个候选，且只改必要范围。

原则：

- 小 patch 优先；
- 不重排全文；
- 不删除已验证信息；
- 不写入临时状态；
- 不把计划当事实写入；
- L1 只写极简索引，不写 how-to。

### Phase 4：验证与棘轮

至少验证：

- 文件存在；
- 结构头字段完整；
- 关键章节存在；
- markdown 结构未明显破坏；
- git diff 可读；
- L1 同步符合极简原则；
- 必要时运行脚本或规则回归；
- 若本轮建立了测试 prompt，至少用 1 条代表性 prompt 做前后对比或人工判定。
- **100 分引擎重评**：用 `darwin_eval_engine.py` 对修改后的资产重新评估，确保总分不低于基线（棘轮：只升不降）。

```python
# 重评验证（Phase 4 标准操作）
import sys
sys.path.insert(0, r"D:\AI\GenericAgent")
sys.path.insert(0, r"D:\AI\GenericAgent\memory")
from pathlib import Path
from darwin_eval_engine import evaluate_asset, evaluate_d7_d8_llm, _apply_llm_scores

result = evaluate_asset(Path(r"D:\AI\GenericAgent\memory\target_sop.md"))
llm = evaluate_d7_d8_llm(text, "target_sop")
_apply_llm_scores(result, llm)
print(f"Total: {result['total_score']:.1f}/100")  # 必须 >= 基线分
```

棘轮记录建议写入本轮报告；若后续需要长期追踪，可在隔离实验区或项目目录维护 `results.tsv`，字段建议：

```text
date	asset	mode	baseline_score_100	new_score_100	change	decision	evidence	commit
```

`mode` 可取：

```text
dry_run      只读评分和候选建议
patch        单资产最小修改
full_test    带测试 prompt 的前后对比
rollback     验证失败后回滚
```

决策：

```text
验证通过且改进明确 → 保留
验证失败或收益不明 → 回滚/不提交
影响超出预期 → 停止并请求用户确认
```

对 Skill/SOP 的体积棘轮：修改后正文不应超过基线的 150%，除非用户明确要求扩展；超过时必须说明新增内容为何不可压缩。

### Phase 5：提交与记录

memory 资产变更完成后：

```text
git add -A
git commit -m "add/update/fix/refactor: ..."
```

提交前检查 git diff，提交后记录 commit 摘要。

## 6. 规则与约束维护特别要求

规则/约束变更必须有证据，禁止凭感觉修改。

误触发案例记录格式：

```text
规则 ID：
触发文本：
真实任务：
为什么是误触发：
最小修正建议：
回归样例：
```

漏触发案例记录格式：

```text
应触发规则：
未拦截行为：
造成风险：
最小新增条件：
回归样例：
```

修正规则时必须同时保留正例与反例，避免只修误触发却制造漏触发。

## 7. L1/L2/L3 维护特别要求

- L1 只保留极简索引和高频触发词；
- 新增 L3 SOP 后，只在 L1 低频列表增加文件名或极短触发词；
- 不把 SOP 正文搬进 L1；
- 修改 L2/L3 若不影响定位，默认不动 L1；
- 删除或迁移已验证信息前必须确认不丢失可追溯性；
- 写入前遵守 memory_management_sop。

## 8. 输出模板

### 8.1 只读评估输出

```md
# Darwin Maintenance 只读评估

目标资产：
证据来源：
当前问题：
8 维评分：
候选改进：
风险：
建议：
是否需要用户确认：
```

### 8.2 修改后输出

```md
# Darwin Maintenance 修改报告

目标资产：
改动摘要：
基线问题：
验证结果：
棘轮判断：
影响范围：
是否同步 L1：
git commit：
后续建议：
```

## 9. 使用边界

适用：新增/大修 SOP 时补结构头、边界、验收、失败路径；针对有证据的规则误触发/漏触发输出只读维护建议。

排除：自动批量优化全部 SOP；自动删除或合并大批记忆；无用户确认地修改全局约束；无回归样例地改规则。

## 10. 最小验收清单

```text
[ ] 已确认目标资产和授权范围
[ ] 已读取相关 SOP/索引
[ ] 已完成只读基线
[ ] 一次只改一个主资产
[ ] patch 最小且可回滚
[ ] 已验证结构、索引、diff
[ ] 涉及高风险变更时已请求用户确认
[ ] memory 变更已 git commit
```