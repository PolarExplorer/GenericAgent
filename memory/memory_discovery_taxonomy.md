# memory_discovery_taxonomy.md — 记忆发现分类体系

> 定位：L3 参考分类。对记忆系统改造中发现的问题和改进进行标准化归类。  
> 来源：arXiv:2604.01007 OMNI-SIMPLEMEM 6 类发现 + GA 实践经验。  
> 用途：填写 `memory_experiment_log.md` 的"改动类型"字段时引用此表。

## 六大类别

### 1. `ingestion` — 写入/摄入问题
信息进入记忆时的过滤、格式化、去重、选择性写入相关。

**典型表现**：
- 冗余信息反复写入（噪声膨胀）
- 高波动性信息未经门控直接落盘
- 写入粒度不当（过粗丢细节 / 过细难检索）
- 多模态信号未统一表征

**GA 已有对策**：typed_memory_sop 分类写入、memory_volatility_sop 波动性门控

---

### 2. `retrieval` — 检索/召回问题
从记忆中查找相关信息时的精度、覆盖率、延迟相关。

**典型表现**：
- 查到了但不相关（精度低）
- 相关信息存在但查不到（召回低）
- 检索路径单一（仅关键词 / 仅向量）
- 时间衰减未考虑（旧信息权重过高）

**GA 已有对策**：skill_search、ga_mem_find、tool_selector embedding 粗筛

---

### 3. `consolidation` — 整合/压缩问题
记忆随时间累积后的合并、摘要、层级化相关。

**典型表现**：
- L1 索引膨胀，不再"极简"
- L2 条目重复或互相矛盾
- 跨文件信息碎片化，缺乏交叉引用
- 旧信息未清理（记忆腐烂）

**GA 已有对策**：memory_cleanup_sop、memory_conflict_scan.py、mem_distill

---

### 4. `format` — 格式/元数据问题
记忆存储的结构、模板、元数据字段相关。

**典型表现**：
- 缺少时间戳导致无法判断新鲜度
- 缺少来源标注导致无法溯源
- 格式不统一导致解析困难
- 元数据字段定义不一致

**GA 已有对策**：typed_memory_sop 模板、memory_volatility_sop 时间戳要求

---

### 5. `pipeline` — 流程/管线问题
记忆系统的端到端流程、自动化、门控机制相关。

**典型表现**：
- 写入前缺少验证环节
- 改动后无回归测试
- 手动操作多、自动化不足
- 缺少实验轨迹记录

**GA 已有对策**：script_health_check.py、meta_check.py、本实验轨迹表

---

### 6. `infra` — 基础设施问题
工具、脚本、约束引擎、SOP 本身的可用性和完备性。

**典型表现**：
- SOP 存在但无人遵守（约束未硬编码）
- 工具脚本报错或环境依赖缺失
- 新增 SOP 未索引到 L1
- 监控/告警缺失

**GA 已有对策**：rules_engine.md 约束引擎、script_guard、ga_watchdog

---

## 使用方式

1. 在 `memory_experiment_log.md` 中填写"类型"列时，使用上述 6 个标签之一：
   `ingestion` / `retrieval` / `consolidation` / `format` / `pipeline` / `infra`
2. 写入 L1/L2/L3 前，也先用上述 6 类判断候选记忆对应的问题/改动主类；无法归类的候选，先不升层，放 working/project_board/L4 继续观察。
3. 若一次改动涉及多类，用主类标记，备注列注明次类。