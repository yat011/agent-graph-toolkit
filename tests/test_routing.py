"""Tests for classify_gate / gate_route at their public seam (nested per-node records)."""

from agentgraph_engine.constants import (
    ACCEPT,
    ATTEMPT_COUNT_KEY,
    HALT_MANUAL_REQUESTED,
    HALT_REASON_KEY,
    HALT_REJECT_ATTEMPTS_EXHAUSTED,
    HALT_UNRECOGNIZED_RESULT,
    IMPLEMENT_REQUIREMENTS_NODE,
    LOOP_BACK,
    MANUAL,
    MANUAL_FLAG_NODE,
    RESULT_ACCEPT,
    RESULT_KEY,
    RESULT_MANUAL,
    RESULT_REJECT,
    REVIEW_NODE,
    ROUTE_KEY,
    SUCCESS_NODE,
)
from agentgraph_engine.routing import GateConfig, classify_gate, gate_route

REVIEW_GATE = GateConfig(
    retry_target=IMPLEMENT_REQUIREMENTS_NODE,
    max_retry_attempts=3,
)


def test_classify_gate_accept():
    state = {REVIEW_NODE: {RESULT_KEY: "accepted — looks good"}}
    assert classify_gate(state, REVIEW_GATE, REVIEW_NODE) == {ROUTE_KEY: ACCEPT}


def test_classify_gate_manual_requested():
    state = {REVIEW_NODE: {RESULT_KEY: "manual — needs a human"}}
    assert classify_gate(state, REVIEW_GATE, REVIEW_NODE) == {
        ROUTE_KEY: MANUAL,
        HALT_REASON_KEY: HALT_MANUAL_REQUESTED,
    }


def test_classify_gate_reject_loops_back_under_budget():
    state = {
        REVIEW_NODE: {RESULT_KEY: "rejected — fix tests"},
        IMPLEMENT_REQUIREMENTS_NODE: {ATTEMPT_COUNT_KEY: 2},
    }
    assert classify_gate(state, REVIEW_GATE, REVIEW_NODE) == {ROUTE_KEY: LOOP_BACK}


def test_classify_gate_reject_exhausted_goes_manual():
    state = {
        REVIEW_NODE: {RESULT_KEY: "rejected — still bad"},
        IMPLEMENT_REQUIREMENTS_NODE: {ATTEMPT_COUNT_KEY: 3},
    }
    assert classify_gate(state, REVIEW_GATE, REVIEW_NODE) == {
        ROUTE_KEY: MANUAL,
        HALT_REASON_KEY: HALT_REJECT_ATTEMPTS_EXHAUSTED,
    }


def test_classify_gate_unrecognized_is_immediate_manual():
    state = {REVIEW_NODE: {RESULT_KEY: "garbled nonsense"}}
    assert classify_gate(state, REVIEW_GATE, REVIEW_NODE) == {
        ROUTE_KEY: MANUAL,
        HALT_REASON_KEY: HALT_UNRECOGNIZED_RESULT,
    }


def test_gate_route_reads_nested_route_field():
    accept_state = {REVIEW_NODE: {ROUTE_KEY: ACCEPT}}
    assert (
        gate_route(accept_state, REVIEW_GATE, REVIEW_NODE, accept_target=SUCCESS_NODE, manual_target=MANUAL_FLAG_NODE)
        == SUCCESS_NODE
    )

    loop_state = {REVIEW_NODE: {ROUTE_KEY: LOOP_BACK}}
    assert (
        gate_route(loop_state, REVIEW_GATE, REVIEW_NODE, accept_target=SUCCESS_NODE, manual_target=MANUAL_FLAG_NODE)
        == IMPLEMENT_REQUIREMENTS_NODE
    )

    manual_state = {REVIEW_NODE: {ROUTE_KEY: MANUAL}}
    assert (
        gate_route(manual_state, REVIEW_GATE, REVIEW_NODE, accept_target=SUCCESS_NODE, manual_target=MANUAL_FLAG_NODE)
        == MANUAL_FLAG_NODE
    )


def test_classify_gate_manual_keyword_constant_matches_result_manual():
    assert RESULT_MANUAL == "manual"
    state = {REVIEW_NODE: {RESULT_KEY: RESULT_MANUAL}}
    assert classify_gate(state, REVIEW_GATE, REVIEW_NODE)[ROUTE_KEY] == MANUAL
