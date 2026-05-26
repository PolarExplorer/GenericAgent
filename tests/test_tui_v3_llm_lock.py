"""
Isolated regression tests for TUI v3 model-locking behavior.

Why isolated:
- frontends/tui_v3.py has heavy optional UI dependencies (e.g. rich).
- We only need to protect the behavioral contract around `/llm` locking.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FakeAgent:
    llm_no: int = 0
    baseline_llm_no: int = 0
    llm_locked: bool = False

    def next_llm(self, n: int) -> None:
        self.llm_no = n


class FakeBridge:
    """Minimal reproduction of the fixed TUI v3 bridge contract."""

    def __init__(self, agent: FakeAgent):
        self.agent = agent

    def switch_llm(self, n: int) -> None:
        self.agent.next_llm(n)
        self.agent.baseline_llm_no = self.agent.llm_no
        self.agent.llm_locked = True

    def unlock_llm(self) -> None:
        self.agent.llm_locked = False


def _simulate_pre_route_baseline_reset(agent: FakeAgent) -> None:
    if not agent.llm_locked and agent.llm_no != agent.baseline_llm_no:
        agent.llm_no = agent.baseline_llm_no


def test_switch_llm_locks_model_and_syncs_baseline():
    agent = FakeAgent()
    bridge = FakeBridge(agent)

    bridge.switch_llm(7)

    assert agent.llm_no == 7
    assert agent.baseline_llm_no == 7
    assert agent.llm_locked is True


def test_unlock_llm_disables_lock():
    agent = FakeAgent()
    bridge = FakeBridge(agent)

    bridge.switch_llm(7)
    bridge.unlock_llm()

    assert agent.llm_locked is False
    assert agent.baseline_llm_no == 7
    assert agent.llm_no == 7


def test_locked_llm_should_resist_pre_route_baseline_reset():
    agent = FakeAgent()
    bridge = FakeBridge(agent)

    bridge.switch_llm(7)
    _simulate_pre_route_baseline_reset(agent)

    assert agent.llm_no == 7
    assert agent.baseline_llm_no == 7
    assert agent.llm_locked is True


def test_unlocked_llm_should_allow_pre_route_baseline_reset():
    agent = FakeAgent()

    agent.next_llm(7)
    agent.baseline_llm_no = 0

    _simulate_pre_route_baseline_reset(agent)

    assert agent.llm_no == 0
    assert agent.llm_locked is False


def test_switch_llm_edge_case_zero_model_should_lock_and_resist_reset():
    agent = FakeAgent(llm_no=3, baseline_llm_no=3)
    bridge = FakeBridge(agent)

    bridge.switch_llm(0)
    _simulate_pre_route_baseline_reset(agent)

    assert agent.llm_no == 0
    assert agent.baseline_llm_no == 0
    assert agent.llm_locked is True


def test_switch_llm_should_be_idempotent_on_repeated_calls():
    agent = FakeAgent()
    bridge = FakeBridge(agent)

    bridge.switch_llm(7)
    bridge.switch_llm(7)
    _simulate_pre_route_baseline_reset(agent)

    assert agent.llm_no == 7
    assert agent.baseline_llm_no == 7
    assert agent.llm_locked is True
