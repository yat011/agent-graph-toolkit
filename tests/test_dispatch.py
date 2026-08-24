"""Tests for agentgraph_engine.dispatch — the shared headless-CLI dispatch/executor module.

Follows agentgraph-test-quality-bar: tests go through the public dispatch_worker/
dispatch_with_retry/extract_result_line/load_role_prompt seam, never reach into private state.
A fake `executor` stands in for a real subprocess call — that's the documented pluggable seam
(the "Executor"), not an implementation detail.
"""

from agentgraph_engine.constants import (
    MODEL_CHEAP,
    ROLE_GENERAL_PURPOSE,
)

import json
import subprocess
from pathlib import Path

import pytest

from agentgraph_engine.dispatch import (
    RolePromptError,
    dispatch_worker,
    dispatch_with_retry,
    extract_result_line,
    load_role_prompt,
    preflight_role_prompts,
    required_roles_from_graph_path,
)
from agentgraph_engine.graph_loader import TEMPLATES_ROOT
from agentgraph_engine.worker_cli import UnknownGraphModelError, resolve_worker_cli


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


def test_dispatch_prompt_sets_caveman_full_output_voice(tmp_path):
    output_path = tmp_path / "node" / "attempt-1" / "output.md"
    captured: dict[str, str] = {}

    def executor(argv, input_text, timeout):
        captured["text"] = input_text
        marker = "Write your full output to this exact file path before finishing: "
        path_line = next(line for line in input_text.splitlines() if line.startswith(marker))
        out_path = Path(path_line[len(marker) :].strip())
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("Result: done\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, stdout='{"result":""}', stderr="")

    result = dispatch_worker(
        role=ROLE_GENERAL_PURPOSE,
        task_prompt="do the thing",
        output_path=output_path,
        executor=executor,
    )
    assert result.ok is True
    text = captured["text"]
    assert "caveman full" in text
    assert "Keep Result: line" in text
    assert text.index("caveman full") < text.index(
        "Write your full output to this exact file path before finishing:"
    )


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
    assert "--exclude-dynamic-system-prompt-sections" in argv


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


def test_extract_result_line_heading_and_case():
    assert extract_result_line("## Result: ACCEPTED\n") == "ACCEPTED"
    assert extract_result_line("RESULT: accepted\n") == "accepted"
    assert extract_result_line("- Result: not a heading\n") is None


def test_load_role_prompt_strips_frontmatter_for_real_role_file():
    text = load_role_prompt("code-writer")
    assert not text.startswith("---")
    assert "minimal correct change" in text


def test_load_role_prompt_general_purpose_has_no_persona():
    assert load_role_prompt(ROLE_GENERAL_PURPOSE) == ""


def test_load_role_prompt_unknown_role_raises():
    with pytest.raises(RolePromptError, match="Missing role prompt"):
        load_role_prompt("not-a-real-role-xyz")


def test_dispatch_worker_fails_before_subprocess_when_named_role_missing(tmp_path):
    calls: list = []

    def executor(argv, input_text, timeout):
        calls.append(argv)
        raise AssertionError("executor must not run when the role prompt cannot load")

    with pytest.raises(RolePromptError):
        dispatch_worker(
            role="not-a-real-role-xyz",
            task_prompt="x",
            output_path=tmp_path / "out.md",
            executor=executor,
        )
    assert calls == []


def test_preflight_feature_kickoff_and_standard_task_pass_with_repo_agents():
    preflight_role_prompts(TEMPLATES_ROOT / "feature-kickoff" / "graph.py")
    preflight_role_prompts(TEMPLATES_ROOT / "standard-task" / "graph.py")


def test_required_roles_skip_general_purpose_and_researcher_unless_dispatched():
    standard = required_roles_from_graph_path(TEMPLATES_ROOT / "standard-task" / "graph.py")
    assert "code-writer" in standard
    assert "reviewer" in standard
    assert "general-purpose" not in standard
    kickoff = required_roles_from_graph_path(TEMPLATES_ROOT / "feature-kickoff" / "graph.py")
    assert "planner" in kickoff
    assert "tech-plan-reviewer" in kickoff
    assert "code-writer" in kickoff
    assert "reviewer" in kickoff
    assert "researcher" not in kickoff
    assert "general-purpose" not in kickoff


def test_template_nodes_have_no_triple_quoted_fstrings():
    for name in ("feature-kickoff", "standard-task"):
        text = (TEMPLATES_ROOT / name / "nodes.py").read_text(encoding="utf-8")
        assert 'f"""' not in text
        assert "f'''" not in text


def test_preflight_fails_when_agents_dir_is_empty(tmp_path, monkeypatch):
    empty = tmp_path / "agents"
    empty.mkdir()
    monkeypatch.setattr("agentgraph_engine.dispatch.AGENTS_DIR", empty)
    with pytest.raises(RolePromptError):
        preflight_role_prompts(TEMPLATES_ROOT / "standard-task" / "graph.py")


_CLAUDE_SONNET_TAIL = [
    "-p",
    "--permission-mode",
    "auto",
    "--output-format",
    "json",
    "--exclude-dynamic-system-prompt-sections",
    "--model",
    "sonnet",
]
_CLAUDE_HAIKU_TAIL = [
    "-p",
    "--permission-mode",
    "acceptEdits",
    "--allowedTools",
    "Write",
    "--output-format",
    "json",
    "--exclude-dynamic-system-prompt-sections",
    "--model",
    "haiku",
]
_CLAUDE_OPUS_TAIL = [
    "-p",
    "--permission-mode",
    "auto",
    "--output-format",
    "json",
    "--exclude-dynamic-system-prompt-sections",
    "--model",
    "opus",
]
_GROK_TAIL = [
    "-p",
    "--permission-mode",
    "auto",
    "--output-format",
    "json",
    "--model",
    "grok-4.6",
    "--effort",
    "high",
]
_CURSOR_TAIL = [
    "-p",
    "--auto-review",
    "--approve-mcps",
    "--trust",
    "--output-format",
    "json",
    "--model",
    "cursor-grok-4.6-high",
]


@pytest.mark.parametrize(
    ("cli", "model", "binary_stem", "argv_tail"),
    [
        ("claude", MODEL_CHEAP, "claude", _CLAUDE_HAIKU_TAIL),
        ("claude", "sonnet", "claude", _CLAUDE_SONNET_TAIL),
        ("claude", "opus", "claude", _CLAUDE_OPUS_TAIL),
        ("claude", None, "claude", _CLAUDE_SONNET_TAIL),
        ("grok", MODEL_CHEAP, "grok", _GROK_TAIL),
        ("grok", "sonnet", "grok", _GROK_TAIL),
        ("grok", "opus", "grok", _GROK_TAIL),
        ("grok", None, "grok", _GROK_TAIL),
        ("cursor", MODEL_CHEAP, "cursor-agent", _CURSOR_TAIL),
        ("cursor", "sonnet", "cursor-agent", _CURSOR_TAIL),
        ("cursor", "opus", "cursor-agent", _CURSOR_TAIL),
        ("cursor", None, "cursor-agent", _CURSOR_TAIL),
    ],
)
def test_dispatch_argv_matches_spec_per_cli_and_model(tmp_path, cli, model, binary_stem, argv_tail):
    resolve_worker_cli(cli_flag=cli)
    calls = []
    output_path = tmp_path / "node" / "attempt-1" / "output.md"
    dispatch_worker(
        role=ROLE_GENERAL_PURPOSE,
        task_prompt="x",
        output_path=output_path,
        model=model,
        executor=make_executor(write_output=True, call_log=calls),
    )
    argv = calls[0]
    assert Path(argv[0]).stem.lower() == binary_stem
    assert argv[1:] == argv_tail
    assert "--resume" not in argv
    assert "--continue" not in argv
    if cli == "cursor":
        assert "--force" not in argv
        assert "--yolo" not in argv
        assert Path(argv[0]).stem.lower() != "agent"


def test_unknown_graph_model_is_error(tmp_path):
    output_path = tmp_path / "node" / "attempt-1" / "output.md"
    with pytest.raises(UnknownGraphModelError, match="unknown graph model"):
        dispatch_worker(
            role=ROLE_GENERAL_PURPOSE,
            task_prompt="x",
            output_path=output_path,
            model="gpt-4",
            executor=make_executor(write_output=True),
        )


def test_usage_json_written_from_claude_envelope(tmp_path):
    output_path = tmp_path / "node" / "attempt-1" / "output.md"
    envelope = {
        "result": "chat text",
        "session_id": "sess-1",
        "total_cost_usd": 0.12,
        "usage": {
            "input_tokens": 10,
            "output_tokens": 20,
            "cache_read_input_tokens": 3,
            "cache_creation_input_tokens": 4,
        },
    }
    result = dispatch_worker(
        role=ROLE_GENERAL_PURPOSE,
        task_prompt="x",
        output_path=output_path,
        executor=make_executor(write_output=True, envelope=envelope),
    )
    expected = {
        "worker_cli": "claude",
        "model": "sonnet",
        "cost_usd": 0.12,
        "input_tokens": 10,
        "output_tokens": 20,
        "cache_read_tokens": 3,
        "cache_write_tokens": 4,
    }
    usage_path = output_path.parent / "usage.json"
    assert json.loads(usage_path.read_text(encoding="utf-8")) == expected
    assert result.usage == expected
    assert result.cost_usd == 0.12


def test_cursor_usage_fields_are_null_when_envelope_has_no_tokens(tmp_path):
    resolve_worker_cli(cli_flag="cursor")
    output_path = tmp_path / "node" / "attempt-1" / "output.md"
    result = dispatch_worker(
        role=ROLE_GENERAL_PURPOSE,
        task_prompt="x",
        output_path=output_path,
        model="sonnet",
        executor=make_executor(write_output=True, envelope={"result": "ok"}),
    )
    expected = {
        "worker_cli": "cursor",
        "model": "cursor-grok-4.6-high",
        "cost_usd": None,
        "input_tokens": None,
        "output_tokens": None,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
    }
    usage_path = output_path.parent / "usage.json"
    assert json.loads(usage_path.read_text(encoding="utf-8")) == expected
    assert result.usage == expected
    assert result.cost_usd is None


def test_usage_json_written_when_executor_cannot_start(tmp_path):
    output_path = tmp_path / "node" / "attempt-1" / "output.md"

    def boom(argv, input_text, timeout):
        raise RuntimeError("cannot start")

    result = dispatch_worker(
        role=ROLE_GENERAL_PURPOSE,
        task_prompt="x",
        output_path=output_path,
        model=MODEL_CHEAP,
        executor=boom,
    )
    assert result.ok is False
    usage_path = output_path.parent / "usage.json"
    data = json.loads(usage_path.read_text(encoding="utf-8"))
    assert data == {
        "worker_cli": "claude",
        "model": "haiku",
        "cost_usd": None,
        "input_tokens": None,
        "output_tokens": None,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
    }


def test_grok_envelope_accepts_cache_read_tokens_field_names(tmp_path):
    resolve_worker_cli(cli_flag="grok")
    output_path = tmp_path / "node" / "attempt-1" / "output.md"
    envelope = {
        "result": "ok",
        "cost_usd": 0.4,
        "usage": {
            "input_tokens": 1,
            "output_tokens": 2,
            "cache_read_tokens": 5,
            "cache_write_tokens": 6,
        },
    }
    result = dispatch_worker(
        role=ROLE_GENERAL_PURPOSE,
        task_prompt="x",
        output_path=output_path,
        executor=make_executor(write_output=True, envelope=envelope),
    )
    assert result.usage == {
        "worker_cli": "grok",
        "model": "grok-4.6",
        "cost_usd": 0.4,
        "input_tokens": 1,
        "output_tokens": 2,
        "cache_read_tokens": 5,
        "cache_write_tokens": 6,
    }

