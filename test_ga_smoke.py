"""
ga.py smoke test - 防止改完 ga.py 漏跑集成验证 (REG-R040)

每次修改 ga.py 后必须跑:
    pytest test_ga_smoke.py -v
"""
import os
import sys
import inspect

import pytest

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


@pytest.fixture(scope="module")
def ga_module():
    import ga
    return ga


@pytest.fixture(scope="module")
def handler_cls(ga_module):
    return ga_module.GenericAgentHandler


def test_ga_imports(ga_module):
    assert ga_module is not None


def test_handler_class_exists(handler_cls):
    assert handler_cls is not None
    assert handler_cls.__name__ == "GenericAgentHandler"


@pytest.mark.parametrize("method_name", [
    "do_code_run",
    "do_file_read",
    "do_file_write",
    "do_file_patch",
    "turn_end_callback",
    "__init__",
])
def test_core_methods_exist(handler_cls, method_name):
    assert hasattr(handler_cls, method_name), f"Missing method: {method_name}"


# ---------- CodingGate integration regression ----------

def test_coding_gate_imported():
    src = open(os.path.join(_ROOT, "ga.py"), encoding="utf-8").read()
    assert "from ga_coding_gate import CodingGate" in src


def test_coding_gate_initialized(handler_cls):
    src = inspect.getsource(handler_cls.__init__)
    assert "CodingGate(" in src


def test_check_coding_gate_helper(handler_cls):
    assert hasattr(handler_cls, "_check_coding_gate")


@pytest.mark.parametrize("method_name", [
    "do_code_run",
    "do_file_patch",
    "do_file_write",
])
def test_write_tools_gated(handler_cls, method_name):
    src = inspect.getsource(getattr(handler_cls, method_name))
    assert "_check_coding_gate" in src, f"{method_name} missing CodingGate check"


def test_turn_end_audits_coding_gate(handler_cls):
    src = inspect.getsource(handler_cls.turn_end_callback)
    assert "_coding_gate" in src


# ---------- file_read governance regression ----------

def test_file_read_default_count_and_partial_hint(tmp_path, ga_module):
    sample = tmp_path / "large.txt"
    sample.write_text("\n".join(f"line {i}" for i in range(1, 151)), encoding="utf-8")

    result = ga_module.file_read(str(sample), show_linenos=True)

    assert "PARTIAL showing 120" in result
    assert "Prefer keyword or start+count ranges" in result
    assert "120|line 120" in result
    assert "121|line 121" not in result


def test_do_file_read_caps_count(tmp_path, handler_cls):
    sample = tmp_path / "large.txt"
    sample.write_text("\n".join(f"line {i}" for i in range(1, 351)), encoding="utf-8")
    handler = object.__new__(handler_cls)
    handler._get_abs_path = lambda path: path
    handler._get_anchor_prompt = lambda skip=False: ""

    gen = handler.do_file_read({"path": str(sample), "count": 999}, response=None)
    chunks = []
    while True:
        try:
            chunks.append(next(gen))
        except StopIteration as exc:
            outcome = exc.value
            break

    assert "count capped at 300" in outcome.data
    assert "PARTIAL showing 300" in outcome.data
    assert "300|line 300" in outcome.data
    assert "301|line 301" not in outcome.data


# ---------- GlueGate reminder regression ----------


def test_glue_gate_prompt_triggers_for_new_infra(ga_module):
    prompt = ga_module.build_glue_gate_prompt(
        "I will add an auth SDK wrapper and workflow scheduler.",
        [],
        ["[User] 新增一个通用 auth client"],
    )

    assert "Glue Gate Reminder" in prompt
    assert "glue_coding_gate_sop.md" in prompt


def test_glue_gate_prompt_skips_after_sop_read(ga_module):
    prompt = ga_module.build_glue_gate_prompt(
        "I will add an auth SDK wrapper.",
        [{"tool_name": "file_read", "args": {"path": "../memory/glue_coding_gate_sop.md"}}],
        ["[User] 新增一个通用 auth client"],
    )

    assert prompt == ""


def test_glue_gate_prompt_ignores_neutral_turn(ga_module):
    prompt = ga_module.build_glue_gate_prompt(
        "Summarize current status.",
        [],
        ["[User] 继续"],
    )

    assert prompt == ""
