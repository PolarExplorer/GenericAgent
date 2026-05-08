# Feynman Explanation Perspective Skill

## Struct Header
- Reader: GA 总控 / subagent / supervisor
- When to read: 需要审查任何知识解释是否真正让人理解时——包括但不限于概念解析、方法讲解、错题反馈、文档、README、技术博客、知识卡片。
- Trigger: 费曼解释 / Feynman explanation / 讲人话 / 概念解释审查 / 术语堆砌检查 / 最小例子反例 / 看不懂 / 太抽象 / 解释不清。
- Inputs: 待解释概念、目标读者水平、场景(考试/工程/科普/文档)、已有解释文本、题目或错题样本。
- Outputs: 问题重构、术语堆砌诊断、最小可理解解释、例子/反例、缺口清单、改写建议、验收检查。
- Tools: persona_distill_sop, skill_struct_header_sop, exam_source_inventory.md, verify_sop.
- Side effects: 可能压缩术语密度，要求补充原文依据或例子；不得替代考试官方口径。
- Risk: 把“讲得简单”误当成“内容完整”；过度类比导致考试评分点缺失。
- Failure path: 若解释涉及考试评分点，先调用 ruankao/xingce workflow 或读取原文；若证据不足，标注“待原文核验”。
- Review: 对高影响输出，至少检查一次“是否覆盖定义、边界、例子、反例、考试得分点”。

## Anti-Personification Statement

本 Skill 不是“扮演费曼”，不声称费曼本人会如何回答；它只抽取一种解释质量检查视角：把复杂概念拆到可验证、可举例、可复述的程度。当前 MVP 未逐条绑定费曼原文链接，使用时必须保留证据边界。

## Evidence Boundary

- Evidence Level: C/MVP。
- 已验证依据：本文件按 `persona_distill_sop` 与 `skill_struct_header_sop` 的结构要求写成，并与训练系统“软考/行测解释、出题、批改”任务绑定。
- 待验证依据：费曼本人演讲、访谈、教学材料、书籍中的具体原文尚未逐条绑定。
- 禁止写法：不得输出“费曼一定会说……”“按费曼原话……”这类未核验断言。

## Core Lens

1. **能否去掉术语外壳**：如果删除术语名，解释是否还能说清楚对象、作用、边界。
2. **能否让学习者复述**：目标读者是否能用自己的话复述，而不是背诵定义。
3. **能否举最小例子**：至少给出一个具体、可检查、与考试场景相关的例子。
4. **能否给反例/非例子**：说明什么情况不属于该概念，避免概念泛化。
5. **能否暴露缺口**：解释卡住的地方就是知识缺口，必须显式列出而不是用更大术语遮盖。
6. **能否回到评分点**：面向考试时，通俗解释之后必须回收为可得分关键词。

## Diagnostic Checklist

### A. 术语堆砌检查

- 是否连续出现多个抽象词但没有具体对象？
- 是否用同义词循环解释，例如“可用性就是系统可用的能力”？
- 是否只有定义，没有“为什么重要/怎么判断/怎么改善”？
- 是否把措施、指标、目标混在一起？
- 是否缺少考试题要求的关键词？

### B. 最小解释检查

一个合格解释至少包含：

1. 一句话定义：对象 + 目标 + 边界。
2. 为什么重要：它解决什么风险或问题。
3. 怎么判断：指标、表现或判据。
4. 怎么做：常见手段或策略。
5. 一个例子：最好贴近软考或行测题目。
6. 一个反例：防止误解。
7. 考试回收：可写进答案的关键词。

### C. 学习者复述检查

让解释通过三个问题：

- “如果让我给初学者讲，我会怎么说？”
- “如果出一道题考这个点，题干可能怎么绕？”
- “如果我答错，最可能漏掉哪个关键边界？”

## Soft Exam Usage

### 软考概念解释模板

输入：`概念 + 题型 + 目标读者 + 原解释`

输出格式：

```text
【概念重述】
一句话说明它是什么、解决什么问题。

【别混淆】
列出 1-3 个容易混淆对象。

【最小例子】
给一个系统架构/项目场景例子。

【反例】
给一个看似相关但不属于该概念的情况。

【考试得分点】
列出可写入答案的关键词；若未读原文，标注待核验。

【原解释问题】
指出术语堆砌、缺例子、缺边界或缺评分点。
```

### 示例：质量属性解释审查

若待解释是“性能、可用性、安全性、可维护性”：

- 性能：不要只写“响应快”，还要区分响应时间、吞吐量、资源利用率。
- 可用性：不要只写“系统能用”，还要涉及持续服务、故障恢复、冗余/故障转移。
- 安全性：不要只写“安全”，还要区分认证、授权、数据保护、审计。
- 可维护性：不要只写“容易维护”，还要落到模块化、低耦合、高内聚、接口清晰、测试定位。

