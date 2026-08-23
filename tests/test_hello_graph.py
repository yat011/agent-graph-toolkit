"""Tests for the hello_graph worked example (sequence, sequential map/fan-out, interrupt-based
checkpoint gate, one CLI-dispatched Worker node, and a plain-code checker/branch node) — fast,
uses a fake `_run_subprocess` (no real `claude` CLI call) and an in-memory checkpointer.
"""

import json
import subprocess
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agentgraph_engine.graph_loader import get_build_graph, load_graph_module

GRAPH_PATH = (
    Path(__file__).resolve().parent.parent / "agentgraph_engine" / "examples" / "hello_graph" / "graph.py"
)

MARKER = "Write your full output to this exact file path before finishing: "


def _write_output(input_text: str, content: str) -> None:
    path_line = next(line for line in input_text.splitlines() if line.startswith(MARKER))
    out_path = Path(path_line[len(MARKER) :].strip())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")


def _ok_executor(content="Said hello.\nResult: greeted", ok=True):
    def executor(argv, input_text, timeout):
        if content is not None:
            _write_output(input_text, content)
        return subprocess.CompletedProcess(argv, 0 if ok else 1, stdout=json.dumps({"result": content or ""}), stderr="")

    return executor


def _build():
    module = load_graph_module(GRAPH_PATH)
    return get_build_graph(module)


def test_pauses_at_checkpoint_gate_then_resumes_to_pass(monkeypatch, tmp_path):
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", _ok_executor())
    build_graph = _build()
    graph = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "hello-1"}}

    r1 = graph.invoke({"run_dir": str(tmp_path), "items": ["alpha", "beta", "gamma"]}, config=config)
    assert "__interrupt__" in r1
    assert "outcome" not in r1

    r2 = graph.invoke(Command(resume="go"), config=config)
    assert r2["outcome"] == "pass"
    assert r2["fan_out_results"] == ["ALPHA", "BETA", "GAMMA"]
    assert r2["worker_result_line"] == "greeted"


def test_worker_dispatch_failure_after_resume_routes_to_halted(monkeypatch, tmp_path):
    def always_fail(argv, input_text, timeout):
        return subprocess.CompletedProcess(argv, 1, stdout='{"result":""}', stderr="boom")

    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", always_fail)
    build_graph = _build()
    graph = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "hello-2"}}

    graph.invoke({"run_dir": str(tmp_path), "items": ["x"]}, config=config)
    result = graph.invoke(Command(resume="go"), config=config)
    assert result.get("halted") is True
    assert result.get("halt_reason") == "retries_exhausted"


def test_checker_fails_when_worker_result_line_wrong(monkeypatch, tmp_path):
    monkeypatch.setattr("agentgraph_engine.dispatch._run_subprocess", _ok_executor(content="Result: something-else"))
    build_graph = _build()
    graph = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "hello-3"}}

    graph.invoke({"run_dir": str(tmp_path), "items": ["a", "b"]}, config=config)
    result = graph.invoke(Command(resume="go"), config=config)
    assert result["outcome"] == "fail"
