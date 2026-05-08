# Bloom Learning Objective Perspective Skill

## Struct Header
- Reader: GA 总控 / subagent / supervisor
- When to read: 需要把知识点、题目、练习、错题反馈按认知层级分层，或检查训练系统是否只停留在背诵层时。
- Trigger: Bloom taxonomy / 布鲁姆分类 / 学习目标分层 / 认知层级 / 题目难度结构 / 记忆理解应用分析评价创造。
- Inputs: 知识点列表、题目、参考答案、错题记录、训练目标、考试场景、目标用户阶段。
- Outputs: 认知层级标注、题型/任务改写建议、层级覆盖表、题目质量风险、下一步训练建议。
- Tools: persona_distill_sop, skill_struct_header_sop, exam_source_inventory.md, verify_sop.
- Side effects: 可能要求拆分题目、补充更高阶任务或降低不适配任务难度。
- Risk: 把 Bloom 层级机械套用；把考试难度等同于认知层级；忽略具体学科和题型证据。
- Failure path: 若题目内容或评分点不清，先读取考试 workflow/原文；若层级无法判定，输出“待题干/评分点核验”。
- Review: 高影响题库调整前，至少检查“题干动作动词、证据要求、答案产物、评分标准”是否一致。

## Anti-Personification Statement

本 Skill 不是“扮演 Bloom 本人”，也不声称 Bloom 会如何评价某道题；它只抽取 Bloom Taxonomy 相关教育测量视角，用于训练目标与题目认知层级检查。当前 MVP 未逐条绑定原始论文或修订版权威资料链接，使用时必须保留证据边界。

## Evidence Boundary

- Evidence Level: C/MVP。
- 已验证依据：本文件按 `persona_distill_sop` 与 `skill_struct_header_sop` 的结构要求写成，并面向软考/行测训练系统任务落地。
- 待验证依据：Bloom 原始 taxonomy、Anderson/Krathwohl 修订版、教育测量资料尚未逐条绑定原文链接。
- 禁止写法：不得写“Bloom 原文要求此题必须……”或把本 skill 的判断当作官方考试难度。

## Core Lens

1. **先问学习目标**：这道题到底想训练记忆、理解、应用、分析、评价还是创造？
2. **看产物而不是看词**：题干写“分析”不代表真是分析题，要看答案是否需要比较、拆解、证据链。
3. **区分难度与层级**：一道记忆题也可能很难，一道应用题也可能很基础。
4. **层级要服务训练路径**：错题复习先补低阶缺口，再安排高阶迁移。
5. **题目与反馈对齐**：如果题目要求应用，反馈不能只给定义；如果题目要求记忆，反馈不应强行扩展到系统设计。
6. **考试场景优先**：软考大题要回收评分点，行测选择题要回收解题路径与干扰项机制。

## Cognitive Level Checklist

### A. Remember / 记忆

- 任务表现：识别、列举、背出定义、记住公式或术语。
- 软考例子：列出质量属性名称、记住某概念定义关键词。
- 行测例子：记住常识、公式、成语含义。
- 风险：只背词，不知道适用条件。

### B. Understand / 理解

- 任务表现：解释、举例、分类、比较相近概念。
- 软考例子：说明可用性与可靠性的区别，解释某架构风格适用场景。
- 行测例子：解释为什么某词语更符合语境。
- 风险：解释看似通顺但没有边界或反例。

### C. Apply / 应用

- 任务表现：把规则、公式、方法用于新题或具体案例。
- 软考例子：给系统场景选择合适的质量属性改进策略。
- 行测例子：按资料分析公式计算、按逻辑规则排除选项。
- 风险：只会复述方法，不会迁移到新题。

### D. Analyze / 分析

- 任务表现：拆解结构、比较方案、识别因果链、定位漏洞。
- 软考例子：拆解架构评估案例中的风险、权衡、冲突质量属性。
- 行测例子：判断推理中拆出条件关系、干扰项设置与必要条件。
- 风险：把“多写几句”误当分析。

### E. Evaluate / 评价

