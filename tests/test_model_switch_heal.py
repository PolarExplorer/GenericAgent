import importlib.util
import pathlib
import py_compile


ROOT = pathlib.Path(__file__).resolve().parents[1]
LLMCORE = ROOT / "llmcore.py"
AGENTMAIN = ROOT / "agentmain.py"


def load_module(path, name):
    py_compile.compile(str(path), doraise=True)
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_session_heal_is_reentrant_under_existing_lock():
    mod = load_module(LLMCORE, "llmcore_switch_heal_test")
    session = mod.BaseSession({"apikey": "k", "apibase": "http://localhost", "model": "m"})
    session.history = [
        {"role": "user", "content": [{"type": "text", "text": "bad turn"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "!!!Error: HTTP 400 orphan tool_call"}]},
    ]

    with session.lock:
        removed = session._heal_history()

    assert removed == 2
    assert session.history == []


def test_agentmain_next_llm_copies_history_then_invokes_target_heal(monkeypatch):
    mod = load_module(AGENTMAIN, "agentmain_switch_heal_test")

    class Backend:
        def __init__(self, model):
            self.model = model
            self.history = []
            self.heal_called = 0

        def heal(self):
            self.heal_called += 1
            self.history = [m for m in self.history if not str(m.get("content", "")).startswith("!!!Error:")]

    class Client:
        def __init__(self, backend):
            self.backend = backend
            self.last_tools = "old"

    old_backend = Backend("claude")
    bad_msg = {"role": "assistant", "content": "!!!Error: HTTP 400 orphan tool_call_id"}
    good_msg = {"role": "assistant", "content": "ok"}
    old_backend.history = [good_msg, bad_msg]
    new_backend = Backend("kimi")

    agent = mod.GenericAgent.__new__(mod.GenericAgent)
    agent.llm_no = 0
    agent.llmclients = [Client(old_backend), Client(new_backend)]
    agent.llmclient = agent.llmclients[0]

    monkeypatch.setattr(mod.GenericAgent, "load_llm_sessions", lambda self: None)
    schema_calls = []
    monkeypatch.setattr(mod, "load_tool_schema", lambda suffix="": schema_calls.append(suffix))

    agent.next_llm(1)

    assert agent.llmclient.backend is new_backend
    assert new_backend.history == [good_msg]
    assert new_backend.history is not old_backend.history
    assert new_backend.heal_called == 1
    assert agent.llmclient.last_tools == ""
    assert schema_calls == ["_cn"]
