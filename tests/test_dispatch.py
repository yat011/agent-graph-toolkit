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
    OUTPUT_PATH_LINE_PREFIX,
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
            path_line = next(
                line for line in input_text.splitlines() if line.startswith(OUTPUT_PATH_LINE_PREFIX)
            )
            out_path = Path(path_line[len(OUTPUT_PATH_LINE_PREFIX):].strip())
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
        path_line = next(
            line for line in input_text.splitlines() if line.startswith(OUTPUT_PATH_LINE_PREFIX)
        )
        out_path = Path(path_line[len(OUTPUT_PATH_LINE_PREFIX) :].strip())
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
    assert "caveman skill full" in text
    assert "Keep Result: line" in text
    assert "Never recap" in text
    assert "file:line" in text
    assert "one-line `Result: {xxx}`" in text
    assert text.index("caveman skill full") < text.index(OUTPUT_PATH_LINE_PREFIX)


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
        path_line = next(
            line for line in input_text.splitlines() if line.startswith(OUTPUT_PATH_LINE_PREFIX)
        )
        out_path = Path(path_line[len(OUTPUT_PATH_LINE_PREFIX):].strip())
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


def test_load_role_prompt_reviewer_gitignore_is_non_blocking():
    text = load_role_prompt("reviewer")
    assert "Gitignored generated file" in text
    assert "non-blocking" in text
    assert text.count("Gitignored generated file") == 1


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
    assert len(calls) == 1
    argv = calls[0]
    assert Path(argv[0]).stem.lower() == binary_stem
    if cli == "grok":
        assert argv[1] == "-p"
        assert OUTPUT_PATH_LINE_PREFIX in argv[2]
        assert argv[3:] == argv_tail
    else:
        assert argv[1:] == argv_tail
    assert "--resume" not in argv
    assert "--continue" not in argv
    if cli == "cursor":
        assert "--force" not in argv
        assert "--yolo" not in argv
        assert Path(argv[0]).stem.lower() != "agent"


