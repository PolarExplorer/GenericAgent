# Multi-Source Profile SOP

## Struct Header
- Reader: GA 总控
- When to read: 需要从多个平台采集数据融合生成画像/报告时
- Trigger: 个人画像、竞品分析、品牌审计、学者档案、团队画像等
- Inputs: 目标主体（人/项目/品牌）、平台列表、输出格式要求
- Outputs: JSON 中间态 + MD 报告 + HTML 可视化页面
- Tools: authenticated_fetch_skill(K1), llm_json_distill_skill(K2), dual_format_render_skill(K3), web_access_sop
- Side effects: 多平台网络请求（只读）
- Risk: R1
- Failure path: 平台不可达→跳过并标注；数据不足→降级输出；蒸馏偏差→调整 prompt+重试
- Review: None

## 核心流程: 探测 → 采样 → 蒸馏 → 渲染

### Phase 1: 平台探测
1. 根据目标主体确定相关平台列表
2. 逐个检查可达性和登录态
3. 对不可达平台标注跳过原因

### Phase 2: 内容采样
1. 每个平台用 K1 拉取数据（个人主页/动态/作品等）
2. 保存为 `{platform}_content.txt`
3. 控制采样量（每平台最近 20-50 条）

### Phase 3: 融合蒸馏
1. 所有平台内容 concat（加分隔符）
2. 用 K2 一次性蒸馏为 JSON 中间态
3. JSON 必含: 综合画像 + 各平台子画像 + evidence

### Phase 4: 双格式渲染
1. 用 K3 从 JSON 生成 MD + HTML
2. 修改只改 JSON，重新渲染

## 关键约束

1. **单次蒸馏**: 多源数据必须一次性蒸馏，禁止分平台分别蒸馏再合并（口径不一致）
2. **JSON 为单一真相源**: 所有修改只发生在 JSON，禁止直接改 MD/HTML
3. **不可达平台不强求**: 部分平台数据缺失不影响整体画像，明确标注数据来源


**gate:** 画像采集前，用 `ask_user` 确认数据源列表和隐私边界。

**确认点:** 每完成一个数据源采集，汇报采集结果和数据质量。

**进度:** 最终画像合并前，展示各源数据覆盖率供用户确认。

## 常见坑

- 多平台采集耗时长 → 设合理超时，失败平台跳过而非重试
- 不同平台数据量差异大 → 采样时控制每平台上限，避免偏向数据多的平台
- concat 超过 token 限制 → 截断至 ~30k token，优先保留近期内容

## 相关资源
- 登录态 fetch：`memory/authenticated_fetch_skill.md`
- JSON 蒸馏：`memory/llm_json_distill_skill.md`
- 飞书 CLI：飞书 CLI（shell=True, user+bot 双身份）
