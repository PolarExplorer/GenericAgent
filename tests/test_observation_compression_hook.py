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



def test_p16_candidate_evaluator_keeps_replacement_disabled_for_long_logs():
    content = "\n".join(["install noise line %03d" % i for i in range(180)]) + "\nstatus=success\nexit_code=0"

    candidate = hook.build_compression_candidate_evaluation(content, tool_name="code_run", source_ref="test")

    assert candidate["schema"] == "ga.observation_compression_candidate.v1"
    assert candidate["shadow_only"] is True
    assert candidate["returned_observation_identity"] == "unchanged_by_contract"
    assert candidate["allowlisted_tool"] is True
    assert candidate["hard_deny"] is False
    assert candidate["loss_class"] == "boilerplate-only"
    assert candidate["gate"]["decision_equivalent"] is True
    assert candidate["gate"]["eligible_for_future_replacement"] is False
    assert candidate["candidate_chars"] < len(content)


def test_p16_candidate_evaluator_denies_secrets_user_choices_and_code_review(tmp_path, monkeypatch):
    secret_content = "status=failed\napi_key=abcdef123456\nTraceback: boom"
    secret_candidate = hook.build_compression_candidate_evaluation(secret_content, tool_name="code_run")
    assert secret_candidate["hard_deny"] is True
    assert "secret_raw" in secret_candidate["deny_reasons"]
    assert secret_candidate["gate"]["sensitive_safe"] is False
    assert "abcdef123456" not in json.dumps(secret_candidate, ensure_ascii=False)

    user_choice = "User confirmed: delete the production database"
    user_candidate = hook.build_compression_candidate_evaluation(user_choice, tool_name="ask_user")
    assert user_candidate["hard_deny"] is True
    assert "deny_tool" in user_candidate["deny_reasons"]
    assert user_candidate["gate"]["eligible_for_future_replacement"] is False

    review_content = "LLM1 code review verdict: PASS_WITH_RISK\n```diff\n@@ changed lines\n```"
    review_candidate = hook.build_compression_candidate_evaluation(review_content, tool_name="code_run")
    assert review_candidate["hard_deny"] is True
    assert "deny_marker:llm1" in review_candidate["deny_reasons"]
    assert "deny_marker:```diff" in review_candidate["deny_reasons"]
    assert review_candidate["gate"]["decision_equivalent"] is False


def test_p16_candidate_evaluator_covers_web_scan_and_secret_like_markers():
    content = "\n".join(["visible web row %03d status=success" % i for i in range(160)])

    web_candidate = hook.build_compression_candidate_evaluation(content, tool_name="web_scan", source_ref="test")
    assert web_candidate["allowlisted_tool"] is True
    assert web_candidate["hard_deny"] is False
    assert web_candidate["loss_class"] == "boilerplate-only"
    assert web_candidate["gate"]["eligible_for_future_replacement"] is False

    bearer_candidate = hook.build_compression_candidate_evaluation(
        "HTTP Authorization: Bearer abc.def.ghi123456789", tool_name="code_run"
    )
    assert bearer_candidate["hard_deny"] is True
    assert "deny_marker:bearer " in bearer_candidate["deny_reasons"]


def test_p16_candidate_preview_truncates_when_evidence_is_large():
    content = "status=success " + ("x" * 5000)

    candidate = hook.build_compression_candidate_evaluation(content, tool_name="code_run", source_ref="test")

    assert candidate["candidate_chars"] <= hook._MAX_CANDIDATE_CHARS
    assert candidate["candidate_preview"].endswith("...[truncated_candidate]")
    assert candidate["gate"]["traceable"] is True


def test_enabled_shadow_record_includes_p16_sidecar_without_mutation(tmp_path, monkeypatch):
    log_path = tmp_path / "shadow.jsonl"
    monkeypatch.setenv("GA_OBSERVATION_COMPRESSION_SHADOW", "1")
    monkeypatch.setenv("GA_OBSERVATION_COMPRESSION_LOG", str(log_path))
    content = "status=success\nexit_code=0\n" + "noise\n" * 200

    assert hook.observe_tool_result_content(content, tool_name="code_run", tool_use_id="p16") == content
    row = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    sidecar = row["compression_candidate"]
    assert sidecar["schema"] == "ga.observation_compression_candidate.v1"
    assert sidecar["gate"]["eligible_for_future_replacement"] is False
    assert row["returned_observation_identity"] == "unchanged_by_contract"