def test_grok_single_flag_takes_prompt_not_the_next_option(tmp_path):
    resolve_worker_cli(cli_flag="grok")
    calls: list[list[str]] = []
    stdin: list[str] = []
    output_path = tmp_path / "node" / "attempt-1" / "output.md"

    def executor(argv: list, input_text: str, timeout: int | None) -> subprocess.CompletedProcess:
        calls.append(argv)
        stdin.append(input_text)
        path_line = next(
            line for line in input_text.splitlines() if line.startswith(OUTPUT_PATH_LINE_PREFIX)
        )
        out_path = Path(path_line[len(OUTPUT_PATH_LINE_PREFIX) :].strip())
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("Result: done\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, stdout='{"result":""}', stderr="")

    dispatch_worker(
        role=ROLE_GENERAL_PURPOSE,
        task_prompt="x",
        output_path=output_path,
        executor=executor,
    )
    argv = calls[0]
    p_idx = argv.index("-p")
    assert argv[p_idx + 1] == stdin[0]
    assert not argv[p_idx + 1].startswith("-")


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


ORCA_CREATE_HANDLE = "term_test_handle"


def _stub_orca_and_grok_which(monkeypatch, tmp_path, *, orca: bool = True, grok: bool = True):
    """PATH stand-in so grok-orca tests do not depend on a live orca/grok install."""

    def fake_which(cmd: str) -> str | None:
        stem = Path(cmd).stem.lower()
        if stem == "orca" and orca:
            return str(tmp_path / "fake-bin" / "orca")
        if stem == "grok" and grok:
            return str(tmp_path / "fake-bin" / "grok")
        return None

    monkeypatch.setattr("shutil.which", fake_which)


def _orca_action(argv: list) -> str:
    return argv[argv.index("terminal") + 1]


def make_orca_cli_executor(call_log: list, *, fail_second_wait: bool = False):
    """Fake `orca` CLI: argv in, JSON stdout out. Writes output.md on send from --text.

    Create stdout is the known-good handle blob `{"handle": "term_test_handle"}`.
    """
    waits = {"n": 0}

    def executor(argv, input_text, timeout):
        call_log.append(list(argv))
        action = _orca_action(argv)
        if action == "create":
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=json.dumps(
                    {
                        "ok": True,
                        "id": "req-uuid",
                        "result": {"handle": ORCA_CREATE_HANDLE},
                    }
                ),
                stderr="",
            )
        if action == "wait":
            waits["n"] += 1
            orca_wait_json = json.dumps({"ok": True, "id": "req-uuid", "result": {}})
            if fail_second_wait and waits["n"] == 2:
                return subprocess.CompletedProcess(
                    argv, 1, stdout=orca_wait_json, stderr="tui-idle timeout"
                )
            return subprocess.CompletedProcess(argv, 0, stdout=orca_wait_json, stderr="")
        if action == "send":
            text = argv[argv.index("--text") + 1]
            path_line = next(
                line for line in text.splitlines() if line.startswith(OUTPUT_PATH_LINE_PREFIX)
            )
            out_path = Path(path_line[len(OUTPUT_PATH_LINE_PREFIX) :].strip())
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text("Result: done\n", encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")
        if action == "close":
            return subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")
        raise AssertionError(f"unexpected orca argv: {argv}")

    return executor


def test_grok_orca_dispatch_drives_orca_create_wait_send_wait_close(tmp_path, monkeypatch):
    resolve_worker_cli(cli_flag="grok-orca")
    _stub_orca_and_grok_which(monkeypatch, tmp_path)
    calls: list[list[str]] = []
    output_path = tmp_path / "dispatch_worker" / "attempt-1" / "output.md"
    result = dispatch_worker(
        role=ROLE_GENERAL_PURPOSE,
        task_prompt="x",
        output_path=output_path,
        executor=make_orca_cli_executor(calls),
    )
    assert result.ok is True
    assert [_orca_action(argv) for argv in calls] == [
        "create",
        "wait",
        "send",
        "wait",
        "close",
    ]
    for argv in calls:
        assert Path(argv[0]).stem.lower() == "orca"

    create = calls[0]
    assert create[create.index("--worktree") + 1] == "active"
    assert create[create.index("--title") + 1] == f"{tmp_path.name}:dispatch_worker"
    assert "--focus" not in create
    command = create[create.index("--command") + 1]
    assert "--permission-mode" in command
    assert "auto" in command
    assert "--model" in command
    assert "grok-4.6" in command
    assert "--effort" in command
    assert "high" in command
    assert "-p" not in command.split()
    assert "--output-format" not in command
    assert "--always-approve" not in command

    send = calls[2]
    assert "--enter" in send
    assert send[send.index("--terminal") + 1] == ORCA_CREATE_HANDLE
    prompt = send[send.index("--text") + 1]
    assert "caveman skill full" in prompt
    assert OUTPUT_PATH_LINE_PREFIX in prompt

    for wait in (calls[1], calls[3]):
        assert wait[wait.index("--for") + 1] == "tui-idle"
        assert wait[wait.index("--terminal") + 1] == ORCA_CREATE_HANDLE

    expected_usage = {
        "worker_cli": "grok-orca",
        "model": "grok-4.6",
        "cost_usd": None,
        "input_tokens": None,
        "output_tokens": None,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
    }
    usage_path = output_path.parent / "usage.json"
    assert json.loads(usage_path.read_text(encoding="utf-8")) == expected_usage
    assert result.usage == expected_usage


def test_grok_orca_closes_terminal_when_second_wait_fails(tmp_path, monkeypatch):
    resolve_worker_cli(cli_flag="grok-orca")
    _stub_orca_and_grok_which(monkeypatch, tmp_path)
    calls: list[list[str]] = []
    output_path = tmp_path / "dispatch_worker" / "attempt-1" / "output.md"
    result = dispatch_worker(
        role=ROLE_GENERAL_PURPOSE,
        task_prompt="x",
        output_path=output_path,
        executor=make_orca_cli_executor(calls, fail_second_wait=True),
    )
    assert result.ok is False
    assert [_orca_action(argv) for argv in calls] == [
        "create",
        "wait",
        "send",
        "wait",
        "close",
    ]
    assert calls[-1][calls[-1].index("terminal") + 1] == "close"


def test_grok_orca_missing_orca_fails_before_create(tmp_path, monkeypatch):
    resolve_worker_cli(cli_flag="grok-orca")
    _stub_orca_and_grok_which(monkeypatch, tmp_path, orca=False, grok=True)
    calls: list = []

    def executor(argv, input_text, timeout):
        calls.append(argv)
        raise AssertionError("executor must not run when orca is missing")

    result = dispatch_worker(
        role=ROLE_GENERAL_PURPOSE,
        task_prompt="x",
        output_path=tmp_path / "dispatch_worker" / "attempt-1" / "output.md",
        executor=executor,
    )
    assert result.ok is False
    assert calls == []
    assert "orca" in result.stderr.lower()