- 任务表现：基于标准评价方案优劣、选择并说明理由。
- 软考例子：评价某架构方案是否满足性能/可用性目标并说明取舍。
- 行测例子：在多个解题路径中选择更稳妥路径并解释依据。
- 风险：评价标准不清，变成主观偏好。

### F. Create / 创造

- 任务表现：设计方案、生成题目、构建复习计划或迁移框架。
- 软考例子：根据业务约束设计质量属性改进方案或论文论证结构。
- 行测例子：设计一套同类题训练变式或总结可迁移模型。
- 风险：脱离考试要求，生成华丽但不可评分的产物。

## Task Classification Procedure

1. **抽取题干动作**：列出题目要求做什么：列举、解释、计算、选择、比较、设计、评价。
2. **抽取答案证据**：判断答案必须包含定义、步骤、计算、理由、权衡、方案还是反例。
3. **识别最低充分层级**：能答对该题所需的最低认知层级。
4. **识别训练目标层级**：出题者希望训练到的层级，可高于最低层级。
5. **检查反馈对齐**：批改反馈是否对应层级；例如分析题反馈要指出结构缺口，不能只补概念。
6. **给出改写建议**：若层级与目标不一致，改题干、评分点或反馈方式。

## Soft Exam Usage

### 软考题库分层

输出格式：

```text
【题目目标】
本题主要训练：{记忆/理解/应用/分析/评价/创造}

【判断依据】
- 题干动作：{...}
- 答案产物：{...}
- 评分点要求：{...}

【层级风险】
- 是否把背诵题伪装成分析题：{是/否 + 原因}
- 是否缺少应用场景：{是/否 + 原因}
- 是否反馈层级不足：{是/否 + 原因}

【改写建议】
如果要提升一层，可以把题目改为：{...}
如果要降低一层，可以把题目改为：{...}
```

### 行测题型分层

- 言语理解：通常落在理解/应用；若要求比较干扰项机制，可到分析。
- 数量关系：通常是应用；若总结多解法与适用条件，可到分析/评价。
- 判断推理：常在应用/分析之间；关键看是否需要拆条件链。
- 资料分析：基础计算是应用；估算策略选择可到评价。
- 常识判断：多为记忆/理解；不应伪装成高阶推理。

## Training System Checks

1. **知识卡片**：是否只写定义，缺例子、反例和适用场景？若是，只到记忆/浅理解。
2. **练习生成**：每组题是否覆盖多个层级，而不是全是选择识别题？
3. **错题反馈**：是否指出错在记忆缺口、理解偏差、应用失误、分析链断裂或评价标准不明？
4. **复习计划**：低阶缺口先用主动回忆补齐，高阶缺口用变式迁移和对比题。
5. **大题批改**：参考答案若只列关键词，必须补“如何展开为得分句”的层级桥接。

## Decision Tree: Quick Classification

When you encounter a question/task/exercise, use this tree:

```
Q: Does the learner just need to recall a fact/definition?
  YES -> Level 1: Remember
  NO  -> Q: Does the learner need to explain in own words / give examples?
           YES -> Level 2: Understand
           NO  -> Q: Does the learner need to use a method/formula in a new situation?
                    YES -> Level 3: Apply
                    NO  -> Q: Does the learner need to break down, compare, find relationships?
                             YES -> Level 4: Analyze
                             NO  -> Q: Does the learner need to judge, evaluate, choose between options?
                                      YES -> Level 5: Evaluate
                                      NO  -> Level 6: Create (design something new)
```

### Quick Verb Mapping

| Level | Typical Verbs | Exam Signal Words |
|-------|--------------|-------------------|
| 1 Remember | list, define, name, recall | "what is", "which of the following" |
| 2 Understand | explain, summarize, classify | "describe", "in your own words" |
| 3 Apply | calculate, solve, demonstrate | "given X, find Y", "use...to..." |
| 4 Analyze | compare, contrast, distinguish | "what is the difference", "why does" |
| 5 Evaluate | justify, critique, recommend | "which is better", "do you agree" |
| 6 Create | design, propose, construct | "design a...", "how would you..." |

