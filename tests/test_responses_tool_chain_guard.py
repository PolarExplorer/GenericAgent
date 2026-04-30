import importlib.util
import json
import pathlib
import py_compile


ROOT = pathlib.Path(__file__).resolve().parents[1]
LLMCORE = ROOT / "llmcore.py"


def load_llmcore():
    py_compile.compile(str(LLMCORE), doraise=True)
    spec = importlib.util.spec_from_file_location("llmcore_test", str(LLMCORE))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_responses_guard_converts_orphan_function_call_output():
    mod = load_llmcore()
    raw = [{"type": "function_call_output", "call_id": "missing_call", "output": "orphan payload"}]

    clean = mod._sanitize_responses_tool_chain(raw)

    assert clean[0]["role"] == "user"
    assert "missing_call" in clean[0]["content"][0]["text"]
    assert not mod._responses_tool_chain_orphans(clean)


def test_responses_guard_keeps_valid_function_call_pair():
    mod = load_llmcore()
    raw = [
        {"type": "function_call", "call_id": "call_ok", "name": "x", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call_ok", "output": "ok"},
    ]

    clean = mod._sanitize_responses_tool_chain(raw)

    assert clean == raw
    assert not mod._responses_tool_chain_orphans(clean)


def test_responses_guard_converts_dangling_function_call():
    mod = load_llmcore()
    raw = [{"type": "function_call", "call_id": "call_missing_output", "name": "ask_user", "arguments": "{}"}]

    clean = mod._sanitize_responses_tool_chain(raw)

    assert clean[0]["role"] == "user"
    assert "call_missing_output" in clean[0]["content"][0]["text"]
    assert not any(isinstance(item, dict) and item.get("type") == "function_call" for item in clean)
    assert not mod._responses_tool_chain_orphans(clean)


def test_to_responses_input_does_not_emit_orphan_tool_output():
    mod = load_llmcore()
    msgs = [{"role": "tool", "tool_call_id": "ghost", "content": "x"}]

    resp = mod._sanitize_responses_tool_chain(mod._to_responses_input(msgs))

    assert not any(
        isinstance(item, dict) and item.get("type") == "function_call_output"
        for item in resp
    )
    assert not mod._responses_tool_chain_orphans(resp)