"""Tests for agentgraph_engine.dispatch — the shared headless-CLI dispatch/executor module.

Follows agentgraph-test-quality-bar: tests go through the public dispatch_worker/
dispatch_with_retry/extract_result_line/load_role_prompt seam, never reach into private state.
A fake `executor` stands in for a real `claude -p` subprocess call — that's the documented
pluggable seam (the "Executor"), not an implementation detail.
"""

from agentgraph_engine.constants import (
    MODEL_CHEAP,
    ROLE_GENERAL_PURPOSE,
)

import subprocess
from pathlib import Path

import pytest

from agentgraph_engine.dispatch import (
    dispatch_worker,
    dispatch_with_retry,
    extract_result_line,
    load_role_prompt,
)


def make_executor(write_output: bool, returncode: int = 0, envelope: dict | None = None,
                   call_log: list | None = None):
    envelope = envelope if envelope is not None else {"result": "chat text", "session_id": "sess-1",
                                                        "total_cost_usd": 0.05}

    def executor(argv, input_text, timeout):
        if call_log is not None:
            call_log.append(argv)
        if write_output:
            # Simulate the headless worker writing its own output.md, as instructed in the
            # combined prompt.
            marker = "Write your full output to this exact file path before finishing: "
            path_line = next(line for line in input_text.splitlines() if line.startswith(marker))
            out_path = Path(path_line[len(marker):].strip())
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text("Some prose.\nResult: done\n", encoding="utf-8")
        import json
        return subprocess.CompletedProcess(argv, returncode, stdout=json.dumps(envelope), stderr="")

    return executor


def test_dispatch_success_reads_result_line_from_output_file(tmp_path):
    output_path = tmp_path / "01_node" / "attempt-1" / "output.md"
    result = dispatch_worker(
        role=ROLE_GENERAL_PURPOSE,
        task_prompt="do the thing",
        output_path=output_path,
        executor=make_executor(write_output=True),
    )
    assert result.ok is True
    assert result.result_line == "done"
    assert result.session_id == "sess-1"
    assert result.cost_usd == 0.05
    assert result.output_exists is True


def test_dispatch_missing_output_file_is_technical_failure(tmp_path):
    output_path = tmp_path / "node" / "attempt-1" / "output.md"
    result = dispatch_worker(
        role=ROLE_GENERAL_PURPOSE,
        task_prompt="do the thing",
        output_path=output_path,
        executor=make_executor(write_output=False),
    )
    assert result.ok is False
    assert result.output_exists is False


def test_dispatch_nonzero_exit_is_technical_failure_even_with_output(tmp_path):
    output_path = tmp_path / "node" / "attempt-1" / "output.md"
    result = dispatch_worker(
        role=ROLE_GENERAL_PURPOSE,
        task_prompt="do the thing",
        output_path=output_path,
        executor=make_executor(write_output=True, returncode=1),
    )
    assert result.ok is False


def test_dispatch_falls_back_to_result_text_when_output_has_no_result_line(tmp_path):
    output_path = tmp_path / "node" / "attempt-1" / "output.md"

    def executor(argv, input_text, timeout):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("no result line in the file", encoding="utf-8")
        import json
        return subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps({"result": "closing line\nResult: from-chat"}), stderr=""
        )

    result = dispatch_worker(role=ROLE_GENERAL_PURPOSE, task_prompt="x", output_path=output_path, executor=executor)
    assert result.ok is True
    assert result.result_line == "from-chat"


def test_dispatch_never_passes_resume_or_continue_flags(tmp_path):
    calls = []
    output_path = tmp_path / "node" / "attempt-1" / "output.md"
    dispatch_worker(
        role=ROLE_GENERAL_PURPOSE, task_prompt="x", output_path=output_path,
        executor=make_executor(write_output=True, call_log=calls),
    )
    assert calls, "executor was not called"
    argv = calls[0]
    assert "--resume" not in argv
    assert "--continue" not in argv
    # argv[0] is resolved via shutil.which (e.g. `claude.CMD` on Windows) — check the resolved
    # binary name, not the literal string "claude".
    assert Path(argv[0]).stem.lower() == "claude"
    assert argv[1] == "-p"
    assert "--permission-mode" in argv and "auto" in argv
    assert "--output-format" in argv and "json" in argv


def test_dispatch_model_cheap_alias_maps_to_haiku(tmp_path):
    calls = []
    output_path = tmp_path / "node" / "attempt-1" / "output.md"
    dispatch_worker(
        role=ROLE_GENERAL_PURPOSE, task_prompt="x", output_path=output_path, model=MODEL_CHEAP,
        executor=make_executor(write_output=True, call_log=calls),
    )
    argv = calls[0]
    idx = argv.index("--model")
    assert argv[idx + 1] == "haiku"


def test_dispatch_haiku_model_uses_accept_edits_with_write_allowlist(tmp_path):
    calls = []
    output_path = tmp_path / "node" / "attempt-1" / "output.md"
    dispatch_worker(
        role=ROLE_GENERAL_PURPOSE, task_prompt="x", output_path=output_path, model="haiku",
        executor=make_executor(write_output=True, call_log=calls),
    )
    argv = calls[0]
    assert "--permission-mode" in argv and "acceptEdits" in argv
    assert "auto" not in argv
    idx = argv.index("--allowedTools")
    assert argv[idx + 1] == "Write"


def test_dispatch_with_retry_stops_at_first_ok(tmp_path):
    output_path = tmp_path / "node" / "attempt-1" / "output.md"
    calls = {"n": 0}

    def executor(argv, input_text, timeout):
        calls["n"] += 1
        if calls["n"] < 2:
            return subprocess.CompletedProcess(argv, 0, stdout='{"result":""}', stderr="")
        marker = "Write your full output to this exact file path before finishing: "
        path_line = next(line for line in input_text.splitlines() if line.startswith(marker))
        out_path = Path(path_line[len(marker):].strip())
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("Result: implemented\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, stdout='{"result":""}', stderr="")

    result = dispatch_with_retry(
        retry=2, role=ROLE_GENERAL_PURPOSE, task_prompt="x", output_path=output_path, executor=executor
    )
    assert result.ok is True
    assert calls["n"] == 2


def test_dispatch_with_retry_exhausts_and_returns_last_failure(tmp_path):
    output_path = tmp_path / "node" / "attempt-1" / "output.md"
    calls = {"n": 0}

    def executor(argv, input_text, timeout):
        calls["n"] += 1
        return subprocess.CompletedProcess(argv, 0, stdout='{"result":""}', stderr="")

    result = dispatch_with_retry(
        retry=2, role=ROLE_GENERAL_PURPOSE, task_prompt="x", output_path=output_path, executor=executor
    )
    assert result.ok is False
    assert calls["n"] == 3  # retry=2 -> 3 total attempts


def test_extract_result_line_takes_last_match_and_strips_prefix():
    assert extract_result_line("foo\nResult: accepted\n") == "accepted"
    assert extract_result_line("Result: a\nResult: rejected - x") == "rejected - x"
    assert extract_result_line("no result here") is None
    assert extract_result_line(None) is None


def test_load_role_prompt_strips_frontmatter_for_real_role_file():
    text = load_role_prompt("code-writer")
    assert not text.startswith("---")
    assert "minimal correct change" in text


def test_load_role_prompt_general_purpose_has_no_persona():
    assert load_role_prompt(ROLE_GENERAL_PURPOSE) == ""


def test_load_role_prompt_unknown_role_is_empty():
    assert load_role_prompt("not-a-real-role-xyz") == ""