## Worked Examples

### Example 1: Classifying Exam Questions (Software Exam)

**Question**: "List the 5 phases of the software development lifecycle."
- **Classification**: Level 1 (Remember) - pure recall of a list
- **Training implication**: Use spaced retrieval, flashcards, mnemonics

**Question**: "Explain why the waterfall model is unsuitable for projects with unclear requirements."
- **Classification**: Level 4 (Analyze) - requires understanding waterfall's assumptions AND comparing with reality
- **Training implication**: Don't just memorize "waterfall is bad for unclear requirements" - practice articulating the causal chain (waterfall assumes stable requirements -> unclear requirements change -> rework cascades -> cost explodes)

**Question**: "Given the following project scenario, recommend an appropriate development methodology and justify your choice."
- **Classification**: Level 5 (Evaluate) - must weigh multiple options against criteria
- **Training implication**: Practice with varied scenarios; build a decision matrix; rehearse justification structure

### Example 2: Upgrading a Training Exercise

**Original exercise (Level 1)**: "What is the definition of coupling in software engineering?"

**Upgraded versions**:
- Level 2: "Explain coupling to a non-technical manager using an everyday analogy."
- Level 3: "Given this code snippet, identify the type of coupling between modules A and B."
- Level 4: "Compare the coupling in Design A vs Design B. Which has lower coupling and why?"
- Level 5: "This system has high coupling. Evaluate whether refactoring is worth the cost given the project constraints."
- Level 6: "Redesign this module interface to reduce coupling while maintaining functionality."

### Example 3: Civil Service Exam (Logic/Reasoning)

**Question**: "All A are B. Some B are C. Therefore: (options)"
- **Classification**: Level 3 (Apply) - applying syllogism rules to a new instance
- **Common mistake**: Students memorize rules (Level 1) but can't apply them under time pressure
- **Training fix**: Drill with varied structures, not just repeated rule recitation

## Diagnostic Checklist

When reviewing a training system or question bank:

1. **Level distribution**: Are 80%+ questions at Level 1-2? If yes, learners will plateau at recall without developing higher-order skills.
2. **Level mismatch**: Does the exam test Level 4-5 but training only practices Level 1-2? This is the #1 cause of "I studied hard but still failed."
3. **False elevation**: Is a question labeled "analysis" but actually just recall with extra words? (e.g., "Analyze the definition of X" is still Level 1)
4. **Missing scaffolding**: Are there Level 4-5 questions without Level 2-3 prerequisites? Learners need the ladder.

## Application Beyond Exams

This skill applies to any learning/training design:
- **Onboarding docs**: Are they all "here's what X is" (Level 1) or do they include "try doing X" (Level 3)?
- **Code reviews**: Feedback like "this violates SRP" is Level 1 recall. Better: "compare this design with alternative Y and explain tradeoffs" (Level 4-5).
- **Meeting summaries**: "We decided X" is Level 1. "We decided X because Y outweighed Z given constraint W" is Level 5.
- **Documentation**: API docs that only list endpoints (Level 1) vs. docs with decision guides for choosing between endpoints (Level 5).

## Call Template

```text
Invoke Bloom Learning Objective Perspective Skill:
- Context: {exam prep / training design / content review / onboarding / other}
- Material: {question / exercise / document / curriculum}
- Goal: {classify levels / upgrade difficulty / diagnose distribution / redesign}
Input: {...}
Requirements: classify each item by Bloom level, identify gaps, suggest upgrades where levels are too low for the target assessment.
```

## Minimal Acceptance

- Can classify any question/task into one of 6 Bloom levels with justification.
- Can identify level mismatch between training and assessment.
- Can upgrade a Level 1-2 item to Level 3-5 while preserving the topic.
- Can diagnose a question bank's level distribution and flag imbalances.
- Handles non-exam scenarios (docs, onboarding, code review).
- Does not over-classify (avoids calling simple recall "analysis" just because the word appears).
