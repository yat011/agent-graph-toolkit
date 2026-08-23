"""Tests for Worker CLI selection (cascade, ContextVar, argparse) — public seams only."""

from __future__ import annotations

import json

import pytest

from agentgraph_engine.cli import build_parser
from agentgraph_engine.worker_cli import (
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


def test_status_has_no_cli_flag():
    parser = build_parser()
    args = parser.parse_args(["status", "--run", "r"])
    assert not hasattr(args, "cli")
