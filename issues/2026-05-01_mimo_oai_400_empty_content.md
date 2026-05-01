# Issue: OAI兼容API 400 "must have non-empty content or tool_calls"

- 日期: 2026-05-01
- 文件: llmcore.py
- 状态: ✅ 已修复 (待实际运行验证)

## 症状

使用 mimo-v2.5-pro (OpenAI兼容格式) 时，第11轮对话突然停止，后续请求报400错误：
```
HTTP 400: {"error":{"message":"messages[60] : assistant message must have non-empty content, reasoning_content or tool_calls","type":""}}
```

## 根因

调用链：`chat()` → `_msgs_claude2oai()` → 构建 OAI 格式 assistant 消息

`_msgs_claude2oai()` L700 处，当 assistant 消息没有 text_parts 时，content 被设为空字符串 `""`。

当该 assistant 消息同时携带 tool_calls 时，OAI兼容API要求 content 为 `null`（而非空字符串），否则返回400。

具体场景：assistant 纯 tool_use 消息（无文本输出，只有工具调用）→ text_parts 为空 → content="" → 与 tool_calls 共存 → API 拒绝。

## 修复内容

### _msgs_claude2oai() L697-702

原代码：
```python
content = "\n\n".join(text_parts) if text_parts else ""
```

修复后：
```python
if text_parts:
    content = "\n\n".join(text_parts)
elif tool_calls:
    content = None  # OAI spec: assistant with tool_calls allows null content
else:
    content = "(empty)"  # fallback placeholder to avoid empty content without tool_calls
```

## 验证

- 代码 patch 确认: ✅ 修改后逻辑正确
- AST 语法检查: 待验证（下次运行时自动验证）

## 影响范围

所有通过 `_msgs_claude2oai()` 转换的 OAI 兼容模型（mimo系列、deepseek等），当 assistant 消息为纯 tool_use（无文本）时均可能触发。Claude 原生 API 不受影响（走不同路径）。

## 复现条件

多轮对话 → assistant 发出纯 tool_use 消息（无文本内容） → 后续轮次构建 history → content="" 与 tool_calls 共存 → API 400