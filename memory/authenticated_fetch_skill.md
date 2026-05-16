# Authenticated Fetch Skill

## Struct Header
- Reader: GA 总控 / subagent
- When to read: 需要在用户已登录的网站上拉取数据时
- Trigger: 任何需要借登录态调用网站 API 的场景（读取收藏、热榜、个人信息、订单等）
- Inputs: 目标 URL/API 端点、已登录的浏览器 tab
- Outputs: JSON/文本数据
- Tools: web_execute_js (fetch + credentials:'include'), web_scan
- Side effects: 网络请求（只读），可能触发网站频率限制
- Risk: R1(只读) / R2(写入操作)
- Schedule: 1.确认 tab 已登录 → 2.构造 fetch → 3.分页拉取 → 4.返回结果
- Failure path: 401/403→cookie过期→引导用户重新登录；频率限制→加 delay；跨域→先导航到目标域再 fetch
- Review: None

## 核心模式

```javascript
// 在已登录 tab 内执行，cookie 自动携带
const resp = await fetch(apiUrl, {
  credentials: 'include',
  headers: { 'Accept': 'application/json' }
});
return await resp.json();
```

## 关键约束

1. **同域原则**: fetch 必须在目标网站的 tab 内执行，否则 cookie 不携带。跨域时先 `location.href` 导航到目标域
2. **分页处理**: API 返回分页时，循环拉取并 concat，注意频率控制（每页间隔 300-500ms）
3. **await + return**: web_execute_js 中用 await 时必须显式 return（tmwebdriver 特性）
4. **响应检查**: 先检查 `resp.ok`，非 200 时返回错误信息而非崩溃
5. **URLSearchParams**: POST 请求体用 `new URLSearchParams()` 构造，禁止手工拼接

## 常见坑

- B站 API 需要 `csrf` token，从 cookie 中的 `bili_jct` 字段获取
- 部分网站 API 需要额外 header（如 X-Requested-With）
- fetch 返回的数据可能被截断（单次返回量限制），需根据 total/has_more 判断是否继续
- 部分平台有 wbi 签名（B站），但大多数业务 API 可以绕过
