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
