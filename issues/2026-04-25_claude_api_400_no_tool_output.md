# Issue: Claude API 400 "No tool output found for function call"

- 日期: 2026-04-25
- 文件: llmcore.py
- 状态: ✅ 已修复 (待实际运行验证)

## 症状

长对话中 Claude API 返回 400 错误：
```
"No tool output found for function call tooluse_xxx"
```
即 history 中存在 assistant 的 `tool_use` block，但后续 user 消息中没有对应 `tool_use_id` 的 `tool_result`。

## 根因

调用链：`agent_loop` → `NativeToolClient.chat()` → `NativeClaudeSession.ask()` → `trim_messages_history()` → `_fix_messages()` → Claude API

`trim_messages_history()` 中的 orphan cleanup 逻辑 (原 L89-108) 存在 bug：

当 assistant 消息的 `tool_use` IDs 与下一条 user 消息的 `tool_result` IDs 没有交集时，只 `pop` 删掉了 assistant 消息，但保留了后续 user 消息中引用已删除 `tool_use_id` 的 `tool_result` blocks。

这些孤立的 `tool_result` blocks 随后可能被 `_fix_messages()` 的同 role 合并逻辑 (L591-592) 合并到其他 user 消息中，导致最终发给 API 的 history 中出现引用不存在 `tool_use_id` 的 `tool_result`。

`_fix_messages()` 原有逻辑只做正向补全（为 assistant 的 tool_use 补缺失的 tool_result），不做反向清理（删除 user 中引用不存在 tool_use 的 tool_result），所以无法兜底。

## 修复内容

### 修复1: trim_messages_history orphan cleanup (L106-113)

删除孤立 assistant 时，同时清理后续 user 消息中引用被删 `tool_use_id` 的 `tool_result` blocks：

```python
if not result_ids.intersection(use_ids):
    history.pop(i)
    # Also strip orphaned tool_result blocks from the now-exposed user msg
    if i < len(history) and history[i].get('role') == 'user':
        uc = history[i].get('content', [])
        if isinstance(uc, list):
            cleaned = [b for b in uc if not (isinstance(b, dict) and b.get('type') == 'tool_result' and b.get('tool_use_id') in use_ids)]
            if cleaned != uc:
                history[i] = {**history[i], 'content': cleaned if cleaned else [{"type": "text", "text": "(trimmed)"}]}
    continue
```

### 修复2: _fix_messages 反向清理 (L598-603)

作为第二道安全网，删除 user 消息中引用不存在于前一条 assistant 的 `tool_use_id` 的 `tool_result` blocks：

```python
# Reverse: strip tool_result blocks referencing non-existent tool_use ids
use_set = set(uses)
orphan_results = [b for b in _wrap(m['content']) if isinstance(b, dict) and b.get('type') == 'tool_result' and b.get('tool_use_id') not in use_set]
if orphan_results:
    cleaned = [b for b in _wrap(m['content']) if not (isinstance(b, dict) and b.get('type') == 'tool_result' and b.get('tool_use_id') not in use_set)]
    m = {**m, 'content': cleaned if cleaned else [{"type": "text", "text": "(trimmed)"}]}
```

## 验证

- AST 语法检查: ✅ 通过
- 单元测试 (3个): ✅ 全部通过
  - Test1: _fix_messages 反向清理孤立 tool_result
  - Test2: _fix_messages 正向补全缺失 tool_result (回归)
  - Test3: trim_messages_history orphan cleanup 不留孤立引用

## 复现条件

长对话 → context 超限触发 trim → trim 从头部删除消息 → orphan cleanup 删掉 assistant 但留下 user 中的孤立 tool_result → API 400