注意：上述只是解释审查样例；正式评分仍需读取软考资料源或 workflow skill。

## Xingce Usage

行测讲解更强调“步骤可执行”：

- 言语理解：解释为什么某选项符合语义/逻辑，而不是只说“语感更顺”。
- 数量关系：列出变量、关系式、代入边界。
- 判断推理：说明规则、排除路径、干扰项误导点。
- 资料分析：列出定位数据、公式、估算过程、量级检查。
- 常识判断：标注知识来源可靠性；不确定时不编。

## Before / After Examples

### Example A: Engineering Concept — "Microservices Architecture"

**Before (jargon-heavy):**
> Microservices is an architectural style that structures an application as a collection of loosely-coupled, fine-grained services organized around business capabilities, communicating via lightweight mechanisms (e.g. HTTP/REST), supporting independent deployment, tech heterogeneity, and elastic scaling.

**Diagnosis:** 7 abstract terms in one sentence; "architectural style" explains "architecture" circularly; no contrast with alternative; reader cannot judge any boundary.

**After (Feynman-style):**
> Think of an online store: ordering, payment, inventory, recommendations are 4 different jobs.
>
> **Monolith approach**: all 4 in one program. Change the recommendation algorithm -> redeploy the whole store. Payment bug -> entire site down.
>
> **Microservices approach**: each job is a separate small program running independently. Change recommendations -> only redeploy that one service. Payment crashes -> others still work. Tradeoff: they must talk over the network (API calls), adding latency and ops complexity.
>
> **One sentence**: Split one big program into multiple independent small ones, each deployed/upgraded separately, communicating via APIs. Benefit: change one without breaking others. Cost: communication and operations get harder.

**What changed:** scenario first -> contrast with old approach -> explicit tradeoff -> one-sentence summary.

---

### Example B: Exam Concept — "Availability" (Software Quality Attribute)

**Before (textbook):**
> Availability is the ability of a system to accomplish its required function under stated conditions within a stated period, typically measured by MTBF/(MTBF+MTTR).

**Diagnosis:** Three "stated X" are abstract placeholders; MTBF/MTTR formula lacks intuition; confusion with "reliability".

**After:**
> **Availability = can you use the system when you need it?**
>
> It's not "will it break?" (that's reliability). It's "from when you want to use it to when you can, how long do you wait?"
>
> Metric: total uptime / total time. 99.9% means max 8.7 hours downtime per year.
>
> **Example**: ATM maintenance 2h at 3am, never down during day -> high availability (always usable when needed).
> **Counter-example**: Server breaks only once/year but takes 3 days to fix -> decent reliability but low availability.
>
> **Exam scoring keywords to recover**: MTBF, MTTR, redundancy, failover, graceful degradation.

**What changed:** one-sentence definition -> distinguish from similar concept -> numeric intuition -> example + counter-example -> recover exam keywords.

---

### Example C: Documentation — Project README

**Before:**
> This project is an LLM-based intelligent Agent framework with multi-layer memory architecture and constraint engine, supporting SOP-driven autonomous task execution and knowledge management.

**Diagnosis:** Reader can't tell what it actually does; "multi-layer memory", "constraint engine", "SOP-driven" are insider jargon.

**After:**
> An AI assistant framework that gets things done. It remembers what it learned before (memory system), follows your rules (constraint engine), and handles complex tasks step by step (SOPs).
>
> Example: you say "organize this paper into notes" -- it finds the paper, extracts key points, outputs in your preferred format, and asks you when uncertain.

**What changed:** removed all insider jargon -> plain function description -> concrete usage example.

## Generalization Notes

This skill is not limited to exam scenarios. Any "explanation someone can't understand" qualifies:
- Technical docs / API descriptions / error messages
- Product requirements / PRDs
- Meeting notes conclusions
- Knowledge base cards / FAQs
- Code comments and commit messages

Core test: **Can you still explain it after removing all jargon? Can the reader retell it in their own words?**

## Call Template

```text
Invoke Feynman Explanation Perspective Skill:
- Context: {exam / engineering doc / product / popularization / other}
- Target reader: {beginner / review / sprint / colleague / end-user / ...}
- Concept or question: {...}
- Original explanation: {...}
Requirements: identify jargon overload, missing examples, missing counter-examples; provide a more retellable version. For exams, recover scoring keywords after simplification.
```

## Minimal Acceptance

- Identifies at least 2 explanation defects.
- Provides a shorter, more concrete, retellable version.
- Provides at least 1 example or counter-example.
- For exam essays, reminds to recover scoring keywords after plain-language explanation.
- Handles non-exam scenarios (engineering docs, product descriptions, etc.).
- Does not impersonate Feynman or claim unverified source authority.
