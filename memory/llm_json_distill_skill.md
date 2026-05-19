# LLM JSON Distill Skill

## Struct Header
- Reader: GA 总控 / subagent
- When to read: 需要把大段非结构化文本蒸馏为结构化 JSON 时
- Trigger: 拿到原始文本/多源内容，需要提取、分类、摘要、画像等结构化输出
- Inputs: 原始文本（可多源 concat）、目标 schema 描述
- Outputs: 符合 schema 的 JSON 对象
- Tools: LLM 调用（GA 总控内部推理 / llmclient.chat）
- Side effects: token 消耗
- Risk: R1
- Failure path: JSON parse 失败→只修格式重试；证据不足→保留 unknown/other；输入超长→分块蒸馏再最终合并
- Acceptance: 可被 json.loads 解析；字段符合 schema；每个结论有 evidence；不确定性不被编造
- Boundary: 只负责从给定文本蒸馏结构，不负责外部事实核验或补充搜索
- Review: None

## 核心模式

```
输入: 多源原始文本 (concat, 截断至 ~30k token)
  ↓
LLM 一次性调用 (JSON-mode / 强制 JSON 输出)
  ↓
输出: 结构化 JSON，每个结论必含 evidence 字段
```

## 关键约束

1. **单次调用原则**: 所有多源内容 concat 后一次性喷给 LLM，禁止分多次调用（口径不一致）
2. **evidence 字段必填**: 每个结论/分类/标签都必须带 evidence 字段指向原文，确保可追溯
3. **JSON-mode**: 优先使用 LLM 的 JSON-mode；若不支持，在 prompt 中用 ```json ``` 围栏强制
4. **schema 先行**: prompt 中先给出目标 JSON schema 示例，再给原文
5. **模糊归“其他”**: 无法确定的条目归入 "other" 类别，宁可不分不可分错
6. **验证闭环**: 输出后必须先 parse JSON，再抽查 evidence 是否能在原文定位；失败只做最小重试

## 执行步骤

1. 明确目标 schema、字段类型、必填 evidence 与 unknown/other 规则
2. 按源添加 `--- source: {name} ---` 分隔符，必要时先分块蒸馏再合并
3. 调用 LLM JSON-mode 执行蒸馏，只允许返回 JSON 对象
4. 用 `json.loads` 解析结果，并抽查 evidence 是否能回指原文
5. 失败时只修复格式或最小补证据，不扩展到外部搜索

## 索引/路由

- L1/L2/L3 索引名：`llm_json_distill_skill`，用于“文本/网页/多源内容 → JSON”召回
- 常接在 `authenticated_fetch_skill` 之后，或接到 `dual_format_render_skill` 之前

## 常见坑

- 输入超过 context window → 先按源分块蒸馏出子 JSON，再最终合并一次
- LLM 可能在 JSON 中混入自然语言解释 → prompt 中明确禁止额外文字
- 数字/日期类字段可能被 LLM 转为字符串 → schema 中明确标注类型
- 多源 concat 时加分隔符 `--- source: {platform} ---` 避免混淆
