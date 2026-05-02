# Issue: Streamlit fragment rerun scope exception

- 日期: 2026-05-01
- 文件: `frontends/stapp.py`
- 状态: ✅ 已做最小修复，已通过 `py_compile`

## 症状

侧边栏切换备用链路时，Streamlit 抛出：

```text
streamlit.errors.StreamlitAPIException:
scope="fragment" can only be specified from `@st.fragment`-decorated functions during fragment reruns.
```

调用栈指向：

```text
frontends/stapp.py:46
agent.next_llm(selected_idx); st.rerun(scope="fragment")
```

## 根因

`render_sidebar()` 虽然有 `@st.fragment` 装饰，但 `st.rerun(scope="fragment")` 只能在 Streamlit 判定的 fragment rerun 周期内调用；在 full app run 或非 fragment-rerun 上下文中调用会直接抛异常。

该处用于切换 LLM 备用链路，不依赖局部 fragment rerun 语义；全量 rerun 更稳。

## 修复内容

将：

```python
agent.next_llm(selected_idx); st.rerun(scope="fragment")
```

改为：

```python
agent.next_llm(selected_idx); st.rerun()
```

## 验证

- `git diff -- frontends/stapp.py` 确认仅替换 rerun 调用参数。
- `py_compile` 通过：

```text
py_compile ok D:\AI\GenericAgent\frontends\stapp.py
```

## 影响范围

只影响 Streamlit 前端侧边栏的备用链路切换刷新方式。功能语义保持为：切换模型后重新运行应用以刷新状态。