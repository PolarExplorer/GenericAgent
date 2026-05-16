# Login Read Distill SOP

## Struct Header
- Reader: GA 总控
- When to read: 需要借登录态读取平台内容并用 AI 蒸馏理解后起草回复/评论时
- Trigger: 围观热榜/论坛/动态、起草评论/回复、舒情分析、内容摘要等
- Inputs: 目标平台 URL、已登录 tab、任务描述（读/读+写）
- Outputs: 蒸馏摘要 + 起草回复（可选）
- Tools: authenticated_fetch_skill(K1), llm_json_distill_skill(K2), web_execute_js
- Side effects: 网络请求（读）；可能写入评论（写）
- Risk: R1(只读) / R3(写入评论不可撤回)
- Failure path: 登录态失效→引导重新登录；蒸馏质量差→重试+调整 prompt；评论发送失败→检查频率限制/内容审核
- Review: 写入操作前必须展示草稿给用户确认

## 核心流程: 读 → 蒸馏 → [写]

### Phase 1: 读取
1. 确认 tab 已登录（检查用户头像/昵称元素）
2. 导航到目标页面（热榜/论坛/动态流）
3. 用 K1 (authenticated_fetch_skill) 拉取结构化数据
4. 备选: 直接 DOM 抽取（无 API 时）

### Phase 2: 蒸馏
1. 用 K2 (llm_json_distill_skill) 将原始内容蒸馏为结构化摘要
2. 必含 evidence 字段指向原文
3. 向用户展示蒸馏结果

### Phase 3: 写入（可选，人在回路）
1. 基于蒸馏结果起草回复/评论
2. **展示草稿给用户确认**，禁止自动发送
3. 用户确认后执行写入（GUI 操作或 API）
4. 写入后验证成功

## 关键约束

1. **三层能力递进**: 只读(R1) → 读+蒸馏(R1) → 读+蒸馏+写(R3)，根据任务选择最小必要层级
2. **评论礼貌原则**: AI 起草的评论必须礼貌、有实质内容、禁止广告/引流
3. **频率控制**: 写入操作间隔 ≥30秒，避免触发反垃圾机制
4. **口径一致**: 蒸馏和回复必须基于同一份原始数据

## 常见坑

- 部分平台评论需要 csrf token → 从 cookie/页面中提取
- 评论内容触发敏感词审核 → 用 AI 检查一遍
- 热榜数据快速变化 → 读取和写入之间间隔不宜太长
