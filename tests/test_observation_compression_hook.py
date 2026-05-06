import importlib
import json

import observation_compression_hook as hook


def test_disabled_helper_returns_original_and_does_not_log(tmp_path, monkeypatch):
    log_path = tmp_path / "shadow.jsonl"
    monkeypatch.delenv("GA_OBSERVATION_COMPRESSION_SHADOW", raising=False)
    monkeypatch.setenv("GA_OBSERVATION_COMPRESSION_LOG", str(log_path))

    content = "status=success\nsecret token=abcdef123456"

    assert hook.observe_tool_result_content(content, tool_name="code_run", tool_use_id="t1") == content
    assert not log_path.exists()


def test_enabled_helper_logs_redacted_shadow_record(tmp_path, monkeypatch):
    log_path = tmp_path / "shadow.jsonl"
    monkeypatch.setenv("GA_OBSERVATION_COMPRESSION_SHADOW", "1")
    monkeypatch.setenv("GA_OBSERVATION_COMPRESSION_LOG", str(log_path))

    content = "status=failed\napi_key=abcdef123456\nTraceback: boom"

    assert hook.observe_tool_result_content(content, tool_name="code_run", tool_use_id="t2") == content
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["schema"] == "ga.observation_shadow.v1"
    assert row["shadow_only"] is True
    assert row["returned_observation_identity"] == "unchanged_by_contract"
    assert row["tool"] == "code_run"
    assert row["tool_use_id"] == "t2"
    assert row["secret_detected_raw"] is True
    visible = json.dumps(row, ensure_ascii=False)
    assert "abcdef123456" not in visible
    assert "[REDACTED_SECRET]" in visible
    assert "failed" in row["risk_signals"]


def test_agent_loop_imports_default_off_hook(monkeypatch):
    monkeypatch.delenv("GA_OBSERVATION_COMPRESSION_SHADOW", raising=False)
    agent_loop = importlib.import_module("agent_loop")
    assert agent_loop.observe_tool_result_content("abc", tool_name="x", tool_use_id="y") == "abc"
