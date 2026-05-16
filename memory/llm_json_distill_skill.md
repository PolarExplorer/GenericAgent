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
- Failure path: 输出格式错误→重试+加强 schema 提示；输入超长→分块蒸馏再合并
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

## 常见坑

- 输入超过 context window → 先按源分块蒸馏出子 JSON，再最终合并一次
- LLM 可能在 JSON 中混入自然语言解释 → prompt 中明确禁止额外文字
- 数字/日期类字段可能被 LLM 转为字符串 → schema 中明确标注类型
- 多源 concat 时加分隔符 `--- source: {platform} ---` 避免混淆
