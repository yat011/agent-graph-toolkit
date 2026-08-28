"""`agentgraph monitor` argparse surface — defaults, flags, and the missing `--cli` seam."""

from __future__ import annotations

import pytest

from agentgraph_engine.cli import build_parser, cmd_monitor


def test_monitor_defaults_interval_three_and_agent_works_root_none():
    args = build_parser().parse_args(["monitor"])
    assert args.interval == 3
    assert args.agent_works_root is None


def test_monitor_records_interval_and_agent_works_root():
    args = build_parser().parse_args(["monitor", "--interval", "5", "--agent-works-root", "x"])
    assert args.interval == 5
    assert args.agent_works_root == "x"


def test_monitor_has_no_cli_flag():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["monitor", "--cli", "claude"])


def test_status_still_has_no_cli_flag():
    parser = build_parser()
    args = parser.parse_args(["status", "--run", "r"])
    assert not hasattr(args, "cli")


def test_zero_interval_is_rejected_before_tui_starts():
    args = build_parser().parse_args(["monitor", "--interval", "0"])
    assert cmd_monitor(args) != 0


def test_negative_interval_is_rejected_before_tui_starts():
    args = build_parser().parse_args(["monitor", "--interval", "-1"])
    assert cmd_monitor(args) != 0
