"""Tests for typed Turn state machine."""
import pytest
from OriginAgent.agent.agent_turn_pipeline import TurnState, TurnEvent, TURN_PIPELINE_TRANSITIONS


def test_turnevent_is_enum():
    event = TurnEvent.OK
    assert isinstance(event, TurnEvent)


def test_all_transitions_use_turnevent():
    for (state, event), next_state in TURN_PIPELINE_TRANSITIONS.items():
        assert isinstance(state, TurnState), f"State {state} is not TurnState"
        assert isinstance(event, TurnEvent), f"Event {event} is not TurnEvent"
        assert isinstance(next_state, TurnState), f"Next state {next_state} is not TurnState"


def test_all_states_reachable():
    """Every state (except the entry point and terminal) must appear as a target."""
    all_states = set(TurnState)
    target_states = set(TURN_PIPELINE_TRANSITIONS.values())
    start_and_terminal = {TurnState.RESTORE, TurnState.DONE}
    unreachable = (all_states - start_and_terminal) - target_states
    assert not unreachable, f"States never reached: {unreachable}"


def test_no_dead_end_except_done():
    all_sources = {s for (s, _) in TURN_PIPELINE_TRANSITIONS.keys()}
    all_targets = set(TURN_PIPELINE_TRANSITIONS.values())
    terminal = {TurnState.DONE}
    dead_ends = (all_targets - all_sources) - terminal
    assert not dead_ends, f"States with no outgoing transitions: {dead_ends}"
