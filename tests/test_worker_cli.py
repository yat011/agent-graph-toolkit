"""Tests for Worker CLI selection (cascade, ContextVar, argparse) — public seams only."""

from __future__ import annotations

import json
import subprocess

import pytest

from agentgraph_engine.cli import build_parser
from agentgraph_engine.worker_cli import (
    MUSE_PROMPT_FILENAME,
    MuseWorkerCli,
    WorkerCliError,
    current_worker_cli,
    default_settings_path,
    resolve_worker_cli,
)


def test_cli_flag_beats_settings_file(tmp_path):
    settings = tmp_path / "agentgraph.json"
    settings.write_text(json.dumps({"worker_cli": "grok"}), encoding="utf-8")
    assert resolve_worker_cli(cli_flag="cursor", settings_path=settings) == "cursor"


def test_settings_file_beats_default(tmp_path):
    settings = tmp_path / "agentgraph.json"
    settings.write_text(json.dumps({"worker_cli": "grok"}), encoding="utf-8")
    assert resolve_worker_cli(settings_path=settings) == "grok"


def test_default_is_claude_when_settings_file_missing(tmp_path):
    assert resolve_worker_cli(settings_path=tmp_path / "missing.json") == "claude"


def test_running_process_does_not_reread_the_settings_file(tmp_path):
    settings = tmp_path / "agentgraph.json"
    settings.write_text(json.dumps({"worker_cli": "grok"}), encoding="utf-8")
    assert resolve_worker_cli(settings_path=settings) == "grok"
    settings.write_text(json.dumps({"worker_cli": "cursor"}), encoding="utf-8")
    assert resolve_worker_cli(cli_flag="cursor", settings_path=settings) == "grok"


def test_unknown_cli_flag_on_resolve_is_error():
    with pytest.raises(WorkerCliError, match="unknown Worker CLI"):
        resolve_worker_cli(cli_flag="nope")


def test_unknown_worker_cli_in_settings_file_is_error(tmp_path):
    settings = tmp_path / "agentgraph.json"
    settings.write_text(json.dumps({"worker_cli": "nope"}), encoding="utf-8")
    with pytest.raises(WorkerCliError, match="unknown Worker CLI"):
        resolve_worker_cli(settings_path=settings)


def test_resolve_does_not_create_the_settings_file(tmp_path):
    settings = tmp_path / "agentgraph.json"
    resolve_worker_cli(settings_path=settings)
    assert not settings.exists()


def test_current_worker_cli_defaults_without_reading_the_file(tmp_path):
    settings = default_settings_path()
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"worker_cli": "grok"}), encoding="utf-8")
    assert current_worker_cli() == "claude"


def test_unknown_cli_argparse_choice_is_error():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["start", "--graph", "x", "--cli", "nope"])


def test_parser_accepts_cli_on_start_resume_redrive():
    parser = build_parser()
    assert parser.parse_args(["start", "--graph", "g", "--cli", "grok"]).cli == "grok"
    assert parser.parse_args(["resume", "--run", "r", "--cli", "cursor"]).cli == "cursor"
    assert parser.parse_args(["redrive", "--run", "r", "--cli", "claude"]).cli == "claude"
    assert parser.parse_args(["start", "--graph", "g", "--cli", "grok-orca"]).cli == "grok-orca"
    assert parser.parse_args(["start", "--graph", "g", "--cli", "muse"]).cli == "muse"
    assert parser.parse_args(["resume", "--run", "r", "--cli", "muse"]).cli == "muse"
    assert parser.parse_args(["redrive", "--run", "r", "--cli", "muse"]).cli == "muse"


def test_resolve_worker_cli_accepts_grok_orca():
    assert resolve_worker_cli(cli_flag="grok-orca") == "grok-orca"


def test_resolve_worker_cli_accepts_muse():
    assert resolve_worker_cli(cli_flag="muse") == "muse"


def test_status_has_no_cli_flag():
    parser = build_parser()
    args = parser.parse_args(["status", "--run", "r"])
    assert not hasattr(args, "cli")


def test_muse_run_sends_work_order_via_prompt_file_not_argv(tmp_path):
    output_path = tmp_path / "node" / "attempt-1" / "output.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prompt = "do the thing " + "x" * 40000
    captured: dict = {}

    def executor(argv, input_text, timeout):
        captured["argv"] = argv
        captured["input_text"] = input_text
        return subprocess.CompletedProcess(argv, 0, stdout='{"result":"hi"}', stderr="")

    proc = MuseWorkerCli().run("muse", "max", prompt, None, executor, output_path)
    prompt_file = output_path.parent / MUSE_PROMPT_FILENAME
    assert prompt_file.read_text(encoding="utf-8") == prompt
    argv = captured["argv"]
    assert argv[argv.index("--prompt-file") + 1] == str(prompt_file)
    # A real work order alone exceeds the Windows command-line limit — argv must stay small.
    assert sum(len(arg) for arg in argv) < 32767
    assert not any("xxxx" in arg for arg in argv)
    # stdin stays empty: `muse exec` never reads it, and an unread pipe fed with
    # the prompt blocks forever once grandchildren hold it open. Fakes read the file.
    assert captured["input_text"] == ""
    assert json.loads(proc.stdout)["result"] == "hi"
