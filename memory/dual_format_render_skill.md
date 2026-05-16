# Dual Format Render Skill

## Struct Header
- Reader: GA 总控 / subagent
- When to read: 需要从同一份数据源生成多种格式输出时
- Trigger: 有结构化数据（JSON/dict），需要同时输出 MD 和 HTML（或其他格式）
- Inputs: JSON 中间态文件（单一真相源）
- Outputs: .md 文件 + .html 文件（或其他目标格式）
- Tools: Python (json + 字符串模板 / Jinja2 可选)
- Side effects: 磁盘写入
- Risk: R1
- Failure path: JSON schema 不符合预期→校验后报错；渲染丢字段→添加完整性检查
- Review: None

## 核心模式

```
JSON 中间态 (单一真相源)
    ├── render_md(json_data) → output.md
    └── render_html(json_data) → output.html

修改数据 → 只改 JSON，重新渲染
新增格式 → 只加一个 render_xxx() 分支
```

## 关键约束

1. **单一真相源**: 所有修改只发生在 JSON，禁止直接改 MD/HTML
2. **字段完整性**: 渲染后检查 JSON 中每个 key 都已出现在输出中
3. **编码安全**: HTML 渲染时对用户数据做 HTML escape
4. **可预览**: HTML 版本要可直接在浏览器打开，内联 CSS

## 典型应用

- 个人画像报告（Case 3）: JSON → MD摘要 + HTML可视化页面
- 竞品分析: JSON → MD报告 + HTML交互图表
- 学术画像: JSON → MD简历补充 + HTML展示页
- 数据报表: JSON → MD备忘录 + HTML邮件正文

## 常见坑

- 直接改 MD 而非 JSON 导致两版不一致 → 流程强制从 JSON 出发
- HTML 中未 escape 用户数据导致标签破坏 → 用 html.escape()
- Jinja2 模板中变量名拼错无报错 → 用 StrictUndefined
