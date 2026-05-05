# Session Handoff SOP — 跨会话任务交接
> 来源：底座项目 6 次「继续任务」每次都要重新探测状态
> 适用：任何需要跨会话延续的长时间任务

## 触发条件
任务预计跨越多个会话，或单次会话无法完成。

## 流程

```
1. 运行时自动记录（嵌入 batch_runner.py）
   ├─ progress.json：实时进度（每 N 条更新）
   │   格式：{stage, step, done, total, last_item_id, updated_at}
   └─ error_log.jsonl：失败条目（item_id, error, timestamp）

2. 会话结束前（手动或自动触发）
   ├─ 生成 stage_state.json：
   │   {stage, status, validation_level, completed_steps, pending_steps,
   │    blockers, next_action, files_modified, timestamp}
   ├─ 生成交接摘要（≤20 行纯文本）：
   │   - 当前在哪个阶段哪个步骤
   │   - 已完成什么、还差什么
   │   - 下一步具体指令（可直接复制执行）
   │   - 阻塞项（如有）
   ├─ 生成验收边界表（集成/服务型项目必填）：
   │   - 能力 / 状态 / 证据 / 未完成边界
   │   - 状态枚举：PASS_LOCAL, PASS_DRY_RUN, PASS_SANDBOX,
   │     PASS_REAL_SMOKE, PASS_PRODUCTION, PARTIAL, BLOCKED_BY_USER_CONFIG
   │   - 明确区分“本地可用、dry-run 可用、真实小流量可用、生产可用”
   └─ 后台进程：若有在跑的任务，记录 PID + 命令行 + 预计完成时间

3. 新会话恢复
   ├─ 读 stage_state.json → 跳过已完成步骤
   ├─ 读 progress.json → 判断是否有在跑/中断的任务
   │   ├─ 进程仍在跑 → 只监控，不重启
   │   ├─ 进程已中断 → 从 checkpoint 恢复（batch_runner --resume）
   │   └─ 无进程 → 按 next_action 继续
   └─ 禁止：重新扫描全部日志来推断进度（浪费 token + 不可靠）
```

## state 文件位置约定
- 项目根目录 / `00_项目管理/stage_state.json`
- 项目根目录 / `00_项目管理/handoff_summary.txt`

## 典型坑
1. 只写了交接文档没写 state 文件 → 新会话仍要人工解读 → **state 文件是机器读的，交接摘要是人读的，两个都要**
2. 后台进程还在跑就重启了新一轮 → **先检查 PID 存活再决定动作**
3. checkpoint 文件被覆盖 → checkpoint 用追加模式，或带时间戳命名