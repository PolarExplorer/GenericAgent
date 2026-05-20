# Expert Skill Index

## Struct Header
- Reader: GA 总控 / subagent / supervisor
- When to read: 建设、维护知识底座与训练系统，或需要调用专家视角/考试工作流时。
- Trigger: 专家Skill / 知识底座维护 / 训练系统维护 / 软考出题 / 行测出题 / 复习批改质量检查。
- Inputs: 任务目标、学科范围、题目/知识点样本、验收标准。
- Outputs: 推荐调用的 persona skill / workflow skill、组合方式、验证清单。
- Tools: persona_distill_sop, skill_struct_header_sop, verify_sop.
- Safety: 真实人物只抽取公开可证据化方法，不拟人化代言；考试领域只做非人格化工作流，不虚构专家人格。

## Directory

```text
../memory/skills/
  expert_skill_index.md
  persona/
  workflow/
```

## Naming

- 真实人物视角：`<name>_perspective_skill.md`
- 考试领域工作流：`<domain>_exam_workflow_skill.md`

## Planned MVP Skills

### Persona perspective skills

1. `persona/feynman_perspective_skill.md` — 用于解释、概念澄清、发现伪理解。
2. `persona/bloom_perspective_skill.md` — 用于学习目标分层、测评层级设计。
3. `persona/bjork_perspective_skill.md` — 用于长期记忆、提取练习、间隔复习设计。

### Exam workflow skills

1. `workflow/ruankao_exam_workflow_skill.md` — 软考知识整理、题目生成、答案/解析检查。
2. `workflow/xingce_exam_workflow_skill.md` — 行测题型拆解、题目生成、解析与干扰项检查。

## Composition Rules

- 内容正确性优先调用考试 workflow skill。
- 解释质量优先叠加 Feynman perspective。
- 目标层级/题目能力层级优先叠加 Bloom perspective。
- 长期复习/错题再练优先叠加 Bjork perspective。
- 高影响输出必须至少经过一个 workflow skill 与一个验证清单。
## Workflow
1. **需求识别**: 用户提出知识/考试相关任务时，读取本文件获取 skill 推荐。
2. **Skill 选择**: 根据 Composition Rules 匹配 persona + workflow 组合。
3. **执行**: 调用推荐的 skill 完成任务（出题/解释/复习设计）。
4. **验证**: 按 Checkpoints 中的确认点逐项检查输出质量。
5. **验收**: 交付前确认 Safety 约束和 Acceptance 标准全部满足。



## Checkpoints

**gate:** 新建 persona/workflow skill 前，用 `ask_user` 确认：(1) 是否已有类似 skill；(2) 证据边界是否满足 Safety 约束。
**确认点:** 高影响输出（题库/训练数据）完成后，必须经过至少一个 workflow skill 的检查清单验证。
**确认点:** Skill 文件提交前，验证 Struct Header 六要素齐全（Reader/When/Trigger/Inputs/Outputs/Safety）。
**进度汇报:** 批量任务（出题/知识整理）每完成 1/3 时，用 `report_progress` 向用户汇报已完成数量和发现的问题。

## Acceptance

- 每个 skill 必须有 Struct Header。
- 真实人物 skill 必须包含：证据边界、反拟人化声明、适用/不适用范围、调用模板、最小验收。
- 考试 workflow skill 必须包含：资料边界、题型/知识点流程、出题检查、批改检查、反幻觉规则。
- MVP 允许先标注“未逐条绑定原文链接”，但不得写成已完成证据闭环。

## Boundary Conditions
- **未知学科**: 不在 Planned MVP Skills 范围内的学科，用 `ask_user` 确认是否新建 skill 还是借用已有。
- **多专家冲突**: 当 Composition Rules 推荐多个 persona 且优先级不明确时，用 `ask_user` 确认叠加顺序。
- **证据不足**: 真实人物 skill 无法找到公开证据化方法时，降级为通用工作流，不虚构专家人格。
- **超大批量**: 题目生成超过 100 道时，分批调用并用 progress_report 中间汇报。

## 相关资源
- 结构头模板：`memory/skill_struct_header_sop.md`
- 验证 SOP：`memory/verify_sop.md`
- 人格蒸馏：`memory/persona_distill_sop.md`
- 审计引擎：`temp/darwin_eval_engine.py`
- Persona 目录：`memory/skills/persona/`
- Workflow 目录：`memory/skills/workflow/`
